"""Unit tests for the receptionist persistence layer and retention."""

from __future__ import annotations

import time
import unittest
import uuid

from app import database as db
from app.receptionist.store import (
    create_call,
    create_turn,
    get_call,
    get_call_with_turns,
    init_receptionist_schema,
    list_calls,
    list_turns,
    save_call_review,
    update_call,
)


class TestReceptionistStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_receptionist_schema()

    def test_call_lifecycle_and_crud(self):
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        call = create_call(
            call_id=call_id,
            conversation_id=f"conv_{call_id}",
            user_id="taxpayer_123",
            tenant_id="default",
            locale="en",
            status="ai",
        )
        self.assertEqual(call["call_id"], call_id)
        self.assertEqual(call["status"], "ai")

        # Update call
        ok = update_call(
            call_id,
            status="transferring",
            transferred=True,
            transfer_reason="caller_requested",
            ticket_id="TICK-9999",
        )
        self.assertTrue(ok)

        fetched = get_call(call_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["status"], "transferring")
        self.assertEqual(fetched["transferred"], 1)
        self.assertEqual(fetched["transfer_reason"], "caller_requested")
        self.assertEqual(fetched["ticket_id"], "TICK-9999")

        # Add turns
        turn1 = create_turn(
            call_id=call_id,
            seq=1,
            speaker="caller",
            kind="utterance",
            text="My phone number is 0771234567 and I need a TIN",
            low_conf_words=[{"word": "TIN", "prob": 0.45}],
            mean_word_prob=0.88,
        )
        self.assertEqual(turn1["call_id"], call_id)
        self.assertEqual(turn1["seq"], 1)
        # Verify PII redaction on phone number
        self.assertNotIn("0771234567", turn1["text"])

        turn2 = create_turn(
            call_id=call_id,
            seq=2,
            speaker="assistant",
            kind="answer",
            text="You can apply for an instant TIN online.",
            faithfulness=0.95,
            latencies={"stt_ms": 120, "llm_ms": 800, "tts_first_ms": 250},
        )
        self.assertEqual(turn2["seq"], 2)

        turns = list_turns(call_id)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["speaker"], "caller")
        self.assertEqual(turns[1]["speaker"], "assistant")

        # Full call with turns
        full = get_call_with_turns(call_id)
        self.assertIsNotNone(full)
        self.assertEqual(len(full["turns"]), 2)

        # Review call
        rev_ok = save_call_review(call_id, rating=5, note="Excellent caller handling")
        self.assertTrue(rev_ok)
        reviewed = get_call(call_id)
        self.assertEqual(reviewed["officer_rating"], 5)
        self.assertEqual(reviewed["officer_note"], "Excellent caller handling")

    def test_list_calls_filter(self):
        id_ai = f"call_{uuid.uuid4().hex[:8]}"
        id_ended = f"call_{uuid.uuid4().hex[:8]}"
        create_call(id_ai, status="ai")
        create_call(id_ended, status="ended")

        live_calls = list_calls(status="live")
        ended_calls = list_calls(status="ended")

        live_ids = [c["call_id"] for c in live_calls]
        ended_ids = [c["call_id"] for c in ended_calls]

        self.assertIn(id_ai, live_ids)
        self.assertNotIn(id_ended, live_ids)
        self.assertIn(id_ended, ended_ids)
        self.assertNotIn(id_ai, ended_ids)

    def test_retention_cleanup(self):
        old_call_id = f"call_old_{uuid.uuid4().hex[:8]}"
        # Created 100 days ago
        old_time = time.time() - (100 * 86400)
        create_call(old_call_id, started_at=old_time)
        create_turn(old_call_id, seq=1, speaker="caller", kind="utterance", text="old query", created_at=old_time)

        # Run cleanup
        deleted = db.cleanup_expired_data()
        self.assertGreaterEqual(deleted.get("voice_calls", 0), 1)
        self.assertGreaterEqual(deleted.get("voice_call_turns", 0), 1)

        # Check call is deleted
        self.assertIsNone(get_call(old_call_id))
        self.assertEqual(len(list_turns(old_call_id)), 0)
