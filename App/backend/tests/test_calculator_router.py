"""Calculator router: amount extraction, intent planning, and the two
service fast paths (instant compute vs guided missing-info elicitation)."""

from __future__ import annotations

import unittest
import uuid

from app.query import rewrite as rewrite_query
from app.calculator_router import (
    _INFO_ONLY_RE,
    _PAYE_THRESHOLD_ASK_RE,
    extract_amounts,
    parse_ugx_amount,
    plan_calculation,
    rate_lookup_calendar_years,
    plan_rate_lookup,
)
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

    def test_bare_definitional_questions_do_not_open_a_calculator(self) -> None:
        """"What is VAT?" wants an explanation, not a wizard asking for a figure.

        Widening the calculation-verb gate to catch "what will my PAYE be on 2m"
        also swallowed every "what is <tax>?" — the most common question the
        assistant gets — and answered it with a calculator prompt. The opener is
        only a calculation ask when a figure comes with it.
        """
        for question in (
            "What is VAT?",
            "What is PAYE?",
            "What is withholding tax?",
            "What is customs duty?",
            "What is capital gains tax?",
            "What is rental income tax?",
            "what is corporation tax",
            "What does VAT mean?",
        ):
            with self.subTest(question=question):
                self.assertIsNone(plan_calculation(question))

    def test_definitional_opener_with_a_figure_is_still_a_calculation(self) -> None:
        """The widening this guards still has to do the job it was added for."""
        plan = plan_calculation("what is VAT on 1.5m")
        self.assertEqual(plan.tool, "calculate_vat")
        self.assertEqual(plan.params["amount"], 1_500_000.0)

        plan = plan_calculation("what is the tax on a 5,000,000 salary")
        self.assertEqual(plan.tool, "calculate_paye")
        self.assertEqual(plan.params["monthly_gross"], 5_000_000.0)

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


class FigureLookupIsNotACalculationTests(unittest.TestCase):
    """A question about what a published figure IS must not open a calculator.

    Measured against a live Sunflower-14B-FP8 + hybrid-Qdrant stack on
    2026-09-02 (``docs/GAPS_AND_AGENTIC_ROADMAP.md`` §2.11, G42). Both of these
    produced a guided calculator flow asking the taxpayer for an amount instead
    of stating the figure they asked for:

        "How much monthly income is exempt from PAYE in Uganda?"
            -> calc_paye, "What is your gross monthly salary?"
        "What will Uganda's VAT rate be in 2031?"
            -> calc_vat, "What is the amount in UGX?"

    ``plan_calculation`` is the right gate for this — the guided flow is opened
    downstream of it by ``_maybe_handle_calculator`` when a plan reports missing
    params, so a plan that is never formed is a flow that never opens.
    """

    def test_the_guard_never_matches_the_empty_string(self) -> None:
        """An empty alternative here would suppress every calculation.

        ``_INFO_ONLY_RE`` is an alternation that gets extended over time, and a
        stray ``|`` produces a branch matching at position 0 of any string. The
        symptom is the guard appearing to work perfectly while the calculators
        go dark.
        """
        self.assertIsNone(_INFO_ONLY_RE.search(""))
        self.assertIsNone(_INFO_ONLY_RE.search("hello"))

    def test_threshold_lookups_do_not_produce_a_plan(self) -> None:
        for message in (
            "How much monthly income is exempt from PAYE in Uganda?",
            "How much is tax-free under PAYE?",
            "How much of my salary is taxable?",
        ):
            with self.subTest(message=message):
                self.assertIsNone(
                    plan_calculation(message),
                    "a threshold lookup has no amount to compute on",
                )

    def test_rate_lookups_in_any_tense_do_not_produce_a_plan(self) -> None:
        """"will" and "would" belong with "is/are/was/were" here."""
        for message in (
            "What will Uganda's VAT rate be in 2031?",
            "What would the PAYE rate be for a non-resident?",
            "What is the standard VAT rate?",
            "What are the PAYE bands for 2026?",
        ):
            with self.subTest(message=message):
                self.assertIsNone(plan_calculation(message))

    def test_real_computations_still_produce_a_plan(self) -> None:
        """The guard must not cost the calculators their actual traffic."""
        for message, tool in (
            ("Calculate PAYE for a monthly salary of 3,500,000 UGX.", "calculate_paye"),
            ("How much PAYE will I pay on a monthly salary of 3,500,000 UGX?", "calculate_paye"),
            ("Compute VAT on 500,000", "calculate_vat"),
            ("How much VAT on 2,000,000 UGX?", "calculate_vat"),
        ):
            with self.subTest(message=message):
                plan = plan_calculation(message)
                self.assertIsNotNone(plan, "this is a computation, not a lookup")
                self.assertEqual(plan.tool, tool)


