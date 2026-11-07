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
hashed so this module never holds a raw identifier. State is per replica and
bounded; a client that discards its session and conversation starts afresh,
which is why the IP rate limit stays in front of this (OWASP LLM10).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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
) -> str:
    """A stable, hashed key for whoever sent the turn, or ``""`` when nothing identifies them."""
    if user_id:
        raw = f"user:{tenant_id or 'default'}:{user_id}"
    elif session_id:
        raw = f"session:{session_id}"
    elif conversation_id:
        raw = f"conversation:{conversation_id}"
    else:
        return ""
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
    ) -> None:
        self._threshold = max(1, threshold)
        self._window_s = max(1.0, window_s)
        self._cooldown_s = max(0.0, cooldown_s)
        self._max_tracked = max(1, max_tracked)
        self._clock = clock
        self._lock = threading.Lock()
        self._strikes: OrderedDict[str, deque[float]] = OrderedDict()
        self._cooldown_until: dict[str, float] = {}

    def cooldown_remaining(self, key: str) -> float:
        """Seconds left in *key*'s cool-down, ``0.0`` when there is none."""
        if not key:
            return 0.0
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
        now = self._clock()
        with self._lock:
            strikes = self._strikes.pop(key, None) or deque()
            while strikes and now - strikes[0] > self._window_s:
                strikes.popleft()
            strikes.append(now)
            if len(strikes) >= self._threshold:
                self._cooldown_until[key] = now + self._cooldown_s
                strikes.clear()
                started = True
            else:
                started = False
            if strikes:
                self._strikes[key] = strikes
            while len(self._strikes) > self._max_tracked:
                self._strikes.popitem(last=False)
            if len(self._cooldown_until) > self._max_tracked:
                for stale in [k for k, until in self._cooldown_until.items() if until <= now]:
                    del self._cooldown_until[stale]
            return started

    def reset(self) -> None:
        """Forget every principal (tests and operator reset)."""
        with self._lock:
            self._strikes.clear()
            self._cooldown_until.clear()


#: The process-wide tracker the chat service consults.
strike_tracker = StrikeTracker()
