"""The graph is one way to draft an answer, not a way around checking it.

With ``langgraph`` on, a reply left through ``_finalize_reply`` alone: no claim
verification, no judge, no contradiction withholding, ``response_judge: None``, and
a ticket with no handoff. Separately, the runtime's checkpoint store had no retention
and no erasure, a resume was not bound to the taxpayer who owned the thread, and a
corrupt checkpoint read back as "answered".
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest import mock

import pytest
from app import service as service_module
from app.agents.graphs.main_graph import build_main_graph, node_human_gate
from app.agents.graphs.runtime import END, GraphRuntime, InMemoryCheckpointStore, NodeResult
from app.agents.graphs.state import AgentGraphState, GraphOutcome, TaxpayerActionRequest
from app.flags import flags
from app.providers.routing import ModelTier, TierDecision
from app.resilience import turn_deadline
from app.service import ChatModel

HIT = {
    "text": "The standard VAT rate in Uganda is 18 percent.",
    "answer": "The standard VAT rate in Uganda is 18 percent.",
    "source": "vat.pdf",
    "score_rerank": 0.9,
}
CITATION = {"marker": "1", "ref": "[1]", "source": "vat.pdf", "passage": HIT["text"], "text": HIT["text"]}
CONTRADICTED = {
    "decision": "escalate",
    "score": 0.1,
    "claim_count": 1,
    "contradicted_claims": [{"text": "The VAT rate is 25%."}],
    "unsupported_claims": [],
    "uncited_claims": [],
}


class FakeGraph:
    def __init__(self, final: AgentGraphState) -> None:
        self.final = final

    def run(self, state: AgentGraphState, thread_id: str = "") -> AgentGraphState:
        state.tool_calls = self.final.tool_calls
        return self.final


def _final(reply: str = "The VAT rate is 25% [1].", **overrides: Any) -> AgentGraphState:
    state = AgentGraphState(
        query="What is the VAT rate?",
        reply=reply,
        outcome=GraphOutcome.ANSWERED,
        hits=[HIT],
        citations=[CITATION],
        sources=["vat.pdf"],
        retrieval_mode="hybrid",
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


@pytest.fixture(scope="module")
def chat_model() -> ChatModel:
    return ChatModel()


@pytest.fixture
def on_graph(clean_flags, tmp_db, monkeypatch):
    flags.set("langgraph", True)
    flags.set("agentic_mode", True)
    flags.set("ticket_queue", True)

    def install(final: AgentGraphState) -> None:
        monkeypatch.setattr("app.agents.graphs.main_graph.build_main_graph", lambda: FakeGraph(final))

    return install


class TestAGraphAnswerIsCheckedLikeAnyOther:
    def test_a_contradicted_figure_is_withheld(self, chat_model, on_graph) -> None:
        on_graph(_final())
        with mock.patch.object(service_module, "verify_claims", return_value=CONTRADICTED):
            result = chat_model.generate("What is the VAT rate?", session_id=uuid.uuid4().hex)
        assert "25%" not in result["reply"]
        assert result["escalation_required"] is True
        assert result["response_judge"]["withheld_contradicted"] is True

    def test_the_judge_ran(self, chat_model, on_graph) -> None:
        on_graph(_final("The standard VAT rate in Uganda is 18 percent [1]."))
        result = chat_model.generate("What is the VAT rate?", session_id=uuid.uuid4().hex)
        assert result["response_judge"] is not None

    def test_the_audit_row_says_how_the_figure_lock_went(self, chat_model, on_graph) -> None:
        on_graph(_final("The standard VAT rate in Uganda is 18 percent [1]."))
        result = chat_model.generate("What is the VAT rate?", session_id=uuid.uuid4().hex)
        assert result["_figure_lock"]["mode"] in ("shadow", "enforced")

    def test_the_turn_attests_with_a_real_guard(self, chat_model, on_graph) -> None:
        on_graph(_final("The standard VAT rate in Uganda is 18 percent [1]."))
        result = chat_model.generate("What is the VAT rate?", session_id=uuid.uuid4().hex)
        attestation = result.get("_attestation") or {}
        assert attestation.get("guard_basis") == "verified"
        assert attestation.get("monotonic_stage_order") is True

    def test_an_escalation_keeps_its_tenant(self, chat_model, on_graph, tmp_db) -> None:
        on_graph(_final("A person will help.", outcome=GraphOutcome.ESCALATED, escalation_reason="dispute"))
        result = chat_model.generate(
            "What is the VAT rate?", session_id=uuid.uuid4().hex, tenant_id="tenant-a", user_id="u1"
        )
        assert tmp_db.get_ticket(result["ticket_id"])["tenant_id"] == "tenant-a"

    def test_a_clarification_is_not_judged_as_a_claim(self, chat_model, on_graph) -> None:
        on_graph(_final("Which tax do you mean?", outcome=GraphOutcome.CLARIFY, hits=[], citations=[]))
        with mock.patch.object(service_module, "verify_claims") as verify:
            result = chat_model.generate("that one", session_id=uuid.uuid4().hex)
        verify.assert_not_called()
        assert result["reply"] == "Which tax do you mean?"

    def test_a_tool_answer_is_not_failed_for_having_no_passages(self, chat_model, on_graph) -> None:
        on_graph(
            _final(
                "Today is Monday, 2 November 2026.",
                hits=[],
                citations=[],
                tool_calls=[{"name": "get_current_date", "ok": True, "result": {"ok": True}}],
            )
        )
        result = chat_model.generate("What is the date today?", session_id=uuid.uuid4().hex)
        assert result["reply"].startswith("Today is Monday")
        assert result["escalation_required"] is False

    def test_a_streamed_clarification_does_not_fail_attestation(self, chat_model, on_graph) -> None:
        # The alert on this pages someone: canned text has nothing to verify.
        from app.turn_pipeline import attest_turn

        on_graph(_final("Which tax do you mean?", outcome=GraphOutcome.CLARIFY, hits=[], citations=[]))
        stages = {"input_guard": 1.0}
        token = service_module._TURN_STAGES.set(stages)
        try:
            chat_model.generate_retrieval_only("that one", session_id=uuid.uuid4().hex)
        finally:
            service_module._TURN_STAGES.reset(token)
        assert attest_turn(stages, transport="sse")["ok"] is True

    def test_the_stream_gets_the_same_checks(self, chat_model, on_graph) -> None:
        on_graph(_final())
        with mock.patch.object(service_module, "verify_claims", return_value=CONTRADICTED):
            result = chat_model.generate_retrieval_only("What is the VAT rate?", session_id=uuid.uuid4().hex)
        assert "25%" not in result["reply"]
        assert result["_short_circuit"] is True
        assert result["response_judge"]["withheld_contradicted"] is True


def _node(name: str, then: str = END, *, outcome: GraphOutcome | None = None, seen: list[str] | None = None):
    def run(state: AgentGraphState) -> NodeResult:
        if seen is not None:
            seen.append(name)
        return NodeResult(next_node=then, outcome=outcome)

    return run


class TestAReasoningTurnOnTheGraph:
    """A turn's tier reaches the guards, or the figure lock depends on which path ran it."""

    @pytest.fixture
    def tier(self, monkeypatch):
        def choose(tier: ModelTier) -> None:
            monkeypatch.setattr(service_module, "select_tier", lambda *_a, **_k: TierDecision(tier=tier, reason="test"))

        return choose

    @pytest.mark.parametrize("transport", ["generate", "generate_retrieval_only"])
    def test_an_invented_figure_is_stripped(self, chat_model, on_graph, tier, transport) -> None:
        tier(ModelTier.T3)
        on_graph(_final("The penalty is UGX 7,777,777 [1]."))
        with mock.patch.object(service_module, "verify_claims", return_value={"decision": "approve"}):
            result = getattr(chat_model, transport)("What is the VAT rate?", session_id=uuid.uuid4().hex)
        assert "7,777,777" not in result["reply"]
        assert result["_figure_lock"]["mode"] == "enforced"

    @pytest.mark.parametrize("transport", ["generate", "generate_retrieval_only"])
    def test_below_that_tier_it_only_measures(self, chat_model, on_graph, tier, transport) -> None:
        tier(ModelTier.T1)
        on_graph(_final("The penalty is UGX 7,777,777 [1]."))
        with mock.patch.object(service_module, "verify_claims", return_value={"decision": "approve"}):
            result = getattr(chat_model, transport)("What is the VAT rate?", session_id=uuid.uuid4().hex)
        assert "7,777,777" in result["reply"]
        assert result["_figure_lock"]["mode"] == "shadow"


