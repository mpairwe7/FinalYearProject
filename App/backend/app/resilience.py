"""Shared resilience primitives — circuit breaker, deadlines, bulkheads.

Extracted from ``retriever.py`` so the same breaker can guard both
Qdrant (retrieval) and the LLM worker (generation).  2026 production
standard: each external/expensive dependency gets its own breaker so one
slow dependency cannot exhaust the request pool via thundering-herd.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        name: str = "breaker",
        failure_threshold: int = 3,
        reset_timeout: float = 10.0,
        max_timeout: float = 300.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self._base_timeout = reset_timeout
        self._max_timeout = max_timeout
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._current_timeout = reset_timeout
        self._half_open_probe_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and time.monotonic() - self._opened_at >= self._current_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = False
                logger.info("Circuit breaker %s → HALF_OPEN (testing)", self.name)
            return self._state

    def allow_request(self) -> bool:
        """Return True if the request should proceed (single probe in HALF_OPEN)."""
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and time.monotonic() - self._opened_at >= self._current_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = False
                logger.info("Circuit breaker %s → HALF_OPEN (testing)", self.name)
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN and not self._half_open_probe_in_flight:
                self._half_open_probe_in_flight = True
                return True
            return False

    def record_success(self) -> None:
        """Call after a successful operation."""
        with self._lock:
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                logger.info("Circuit breaker %s → CLOSED (recovered)", self.name)
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._current_timeout = self._base_timeout
            self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        """Call after a failed operation."""
        with self._lock:
            self._failures += 1
            was_half_open = self._state == CircuitState.HALF_OPEN
            self._half_open_probe_in_flight = False
            if self._failures >= self.failure_threshold or was_half_open:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                # Double backoff only from HALF_OPEN→OPEN (not first trip)
                if was_half_open:
                    self._current_timeout = min(self._current_timeout * 2, self._max_timeout)
                logger.warning(
                    "Circuit breaker %s → OPEN (failures=%d, backoff=%.0fs)",
                    self.name,
                    self._failures,
                    self._current_timeout,
                )
