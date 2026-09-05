from __future__ import annotations

import unittest

from app.guardrails import OutputGuard


class OutputGuardSanitizerTests(unittest.TestCase):
    def test_sanitize_strips_reasoning_preamble_and_keeps_answer(self) -> None:
        raw = (
            "Okay, the user is asking what a Taxpayer Identification Number (TIN) is. "
            "Let me check the provided passages to find the answer.\n\n"
            "Looking at passage [1], it mentions TIN as a 10-digit number acting as an "
            "account. I should combine the relevant parts from the passages.\n\n"
            "A Taxpayer Identification Number (TIN) is a 10-digit number that acts as "
            "an account for tax purposes [1]."
        )

        sanitized = OutputGuard.sanitize(raw)

        self.assertEqual(
            sanitized,
            "A Taxpayer Identification Number (TIN) is a 10-digit number that acts as "
            "an account for tax purposes [1].",
        )

    def test_sanitize_drops_reasoning_only_chunk(self) -> None:
        raw = "Looking at passage [1], it mentions TIN as a 10-digit number acting as an account."

        self.assertEqual(OutputGuard.sanitize(raw), "")

    def test_sanitize_preserves_normal_user_facing_answer(self) -> None:
        raw = "A Taxpayer Identification Number (TIN) is a 10-digit number used for tax purposes [1]."

        self.assertEqual(OutputGuard.sanitize(raw), raw)

    def test_sanitize_preserves_however_with_factual_content(self) -> None:
        raw = "However, the VAT rate is 18% on most goods and services in Uganda."

        self.assertEqual(OutputGuard.sanitize(raw), raw)

    def test_sanitize_preserves_the_prefix_answers(self) -> None:
        raw = "The standard VAT rate in Uganda is 18% [1]."

        self.assertEqual(OutputGuard.sanitize(raw), raw)

    def test_sanitize_still_strips_reasoning_with_however(self) -> None:
        raw = (
            "Okay, the user is asking about VAT. "
            "The standard VAT rate in Uganda is 18% [1]."
        )
        sanitized = OutputGuard.sanitize(raw)
        self.assertIn("18%", sanitized)
        self.assertNotIn("Okay, the user", sanitized)

    def test_official_ura_emails_are_not_redacted(self) -> None:
        raw = "You can contact URA via services@ura.go.ug or info@ura.go.ug. Do not email user@gmail.com."
        redacted = OutputGuard.redact_pii(raw)
        self.assertIn("services@ura.go.ug", redacted)
        self.assertIn("info@ura.go.ug", redacted)
        self.assertNotIn("user@gmail.com", redacted)
        self.assertIn("[REDACTED_EMAIL]", redacted)

    def test_sanitize_restores_legacy_redacted_ura_email(self) -> None:
        raw = "Email:[REDACTED_EMAIL]; [REDACTED_EMAIL] | https://ura.go.ug"
        sanitized = OutputGuard.sanitize(raw)
        self.assertIn("services@ura.go.ug", sanitized)
        self.assertNotIn("[REDACTED_EMAIL]", sanitized)


if __name__ == "__main__":
    unittest.main()


class GroundingWarningLocaleTests(unittest.TestCase):
    """The low-faithfulness disclaimer is appended to the user's own reply.

    It was English-only, so a Luganda or Kiswahili answer that tripped the
    grounding threshold got an English paragraph stapled to the end of it.
    """

    # Deliberately unrelated to the answer so faithfulness is low and the
    # disclaimer actually fires.
    CONTEXTS = ["The VAT registration threshold in Uganda is 150 million shillings."]
    ANSWER = "Omusolo gwa EFRIS gusasulwa buli mwezi."

    def _warned(self, locale: str) -> str:
        result = OutputGuard.check_grounding(
            self.ANSWER, self.CONTEXTS, threshold=0.99, locale=locale
        )
        self.assertIn("low_faithfulness", result.flags)
        return result.sanitized_text

    def test_luganda_reply_gets_a_luganda_disclaimer(self) -> None:
        text = self._warned("lg")
        self.assertIn("Okulabula", text)
        self.assertNotIn("may not be fully supported", text)

    def test_kiswahili_reply_gets_a_kiswahili_disclaimer(self) -> None:
        text = self._warned("sw")
        self.assertIn("Tahadhari", text)
        self.assertNotIn("may not be fully supported", text)

    def test_english_and_unknown_locales_keep_the_english_disclaimer(self) -> None:
        for locale in ("en", "fr", ""):
            with self.subTest(locale=locale):
                self.assertIn("may not be fully supported", self._warned(locale))

    def test_every_disclaimer_points_at_the_official_source(self) -> None:
        for locale in OutputGuard._GROUNDING_WARNINGS:
            with self.subTest(locale=locale):
                self.assertIn("https://ura.go.ug", self._warned(locale))

    def test_well_grounded_answer_gets_no_disclaimer_in_any_locale(self) -> None:
        grounded = self.CONTEXTS[0]
        for locale in ("en", "lg", "sw"):
            with self.subTest(locale=locale):
                result = OutputGuard.check_grounding(
                    grounded, self.CONTEXTS, threshold=0.3, locale=locale
                )
                self.assertEqual(result.sanitized_text, grounded)
                self.assertEqual(result.flags, [])
