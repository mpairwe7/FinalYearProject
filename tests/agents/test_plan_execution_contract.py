"""A plan runs once, within a budget, and says what it could not do.

The deterministic planner produces the block the model is told to quote verbatim.
The defects pinned here share a shape: a step that did not complete left no mark,
so the instruction "present these exact computed figures" sat above a gap the
model then filled from memory.
"""

from __future__ import annotations

import pytest

from app import scenario_planner as sp
from app.scenario_planner import PlanRunReport, build_verified_facts, plan_digest, plan_tools

PAYE_AND_VAT = "What is PAYE on a monthly salary of UGX 3,000,000 and VAT on UGX 1,000,000 purchase?"
USD_SALARY = "My salary is USD 5,000 per month, what is my PAYE?"
COMPOUND = (
    "I earn UGX 3,000,000 salary, rent out flats for UGX 2,000,000 monthly, "
    "bought stock for UGX 1,000,000 with VAT, paid management fees of UGX 500,000 to a foreign consulting firm, "
    "imported a used car with CIF value USD 9,000, sold land for UGX 50,000,000, stamp duty on lease of UGX 9,000,000, "
    "late filing penalty on UGX 800,000, gaming winnings UGX 100,000, excise on beer UGX 300,000"
)


def _ok(tool: str, _params: dict) -> dict:
    return {"ok": True, "explanation": f"{tool} done", "ugx_amount": 1000.0}


class TestFailureIsNamed:
    def test_a_crashed_step_is_listed_not_dropped(self):
        def crashing(tool: str, params: dict) -> dict:
            if tool == "calculate_paye":
                raise RuntimeError("tool crashed")
            return _ok(tool, params)

        report = PlanRunReport()
        block = build_verified_facts(PAYE_AND_VAT, tool_caller=crashing, report=report)

        assert "Not calculated: calculate paye" in block
        assert "do not estimate one" in block
        assert "calculate_vat done" in block
        assert report.outcomes == {"failed": 1, "ok": 1}

    def test_an_error_result_with_no_named_input_is_a_failure(self):
        def erroring(tool: str, params: dict) -> dict:
            return {"ok": False, "error": "schema"} if tool == "calculate_vat" else _ok(tool, params)

        block = build_verified_facts(PAYE_AND_VAT, tool_caller=erroring)

        assert "Not calculated: calculate vat" in block

    def test_a_result_that_cannot_be_rendered_costs_only_itself(self):
        # format_calc_reply raises on a result missing a field, and that used to
        # lose every other figure in the block as well.
        report = PlanRunReport()

        block = build_verified_facts(
            "I rent out flats for UGX 2,000,000 monthly and bought stock for UGX 1,000,000 with VAT",
            tool_caller=_ok,
            report=report,
        )

        assert "Not calculated: calculate rental tax" in block
        assert "calculate_vat done" in block
        assert report.outcomes["failed"] == 1

    def test_an_answerable_gap_is_a_question_not_a_failure(self):
        def needs(tool: str, params: dict) -> dict:
            return {"ok": False, "needs": "annual_turnover"} if tool == "calculate_vat" else _ok(tool, params)

        report = PlanRunReport()
        block = build_verified_facts(PAYE_AND_VAT, tool_caller=needs, report=report)

        assert "Still needed for an exact figure: annual turnover" in block
        assert "Not calculated" not in block
        assert report.outcomes["needs_input"] == 1

    def test_a_failed_conversion_asks_for_the_rate_when_live_fx_is_on(self, clean_flags):
        # Planning sees live FX enabled and lists nothing as missing, so when the
        # network then failed the block said nothing about the rate at all.
        clean_flags.set("live_web_research", True)

        def fx_down(tool: str, params: dict) -> dict:
            return {"ok": False, "error": "network"} if tool == "convert_currency" else _ok(tool, params)

        block = build_verified_facts(USD_SALARY, tool_caller=fx_down)

        assert "exchange rate (USD to UGX) on payment date" in block


class TestBudgets:
    def test_the_plan_stops_at_its_call_budget_and_says_so(self, monkeypatch):
        monkeypatch.setattr(sp, "PLANNER_MAX_TOOL_CALLS", 2)
        calls: list[str] = []

        def counting(tool: str, params: dict) -> dict:
            calls.append(tool)
            return _ok(tool, params)

        report = PlanRunReport()
        block = build_verified_facts(COMPOUND, tool_caller=counting, report=report)

        assert len(calls) == 2
        assert "Not calculated:" in block
        assert report.outcomes["skipped"] >= 1

    def test_a_large_compound_plan_fits_the_default_budget(self):
        assert len(plan_tools(COMPOUND)) <= sp.PLANNER_MAX_TOOL_CALLS

        report = PlanRunReport()
        build_verified_facts(COMPOUND, tool_caller=_ok, report=report)

        assert report.outcomes["skipped"] == 0

    def test_the_deadline_stops_a_slow_plan(self, monkeypatch):
        monkeypatch.setattr(sp, "PLANNER_DEADLINE_SECONDS", 0.0)
        calls: list[str] = []

        def counting(tool: str, params: dict) -> dict:
            calls.append(tool)
            return _ok(tool, params)

        block = build_verified_facts(PAYE_AND_VAT, tool_caller=counting)

        assert calls == []
        assert "Not calculated:" in block


