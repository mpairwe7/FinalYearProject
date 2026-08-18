"""A question asked in Luganda reaches the same answer paths as English.

Issue #302. The deterministic routers — TIN clarification, calculators, rate
tables, workflows — match English patterns and run *before* retrieval, which
is where translation used to happen (inside HybridRetriever). So a Luganda
question reached none of them and fell through to an abstention: the
languages most in need of a guided path were getting the weakest one.

Verified against the live Sunbird MT and Qwen3-8B, "Nkola ntya okuwandiisa
TIN?" went from ``abstained`` to ``workflow``, answered in Luganda.

Two distinct defects are pinned here, because each failed on its own:

* MT returns the *expanded* term where the routers key on the abbreviation
  ("what is the value-added tax rate", "a Tax Identification Number"), so the
  translated English still missed every matcher; and
* ChatModel.generate() localized against the locale it was *called* with,
  while detect_language() sets the real one inside _generate_en. A caller
  that just sends Luganda text sends no locale at all, so the reply came back
  in English — the exact path a taxpayer takes.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app.query import canonicalize_tax_terms


class CanonicalizeTaxTermsTest(unittest.TestCase):
    def test_expanded_terms_become_the_abbreviations_matchers_use(self) -> None:
        cases = [
            ("What is the value-added tax rate in Uganda?", "What is the VAT rate in Uganda?"),
            ("What is the value added tax rate?", "What is the VAT rate?"),
            ("What is the pay as you earn rate?", "What is the PAYE rate?"),
            # MT renders this both with and without "payer".
            ("How do I register for a Tax Identification Number?", "How do I register for a TIN?"),
            (
                "How do I register for a taxpayer identification number?",
                "How do I register for a TIN?",
            ),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(canonicalize_tax_terms(raw), expected)

    def test_text_already_using_abbreviations_is_untouched(self) -> None:
        for text in ("What is the VAT rate in Uganda?", "How do I register for a TIN?"):
            self.assertEqual(canonicalize_tax_terms(text), text)

    def test_unrelated_text_is_left_alone(self) -> None:
        text = "How do I object to a tax assessment I disagree with?"
        self.assertEqual(canonicalize_tax_terms(text), text)

    def test_empty_input_is_safe(self) -> None:
        self.assertEqual(canonicalize_tax_terms(""), "")


class DeterministicRoutingReachabilityTest(unittest.TestCase):
    """The English form of a local-language question must hit a fast path."""

    @classmethod
    def setUpClass(cls) -> None:
        from app import service

        cls.model = service.ChatModel()

    def _mode(self, text: str) -> str | None:
        canon = canonicalize_tax_terms(text)
        result = self.model._maybe_handle_fast_paths(
            message=canon, rewritten=canon, thread_id="t", locale="en"
        )
        return None if result is None else result.get("retrieval_mode")

    def test_translated_rate_question_reaches_the_calculator(self) -> None:
        self.assertEqual(self._mode("What is the value-added tax rate in Uganda?"), "calculator")

    def test_translated_tin_question_reaches_the_workflow(self) -> None:
        self.assertIsNotNone(self._mode("How do I register for a Tax Identification Number?"))


class EffectiveLocaleTest(unittest.TestCase):
    """Localization keys off the detected locale, not the requested one."""

    def test_autodetected_locale_drives_localization(self) -> None:
        from app import service

        model = service.ChatModel.__new__(service.ChatModel)
        detected = {"reply": "The VAT rate is 18%.", "locale": "lg"}
        with mock.patch.object(service.ChatModel, "_generate_en", return_value=detected), \
                mock.patch.object(
                    service, "localize_reply", return_value="Omusolo gwa VAT..."
                ) as localize:
            # Caller sends no locale at all — the default "en". _generate_en is
            # mocked above, so nothing reaches a model; this asserts only which
            # locale generate() hands to localize_reply.
            # nosemgrep: ura-llm01-raw-user-input-to-llm
            out = service.ChatModel.generate(model, message="Omusolo gwa VAT guli gwa bbeeyi ki?")
        localize.assert_called_once()
        self.assertEqual(localize.call_args.args[1], "lg")
        self.assertEqual(out["reply"], "Omusolo gwa VAT...")

    def test_english_turn_is_not_sent_for_translation(self) -> None:
        from app import service

        model = service.ChatModel.__new__(service.ChatModel)
        english = {"reply": "The VAT rate is 18%.", "locale": "en"}
        with mock.patch.object(service.ChatModel, "_generate_en", return_value=english), \
                mock.patch.object(service, "localize_reply") as localize:
            # nosemgrep: ura-llm01-raw-user-input-to-llm  # _generate_en mocked; no model call.
            out = service.ChatModel.generate(model, message="What is the VAT rate?")
        localize.assert_not_called()
        self.assertEqual(out["reply"], "The VAT rate is 18%.")


if __name__ == "__main__":
    unittest.main()
