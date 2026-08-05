"""Agent-loop budgets, thrash suppression, and observation compaction.

The defects pinned here are the ones an iteration cap does not catch:
a single round can fan out unboundedly, the same call can be paid for
in every round, and a byte-sliced payload puts invalid JSON in the
model's context.
"""

from __future__ import annotations

import json
import unittest

from app.agents.loop_control import (
    DEFAULT_MAX_TOTAL_CALLS,
    MIN_OBSERVATION_CHARS,
    Admission,
    ToolCallBudget,
    call_fingerprint,
    compact_observation,
)


class FingerprintTests(unittest.TestCase):
    def test_argument_order_does_not_change_identity(self) -> None:
        a = call_fingerprint("calculate_vat", {"amount": 100, "direction": "add"})
        b = call_fingerprint("calculate_vat", {"direction": "add", "amount": 100})
        self.assertEqual(a, b)

    def test_different_arguments_are_different_calls(self) -> None:
        self.assertNotEqual(
            call_fingerprint("calculate_vat", {"amount": 100}),
            call_fingerprint("calculate_vat", {"amount": 200}),
        )

    def test_same_arguments_to_different_tools_differ(self) -> None:
        self.assertNotEqual(
            call_fingerprint("calculate_vat", {"amount": 100}),
            call_fingerprint("calculate_paye", {"amount": 100}),
        )

    def test_unserialisable_arguments_do_not_raise(self) -> None:
        self.assertTrue(call_fingerprint("t", {"obj": object()}))

    def test_none_and_empty_arguments_agree(self) -> None:
        self.assertEqual(call_fingerprint("t", None), call_fingerprint("t", {}))


class TurnBudgetTests(unittest.TestCase):
    def test_fan_out_within_one_round_is_capped(self) -> None:
        # max_iterations bounds rounds, not the calls emitted per round —
        # without this cap one generation can dispatch a tool storm.
        budget = ToolCallBudget(max_calls_per_iteration=2)
        self.assertIs(budget.admit("a", {"x": 1}, iteration=0).admission, Admission.ADMIT)
        self.assertIs(budget.admit("b", {"x": 2}, iteration=0).admission, Admission.ADMIT)
        denied = budget.admit("c", {"x": 3}, iteration=0)
        self.assertIs(denied.admission, Admission.DENIED)
        self.assertIn("fan-out", denied.reason)

    def test_a_new_round_gets_a_fresh_fan_out_allowance(self) -> None:
        budget = ToolCallBudget(max_calls_per_iteration=1)
        budget.admit("a", {"x": 1}, iteration=0)
        self.assertIs(budget.admit("b", {"x": 2}, iteration=1).admission, Admission.ADMIT)

    def test_turn_total_is_capped_across_rounds(self) -> None:
        budget = ToolCallBudget(max_total_calls=3, max_calls_per_iteration=99)
        for i in range(3):
            self.assertIs(
                budget.admit(f"tool_{i}", {"x": i}, iteration=i).admission, Admission.ADMIT
            )
        denied = budget.admit("tool_3", {"x": 3}, iteration=3)
        self.assertIs(denied.admission, Admission.DENIED)
        self.assertTrue(budget.exhausted())

    def test_one_tool_cannot_monopolise_the_turn(self) -> None:
        budget = ToolCallBudget(max_calls_per_tool=2, max_calls_per_iteration=99)
        budget.admit("lookup_rate", {"tax_type": "vat"}, iteration=0)
        budget.admit("lookup_rate", {"tax_type": "paye"}, iteration=0)
        denied = budget.admit("lookup_rate", {"tax_type": "cit"}, iteration=0)
        self.assertIs(denied.admission, Admission.DENIED)
        self.assertIn("lookup_rate", denied.reason)
        # A different tool is still admissible.
        self.assertIs(budget.admit("calculate_vat", {"amount": 1}, iteration=0).admission,
                      Admission.ADMIT)

    def test_a_denial_is_an_answer_the_model_can_read(self) -> None:
        budget = ToolCallBudget(max_total_calls=0)
        denied = budget.admit("anything", {}, iteration=0)
        self.assertIsNotNone(denied.result)
        assert denied.result is not None
        self.assertFalse(denied.result["ok"])
        self.assertTrue(denied.result["budget_exhausted"])

    def test_denied_calls_are_never_dispatched(self) -> None:
        budget = ToolCallBudget(max_total_calls=1)
        self.assertTrue(budget.admit("a", {}, iteration=0).should_dispatch)
        self.assertFalse(budget.admit("b", {}, iteration=0).should_dispatch)