class TestDataFlow:
    def test_paye_uses_the_salary_conversion_whatever_ran_last(self):
        seen: dict = {}

        def caller(tool: str, params: dict) -> dict:
            if tool == "convert_currency":
                rate = 4000.0 if params["currency"] == "USD" else 5000.0
                return {"ok": True, "currency": params["currency"], "ugx_amount": params["amount"] * rate}
            if tool == "calculate_paye":
                seen.update(params)
            return {"ok": True, "explanation": tool}

        build_verified_facts(
            "I paid EUR 2,000 for consulting. My salary is USD 5,000 per month. What is my PAYE?",
            tool_caller=caller,
        )

        assert seen.get("monthly_gross") == 20_000_000.0


class TestPlanIdentity:
    def test_the_digest_is_stable_and_specific(self):
        first = plan_digest(plan_tools(PAYE_AND_VAT))

        assert first == plan_digest(plan_tools(PAYE_AND_VAT))
        assert first != plan_digest(plan_tools(USD_SALARY))

    def test_the_report_holds_the_digest_and_no_amounts(self):
        report = PlanRunReport()

        build_verified_facts(PAYE_AND_VAT, tool_caller=_ok, report=report)

        summary = report.summary()
        assert summary["sha256"] == plan_digest(plan_tools(PAYE_AND_VAT))
        assert "3000000" not in str(summary)


class TestOncePerTurn:
    def test_prompt_building_and_verification_share_one_run(self, monkeypatch):
        from app import service

        calls: list[str] = []

        def fake(query: str, facts=None, *, report=None, **_kw) -> str:
            calls.append(query)
            report.plan_sha256 = "abc"
            return "block"

        monkeypatch.setattr(sp, "build_verified_facts", fake)
        token = service._TURN_PLANS.set({})
        try:
            first = service._evaluate_calculation_context("What is PAYE?")
            second = service._evaluate_calculation_context("what is  paye?")
            summary = service._turn_plan_summary()
        finally:
            service._TURN_PLANS.reset(token)

        assert first == second == "block"
        assert calls == ["What is PAYE?"]
        assert summary["sha256"] == "abc"

    def test_outside_a_turn_each_call_runs(self, monkeypatch):
        from app import service

        calls: list[str] = []

        def fake(query: str, facts=None, *, report=None, **_kw) -> str:
            calls.append(query)
            return "block"

        monkeypatch.setattr(sp, "build_verified_facts", fake)

        service._evaluate_calculation_context("What is PAYE?")
        service._evaluate_calculation_context("What is PAYE?")

        assert len(calls) == 2


class TestAuditRow:
    def test_the_turn_records_its_plan(self, tmp_db, clean_flags, fresh_registry):
        from app.audit.turns import build_turn_payload

        payload = build_turn_payload(
            message="q",
            result={"_plan": {"sha256": "abc", "steps": {"ok": 2}, "not_computed": []}},
            channel="rest",
        )

        assert payload["plan"]["sha256"] == "abc"


class TestPlanReachesTheLedger:
    def test_a_real_turn_writes_its_plan_digest(self, tmp_db, clean_flags, fresh_registry, monkeypatch):
        from app import audit
        from app.service import ChatModel

        written: list[dict] = []

        def capture(**kwargs):
            written.append(kwargs["result"])
            return True

        monkeypatch.setattr(audit.turns, "append_turn", capture)
        question = "I am a landlord earning rent of UGX 4,000,000 a year from one flat. How is it taxed?"

        ChatModel().generate(question)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert written, "the turn must reach the ledger writer"
        assert written[0]["_plan"]["sha256"] == plan_digest(plan_tools(question))