class RateLookupCalendarYearTests(unittest.TestCase):
    def test_extracts_explicit_years_without_treating_them_as_amounts(self) -> None:
        self.assertEqual(rate_lookup_calendar_years("What will Uganda's VAT rate be in 2031?"), (2031,))
        self.assertEqual(
            rate_lookup_calendar_years("Compare FY2025-26 with FY2026-27 PAYE rates"),
            (2025, 2026, 2026, 2027),
        )
        self.assertEqual(rate_lookup_calendar_years("What is the VAT rate?"), ())


class SalaryThresholdIsARateLookupTests(unittest.TestCase):
    """Plain-language PAYE threshold questions must read the rate table.

    Measured against the deployed HF Space on 2026-09-02
    (``docs/GAPS_AND_AGENTIC_ROADMAP.md`` §2.11, G44). ``plan_rate_lookup``
    required BOTH a "rate"/"threshold" word AND a named tax, so the way
    taxpayers actually ask missed it and fell through to hybrid retrieval:

        "How much of my salary is tax free?"              -> hybrid, "235,000"
        "At what monthly salary do I start paying PAYE?"  -> hybrid, "235,000"

    Retrieval answered from superseded handbook editions (FY2024-25,
    FY2025-26) still carrying the old threshold, while the FY2026-27 rate
    table says 335,000 — so the taxpayer was told the tax-free line sits
    100,000 UGX/month lower than it does. The same question phrased as
    "What are the PAYE rates?" was correct throughout, which is what kept
    this invisible.
    """

    def test_plain_language_threshold_questions_reach_the_rate_table(self) -> None:
        for message in (
            "How much of my salary is tax free?",
            "At what monthly salary do I start paying PAYE?",
            "Is my salary exempt from tax?",
            "how much of my wage is tax-free",
            "What part of my earnings is untaxed?",
        ):
            with self.subTest(message=message):
                plan = plan_rate_lookup(message)
                self.assertIsNotNone(plan, "must not fall through to retrieval")
                self.assertEqual(plan.summary, "paye")

    def test_procedural_and_other_taxes_are_not_hijacked(self) -> None:
        """The widened gate must not swallow questions it cannot answer.

        A registration question wants the procedure, not a band table; an
        allowance question wants the exempt list; and a turnover question is
        about VAT registration, where quoting the 18% standard rate would be
        the same class of wrong answer this guard exists to prevent.
        """
        for message in (
            "Which allowances are exempt from PAYE?",
            "How do I start paying PAYE?",
            "How do I register for PAYE?",
            "At what turnover do I start paying VAT?",
            "When is my PAYE return due?",
            "What is my salary?",
        ):
            with self.subTest(message=message):
                self.assertIsNone(plan_rate_lookup(message))

    def test_existing_rate_routing_is_unchanged(self) -> None:
        for message, expected in (
            ("What are the PAYE rates?", "paye"),
            ("What is the VAT rate?", "vat_standard"),
            ("What is the VAT registration threshold?", "vat_registration_threshold_annual"),
            ("What is the corporation tax rate?", "corporation_tax"),
            ("What is the rental tax rate for a company?", "rental_tax_company"),
            ("withholding tax rate on dividends", "withholding_dividend"),
        ):
            with self.subTest(message=message):
                plan = plan_rate_lookup(message)
                self.assertIsNotNone(plan)
                self.assertEqual(plan.summary or plan.tax_type, expected)

    def test_a_bands_question_reaches_the_rate_table(self) -> None:
        """The original G44 phrasing: "bands" is how the schedule is asked for.

        ``_RATE_TYPE_RES`` already carried an ``income tax band`` alternative,
        so the type gate was ready for this — it was the ask gate, which only
        knew "rate" and "threshold", that turned the question away. The type
        pattern was also singular, so the plural nobody actually writes as
        "band" never matched either.
        """
        for message in (
            "What are the PAYE tax bands in Uganda?",
            "What are the PAYE bands?",
            "What are the income tax bands?",
            "What is the income tax band?",
        ):
            with self.subTest(message=message):
                plan = plan_rate_lookup(message)
                self.assertIsNotNone(plan, "must not fall through to retrieval")
                self.assertEqual(plan.summary, "paye")

    def test_bands_alone_is_not_a_rate_question(self) -> None:
        """"Band" is an ordinary English word; it needs a named tax too."""
        for message in (
            "Which tax band am I in?",
            "The band played at the URA staff party",
        ):
            with self.subTest(message=message):
                self.assertIsNone(plan_rate_lookup(message))

    def test_an_amount_still_routes_to_the_calculator(self) -> None:
        """``plan_rate_lookup`` yields to ``plan_calculation`` on amounts.

        Without this, "is a salary of 300,000 tax free?" would answer with the
        band table instead of the figure the taxpayer asked for.
        """
        self.assertIsNone(plan_rate_lookup("Is a salary of 300,000 tax free?"))

    def test_the_guard_never_matches_the_empty_string(self) -> None:
        """A stray ``|`` here would route every message to the PAYE bands."""
        self.assertIsNone(_PAYE_THRESHOLD_ASK_RE.search(""))
        self.assertIsNone(_PAYE_THRESHOLD_ASK_RE.search("hello"))


