"""Numeric grounding: money figures, not just percentages.

The FY2026-27 amendments moved two *thresholds* — the PAYE tax-free
threshold (235,000 -> 335,000) and the VAT registration threshold
(150m -> 300m) — without changing a single rate. The percentage-only
contradiction check could not see either, and comma-grouped amounts were
tokenised into fragments that matched almost any passage, so a stale
threshold quoted against a current passage scored as supported.
"""

from __future__ import annotations

import unittest

from app.claim_verifier import _numbers, verify_claims
from app.entailment import canonical_amounts, numeric_contradiction


class CanonicalAmountTests(unittest.TestCase):
    def test_grouped_suffixed_and_plain_forms_agree(self) -> None:
        for text in ("UGX 1,500,000", "1.5m", "1500000", "1 500 000"):
            with self.subTest(text=text):
                self.assertIn(1_500_000.0, canonical_amounts(text))

    def test_millions_and_billions(self) -> None:
        self.assertIn(300_000_000.0, canonical_amounts("300 million"))
        self.assertIn(2_000_000_000.0, canonical_amounts("2bn"))

    def test_percentages_are_not_read_as_amounts(self) -> None:
        self.assertNotIn(18.0, canonical_amounts("VAT is charged at 18%"))

    def test_figures_no_longer_shatter_into_fragments(self) -> None:
        # The old extractor produced {"1", "500", "000"} — "000" matched
        # nearly any passage containing a grouped number.
        self.assertEqual(_numbers("UGX 1,500,000"), {"1500000.00"})

    def test_close_amounts_stay_distinct(self) -> None:
        # Six-significant-digit formatting would collapse these onto one key.
        self.assertNotEqual(_numbers("UGX 1,234,567"), _numbers("UGX 1,234,568"))

    def test_percentages_survive_alongside_amounts(self) -> None:
        self.assertEqual(_numbers("300 million at 18%"), {"300000000.00", "18%"})


class MoneyContradictionTests(unittest.TestCase):
    def test_stale_paye_threshold_is_a_contradiction(self) -> None:
        self.assertTrue(
            numeric_contradiction(
                "the PAYE tax-free threshold is UGX 235,000 per month",
                "the monthly tax-free threshold is UGX 335,000",
            )
        )

    def test_stale_vat_registration_threshold_is_a_contradiction(self) -> None:
        self.assertTrue(
            numeric_contradiction(
                "you must register for VAT above UGX 150 million",
                "registration is compulsory once turnover reaches UGX 300,000,000",
            )
        )

    def test_percentage_rule_still_fires(self) -> None:
        self.assertTrue(numeric_contradiction("VAT is 20%", "VAT is charged at 18%"))


class MoneyContradictionPrecisionTests(unittest.TestCase):
    """The money rule must not fire on legitimate arithmetic."""

    def test_computed_totals_are_not_contradictions(self) -> None:
        # A calculator result carries amounts the passage never states.
        self.assertFalse(
            numeric_contradiction(
                "PAYE comes to UGX 202,000 and net pay is UGX 798,000",
                "the rate on this band is 30%",
            )
        )

    def test_matching_threshold_is_not_a_contradiction(self) -> None:
        self.assertFalse(
            numeric_contradiction(
                "the threshold is UGX 335,000",
                "a tax-free threshold of UGX 335,000 per month",
            )
        )

    def test_claim_without_figures_is_not_a_contradiction(self) -> None:
        self.assertFalse(
            numeric_contradiction(
                "you must register for VAT", "the threshold is UGX 300,000,000"
            )
        )

    def test_context_without_figures_is_not_a_contradiction(self) -> None:
        self.assertFalse(
            numeric_contradiction(
                "the threshold is UGX 335,000", "registration is handled on the portal"
            )
        )


class VerifyClaimsIntegrationTests(unittest.TestCase):
    _PASSAGE = "The monthly PAYE tax-free threshold is UGX 335,000 and VAT is charged at 18%."

    def _report(self, reply: str) -> dict:
        hits = [{"text": self._PASSAGE}]
        citations = [{"ref": "[1]", "passage": self._PASSAGE}]
        return verify_claims(reply, citations, hits)

    def test_stale_threshold_claim_escalates(self) -> None:
        report = self._report("The PAYE tax-free threshold is UGX 235,000 per month [1].")
        self.assertEqual(report["decision"], "escalate")
        self.assertEqual(len(report["contradicted_claims"]), 1)

    def test_correct_threshold_claim_is_approved(self) -> None:
        report = self._report("The PAYE tax-free threshold is UGX 335,000 per month [1].")
        self.assertEqual(report["decision"], "approve")
        self.assertEqual(report["score"], 1.0)

    def test_wrong_rate_still_escalates(self) -> None:
        report = self._report("VAT is charged at 20% on taxable supplies [1].")
        self.assertEqual(report["decision"], "escalate")


if __name__ == "__main__":
    unittest.main()