class TestGraphBinding:
    @pytest.fixture
    def state(self):
        from app.agents.graphs.state import AgentGraphState

        return lambda query: AgentGraphState(query=query, rewritten_query=query, locale="en", top_k=3)

    def test_a_year_is_not_an_amount(self, state, fresh_registry, monkeypatch):
        from app import calculator_router
        from app.agents.graphs.main_graph import bind_arguments

        monkeypatch.setattr(calculator_router, "plan_calculation", lambda _q: None)

        bound = bind_arguments("calculate_vat", state("In 2026 what is VAT on UGX 500,000?"))

        assert bound == {"amount": 500_000.0}

    def test_two_amounts_are_not_guessed_between(self, state, fresh_registry, monkeypatch):
        from app import calculator_router
        from app.agents.graphs.main_graph import bind_arguments

        monkeypatch.setattr(calculator_router, "plan_calculation", lambda _q: None)

        assert bind_arguments("calculate_vat", state("VAT on UGX 500,000 or UGX 900,000?")) is None


class TestRuntimeAttestation:
    FULL_TURN = {
        "query_rewrite": 1.0,
        "lang_detect": 1.0,
        "input_guard": 2.0,
        "workflow_router": 1.0,
        "calculator_router": 1.0,
        "cache_lookup": 1.0,
        "supervisor": 1.0,
        "hybrid_search": 100.0,
        "corrective_rag": 10.0,
        "faq_blend": 5.0,
        "abstention_check": 1.0,
        "llm_generate": 900.0,
        "output_guard": 2.0,
        "grounding": 4.0,
    }

    def test_a_complete_turn_attests(self):
        from app.turn_pipeline import attest_turn

        verdict = attest_turn(self.FULL_TURN)

        assert verdict["ok"] is True
        assert verdict["stage_count"] == 7
        assert verdict["sla_breaches"] == []

    def test_a_tool_turn_is_in_order(self):
        # llm_agentic runs before abstention_check, both inside synthesis.
        from app.turn_pipeline import attest_turn

        timings = {"input_guard": 1.0, "hybrid_search": 1.0, "llm_agentic": 1.0, "abstention_check": 1.0}

        assert attest_turn(timings)["ok"] is True

    def test_a_turn_that_skipped_the_input_guard_fails(self):
        from app.turn_pipeline import attest_turn

        verdict = attest_turn({"query_rewrite": 1.0, "hybrid_search": 1.0, "llm_generate": 1.0})

        assert verdict["ok"] is False
        assert verdict["missing_mandatory_guards"] == ["stage_1_input_guard"]

    def test_a_guard_after_generation_is_an_order_violation(self):
        from app.turn_pipeline import attest_turn

        verdict = attest_turn({"llm_generate": 1.0, "input_guard": 1.0})

        assert verdict["monotonic_stage_order"] is False

    def test_an_early_exit_is_audited_and_attests(self):
        from app.turn_pipeline import attest_turn

        assert attest_turn({"query_rewrite": 1.0, "lang_detect": 1.0, "input_guard": 1.0})["ok"] is True

    def test_live_research_does_not_count_against_the_retrieval_sla(self):
        from app.turn_pipeline import attest_turn

        verdict = attest_turn({"input_guard": 1.0, "hybrid_search": 100.0, "live_web_research": 6000.0})

        assert verdict["sla_breaches"] == []

    def test_a_real_turn_reaches_the_audit_row(self, tmp_db, clean_flags, fresh_registry):
        from app.audit.turns import build_turn_payload
        from app.service import ChatModel

        result = ChatModel().generate("How do I evade tax?")  # nosemgrep: ura-llm01-raw-user-input-to-llm

        payload = build_turn_payload(message="q", result=result, channel="rest")

        assert payload["pipeline_attestation"]["ok"] is True
        assert payload["pipeline_attestation"]["manifest_sha256"]


class TestPlanSpan:
    class _Span:
        def __init__(self, name: str, attributes: dict):
            self.name = name
            self.attributes = dict(attributes)

        def set_attribute(self, key: str, value) -> None:
            self.attributes[key] = value

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def test_planning_is_one_span_named_by_the_conventions(self, monkeypatch):
        from app import tracing

        spans: list = []

        class Tracer:
            def start_as_current_span(_self, name, attributes=None, **_kw):
                span = TestPlanSpan._Span(name, attributes or {})
                spans.append(span)
                return span

        monkeypatch.setattr(tracing, "_tracer", Tracer())

        build_verified_facts(PAYE_AND_VAT, tool_caller=_ok)

        plan = next(span for span in spans if span.name.startswith("plan "))
        assert plan.name == f"plan {tracing.AGENT_NAME}"
        assert plan.attributes["gen_ai.operation.name"] == "plan"
        assert plan.attributes["ura.plan.sha256"] == plan_digest(plan_tools(PAYE_AND_VAT))
        assert plan.attributes["ura.plan.step_count"] == 2

    def test_with_tracing_off_planning_still_runs(self):
        assert build_verified_facts(PAYE_AND_VAT, tool_caller=_ok)
