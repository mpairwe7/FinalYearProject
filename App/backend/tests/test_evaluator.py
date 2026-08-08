"""Evaluator-optimizer: deterministic verification of money answers.

The behaviour worth pinning is the distinction the previous
single-pass reflection could not make — between an answer that was
*checked and passed*, one that was *checked and failed*, and one that
*could not be checked at all*. Collapsing the third into the first is
how a wrong figure reaches a taxpayer looking verified.
"""

from __future__ import annotations

import unittest

from app.agents.evaluator import (
    MIN_VERIFIABLE_AMOUNT,
    RevisionBudget,
    Verdict,
    evaluate,
    verify_money,
    verify_rate_currency,
)


class _Recorder:
    """Stands in for the MCP client, and records what it was asked."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool: str, args: dict) -> object:
        self.calls.append((tool, args))
        return self.result


class VerdictTests(unittest.TestCase):
    def test_all_checks_passing_is_accepted(self) -> None:
        self.assertTrue(Verdict().accepted)

    def test_any_failure_rejects(self) -> None:
        for field in (
            "grounded",
            "numerically_consistent",
            "cites_effective_year",
            "tone_appropriate",
            "actionable",
        ):
            self.assertFalse(Verdict(**{field: False}).accepted, field)

    def test_failures_are_reported_numeric_first(self) -> None:
        verdict = Verdict(numerically_consistent=False, tone_appropriate=False)
        self.assertEqual(verdict.failures()[0], "numerically_consistent")


class RevisionBudgetTests(unittest.TestCase):
    def test_money_answer_may_be_revised(self) -> None:
        allowed, _why = RevisionBudget().may_revise(
            carries_money=True, escalation_bound=False
        )
        self.assertTrue(allowed)

    def test_escalation_bound_answer_may_be_revised(self) -> None:
        allowed, _why = RevisionBudget().may_revise(
            carries_money=False, escalation_bound=True
        )
        self.assertTrue(allowed)

    def test_ordinary_answer_is_not_worth_a_second_generation(self) -> None:
        allowed, why = RevisionBudget().may_revise(
            carries_money=False, escalation_bound=False
        )
        self.assertFalse(allowed)
        self.assertIn("money", why)

    def test_budget_is_spent_after_one_revision(self) -> None:
        budget = RevisionBudget()
        budget.spend()
        allowed, why = budget.may_revise(carries_money=True, escalation_bound=True)
        self.assertFalse(allowed)
        self.assertIn("budget spent", why)

    def test_the_loop_cannot_run_away(self) -> None:
        """The property that makes this safe to enable at all."""
        budget = RevisionBudget()
        spent = 0
        for _ in range(50):
            allowed, _why = budget.may_revise(carries_money=True, escalation_bound=True)
            if not allowed:
                break
            budget.spend()
            spent += 1
        self.assertEqual(spent, 1)

    def test_ceiling_is_configurable(self) -> None:
        budget = RevisionBudget(max_revisions=3)
        for _ in range(3):
            allowed, _ = budget.may_revise(carries_money=True, escalation_bound=False)
            self.assertTrue(allowed)
            budget.spend()
        allowed, _ = budget.may_revise(carries_money=True, escalation_bound=False)
        self.assertFalse(allowed)


class VerifyMoneyTests(unittest.TestCase):
    QUERY = "How much VAT on UGX 500,000?"

    def test_matching_figure_passes(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.0, "total": 590000.0})
        verdict, detail = verify_money(
            self.QUERY, "The VAT is UGX 90,000 on that amount. [1]", tool
        )
        self.assertTrue(verdict)
        self.assertEqual(detail["matched_value"], 90000.0)

    def test_any_field_of_the_result_may_be_the_one_quoted(self) -> None:
        """An answer may quote the total rather than the tax component."""
        tool = _Recorder({"ok": True, "vat_amount": 90000.0, "total": 590000.0})
        verdict, detail = verify_money(self.QUERY, "That comes to UGX 590,000. [1]", tool)
        self.assertTrue(verdict)
        self.assertEqual(detail["matched_value"], 590000.0)

    def test_citation_markers_are_not_read_as_figures(self) -> None:
        """``[1]`` parses as the amount 1 unless it is stripped first.

        Left in, an answer stating no figure at all looks like one
        stating several, and the reviser is told the wrong thing: that
        its number disagreed rather than that it never gave one.
        """
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict, detail = verify_money(
            self.QUERY, "VAT applies at the standard rate. [1][2]", tool
        )
        self.assertFalse(verdict)
        self.assertIn("no figure", detail["reason"])

    def test_wrong_figure_is_rejected(self) -> None:
        """The failure this whole phase exists to catch."""
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict, detail = verify_money(self.QUERY, "The VAT is UGX 75,000. [1]", tool)
        self.assertFalse(verdict)
        self.assertIn("no stated figure matches", detail["reason"])

    def test_rounding_in_prose_is_accepted(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.40})
        verdict, _detail = verify_money(self.QUERY, "About UGX 90,000. [1]", tool)
        self.assertTrue(verdict)

    def test_a_one_percent_error_is_still_caught(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict, _detail = verify_money(self.QUERY, "UGX 95,000. [1]", tool)
        self.assertFalse(verdict)

    def test_answer_with_no_figure_is_rejected(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict, detail = verify_money(self.QUERY, "VAT applies at the standard rate. [1]", tool)
        self.assertFalse(verdict)
        self.assertIn("no figure", detail["reason"])

    def test_non_calculation_question_is_unverified_not_passed(self) -> None:
        """The distinction that matters: None is not True."""
        tool = _Recorder({"ok": True})
        verdict, detail = verify_money(
            "What is a TIN?", "A TIN is a taxpayer identification number. [1]", tool
        )
        self.assertIsNone(verdict)
        self.assertIn("skipped", detail)
        self.assertEqual(tool.calls, [])

    def test_incomplete_question_does_not_recompute(self) -> None:
        tool = _Recorder({"ok": True})
        verdict, _detail = verify_money("How much PAYE do I pay?", "It depends. [1]", tool)
        self.assertIsNone(verdict)
        self.assertEqual(tool.calls, [])

    def test_calculator_failure_is_unverified_not_a_rejection(self) -> None:
        """A broken verifier must not reject an answer that may be fine."""
        tool = _Recorder({"ok": False, "error": "boom"})
        verdict, detail = verify_money(self.QUERY, "UGX 90,000. [1]", tool)
        self.assertIsNone(verdict)
        self.assertIn("skipped", detail)

    def test_an_exception_never_breaks_the_turn(self) -> None:
        def explode(_tool: str, _args: dict) -> object:
            raise RuntimeError("transport down")

        verdict, detail = verify_money(self.QUERY, "UGX 90,000. [1]", explode)
        self.assertIsNone(verdict)
        self.assertIn("skipped", detail)

    def test_small_numbers_are_not_used_as_evidence(self) -> None:
        """Single digits collide with years and section numbers."""
        tool = _Recorder({"ok": True, "bands_used": 3, "rate": 18})
        verdict, detail = verify_money(self.QUERY, "Under section 3 of the Act. [1]", tool)
        self.assertIsNone(verdict)
        self.assertIn("no verifiable figure", detail["skipped"])

    def test_the_recomputation_uses_the_planned_tool(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verify_money(self.QUERY, "UGX 90,000. [1]", tool)
        self.assertEqual(len(tool.calls), 1)
        self.assertIn("vat", tool.calls[0][0])

    def test_min_amount_threshold_is_meaningful(self) -> None:
        self.assertGreaterEqual(MIN_VERIFIABLE_AMOUNT, 1000)


class RateCurrencyTests(unittest.TestCase):
    def test_rate_with_its_fiscal_year_passes(self) -> None:
        self.assertTrue(
            verify_rate_currency("VAT is 18% for FY2026-27. [1]", "FY2026-27")
        )

    def test_bare_year_satisfies_the_check(self) -> None:
        self.assertTrue(verify_rate_currency("VAT is 18% in 2026. [1]", "FY2026-27"))

    def test_rate_without_a_year_is_rejected(self) -> None:
        """A percentage a taxpayer cannot date is one they cannot check."""
        self.assertFalse(verify_rate_currency("VAT is charged at 18%. [1]", "FY2026-27"))

    def test_answer_quoting_no_rate_is_unverified(self) -> None:
        self.assertIsNone(verify_rate_currency("Register at ura.go.ug. [1]", "FY2026-27"))

    def test_no_effective_year_configured_does_not_reject(self) -> None:
        self.assertTrue(verify_rate_currency("VAT is 18%. [1]", ""))


class EvaluateTests(unittest.TestCase):
    QUERY = "How much VAT on UGX 500,000?"

    def test_a_correct_answer_is_accepted(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict = evaluate(
            self.QUERY,
            "The VAT is 18%, so UGX 90,000, for FY2026-27. [1]",
            call_tool=tool,
            effective_year="FY2026-27",
            faithfulness=0.9,
        )
        self.assertTrue(verdict.accepted, verdict.failures())
        self.assertEqual(verdict.unverified, ())

    def test_an_answer_quoting_no_rate_leaves_that_check_unverified(self) -> None:
        """Passing and not-applicable must stay distinguishable."""
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict = evaluate(
            self.QUERY,
            "The VAT is UGX 90,000. [1]",
            call_tool=tool,
            effective_year="FY2026-27",
            faithfulness=0.9,
        )
        self.assertTrue(verdict.accepted)
        self.assertIn("cites_effective_year", verdict.unverified)

    def test_a_wrong_figure_is_rejected_with_the_right_number(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict = evaluate(
            self.QUERY,
            "The VAT is UGX 75,000 for FY2026-27. [1]",
            call_tool=tool,
            effective_year="FY2026-27",
            faithfulness=0.9,
        )
        self.assertFalse(verdict.accepted)
        self.assertIn("numerically_consistent", verdict.failures())
        self.assertIn("90,000", verdict.revision_note)

    def test_the_revision_note_says_what_to_do(self) -> None:
        """A critique the reviser must interpret is a second chance to err."""
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict = evaluate(
            self.QUERY, "It is UGX 75,000. [1]", call_tool=tool, faithfulness=0.9
        )
        self.assertIn("use that number", verdict.revision_note)

    def test_low_faithfulness_rejects(self) -> None:
        verdict = evaluate(self.QUERY, "Something. [1]", faithfulness=0.2)
        self.assertFalse(verdict.accepted)
        self.assertIn("grounded", verdict.failures())

    def test_unrunnable_checks_are_reported_not_silently_passed(self) -> None:
        verdict = evaluate("What is a TIN?", "A TIN identifies a taxpayer. [1]")
        self.assertTrue(verdict.accepted)
        self.assertIn("numerically_consistent", verdict.unverified)
        self.assertIn("grounded", verdict.unverified)

    def test_undated_rate_rejects_and_says_why(self) -> None:
        verdict = evaluate(
            "What is the VAT rate?",
            "VAT is charged at 18%. [1]",
            effective_year="FY2026-27",
            faithfulness=0.9,
        )
        self.assertFalse(verdict.accepted)
        self.assertIn("1 July", verdict.revision_note)

    def test_model_judged_axes_default_to_passing(self) -> None:
        """An unavailable judge must not block a sound answer."""
        verdict = evaluate(self.QUERY, "UGX 90,000. [1]")
        self.assertTrue(verdict.tone_appropriate)
        self.assertTrue(verdict.actionable)

    def test_accepted_answer_carries_no_revision_note(self) -> None:
        tool = _Recorder({"ok": True, "vat_amount": 90000.0})
        verdict = evaluate(self.QUERY, "UGX 90,000. [1]", call_tool=tool, faithfulness=0.9)
        self.assertEqual(verdict.revision_note, "")


if __name__ == "__main__":
    unittest.main()


class ServiceWiringTests(unittest.TestCase):
    """The evaluator has to be reachable from the live path, not just importable."""

    def test_recompute_goes_through_the_mcp_client(self) -> None:
        """Verification must exercise the path that produced the answer.

        Calling the calculator directly would check a different code
        path than the agent's own call — different routing, different
        validation — and could pass an answer the real path would fail.
        """
        from app.service import ChatModel

        result = ChatModel._recompute_for_verification(
            "calculate_vat", {"amount": 500000, "direction": "add"}
        )
        self.assertIsInstance(result, dict)
        self.assertTrue(result)

    def test_recompute_result_verifies_a_correct_answer(self) -> None:
        from app.service import ChatModel

        verdict, detail = verify_money(
            "How much VAT on UGX 500,000?",
            "The VAT is UGX 90,000. [1]",
            ChatModel._recompute_for_verification,
        )
        self.assertTrue(verdict, detail)

    def test_recompute_result_catches_a_wrong_answer(self) -> None:
        from app.service import ChatModel

        verdict, _detail = verify_money(
            "How much VAT on UGX 500,000?",
            "The VAT is UGX 75,000. [1]",
            ChatModel._recompute_for_verification,
        )
        self.assertFalse(verdict)

    def test_confirmed_mismatch_escalates(self) -> None:
        from app.service import ChatModel

        escalate, reason = ChatModel._escalate_on_numeric_mismatch(
            {"accepted": False, "failures": ["numerically_consistent"], "unverified": []},
            False,
            "",
        )
        self.assertTrue(escalate)
        self.assertIn("calculator", reason)

    def test_unverified_does_not_escalate(self) -> None:
        """Treating "could not check" as "wrong" would flood the queue."""
        from app.service import ChatModel

        escalate, reason = ChatModel._escalate_on_numeric_mismatch(
            {"accepted": True, "failures": [], "unverified": ["numerically_consistent"]},
            False,
            "",
        )
        self.assertFalse(escalate)
        self.assertEqual(reason, "")

    def test_absent_verification_leaves_the_decision_alone(self) -> None:
        from app.service import ChatModel

        self.assertEqual(
            ChatModel._escalate_on_numeric_mismatch(None, True, "prior reason"),
            (True, "prior reason"),
        )

    def test_other_failures_do_not_escalate_on_this_rule(self) -> None:
        from app.service import ChatModel

        escalate, _reason = ChatModel._escalate_on_numeric_mismatch(
            {"accepted": False, "failures": ["cites_effective_year"], "unverified": []},
            False,
            "",
        )
        self.assertFalse(escalate)
