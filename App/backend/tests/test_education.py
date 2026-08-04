"""Taxpayer education tool — scaffolding, fading, and figure provenance.

Two properties carry the design. Worked examples must come from the
calculators, so a lesson cannot teach a rate the rest of the system has
stopped using. And the check answer must be genuinely withheld, because
a tool that always returns the answer cannot ask a question — which is
the whole mechanism against learners offloading the thinking.
"""

from __future__ import annotations

import unittest

from app.tools import ToolRegistry
from app.tools.education import _BY_KEY, LEVELS, explain, learning_path


class CurriculumIntegrityTests(unittest.TestCase):
    def test_every_concept_renders_at_every_level(self) -> None:
        for key in _BY_KEY:
            for level in LEVELS:
                result = explain(key, level=level)
                self.assertTrue(result["ok"], f"{key}/{level}: {result.get('error')}")
                self.assertTrue(result["explanation"])
                self.assertTrue(result["check_question"])

    def test_every_declared_example_actually_renders(self) -> None:
        # Guards the failure mode where a calculator renames a payload
        # field and a lesson silently loses its figures.
        for key, concept in _BY_KEY.items():
            if concept.example is None:
                continue
            example = explain(key, level="beginner").get("worked_example")
            self.assertIsNotNone(example, f"{key}: example declared but not rendered")
            assert example is not None
            self.assertEqual(len(example["steps"]), len(concept.example.steps))

    def test_no_step_renders_a_placeholder(self) -> None:
        for key, concept in _BY_KEY.items():
            if concept.example is None:
                continue
            for step in explain(key, level="beginner")["worked_example"]["steps"]:
                self.assertNotEqual(step["value"], "—", f"{key}: {step['prompt']} unresolved")
                self.assertTrue(step["value"], f"{key}: {step['prompt']} empty")

    def test_every_example_names_a_registered_tool(self) -> None:
        for key, concept in _BY_KEY.items():
            if concept.example is None:
                continue
            self.assertIsNotNone(
                ToolRegistry.get(concept.example.tool), f"{key}: {concept.example.tool}"
            )

    def test_prerequisites_all_exist(self) -> None:
        for key, concept in _BY_KEY.items():
            for name in concept.prerequisites + concept.next_concepts:
                self.assertIn(name, _BY_KEY, f"{key} points at unknown concept {name}")

    def test_the_curriculum_is_acyclic(self) -> None:
        # learning_path terminates on a cycle, so an ordering that
        # silently drops a prerequisite is the symptom to catch.
        for key, concept in _BY_KEY.items():
            path = learning_path(key)
            self.assertEqual(path[-1], key)
            for prerequisite in concept.prerequisites:
                self.assertIn(prerequisite, path)
                self.assertLess(path.index(prerequisite), path.index(key))


class FadingTests(unittest.TestCase):
    """Calibrated, fading scaffolding — not the same lesson three times."""

    def test_beginner_gets_every_step_worked(self) -> None:
        example = explain("vat", level="beginner")["worked_example"]
        self.assertFalse(example["final_step_withheld"])
        self.assertTrue(all(step["value"] for step in example["steps"]))

    def test_intermediate_withholds_the_final_step(self) -> None:
        example = explain("vat", level="intermediate")["worked_example"]
        self.assertTrue(example["final_step_withheld"])
        self.assertEqual(example["steps"][-1]["value"], "")
        self.assertTrue(example["steps"][-1]["to_complete"])
        # Only the last one — the learner needs the earlier steps to work from.
        self.assertTrue(all(step["value"] for step in example["steps"][:-1]))

    def test_the_withheld_value_is_absent_not_merely_flagged(self) -> None:
        # A value left in the payload is a value the model reads out.
        example = explain("vat", level="intermediate")["worked_example"]
        beginner = explain("vat", level="beginner")["worked_example"]
        answer = beginner["steps"][-1]["value"]
        self.assertNotIn(answer, str(example))

    def test_advanced_drops_the_worked_example_entirely(self) -> None:
        result = explain("vat", level="advanced")
        self.assertNotIn("worked_example", result)

    def test_advanced_asks_a_transfer_question(self) -> None:
        concept = _BY_KEY["vat"]
        self.assertEqual(explain("vat", level="advanced")["check_question"],
                         concept.transfer_question)
        self.assertNotEqual(explain("vat", level="beginner")["check_question"],
                            concept.transfer_question)

    def test_a_single_step_example_is_not_wholly_withheld(self) -> None:
        # Withholding the only step would leave nothing to reason from.
        for key, concept in _BY_KEY.items():
            if concept.example is None or len(concept.example.steps) != 1:
                continue
            example = explain(key, level="intermediate")["worked_example"]
            self.assertFalse(example["final_step_withheld"])


