"""Calculator router: amount extraction, intent planning, and the two
service fast paths (instant compute vs guided missing-info elicitation)."""

from __future__ import annotations

import unittest
import uuid

from app.calculator_router import extract_amounts, parse_ugx_amount, plan_calculation
from app.workflows.slots import validate_slot


class AmountExtractionTests(unittest.TestCase):
    def test_suffix_multipliers(self) -> None:
        self.assertEqual(parse_ugx_amount("1.5m"), 1_500_000.0)
        self.assertEqual(parse_ugx_amount("500k"), 500_000.0)
        self.assertEqual(parse_ugx_amount("2 million"), 2_000_000.0)

    def test_currency_and_separators(self) -> None:
        self.assertEqual(parse_ugx_amount("UGX 1,000,000"), 1_000_000.0)
        self.assertEqual(parse_ugx_amount("shs 250,000"), 250_000.0)
        self.assertEqual(parse_ugx_amount("1 200 000"), 1_200_000.0)

    def test_percentages_years_and_phones_ignored(self) -> None:
        self.assertEqual(extract_amounts("the rate is 18% this year"), [])
        self.assertEqual(extract_amounts("since 2024 the rules changed"), [])
        self.assertEqual(extract_amounts("call 0772140000 for help"), [])

    def test_multiple_amounts_refuse_single_parse(self) -> None:
        self.assertIsNone(parse_ugx_amount("between 1m and 2m"))
        self.assertIsNone(parse_ugx_amount("no numbers here"))


class NumberSlotValidatorTests(unittest.TestCase):
    def test_accepts_shorthand_amounts(self) -> None:
        ok, value, _ = validate_slot("1.5m", "number")
        self.assertTrue(ok)
        self.assertEqual(value, 1_500_000.0)

    def test_rejects_non_numbers_and_zero(self) -> None:
        ok, _, error = validate_slot("a lot", "number")
        self.assertFalse(ok)
        self.assertIn("amount", error)

    def test_min_zero_variant_accepts_zero(self) -> None:
        ok, value, _ = validate_slot("0", "number[min=0]")
        self.assertFalse(ok)  # "0" alone is below the bare-number floor of 1000
        ok, value, _ = validate_slot("ugx 0", "number[min=0]")
        self.assertTrue(ok)
        self.assertEqual(value, 0.0)


class PlanCalculationTests(unittest.TestCase):
    def test_vat_with_amount_computes_directly(self) -> None:
        plan = plan_calculation("Calculate VAT on 1.5m")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.tool, "calculate_vat")
        self.assertEqual(plan.missing, [])
        self.assertEqual(plan.params["amount"], 1_500_000.0)
        self.assertEqual(plan.params["direction"], "add")

    def test_vat_inclusive_extracts(self) -> None:
        plan = plan_calculation("how much VAT is included in 2,360,000")
        self.assertEqual(plan.params["direction"], "extract")

    def test_paye_without_salary_asks_for_it(self) -> None:
        plan = plan_calculation("how much PAYE will I pay?")
        self.assertEqual(plan.tool, "calculate_paye")
        self.assertEqual(plan.missing, ["monthly_gross"])
        self.assertEqual(plan.params["residency"], "resident")
        self.assertTrue(plan.assumptions)

    def test_paye_annual_salary_converted_monthly(self) -> None:
        plan = plan_calculation("calculate PAYE on my annual salary of 12m")
        self.assertEqual(plan.missing, [])
        self.assertEqual(plan.params["monthly_gross"], 1_000_000.0)
        self.assertTrue(any("annual" in a for a in plan.assumptions))

    def test_informational_questions_are_not_hijacked(self) -> None:
        self.assertIsNone(plan_calculation("how is PAYE calculated?"))
        self.assertIsNone(plan_calculation("what is the VAT rate in Uganda?"))

    def test_rental_monthly_rent_annualised(self) -> None:
        plan = plan_calculation("calculate rental tax on 2m per month")
        self.assertEqual(plan.tool, "calculate_rental_tax")
        self.assertEqual(plan.params["annual_gross_rent"], 24_000_000.0)
        self.assertEqual(plan.params["landlord_type"], "individual")

    def test_withholding_consultancy_maps_to_services(self) -> None:
        plan = plan_calculation("how much withholding tax on a 3m consultancy invoice")
        self.assertEqual(plan.tool, "calculate_withholding")
        self.assertEqual(plan.missing, [])
        self.assertEqual(plan.params["payment_type"], "services")
        self.assertEqual(plan.params["amount"], 3_000_000.0)

    def test_capital_gains_keyword_mapping(self) -> None:
        plan = plan_calculation("calculate capital gains: bought at 20m, sold for 50m")
        self.assertEqual(plan.missing, [])
        self.assertEqual(plan.params["sale_price"], 50_000_000.0)
        self.assertEqual(plan.params["cost_base"], 20_000_000.0)

    def test_customs_cif_direct(self) -> None:
        plan = plan_calculation("how much customs duty on a car with CIF 30m")
        self.assertEqual(plan.tool, "calculate_customs_duty")
        self.assertEqual(plan.params["cif_value"], 30_000_000.0)
        self.assertTrue(plan.params["include_vat"])


class WorkflowArgInterpolationTests(unittest.TestCase):
    def test_unfilled_placeholders_are_dropped_not_passed_through(self) -> None:
        from app.workflows.registry import _interpolate_args

        args = _interpolate_args(
            {"monthly_gross": "{monthly_gross}", "residency": "{residency}"},
            {"monthly_gross": 1_500_000},
        )
        # A literal "{residency}" used to reach the calculator and be read
        # as "not resident", silently returning non-resident bands.
        self.assertEqual(args, {"monthly_gross": 1_500_000})

    def test_zero_is_a_value_not_an_empty_slot(self) -> None:
        from app.workflows.registry import _interpolate_args

        args = _interpolate_args(
            {"allowable_expenses": "{allowable_expenses}"}, {"allowable_expenses": 0}
        )
        self.assertEqual(args, {"allowable_expenses": 0})


