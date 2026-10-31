"""What the taxpayer reads is checked before they read it.

A tax figure is what a taxpayer acts on, so these are the checks that matter most.
The token stream sent text as the model wrote it, and verification ran afterwards:
"A figure that contradicts its own cited passage does not get printed" was false on
the web channel, where it was printed and then corrected. The deterministic figure
lock ran only on REST, and there it was never shown the tool results, so it rejected
correct calculator figures. Stored facts could fill a calculator slot they did not
mean: chargeable income computed from turnover, or an account lookup steered by an
identifier taken from another tool's output.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types
from typing import Any
from unittest import mock

import pytest
from app import llm
from app import service as service_module
from app.agents.evaluator import verify_figure_lock
from app.agents.graphs import main_graph
from app.agents.graphs.state import AgentGraphState
from app.agents.state import AgentRoute, RouteDecision
from app.agents.tiebreak import _READ_ONLY_TOOLS, cache_clear, refine
from app.analytics import metrics
from app.flags import flags
from app.guardrails import FigureReleaseGate, OutputGuard
from app.providers.routing import ModelTier
from app.tools import ToolRegistry

HIT = {"text": "The standard VAT rate in Uganda is 18 percent.", "source": "vat.pdf", "score_rerank": 0.9}
CITATION = {"marker": "1", "ref": "[1]", "source": "vat.pdf", "text": HIT["text"]}


class TestTheReleaseGate:
    def _feed(self, *chunks: str) -> tuple[str, str]:
        gate = FigureReleaseGate()
        released = "".join(gate.feed(chunk) for chunk in chunks)
        return released, gate.drain()

    def test_prose_flows(self) -> None:
        released, held = self._feed("You can ", "register online ", "through the portal. ", "Then wait. ")
        assert released == "You can register online through the portal. Then wait. "
        assert held == ""

    def test_a_figure_is_held_from_the_start_of_its_sentence(self) -> None:
        released, held = self._feed("You must register. ", "The rate ", "is 18% ", "of the price. ", "Pay on time. ")
        assert released == "You must register. "
        assert held == "The rate is 18% of the price. Pay on time. "

    def test_a_digit_split_across_tokens_is_still_caught(self) -> None:
        released, held = self._feed("The rate is 1", "8 percent. ")
        assert "1" not in released
        assert held == "The rate is 18 percent. "

    def test_a_citation_is_not_a_figure(self) -> None:
        released, held = self._feed("Register online [1]. ", "Keep records [2]. ")
        assert released == "Register online [1]. Keep records [2]. "
        assert held == ""

    def test_a_citation_split_across_tokens_is_not_a_figure(self) -> None:
        released, _held = self._feed("Register online [", "1]. ", "Keep records. ")
        assert "Keep records. " in released

    @pytest.mark.parametrize("word", ["percent", "million", "eighteen", "thousand"])
    def test_a_figure_in_words_is_a_figure(self, word: str) -> None:
        released, held = self._feed("Note this. ", f"It is {word} here. ")
        assert word not in released
        assert word in held

    def test_a_word_split_across_tokens_is_still_caught(self) -> None:
        released, _held = self._feed("It is mill", "ion shillings. ")
        assert "mill" not in released

    def test_a_tail_without_a_boundary_stays_held(self) -> None:
        released, held = self._feed("Register now")
        assert released == ""
        assert held == "Register now"

    def test_once_holding_it_keeps_holding(self) -> None:
        gate = FigureReleaseGate()
        gate.feed("It is 18% here. ")
        assert gate.feed("Plain prose now. ") == ""
        assert gate.holding is True

    def test_it_can_be_turned_off(self) -> None:
        gate = FigureReleaseGate(enabled=False)
        assert gate.feed("It is 18% here. ") == "It is 18% here. "
        assert gate.drain() == ""


class Model:
    name = "test-model"

    def __init__(self) -> None:
        self._cache = types.SimpleNamespace(put=lambda *a, **k: None, get=lambda *a, **k: None)
        self.audited: list[dict[str, Any]] = []
        self.judge: dict[str, Any] = {
            "decision": "approve",
            "final_decision": "approve",
            "reasons": [],
            "confidence_band": "high",
            "revised_reply": "",
        }

    def generate_retrieval_only(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "_hits": [HIT],
            "retrieval_mode": "hybrid",
            "sources": ["vat.pdf"],
            "citations": [CITATION],
            "_history": [],
            "_rewritten": kwargs.get("message", ""),
            "_personalization_context": "",
            "conversation_id": "conv-1",
            "agent_role": "rag_answerer",
            "reply": "",
            "model": "test-model",
            "locale": kwargs.get("locale", "en"),
            "next_actions": [],
        }

    def record_turn_audit(self, **kwargs: Any) -> bool:
        self.audited.append(kwargs)
        return True

    def _evaluate_response_judge(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.judge)

    def _build_handoff_packet(self, **_kwargs: Any) -> dict[str, Any]:
        return {"priority": "normal"}

    def _maybe_create_ticket(self, **_kwargs: Any) -> str:
        return ""


ARGUMENTS = {
    "message": "What is the VAT rate?",
    "conversation_id": "conv-1",
    "top_k": 4,
    "locale": "en",
    "session_id": "s",
    "request_id": "r",
    "user_id": "",
    "tenant_id": "default",
}


def run_turn(model: Model, **overrides: Any) -> list[tuple[str, Any]]:
    async def collect() -> list[tuple[str, Any]]:
        return [e async for e in service_module.run_chat_turn(model, channel="sse", **{**ARGUMENTS, **overrides})]

    return asyncio.run(collect())


def visible(events: list[tuple[str, Any]]) -> str:
    """What the taxpayer's screen shows at the end of *events*."""
    text = ""
    for kind, payload in events:
        if kind == "token":
            text += payload
        elif kind == "revision":
            text = payload
    return text