class TestTheGraphHasADeadline:
    def test_it_stops_between_nodes_when_the_turn_is_out_of_time(self) -> None:
        seen: list[str] = []

        def slow(state: AgentGraphState) -> NodeResult:
            seen.append("slow")
            time.sleep(0.03)
            return NodeResult(next_node="next")

        graph = GraphRuntime({"slow": slow, "next": _node("next", seen=seen)}, entry="slow")
        with turn_deadline(0.01):
            state = graph.run(AgentGraphState())
        assert seen == ["slow"]
        assert state.outcome is GraphOutcome.TRUNCATED

    @pytest.mark.parametrize("method", ["run", "arun"])
    def test_both_loops_behave_alike(self, method) -> None:
        import asyncio

        def broken(state: AgentGraphState) -> NodeResult:
            raise ValueError("node failed")

        graph = GraphRuntime({"broken": broken}, entry="broken")
        outcome = getattr(graph, method)(AgentGraphState())
        state = asyncio.run(outcome) if method == "arun" else outcome
        assert state.outcome is GraphOutcome.ERRORED
        assert state.trace[-1]["status"] == "error"


class TestASuspendedThreadBelongsToItsOwner:
    def _suspended(self, user_id: str = "taxpayer-1", tenant_id: str = "tenant-a") -> tuple[GraphRuntime, list[str]]:
        store = InMemoryCheckpointStore()
        seen: list[str] = []
        graph = GraphRuntime(
            {
                "human_gate": node_human_gate,
                "act": _node("act", "respond", seen=seen),
                "respond": _node("respond", seen=seen),
            },
            entry="human_gate",
            checkpointer=store,
        )
        state = AgentGraphState(
            user_id=user_id,
            tenant_id=tenant_id,
            pending_action=TaxpayerActionRequest(action_type="file", description="file a return"),
        )
        graph.run(state, thread_id="t1")
        assert state.outcome is GraphOutcome.WAITING_USER
        return graph, seen

    def test_the_owner_can_approve(self) -> None:
        graph, seen = self._suspended()
        graph.resume("t1", {"approved": True}, expected_user_id="taxpayer-1", expected_tenant_id="tenant-a")
        assert "act" in seen

    def test_someone_else_cannot(self) -> None:
        graph, seen = self._suspended()
        with pytest.raises(PermissionError):
            graph.resume("t1", {"approved": True}, expected_user_id="someone-else", expected_tenant_id="tenant-a")
        assert seen == []

    def test_a_caller_naming_nobody_cannot(self) -> None:
        graph, seen = self._suspended()
        with pytest.raises(PermissionError):
            graph.resume("t1", {"approved": True})
        assert seen == []

    def test_another_tenant_cannot(self) -> None:
        graph, seen = self._suspended()
        with pytest.raises(PermissionError):
            graph.resume("t1", {"approved": True}, expected_user_id="taxpayer-1", expected_tenant_id="tenant-b")
        assert seen == []

    def test_naming_a_start_node_does_not_skip_the_approval(self) -> None:
        graph, seen = self._suspended()
        with pytest.raises(PermissionError):
            graph.resume(
                "t1", {"approved": False}, start_node="act",
                expected_user_id="taxpayer-1", expected_tenant_id="tenant-a",
            )
        assert "act" not in seen

    def test_the_async_path_is_bound_too(self) -> None:
        import asyncio

        graph, seen = self._suspended()
        with pytest.raises(PermissionError):
            asyncio.run(graph.aresume("t1", {"approved": True}, expected_user_id="someone-else"))
        assert seen == []


