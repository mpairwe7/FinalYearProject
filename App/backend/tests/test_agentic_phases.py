from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app import database as db
from app.memory.semantic import UserFact
from app.memory.service import get_memory_service, reset_memory_service
from app.service import ChatModel
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry


FLOWS_DIR = Path(__file__).resolve().parents[1] / "app" / "workflows" / "flows"


def _reset_db_root(base: str) -> None:
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")
    db._DB_DIR = Path(base)
    db._DB_PATH = db._DB_DIR / "analytics.db"
    reset_memory_service()
    db.init_db()


class AgenticPhaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = TemporaryDirectory(dir="/tmp")
        _reset_db_root(cls.tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            delattr(db._local, "conn")
        reset_memory_service()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        conn = db._get_connection()
        tables = [
            "workflow_sessions",
            "tickets",
            "consent_receipts",
            "user_profiles",
            "users",
            "user_facts",
            "episodic_summaries",
        ]
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed test table list
            except Exception:
                continue
        conn.commit()
        WorkflowRegistry._workflows.clear()
        for path in sorted(FLOWS_DIR.glob("*.yaml")):
            WorkflowRegistry.register(load_workflow(path))
        reset_memory_service()
        os.environ["FLAG_MEMORY_ENABLED"] = "true"

    def tearDown(self) -> None:
        os.environ.pop("FLAG_MEMORY_ENABLED", None)

    def test_new_workflow_triggers_are_registered(self) -> None:
        cases = {
            "How do I file a return?": "return_filing",
            "I need to object to an assessment": "objection_or_dispute",
            "Help me generate a PRN": "payment_assistance",
            "How do I clear my goods?": "customs_clearance",
        }
        for query, expected in cases.items():
            matched = WorkflowRegistry.match_trigger(query)
            self.assertIsNotNone(matched, query)
            self.assertEqual(matched.id, expected)

    def test_personalization_context_prefills_workflow_slots(self) -> None:
        user = db.upsert_user(
            external_id="personalized-user",
            email="user@example.com",
            role="verified_taxpayer",
        )
        user_id = user["id"]
        db.grant_consent(user_id, "personalization", "2026-04")
        db.upsert_user_profile(
            user_id,
            {
                "display_name": "Amina",
                "taxpayer_type": "company",
                "detail_level": "beginner",
                "registered_tax_types": ["vat", "paye"],
            },
        )
        memsvc = get_memory_service()
        memsvc.semantic.write(
            UserFact(
                fact_id="fact-1",
                user_id=user_id,
                tenant_id="default",
                category="industry",
                subject="user",
                predicate="operates_in",
                object_value="agriculture",
                confidence=0.9,
                extracted_at=time.time(),
                conversation_id="conv-1",
                turn_id="turn-1",
                extractor_model="rules-v1",
            )
        )

        model = ChatModel.__new__(ChatModel)
        personalization = model._load_personalization_state(user_id)
        self.assertIsNotNone(personalization)
        assert personalization is not None
        self.assertEqual(personalization["prefill_slots"]["taxpayer_type"], "company")
        self.assertIn("Preferred name: Amina", personalization["prompt_context"])
        self.assertIn("Industry: agriculture", personalization["prompt_context"])

        session = WorkflowRegistry.create_session("tin_registration")
        assert session is not None
        model._apply_personalization_to_workflow(session, personalization)
        turn = WorkflowRegistry.advance(session, "")
        self.assertIn("full legal name", turn.question.lower())
        self.assertEqual(turn.slot_name, "legal_name")

    def test_response_judge_revises_weak_uncited_reply(self) -> None:
        model = ChatModel.__new__(ChatModel)
        judgment = model._evaluate_response_judge(
            message="How do I file VAT?",
            reply="You can do it online.",
            hits=[
                {
                    "answer": (
                        "File the VAT return through the URA portal under Returns, "
                        "validate the declaration, and submit it."
                    )
                }
            ],
            citations=[{"ref": "[1]"}],
            faithfulness_score=0.34,
            escalation_required=False,
            escalation_reason="",
        )

        self.assertEqual(judgment["decision"], "revise")
        self.assertIn("grounding confidence is below the release threshold", judgment["reasons"])
        # References stay OUT of the revised prose — they reach the UI via
        # the citations panel (no orphan "[1]"/"1" at passage ends).
        self.assertNotIn("[1]", judgment["revised_reply"])
        self.assertTrue(judgment["revised_reply"].startswith("Here's the most relevant guidance"))

    def test_response_judge_keeps_well_grounded_reply_missing_markers(self) -> None:
        # Regression: "What services does URA provide?" was answered correctly
        # and then discarded. Claim verification reported 5/5 claims supported
        # (score 1.0) but no [N] markers, and uncited-alone forced a revise —
        # so the user got raw passages instead of the answer. Missing markers
        # on an otherwise well-grounded reply are not worth that trade.
        model = ChatModel.__new__(ChatModel)
        judgment = model._evaluate_response_judge(
            message="What services does URA provide?",
            reply=(
                "URA is the central tax and customs authority. Core services include "
                "taxpayer registration, domestic tax administration and customs."
            ),
            hits=[{"answer": "URA is the central tax and customs authority."}],
            citations=[{"ref": "[1]"}],
            faithfulness_score=1.0,
            escalation_required=False,
            escalation_reason="",
            claim_report={
                "decision": "revise",
                "score": 1.0,
                "uncited_claims": [{"text": "URA is the central tax and customs authority."}],
                "unsupported_claims": [],
            },
        )

        self.assertEqual(judgment["decision"], "approve")
        self.assertEqual(judgment["revised_reply"], "")

    def test_response_judge_still_revises_weakly_supported_claims(self) -> None:
        model = ChatModel.__new__(ChatModel)
        judgment = model._evaluate_response_judge(
            message="What services does URA provide?",
            reply="URA waives all penalties for first-time filers. [1]",
            hits=[{"answer": "URA is the central tax and customs authority."}],
            citations=[{"ref": "[1]"}],
            faithfulness_score=1.0,
            escalation_required=False,
            escalation_reason="",
            claim_report={
                "decision": "revise",
                "score": 0.5,
                "uncited_claims": [],
                "unsupported_claims": [{"text": "URA waives all penalties."}],
            },
        )

        self.assertEqual(judgment["decision"], "revise")
        self.assertIn(
            "claim verification found weakly supported factual claims", judgment["reasons"]
        )

    def test_ticket_roundtrip_preserves_handoff_and_judge_metadata(self) -> None:
        ticket = db.create_ticket(
            reason="Account-specific question",
            user_query="My TIN balance is wrong",
            bot_reply="Please contact URA.",
            conversation_id="conv-ticket",
            priority="high",
            handoff={
                "summary": "Needs authenticated staff review.",
                "topic": "account_specific",
                "required_details": ["TIN", "return period"],
                "sources_reviewed": ["ura_guide.pdf"],
            },
            response_judge={
                "decision": "escalate",
                "final_decision": "escalate",
                "reasons": ["account-specific query needs authenticated lookup or human review"],
                "confidence_band": "low",
            },
        )

        fetched = db.get_ticket(ticket["id"])
        assert fetched is not None
        self.assertEqual(fetched["handoff"]["topic"], "account_specific")
        self.assertEqual(fetched["response_judge"]["final_decision"], "escalate")

        rows = db.list_tickets(status="open", limit=10, offset=0)
        self.assertTrue(any(r["id"] == ticket["id"] for r in rows))
