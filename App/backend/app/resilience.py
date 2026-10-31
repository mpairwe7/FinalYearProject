"""Shared resilience primitives — circuit breaker, turn deadline, cancel scope.

Extracted from ``retriever.py`` so the same breaker can guard both
Qdrant (retrieval) and the LLM worker (generation).  2026 production
standard: each external/expensive dependency gets its own breaker so one
slow dependency cannot exhaust the request pool via thundering-herd.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

#: How long a half-open probe may stay unreported before it is presumed lost.
#: Longer than any single call a breaker guards (the reasoner runs up to 90 s),
#: so a slow probe is not replaced while it is still working.
DEFAULT_PROBE_TIMEOUT_S = 120.0


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker with exponential back-off.

    - CLOSED: requests flow normally; consecutive failures tracked.
    - OPEN: requests rejected immediately; waits *reset_timeout*
      (doubles on each HALF_OPEN→OPEN trip, capped at *max_timeout*).
    - HALF_OPEN: one test request allowed; success → CLOSED, failure → OPEN.

    The HALF_OPEN probe is a **lease**. It used to be held until someone reported
    back, and several callers could take it and never report: a budget check that
    refused after the breaker said yes, a generator closed by a client leaving, a
    fallback re-entering the same breaker. Each left it HALF_OPEN with the probe
    "in flight" for the life of the process, refusing everything, with nothing in
    the logs. A lease expires after *probe_timeout*, so no caller can wedge a
    dependency. A caller that learned nothing should :meth:`release` instead of
    waiting it out. Resilience4j's ``maxWaitDurationInHalfOpenState`` is the same
    control; a lost probe here hands over to a new one rather than re-opening.
    """

    def __init__(
        self,
        name: str = "breaker",
        failure_threshold: int = 3,
        reset_timeout: float = 10.0,
        max_timeout: float = 300.0,
        probe_timeout: float = DEFAULT_PROBE_TIMEOUT_S,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self._base_timeout = reset_timeout
        self._max_timeout = max_timeout
        self._probe_timeout = probe_timeout
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._current_timeout = reset_timeout
        self._half_open_probe_in_flight = False
        self._probe_taken_at: float = 0.0
        self._lock = threading.Lock()

    def _half_open_if_due(self) -> bool:
        """Move OPEN→HALF_OPEN once the back-off has passed. Caller holds the lock."""
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._opened_at >= self._current_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_probe_in_flight = False
            logger.info("Circuit breaker %s → HALF_OPEN (probing)", self.name)
            return True
        return False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            moved = self._half_open_if_due()
            state = self._state
        if moved:
            self._publish(state)
        return state

    def allow_request(self) -> bool:
        """Return True if the request should proceed (single probe in HALF_OPEN)."""
        lost_probe = False
        allowed = False
        with self._lock:
            moved = self._half_open_if_due()
            state = self._state
            if state == CircuitState.CLOSED:
                allowed = True
            elif state == CircuitState.HALF_OPEN:
                now = time.monotonic()
                if self._half_open_probe_in_flight and now - self._probe_taken_at >= self._probe_timeout:
                    self._half_open_probe_in_flight = False
                    lost_probe = True
                if not self._half_open_probe_in_flight:
                    self._half_open_probe_in_flight = True
                    self._probe_taken_at = now
                    allowed = True
        if moved:
            self._publish(state)
        if lost_probe:
            logger.warning("Circuit breaker %s: probe never reported, taking a new one", self.name)
            self._count_lost_probe()
        return allowed

    def release(self) -> None:
        """Give back a slot without judging the dependency.

        For a call that never tested it: the client left, a budget refused, a
        turn ran out of time. Counting that as a failure would blame the model
        for a closed browser tab; counting it as a success would close the
        breaker on no evidence.
        """
        with self._lock:
            self._half_open_probe_in_flight = False

    def record_success(self) -> None:
        """Call after a successful operation."""
        with self._lock:
            recovered = self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN)
            if recovered:
                logger.info("Circuit breaker %s → CLOSED (recovered)", self.name)
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._current_timeout = self._base_timeout
            self._half_open_probe_in_flight = False
        if recovered:
            self._publish(CircuitState.CLOSED)

    def record_failure(self) -> None:
        """Call after a failed operation."""
        opened = False
        with self._lock:
            self._failures += 1
            was_half_open = self._state == CircuitState.HALF_OPEN
            self._half_open_probe_in_flight = False
            # Already OPEN: calls that were in flight when it tripped are reporting
            # in. Restarting the clock for each kept pushing recovery further away.
            if self._state != CircuitState.OPEN and (
                self._failures >= self.failure_threshold or was_half_open
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                opened = True
                # Double backoff only from HALF_OPEN→OPEN (not first trip)
                if was_half_open:
                    self._current_timeout = min(self._current_timeout * 2, self._max_timeout)
                logger.warning(
                    "Circuit breaker %s → OPEN (failures=%d, backoff=%.0fs)",
                    self.name,
                    self._failures,
                    self._current_timeout,
                )
        if opened:
            self._publish(CircuitState.OPEN)

    def _publish(self, state: CircuitState) -> None:
        """Make a transition visible. Outside the lock; telemetry must not fail a call."""
        try:
            from .analytics import metrics

            metrics.inc("circuit_breaker_transitions_total", labels={"breaker": self.name, "to": state.value})
            metrics.set_gauge("circuit_breaker_state", _STATE_GAUGE[state], labels={"breaker": self.name})
        except Exception:  # noqa: BLE001
            logger.debug("breaker transition not published", exc_info=True)

    def _count_lost_probe(self) -> None:
        try:
            from .analytics import metrics

            metrics.inc("circuit_breaker_probes_lost_total", labels={"breaker": self.name})
        except Exception:  # noqa: BLE001
            logger.debug("lost probe not counted", exc_info=True)


#: ``ura_circuit_breaker_state``: 0 closed, 1 half-open, 2 open.
_STATE_GAUGE = {CircuitState.CLOSED: 0, CircuitState.HALF_OPEN: 1, CircuitState.OPEN: 2}


def admit(breaker: CircuitBreaker, pay: Callable[[], bool]) -> bool:
    """Take a breaker's slot and the budget that pays for it, or neither.

    ``allow_request() and budget.try_consume()`` reads naturally and is the bug:
    when the breaker says yes and the budget says no, the half-open probe is gone
    and nobody will report on it.
    """
    if not breaker.allow_request():
        return False
    if pay():
        return True
    breaker.release()
    return False


# ---------------------------------------------------------------------------
# Turn deadline
# ---------------------------------------------------------------------------
#: Absolute ``time.monotonic()`` by which this turn must end, or None. Carried in
#: the context so every step sees it, including threads started with
#: ``copy_context``: each stage had a timeout of its own, and none knew what the
#: stages before it had already spent.
_TURN_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("turn_deadline", default=None)


def set_turn_deadline(seconds: float | None) -> None:
    """Start this turn's clock. For async generators, which cannot hold a ``with`` safely."""
    _TURN_DEADLINE.set(None if seconds is None else time.monotonic() + seconds)


@contextmanager
def turn_deadline(seconds: float | None) -> Iterator[None]:
    """Run a block under a turn deadline; ``None`` means no deadline."""
    token = _TURN_DEADLINE.set(None if seconds is None else time.monotonic() + seconds)
    try:
        yield
    finally:
        _TURN_DEADLINE.reset(token)


def remaining_turn_seconds(default: float) -> float:
    """The smaller of *default* and what is left of the turn (may be zero or less)."""
    deadline = _TURN_DEADLINE.get()
    if deadline is None:
        return default
    return min(default, deadline - time.monotonic())


class CancelScope:
    """A cancellation of its own that also follows a parent's.

    A run that times out must stop without cancelling the turn, which still has a
    fallback to serve. A turn that is cancelled must stop every run inside it.
    Offers the part of ``threading.Event`` the code here uses.
    """

    _POLL_S = 0.05

    def __init__(self, parent: threading.Event | CancelScope | None = None) -> None:
        self._own = threading.Event()
        self._parent = parent

    def set(self) -> None:
        self._own.set()

    def is_set(self) -> bool:
        return self._own.is_set() or (self._parent is not None and self._parent.is_set())

    def wait(self, timeout: float | None = None) -> bool:
        """Block until set or *timeout* elapses; polls because a parent cannot wake this."""
        end = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = self._POLL_S if end is None else min(self._POLL_S, end - time.monotonic())
            if remaining <= 0:
                return False
            self._own.wait(remaining)
        return True
