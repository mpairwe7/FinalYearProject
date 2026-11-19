"""Progressive friction for a principal that keeps tripping the input guard (G464).

A keyword guard is a filter an attacker can iterate against: adaptive
attacks that rewrite a prompt until it passes broke twelve published
jailbreak and prompt-injection defences at over 90% success (Nasr et al.,
"The Attacker Moves Second", arXiv:2510.09023). Each refusal used to cost the
attacker one request, which the per-IP rate limit allowed thirty times a
minute. Here the same principal tripping the guard ``GUARD_STRIKE_THRESHOLD``
times inside ``GUARD_STRIKE_WINDOW_S`` pauses that principal for
``GUARD_STRIKE_COOLDOWN_S``: their turns are answered with a fixed notice and
URA's contact channels, and no model is called. The pause is a security
event (``guardrail_strike_cooldown``), so the ``GuardrailProbingCampaign``
alert sees a probing campaign rather than a scatter of refusals.

A principal is the signed-in user, else the session, else the conversation,
hashed so this module never holds a raw identifier. With ``REDIS_URL`` set the
strikes and pauses live in Redis, so every worker and replica counts the same
principal together; Compose runs four workers, and per-process counts gave an
attacker four times the tries (G679). Without Redis, or when it fails, state is
per process and bounded. A client that discards its session and conversation
starts afresh, which is why the IP rate limit stays in front of this (OWASP LLM10).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from .input_checks import GuardResult

logger = logging.getLogger(__name__)

STRIKE_THRESHOLD = int(os.getenv("GUARD_STRIKE_THRESHOLD", "5"))
STRIKE_WINDOW_S = float(os.getenv("GUARD_STRIKE_WINDOW_S", "600"))
COOLDOWN_S = float(os.getenv("GUARD_STRIKE_COOLDOWN_S", "300"))
MAX_TRACKED = int(os.getenv("GUARD_STRIKE_MAX_TRACKED", "50000"))

#: Guard verdicts that count as a strike. A message that is merely too long is not an attack.
STRIKE_FLAGS = frozenset({"prompt_injection", "harmful_intent"})

COOLDOWN_REPLY = (
    "For your security, this chat is paused for a few minutes after several requests "
    "I could not process. You can still reach URA directly: toll-free 0800 117 000, "
    "WhatsApp 0772 140 000, or services@ura.go.ug."
)


def principal_key(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    tenant_id: str | None = None,
    client_address: str | None = None,
) -> str:
    """A stable, hashed key for whoever sent the turn, or ``""`` when nothing identifies them.

    *client_address* is the last resort, for a route with no session or
    conversation (the chat title). It is hashed like the rest and only ever
    held in memory.
    """
    tenant = tenant_id or "default"
    # JSON keeps the parts unambiguous: a tenant or id containing ":" cannot
    # make two principals collide, and one tenant's strikes cannot pause another's.
    if user_id:
        parts = ["user", tenant, user_id]
    elif session_id:
        parts = ["session", tenant, session_id]
    elif conversation_id:
        parts = ["conversation", tenant, conversation_id]
    elif client_address:
        parts = ["client", client_address]
    else:
        return ""
    raw = json.dumps(parts, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class StrikeTracker:
    """Sliding-window strike counts and cool-downs per principal, bounded in size and thread-safe."""

    def __init__(
        self,
        *,
        threshold: int = STRIKE_THRESHOLD,
        window_s: float = STRIKE_WINDOW_S,
        cooldown_s: float = COOLDOWN_S,
        max_tracked: int = MAX_TRACKED,
        clock: Callable[[], float] = time.monotonic,
        shared: Callable[[], Any] | None = None,
    ) -> None:
        self._threshold = max(1, threshold)
        self._window_s = max(1.0, window_s)
        self._cooldown_s = max(0.0, cooldown_s)
        self._max_tracked = max(1, max_tracked)
        self._clock = clock
        #: Returns the Redis client every worker shares, or None to keep state in this process.
        self._shared = shared or (lambda: None)
        self._lock = threading.Lock()
        self._strikes: OrderedDict[str, deque[float]] = OrderedDict()
        #: Insertion order is expiry order, because every cool-down lasts the same time.
        self._cooldown_until: OrderedDict[str, float] = OrderedDict()

    def cooldown_remaining(self, key: str) -> float:
        """Seconds left in *key*'s cool-down, ``0.0`` when there is none."""
        if not key:
            return 0.0
        r = self._shared()
        if r is not None:
            try:
                ms = r.pttl(f"{_SHARED_PREFIX}cooldown:{key}")
                return ms / 1000 if ms and ms > 0 else 0.0
            except Exception:  # noqa: BLE001 - fall back to this process's own state
                logger.debug("shared strike store unavailable; using this process's", exc_info=True)
        now = self._clock()
        with self._lock:
            until = self._cooldown_until.get(key, 0.0)
            if until <= now:
                self._cooldown_until.pop(key, None)
                return 0.0
            return until - now

    def record_strike(self, key: str) -> bool:
        """Count one guard block for *key*. True when this strike starts a cool-down."""
        if not key:
            return False
        r = self._shared()
        if r is not None:
            try:
                return self._record_shared_strike(r, key)
            except Exception:  # noqa: BLE001 - fall back to this process's own state
                logger.debug("shared strike store unavailable; using this process's", exc_info=True)
        now = self._clock()
        with self._lock:
            strikes = self._strikes.pop(key, None) or deque()
            while strikes and now - strikes[0] > self._window_s:
                strikes.popleft()
            strikes.append(now)
            if len(strikes) >= self._threshold:
                self._cooldown_until.pop(key, None)
                self._cooldown_until[key] = now + self._cooldown_s
                strikes.clear()
                started = True
            else:
                started = False
            if strikes:
                self._strikes[key] = strikes
            while len(self._strikes) > self._max_tracked:
                self._strikes.popitem(last=False)
            # Expired cool-downs leave from the front. Past capacity the one
            # closest to expiry goes first: that principal loses at most the
            # remainder of its pause, and the per-IP limit still applies.
            while self._cooldown_until and (
                next(iter(self._cooldown_until.values())) <= now
                or len(self._cooldown_until) > self._max_tracked
            ):
                self._cooldown_until.popitem(last=False)
            return started

    def _record_shared_strike(self, r: Any, key: str) -> bool:
        """The sliding window as a Redis sorted set; the pause as a key that expires."""
        now = time.time()  # wall clock: every process scores against the same one
        strikes = f"{_SHARED_PREFIX}strikes:{key}"
        pipe = r.pipeline()
        pipe.zremrangebyscore(strikes, "-inf", now - self._window_s)
        pipe.zadd(strikes, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        pipe.zcard(strikes)
        pipe.expire(strikes, int(self._window_s) + 1)
        count = pipe.execute()[2]
        if count < self._threshold:
            return False
        pipe = r.pipeline()
        pipe.set(f"{_SHARED_PREFIX}cooldown:{key}", "1", px=max(1, int(self._cooldown_s * 1000)))
        pipe.delete(strikes)
        pipe.execute()
        return True

    def reset(self) -> None:
        """Forget every principal (tests and operator reset)."""
        with self._lock:
            self._strikes.clear()
            self._cooldown_until.clear()
        r = self._shared()
        if r is not None:
            try:
                for name in r.scan_iter(f"{_SHARED_PREFIX}*"):
                    r.delete(name)
            except Exception:  # noqa: BLE001 - a reset of this process still happened
                logger.debug("shared strike store not reset", exc_info=True)


_SHARED_PREFIX = "guard:friction:"
_redis_client: Any = None
_redis_checked = False


def _shared_redis() -> Any:
    """The Redis client at ``REDIS_URL``, once per process, or None."""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    url = os.getenv("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis

        client = redis.from_url(url, socket_timeout=1)
        client.ping()
        _redis_client = client
    except Exception:  # noqa: BLE001 - no Redis means per-process state, as before
        logger.warning("REDIS_URL is set but unreachable; guard strikes stay per process")
    return _redis_client


#: The process-wide tracker the chat service consults.
strike_tracker = StrikeTracker(shared=_shared_redis)


def _friction_enabled() -> bool:
    from ..config.flags import flags

    return flags.is_enabled("guardrail_strike_friction")


def paused(principal: str, *, crisis: bool = False) -> GuardResult | None:
    """The fixed refusal while *principal* cools down, else ``None``.

    A message that reads as a crisis is never paused: the caller's
    crisis-support reply, with its emergency numbers, has to reach them.
    """
    if not principal or crisis or not _friction_enabled():
        return None
    if strike_tracker.cooldown_remaining(principal) <= 0:
        return None
    from .input_checks import GuardResult

    return GuardResult(allowed=False, reason=COOLDOWN_REPLY, flags=["guardrail_cooldown"])


def note_refusal(principal: str, guard: GuardResult) -> None:
    """Count *guard* as a strike when it refused an attack, and record a pause it starts.

    The event carries the hashed principal so an operator can tell one
    principal paused repeatedly from many. A failure to record it never
    changes the refusal the caller returns.
    """
    if not principal or guard.allowed or not STRIKE_FLAGS.intersection(guard.flags):
        return
    if not _friction_enabled() or not strike_tracker.record_strike(principal):
        return
    try:
        from ..observability.security_events import record_security_event

        record_security_event(
            "guardrail_strike_cooldown", reason="repeated_guard_blocks", principal=principal
        )
    except Exception:  # noqa: BLE001 - telemetry must not turn a refusal into an error
        logger.warning("guardrail_strike_cooldown event not recorded", exc_info=True)
