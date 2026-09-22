"""Unit tests for UraReceptionistBrain logic, turns, transfer, and barge-in handling."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.flags import flags
from app.receptionist.brain import LLMContextFrame, UraReceptionistBrain
from app.receptionist.clarify import ClarifyState
from app.receptionist.serializer import (
    InterruptionFrame,
    OutputTransportMessageFrame,
    RequestOfficerFrame,
)
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import init_receptionist_schema, list_turns


class TestReceptionistBrain(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.init_db()
        init_receptionist_schema()

        import uuid
        self.call_id = f"call_brain_{uuid.uuid4().hex[:8]}"
        self.state = CallState(
            call_id=self.call_id,
            conversation_id=f"conv_{self.call_id}",
            user_id="taxpayer_1",
            mode="ai",
        )
        self.room = CallRoom(call_id=self.call_id, state=self.state)

        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {
            "reply": "You can register for a TIN using the URA portal.",
            "sources": ["URA Portal Guide"],
            "faithfulness_score": 0.95,
        }
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-TEST-123"

        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()

    async def test_greeting(self):
        await self.brain.say_greeting()
        turns = list_turns(self.call_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["speaker"], "assistant")
        self.assertIn("Hello", turns[0]["text"])

    async def test_answer_path_logs_conversation_and_turn(self):
        frame = LLMContextFrame(context="How do I register for a TIN?")
        await self.brain.process_frame(frame)

        # Check turn recorded
        turns = list_turns(self.call_id)
        # Should have caller turn + assistant answer
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["speaker"], "caller")
        self.assertEqual(turns[1]["speaker"], "assistant")
        self.assertTrue(any(term in turns[1]["text"] for term in ("TIN", "T-I-N", "portal")))

    async def test_explicit_human_request_triggers_transfer(self):
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: True if name == "ticket_queue" else False):
            frame = LLMContextFrame(context="I want to talk to an officer please")
            await self.brain.process_frame(frame)

            self.assertEqual(self.room.state.mode, "transferring")
            self.assertEqual(self.room.state.ticket_id, "TICK-TEST-123")
            self.assertEqual(self.room.state.transfer_reason, "caller_requested")

    async def test_ticket_queue_disabled_avoids_handoff_promise(self):
        # When ticket_queue flag is False: invariant must be respected
        with patch.object(flags, "is_enabled", return_value=False):
            frame = RequestOfficerFrame()
            await self.brain.process_frame(frame)

            # Mode remains ai (not transferred), caller is told to call toll-free
            self.assertNotEqual(self.room.state.mode, "transferring")
            turns = list_turns(self.call_id)
            self.assertTrue(any("0800 117 000" in t["text"] for t in turns))

    async def test_interruption_drops_late_generation(self):
        # Caller asks question
        start_gen_id = self.room.state.generation_id

        # Slow chat_model
        def slow_generate(*_args, **_kwargs):
            return {"reply": "Late reply"}

        self.chat_model.generate.side_effect = slow_generate

        # Barge-in frame arrives
        await self.brain.process_frame(InterruptionFrame())
        self.assertGreater(self.room.state.generation_id, start_gen_id)
        self.assertEqual(self.room.state.barge_in_count, 1)
