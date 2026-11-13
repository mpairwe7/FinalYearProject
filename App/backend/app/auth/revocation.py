"""Sessions revoked before their tokens expire (G304, ADR-0048).

The service verifies a bearer token by its signature and its expiry, so a
token that has been signed out of, or that an administrator wants stopped,
kept working until it expired. A revocation is a cutoff per person: every
token issued to them before ``revoked_before`` is refused, and any token issued
after it works. It is stored in ``revoked_sessions`` (one row per person) by
``POST /v1/me/logout`` and ``POST /v1/admin/users/{user_id}/revoke``.

Each process keeps the table in memory and re-reads it every 5 s
(``main._session_revocation_sync_loop``), as the flag overrides are
(ADR-0044); the process that records a revocation applies it at once. So the
check on every request and in every open socket's loop is a dictionary
lookup.

The cutoff is the wall-clock time of the revocation, a float. A token's
``iat`` is a whole second, so a token issued in the same second as the
sign-out, before it, is revoked; so is a sign-in completed in that same
second. A sign-in a second later works. A token with no usable ``iat`` cannot
be placed before or after the cutoff and is not affected: its expiry still
ends it. ``jti`` and ``sid`` are not used: not every provider issues them.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

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
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def revoked_before(user_id: str) -> float | None:
    """The cutoff for *user_id*, or ``None`` when their sessions were never revoked."""
    with _lock:
        return _revoked.get(user_id)


def is_revoked(user_id: str, issued: float | None) -> bool:
    """Whether a token issued to *user_id* at *issued* was revoked.

    False for an anonymous socket (no user id) and for a token with no
    ``iat``: neither can be placed before a cutoff.
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


def sync() -> None:
    """One pass: bring this process's map up to date with ``revoked_sessions``.

    An unreadable store keeps the map as it is (a revocation is never
    forgotten because the store could not be read), counts
    ``session_revocation_sync_failures_total`` and logs once until it answers.
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
        return
    if _state["failing"]:
        logger.info("Session revocations are readable again")
    _state["failing"] = False
    apply(stored)
    _state["synced_at"] = time.time()
