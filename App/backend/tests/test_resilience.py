"""State-machine tests for ``resilience.CircuitBreaker``.

Every provider tier (local LLM, Cloudflare Workers AI, Gemini, Vectorize,
speech ASR/TTS) trusts this one class to stop hammering a dead dependency
and to let it back in after the back-off — but until now nothing tested the
CLOSED → OPEN → HALF_OPEN transitions, the exponential back-off, or the
``max_timeout`` cap. Time is injected by swapping the module's ``time``
reference for a controllable stub (no sleeps).
"""

from __future__ import annotations

import types
import unittest
import unittest.mock as mock

from app import resilience
from app.resilience import CircuitBreaker, CircuitState


class CircuitBreakerStateMachineTest(unittest.TestCase):
    def _breaker(self, **kwargs) -> tuple[CircuitBreaker, types.SimpleNamespace]:
        """Breaker wired to a fake monotonic clock local to the module."""
        clock = types.SimpleNamespace(t=1000.0)
        fake_time = types.SimpleNamespace(monotonic=lambda: clock.t)
        patcher = mock.patch.object(resilience, "time", fake_time)
        patcher.start()
        self.addCleanup(patcher.stop)
        defaults = {"name": "test", "failure_threshold": 3, "reset_timeout": 10.0, "max_timeout": 40.0}
        defaults.update(kwargs)
        return CircuitBreaker(**defaults), clock

    def test_stays_closed_below_threshold(self):
        br, _ = self._breaker()
        br.record_failure()
        br.record_failure()
        self.assertIs(br.state, CircuitState.CLOSED)
        self.assertTrue(br.allow_request())

    def test_opens_after_threshold_failures(self):
        br, _ = self._breaker()
        for _ in range(3):
            br.record_failure()
        self.assertIs(br.state, CircuitState.OPEN)
        self.assertFalse(br.allow_request())

    def test_success_resets_failure_count(self):
        br, _ = self._breaker()
        br.record_failure()
        br.record_failure()
        br.record_success()
        # Two earlier failures must not count toward the threshold anymore.
        br.record_failure()
        br.record_failure()
        self.assertTrue(br.allow_request())
        br.record_failure()
        self.assertFalse(br.allow_request())

    def test_half_open_after_reset_timeout(self):
        br, clock = self._breaker()
        for _ in range(3):
            br.record_failure()
        clock.t += 9.9
        self.assertFalse(br.allow_request())
        clock.t += 0.2  # past reset_timeout
        self.assertIs(br.state, CircuitState.HALF_OPEN)
        self.assertTrue(br.allow_request())  # exactly one probe is let through

    def test_half_open_success_closes_and_resets_backoff(self):
        br, clock = self._breaker()
        for _ in range(3):
            br.record_failure()
        clock.t += 10.1
        self.assertTrue(br.allow_request())  # HALF_OPEN probe
        br.record_success()
        self.assertIs(br.state, CircuitState.CLOSED)
        # Back-off is back at the base: a fresh trip re-opens for 10s, not 20s.
        for _ in range(3):
            br.record_failure()
        clock.t += 10.1
        self.assertTrue(br.allow_request())

    def test_half_open_failure_reopens_with_doubled_backoff(self):
        br, clock = self._breaker()
        for _ in range(3):
            br.record_failure()
        clock.t += 10.1
        self.assertIs(br.state, CircuitState.HALF_OPEN)
        br.record_failure()  # failed probe → OPEN with doubled back-off (20s)
        self.assertFalse(br.allow_request())
        clock.t += 10.1
        self.assertFalse(br.allow_request())  # base timeout is no longer enough
        clock.t += 10.1  # 20.2s since reopen
        self.assertTrue(br.allow_request())

    def test_backoff_is_capped_at_max_timeout(self):
        br, clock = self._breaker()  # base 10s, cap 40s
        for _ in range(3):
            br.record_failure()
        # Fail the probe repeatedly: 10 → 20 → 40 → 40 (capped).
        for expected_wait in (10.1, 20.1, 40.1, 40.1):
            clock.t += expected_wait
            self.assertTrue(br.allow_request(), f"probe expected after {expected_wait}s")
            br.record_failure()
        # Still capped: 40s opens the probe window again.
        clock.t += 40.1
        self.assertTrue(br.allow_request())


if __name__ == "__main__":
    unittest.main()
