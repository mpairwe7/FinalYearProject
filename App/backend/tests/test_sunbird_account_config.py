"""`/ready` must show whether Sunbird failover can actually fail over.

`sunbird._post` retries within an account and then moves to the next one.
That design only works with two accounts, and this deployment has been
running on one: `SUNBIRD_API_TOKEN` unset, `SUNBIRD_FALLBACK_API_TOKEN` set.
`is_available()` returns True either way, so from outside the pod a
single-account deployment looked identical to a healthy two-account one.

It stopped looking identical when that account's daily quota returned
`429 RATE_LIMIT_ERROR — "Daily quota exceeded"` with `retry_after_seconds:
16276` during one corpus-coverage sweep: every Luganda and Kiswahili
narration lost its native Sunbird voice for four and a half hours, with no
second account to move to.

`account_summary()` names the accounts by role rather than counting them, so
"fallback" on its own reads as the misconfiguration it is instead of as
"the fallback is working". It must never emit a token.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sunbird  # noqa: E402

_PRIMARY = "primary-token-value"
_FALLBACK = "fallback-token-value"


class AccountSummaryTests(unittest.TestCase):
    def _summary(self, primary: str, fallback: str) -> str:
        with patch.object(sunbird, "SUNBIRD_API_TOKEN", primary), \
                patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", fallback):
            return sunbird.account_summary()

    def test_no_tokens_reads_unavailable(self):
        self.assertEqual(self._summary("", ""), "unavailable")

    def test_both_accounts_are_named_in_priority_order(self):
        self.assertEqual(self._summary(_PRIMARY, _FALLBACK), "primary+fallback")

    def test_primary_only(self):
        self.assertEqual(self._summary(_PRIMARY, ""), "primary")

    def test_fallback_only_is_visible_as_such(self):
        """The state this deployment was actually in."""
        self.assertEqual(self._summary("", _FALLBACK), "fallback")

    def test_the_summary_never_contains_a_token(self):
        for primary, fallback in (("", ""), (_PRIMARY, ""), ("", _FALLBACK), (_PRIMARY, _FALLBACK)):
            with self.subTest(primary=bool(primary), fallback=bool(fallback)):
                summary = self._summary(primary, fallback)
                self.assertNotIn(_PRIMARY, summary)
                self.assertNotIn(_FALLBACK, summary)

    def test_availability_alone_cannot_distinguish_one_account_from_two(self):
        """Why the extra field exists at all."""
        with patch.object(sunbird, "SUNBIRD_API_TOKEN", ""), \
                patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", _FALLBACK):
            one = (sunbird.is_available(), len(sunbird._account_tokens()))
        with patch.object(sunbird, "SUNBIRD_API_TOKEN", _PRIMARY), \
                patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", _FALLBACK):
            two = (sunbird.is_available(), len(sunbird._account_tokens()))
        self.assertEqual(one[0], two[0], "is_available cannot tell these apart — hence the field")
        self.assertNotEqual(one[1], two[1])


if __name__ == "__main__":
    unittest.main()
