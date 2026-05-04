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


if __name__ == "__main__":
    unittest.main()
