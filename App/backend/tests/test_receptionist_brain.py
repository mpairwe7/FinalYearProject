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
        self.assertTrue(turns[0]["text"].startswith("Hi, thanks for contacting URA."))

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

    async def test_filler_pool_non_repeating_and_config(self):
        from app.receptionist.brain import FILLER_POOL
        from app.receptionist.config import get_filler_after_ms, get_max_spoken_sentences

        self.assertEqual(get_filler_after_ms(), 450)
        self.assertEqual(get_max_spoken_sentences(), 3)
        self.assertGreaterEqual(len(FILLER_POOL), 6)

        # Test no consecutive repeats over multiple picks
        last = None
        for _ in range(30):
            current = self.brain._pick_filler()
            self.assertIn(current, FILLER_POOL)
            self.assertNotEqual(current, last)
            last = current

    async def test_tts_voice_keyword_arguments(self):
        from app.receptionist.tts import UraSpeechTTS

        speech_mock = MagicMock()
        speech_mock.synthesize.return_value = MagicMock(audio=b"\x00" * 640)
        speech_mock._decode_audio_bytes.return_value = [0.0] * 320

        tts = UraSpeechTTS(
            speech_model=speech_mock,
            voice="en-KE-AsiliaNeural",
            language="en",
        )
        frames = [f async for f in tts.run_tts("Hello")]
        self.assertGreater(len(frames), 0)
        speech_mock.synthesize.assert_called_once_with(
            "Hello",
            voice="en-KE-AsiliaNeural",
            language="en",
        )


class TestReceptionistBrainLanguages(unittest.IsolatedAsyncioTestCase):
    """The brain speaks the call's current language and hears officer requests in any."""

    async def asyncSetUp(self):
        import uuid

        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_brain_lang_{uuid.uuid4().hex[:8]}"
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", mode="ai", locale="lg")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {"reply": "Okwewandiisa ku TIN tekusasulwa.", "sources": []}
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-LG-1"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()

    async def test_fillers_follow_the_call_language(self):
        from app.receptionist.phrases import fillers

        self.assertIn(self.brain._pick_filler(), fillers("lg"))
        self.state.locale = "sw"
        self.assertIn(self.brain._pick_filler(), fillers("sw"))

    async def test_answers_are_generated_in_the_call_language(self):
        self.state.locale = "sw"
        await self.brain.process_frame(LLMContextFrame(context="Ninawezaje kupata TIN?"))
        self.assertEqual(self.chat_model.generate.call_args.kwargs["locale"], "sw")

    async def test_luganda_answers_use_cross_lingual_knowledge_bridge(self):
        await self.brain.process_frame(LLMContextFrame(context="Nnyinza ntya okufuna TIN?"))
        self.assertEqual(self.chat_model.generate.call_args.kwargs["locale"], "en")

    async def test_a_luganda_request_for_a_person_transfers_with_a_luganda_notice(self):
        from app.receptionist.phrases import phrase

        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(LLMContextFrame(context="Njagala okwogera n'omuntu"))
        self.assertEqual(self.state.mode, "transferring")
        texts = [t["text"] for t in list_turns(self.call_id)]
        self.assertIn(phrase("transfer", "lg"), texts)
        handoff = self.chat_model._maybe_create_ticket.call_args.kwargs["handoff"]
        self.assertEqual(handoff["language"], "lg")

    async def test_a_swahili_request_for_an_officer_is_heard_on_any_call(self):
        from app.receptionist.brain import is_human_request

        self.assertTrue(is_human_request("Naomba msaada wa binadamu"))
        self.assertTrue(is_human_request("I want to talk to an officer"))
        self.assertFalse(is_human_request("Omusolo gwa VAT guli ki?"))

    async def test_a_turn_closed_after_the_call_moved_to_gemini_is_dropped(self):
        self.state.engine = "gemini_live"
        await self.brain.process_frame(LLMContextFrame(context="Nnyinza ntya okufuna TIN?"))
        self.chat_model.generate.assert_not_called()
        self.assertEqual(list_turns(self.call_id), [])

    async def test_a_language_switch_is_not_a_barge_in(self):
        """The router bumps generation_id itself; the brain only reports the frame passing."""
        seen = []
        self.brain.on_switch_interrupt = lambda: seen.append(True)
        switch = InterruptionFrame()
        switch.metadata = {"language_switch": True}
        await self.brain.process_frame(switch)
        self.assertEqual((self.state.barge_in_count, self.state.generation_id), (0, 0))
        self.assertEqual(seen, [True])
        await self.brain.process_frame(InterruptionFrame())
        self.assertEqual((self.state.barge_in_count, self.state.generation_id), (1, 1))

    async def test_the_officers_busy_line_is_localised(self):
        from app.receptionist.phrases import phrase

        self.state.mode = "transferring"
        await self.brain._transfer_timeout_countdown(0, "TICK-LG-1")
        texts = [t["text"] for t in list_turns(self.call_id)]
        self.assertIn(phrase("officers_busy", "lg", ref="TICK-LG-1"), texts)

    async def test_a_prefilled_answer_does_not_get_a_second_filler(self):
        import time as _time

        def slow_generate(**_kwargs):
            _time.sleep(0.7)  # past RECEPTIONIST_FILLER_AFTER_MS
            return {"reply": "Okwewandiisa ku TIN tekusasulwa.", "sources": []}

        self.chat_model.generate.side_effect = slow_generate
        await self.brain.say_filler()
        await self.brain.handle_external_question("Nnyinza ntya okufuna TIN?")
        kinds = [c.args[0].message.get("text") for c in self.brain.push_frame.call_args_list
                 if hasattr(c.args[0], "message") and isinstance(c.args[0].message, dict)
                 and c.args[0].message.get("type") == "caption"]
        from app.receptionist.phrases import fillers

        self.assertEqual(sum(text in fillers("lg") for text in kinds), 1)