class VatRegistrationScopeTests(unittest.TestCase):
    """The registration check must own only questions *about* registering.

    Measured against the live Space (issue #430): "my business is registered
    for vat, do i have to use efris" returned the turnover-elicitation
    workflow.  "vat" and "registered" co-occurred and an obligation word
    appeared somewhere in the sentence, which was the whole gate — so a
    declarative premise plus a question about a different obligation was
    indistinguishable from "must I register?".
    """

    def test_an_already_registered_premise_does_not_claim_the_question(self) -> None:
        for message in (
            "my business is registered for vat, do i have to use efris",
            "i am registered for vat, do i need to file monthly returns",
            "i'm vat registered, what is efris",
            "we are already registered for vat, must we issue e-invoices",
            "our company has been registered for vat, do we need a tax agent",
            # A possessive subject is not always one word.
            "my small business is registered for vat, do i have to use efris",
        ):
            with self.subTest(message=message):
                plan = plan_calculation(message)
                if plan is not None:
                    self.assertNotEqual(plan.tool, "check_vat_registration")

    def test_the_guard_survives_abbreviation_expansion(self) -> None:
        """`_maybe_handle_calculator` falls back to `plan_calculation(rewritten)`.

        The rewriter expands abbreviations, so "registered for vat" reaches the
        router as "registered for Value Added Tax (VAT)". The guard matched only
        the short spelling, so it passed on the raw message and was bypassed on
        the rewritten one — the live Space still returned the turnover workflow
        with every unit test here green, because they all asked in raw wording.
        """
        for message in (
            "my business is registered for vat, do i have to use efris",
            "my small business is registered for vat, do i have to use efris",
            "i am registered for vat, do i need to keep records in english",
            "our firm has been registered for vat since 2023, must we issue e-invoices",
            "i'm vat registered, what is efris",
        ):
            rewritten = rewrite_query(message, history=None)
            # Without this the loop can check the abbreviated spelling twice and
            # prove nothing — which is the exact shape of the blind spot that
            # let the original bug ship.
            self.assertIn("value added tax", rewritten.lower(), rewritten)
            for form in (message, rewritten):
                with self.subTest(form=form):
                    plan = plan_calculation(form)
                    if plan is not None:
                        self.assertNotEqual(plan.tool, "check_vat_registration")

    def test_genuine_questions_route_in_both_spellings(self) -> None:
        for message in (
            "do i have to register for vat",
            "am i required to be registered for vat",
            "my business is registered, do i have to register for vat",
        ):
            rewritten = rewrite_query(message, history=None)
            self.assertIn("value added tax", rewritten.lower(), rewritten)
            for form in (message, rewritten):
                with self.subTest(form=form):
                    plan = plan_calculation(form)
                    self.assertIsNotNone(plan, form)
                    self.assertEqual(plan.tool, "check_vat_registration")

    def test_genuine_registration_questions_still_route(self) -> None:
        """The premise guard keys on subject-then-copula, so a question that
        merely contains "registered" is untouched."""
        for message in (
            "do i have to register for vat",
            "must i register for vat if my turnover is 200m",
            "am i required to be registered for vat",
            "should my company register for vat",
            "when do i need to register for vat",
            # The premise names a registration that is not the VAT one, and the
            # real question follows it. Suppressing here answered nothing at all.
            "my business is registered, do i have to register for vat",
        ):
            with self.subTest(message=message):
                plan = plan_calculation(message)
                self.assertIsNotNone(plan, message)
                self.assertEqual(plan.tool, "check_vat_registration")