def ever_shown(events: list[tuple[str, Any]]) -> str:
    """Everything the taxpayer saw at any point, including what was replaced."""
    return "".join(payload for kind, payload in events if kind in ("token", "revision"))


@pytest.fixture
def stream(monkeypatch):
    def install(*words: str) -> None:
        monkeypatch.setattr(service_module, "stream_llm_tokens", lambda *_a, **_k: iter(words))

    monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
    monkeypatch.setattr(service_module.llm_module, "can_generate_in_locale", lambda _locale: True)
    monkeypatch.setattr(service_module, "_answer_in_english", lambda text: text)
    monkeypatch.setattr(OutputGuard, "should_abstain", lambda _self, _hits, locale="en": False)
    return install


CONTRADICTED = {
    "decision": "escalate",
    "contradicted_claims": [{"text": "The VAT rate is 25%."}],
    "unsupported_claims": [],
    "uncited_claims": [],
}


class TestAnUnverifiedFigureIsNeverShown:
    def test_a_contradicted_figure_never_reaches_the_screen(self, stream, monkeypatch) -> None:
        stream("The VAT rate is 25%. ", "Register online. [1]")
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: CONTRADICTED)

        events = run_turn(Model())

        assert "25%" not in ever_shown(events)

    def test_prose_before_the_figure_still_streams_first(self, stream, monkeypatch) -> None:
        stream("You must register first. ", "The rate is 18 percent. [1]")
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})

        events = run_turn(Model())

        tokens = [payload for kind, payload in events if kind == "token"]
        assert tokens[0].startswith("You must register first.")
        assert "18" not in tokens[0]

    def test_a_verified_figure_arrives_as_a_token_with_no_revision(self, stream, monkeypatch) -> None:
        stream("You must register. ", "The rate is 18 percent. [1]")
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})

        events = run_turn(Model())

        assert visible(events).count("18 percent") == 1
        assert not any(kind == "revision" for kind, _ in events)

    def test_the_figure_is_released_before_the_turn_closes(self, stream, monkeypatch) -> None:
        stream("The rate is 18 percent. [1]")
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})

        kinds = [kind for kind, _ in run_turn(Model())]

        assert kinds.index("token") < kinds.index("grounding") < kinds.index("done")

    def test_held_text_is_not_mistaken_for_an_empty_stream(self, stream, monkeypatch) -> None:
        # The whole reply held from its first word used to read as "the model said
        # nothing", which fell through to the extractive answer.
        stream("18 percent is the rate. [1]")
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})

        events = run_turn(Model())

        assert "18 percent is the rate" in visible(events)

    def test_a_held_figure_dies_with_the_deadline(self, stream, monkeypatch) -> None:
        def slow(*_a: Any, **_k: Any):
            yield "You must register first. "
            yield "The rate is 25 percent. "
            time.sleep(0.3)
            yield "More text. "

        monkeypatch.setattr(service_module, "stream_llm_tokens", slow)

        events = run_turn(Model(), turn_deadline_s=0.15)

        assert "25" not in ever_shown(events)

    def test_the_audit_row_has_what_was_served(self, stream, monkeypatch) -> None:
        stream("You must register. ", "The rate is 18 percent. [1]")
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})
        model = Model()

        run_turn(model)

        assert "18 percent" in model.audited[0]["result"]["reply"]

    def test_the_gate_can_be_switched_off(self, stream, monkeypatch, clean_flags) -> None:
        flags.set("stream_figure_hold", False)
        stream("The VAT rate is 25%. ", "Register online. [1]")
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})

        events = run_turn(Model())

        assert "25%" in [payload for kind, payload in events if kind == "token"][0]

    def test_a_tool_answer_is_checked_before_any_of_it_is_shown(self, monkeypatch) -> None:
        # The tool loop produces its reply whole, so waiting costs nothing.
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
        monkeypatch.setattr(service_module.llm_module, "can_generate_in_locale", lambda _locale: True)
        monkeypatch.setattr(service_module, "_answer_in_english", lambda text: text)
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: CONTRADICTED)
        monkeypatch.setattr(
            service_module,
            "_call_llm_agentic",
            lambda **_kw: {"text": "The VAT rate is 25% [1].", "tool_calls": [], "iterations": 1, "truncated": False},
        )
        model = Model()
        original = model.generate_retrieval_only
        model.generate_retrieval_only = lambda **kw: {**original(**kw), "_force_agentic": True}

        events = run_turn(model)

        assert "25%" not in ever_shown(events)


