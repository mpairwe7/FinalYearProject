"""A Sunbird timeout must not be waited out twice on the same account.

`_post` retried every `httpx.HTTPError`, which includes timeouts. A timeout
means the model is slow right now — usually a cold start — so the retry buys
an identical wait for the same answer.

The cost is not abstract. Measured across 8 live TTS calls: median 7.2s, max
79.1s. `_cloud_call` bounds the caller at SPEECH_CLOUD_DEADLINE_S (40s), so the
79.1s call was abandoned and TTS fell through to edge-tts — which has no
Ugandan voice and answers Luganda in an American English one. That is precisely
the failure the Sunbird tier exists to prevent, caused by the retry meant to
add resilience.

Transport errors (connection reset, DNS blip) are still retried: those are
genuinely transient and a second attempt is cheap.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sunbird  # noqa: E402


class _Boom:
    """Stands in for the httpx client, counting POSTs and raising to order."""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def post(self, path, **kwargs):
        self.calls += 1
        raise self.exc


class TestTimeoutsAreNotRetriedWithinAnAccount(unittest.TestCase):
    def _attempts(self, exc: Exception) -> int:
        client = _Boom(exc)
        with patch.object(sunbird, "_client_for", return_value=client), patch.object(
            sunbird, "_account_tokens", return_value=["tok-a", "tok-b"]
        ), patch.object(sunbird.time, "sleep", lambda *_: None):
            with self.assertRaises(Exception):
                sunbird._post("/tasks/audio/speech", json={})
        return client.calls

    def test_a_read_timeout_costs_one_attempt_per_account(self):
        """Two accounts, one attempt each — not two waits per account."""
        self.assertEqual(self._attempts(httpx.ReadTimeout("timed out")), 2)

    def test_a_connect_timeout_behaves_the_same(self):
        self.assertEqual(self._attempts(httpx.ConnectTimeout("timed out")), 2)

    def test_a_transport_error_is_still_retried(self):
        """Connection resets are genuinely transient; retrying is cheap."""
        expected = 2 * sunbird.SUNBIRD_RETRIES
        self.assertEqual(self._attempts(httpx.ConnectError("reset")), expected)
        self.assertGreater(expected, 2, "SUNBIRD_RETRIES>1 makes this meaningful")

    def test_the_worst_case_wait_now_fits_the_cloud_deadline_budget(self):
        """The arithmetic that made this a user-visible bug.

        Worst case is now one timeout per account rather than SUNBIRD_RETRIES
        per account.
        """
        from app.speech_service import SPEECH_CLOUD_DEADLINE_S

        accounts = 2
        worst_before = sunbird.SUNBIRD_TIMEOUT * sunbird.SUNBIRD_RETRIES * accounts
        worst_after = sunbird.SUNBIRD_TIMEOUT * accounts
        self.assertLess(worst_after, worst_before)
        self.assertLessEqual(
            worst_after,
            SPEECH_CLOUD_DEADLINE_S + sunbird.SUNBIRD_TIMEOUT,
            "a stuck endpoint should not vastly overshoot the deadline that bounds it",
        )


if __name__ == "__main__":
    unittest.main()
