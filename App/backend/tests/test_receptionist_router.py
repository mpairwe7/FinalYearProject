"""LanguageRouter: what a language decision does to a live call."""

from __future__ import annotations

import asyncio
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from app import database as db
from app.receptionist.language import LanguagePolicy, LanguageVote, PolicyConfig
from app.receptionist.phrases import phrase
from app.receptionist.router import LanguageRouter
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import create_call, init_receptionist_schema, list_turns
from app.speech_service import pcm16_to_wav

PCM = b"\x10\x00" * 32000


def vote(top: str, p: float, speech_s: float = 3.0, text: str = "") -> LanguageVote:
    rest = [lang for lang in ("en", "sw", "lg") if lang != top]
    return LanguageVote({top: p, **{lang: (1 - p) / 2 for lang in rest}}, top, speech_s, text, latency_ms=120.0)


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        call_id = f"call_router_{uuid.uuid4().hex[:8]}"
        create_call(call_id, conversation_id=f"conv_{call_id}")
        self.room = CallRoom(call_id=call_id, state=CallState(call_id=call_id, conversation_id=f"conv_{call_id}"))
        self.room.state.locale = "en"
        self.room.state.languages_used = ["en"]
        self.brain = MagicMock()
        self.brain.handle_external_question = AsyncMock()
        self.brain._say_and_record = AsyncMock()
        self.brain.say_filler = AsyncMock()
        self.speech = MagicMock()
        self.speech.transcribe.return_value = SimpleNamespace(text="Nsaba okumanya ku TIN", words=["w"])
        self.outlet = MagicMock()
        self.outlet.send = AsyncMock()
        self.router = LanguageRouter(
            self.room,
            LanguagePolicy(active="en", config=PolicyConfig()),
            self.speech,
            outlet=self.outlet,
            brain=self.brain,
        )

        async def interrupt() -> None:
            # The brain reports the switch interruption passing it.
            self.router.switch_passed()

        self.router.interrupt = AsyncMock(side_effect=interrupt)

    async def settle(self) -> None:
        while pending := [t for t in self.router._reasks if not t.done()]:
            await asyncio.gather(*pending)

    async def turn(self, v: LanguageVote, pcm: bytes = PCM, decoded=None, segments: int = 1) -> None:
        """One caller turn: its start, its vote, then its end (when deferred switches run)."""
        await self.router.on_speech_started()
        await self.router.on_vote(v, pcm, decoded)
        await self.router.on_turn_end(pcm, segments)
        await self.settle()

    def sent(self, kind: str) -> list[dict]:
        return [c.args[0] for c in self.outlet.send.call_args_list if c.args[0].get("type") == kind]

    async def test_a_confident_first_vote_locks_the_language(self):
        await self.turn(vote("en", 0.92))
        self.assertTrue(self.router.policy.locked)
        self.assertEqual(self.sent("language"), [{"type": "language", "language": "en", "source": "auto", "confidence": 0.92}])
        self.router.interrupt.assert_not_awaited()
        self.assertFalse(self.router.claims_turn())  # the brain answers this turn itself

    async def test_a_weak_first_vote_stays_open(self):
        await self.turn(vote("lg", 0.6))
        self.assertFalse(self.router.policy.locked)
        self.assertEqual(self.sent("language"), [])
        self.assertEqual(self.room.state.locale, "en")

    async def test_luganda_moves_the_call_and_answers_the_same_question(self):
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), PCM, None)
        # Decided, but the caller may not have finished: nothing moves yet, and
        # the turn is the router's — the brain must not answer its English transcript.
        self.assertEqual(self.room.state.locale, "en")
        self.router.interrupt.assert_not_awaited()
        self.assertTrue(self.router.claims_turn())
        await self.router.on_turn_end(PCM, 1)
        await self.settle()
        state = self.room.state
        self.assertEqual((state.locale, state.language_switches), ("lg", 1))
        self.router.interrupt.assert_awaited_once()
        self.brain.say_filler.assert_awaited_once()  # something to hear at once
        self.speech.transcribe.assert_called_once_with(pcm16_to_wav(PCM), 16000, "lg", True)
        self.brain.handle_external_question.assert_awaited_once_with("Nsaba okumanya ku TIN", ["w"], reasked_since=ANY)
        captions = self.sent("caption")
        self.assertEqual((captions[0]["speaker"], captions[0]["text"]), ("caller", "Nsaba okumanya ku TIN"))
        self.assertEqual(self.sent("language")[0]["language"], "lg")

    async def test_the_re_ask_says_when_its_turn_began(self):
        # The brain drops a clarification this turn's old-language transcript opened (G92).
        before = time.monotonic()
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), PCM, None)
        await self.router.on_speech_started()  # a second segment: the same turn
        await self.router.on_turn_end(PCM, 2)
        await self.settle()
        since = self.brain.handle_external_question.await_args.kwargs["reasked_since"]
        self.assertGreaterEqual(since, before)
        self.assertLessEqual(since, time.monotonic())
        await self.turn(vote("en", 0.95), decoded=("What is the VAT rate?", "en"))
        self.assertGreater(self.brain.handle_external_question.await_args.kwargs["reasked_since"], since)

    async def test_the_switched_turn_stays_claimed_until_the_caller_speaks_again(self):
        await self.turn(vote("lg", 0.9))
        # The aggregator's transcript of that turn can land after the re-ask.
        self.assertTrue(self.router.claims_turn())
        await self.router.on_speech_started()
        self.assertFalse(self.router.claims_turn())

    async def test_the_sentinel_s_decode_is_reused(self):
        await self.turn(vote("lg", 0.9), decoded=("Nsaba okumanya ku TIN yange", "lg"))
        self.speech.transcribe.assert_not_called()
        self.brain.handle_external_question.assert_awaited_once_with("Nsaba okumanya ku TIN yange", [], reasked_since=ANY)

    async def test_back_to_english_is_answered_on_the_same_local_engine(self):
        await self.turn(vote("lg", 0.9))
        await self.turn(vote("en", 0.95), decoded=("What is the VAT rate?", "en"))
        self.assertEqual(self.room.state.locale, "en")
        self.assertEqual(self.room.state.language_switches, 2)
        self.brain.handle_external_question.assert_awaited_with("What is the VAT rate?", [], reasked_since=ANY)

    async def test_an_explicit_request_is_confirmed_not_answered(self):
        await self.turn(vote("en", 0.95, speech_s=1.4, text="Can we speak Luganda?"))
        self.assertEqual(self.room.state.locale, "lg")
        self.brain._say_and_record.assert_awaited_once_with(phrase("switched", "lg"), kind="notice")
        self.brain.handle_external_question.assert_not_awaited()
        self.speech.transcribe.assert_not_called()
        self.assertEqual(self.room.state.language_source, "explicit")

    async def test_an_on_screen_choice_is_confirmed_at_once(self):
        await self.router.on_override("sw")
        await self.settle()
        self.assertEqual(self.room.state.locale, "sw")
        self.router.interrupt.assert_awaited_once()
        self.brain._say_and_record.assert_awaited_once_with(phrase("switched", "sw"), kind="notice")
        self.assertEqual(self.room.state.language_overrides, 1)
        self.assertEqual(self.sent("language")[0]["source"], "override")

    async def test_staff_see_the_switch_live_and_in_the_transcript(self):
        from app.receptionist import router as router_mod

        with patch.object(router_mod.hub, "publish_call") as publish_call, patch.object(
            router_mod.hub, "publish_lobby"
        ) as publish_lobby:
            await self.turn(vote("lg", 0.93))
        self.assertIn("language", [c.args[1] for c in publish_call.call_args_list])
        publish_lobby.assert_any_call(
            "call.language", {"call_id": self.room.call_id, "language": "lg", "source": "auto"}
        )
        notes = [t for t in list_turns(self.room.call_id) if t["kind"] == "language"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["speaker"], "system")
        self.assertIn("Luganda", notes[0]["text"])
        self.assertIn("93%", notes[0]["text"])

    async def test_detection_metrics_are_recorded(self):
        await self.router.on_vote(vote("en", 0.9), PCM, None)
        self.assertEqual(self.room.state.lid_latencies_ms, [120.0])
        self.assertEqual(self.room.state.lid_confidences, [0.9])

    async def test_a_turn_in_two_segments_is_re_read_whole(self):
        """Measured end to end: a pause mid-question split it, and half was answered."""
        first, second = PCM[:32000], PCM[32000:]
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), first, ("VAT yange ntya okugisasula", "lg"))
        await self.router.on_speech_started()  # the second segment: still the claimed turn
        self.assertTrue(self.router.claims_turn())
        await self.router.on_vote(vote("lg", 0.95), second, ("era ebitundu bimeka", "lg"))
        self.brain.handle_external_question.assert_not_awaited()
        await self.router.on_turn_end(first + second, 2)
        await self.settle()
        self.speech.transcribe.assert_called_once_with(pcm16_to_wav(first + second), 16000, "lg", True)
        self.brain.handle_external_question.assert_awaited_once()

    async def test_a_later_vote_back_to_the_language_in_use_cancels_the_switch(self):
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), PCM, None)
        await self.router.on_vote(vote("en", 0.99, text="Sorry, what is the VAT rate?"), PCM, None)
        self.assertFalse(self.router.claims_turn())  # the brain answers it after all
        await self.router.on_turn_end(PCM, 2)
        await self.settle()
        self.assertEqual(self.room.state.locale, "en")
        self.router.interrupt.assert_not_awaited()
        self.brain.handle_external_question.assert_not_awaited()

    async def test_talking_over_the_assistant_stops_it(self):
        self.router.barge_in = AsyncMock()
        await self.router.on_barge_in()
        self.router.barge_in.assert_awaited_once()
        # The brain counts it when the interruption reaches it, not the router.
        self.assertEqual(self.room.state.barge_in_count, 0)

    async def test_no_barge_in_over_a_switch_waiting_for_the_turn(self):
        self.router.barge_in = AsyncMock()
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), PCM, None)  # decided, deferred
        await self.router.on_barge_in()
        self.router.barge_in.assert_not_awaited()

    async def test_no_barge_in_while_an_officer_has_the_call(self):
        self.router.barge_in = AsyncMock()
        self.room.state.mode = "bridged"
        await self.router.on_barge_in()
        self.router.barge_in.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