class TestTheFigureLockRunsOnEveryTransport:
    def _guards(self, reply: str, message: str = "What is the VAT rate?", **kwargs: Any) -> dict[str, Any]:
        return service_module._apply_output_guards(
            Model(),
            message=message,
            reply=reply,
            hits=[HIT],
            citations=[CITATION],
            conversation_history=None,
            session_id=None,
            conversation_id="c",
            output_guard=OutputGuard(),
            **kwargs,
        )

    def test_the_reasoning_tier_strips_an_invented_figure_when_streamed(self, monkeypatch) -> None:
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})
        guard = self._guards(
            "The rate is 18% [1]. The penalty is UGX 7,777,777 [1].", model_tier=ModelTier.T3.value
        )
        assert "7,777,777" not in guard["reply"]
        assert guard["revised"] is True
        assert guard["figure_lock"]["mode"] == "enforced"

    def test_the_flag_enforces_it_on_any_tier(self, monkeypatch, clean_flags) -> None:
        flags.set("evaluator_optimizer", True)
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})
        guard = self._guards("The penalty is UGX 7,777,777 [1].")
        assert "7,777,777" not in guard["reply"]

    def test_otherwise_it_measures_without_touching_the_reply(self, monkeypatch, clean_flags) -> None:
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})
        key = 'figure_lock_checks_total{mode="shadow",outcome="reject"}'
        before = metrics.snapshot()["counters"].get(key, 0)

        guard = self._guards("The penalty is UGX 7,777,777 [1].")

        assert "7,777,777" in guard["reply"]
        assert guard["figure_lock"]["mode"] == "shadow"
        assert guard["figure_lock"]["ok"] is False
        assert metrics.snapshot()["counters"].get(key, 0) - before == 1

    def test_a_figure_a_calculator_returned_is_not_invented(self, monkeypatch) -> None:
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})
        guard = self._guards(
            "VAT on 1,000,000 is UGX 180,000 [1].",
            message="What is the VAT on 1,000,000?",
            model_tier=ModelTier.T3.value,
            tool_outputs=[{"ok": True, "vat": 180000.0}],
        )
        assert "180,000" in guard["reply"]

    def test_a_withheld_reply_is_not_checked_again(self, monkeypatch) -> None:
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: CONTRADICTED)
        guard = self._guards("The penalty is UGX 7,777,777 [1].", model_tier=ModelTier.T3.value)
        assert guard["response_judge"]["withheld_contradicted"] is True
        assert guard["figure_lock"] == {}

    def test_rest_gives_the_lock_the_tool_results(self) -> None:
        # Without them the lock rejected a correct calculator figure as invented.
        assert verify_figure_lock(
            "VAT on 1,000,000 is UGX 180,000 [1].",
            query="VAT on 1,000,000?",
            passages=[HIT["text"]],
            tool_outputs=[{"ok": True, "vat": 180000.0}],
        )[0] is True

    def test_the_audit_row_says_what_the_lock_found(self) -> None:
        from app.audit import turns

        payload = turns.build_turn_payload(
            message="q",
            result={"reply": "r", "_figure_lock": {"mode": "shadow", "ok": False, "rejected": 2}},
            channel="sse",
        )
        assert payload["figure_lock"] == {"mode": "shadow", "ok": False, "rejected": 2}


