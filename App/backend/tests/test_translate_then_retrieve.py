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


class TestJudgeRescueIsOffByDefault(unittest.TestCase):
    """The judge makes a network call, so nothing may reach it implicitly.

    It sits last in the ladder — after the untranslated pass, the translated
    pass and the coverage gate have all produced nothing — and is disabled
    unless FAQ_JUDGE_ENABLED is set. A unit run or a deployment without a
    configured model must behave exactly as it did before it existed.
    """

    def test_disabled_by_default(self):
        self.assertFalse(service.FAQ_JUDGE_ENABLED)

    def test_returns_nothing_and_calls_no_model_when_disabled(self):
        with patch("app.providers.gateway.gemini_generate") as gen:
            self.assertEqual(service._judge_rescue("anything", _index(), 4), [])
            gen.assert_not_called()

    def test_an_unconfident_verdict_is_refused(self):
        """A confidently wrong tax answer costs more than 'I could not find it'."""
        with (
            patch.object(service, "FAQ_JUDGE_ENABLED", True),
            patch("app.providers.config.is_gemini_configured", return_value=True),
            patch(
                "app.providers.gateway.gemini_generate",
                return_value='{"pick": 1, "confident": false}',
            ),
        ):
            self.assertEqual(service._judge_rescue("What is VAT?", _index(), 4), [])

    def test_a_confident_verdict_is_accepted(self):
        with (
            patch.object(service, "FAQ_JUDGE_ENABLED", True),
            patch("app.providers.config.is_gemini_configured", return_value=True),
            patch(
                "app.providers.gateway.gemini_generate",
                return_value='```json\n{"pick": 1, "confident": true}\n```',
            ),
        ):
            hits = service._judge_rescue("What is the VAT rate?", _index(), 4)
        self.assertTrue(hits)
        self.assertIn("VAT", hits[0]["question"])

    def test_a_failed_call_degrades_to_no_answer(self):
        for outcome in ({"side_effect": RuntimeError("offline")}, {"return_value": "not json"}):
            with self.subTest(outcome=list(outcome)[0]):
                with (
                    patch.object(service, "FAQ_JUDGE_ENABLED", True),
                    patch("app.providers.config.is_gemini_configured", return_value=True),
                    patch("app.providers.gateway.gemini_generate", **outcome),
                ):
                    self.assertEqual(service._judge_rescue("What is VAT?", _index(), 4), [])

    def test_an_out_of_range_pick_is_refused(self):
        with (
            patch.object(service, "FAQ_JUDGE_ENABLED", True),
            patch("app.providers.config.is_gemini_configured", return_value=True),
            patch(
                "app.providers.gateway.gemini_generate",
                return_value='{"pick": 99, "confident": true}',
            ),
        ):
            self.assertEqual(service._judge_rescue("What is VAT?", _index(), 4), [])
