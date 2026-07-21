"""Tests for Phase 15 Lite — LangGraph-style graph runtime + main graph."""

from __future__ import annotations

import pytest

from app.agents.graphs import (
    AgentGraphState,
    GraphNode,
    GraphOutcome,
    GraphRuntime,
    NodeResult,
)
from app.agents.graphs.runtime import END
from app.agents.graphs.main_graph import build_main_graph


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
