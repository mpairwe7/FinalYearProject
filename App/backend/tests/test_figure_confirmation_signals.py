"""A figure the rate table has not confirmed is never presented as official (G620).

The FY2026-27 table is confirmed, but marks some entries ``unverified`` (the
used-clothing environmental levy and royalty withholding among them). The
calculator's tool payload carried a ``verification_warning`` for them, but the
reply's footer read only "Figures use the official URA FY2026-27 rate table."
and the turn's judge said ``confidence_band: high``, so the chat showed a green
"Official Statutory Calculator" tick over a 30% levy the table itself does not
stand behind. A rate answer did carry the caveat in its text, under the same tick.

Now the caveat is in the reply on both paths, and the judge's band is
``medium`` with a reason, which the chat reads to choose its badge.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.calculator_router import format_calc_reply, reply_has_unconfirmed_figures
from app.guardrails import OutputGuard
from app.service import ChatModel
from app.tools import ToolRegistry


def _used_clothing_duty() -> dict[str, object]:
    result = ToolRegistry.call(
        "calculate_customs_duty",
        {"cif_value": 10_000_000, "goods_category": "used_clothing", "fiscal_year": "FY2026-27"},
    )
    assert result.get("ok"), result
    return result


class CalculatorReplyTests(unittest.TestCase):
    def test_an_unverified_rate_is_named_in_the_reply(self) -> None:
        reply = format_calc_reply("calculate_customs_duty", _used_clothing_duty(), [])
        self.assertIn("not been confirmed against a gazetted schedule", reply)
        self.assertIn("used clothing", reply.lower())
        self.assertTrue(reply_has_unconfirmed_figures(reply))

    def test_a_confirmed_calculation_carries_no_caveat(self) -> None:
        result = ToolRegistry.call("calculate_paye", {"monthly_gross": 1_000_000, "fiscal_year": "FY2026-27"})
        reply = format_calc_reply("calculate_paye", result, [])
        self.assertNotIn("not been confirmed", reply)
        self.assertFalse(reply_has_unconfirmed_figures(reply))


class TurnJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = ChatModel.__new__(ChatModel)
        self.model._output_guard = OutputGuard()
        self.model.name = "test"

    def test_a_calculator_turn_with_an_unverified_rate_is_not_high_confidence(self) -> None:
        reply = format_calc_reply("calculate_customs_duty", _used_clothing_duty(), [])
        turn = self.model._calculator_turn(reply, thread_id="g620-calc", locale="en", tool="calculate_customs_duty")
        self.assertEqual(turn["retrieval_mode"], "calculator")
        self.assertEqual(turn["response_judge"]["confidence_band"], "medium")
        self.assertIn("figure not confirmed against a gazetted schedule", turn["response_judge"]["reasons"])

    def test_a_confirmed_calculator_turn_stays_high(self) -> None:
        result = ToolRegistry.call("calculate_paye", {"monthly_gross": 1_000_000, "fiscal_year": "FY2026-27"})
        turn = self.model._calculator_turn(
            format_calc_reply("calculate_paye", result, []), thread_id="g620-paye", locale="en", tool="calculate_paye"
        )
        self.assertEqual(turn["response_judge"]["confidence_band"], "high")

    @patch("app.tools.rates._authority_payload", return_value=(True, {}))
    def test_a_rate_answer_with_an_unverified_figure_is_not_high_confidence(self, _authority: object) -> None:
        result = self.model._maybe_handle_rate_lookup(
            message="What is the withholding tax rate on royalties?",
            rewritten="",
            thread_id="g620-rate",
            locale="en",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["retrieval_mode"], "calculator")
        self.assertIn("not been confirmed", result["reply"])
        self.assertEqual(result["response_judge"]["confidence_band"], "medium")

    @patch("app.tools.rates._authority_payload", return_value=(True, {}))
    def test_a_confirmed_rate_answer_stays_high(self, _authority: object) -> None:
        result = self.model._maybe_handle_rate_lookup(
            message="What is the current VAT rate in Uganda?",
            rewritten="",
            thread_id="g620-vat",
            locale="en",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["response_judge"]["confidence_band"], "high")


if __name__ == "__main__":
    unittest.main()
