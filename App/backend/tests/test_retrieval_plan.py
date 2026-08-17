"""Query-time retrieval plan: filters, preferences, decomposition, boosts."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.query import (  # noqa: E402
    current_fiscal_year,
    decompose_query,
    extract_retrieval_filters,
    extract_retrieval_preferences,
    plan_retrieval,
)
from app.retriever import apply_preference_boost, merge_retrieval_hits  # noqa: E402


class FilterExtractionTests(unittest.TestCase):
    def test_explicit_fy_becomes_a_hard_filter(self) -> None:
        self.assertEqual(
            extract_retrieval_filters("What is the VAT rate for FY2024-25?"),
            {"fiscal_year": "FY2024-25"},
        )

    def test_bare_calendar_year_is_not_a_filter(self) -> None:
        self.assertEqual(extract_retrieval_filters("What changed in 2026?"), {})

    def test_tax_type_is_not_a_hard_filter(self) -> None:
        self.assertEqual(extract_retrieval_filters("How do I register for VAT?"), {})


class PreferenceExtractionTests(unittest.TestCase):
    def test_current_year_prefers_configured_fy(self) -> None:
        prefer = extract_retrieval_preferences("What is the current VAT rate this fiscal year?")
        self.assertEqual(prefer.get("fiscal_year"), current_fiscal_year())
        self.assertEqual(prefer.get("tax_type"), "vat")

    def test_unset_env_matches_rate_tables(self) -> None:
        from app.tax.tables import resolve_fiscal_year

        env = {k: v for k, v in os.environ.items() if k != "CURRENT_FISCAL_YEAR"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(current_fiscal_year(), resolve_fiscal_year())

    def test_explicit_fy_does_not_also_prefer_current(self) -> None:
        prefer = extract_retrieval_preferences("VAT rate for FY2023-24 this year")
        self.assertNotIn("fiscal_year", prefer)

    def test_two_tax_types_are_not_a_single_preference(self) -> None:
        prefer = extract_retrieval_preferences("Compare VAT and PAYE")
        self.assertNotIn("tax_type", prefer)


class DecomposeQueryTests(unittest.TestCase):
    def test_single_intent_is_unchanged(self) -> None:
        q = "How do I register for a TIN?"
        self.assertEqual(decompose_query(q), [q])

    def test_and_also_splits(self) -> None:
        parts = decompose_query("How do I register for TIN and also how do I file VAT?")
        self.assertEqual(len(parts), 2)
        self.assertIn("register", parts[0].lower())
        self.assertIn("vat", parts[1].lower())

    def test_comparison_and_is_not_split(self) -> None:
        q = "What are the VAT and PAYE rates?"
        self.assertEqual(decompose_query(q), [q])


class PreferenceBoostTests(unittest.TestCase):
    def test_matching_fy_and_tax_sort_first(self) -> None:
        hits = [
            {
                "text": "old vat",
                "fiscal_year": "FY2023-24",
                "tax_type": "vat",
                "score_rrf": 0.20,
                "score_norm": 0.40,
            },
            {
                "text": "current vat",
                "fiscal_year": "FY2025-26",
                "tax_type": "vat",
                "score_rrf": 0.16,
                "score_norm": 0.38,
            },
        ]
        ranked = apply_preference_boost(
            hits, {"fiscal_year": "FY2025-26", "tax_type": "vat"}
        )
        self.assertEqual(ranked[0]["text"], "current vat")
        self.assertGreater(ranked[0]["score_norm"], 0.38)

    def test_empty_prefer_is_noop(self) -> None:
        hits = [{"text": "a", "score_rrf": 0.1}]
        self.assertIs(apply_preference_boost(hits, {}), hits)


class MergeHitsTests(unittest.TestCase):
    def test_dedupes_and_keeps_top_k(self) -> None:
        a = {"text": "same passage about tin registration here extra", "score_rrf": 0.1, "score_norm": 0.9}
        b = {"text": "same passage about tin registration here extra", "score_rrf": 0.2, "score_norm": 0.2}
        c = {"text": "a completely different customs valuation passage", "score_rrf": 0.3, "score_norm": 0.7}
        merged = merge_retrieval_hits([[a], [b, c]], top_k=2)
        texts = {h["text"] for h in merged}
        self.assertIn(c["text"], texts)
        self.assertLessEqual(len(merged), 2)


class PlanBundleTests(unittest.TestCase):
    def test_plan_keys(self) -> None:
        plan = plan_retrieval("What is VAT this fiscal year and also how do I get a TIN?")
        self.assertIn("filters", plan)
        self.assertIn("prefer", plan)
        self.assertIn("subqueries", plan)
        self.assertGreaterEqual(len(plan["subqueries"]), 2)


class CorrectiveFlagTests(unittest.TestCase):
    def test_flag_off_skips_correction(self) -> None:
        from app.corrective_rag import should_correct

        hits = [{"score_norm": 0.1}]
        with patch("app.flags.flags.is_enabled", return_value=False):
            self.assertFalse(should_correct(hits))


if __name__ == "__main__":
    unittest.main()
