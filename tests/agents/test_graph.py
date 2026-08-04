"""Tests for Phase 15 Lite — LangGraph-style graph runtime + main graph."""

from __future__ import annotations

import pytest
from app.agents.graphs import (
    AgentGraphState,
    GraphOutcome,
    GraphRuntime,
    NodeResult,
)
from app.agents.graphs.main_graph import (
    bind_arguments,
    build_main_graph,
    node_act,
    node_reflect,
    node_synthesize,
)
from app.agents.graphs.runtime import END
from app.agents.loop_control import ToolCallBudget


# ---------------------------------------------------------------------------
# AgentGraphState
# ---------------------------------------------------------------------------
class TestAgentGraphState:
    def test_defaults(self):
        s = AgentGraphState()
        assert s.query == ""
        assert s.outcome == GraphOutcome.ANSWERED
        assert s.iterations == 0
        assert s.max_iterations == 3
        assert s.tenant_id == "default"
        assert s.granted_purposes == []

    def test_record_appends_trace(self):
        s = AgentGraphState()
        s.record("node_a", 42.5, extra="info")
        assert len(s.trace) == 1
        assert s.trace[0]["node"] == "node_a"
        assert s.trace[0]["duration_ms"] == 42.5
        assert s.trace[0]["extra"] == "info"

    def test_summary_compact(self):
        s = AgentGraphState(
            query="hello world",
            user_id="alice",
            plan=["search_ura_knowledge_base"],
            outcome=GraphOutcome.ANSWERED,
        )
        summary = s.to_summary()
        assert summary["query_len"] == 11
        assert summary["user_id"] == "alice"
        assert summary["plan_steps"] == 1
        assert summary["outcome"] == "answered"