class DuplicateCallTests(unittest.TestCase):
    def test_an_identical_repeat_is_served_from_the_memo(self) -> None:
        budget = ToolCallBudget()
        budget.admit("lookup_rate", {"tax_type": "vat"}, iteration=0)
        budget.record("lookup_rate", {"tax_type": "vat"}, {"ok": True, "rate": 0.18})

        repeat = budget.admit("lookup_rate", {"tax_type": "vat"}, iteration=1)
        self.assertIs(repeat.admission, Admission.REPEAT)
        self.assertFalse(repeat.should_dispatch)
        assert repeat.result is not None
        self.assertEqual(repeat.result["rate"], 0.18)
        self.assertTrue(repeat.result["repeated_call"])

    def test_a_repeat_does_not_consume_the_turn_budget(self) -> None:
        # Nothing executes, so charging for it would deny real work later.
        budget = ToolCallBudget(max_total_calls=2)
        budget.admit("a", {"x": 1}, iteration=0)
        budget.record("a", {"x": 1}, {"ok": True})
        for i in range(1, 4):
            budget.admit("a", {"x": 1}, iteration=i)
        self.assertEqual(budget.dispatched, 1)
        self.assertIs(budget.admit("b", {"y": 2}, iteration=4).admission, Admission.ADMIT)

    def test_the_memo_does_not_mask_a_genuinely_different_call(self) -> None:
        budget = ToolCallBudget()
        budget.admit("lookup_rate", {"tax_type": "vat"}, iteration=0)
        budget.record("lookup_rate", {"tax_type": "vat"}, {"ok": True, "rate": 0.18})
        fresh = budget.admit("lookup_rate", {"tax_type": "paye"}, iteration=0)
        self.assertIs(fresh.admission, Admission.ADMIT)

    def test_mutating_a_served_repeat_cannot_corrupt_the_memo(self) -> None:
        budget = ToolCallBudget()
        budget.record("t", {}, {"ok": True, "rate": 0.18})
        first = budget.admit("t", {}, iteration=0)
        assert first.result is not None
        first.result["rate"] = 999
        second = budget.admit("t", {}, iteration=1)
        assert second.result is not None
        self.assertEqual(second.result["rate"], 0.18)

    def test_non_dict_results_are_not_memoized(self) -> None:
        budget = ToolCallBudget()
        budget.admit("t", {}, iteration=0)
        budget.record("t", {}, "just a string")
        self.assertIs(budget.admit("t", {}, iteration=1).admission, Admission.ADMIT)

    def test_stats_expose_the_thrash_signal(self) -> None:
        budget = ToolCallBudget(max_total_calls=1)
        budget.admit("a", {}, iteration=0)
        budget.record("a", {}, {"ok": True})
        budget.admit("a", {}, iteration=1)
        budget.admit("b", {}, iteration=1)
        stats = budget.stats()
        self.assertEqual(stats["dispatched"], 1)
        self.assertEqual(stats["repeats"], 1)
        self.assertEqual(stats["denied"], 1)
        self.assertTrue(stats["exhausted"])


class ObservationCompactionTests(unittest.TestCase):
    def test_a_small_result_is_passed_through_unchanged(self) -> None:
        result = {"ok": True, "rate": 0.18}
        self.assertEqual(json.loads(compact_observation(result)), result)

    def test_an_oversized_result_stays_valid_json(self) -> None:
        # The defect: json.dumps(result)[:2000] cuts mid-token and hands
        # the model a payload it cannot parse.
        result = {"ok": True, "text": "x" * 50_000}
        compacted = compact_observation(result, budget_chars=500)
        self.assertLessEqual(len(compacted), 500)
        json.loads(compacted)  # must not raise

    def test_the_priority_keys_survive_compaction(self) -> None:
        result = {
            "ok": True,
            "amount": 18000,
            "explanation": "VAT at 18 percent",
            "debug_trace": "y" * 20_000,
            "raw_html": "z" * 20_000,
        }
        payload = json.loads(compact_observation(result, budget_chars=400))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["amount"], 18000)

    def test_dropped_keys_are_named_rather_than_silently_lost(self) -> None:
        result = {"ok": True, "amount": 1, "debug_trace": "y" * 20_000}
        payload = json.loads(compact_observation(result, budget_chars=300))
        self.assertIn("debug_trace", payload.get("_omitted", []) + list(payload))

    def test_a_non_dict_result_is_truncated_to_valid_json(self) -> None:
        compacted = compact_observation("q" * 10_000, budget_chars=300)
        self.assertLessEqual(len(compacted), 300)
        self.assertIsInstance(json.loads(compacted), str)

    def test_an_unserialisable_result_does_not_raise(self) -> None:
        json.loads(compact_observation({"ok": True, "obj": object()}))

    def test_the_budget_floor_is_respected(self) -> None:
        compacted = compact_observation({"ok": True, "t": "x" * 5000}, budget_chars=1)
        self.assertLessEqual(len(compacted), MIN_OBSERVATION_CHARS)
        json.loads(compacted)


class TurnObservationBudgetTests(unittest.TestCase):
    def test_accumulated_observations_shrink_as_the_turn_fills_up(self) -> None:
        budget = ToolCallBudget(
            observation_budget_chars=1000,
            turn_observation_budget_chars=1500,
        )
        big = {"ok": True, "text": "x" * 10_000}
        first = budget.compact(big)
        self.assertLessEqual(len(first), 1000)
        self.assertLess(budget.observation_allowance(), 1000)
        second = budget.compact(big)
        self.assertLess(len(second), len(first))
        json.loads(second)

    def test_the_allowance_tracks_what_the_turn_has_already_spent(self) -> None:
        budget = ToolCallBudget(
            observation_budget_chars=500,
            turn_observation_budget_chars=1200,
        )
        self.assertEqual(budget.observation_allowance(), 500)
        budget.spend_observation("x" * 900)
        self.assertEqual(budget.observation_allowance(), 300)

    def test_a_late_observation_is_short_not_absent(self) -> None:
        budget = ToolCallBudget(turn_observation_budget_chars=10)
        text = budget.compact({"ok": True, "text": "x" * 10_000})
        self.assertGreater(len(text), 0)
        self.assertLessEqual(len(text), MIN_OBSERVATION_CHARS)

    def test_defaults_leave_room_for_a_full_turn(self) -> None:
        budget = ToolCallBudget()
        self.assertGreaterEqual(
            budget.turn_observation_budget_chars,
            budget.observation_budget_chars * 2,
        )
        self.assertGreater(DEFAULT_MAX_TOTAL_CALLS, 0)


if __name__ == "__main__":
    unittest.main()