class TestASlotOnlyTakesWhatItMeans:
    def _bind(self, tool: str, required: list[str], observation: dict[str, Any]) -> dict[str, Any] | None:
        state = AgentGraphState(query="please compute it")
        state.observations = [{"ok": True, **observation}]
        return main_graph._bind_from_memory_or_dag(tool, required, state)

    def test_turnover_is_not_chargeable_income(self) -> None:
        assert self._bind("calculate_corporation_tax", ["chargeable_income"], {"annual_turnover": 500_000_000}) is None

    def test_a_vat_inclusive_landed_cost_is_not_a_vat_base(self) -> None:
        assert self._bind("calculate_vat", ["amount"], {"landed_cost": 9_000_000}) is None

    def test_turnover_is_not_a_payment(self) -> None:
        assert self._bind("calculate_vat", ["amount"], {"annual_turnover": 500_000_000}) is None

    def test_a_vague_amount_is_not_a_cif_value(self) -> None:
        assert self._bind("calculate_customs_duty", ["cif_value"], {"amount": 1_000_000}) is None

    def test_an_annual_figure_is_not_a_monthly_one(self) -> None:
        assert self._bind("calculate_rental_tax", ["gross_rent"], {"annual_gross_rent": 12_000_000}) is None

    def test_the_same_concept_still_binds(self) -> None:
        assert self._bind("calculate_paye", ["monthly_salary"], {"gross_salary": 3_000_000}) == {
            "monthly_salary": 3_000_000
        }

    @pytest.mark.parametrize("slot", ["taxpayer_id", "tin"])
    def test_an_identifier_never_comes_from_a_tool_result(self, slot: str) -> None:
        # One tool's output choosing whose record the next tool reads.
        assert self._bind("ura_account_profile", [slot], {"tin": "1000000001", "taxpayer_id": "1000000001"}) is None

    def test_what_was_assumed_is_recorded(self) -> None:
        state = AgentGraphState(query="please compute it")
        state.observations = [{"ok": True, "gross_salary": 3_000_000}]
        main_graph._bind_from_memory_or_dag("calculate_paye", ["monthly_salary"], state)
        assert state.assumed_inputs == [
            {"tool": "calculate_paye", "inputs": ["monthly_salary"], "source": "dag_receipt"}
        ]

    def test_recording_is_not_repeated_for_the_same_call(self) -> None:
        state = AgentGraphState(query="please compute it")
        state.observations = [{"ok": True, "gross_salary": 3_000_000}]
        for _ in range(3):
            main_graph._bind_from_memory_or_dag("calculate_paye", ["monthly_salary"], state)
        assert len(state.assumed_inputs) == 1

    def test_the_taxpayer_is_told(self) -> None:
        state = AgentGraphState(query="q", reply="Your PAYE is UGX 100,000.")
        state.assumed_inputs = [{"tool": "calculate_paye", "inputs": ["monthly_salary"], "source": "semantic_memory"}]
        result = main_graph.node_respond(state)
        assert result.next_node == main_graph.END
        assert "monthly salary" in state.reply
        assert "UGX 100,000" in state.reply


