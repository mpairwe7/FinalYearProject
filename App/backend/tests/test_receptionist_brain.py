"""Unit tests for UraReceptionistBrain logic, turns, transfer, and barge-in handling."""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.flags import flags
from app.receptionist.brain import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMContextFrame,
    UraReceptionistBrain,
)
from app.receptionist.clarify import ClarifyState
from app.receptionist.serializer import (
    InterruptionFrame,
    OutputTransportMessageFrame,
    RequestOfficerFrame,
)
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import (
    create_call,
    get_call,
    init_receptionist_schema,
    list_turns,
    update_call,
)

#: A Luganda TIN question as Whisper hears it in English, word by word and
#: confident: "ttiimu" is a known mishear of TIN, so the gate asks about it.
ENGLISH_HEARING = "Nyinza ntya okwewandiisa okufuna ttiimu yange?"


def _words(text: str) -> list[dict]:
    return [{"word": w, "prob": 0.95} for w in text.split()]


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

    async def test_an_answered_call_turn_is_counted_once_on_the_call_channel(self):
        from app.analytics import metrics

        key = 'chat_turns_total{channel="call",outcome="answered"}'
        before = metrics.snapshot()["counters"].get(key, 0)
        await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
        self.assertEqual(metrics.snapshot()["counters"].get(key, 0) - before, 1)

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
            # On screen: the local voice cannot read a phone number out.
            self.assertFalse(any("0800" in v for v in self._voiced()))

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

    async def test_a_request_for_a_person_is_heard_however_it_is_put(self):
        from app.receptionist.brain import is_human_request

        for text in (
            "Yes, connect me to an officer.",
            "Put me through to someone, please.",
            "Can I speak with an agent?",
            "Transfer me to a person.",
            "Get me a human.",
            # The one-word zero-out the greeting invites, in any call language.
            "Officer.",
            "An agent, please.",
            "Yes, a real person.",
            "Customer care!",
            "Omukozi, nsaba.",
            "Afisa tafadhali",
        ):
            self.assertTrue(is_human_request(text), text)
        for text in (
            "I talked to my accountant about PAYE.",
            "What is the penalty for filing late?",
            "How do I transfer my TIN to a new business?",
            "What does a tax officer do?",
            "Is the customs agent responsible for the duty?",
        ):
            self.assertFalse(is_human_request(text), text)

    def _offer_officer(self):
        """An answer the AI is unsure of ends with the officer offer."""
        self.chat_model.generate.return_value = {**self.chat_model.generate.return_value, "confidence": 0.4}
        return patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue")

    async def test_yes_to_the_officer_offer_transfers_the_call(self):
        from app.receptionist.phrases import phrase

        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            self.assertTrue(list_turns(self.call_id)[-1]["text"].endswith(phrase("officer_offer", "en")))
            await self.brain.process_frame(LLMContextFrame(context="Yes, please."))
        self.assertEqual((self.state.mode, self.state.transfer_reason), ("transferring", "offer_accepted"))
        self.assertEqual(self.chat_model.generate.call_count, 1)  # "Yes, please." is not a new question

    async def test_no_to_the_officer_offer_keeps_the_ai_on_the_call(self):
        from app.receptionist.phrases import phrase

        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            await self.brain.process_frame(LLMContextFrame(context="No, thank you."))
        self.assertEqual(self.state.mode, "ai")
        self.assertEqual(self.state.pending_offer, "")
        self.assertEqual(list_turns(self.call_id)[-1]["text"], phrase("offer_declined", "en"))
        self.assertEqual(self.chat_model.generate.call_count, 1)

    async def test_a_question_after_the_offer_is_answered_and_the_offer_lapses(self):
        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            self.chat_model.generate.return_value = {**self.chat_model.generate.return_value, "confidence": 0.9}
            await self.brain.process_frame(LLMContextFrame(context="Yes, and what is the VAT rate?"))
            self.assertEqual(self.state.pending_offer, "")
            await self.brain.process_frame(LLMContextFrame(context="Yes."))
        self.assertEqual(self.state.mode, "ai")  # the offer lapsed: "Yes." is not an acceptance now
        self.assertEqual(self.chat_model.generate.call_count, 3)

    async def test_an_at_risk_call_is_offered_an_officer_once(self):
        from app.receptionist.phrases import phrase

        create_call(self.call_id, conversation_id=self.state.conversation_id)
        update_call(self.call_id, risk_json=json.dumps({"level": "at_risk", "signals": ["distress"]}))
        offer = phrase("officer_offer", "en")
        await self.brain.process_frame(LLMContextFrame(context="I'm so stressed. How do I register for a TIN?"))
        self.assertTrue(list_turns(self.call_id)[-1]["text"].endswith(offer))
        self.assertEqual(self.state.pending_offer, "officer")
        await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        self.assertFalse(list_turns(self.call_id)[-1]["text"].endswith(offer))
        self.assertEqual(self.state.mode, "ai")

    async def test_an_at_risk_offer_talked_over_before_it_was_heard_is_made_again(self):
        from app.receptionist.phrases import phrase

        create_call(self.call_id, conversation_id=self.state.conversation_id)
        update_call(self.call_id, risk_json=json.dumps({"level": "at_risk", "signals": ["distress"]}))
        offer = phrase("officer_offer", "en")
        await self.brain.process_frame(LLMContextFrame(context="I'm so stressed. How do I register for a TIN?"))
        await self.brain.process_frame(BotStartedSpeakingFrame())
        await self.brain.process_frame(InterruptionFrame())  # talked over it, before the offer
        await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        self.assertTrue(list_turns(self.call_id)[-1]["text"].endswith(offer))  # never heard, so made again
        await self.brain.process_frame(BotStoppedSpeakingFrame())
        await self.brain.process_frame(LLMContextFrame(context="What is the VAT rate?"))
        self.assertFalse(list_turns(self.call_id)[-1]["text"].endswith(offer))  # heard this time: once is enough

    async def test_an_at_risk_offer_repeated_after_being_talked_over_is_still_made_once(self):
        from app.receptionist.phrases import phrase

        create_call(self.call_id, conversation_id=self.state.conversation_id)
        update_call(self.call_id, risk_json=json.dumps({"level": "at_risk", "signals": ["distress"]}))
        offer = phrase("officer_offer", "en")
        await self.brain.process_frame(LLMContextFrame(context="I'm so stressed. How do I register for a TIN?"))
        await self.brain.process_frame(BotStartedSpeakingFrame())
        await self.brain.process_frame(InterruptionFrame())  # talked over it, before the offer
        await self.brain.process_frame(BotStoppedSpeakingFrame())
        await self.brain.process_frame(LLMContextFrame(context="Could you repeat that please?"))
        self.assertTrue(list_turns(self.call_id)[-1]["text"].endswith(offer))  # heard this time
        await self.brain.process_frame(LLMContextFrame(context="No, thank you."))
        await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        self.assertFalse(list_turns(self.call_id)[-1]["text"].endswith(offer))  # declined: not offered again

    async def test_a_switch_re_asks_the_question_not_the_clarification_its_old_transcript_opened(self):
        # G92: Whisper's English transcript of a Luganda question landed before the language vote.
        from app.receptionist.phrases import phrase

        started = time.monotonic()
        await self.brain.handle_external_question(ENGLISH_HEARING, _words(ENGLISH_HEARING))
        self.assertIsNotNone(self.state.clarify)  # "Excuse me, did you say tin?"
        self.state.locale = "sw"  # the router moved the call, and re-asks the turn
        await self.brain.handle_external_question(
            "Ninawezaje kujisajili kupata TIN yangu?", [], reask_window=(started, time.monotonic())
        )
        self.assertIsNone(self.state.clarify)
        self.assertNotIn(phrase("clarify_restart", "sw"), [t["text"] for t in list_turns(self.call_id)])
        self.assertEqual(self.chat_model.generate.call_args.kwargs["locale"], "sw")  # answered

    async def test_a_clarification_from_an_earlier_turn_survives_a_switch(self):
        await self.brain.handle_external_question(ENGLISH_HEARING, _words(ENGLISH_HEARING))
        self.assertIsNotNone(self.state.clarify)
        started = time.monotonic()  # the caller's next turn, after hearing the question
        self.state.locale = "sw"
        with patch.object(self.brain.clarify_gate, "resolve", wraps=self.brain.clarify_gate.resolve) as resolve:
            await self.brain.handle_external_question("Ndiyo, TIN", [], reask_window=(started, time.monotonic()))
        resolve.assert_called_once()  # taken as the reply to it, not dropped

    async def test_a_clarification_from_a_turn_begun_during_the_re_ask_survives_it(self):
        # CodeRabbit on #522: a slow re-transcription can finish after the
        # caller has started the next turn, and that turn opened its own.
        window = (time.monotonic(), time.monotonic())  # the re-asked turn, and the switch
        await self.brain.handle_external_question(ENGLISH_HEARING, _words(ENGLISH_HEARING))  # the next turn
        opened = self.state.clarify
        self.assertIsNotNone(opened)
        self.state.locale = "sw"
        await self.brain.handle_external_question("Ninawezaje kujisajili kupata TIN yangu?", [], reask_window=window)
        self.assertIsNotNone(self.state.clarify)  # still asking the later turn's question

    def _long_answer(self, **extra):
        """An answer longer than RECEPTIONIST_MAX_SPOKEN_SENTENCES (3)."""
        reply = " ".join(f"Step {n} of the return is done online." for n in ("one", "two", "three", "four", "five"))
        self.chat_model.generate.return_value = {**self.chat_model.generate.return_value, "reply": reply, **extra}

    def _voiced(self) -> list[str]:
        return [
            getattr(c.args[0], "text", "")
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "LLMTextFrame"
        ]

    async def test_an_officer_offer_is_the_only_question_a_long_answer_ends_on(self):
        from app.receptionist.phrases import phrase

        self._long_answer(confidence=0.4)
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        spoken = list_turns(self.call_id)[-1]["text"]
        self.assertTrue(spoken.endswith(phrase("officer_offer", "en")))
        self.assertNotIn(phrase("more_detail", "en"), spoken)  # "yes" can only mean one thing
        self.assertEqual(self.state.pending_offer, "officer")

    async def test_yes_to_more_detail_reads_the_rest_of_the_answer(self):
        from app.receptionist.phrases import phrase

        self._long_answer()
        await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        self.assertTrue(list_turns(self.call_id)[-1]["text"].endswith(phrase("more_detail", "en")))
        self.assertEqual(self.state.pending_offer, "more")
        await self.brain.process_frame(LLMContextFrame(context="Yes, please."))
        rest = list_turns(self.call_id)[-1]["text"]
        self.assertIn("Step four", rest)
        self.assertIn("Step five", rest)
        self.assertNotIn("Step one", rest)
        self.assertEqual(self.chat_model.generate.call_count, 1)  # read on, not looked up again
        self.assertEqual((self.state.mode, self.state.pending_offer), ("ai", ""))

    async def test_tell_me_more_answers_more_detail_but_not_an_officer_offer(self):
        self._long_answer()
        await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        await self.brain.process_frame(LLMContextFrame(context="Tell me more."))
        self.assertIn("Step four", list_turns(self.call_id)[-1]["text"])
        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            await self.brain.process_frame(LLMContextFrame(context="Tell me more."))
        self.assertEqual(self.state.mode, "ai")  # a new question, not a transfer
        self.assertEqual(self.chat_model.generate.call_count, 3)

    async def test_no_to_more_detail_ends_the_answer(self):
        from app.receptionist.phrases import phrase

        self._long_answer()
        await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        await self.brain.process_frame(LLMContextFrame(context="No, that's all."))
        self.assertEqual(list_turns(self.call_id)[-1]["text"], phrase("offer_declined", "en"))
        self.assertEqual(self.state.pending_offer, "")

    async def test_a_repeated_answer_asks_its_question_again(self):
        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            for _ in range(2):
                await self.brain.process_frame(LLMContextFrame(context="Could you repeat that please?"))
            self.assertEqual(list_turns(self.call_id)[-1]["text"].count("let me repeat"), 1)
            self.assertEqual(self.state.pending_offer, "officer")
            await self.brain.process_frame(LLMContextFrame(context="Yes."))
        self.assertEqual((self.state.mode, self.state.transfer_reason), ("transferring", "offer_accepted"))

    async def test_okay_said_over_an_answer_does_not_take_the_caller_to_an_officer(self):
        from app.receptionist.phrases import phrase

        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            await self.brain.process_frame(BotStartedSpeakingFrame())
            await self.brain.process_frame(InterruptionFrame())  # talked over it, before the question
            await self.brain.process_frame(LLMContextFrame(context="Okay."))
            self.assertEqual(self.state.mode, "ai")
            self.assertEqual(list_turns(self.call_id)[-1]["text"], phrase("officer_offer", "en"))  # asked again
            await self.brain.process_frame(LLMContextFrame(context="Yes."))
        self.assertEqual((self.state.mode, self.state.transfer_reason), ("transferring", "offer_accepted"))
        self.assertEqual(self.chat_model.generate.call_count, 1)

    async def test_a_yes_after_the_question_was_heard_is_a_yes(self):
        # Pipecat interrupts as every caller turn starts: after the assistant
        # has finished speaking that is no barge-in (live replay, 2026-09-30).
        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            await self.brain.process_frame(BotStartedSpeakingFrame())
            await self.brain.process_frame(BotStoppedSpeakingFrame())
            await self.brain.process_frame(InterruptionFrame())
            await self.brain.process_frame(LLMContextFrame(context="Yes please."))
        self.assertEqual((self.state.mode, self.state.transfer_reason), ("transferring", "offer_accepted"))

    async def test_are_you_still_there_replaces_an_unanswered_offer(self):
        with self._offer_officer():
            await self.brain.process_frame(LLMContextFrame(context="How do I register for a TIN?"))
            await self.brain.on_caller_idle()
            await self.brain.process_frame(LLMContextFrame(context="Yes."))  # "yes, I'm here"
        self.assertEqual(self.state.mode, "ai")

    async def test_a_caller_in_crisis_hears_where_to_get_help_and_is_offered_a_person(self):
        from app.receptionist.phrases import phrase

        create_call(self.call_id, conversation_id=self.state.conversation_id)
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(
                LLMContextFrame(context="I can't pay these taxes, I just want to end my life.")
            )
            spoken, screen = list_turns(self.call_id)[-2:]
            self.assertEqual(
                spoken["text"], f"{phrase('crisis_support', 'en')} {phrase('crisis_offer', 'en')}"
            )
            self.assertEqual(screen["text"], phrase("crisis_screen", "en"))
            self.assertNotIn(screen["text"], self._voiced())  # the numbers are shown, not read
            self.chat_model.generate.assert_not_called()  # never a tax answer
            self.assertEqual(self.state.mode, "ai")  # a person is offered, not imposed
            await self.brain.process_frame(LLMContextFrame(context="Yes, please."))
        self.assertEqual((self.state.mode, self.state.transfer_reason), ("transferring", "safety_concern"))
        self.assertEqual(get_call(self.call_id)["priority"], "urgent")

    async def test_a_caller_in_crisis_who_asks_for_a_person_is_put_through_at_once(self):
        from app.receptionist.phrases import phrase

        create_call(self.call_id, conversation_id=self.state.conversation_id)
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(
                LLMContextFrame(context="I want to kill myself. Let me talk to an officer.")
            )
        self.assertEqual((self.state.mode, self.state.transfer_reason), ("transferring", "safety_concern"))
        self.assertEqual(get_call(self.call_id)["priority"], "urgent")
        self.assertIn(phrase("crisis_support", "en"), [t["text"] for t in list_turns(self.call_id)])

    async def test_no_to_an_officer_after_the_crisis_lines_leaves_them_the_numbers(self):
        from app.receptionist.phrases import phrase

        await self.brain.process_frame(LLMContextFrame(context="I want to end it all."))
        await self.brain.process_frame(LLMContextFrame(context="No, thank you."))
        self.assertEqual(list_turns(self.call_id)[-1]["text"], phrase("crisis_declined", "en"))
        self.assertEqual((self.state.mode, self.state.pending_offer), ("ai", ""))

    async def test_a_transfer_settles_the_question_the_ai_last_asked(self):
        self._long_answer()
        await self.brain.process_frame(LLMContextFrame(context="How do I file a return?"))
        self.assertEqual(self.state.pending_offer, "more")
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(RequestOfficerFrame())  # the Talk to an officer button
        self.assertEqual((self.state.mode, self.state.pending_offer), ("transferring", ""))

    async def test_a_crisis_is_never_taken_for_a_goodbye(self):
        await self.brain.process_frame(LLMContextFrame(context="I want to end it all. Goodbye."))
        self.assertEqual((self.state.mode, self.state.pending_offer), ("ai", "officer"))

    async def test_the_chat_pipelines_own_crisis_check_is_honoured(self):
        from app.receptionist.phrases import phrase

        # A Luganda turn is only read for a crisis once it is in English.
        self.chat_model.generate.return_value = {
            "reply": "I'm really sorry you're feeling this way.", "sources": [], "retrieval_mode": "crisis_support",
        }
        await self.brain.process_frame(LLMContextFrame(context="Ekibuuzo kyange ku musolo"))
        self.assertEqual(list_turns(self.call_id)[-1]["text"], phrase("crisis_screen", "en"))
        self.assertEqual(self.state.pending_offer, "officer")


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

    async def test_a_crisis_found_only_in_english_gets_the_luganda_crisis_lines(self):
        from app.receptionist.phrases import phrase

        # "Njagala kufa" is not one of the crisis verbs; the chat finds it in the English query.
        self.chat_model.generate.return_value = {
            "reply": "I'm really sorry you're feeling this way.", "sources": [], "retrieval_mode": "crisis_support",
        }
        with (
            patch.object(self.brain, "_extract_english_tax_query", return_value="I want to die"),
            patch.object(self.brain, "_synthesize_luganda_reply") as synthesize,
        ):
            await self.brain.process_frame(LLMContextFrame(context="Njagala kufa, omusolo gunnemye"))
        texts = [t["text"] for t in list_turns(self.call_id)]
        self.assertIn(f"{phrase('crisis_support', 'lg')} {phrase('crisis_offer', 'lg')}", texts)
        self.assertEqual(texts[-1], phrase("crisis_screen", "lg"))
        synthesize.assert_not_called()  # never a Luganda paraphrase of the chat's reply
        self.assertEqual(self.state.pending_offer, "officer")

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

    async def test_a_turn_the_language_router_is_re_asking_is_dropped(self):
        # Answered once, in the new language, by the router's re-ask; the
        # old-language transcript must not be answered as well.
        self.brain.turn_claimed = lambda: True
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

    async def test_a_statutory_rate_question_is_answered_from_retrieval(self):
        """No hardcoded rates: the answer (and its figure) comes from the knowledge base."""
        frame = LLMContextFrame(context="What is the standard VAT rate in Uganda?")
        await self.brain.process_frame(frame)
        self.chat_model.generate.assert_called_once()

    async def test_the_answer_reaches_tts_as_one_frame_with_its_spacing(self):
        """The TTS aggregator splits sentences itself; pushing them separately
        without the space between them merged "portal.Step" into one."""
        self.brain.push_frame.reset_mock()
        await self.brain._say_and_record("Step 1. Visit the portal. Step 2. Submit your form.")
        text_frames = [
            getattr(c.args[0], "text", "")
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "LLMTextFrame"
        ]
        self.assertEqual(text_frames, ["Step 1. Visit the portal. Step 2. Submit your form."])

    async def test_in_call_voice_control_repeat(self):
        self.state.locale = "en"
        self.state.last_assistant_answer = "The standard VAT rate is 18 percent."
        frame = LLMContextFrame(context="Could you repeat that please?")
        await self.brain.process_frame(frame)
        turns = list_turns(self.call_id)
        last_turn = turns[-1]
        self.assertEqual(last_turn["speaker"], "assistant")
        self.assertIn("18 percent", last_turn["text"])

    async def test_in_call_voice_control_speed_slower(self):
        from app.receptionist.phrases import phrase

        self.state.locale = "lg"
        self.assertFalse(self.state.speech_rate_slow)
        frame = LLMContextFrame(context="Yogera mpola")
        await self.brain.process_frame(frame)
        self.assertTrue(self.state.speech_rate_slow)  # UraSpeechTTS now pauses between sentences
        turns = list_turns(self.call_id)
        self.assertIn(phrase("slow_ack", "lg"), [t["text"] for t in turns])
        self.chat_model.generate.assert_not_called()

    async def test_in_call_voice_control_hangup_is_heard_before_the_call_ends(self):
        from app.receptionist.phrases import phrase

        self.state.locale = "en"
        order: list[str] = []
        say = AsyncMock(side_effect=lambda *_a: order.append("said") or 0.0)
        hang_up = AsyncMock(side_effect=lambda *_a: order.append("hung up"))
        with patch("app.receptionist.desk.say_to_caller", say), patch("app.receptionist.desk.hang_up_caller", hang_up):
            await self.brain.process_frame(LLMContextFrame(context="Goodbye, thank you"))
        say.assert_awaited_once_with(self.room, phrase("officer_closing", "en"), self.brain.speech_model)
        hang_up.assert_awaited_once_with(self.room, "caller_voice_hangup")
        self.assertEqual(order, ["said", "hung up"])

    async def test_a_question_that_mentions_goodbye_is_answered_not_hung_up(self):
        hang_up = AsyncMock()
        with patch("app.receptionist.desk.hang_up_caller", hang_up):
            await self.brain.process_frame(LLMContextFrame(
                context="I said goodbye to my old employer last month, so how do I file my PAYE now?"
            ))
        hang_up.assert_not_awaited()
        self.chat_model.generate.assert_called_once()


