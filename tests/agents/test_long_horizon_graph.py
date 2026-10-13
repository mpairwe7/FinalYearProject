"""Comprehensive unit & integration tests for long-horizon agent state, compactor, HITL gate, and resumption."""
from __future__ import annotations

import unittest
import pytest
from app.agents.graphs.compactor import (
    compact_messages,
    compact_tool_observation,
    node_context_compactor,
)
from app.agents.graphs.main_graph import build_main_graph, node_human_gate
from app.agents.graphs.runtime import (
    END,
    DurableCheckpointStore,
    GraphRuntime,
    InMemoryCheckpointStore,
    NodeResult,
)
from app.agents.graphs.state import (
    AgentGraphState,
    GoalSpec,
    GraphOutcome,
    TaxpayerActionRequest,
)


class TestContextCompaction(unittest.TestCase):
    def test_compact_tool_observation_preserves_statutory_fields(self):
        obs = {
            "ok": True,
            "tax_type": "VAT",
            "payable": 1800000,
            "formatted": "UGX 1,800,000",
            "fiscal_year": "2026/27",
            "excess_bloat": ["item_" + str(i) for i in range(100)],
            "debug_headers": {"h_" + str(i): "v" for i in range(50)},
        }
        compacted = compact_tool_observation(obs)
        self.assertTrue(compacted["ok"])
        self.assertEqual(compacted["tax_type"], "VAT")
        self.assertEqual(compacted["payable"], 1800000)
        self.assertEqual(compacted["formatted"], "UGX 1,800,000")
        self.assertEqual(compacted["fiscal_year"], "2026/27")
        # Ensure array bloat was sliced to top-3
        self.assertEqual(len(compacted["excess_bloat"]), 3)
        self.assertLessEqual(len(compacted["debug_headers"]), 5)

    def test_compact_tool_observation_preserves_errors(self):
        obs = {"ok": False, "error": "Invalid TIN format provided by user"}
        compacted = compact_tool_observation(obs)
        self.assertFalse(compacted["ok"])
        self.assertIn("Invalid TIN format", compacted["error"])

    def test_compact_tool_observation_non_dict(self):
        compacted = compact_tool_observation("plain string output")
        self.assertTrue(compacted["ok"])
        self.assertEqual(compacted["data"], "plain string output")

    def test_compact_messages_within_verbatim_limit(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        recent, summary = compact_messages(msgs, max_verbatim_turns=4)
        self.assertEqual(len(recent), 2)
        self.assertEqual(summary, "")

    def test_compact_messages_beyond_verbatim_limit(self):
        msgs = []
        for i in range(10):
            msgs.append({"role": "user", "content": f"question {i}"})
            msgs.append({"role": "assistant", "content": f"answer {i}"})
        recent, summary = compact_messages(msgs, max_verbatim_turns=4)
        # Should keep 4 verbatim turns (8 messages)
        self.assertEqual(len(recent), 8)
        self.assertIn("Taxpayer: question 0", summary)
        self.assertIn("Assistant: answer 0", summary)

    def test_node_context_compactor_in_state(self):
        state = AgentGraphState(
            query="test",
            conversation_history=[
                {"role": "user", "content": f"q{i}"} for i in range(20)
            ],
            observations=[
                {"ok": True, "items": list(range(50)), "payable": 5000}
            ],
        )
        res = node_context_compactor(state)
        self.assertEqual(res.next_node, "route")
        self.assertEqual(len(state.observations[0]["items"]), 3)
        self.assertEqual(state.observations[0]["payable"], 5000)
        self.assertLessEqual(len(state.conversation_history), 8)
        self.assertIn("[Earlier:", state.context_summary)


class TestHumanGateAndInterruption(unittest.TestCase):
    def test_human_gate_passthrough_when_no_pending_action(self):
        state = AgentGraphState(query="What is PAYE?")
        res = node_human_gate(state)
        self.assertEqual(res.next_node, "act")
        self.assertIsNone(res.outcome)

    def test_human_gate_interrupts_when_consent_required_and_unauthorized(self):
        action = TaxpayerActionRequest(
            action_type="register_tin",
            description="Submit TIN registration to URA portal",
            payload={"tin": "1000000001"},
            requires_consent=True,
            authorized=False,
        )
        state = AgentGraphState(query="Register me for TIN", pending_action=action)
        res = node_human_gate(state)
        self.assertEqual(res.outcome, GraphOutcome.WAITING_USER)
        self.assertEqual(state.outcome, GraphOutcome.WAITING_USER)
        self.assertIn("Action requires taxpayer authorization", state.reply)

    def test_human_gate_passes_when_action_authorized(self):
        action = TaxpayerActionRequest(
            action_type="register_tin",
            description="Submit TIN registration",
            requires_consent=True,
            authorized=True,
        )
        state = AgentGraphState(query="Register me for TIN", pending_action=action)
        res = node_human_gate(state)
        self.assertEqual(res.next_node, "act")
        self.assertIsNone(res.outcome)


class TestLongHorizonResumption(unittest.TestCase):
    def test_graph_runtime_suspends_and_resumes_with_approval(self):
        store = InMemoryCheckpointStore()
        thread_id = "th-hitl-101"

        action = TaxpayerActionRequest(
            action_type="generate_prn",
            description="Generate Payment Registration Number for UGX 500,000",
            payload={"amount": 500000},
            requires_consent=True,
            authorized=False,
        )

        def mock_human_gate(s: AgentGraphState) -> NodeResult:
            if s.pending_action and s.pending_action.requires_consent and not s.pending_action.authorized:
                s.outcome = GraphOutcome.WAITING_USER
                s.reply = f"Authorization needed: {s.pending_action.description}"
                return NodeResult(next_node="act", outcome=GraphOutcome.WAITING_USER)
            return NodeResult(next_node="act")

        def mock_act(s: AgentGraphState) -> NodeResult:
            s.observations.append({"ok": True, "prn": "PRN-998877"})
            return NodeResult(next_node="respond")

        def mock_respond(s: AgentGraphState) -> NodeResult:
            if s.pending_action and s.pending_action.authorized:
                s.reply = f"PRN generated successfully: {s.observations[0]['prn']}"
                s.outcome = GraphOutcome.ANSWERED
            else:
                s.reply = "Action declined."
                s.outcome = GraphOutcome.ESCALATED
            return NodeResult(next_node=END, outcome=s.outcome)

        runtime = GraphRuntime(
            nodes={
                "human_gate": mock_human_gate,
                "act": mock_act,
                "respond": mock_respond,
            },
            entry="human_gate",
            checkpointer=store,
        )

        initial_state = AgentGraphState(
            query="Generate PRN for 500k",
            pending_action=action,
        )
        paused_state = runtime.run(initial_state, thread_id=thread_id)
        self.assertEqual(paused_state.outcome, GraphOutcome.WAITING_USER)
        self.assertIn("Authorization needed", paused_state.reply)

        # Confirm state was checkpointed
        saved = store.load_checkpoint(thread_id)
        self.assertIsNotNone(saved)
        self.assertFalse(saved["pending_action"]["authorized"])

        # Resume with taxpayer approval payload
        resumed_state = runtime.resume(
            thread_id=thread_id,
            resume_payload={"approved": True},
            start_node="act",
        )
        self.assertEqual(resumed_state.outcome, GraphOutcome.ANSWERED)
        self.assertTrue(resumed_state.pending_action.authorized)
        self.assertIn("PRN generated successfully: PRN-998877", resumed_state.reply)

    def test_graph_runtime_resumes_with_decline(self):
        store = InMemoryCheckpointStore()
        thread_id = "th-hitl-102"

        action = TaxpayerActionRequest(
            action_type="submit_return",
            description="Submit VAT Return",
            requires_consent=True,
            authorized=False,
        )

        def mock_human_gate(s: AgentGraphState) -> NodeResult:
            if s.pending_action and not s.pending_action.authorized:
                s.outcome = GraphOutcome.WAITING_USER
                return NodeResult(next_node="respond", outcome=GraphOutcome.WAITING_USER)
            return NodeResult(next_node="respond")

        def mock_respond(s: AgentGraphState) -> NodeResult:
            s.reply = f"Process stopped: {s.escalation_reason}"
            s.outcome = GraphOutcome.ESCALATED
            return NodeResult(next_node=END, outcome=s.outcome)

        runtime = GraphRuntime(
            nodes={"human_gate": mock_human_gate, "respond": mock_respond},
            entry="human_gate",
            checkpointer=store,
        )

        state = AgentGraphState(query="Submit VAT return", pending_action=action)
        paused = runtime.run(state, thread_id=thread_id)
        self.assertEqual(paused.outcome, GraphOutcome.WAITING_USER)

        # Taxpayer declines
        resumed = runtime.resume(
            thread_id=thread_id,
            resume_payload={"approved": False, "reason": "Taxpayer cancelled filing"},
            start_node="respond",
        )
        self.assertEqual(resumed.outcome, GraphOutcome.ESCALATED)
        self.assertIn("Taxpayer cancelled filing", resumed.reply)


class TestGoalTrackingAndSchemaVersioning(unittest.TestCase):
    def test_goal_spec_serialization(self):
        goal = GoalSpec(
            goal_id="g-100",
            description="Reconcile annual VAT payments",
            status="in_progress",
            created_at_s=1700000000.0,
            sub_tasks=["Fetch invoices", "Compute delta"],
        )
        d = goal.to_dict()
        reconstituted = GoalSpec.from_dict(d)
        self.assertEqual(reconstituted.goal_id, "g-100")
        self.assertEqual(reconstituted.status, "in_progress")
        self.assertEqual(len(reconstituted.sub_tasks), 2)

    def test_schema_v1_backwards_compatibility(self):
        # Schema v1 snapshot without active_goals or pending_action
        v1_data = {
            "query": "What is income tax?",
            "locale": "en",
            "outcome": "answered",
            "reply": "Income tax is a tax imposed on income...",
        }
        state = AgentGraphState.from_dict(v1_data)
        self.assertEqual(state.schema_version, 1)
        self.assertEqual(state.active_goals, {})
        self.assertIsNone(state.pending_action)
        self.assertEqual(state.reply, "Income tax is a tax imposed on income...")

    def test_schema_v2_full_round_trip(self):
        goal = GoalSpec(goal_id="g-1", description="Register TIN", status="completed")
        action = TaxpayerActionRequest(action_type="approve_reg", description="Confirm details", authorized=True)
        state = AgentGraphState(
            schema_version=2,
            query="Register my TIN",
            active_goals={"g-1": goal},
            current_goal_id="g-1",
            pending_action=action,
            replan_count=1,
        )
        d = state.to_dict()
        reconstituted = AgentGraphState.from_dict(d)
        self.assertEqual(reconstituted.schema_version, 2)
        self.assertIn("g-1", reconstituted.active_goals)
        self.assertEqual(reconstituted.active_goals["g-1"].description, "Register TIN")
        self.assertIsNotNone(reconstituted.pending_action)
        self.assertTrue(reconstituted.pending_action.authorized)
        self.assertEqual(reconstituted.replan_count, 1)


def test_async_runtime_interruption_and_aresume():
    import asyncio

    async def _run():
        store = InMemoryCheckpointStore()
        thread_id = "th-async-hitl-200"

        action = TaxpayerActionRequest(
            action_type="customs_clear",
            description="Clear customs consignment #998",
            requires_consent=True,
            authorized=False,
        )

        async def async_gate(s: AgentGraphState) -> NodeResult:
            if s.pending_action and not s.pending_action.authorized:
                s.outcome = GraphOutcome.WAITING_USER
                s.reply = "Customs clearance requires taxpayer confirmation."
                return NodeResult(next_node="act", outcome=GraphOutcome.WAITING_USER)
            return NodeResult(next_node="act")

        async def async_act(s: AgentGraphState) -> NodeResult:
            s.observations.append({"ok": True, "customs_receipt": "REC-7766"})
            s.reply = f"Consignment cleared with receipt {s.observations[0]['customs_receipt']}"
            s.outcome = GraphOutcome.ANSWERED
            return NodeResult(next_node=END, outcome=GraphOutcome.ANSWERED)

        runtime = GraphRuntime(
            nodes={"gate": async_gate, "act": async_act},
            entry="gate",
            checkpointer=store,
        )

        state = AgentGraphState(query="Clear consignment #998", pending_action=action)
        paused = await runtime.arun(state, thread_id=thread_id)
        assert paused.outcome == GraphOutcome.WAITING_USER
        assert "confirmation" in paused.reply

        resumed = await runtime.aresume(
            thread_id=thread_id,
            resume_payload={"approved": True},
            start_node="act",
        )
        assert resumed.outcome == GraphOutcome.ANSWERED
        assert "REC-7766" in resumed.reply

    asyncio.run(_run())
