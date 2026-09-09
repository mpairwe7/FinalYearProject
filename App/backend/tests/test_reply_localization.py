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

from app import mt, service


class LocalizeReplyTest(unittest.TestCase):
    ENGLISH = "The standard VAT rate in Uganda is 18% on taxable supplies."

    def setUp(self) -> None:
        # localize_reply memoises a translation once it has passed its guards
        # (see app/mt.py). That is right in production — a backend that fails
        # after one success should still serve the translation it already
        # produced — and it makes these cases order-dependent, because they
        # all translate the same sentence with different backend behaviour.
        mt.cache.clear()

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

    def test_swahili_translation_with_prefix_percentage_survives(self) -> None:
        """'asilimia 18' in Swahili must survive figures_survived check against '18%'."""
        swahili = "Kiwango cha kawaida cha ushuru wa thamani nchini Uganda ni asilimia 18 kwa bidhaa."
        with mock.patch("app.sunbird.translate_from_english", return_value=swahili):
            self.assertEqual(service.localize_reply(self.ENGLISH, "sw"), swahili)

    def test_swahili_word_percentage_survives(self) -> None:
        """'asilimia kumi na nane' in Swahili must survive figures_survived check against '18%'."""
        swahili = "Kiwango cha kawaida cha ushuru wa thamani nchini Uganda ni asilimia kumi na nane kwa bidhaa."
        with mock.patch("app.sunbird.translate_from_english", return_value=swahili):
            self.assertEqual(service.localize_reply(self.ENGLISH, "sw"), swahili)


class ProtectedLocalizationTest(unittest.TestCase):
    """End to end: what the taxpayer actually receives when MT touches a figure."""

    ENGLISH = "The standard VAT rate in Uganda is 18% on taxable supplies."
    THRESHOLD = "The VAT registration threshold is UGX 150,000,000 a year."

    def setUp(self) -> None:
        mt.cache.clear()

    def test_a_digit_mangling_translator_no_longer_costs_the_figure(self):
        """The headline change.

        This translator transposes every digit it is shown — the "UGX 235,000
        comes back as UGX 253,000" failure. Before protection it produced a
        wrong figure, the guard caught it, and the taxpayer got English.
        Now it is never shown a digit, so there is nothing to transpose and
        the taxpayer gets Luganda with the right number.
        """

        def _transposing(text: str, locale: str) -> str:
            swapped = text.replace("18", "81").replace("150,000,000", "105,000,000")
            return f"Omusolo gwa VAT mu Uganda guli {swapped} ku bintu ebiguzibwa."

        with mock.patch("app.sunbird.translate_from_english", side_effect=_transposing):
            out = service.localize_reply(self.ENGLISH, "lg")
        self.assertNotEqual(out, self.ENGLISH)
        self.assertIn("18%", out)
        self.assertTrue(mt.figures_survived(self.ENGLISH, out))

    def test_a_translator_that_echoes_a_sentinel_never_ships_the_fragment(self):
        """Residue is visible garbage; English is the honest answer instead."""

        def _echoing(text: str, locale: str) -> str:
            return f"Omusolo gwa VAT guli {text} ne {text} ku bintu ebiguzibwa."

        with mock.patch("app.sunbird.translate_from_english", side_effect=_echoing):
            out = service.localize_reply(self.ENGLISH, "lg")
        self.assertNotIn("NMBR", out)

    def test_a_translator_that_drops_the_sentinel_falls_back_unprotected(self):
        """Protection can only add coverage, never remove it.

        This tier cannot carry a sentinel — it drops unknown tokens, as an NMT
        model does — but translates the plain sentence correctly. The protected
        pass fails its guards, the unprotected retry succeeds, and the taxpayer
        gets the vernacular answer they would have got before this change.
        """
        luganda = "Omusolo gwa VAT mu Uganda guli ebitundu 18 ku buli kikumi."

        def _drops_sentinels(text: str, locale: str) -> str:
            if "NMBR" in text:
                return "Omusolo gwa VAT mu Uganda guli ebitundu ku buli kikumi."
            return luganda

        with mock.patch("app.sunbird.translate_from_english", side_effect=_drops_sentinels):
            self.assertEqual(service.localize_reply(self.ENGLISH, "lg"), luganda)

    def test_the_kill_switch_restores_the_unprotected_path(self):
        """``MT_PROTECT_FIGURES=false`` means the translator sees the digits."""
        seen: list[str] = []

        def _record(text: str, locale: str) -> str:
            seen.append(text)
            return "Ekkomo ly'okwewandiisa VAT liri obukadde 150 buli mwaka."

        with mock.patch.object(mt, "MT_PROTECT_FIGURES", False), mock.patch(
            "app.sunbird.translate_from_english", side_effect=_record
        ):
            service.localize_reply(self.THRESHOLD, "lg")
        self.assertEqual(len(seen), 1)
        self.assertIn("150,000,000", seen[0])
        self.assertNotIn("NMBR", seen[0])

    def test_a_reply_without_figures_is_never_masked(self):
        """No sentinels, no retry, no extra round trip on the common case."""
        english = "Visit any URA office or call the contact centre for help."
        seen: list[str] = []

        def _record(text: str, locale: str) -> str:
            seen.append(text)
            return "Genda mu ofiisi ya URA yonna oba okube essimu."

        with mock.patch("app.sunbird.translate_from_english", side_effect=_record):
            service.localize_reply(english, "lg")
        self.assertEqual(seen, [english])


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
