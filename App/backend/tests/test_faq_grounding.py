"""Regression tests for FAQ intent binding and unsupported-answer refusal."""

from __future__ import annotations

import unittest
from unittest import mock

from app.cache import _cache_queries_compatible
from app.claim_verifier import verify_claims
from app.service import _filter_unbound_faq_hits, _simple_search


class FAQIntentBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.faqs = {
            "vat": [
                {
                    "question": "When is VAT registration compulsory?",
                    "answer": (
                        "When taxable turnover exceeds UGX 37.5m in any 3 consecutive "
                        "months (UGX 150m annual), which is the VAT registration threshold."
                    ),
                    "source": "ura_taxation_handbook_fy2025_26_faqs.csv",
                },
            ],
            "unrelated": [
                {
                    "question": "Do PAYE and WHT returns share the same deadline?",
                    "answer": "Yes, file and pay by the 15th of the following month.",
                    "source": "ura_withholding_tax_faqs.csv",
                },
            ],
        }

    def test_unrelated_topic_overlap_is_not_retrieved(self) -> None:
        with mock.patch("app.service._get_bm25_encoder", return_value=None):
            hits = _simple_search(
                "What is the tax deadline for a fictional moon tax?",
                self.faqs,
                top_k=4,
            )
        self.assertEqual(hits, [])

    def test_subject_and_intent_match_is_retained(self) -> None:
        with mock.patch("app.service._get_bm25_encoder", return_value=None):
            hits = _simple_search("What is the VAT registration threshold?", self.faqs)
        self.assertEqual(len(hits), 1)
        self.assertIn("VAT registration", hits[0]["question"])
        self.assertGreaterEqual(hits[0]["_faq_match_score"], 0.8)

    def test_indexed_unbound_faq_hit_is_removed(self) -> None:
        hits = [
            {
                "doc_type": "csv",
                "source": "ura_withholding_tax_faqs.csv",
                "question": "Do PAYE and WHT returns share the same deadline?",
                "answer": "Yes, file and pay by the 15th of the following month.",
            }
        ]
        self.assertEqual(
            _filter_unbound_faq_hits("Tell me about quantum computing", hits), []
        )


class FAQSafetyRegressionTests(unittest.TestCase):
    def test_decimal_facts_are_verified_as_one_claim(self) -> None:
        passage = (
            "When taxable turnover exceeds UGX 37.5m in any 3 consecutive months "
            "(UGX 150m annual)."
        )
        report = verify_claims(
            passage + " [1]",
            [{"ref": "[1]", "passage": passage}],
            [{"text": passage}],
        )
        self.assertEqual(report["decision"], "approve")
        self.assertEqual(report["unsupported_claims"], [])

    def test_neighbouring_faq_cache_intents_do_not_cross_reuse(self) -> None:
        self.assertTrue(
            _cache_queries_compatible(
                "What is the current VAT rate?", "What is the standard VAT rate?"
            )
        )
        self.assertFalse(
            _cache_queries_compatible(
                "What is the VAT registration threshold?", "What is the VAT rate?"
            )
        )


if __name__ == "__main__":
    unittest.main()
