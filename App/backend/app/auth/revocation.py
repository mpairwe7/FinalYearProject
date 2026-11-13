"""Sessions revoked before their tokens expire (G304, ADR-0053).

The service verifies a bearer token by its signature and its expiry, so a
token that has been signed out of, or that an administrator wants stopped,
kept working until it expired. A revocation is a cutoff per person: every
token issued to them before ``revoked_before`` is refused, and any token issued
after it works. It is stored in ``revoked_sessions`` (one row per person) by
``POST /v1/me/logout`` and ``POST /v1/admin/users/{user_id}/revoke``.

Each process reads the table during startup, before it serves its first
request (``main._load_session_revocations_at_start``), keeps it in memory and
re-reads it every 5 s (``main._session_revocation_sync_loop``), as the flag
overrides are (ADR-0044); the process that records a revocation applies it at
once. So the check on every request and in every open socket's loop is a
dictionary lookup.

The cutoff is the wall-clock time of the revocation, a float. A token's
``iat`` is a whole second, so a token issued in the same second as the
sign-out, before it, is revoked; so is a sign-in completed in that same
second. A sign-in a second later works. A token with no usable ``iat`` could
not be placed before or after a cutoff, so it is refused when it is verified
(``jwt_auth``, 401 like any invalid token): every token the service accepts can
be revoked. ``jti`` and ``sid`` are not used: not every provider issues them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

#: The longest an open, signed-in socket goes between checks of its session, so
#: a revoked one closes within about this long once this process knows of it
#: (another process learns of it within its own 5 s sync). The chat and voice
#: sockets use it here; the staff and call sockets as ``receptionist.ws._SESSION_POLL_S``.
SOCKET_CHECK_INTERVAL_S = 5.0

_lock = threading.Lock()
#: user id -> cutoff (epoch seconds). Only ever moves later in a running process.
_revoked: dict[str, float] = {}
#: What the sync last did here, for the log (a failure is logged when it starts).
_state: dict[str, Any] = {"synced_at": None, "failing": False}


def issued_at(claims: dict[str, Any] | None) -> float | None:
    """A token's ``iat`` in epoch seconds, or ``None`` when it has no usable one."""
    if not claims:
        return None
    value = claims.get("iat")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def revoked_before(user_id: str) -> float | None:
    """The cutoff for *user_id*, or ``None`` when their sessions were never revoked."""
    with _lock:
        return _revoked.get(user_id)


def is_revoked(user_id: str, issued: float | None) -> bool:
    """Whether a token issued to *user_id* at *issued* was revoked.

    False for an anonymous socket (no user id, no ``iat``). A signed-in
    token always has an ``iat``: verification refuses one without.
    """
    if not user_id or issued is None:
        return False
    cutoff = revoked_before(user_id)
    return cutoff is not None and issued < cutoff


def claims_revoked(claims: dict[str, Any] | None) -> bool:
    """Whether the verified *claims* belong to a revoked token."""
    if not claims:
        return False
    return is_revoked(str(claims.get("sub") or ""), issued_at(claims))


def record(user_id: str, cutoff: float) -> None:
    """Apply one revocation in this process; an earlier cutoff never replaces a later one."""
    if not user_id:
        return
    with _lock:
        current = _revoked.get(user_id)
        if current is None or cutoff > current:
            _revoked[user_id] = float(cutoff)


def apply(stored: dict[str, float]) -> None:
    """Merge the stored cutoffs into this process's map.

    Merged, not replaced: a read that began before this process wrote its own
    revocation must not undo it. A row removed from the table therefore takes
    effect here at the next restart.
    """
    for user_id, cutoff in stored.items():
        record(str(user_id), float(cutoff))


def state() -> dict[str, Any]:
    """When this process last read the store, and whether reading it is failing."""
    return dict(_state)


def reset() -> None:
    """Testing hook: forget every revocation and the sync state."""
    with _lock:
        _revoked.clear()
    _state.update(synced_at=None, failing=False)


