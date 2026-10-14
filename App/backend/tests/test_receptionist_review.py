"""The caller's spoken review of the AI: "was this call helpful?"

Asked once a call, and only after the AI has answered something: at goodbye
(then thanks and hang-up), or while a caller who asked for an officer is
being transferred (the transfer is not held up by it). The answer is
classified positive / neutral / negative / unclear and stored with what was
said; a caller who hangs up first, or meets the officer first, is recorded as
having given no answer — never left pending. A new question in place of an
answer is answered, and the review is not asked again.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app import service_ratings
from app.flags import flags
from app.receptionist import review
from app.receptionist.brain import LLMContextFrame, UraReceptionistBrain
from app.receptionist.phrases import phrase
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import init_receptionist_schema, list_turns


class ClassifierTests(unittest.TestCase):
    def check(self, language: str, cases: dict[str, str]) -> None:
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(review.classify_spoken_review(text, language), expected)

    def test_english(self):
        self.check(
            "en",
            {
                "Yes, it was very helpful, thank you": "positive",
                "Yes": "positive",
                "It was great": "positive",
                "Not really": "negative",
                "No, it wasn't helpful": "negative",
                "Not helpful at all": "negative",
                "It didn't help me": "negative",
                "Useless": "negative",
                "No": "negative",
                "Not bad": "neutral",
                "A bit": "neutral",
                "Kind of, partly": "neutral",
                "Okay": "neutral",
                "No thank you": "unclear",
                "I'm not sure": "unclear",
                "": "unclear",
                "Mmm": "unclear",
            },
        )

    def test_luganda(self):
        self.check(
            "lg",
            {
                "Yee, kinnyambye nnyo": "positive",
                "Webale nnyo": "positive",
                "Tekinnyambye": "negative",
                "Nedda": "negative",
                "Kitono": "neutral",
                "Si kibi": "neutral",
            },
        )

    def test_swahili(self):
        self.check(
            "sw",
            {
                "Ndiyo, imenisaidia sana": "positive",
                "Asante sana": "positive",
                "Haikusaidia": "negative",
                "Hapana": "negative",
                "Si mbaya": "neutral",
                "Kiasi": "neutral",
                "Hapana asante": "unclear",
            },
        )

    def test_an_answer_is_told_from_a_new_question(self):
        self.assertTrue(review.is_review_answer("Yes it was helpful", "en"))
        self.assertTrue(review.is_review_answer("Mmm", "en"))
        self.assertFalse(review.is_review_answer("How do I register for VAT?", "en"))
        self.assertTrue(review.asks_something("Yes thanks, and how do I pay PAYE?"))
        self.assertFalse(review.asks_something("Yes, very helpful"))


class _Call(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_review_{uuid.uuid4().hex[:8]}"
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_r", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {
            "reply": "You can register for a TIN on the URA portal.",
            "sources": ["URA Portal Guide"],
            "faithfulness_score": 0.95,
        }
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-REVIEW-1"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()
        self.say = AsyncMock(return_value=0.0)
        self.hang_up = AsyncMock()
        for p in (
            patch("app.receptionist.desk.say_to_caller", self.say),
            patch("app.receptionist.desk.hang_up_caller", self.hang_up),
            patch.dict(os.environ, {"RECEPTIONIST_CALL_REVIEW": "true", "RECEPTIONIST_REVIEW_ANSWER_TIMEOUT_S": "1"}),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self) -> None:
        for task in (self.state.review_timer_task, self.state.transfer_timer_task):
            if task is not None and not task.done():
                task.cancel()

    async def turn(self, text: str) -> None:
        await self.brain.process_frame(LLMContextFrame(context=text))

    async def answered(self) -> None:
        await self.turn("How do I register for a TIN?")
        self.assertEqual(self.state.ai_answers_count, 1)

    def rating(self, source: str) -> dict | None:
        return service_ratings.find(source, call_id=self.call_id)

    def said(self) -> list[str]:
        return [t["text"] for t in list_turns(self.call_id) if t["speaker"] == "assistant"]

    def kinds(self, speaker: str = "caller") -> list[str]:
        return [t["kind"] for t in list_turns(self.call_id) if t["speaker"] == speaker]


class GoodbyeReviewTests(_Call):
    async def test_a_caller_the_ai_helped_is_asked_before_the_call_ends(self):
        await self.answered()
        await self.turn("Goodbye")
        self.assertEqual(self.said()[-1], phrase("review_ask_end", "en"))
        self.hang_up.assert_not_awaited()
        self.assertEqual(self.state.review_state, "asking_end")
        self.assertEqual(self.rating("call_ai_review_end")["status"], "pending")

        await self.turn("Yes, it was very helpful")
        row = self.rating("call_ai_review_end")
        self.assertEqual((row["status"], row["sentiment"], row["comment"]), ("answered", "positive", "Yes, it was very helpful"))
        self.assertEqual((row["channel"], row["subject"]), ("call", "ai"))
        self.say.assert_awaited_with(self.room, phrase("review_thanks_end", "en"), self.brain.speech_model)
        self.hang_up.assert_awaited_once_with(self.room, "caller_voice_hangup")
        # Recorded as a review, so the at-risk watch does not read it as distress.
        self.assertEqual(self.kinds()[-1], "review")

    async def test_a_satisfied_caller_saying_thats_all_is_asked_too(self):
        await self.answered()
        await self.turn("That's all, thank you")
        self.assertEqual(self.said()[-1], phrase("review_ask_end", "en"))

    async def test_goodbye_as_speech_to_text_writes_it_is_a_goodbye(self):
        # Seen on the live system: the recogniser wrote "Thank you, good bye."
        # and the AI answered the last question again instead of asking.
        await self.answered()
        await self.turn("Thank you, good bye.")
        self.assertEqual(self.said()[-1], phrase("review_ask_end", "en"))

    async def test_good_bye_ends_a_call_the_ai_has_not_answered(self):
        await self.turn("Good-bye")
        self.say.assert_awaited_once_with(self.room, phrase("officer_closing", "en"), self.brain.speech_model)
        self.hang_up.assert_awaited_once_with(self.room, "caller_voice_hangup")

    async def test_a_caller_the_ai_has_not_answered_is_not_asked(self):
        await self.turn("Goodbye")
        self.say.assert_awaited_once_with(self.room, phrase("officer_closing", "en"), self.brain.speech_model)
        self.assertIsNone(self.rating("call_ai_review_end"))

    async def test_a_negative_answer_is_kept_word_for_word(self):
        await self.answered()
        await self.turn("Goodbye")
        await self.turn("No, it didn't help me at all")
        row = self.rating("call_ai_review_end")
        self.assertEqual((row["sentiment"], row["comment"]), ("negative", "No, it didn't help me at all"))
        self.hang_up.assert_awaited_once()

    async def test_another_goodbye_with_no_opinion_is_a_skip(self):
        await self.answered()
        await self.turn("Goodbye")
        await self.turn("Bye")
        self.assertEqual(self.rating("call_ai_review_end")["status"], "skipped")
        self.say.assert_awaited_with(self.room, phrase("officer_closing", "en"), self.brain.speech_model)
        self.hang_up.assert_awaited_once()

    async def test_a_new_question_instead_is_answered_and_the_review_not_asked_again(self):
        await self.answered()
        await self.turn("Goodbye")
        self.chat_model.generate.reset_mock()
        await self.turn("Actually, how do I file my PAYE return?")
        self.chat_model.generate.assert_called_once()
        self.hang_up.assert_not_awaited()
        self.assertEqual(self.state.review_state, "moved_on")
        self.assertEqual(self.kinds()[-1], "utterance")
        await self.turn("Goodbye")
        self.say.assert_awaited_with(self.room, phrase("officer_closing", "en"), self.brain.speech_model)
        self.assertEqual(self.said().count(phrase("review_ask_end", "en")), 1)
        self.assertEqual(self.rating("call_ai_review_end")["status"], "pending")  # until the call ends
        await self.room.end("caller_voice_hangup")
        self.assertEqual(self.rating("call_ai_review_end")["status"], "no_answer")

    async def test_an_answer_that_asks_something_new_is_stored_and_the_question_answered(self):
        await self.answered()
        await self.turn("Goodbye")
        self.chat_model.generate.reset_mock()
        await self.turn("Yes it was helpful, and how do I pay PAYE?")
        self.assertEqual(self.rating("call_ai_review_end")["sentiment"], "positive")
        self.chat_model.generate.assert_called_once()
        self.hang_up.assert_not_awaited()

    async def test_silence_after_the_question_is_no_answer_and_the_usual_goodbye(self):
        await self.answered()
        await self.turn("Goodbye")
        # The "are you still there?" check is the review timer's to make.
        await self.brain.on_caller_idle()
        self.assertNotIn(phrase("idle_check", "en"), self.said())
        await asyncio.wait_for(self.state.review_timer_task, timeout=5)
        self.assertEqual(self.rating("call_ai_review_end")["status"], "no_answer")
        self.say.assert_awaited_with(self.room, phrase("officer_closing", "en"), self.brain.speech_model)
        self.hang_up.assert_awaited_once_with(self.room, "caller_voice_hangup")

    async def test_a_crisis_is_never_taken_for_a_review_answer(self):
        await self.answered()
        await self.turn("Goodbye")
        await self.turn("No. I want to end my life")
        self.hang_up.assert_not_awaited()
        self.assertTrue(any(line.startswith(phrase("crisis_support", "en")) for line in self.said()))
        # The review wait is dropped, so its timer can never end this call.
        self.assertEqual(self.state.review_state, "moved_on")
        await asyncio.sleep(0)
        self.assertTrue(self.state.review_timer_task.cancelled())
        self.assertEqual(self.kinds()[-1], "utterance")
        self.assertEqual(self.rating("call_ai_review_end")["status"], "pending")

    async def test_switched_off_no_call_is_asked(self):
        with patch.dict(os.environ, {"RECEPTIONIST_CALL_REVIEW": "false"}):
            await self.answered()
            await self.turn("Goodbye")
        self.assertNotIn(phrase("review_ask_end", "en"), self.said())
        self.hang_up.assert_awaited_once()

    async def test_the_question_is_asked_in_the_callers_language(self):
        self.state.locale = "sw"
        await self.answered()
        await self.turn("Kwaheri")
        self.assertEqual(self.said()[-1], phrase("review_ask_end", "sw"))
        await self.turn("Ndiyo, imenisaidia sana")
        self.assertEqual(self.rating("call_ai_review_end")["sentiment"], "positive")
        self.assertEqual(self.rating("call_ai_review_end")["locale"], "sw")


class TransferReviewTests(_Call):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for p in (
            patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"),
            patch("app.receptionist.transfer.hub.publish_lobby"),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def test_asked_while_the_transfer_goes_ahead_and_the_answer_is_thanked(self):
        await self.answered()
        await self.turn("Can I speak to an officer please")
        self.assertEqual(self.state.mode, "transferring")  # the transfer did not wait
        self.assertEqual(self.said()[-1], phrase("review_ask_transfer", "en"))
        self.assertEqual(self.rating("call_ai_review_transfer")["status"], "pending")
        await self.turn("Yes, mostly helpful thank you")
        row = self.rating("call_ai_review_transfer")
        self.assertEqual((row["status"], row["sentiment"]), ("answered", "positive"))
        self.assertEqual(self.said()[-1], phrase("review_thanks_transfer", "en"))
        self.assertEqual(self.kinds()[-1], "review")
        # One question, one answer: what the caller says next is for the officer.
        await self.turn("It is about my VAT refund")
        self.assertEqual(self.said()[-1], phrase("review_thanks_transfer", "en"))
        self.assertEqual(self.kinds()[-1], "utterance")

    async def test_not_asked_without_an_answer_first(self):
        await self.turn("Can I speak to an officer please")
        self.assertEqual(self.said()[-1], phrase("transfer", "en"))
        self.assertIsNone(self.rating("call_ai_review_transfer"))

    async def test_not_asked_on_a_transfer_the_caller_did_not_ask_for(self):
        await self.answered()
        await self.brain._transfer("timeout")
        self.assertEqual(self.said()[-1], phrase("transfer", "en"))
        self.assertIsNone(self.rating("call_ai_review_transfer"))

    async def test_the_officer_arriving_first_leaves_it_unanswered(self):
        from types import SimpleNamespace

        from app.receptionist import desk

        await self.answered()
        await self.turn("Can I speak to an officer please")
        self.state.claimed_by = "okello"
        with patch("app.receptionist.desk.hub.publish_lobby"):
            await desk.bridge(self.room, SimpleNamespace(close=AsyncMock(), send_json=AsyncMock()), "okello", speech_model=None)
        self.assertEqual(self.rating("call_ai_review_transfer")["status"], "no_answer")
        self.assertEqual(self.state.review_state, "done")

    async def test_hanging_up_while_asked_is_no_answer(self):
        await self.answered()
        await self.turn("Can I speak to an officer please")
        await self.room.end("caller_hangup")
        self.assertEqual(self.rating("call_ai_review_transfer")["status"], "no_answer")


class SettleTests(unittest.TestCase):
    def test_settling_is_idempotent_and_never_overwrites_an_answer(self):
        db.init_db()
        call_id = f"call_settle_{uuid.uuid4().hex[:8]}"
        room = CallRoom(call_id=call_id, state=CallState(call_id=call_id, conversation_id="c", mode="ai"))
        review.ask(room, "asking_end")
        review.record(room, "Yes, very helpful", "en")
        review.settle(room)
        review.settle(room)
        self.assertEqual(service_ratings.find("call_ai_review_end", call_id=call_id)["status"], "answered")


if __name__ == "__main__":
    unittest.main()
