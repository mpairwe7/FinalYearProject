"""Unit and integration tests for Procedural Memory, Handoff Envelopes, and Agent Memory Tools."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.agents.graphs.compactor import analyze_context_window
from app.agents.graphs.state import AgentGraphState
from app.memory import (
    EvidenceCard,
    HandoffEnvelope,
    HandoffGoal,
    MemoryService,
    ProceduralMemory,
    ProceduralSkill,
    RecommendedAction,
    TrajectoryExemplar,
    get_memory_service,
)
from app.tools import ToolRegistry


class TestProceduralMemory(unittest.TestCase):
    def setUp(self) -> None:
        self.proc = ProceduralMemory()

    def test_default_skills_registered(self) -> None:
        skill = self.proc.get_skill("calc_vat")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "Value Added Tax Calculator")
        self.assertEqual(skill.domain, "domestic_tax")

    def test_register_and_list_custom_skill(self) -> None:
        custom_skill = ProceduralSkill(
            skill_id="custom_dts_check",
            name="DTS Stamp Verification",
            description="Verify digital tax stamp authenticity",
            domain="customs",
        )
        self.proc.register_skill(custom_skill)
        retrieved = self.proc.get_skill("custom_dts_check")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.domain, "customs")

        customs_skills = self.proc.list_skills(domain="customs")
        self.assertTrue(any(s.skill_id == "custom_dts_check" for s in customs_skills))

    def test_find_default_exemplar(self) -> None:
        ex = self.proc.find_exemplar("calculate_paye")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.intent, "calculate_paye")
        self.assertIn("calculate_paye", ex.tool_sequence)
        d = ex.to_dict()
        self.assertEqual(d["exemplar_id"], "ex-paye-calc-01")


class TestHandoffEnvelope(unittest.TestCase):
    def test_envelope_serialization_round_trip(self) -> None:
        envelope = HandoffEnvelope(
            thread_id="th-99",
            tenant_id="default",
            goal=HandoffGoal(primary_objective="VAT Objection", target_resolution="Register formal objection"),
            taxpayer_tin="1000123456",
            verified_evidence=[
                EvidenceCard(source="VAT Act", fact="Standard rate 18%", citation_index=1, confidence=1.0)
            ],
            unverified_assumptions=["Taxpayer is resident"],
            blockers_and_risks=["45-day deadline"],
            next_action=RecommendedAction(action_type="confirm_objection", suggested_message="Please verify details"),
        )
        d = envelope.to_dict()
        reconstituted = HandoffEnvelope.from_dict(d)
        self.assertEqual(reconstituted.thread_id, "th-99")
        self.assertEqual(reconstituted.taxpayer_tin, "1000123456")
        self.assertEqual(len(reconstituted.verified_evidence), 1)
        self.assertEqual(reconstituted.verified_evidence[0].fact, "Standard rate 18%")
        self.assertEqual(reconstituted.goal.primary_objective, "VAT Objection")

    def test_service_create_handoff_envelope(self) -> None:
        memsvc = get_memory_service()
        env = memsvc.create_handoff_envelope(
            thread_id="th-svc-1",
            user_id="1000999999",
            objective="TIN Appeal",
            target_resolution="Resolve suspended TIN",
            evidence=[EvidenceCard(source="TIN Register", fact="Status is suspended")],
        )
        self.assertEqual(env.thread_id, "th-svc-1")
        self.assertEqual(env.taxpayer_tin, "1000999999")
        self.assertEqual(env.goal.primary_objective, "TIN Appeal")
        self.assertEqual(len(env.verified_evidence), 1)


class TestAgentMemoryTools(unittest.TestCase):
    def test_tools_registered_in_registry(self) -> None:
        self.assertIsNotNone(ToolRegistry.get("memory_recall"))
        self.assertIsNotNone(ToolRegistry.get("memory_store"))
        self.assertIsNotNone(ToolRegistry.get("memory_discard"))

    def test_memory_recall_procedural_tier(self) -> None:
        recall = ToolRegistry.get("memory_recall")
        res = recall.execute(query="calculate_vat", memory_type="procedural")
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["count"], 1)

    def test_memory_store_rejects_low_confidence(self) -> None:
        store_tool = ToolRegistry.get("memory_store")
        res = store_tool.execute(
            key="annual_turnover",
            value="80000000",
            confidence=0.50,  # below 0.70 threshold
            user_id="user_123",
        )
        self.assertFalse(res["ok"])
        self.assertIn("minimum threshold", res["error"])

    def test_memory_store_and_recall_semantic(self) -> None:
        store_tool = ToolRegistry.get("memory_store")
        recall_tool = ToolRegistry.get("memory_recall")

        with patch("app.database.has_active_consent", return_value=True), \
             patch("app.database.execute", return_value=1):
            store_res = store_tool.execute(
                key="filing_regime",
                value="presumptive",
                confidence=0.95,
                source_citation="user_statement",
                user_id="user_123",
            )
            self.assertTrue(store_res["ok"])
            self.assertEqual(store_res["status"], "stored")


class TestContextRotAnalytics(unittest.TestCase):
    def test_analyze_context_window_low_risk(self) -> None:
        state = AgentGraphState(
            query="What is the VAT rate?",
            context_summary="Taxpayer is resident.",
            conversation_history=[{"user_message": "hello", "bot_reply": "hi"}],
        )
        analysis = analyze_context_window(state)
        self.assertIn("estimated_tokens", analysis)
        self.assertIn("utilization_pct", analysis)
        self.assertEqual(analysis["rot_risk"], "low")

    def test_analyze_context_window_high_risk_on_heavy_payload(self) -> None:
        state = AgentGraphState(
            query="What is the VAT rate?",
            observations=[{"ok": True, "data": "x" * 15000}],  # heavy observation
        )
        analysis = analyze_context_window(state)
        self.assertIn(analysis["rot_risk"], ("medium", "high"))


if __name__ == "__main__":
    unittest.main()
