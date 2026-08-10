from __future__ import annotations

import unittest
from pathlib import Path

from app.service import ChatModel
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry


FLOW_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "workflows" / "flows" / "tin_registration.yaml"
)


class WorkflowGuideTests(unittest.TestCase):
    def setUp(self) -> None:
        WorkflowRegistry._workflows.clear()
        WorkflowRegistry.register(load_workflow(FLOW_PATH))

    def test_tin_workflow_reaches_terminal_summary(self) -> None:
        session = WorkflowRegistry.create_session("tin_registration")
        assert session is not None

        first = WorkflowRegistry.advance(session, "")
        self.assertIn("individual", first.question.lower())
        self.assertEqual(first.slot_name, "taxpayer_type")

        WorkflowRegistry.advance(session, "individual")
        WorkflowRegistry.advance(session, "Jane Taxpayer")
        WorkflowRegistry.advance(session, "CM84ABCDE8400J")
        WorkflowRegistry.advance(session, "0771234567")
        WorkflowRegistry.advance(session, "jane@example.com")
        final_turn = WorkflowRegistry.advance(session, "yes")

        self.assertTrue(final_turn.is_complete)
        self.assertIn("ura web portal", final_turn.question.lower())
        self.assertTrue(session.completed)


class HandoffPacketTests(unittest.TestCase):
    def test_account_specific_handoff_is_high_priority(self) -> None:
        model = ChatModel.__new__(ChatModel)
        packet = model._build_handoff_packet(
            message="My TIN balance is wrong",
            reason="Account-specific query — needs authenticated lookup or human",
            conversation_history=[
                {
                    "user_message": "My TIN balance is wrong and I need help",
                    "bot_reply": "",
                }
            ],
            hits=[{"source": "ura_guide.pdf"}],
            faithfulness_score=0.18,
        )

        self.assertEqual(packet["topic"], "account_specific")
        self.assertEqual(packet["priority"], "high")
        self.assertIn("TIN or registered taxpayer email", packet["required_details"])
        self.assertIn("ura_guide.pdf", packet["sources_reviewed"])


if __name__ == "__main__":
    unittest.main()