class TestCheckpointsAreSafeToKeep:
    def test_a_corrupt_outcome_does_not_read_back_as_answered(self) -> None:
        state = AgentGraphState.from_dict({"outcome": "not-a-real-outcome"})
        assert state.outcome is GraphOutcome.ERRORED

    def test_a_resume_cannot_refill_the_budget(self) -> None:
        state = AgentGraphState()
        for number in range(3):
            state.budget.admit("calculate_vat", {"n": number})
            state.budget.record("calculate_vat", {"n": number}, {"ok": True})
        restored = AgentGraphState.from_dict(state.to_dict())
        assert restored.budget.dispatched == 3
        assert restored.budget.admit("calculate_vat", {"n": 9}).should_dispatch is False

    def test_a_failed_save_at_a_suspend_is_not_a_suspend(self) -> None:
        class Down(InMemoryCheckpointStore):
            def save_checkpoint(self, *_args: Any, **_kwargs: Any) -> None:
                raise ConnectionError("store unreachable")

        graph = GraphRuntime(
            {"human_gate": node_human_gate, "act": _node("act")}, entry="human_gate", checkpointer=Down()
        )
        state = AgentGraphState(pending_action=TaxpayerActionRequest(action_type="file", description="x"))
        graph.run(state, thread_id="t1")
        assert state.outcome is GraphOutcome.ERRORED, "no checkpoint means nothing to resume"

    def test_a_failed_save_elsewhere_costs_only_the_checkpoint(self) -> None:
        class Down(InMemoryCheckpointStore):
            def save_checkpoint(self, *_args: Any, **_kwargs: Any) -> None:
                raise ConnectionError("store unreachable")

        graph = GraphRuntime({"a": _node("a")}, entry="a", checkpointer=Down())
        assert graph.run(AgentGraphState(), thread_id="t1").outcome is GraphOutcome.ANSWERED

    def test_a_durable_store_reports_a_failed_save(self, monkeypatch) -> None:
        from app.agents.graphs.runtime import DurableCheckpointStore

        def down(*_args: Any, **_kwargs: Any) -> None:
            raise ConnectionError("database unreachable")

        monkeypatch.setattr("app.database.save_agent_checkpoint", down)
        with pytest.raises(ConnectionError):
            DurableCheckpointStore().save_checkpoint("t", 0, "n", {})

    def test_old_checkpoints_expire(self, tmp_db) -> None:
        tmp_db.save_agent_checkpoint("old-thread", 0, "act", {"query": "personal"})
        tmp_db.save_agent_checkpoint("new-thread", 0, "act", {"query": "personal"})
        tmp_db._get_connection().execute(
            "UPDATE agent_checkpoints SET created_at = 1 WHERE thread_id = 'old-thread'"
        )
        tmp_db.cleanup_expired_data()
        assert tmp_db.get_agent_checkpoint("old-thread") is None
        assert tmp_db.get_agent_checkpoint("new-thread") is not None

    def test_erasing_a_taxpayer_erases_their_checkpoints(self, tmp_db) -> None:
        user = tmp_db.upsert_user(external_id="erase-me", tenant_id="default")
        tmp_db.log_conversation(
            session_id="s", conversation_id="conv-erase", user_message="q", bot_reply="a",
            user_id="erase-me", tenant_id="default",
        )
        tmp_db.save_agent_checkpoint("conv-erase", 0, "act", {"query": "personal"})
        tmp_db.delete_user_cascade(user["id"], external_id="erase-me", tenant_id="default")
        assert tmp_db.get_agent_checkpoint("conv-erase") is None


def test_the_factory_still_builds() -> None:
    assert build_main_graph(checkpointer=InMemoryCheckpointStore()) is not None
