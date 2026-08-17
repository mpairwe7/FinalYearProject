"""G25 per-segment metrics and G26 flag-variant grouping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import _compute_by_segment  # noqa: E402
from app.flags import flags  # noqa: E402
from app.retriever import canonical_source_url  # noqa: E402


def _sample(question: str, *, locale: str = "en", variants: str = "{}", topic: str = "") -> dict:
    ctx = ["VAT is charged at 18 percent on taxable supplies in Uganda."]
    return {
        "question": question,
        "answer": "VAT is 18 percent on taxable supplies.",
        "contexts": ctx,
        "locale": locale,
        "flag_variants": variants,
        "topic_tag": topic,
    }


class SegmentBreakdownTests(unittest.TestCase):
    def test_topic_locale_taxpayer_and_variant_dimensions(self) -> None:
        samples = [
            _sample("What is the VAT rate?", locale="en", variants='{"hyde":"off"}', topic="vat"),
            _sample("How is VAT filed?", locale="en", variants='{"hyde":"off"}', topic="vat"),
            _sample("VAT registration threshold?", locale="en", variants='{"hyde":"off"}', topic="vat"),
            _sample("Import duty on CIF?", locale="lg", variants='{"hyde":"on"}', topic="customs"),
            _sample("Customs tariff HS code?", locale="lg", variants='{"hyde":"on"}', topic="customs"),
            _sample("CIF valuation at customs?", locale="lg", variants='{"hyde":"on"}', topic="customs"),
        ]
        by_segment = _compute_by_segment(samples, "heuristic")
        self.assertIn("topic", by_segment)
        self.assertIn("vat", by_segment["topic"])
        self.assertIn("customs", by_segment["topic"])
        self.assertIn("locale", by_segment)
        self.assertIn("en", by_segment["locale"])
        self.assertIn("lg", by_segment["locale"])
        self.assertIn("taxpayer_type", by_segment)
        self.assertIn("variant", by_segment)
        self.assertIn("hyde:off", by_segment["variant"])
        self.assertIn("hyde:on", by_segment["variant"])

    def test_groups_smaller_than_three_are_omitted(self) -> None:
        samples = [_sample("What is VAT?"), _sample("VAT rate again?")]
        self.assertEqual(_compute_by_segment(samples, "heuristic"), {})


class VariantLoggingTests(unittest.TestCase):
    def test_logged_variants_cover_retrieval_flags(self) -> None:
        labels = flags.logged_variants(subject="user-1")
        self.assertEqual(labels["hyde"], "off")
        self.assertEqual(labels["translate_retrieve"], "on")
        fields = flags.experiment_log_fields(subject="user-1", locale="lg")
        self.assertEqual(fields["locale"], "lg")
        self.assertIn("hyde", fields["flag_variants"])


class CanonicalUrlTests(unittest.TestCase):
    def test_stored_https_wins(self) -> None:
        self.assertEqual(
            canonical_source_url("crawl.jsonl", "https://ura.go.ug/en/vat"),
            "https://ura.go.ug/en/vat",
        )

    def test_faq_filename_gets_portal(self) -> None:
        self.assertEqual(canonical_source_url("ura_vat_faqs.csv"), "https://ura.go.ug")

    def test_unrelated_source_stays_empty(self) -> None:
        self.assertEqual(canonical_source_url("notes.txt"), "")


if __name__ == "__main__":
    unittest.main()