class ServiceCalculatorPathTests(unittest.TestCase):
    """End-to-end through ChatModel: instant compute + guided elicitation."""

    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service
        from app.flags import flags

        flags.set("workflows", True)
        cls._flags = flags
        cls.model = service.ChatModel()

    @classmethod
    def tearDownClass(cls):
        cls._flags.clear("workflows")

    def test_direct_compute_short_circuits_streaming(self) -> None:
        out = self.model.generate_retrieval_only(
            message="Calculate VAT on 1,000,000",
            conversation_id=str(uuid.uuid4()),
        )
        self.assertTrue(out.get("_short_circuit"))
        self.assertEqual(out["retrieval_mode"], "calculator")
        self.assertEqual(out["response_judge"]["decision"], "approve")
        self.assertEqual(out["response_judge"]["confidence_band"], "high")
        self.assertIn("180,000", out["reply"])
        self.assertIn("1,180,000", out["reply"])

    def test_missing_salary_elicits_then_computes(self) -> None:
        thread = str(uuid.uuid4())
        first = self.model.generate_retrieval_only(
            message="How much PAYE will I pay?",
            conversation_id=thread,
        )
        self.assertEqual(first["retrieval_mode"], "workflow")
        self.assertIn("gross monthly salary", first["reply"].lower())
        self.assertEqual(first["workflow"]["pending_slot"], "monthly_gross")

        second = self.model.generate_retrieval_only(
            message="1.5m",
            conversation_id=thread,
        )
        self.assertEqual(second["retrieval_mode"], "workflow")
        # The guided flow uses the fiscal year in force today, so derive
        # the expected figure instead of pinning a year's number that
        # goes stale the moment a new rate table lands.
        from app.tools import ToolRegistry

        expected = ToolRegistry.call("calculate_paye", {"monthly_gross": 1_500_000})
        self.assertIn(f"{expected['paye']:,.0f}", second["reply"])

    def test_guided_flow_carries_the_provisional_caveat(self) -> None:
        from app.tax.tables import get_table

        thread = str(uuid.uuid4())
        self.model.generate_retrieval_only(
            message="How much PAYE will I pay?", conversation_id=thread
        )
        reply = self.model.generate_retrieval_only(message="1.5m", conversation_id=thread)["reply"]
        if get_table().confirmed:
            self.assertNotIn("provisional", reply.lower())
        else:
            self.assertIn("provisional", reply.lower())

    def test_invalid_slot_answer_reprompts(self) -> None:
        thread = str(uuid.uuid4())
        self.model.generate_retrieval_only(
            message="calculate my take-home pay", conversation_id=thread
        )
        retry = self.model.generate_retrieval_only(
            message="quite a lot", conversation_id=thread
        )
        self.assertEqual(retry["retrieval_mode"], "workflow")
        self.assertIn("one amount", retry["reply"].lower())


class ReplyReadsTheTableTests(unittest.TestCase):
    """No tax figure or Act name may be hardcoded in the reply layer.

    A rate written into a format string keeps printing after the data
    file moves on, which is exactly the drift the versioned tables were
    introduced to remove — so the reply text is asserted against the
    table rather than against a literal.
    """

    def test_non_resident_line_tracks_the_table(self) -> None:
        from app.calculator_router import RatePlan, format_rate_reply
        from app.tax.tables import get_table

        table = get_table("FY2026-27")
        reply, _actions = format_rate_reply(RatePlan(summary="paye"), table)
        first_rate = table["paye_bands_non_resident"][0][2]
        self.assertIn(f"Non-resident employees are taxed from {first_rate * 100:.0f}%", reply)

    def test_resident_bands_are_listed_at_the_tables_rates(self) -> None:
        from app.calculator_router import RatePlan, format_rate_reply
        from app.tax.tables import get_table

        table = get_table("FY2026-27")
        reply, _actions = format_rate_reply(RatePlan(summary="paye"), table)
        for lower, _upper, rate in table["paye_bands_resident"]:
            with self.subTest(band=lower):
                self.assertIn(f"**{rate * 100:.0f}%**", reply)

    def test_provisional_caveat_names_no_fiscal_year_specific_act(self) -> None:
        from app.calculator_router import PROVISIONAL_CAVEAT

        self.assertNotIn("2026", PROVISIONAL_CAVEAT)
        self.assertIn("{fy}", PROVISIONAL_CAVEAT)

    def test_paye_reply_states_what_was_not_deducted(self) -> None:
        from app.calculator_router import format_calc_reply
        from app.tools import ToolRegistry

        result = ToolRegistry.call(
            "calculate_paye", {"monthly_gross": 1_000_000, "fiscal_year": "FY2026-27"}
        )
        reply = format_calc_reply("calculate_paye", result, [])
        self.assertIn("net of PAYE only", reply)
        self.assertNotIn("NSSF employee contribution:", reply)

    def test_paye_reply_shows_nssf_when_it_was_deducted(self) -> None:
        from app.calculator_router import format_calc_reply
        from app.tools import ToolRegistry

        result = ToolRegistry.call(
            "calculate_paye",
            {"monthly_gross": 1_000_000, "fiscal_year": "FY2026-27", "include_nssf": True},
        )
        reply = format_calc_reply("calculate_paye", result, [])
        self.assertIn("NSSF employee contribution: UGX 50,000", reply)


if __name__ == "__main__":
    unittest.main()
