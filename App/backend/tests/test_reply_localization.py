"""Replies reach the taxpayer in the language they asked in.

Before this, a Luganda question was answered by asking the generation model
to "Respond in locale: lg". Qwen3-8B has no Luganda, and no LoRA adapter is
loaded on the CPU deployments or behind vLLM, so it degenerated into a
repetition loop — "EFRIS kye ki kati kozesa kozesa kozesa…" — which is worse
than an English answer, not better. sunbird.translate_from_english() existed
the whole time and had no callers.

Answers are now generated in English and translated by Sunbird's
Ugandan-language MT. Two properties matter and are asserted here:

* an English answer is never silently shipped to a non-English caller; and
* translation failing never costs the taxpayer the answer. Every degraded
  path — exception, empty, or a collapsed one-word response — falls back to
  the English text, because someone reading English as a second language is
  served by an English answer and served by nothing at all by an empty one.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app import service


class LocalizeReplyTest(unittest.TestCase):
    ENGLISH = "The standard VAT rate in Uganda is 18% on taxable supplies."

    def test_english_locale_is_passed_through_untouched(self) -> None:
        for locale in ("en", ""):
            with self.subTest(locale=locale):
                with mock.patch.object(service, "localize_reply", wraps=service.localize_reply):
                    self.assertEqual(service.localize_reply(self.ENGLISH, locale), self.ENGLISH)

    def test_translated_text_replaces_the_english(self) -> None:
        luganda = "Omusolo gwa VAT mu Uganda guli ebitundu 18 ku buli kikumi."
        with mock.patch("app.sunbird.translate_from_english", return_value=luganda):
            self.assertEqual(service.localize_reply(self.ENGLISH, "lg"), luganda)

    def test_translation_failure_serves_english_rather_than_an_error(self) -> None:
        with mock.patch("app.sunbird.translate_from_english", side_effect=RuntimeError("boom")):
            self.assertEqual(service.localize_reply(self.ENGLISH, "lg"), self.ENGLISH)

    def test_empty_translation_serves_english(self) -> None:
        for bad in (None, "", "   "):
            with self.subTest(returned=repr(bad)):
                with mock.patch("app.sunbird.translate_from_english", return_value=bad):
                    self.assertEqual(service.localize_reply(self.ENGLISH, "lg"), self.ENGLISH)

    def test_collapsed_translation_is_rejected(self) -> None:
        """A one-word MT response must not replace a full answer."""
        with mock.patch("app.sunbird.translate_from_english", return_value="Yee"):
            self.assertEqual(service.localize_reply(self.ENGLISH, "lg"), self.ENGLISH)

    def test_blank_reply_is_not_sent_for_translation(self) -> None:
        with mock.patch("app.sunbird.translate_from_english") as translate:
            self.assertEqual(service.localize_reply("", "lg"), "")
            translate.assert_not_called()


class GenerationLanguageTest(unittest.TestCase):
    """The model is only asked for a language it can actually produce."""

    def test_no_adapter_means_no_respond_in_locale_instruction(self) -> None:
        from app import llm

        with mock.patch.object(llm, "_active_adapter", None):
            self.assertFalse(llm.can_generate_in_locale("lg"))
            self.assertTrue(llm.can_generate_in_locale("en"))

    def test_a_matching_adapter_permits_direct_generation(self) -> None:
        from app import llm

        with mock.patch.object(llm, "_active_adapter", "lg"):
            self.assertTrue(llm.can_generate_in_locale("lg"))
            self.assertFalse(llm.can_generate_in_locale("nyn"))


if __name__ == "__main__":
    unittest.main()