def revoke(user_id: str, *, reason: str, actor: str) -> float:
    """Revoke *user_id*'s sessions now: stored first, then applied in this process.

    Returns the cutoff. Raises when the store cannot keep it, and then this
    process changes nothing either: a revocation only one process knew of
    would be undone by the next restart and missed by every other process.
    """
    from .. import database as db

    cutoff = time.time()
    db.revoke_sessions(user_id, revoked_before=cutoff, reason=reason, actor=actor)
    record(user_id, cutoff)
    return cutoff


def sync(*, strict: bool = False) -> None:
    """One pass: bring this process's map up to date with ``revoked_sessions``.

    An unreadable store keeps the map as it is (a revocation is never
    forgotten because the store could not be read), counts
    ``session_revocation_sync_failures_total`` and logs once until it answers.
    With *strict* (the read at startup, before the process serves) it then
    raises as well, so the caller decides whether to start without them.
    """
    from .. import database as db
    from ..analytics import metrics

    try:
        stored = db.load_session_revocations()
    except Exception:
        metrics.inc("session_revocation_sync_failures_total")
        if not _state["failing"]:
            logger.error(
                "Session revocations could not be read; this process keeps the ones it has "
                "until the store answers",
                exc_info=True,
            )
        _state["failing"] = True
        if strict:
            raise
        return
    if _state["failing"]:
        logger.info("Session revocations are readable again")
    _state["failing"] = False
    apply(stored)
    _state["synced_at"] = time.time()


# ---------------------------------------------------------------------------
# Open sockets
# ---------------------------------------------------------------------------


class SessionRevoked(Exception):
    """The session a socket signed in with was revoked while it was open."""


class SessionCheckedReceiver:
    """A signed-in socket's next frame, with its session re-checked while it waits (G304, ADR-0053).

    The chat and voice sockets waited on ``receive()`` for as long as the
    person was quiet, so a session revoked meanwhile kept the socket open
    until its next frame, or for good. :meth:`next` checks at least every
    ``SOCKET_CHECK_INTERVAL_S`` and on every frame, and raises
    :class:`SessionRevoked` once the session is revoked. A receive still
    pending when a check comes round is kept, not cancelled, so no frame is
    lost; :meth:`close` cancels it when the socket's loop ends. An anonymous
    socket (no user id) has no session and waits as it always did.
    """

    def __init__(self, receive: Callable[[], Awaitable[Any]], user_id: str, issued: float | None) -> None:
        self._receive = receive
        self._user_id = user_id
        self._issued = issued
        self._pending: asyncio.Future[Any] | None = None

    def revoked(self) -> bool:
        return is_revoked(self._user_id, self._issued)

    async def next(self, timeout: float | None = None) -> Any:
        """The next frame; ``TimeoutError`` after *timeout* without one; ``SessionRevoked`` once revoked."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            if self.revoked():
                raise SessionRevoked
            if self._pending is None:
                self._pending = asyncio.ensure_future(self._receive())
            wait = SOCKET_CHECK_INTERVAL_S if self._user_id else None
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                wait = remaining if wait is None else min(wait, remaining)
            done, _pending = await asyncio.wait({self._pending}, timeout=wait)
            if not done:
                continue
            received, self._pending = self._pending, None
            if self.revoked():
                raise SessionRevoked
            return received.result()

    def close(self) -> None:
        """Cancel a receive still pending: the socket's loop has ended."""
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None


async def close_revoked_socket(websocket: Any) -> None:
    """Tell a socket's client its session was revoked, then close it 4401.

    The client is told as a refused ``call_start`` is (``signin``: sign in
    again). Both steps are bounded: a client that stopped reading cannot hold
    the socket open.
    """
    with contextlib.suppress(Exception):
        async with asyncio.timeout(1.0):
            await websocket.send_text(json.dumps(
                {"type": "error", "code": "signin", "detail": "Session revoked", "recoverable": False}
            ))
    with contextlib.suppress(Exception):
        async with asyncio.timeout(1.0):
            await websocket.close(code=4401)
