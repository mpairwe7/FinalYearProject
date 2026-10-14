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

    def test_tin_guide_reaches_portal_summary_without_collecting_identity_data(self) -> None:
        session = WorkflowRegistry.create_session("tin_registration")
        assert session is not None

        first = WorkflowRegistry.advance(session, "")
        self.assertIn("individual", first.question.lower())
        self.assertEqual(first.slot_name, "taxpayer_type")
        self.assertIn("do not share your nin", first.question.lower())

        documents = WorkflowRegistry.advance(session, "individual")
        self.assertEqual(documents.slot_name, "documents_ready")
        self.assertNotIn("nin", documents.question.lower())
        self.assertNotIn("phone number", documents.question.lower())

        final_turn = WorkflowRegistry.advance(session, "yes")

        self.assertTrue(final_turn.is_complete)
        self.assertIn("has not created or submitted a tin application", final_turn.question.lower())
        self.assertIn("https://ura.go.ug", final_turn.question.lower())
        self.assertTrue(session.completed)
        self.assertEqual(set(session.slots), {"taxpayer_type", "documents_ready"})


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