@pytest.fixture
def tool_loop(monkeypatch):
    monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
    monkeypatch.setattr(llm, "_vllm_ready", lambda: True)
    monkeypatch.setattr(llm, "_select_tools_for_query", lambda _query, names, **_options: names)
    monkeypatch.setattr(llm, "_fit_tools_to_window", lambda _m, specs, **_kw: specs)


class TestATruncatedLoopIsNotAFinishedAnswer:
    def test_the_taxpayer_is_told_on_rest(self) -> None:
        reply = service_module._with_degraded_disclosure("Part of an answer.", ["tool_loop_truncated"])
        assert reply.startswith("Part of an answer.")
        assert "may not be complete" in reply

    def test_a_disclosure_is_added_once(self) -> None:
        once = service_module._with_degraded_disclosure("Answer.", ["tool_loop_truncated"])
        assert service_module._with_degraded_disclosure(once, ["tool_loop_truncated"]) == once

    def test_the_stream_says_so_too(self, monkeypatch) -> None:
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
        monkeypatch.setattr(service_module.llm_module, "can_generate_in_locale", lambda _locale: True)
        monkeypatch.setattr(service_module, "_answer_in_english", lambda text: text)
        monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})
        monkeypatch.setattr(
            service_module,
            "_call_llm_agentic",
            lambda **_kw: {
                "text": "I checked some of it [1].",
                "tool_calls": [{"name": "calculate_vat", "result": {"ok": True}}],
                "iterations": 10,
                "truncated": True,
            },
        )
        model = Model()
        original = model.generate_retrieval_only
        model.generate_retrieval_only = lambda **kw: {**original(**kw), "_force_agentic": True}

        events = run_turn(model)

        assert "may not be complete" in visible(events)
        assert "tool_loop_truncated" in model.audited[0]["result"]["_generation"]["degraded"]


class TestAModelCannotWidenItsOwnTools:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        cache_clear()
        yield
        cache_clear()

    def _decide(self, route: str) -> RouteDecision:
        base = RouteDecision(route=AgentRoute.RAG, reason="default", confidence=0.6)
        return refine("some question", base, classifier=lambda _prompt: f'{{"route": "{route}"}}')

    @pytest.mark.parametrize("route", ["tools", "tax_specialist", "customs_specialist"])
    def test_a_model_chosen_tool_route_gets_a_closed_list(self, route: str) -> None:
        decision = self._decide(route)
        assert decision.suggested_tools
        assert set(decision.suggested_tools) <= set(_READ_ONLY_TOOLS)

    def test_the_closed_list_is_read_only_and_low_risk(self) -> None:
        for name in _READ_ONLY_TOOLS:
            schema = ToolRegistry.get(name).schema
            assert schema.read_only is True
            assert schema.risk == "low"

    def test_a_retrieval_route_needs_no_tools(self) -> None:
        assert self._decide("clarify").suggested_tools == []

    def test_a_slow_model_cannot_hold_the_turn(self) -> None:
        release = threading.Event()

        def stuck(_prompt: str) -> str:
            release.wait(2.0)
            return '{"route": "tools"}'

        base = RouteDecision(route=AgentRoute.RAG, reason="default", confidence=0.6)
        started = time.perf_counter()
        with mock.patch("app.agents.tiebreak.CLASSIFIER_TIMEOUT_S", 0.05):
            decision = refine("another question", base, classifier=stuck)
        release.set()
        assert time.perf_counter() - started < 1.0
        assert decision is base