# ---------------------------------------------------------------------------
# GraphRuntime dispatch
# ---------------------------------------------------------------------------
class TestGraphRuntime:
    def test_simple_two_node_chain(self):
        def step_a(s: AgentGraphState) -> NodeResult:
            s.plan.append("a-ran")
            return NodeResult(next_node="step_b")

        def step_b(s: AgentGraphState) -> NodeResult:
            s.plan.append("b-ran")
            return NodeResult(next_node=END, outcome=GraphOutcome.ANSWERED)

        runtime = GraphRuntime(
            nodes={"step_a": step_a, "step_b": step_b},
            entry="step_a",
        )
        s = AgentGraphState(query="test")
        final = runtime.run(s)
        assert final.plan == ["a-ran", "b-ran"]
        assert final.outcome == GraphOutcome.ANSWERED
        assert len(final.trace) == 2

    def test_max_steps_guard(self):
        """A graph that never terminates is bounded by max_steps."""
        def looping(s: AgentGraphState) -> NodeResult:
            return NodeResult(next_node="looping")

        runtime = GraphRuntime(
            nodes={"looping": looping},
            entry="looping",
            max_steps=5,
        )
        s = AgentGraphState()
        final = runtime.run(s)
        assert final.outcome == GraphOutcome.TRUNCATED

    def test_unknown_next_node_terminates_errored(self):
        def bad(s: AgentGraphState) -> NodeResult:
            return NodeResult(next_node="does_not_exist")

        runtime = GraphRuntime(nodes={"bad": bad}, entry="bad")
        final = runtime.run(AgentGraphState())
        assert final.outcome == GraphOutcome.ERRORED
        assert "unknown node" in final.error

    def test_node_exception_terminates_errored(self):
        def exploding(s: AgentGraphState) -> NodeResult:
            raise RuntimeError("boom")

        runtime = GraphRuntime(nodes={"exploding": exploding}, entry="exploding")
        final = runtime.run(AgentGraphState())
        assert final.outcome == GraphOutcome.ERRORED
        assert "boom" in final.error

    def test_entry_node_must_exist(self):
        with pytest.raises(ValueError, match="entry node"):
            GraphRuntime(nodes={"step_a": lambda s: NodeResult(END)}, entry="does_not_exist")

    def test_outcome_override_from_node(self):
        def deciding(s: AgentGraphState) -> NodeResult:
            return NodeResult(next_node=END, outcome=GraphOutcome.CLARIFY)

        runtime = GraphRuntime(nodes={"deciding": deciding}, entry="deciding")
        final = runtime.run(AgentGraphState())
        assert final.outcome == GraphOutcome.CLARIFY

    def test_trace_records_duration(self):
        def fast(s: AgentGraphState) -> NodeResult:
            return NodeResult(next_node=END, outcome=GraphOutcome.ANSWERED)

        runtime = GraphRuntime(nodes={"fast": fast}, entry="fast")
        final = runtime.run(AgentGraphState())
        assert len(final.trace) == 1
        assert "duration_ms" in final.trace[0]
        assert final.trace[0]["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Main graph (integration)
# ---------------------------------------------------------------------------
class TestMainGraph:
    def test_clarify_for_stop_word(self, fresh_registry):
        graph = build_main_graph()
        state = AgentGraphState(query="help", rewritten_query="help")
        final = graph.run(state)
        assert final.outcome == GraphOutcome.CLARIFY
        assert final.clarification_question

    def test_escalate_for_dispute_query(self, fresh_registry):
        graph = build_main_graph()
        state = AgentGraphState(
            query="I want to dispute my assessment",
            rewritten_query="I want to dispute my assessment",
        )
        final = graph.run(state)
        assert final.outcome == GraphOutcome.ESCALATED
        assert final.escalation_reason

    def test_escalate_for_human_request(self, fresh_registry):
        graph = build_main_graph()
        state = AgentGraphState(
            query="I want to speak to a human officer",
            rewritten_query="I want to speak to a human officer",
        )
        final = graph.run(state)
        assert final.outcome == GraphOutcome.ESCALATED

    def test_trace_captures_visited_nodes(self, fresh_registry):
        graph = build_main_graph()
        state = AgentGraphState(query="hello", rewritten_query="hello")
        final = graph.run(state)
        nodes = [t["node"] for t in final.trace]
        assert "route" in nodes
        assert "respond" in nodes


# ---------------------------------------------------------------------------
# Argument binding — node_act must not invent values it does not have
# ---------------------------------------------------------------------------
class TestBindArguments:
    def test_a_tool_with_no_required_parameters_is_callable(self, fresh_registry):
        state = AgentGraphState(query="what is today's date")
        assert bind_arguments("get_current_date", state) == {}

    def test_a_required_free_text_parameter_takes_the_query(self, fresh_registry):
        state = AgentGraphState(query="raw", rewritten_query="what is the VAT rate")
        assert bind_arguments("search_ura_knowledge_base", state) == {
            "query": "what is the VAT rate"
        }

    def test_a_required_value_the_graph_cannot_know_is_not_invented(self, fresh_registry):
        # calculate_vat requires `amount`.  Dispatching with {} fails
        # schema validation, and node_synthesize would then hand the
        # user "amount: required property is missing" as an answer.
        state = AgentGraphState(query="how much VAT do I pay")
        assert bind_arguments("calculate_vat", state) is None
        assert bind_arguments("lookup_rate", state) is None

    def test_an_unknown_tool_binds_to_nothing(self, fresh_registry):
        assert bind_arguments("no_such_tool", AgentGraphState(query="x")) is None


class TestNodeAct:
    def test_unfillable_tools_are_skipped_with_a_reason(self, fresh_registry):
        state = AgentGraphState(
            query="how much VAT",
            plan=["calculate_vat", "get_current_date"],
        )
        node_act(state)
        skipped = {s["name"] for s in state.skipped_tools}
        assert "calculate_vat" in skipped
        assert all(s["reason"] for s in state.skipped_tools)
        assert [c["name"] for c in state.tool_calls] == ["get_current_date"]

    def test_no_failed_observation_reaches_synthesis(self, fresh_registry):
        state = AgentGraphState(query="how much VAT", plan=["calculate_vat"])
        node_act(state)
        assert state.observations == []

    def test_the_turn_budget_caps_a_long_plan(self, fresh_registry):
        state = AgentGraphState(query="what is today's date", plan=["get_current_date"] * 6)
        state.budget = ToolCallBudget(max_calls_per_iteration=2, max_calls_per_tool=99)
        node_act(state)
        assert len(state.tool_calls) <= 2

    def test_a_repeated_call_is_not_dispatched_twice(self, fresh_registry):
        state = AgentGraphState(query="what is today's date", plan=["get_current_date"] * 3)
        node_act(state)
        assert len(state.tool_calls) == 1
        assert state.budget.stats()["repeats"] == 2


# ---------------------------------------------------------------------------
# Synthesis and reflection
# ---------------------------------------------------------------------------
class TestNodeSynthesize:
    def test_a_failed_tool_result_is_not_an_answer(self):
        state = AgentGraphState(query="how much VAT")
        state.observations = [{"ok": False, "error": "amount: required property is missing"}]
        result = node_synthesize(state)
        assert state.outcome == GraphOutcome.ABSTAINED
        assert "required property" not in state.reply
        assert result.next_node == "respond"

    def test_a_result_without_prose_abstains_rather_than_dumping_a_dict(self):
        state = AgentGraphState(query="x")
        state.observations = [{"ok": True, "rate": 0.18}]
        node_synthesize(state)
        assert state.outcome == GraphOutcome.ABSTAINED
        assert "{" not in state.reply

    def test_a_successful_tool_result_is_used(self):
        state = AgentGraphState(query="x")
        state.observations = [
            {"ok": False, "error": "boom"},
            {"ok": True, "explanation": "VAT on UGX 100,000 is UGX 18,000."},
        ]
        node_synthesize(state)
        assert state.reply == "VAT on UGX 100,000 is UGX 18,000."
        assert state.outcome != GraphOutcome.ABSTAINED


class TestNodeReflect:
    def test_weak_grounding_sends_the_rag_path_back_to_retrieval(self, monkeypatch):
        state = AgentGraphState(query="vat rate", rewritten_query="vat rate")
        state.hits = [{"text": "unrelated passage"}]
        state.reply = "The VAT rate is 18 percent."
        monkeypatch.setattr(
            "app.retriever.HybridRetriever.compute_faithfulness",
            staticmethod(lambda reply, contexts: 0.1),
        )
        result = node_reflect(state)
        assert result.next_node == "retrieve"
        assert state.reflections

    def test_reflection_is_bounded(self, monkeypatch):
        state = AgentGraphState(query="vat rate", rewritten_query="vat rate")
        state.hits = [{"text": "unrelated passage"}]
        state.reply = "The VAT rate is 18 percent."
        state.reflect_count = state.max_reflections
        monkeypatch.setattr(
            "app.retriever.HybridRetriever.compute_faithfulness",
            staticmethod(lambda reply, contexts: 0.1),
        )
        assert node_reflect(state).next_node == "respond"

    def test_a_well_grounded_reply_is_not_re_retrieved(self, monkeypatch):
        state = AgentGraphState(query="vat rate", rewritten_query="vat rate")
        state.hits = [{"text": "The VAT rate is 18 percent."}]
        state.reply = "The VAT rate is 18 percent."
        monkeypatch.setattr(
            "app.retriever.HybridRetriever.compute_faithfulness",
            staticmethod(lambda reply, contexts: 0.95),
        )
        assert node_reflect(state).next_node == "respond"

    def test_a_tool_only_answer_does_not_re_retrieve(self):
        # No hits means no faithfulness signal — there is nothing to
        # re-retrieve against, and looping would be pure cost.
        state = AgentGraphState(query="vat on 100000")
        state.reply = "VAT is UGX 18,000."
        assert node_reflect(state).next_node == "respond"
