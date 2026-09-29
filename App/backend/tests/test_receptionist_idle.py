"""A caller who goes quiet: checked on, then the call is ended and its slot freed."""

from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.receptionist import config
from app.receptionist.brain import UraReceptionistBrain
from app.receptionist.phrases import phrase
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import init_receptionist_schema, list_turns


class CallerIdleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_idle_{uuid.uuid4().hex[:8]}"
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.speech = MagicMock()
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model, speech_model=self.speech)
        self.brain.push_frame = AsyncMock()
        self.say_to_caller = AsyncMock(return_value=0.0)
        self.hang_up_caller = AsyncMock()
        patches = [
            patch("app.receptionist.desk.say_to_caller", self.say_to_caller),
            patch("app.receptionist.desk.hang_up_caller", self.hang_up_caller),
            patch.dict(os.environ, {"RECEPTIONIST_IDLE_REPROMPTS": "1"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def said(self) -> list[str]:
        return [t["text"] for t in list_turns(self.call_id) if t["speaker"] == "assistant"]

    async def test_the_first_silence_checks_on_the_caller(self):
        await self.brain.on_caller_idle()
        self.assertEqual(self.said(), [phrase("idle_check", "en")])
        self.assertEqual(self.state.idle_prompts, 1)
        self.hang_up_caller.assert_not_awaited()

    async def test_continued_silence_says_goodbye_and_ends_the_call(self):
        await self.brain.on_caller_idle()
        await self.brain.on_caller_idle()
        self.say_to_caller.assert_awaited_once_with(self.room, phrase("idle_goodbye", "en"), self.speech)
        self.hang_up_caller.assert_awaited_once_with(self.room, "caller_idle")

    async def test_no_check_at_all_ends_the_call_at_the_first_silence(self):
        with patch.dict(os.environ, {"RECEPTIONIST_IDLE_REPROMPTS": "0"}):
            await self.brain.on_caller_idle()
        self.assertEqual(self.said(), [])
        self.hang_up_caller.assert_awaited_once_with(self.room, "caller_idle")

    async def test_the_check_is_in_the_call_s_language(self):
        self.state.locale = "lg"
        await self.brain.on_caller_idle()
        self.assertEqual(self.said(), [phrase("idle_check", "lg")])

    async def test_a_caller_waiting_for_an_officer_is_left_alone(self):
        for mode in ("transferring", "bridged", "ended"):
            with self.subTest(mode=mode):
                self.state.mode = mode
                await self.brain.on_caller_idle()
        self.assertEqual(self.said(), [])
        self.hang_up_caller.assert_not_awaited()

    async def test_silence_while_an_answer_is_worked_out_is_not_the_caller_s(self):
        self.brain._answering = True
        await self.brain.on_caller_idle()
        self.assertEqual((self.said(), self.state.idle_prompts), ([], 0))

    async def test_speaking_again_starts_the_count_over(self):
        await self.brain.on_caller_idle()
        self.chat_model.generate.return_value = {"reply": "Use the URA portal.", "sources": ["Guide"]}
        await self.brain.handle_external_question("How do I register for a TIN?", [])
        self.assertEqual(self.state.idle_prompts, 0)
        await self.brain.on_caller_idle()
        self.hang_up_caller.assert_not_awaited()  # a fresh check, not the goodbye

    async def test_the_answering_flag_clears_even_when_generation_fails(self):
        self.chat_model.generate.side_effect = RuntimeError("vLLM down")
        with patch("app.receptionist.brain.flags.is_enabled", return_value=False):
            await self.brain.handle_external_question("How do I register for a TIN?", [])
        self.assertFalse(self.brain._answering)


class IdleConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RECEPTIONIST_IDLE_REPROMPT_S", None)
            os.environ.pop("RECEPTIONIST_IDLE_REPROMPTS", None)
            self.assertEqual((config.get_idle_reprompt_s(), config.get_idle_reprompts()), (12.0, 1))

    def test_zero_turns_it_off_and_nonsense_keeps_the_default(self):
        with patch.dict(os.environ, {"RECEPTIONIST_IDLE_REPROMPT_S": "0", "RECEPTIONIST_IDLE_REPROMPTS": "-2"}):
            self.assertEqual((config.get_idle_reprompt_s(), config.get_idle_reprompts()), (0.0, 0))
        with patch.dict(os.environ, {"RECEPTIONIST_IDLE_REPROMPT_S": "soon", "RECEPTIONIST_IDLE_REPROMPTS": "x"}):
            self.assertEqual((config.get_idle_reprompt_s(), config.get_idle_reprompts()), (12.0, 1))


if __name__ == "__main__":
    unittest.main()