class RetrievalPracticeTests(unittest.TestCase):
    def test_the_answer_is_withheld_by_default(self) -> None:
        result = explain("progressive_taxation")
        self.assertNotIn("check_answer", result)
        self.assertTrue(result["answer_withheld"])
        self.assertIn("reveal_answer", result["instruction"])

    def test_the_answer_is_returned_when_asked_for(self) -> None:
        result = explain("progressive_taxation", reveal_answer=True)
        self.assertTrue(result["check_answer"])
        self.assertNotIn("answer_withheld", result)

    def test_the_withheld_answer_does_not_leak_into_the_payload(self) -> None:
        answer = explain("vat", reveal_answer=True)["check_answer"]
        self.assertNotIn(answer, str(explain("vat")))

    def test_advanced_has_no_stored_answer_to_reveal(self) -> None:
        result = explain("vat", level="advanced", reveal_answer=True)
        self.assertNotIn("check_answer", result)
        self.assertIn("reasoning", result["instruction"])


class FigureProvenanceTests(unittest.TestCase):
    """Numbers come from the rate tables, with their caveats attached."""

    def test_a_lesson_carries_the_fiscal_year_it_taught_from(self) -> None:
        self.assertTrue(explain("vat")["fiscal_year"])

    def test_a_lesson_carries_the_statutory_basis(self) -> None:
        basis = explain("vat")["rate_basis"]
        self.assertIn("fiscal_year", basis)
        self.assertIn("status", basis)

    def test_an_unconfirmed_table_warns_inside_the_lesson(self) -> None:
        from app.tax.tables import get_table

        result = explain("vat")
        if not get_table(result["fiscal_year"]).confirmed:
            self.assertTrue(result["verification_warning"])

    def test_the_figures_match_the_calculator_exactly(self) -> None:
        # The anti-drift property: no second copy of the arithmetic.
        calculated = ToolRegistry.get("calculate_vat").execute(amount=500_000)
        steps = explain("vat")["worked_example"]["steps"]
        self.assertIn(f"UGX {calculated['vat']:,.2f}", [s["value"] for s in steps])
        self.assertIn(f"UGX {calculated['gross']:,.2f}", [s["value"] for s in steps])

    def test_an_explicit_fiscal_year_is_honoured(self) -> None:
        result = explain("vat", fiscal_year="FY2025-26")
        self.assertEqual(result["fiscal_year"], "FY2025-26")

    def test_an_unknown_fiscal_year_drops_the_example_not_the_lesson(self) -> None:
        result = explain("vat", fiscal_year="FY1999-00")
        self.assertTrue(result["ok"])
        self.assertNotIn("worked_example", result)
        self.assertTrue(result["explanation"])

    def test_marginal_and_effective_rates_are_not_confused(self) -> None:
        # The concept exists to separate these; showing the effective
        # rate under a "marginal" label would teach the misconception.
        steps = explain("progressive_taxation")["worked_example"]["steps"]
        by_prompt = {s["prompt"]: s["value"] for s in steps}
        marginal = next(v for k, v in by_prompt.items() if "marginal" in k)
        effective = next(v for k, v in by_prompt.items() if "effective" in k)
        self.assertNotEqual(marginal, effective)
        self.assertGreater(float(marginal.rstrip("%")), float(effective.rstrip("%")))


class ToolContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = ToolRegistry.get("explain_tax_concept")
        assert self.tool is not None

    def test_the_tool_is_registered(self) -> None:
        self.assertIsNotNone(self.tool)
        self.assertEqual(self.tool.schema.namespace, "education")
        self.assertEqual(self.tool.schema.risk, "low")

    def test_the_topic_enum_matches_the_curriculum(self) -> None:
        enum = self.tool.schema.parameters["properties"]["topic"]["enum"]
        self.assertEqual(sorted(enum), sorted(_BY_KEY))

    def test_an_unknown_topic_lists_what_is_available(self) -> None:
        result = self.tool.execute(topic="crypto_tax")
        self.assertFalse(result["ok"])
        self.assertIn("vat", result["available_topics"])

    def test_a_missing_topic_is_rejected(self) -> None:
        self.assertFalse(self.tool.execute(topic="")["ok"])

    def test_an_unknown_level_is_rejected(self) -> None:
        result = self.tool.execute(topic="vat", level="expert")
        self.assertFalse(result["ok"])
        self.assertIn("beginner", result["error"])

    def test_topic_names_are_forgiving_of_spacing(self) -> None:
        for spelling in ("VAT", "vat_registration", "VAT Registration", "vat-registration"):
            self.assertTrue(self.tool.execute(topic=spelling)["ok"], spelling)

    def test_a_learning_path_is_offered_for_orientation(self) -> None:
        result = self.tool.execute(topic="capital_gains")
        self.assertEqual(result["learning_path"][-1], "capital_gains")
        self.assertIn("corporation_tax", result["learning_path"])


if __name__ == "__main__":
    unittest.main()
