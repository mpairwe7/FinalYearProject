"""LanguageRouter: what a language decision does to a live call."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app import database as db
from app.receptionist.language import LanguagePolicy, LanguageVote, PolicyConfig
from app.receptionist.phrases import phrase
from app.receptionist.router import EngineSelector, LanguageRouter
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import create_call, init_receptionist_schema, list_turns
from app.speech_service import pcm16_to_wav

ENGINES = {"en": "gemini_live", "sw": "gemini_live", "lg": "cascaded"}
PCM = b"\x10\x00" * 32000


def vote(top: str, p: float, speech_s: float = 3.0, text: str = "") -> LanguageVote:
    rest = [lang for lang in ("en", "sw", "lg") if lang != top]
    return LanguageVote({top: p, **{lang: (1 - p) / 2 for lang in rest}}, top, speech_s, text, latency_ms=120.0)


class FakeHold:
    def __init__(self) -> None:
        self.holding = False
        self.buffered = 0
        self.events: list[str] = []

    def hold(self) -> None:
        self.holding = True
        self.events.append("hold")

    def pin(self) -> None:
        self.holding = True
        self.events.append("pin")

    async def release(self) -> None:
        self.holding = False
        self.events.append("release")

    async def discard(self) -> None:
        self.holding = False
        self.events.append("discard")


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        call_id = f"call_router_{uuid.uuid4().hex[:8]}"
        create_call(call_id, conversation_id=f"conv_{call_id}")
        self.room = CallRoom(call_id=call_id, state=CallState(call_id=call_id, conversation_id=f"conv_{call_id}"))
        self.room.state.engine = "gemini_live"
        self.room.state.languages_used = ["en"]
        self.selector = EngineSelector("gemini_live")
        self.hold = FakeHold()
        self.brain = MagicMock()
        self.brain.handle_external_question = AsyncMock()
        self.brain._say_and_record = AsyncMock()
        self.brain.say_filler = AsyncMock()
        self.gemini = MagicMock()
        self.gemini.queue_frame = AsyncMock()
        self.speech = MagicMock()
        self.speech.transcribe.return_value = SimpleNamespace(text="Nsaba okumanya ku TIN", words=["w"])
        self.outlet = MagicMock()
        self.outlet.send = AsyncMock()
        self.router = LanguageRouter(
            self.room,
            LanguagePolicy(active="en", config=PolicyConfig()),
            self.selector,
            ENGINES,
            self.speech,
            outlet=self.outlet,
            hold_gate=self.hold,
            brain=self.brain,
            gemini_service=self.gemini,
        )
        self.engine_when_interrupted: list[str] = []

        async def interrupt() -> None:
            self.engine_when_interrupted.append(self.selector.active)
            # The branches report the interruption passing (brain / Gemini tap).
            self.router.switch_passed("cascaded")
            self.router.switch_passed("gemini_live")

        self.router.interrupt = AsyncMock(side_effect=interrupt)

    async def settle(self) -> None:
        await asyncio.gather(*list(self.router._reasks))

    async def turn(self, v: LanguageVote, pcm: bytes = PCM, decoded=None, segments: int = 1) -> None:
        """One caller turn: its vote, then its end (when deferred switches run)."""
        await self.router.on_vote(v, pcm, decoded)
        await self.router.on_turn_end(pcm, segments)
        await self.settle()

    def sent(self, kind: str) -> list[dict]:
        return [c.args[0] for c in self.outlet.send.call_args_list if c.args[0].get("type") == kind]

    async def test_the_first_reply_is_held_until_the_language_is_known(self):
        await self.router.on_speech_started()
        self.assertTrue(self.hold.holding)
        await self.router.on_vote(vote("en", 0.92), PCM, None)
        self.assertEqual(self.hold.events, ["hold", "release"])
        self.assertTrue(self.router.policy.locked)
        self.assertEqual(self.sent("language"), [{"type": "language", "language": "en", "source": "auto", "confidence": 0.92}])
        self.router.interrupt.assert_not_awaited()

    async def test_once_locked_nothing_is_held(self):
        await self.router.on_vote(vote("en", 0.92), PCM, None)
        await self.router.on_speech_started()
        self.assertFalse(self.hold.holding)

    async def test_a_weak_first_vote_releases_and_stays_open(self):
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.6), PCM, None)
        self.assertEqual(self.hold.events, ["hold", "release"])
        self.assertFalse(self.router.policy.locked)
        self.assertEqual(self.sent("language"), [])

    async def test_luganda_moves_the_call_and_answers_the_same_question(self):
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), PCM, None)
        # Decided, but the caller may not have finished: nothing moves yet,
        # and Gemini's reply stays held with no timeout.
        self.assertEqual(self.selector.active, "gemini_live")
        self.assertEqual(self.hold.events, ["hold", "pin"])
        await self.router.on_turn_end(PCM, 1)
        await self.settle()
        state = self.room.state
        self.assertEqual(self.hold.events, ["hold", "pin", "discard"])
        self.assertEqual(self.engine_when_interrupted, ["gemini_live"])  # interrupted before the flip
        self.assertEqual((self.selector.active, state.engine, state.locale), ("cascaded", "cascaded", "lg"))
        self.assertEqual(state.language_switches, 1)
        self.brain.say_filler.assert_awaited_once()  # something to hear at once
        self.speech.transcribe.assert_called_once_with(pcm16_to_wav(PCM), 16000, "lg", True)
        self.brain.handle_external_question.assert_awaited_once_with("Nsaba okumanya ku TIN", ["w"])
        captions = self.sent("caption")
        self.assertEqual(captions[0]["speaker"], "caller")
        self.assertEqual(captions[0]["text"], "Nsaba okumanya ku TIN")
        self.assertEqual(self.sent("language")[0]["language"], "lg")

    async def test_the_engine_being_left_may_not_act_while_the_switch_waits(self):
        self.assertTrue(self.router.engine_may_act("gemini_live"))
        self.assertFalse(self.router.engine_may_act("cascaded"))  # not in use
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), PCM, None)
        self.assertFalse(self.router.engine_may_act("gemini_live"))  # decided, turn not over
        await self.router.on_turn_end(PCM, 1)
        await self.settle()
        self.assertFalse(self.router.engine_may_act("gemini_live"))
        self.assertTrue(self.router.engine_may_act("cascaded"))

    async def test_a_switch_within_gemini_does_not_silence_its_tools(self):
        await self.router.on_vote(vote("en", 0.92), PCM, None)
        await self.router.on_vote(vote("sw", 0.95), PCM, None)
        self.assertTrue(self.router.engine_may_act("gemini_live"))

    async def test_the_sentinel_s_luganda_decode_is_reused(self):
        await self.turn(vote("lg", 0.9), decoded=("Nsaba okumanya ku TIN yange", "lg"))
        self.speech.transcribe.assert_not_called()
        self.brain.handle_external_question.assert_awaited_once_with("Nsaba okumanya ku TIN yange", [])

    async def test_back_to_english_asks_gemini_the_question(self):
        pytest.importorskip("pipecat")
        await self.turn(vote("lg", 0.9))
        self.speech.transcribe.return_value = SimpleNamespace(text="What is the VAT rate?", words=[])
        await self.turn(vote("en", 0.95), decoded=("What is the VAT rate?", "en"))
        self.assertEqual(self.selector.active, "gemini_live")
        sent = self.gemini.queue_frame.await_args.args[0]
        self.assertIn("What is the VAT rate?", sent.text)
        self.assertIn("Answer it in English", sent.text)

    async def test_an_explicit_request_is_confirmed_not_answered(self):
        await self.turn(vote("en", 0.95, speech_s=1.4, text="Can we speak Luganda?"))
        self.assertEqual(self.selector.active, "cascaded")
        self.brain._say_and_record.assert_awaited_once_with(phrase("switched", "lg"), kind="notice")
        self.brain.handle_external_question.assert_not_awaited()
        self.speech.transcribe.assert_not_called()
        self.assertEqual(self.room.state.language_source, "explicit")

    async def test_on_screen_choice_on_the_same_engine_tells_gemini(self):
        pytest.importorskip("pipecat")
        await self.router.on_override("sw")
        self.assertEqual(self.selector.active, "gemini_live")
        self.router.interrupt.assert_not_awaited()
        self.assertIn("in Swahili only", self.gemini.queue_frame.await_args.args[0].text)
        self.assertEqual(self.room.state.language_overrides, 1)
        self.assertEqual(self.sent("language")[0]["source"], "override")

    async def test_gemini_follows_swahili_by_itself(self):
        await self.router.on_vote(vote("en", 0.95), PCM, None)
        await self.router.on_vote(vote("sw", 0.95), PCM, None)
        self.assertEqual(self.room.state.locale, "sw")
        self.router.interrupt.assert_not_awaited()
        self.gemini.queue_frame.assert_not_awaited()

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
        await self.router.on_vote(vote("lg", 0.9), first, ("VAT yange ntya okugisasula", "lg"))
        await self.router.on_vote(vote("lg", 0.95), second, ("era ebitundu bimeka", "lg"))
        self.brain.handle_external_question.assert_not_awaited()
        await self.router.on_turn_end(first + second, 2)
        await self.settle()
        self.speech.transcribe.assert_called_once_with(pcm16_to_wav(first + second), 16000, "lg", True)
        self.brain.handle_external_question.assert_awaited_once()

    async def test_a_later_vote_back_to_the_current_engine_cancels_the_switch(self):
        await self.router.on_speech_started()
        await self.router.on_vote(vote("lg", 0.9), PCM, None)
        await self.router.on_vote(vote("en", 0.99, text="Sorry, what is the VAT rate?"), PCM, None)
        await self.router.on_turn_end(PCM, 2)
        await self.settle()
        self.assertEqual(self.selector.active, "gemini_live")
        self.assertEqual(self.hold.events[-1], "release")
        self.brain.handle_external_question.assert_not_awaited()

    async def test_an_on_screen_choice_is_not_deferred(self):
        await self.router.on_override("lg")
        await self.settle()
        self.assertEqual(self.selector.active, "cascaded")


if __name__ == "__main__":
    unittest.main()
