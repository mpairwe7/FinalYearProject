"""URA administers taxes in Uganda; a question about elsewhere must be declined.

Measured against a live Sunflower-14B-FP8 + hybrid-Qdrant stack on 2026-09-02
(``docs/GAPS_AND_AGENTIC_ROADMAP.md`` §2.11, G41):

    "What is the corporate income tax rate in Kenya for 2026?"
        -> "**The corporation tax rate in Uganda is 30%** (FY2026-27) …
            That comes from the official URA FY2026-27 rate table."

Reproduced for Rwanda. The reply is labelled Uganda, so it is not a false
statement — which is exactly what makes it dangerous. It answers a different
question in the authoritative register, with a citation, on the deterministic
path taxpayers have most reason to trust.
"""

from __future__ import annotations

import unittest

from app.text_signals import detect_foreign_jurisdiction, out_of_jurisdiction_reply


class ForeignJurisdictionDetectionTests(unittest.TestCase):
    def test_a_named_foreign_country_is_detected(self) -> None:
        for message, expected in (
            ("What is the corporate income tax rate in Kenya for 2026?", "Kenya"),
            ("What is the corporation tax rate in Rwanda?", "Rwanda"),
            ("How much is VAT in Tanzania?", "Tanzania"),
            ("What is the UK corporation tax rate?", "the United Kingdom"),
        ):
            with self.subTest(message=message):
                self.assertEqual(detect_foreign_jurisdiction(message), expected)

    def test_a_demonym_names_a_jurisdiction_too(self) -> None:
        """"the Kenyan VAT rate" is as much a Kenya question as "VAT in Kenya"."""
        self.assertEqual(detect_foreign_jurisdiction("What is the Kenyan VAT rate?"), "Kenya")
        self.assertEqual(
            detect_foreign_jurisdiction("Do Rwandan companies pay withholding tax here?"),
            "Rwanda",
        )

    def test_ugandan_questions_are_untouched(self) -> None:
        for message in (
            "What is the standard VAT rate in Uganda?",
            "What is the VAT rate?",
            "How do I register for a TIN?",
            "Calculate PAYE for a monthly salary of 3,500,000 UGX.",
            "What are the URA penalties for late filing?",
        ):
            with self.subTest(message=message):
                self.assertEqual(detect_foreign_jurisdiction(message), "")

    def test_a_comparison_naming_uganda_too_is_still_answerable(self) -> None:
        """Refusing outright would withhold the half URA can actually speak to.

        The guard stays silent here on purpose so the Uganda figure is still
        given; it is the naming of Uganda, not the absence of Kenya, that makes
        this answerable.
        """
        for message in (
            "How does Uganda's VAT compare with Kenya's?",
            "Is Uganda's corporation tax higher than Rwanda's?",
        ):
            with self.subTest(message=message):
                self.assertEqual(detect_foreign_jurisdiction(message), "")

    def test_the_reply_names_the_country_and_refuses_a_ugandan_figure(self) -> None:
        reply = out_of_jurisdiction_reply("Kenya")
        self.assertIn("Kenya", reply)
        self.assertIn("Uganda Revenue Authority", reply)
        # The whole point is that no Ugandan rate is quoted as the answer.
        for figure in ("30%", "18%", "12%", "6%"):
            self.assertNotIn(figure, reply)


class DispatcherPrecedenceTests(unittest.TestCase):
    """The guard has to run before the paths that would answer regardless.

    Both the calculator and the rate-table path match on the tax word alone, so
    ordering is the whole fix: placed after them, the Kenya question is already
    answered by the time the guard is consulted.
    """

    def test_the_guard_is_the_first_fast_path(self) -> None:
        import inspect

        from app.service import ChatModel

        source = inspect.getsource(ChatModel._maybe_handle_fast_paths)
        order = [
            source.index("_maybe_decline_out_of_jurisdiction"),
            source.index("_maybe_handle_tin_clarification"),
            source.index("_maybe_handle_calculator"),
            source.index("_maybe_handle_rate_lookup"),
        ]
        self.assertEqual(order, sorted(order), "jurisdiction guard must be consulted first")


if __name__ == "__main__":
    unittest.main()
