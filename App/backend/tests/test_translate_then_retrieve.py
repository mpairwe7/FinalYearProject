"""The translated-retrieval fallback in `_simple_search`.

The corpus is English. A question asked in Luganda or Runyankole shares no
terms with it, so BM25 has nothing to score and the coverage gate rejects what
little it finds — measured on the Luganda golden set, all 12 questions returned
zero candidates from the keyword path. Translating and retrying rescues 5 of
them.

The property that matters most here is the one that is easy to lose: English
must be completely unaffected. The fallback is lazy by design — it runs only
after the untranslated attempt comes back empty — so it can add candidates
where there were none but never displace or reorder existing ones.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import service  # noqa: E402


def _index() -> dict[str, list[dict[str, str]]]:
    return {
        "vat": [
            {
                "question": "What is the VAT rate in Uganda?",
                "answer": "The standard VAT rate in Uganda is 18 percent under the VAT Act.",
            }
        ],
        "tin": [
            {
                "question": "How do I register for a TIN?",
                "answer": "Register for a TIN on the URA web portal using your national ID.",
            }
        ],
    }


class TestEnglishIsUntouched(unittest.TestCase):
    def test_results_identical_with_and_without_locale(self):
        idx = _index()
        for query in ("What is the VAT rate in Uganda?", "How do I register for a TIN?"):
            with self.subTest(query=query):
                bare = [h["question"] for h in service._simple_search(query, idx, top_k=4)]
                with_en = [
                    h["question"]
                    for h in service._simple_search(query, idx, top_k=4, locale="en")
                ]
                self.assertEqual(bare, with_en)

    def test_english_never_calls_the_translator(self):
        """locale='en' must not spend a network round trip."""
        with patch("app.sunbird.translate_to_english") as tr:
            service._simple_search("What is the VAT rate in Uganda?", _index(), locale="en")
            tr.assert_not_called()

    def test_a_matching_non_english_query_does_not_translate(self):
        """The fallback is lazy: a hit on the original text short-circuits it."""
        with patch("app.sunbird.translate_to_english") as tr:
            hits = service._simple_search(
                "What is the VAT rate in Uganda?", _index(), locale="lg"
            )
            self.assertTrue(hits)
            tr.assert_not_called()


class TestNonEnglishFallback(unittest.TestCase):
    def test_translation_rescues_a_query_that_found_nothing(self):
        idx = _index()
        luganda = "Omusolo gwa VAT gw'ameka mu Uganda?"
        self.assertEqual(service._simple_search(luganda, idx, top_k=4), [])

        with patch(
            "app.sunbird.translate_to_english",
            return_value="What is the VAT rate in Uganda?",
        ):
            rescued = service._simple_search(luganda, idx, top_k=4, locale="lg")
        self.assertTrue(rescued)
        self.assertIn("VAT", rescued[0]["question"])

    def test_degrades_quietly_when_translation_is_unavailable(self):
        idx = _index()
        luganda = "Omusolo gwa VAT gw'ameka mu Uganda?"
        for outcome in ({"return_value": None}, {"side_effect": RuntimeError("offline")}):
            with self.subTest(outcome=list(outcome)[0]):
                with patch("app.sunbird.translate_to_english", **outcome):
                    self.assertEqual(
                        service._simple_search(luganda, idx, top_k=4, locale="lg"), []
                    )

    def test_no_retry_when_translation_returns_the_input(self):
        """An echoing translator must not trigger a second identical scan."""
        idx = _index()
        luganda = "Omusolo gwa VAT gw'ameka mu Uganda?"
        with patch("app.sunbird.translate_to_english", return_value=luganda):
            self.assertEqual(service._simple_search(luganda, idx, top_k=4, locale="lg"), [])

    def test_an_off_domain_translation_is_still_rejected(self):
        """Translation must not become a way past the relevance gate."""
        idx = _index()
        with patch(
            "app.sunbird.translate_to_english",
            return_value="How do I bake banana bread at home?",
        ):
            self.assertEqual(
                service._simple_search("Nsobola ntya okufumba?", idx, top_k=4, locale="lg"),
                [],
            )


if __name__ == "__main__":
    unittest.main()
