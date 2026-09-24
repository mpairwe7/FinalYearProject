"""LanguageSentinel: segments caller audio on its own VAD and votes per utterance."""

from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pipecat")

from app.receptionist.language import PolicyConfig  # noqa: E402
from app.receptionist.sentinel import LanguageSentinel  # noqa: E402
from app.receptionist.serializer import SetLanguageFrame  # noqa: E402
from app.speech_service import LanguageIdResult  # noqa: E402
from pipecat.audio.vad.vad_analyzer import VADState  # noqa: E402
from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

CHUNK = b"\x10\x00" * 320  # 20 ms at 16 kHz


class ScriptedVAD:
    """Returns a scripted VADState per chunk, then QUIET."""

    def __init__(self, states: list[VADState]) -> None:
        self.states = list(states)

    def set_sample_rate(self, rate: int) -> None:
        self.rate = rate

    async def analyze_audio(self, chunk: bytes) -> VADState:
        return self.states.pop(0) if self.states else VADState.QUIET


class FakeSpeech:
    def __init__(self, lid: LanguageIdResult, text: str = "Nsaba okumanya ku TIN") -> None:
        self.lid = lid
        self.text = text
        self.lid_calls: list[int] = []
        self.transcribe_calls: list[tuple[int, str]] = []

    def identify_language(self, pcm: bytes, sample_rate: int, candidates: tuple[str, ...]) -> LanguageIdResult:
        self.lid_calls.append(len(pcm))
        return self.lid

    def transcribe(self, pcm: bytes, sample_rate: int, language: str, with_words: bool) -> SimpleNamespace:
        assert pcm[:4] == b"RIFF", "call audio must reach transcribe as a WAV"
        self.transcribe_calls.append((len(pcm), language))
        return SimpleNamespace(text=self.text)


class RecordingRouter:
    def __init__(self) -> None:
        self.policy = SimpleNamespace(config=PolicyConfig())
        self.started = 0
        self.votes: list[tuple] = []
        self.overrides: list[str] = []
        self.turns: list[tuple[int, int]] = []
        self.voted = asyncio.Event()
        self.turn_ended = asyncio.Event()

    async def on_speech_started(self) -> None:
        self.started += 1

    async def on_vote(self, vote, pcm, decoded) -> None:
        self.votes.append((vote, pcm, decoded))
        self.voted.set()

    async def on_override(self, language: str) -> None:
        self.overrides.append(language)

    async def on_turn_end(self, pcm: bytes, segments: int) -> None:
        self.turns.append((len(pcm), segments))
        self.turn_ended.set()


def room(mode: str = "ai") -> SimpleNamespace:
    return SimpleNamespace(call_id="c", state=SimpleNamespace(mode=mode, locale="en"))


class SentinelTests(unittest.IsolatedAsyncioTestCase):
    def build(self, states, lid, *, mode="ai", text="Nsaba okumanya ku TIN"):
        router = RecordingRouter()
        speech = FakeSpeech(lid, text)
        sentinel = LanguageSentinel(room(mode), speech, router, ("en", "sw", "lg"),
                                    vad_analyzer=ScriptedVAD(states), preroll_s=0.04)
        pushed: list[tuple] = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append((frame, direction))

        sentinel.push_frame = push_frame  # type: ignore[method-assign]
        sentinel._start()
        return sentinel, router, speech, pushed

    async def feed(self, sentinel, n: int) -> None:
        for _ in range(n):
            await sentinel.process_frame(InputAudioRawFrame(audio=CHUNK, sample_rate=16000, num_channels=1),
                                         FrameDirection.DOWNSTREAM)

    async def wait_vote(self, router) -> None:
        await asyncio.wait_for(router.voted.wait(), timeout=5)

    async def test_an_utterance_is_segmented_and_voted_on(self):
        states = [VADState.QUIET] * 3 + [VADState.SPEAKING] * 5 + [VADState.QUIET]
        lid = LanguageIdResult({"en": 0.1, "sw": 0.05, "lg": 0.85}, "lg", 40.0, 0.2)
        sentinel, router, speech, pushed = self.build(states, lid)
        await self.feed(sentinel, 10)
        await self.wait_vote(router)
        await sentinel._stop()

        self.assertEqual(router.started, 1)
        self.assertEqual(len(pushed), 10)  # every frame passes straight through
        vote, pcm, decoded = router.votes[0]
        # 2 chunks of pre-roll (the one that started speech among them), 4 more
        # while speaking, and the chunk that ended it.
        self.assertEqual(len(pcm), len(CHUNK) * 7)
        self.assertEqual((vote.top, vote.probs["lg"]), ("lg", 0.85))
        self.assertEqual(vote.text, "Nsaba okumanya ku TIN")
        self.assertEqual(decoded, ("Nsaba okumanya ku TIN", "lg"))
        self.assertEqual(speech.transcribe_calls[0][1], "lg")  # decoded in the language it most likely is
        self.assertGreaterEqual(vote.latency_ms, 0.0)

    async def test_a_long_confident_utterance_is_not_decoded(self):
        states = [VADState.SPEAKING] * 300 + [VADState.QUIET]  # 6 s
        lid = LanguageIdResult({"en": 0.97, "sw": 0.02, "lg": 0.01}, "en", 40.0, 6.0)
        sentinel, router, speech, _ = self.build(states, lid)
        await self.feed(sentinel, 301)
        await self.wait_vote(router)
        await sentinel._stop()
        self.assertEqual(speech.transcribe_calls, [])
        self.assertEqual(router.votes[0][0].text, "")

    async def test_nothing_is_analysed_while_an_officer_has_the_call(self):
        states = [VADState.SPEAKING] * 5 + [VADState.QUIET]
        lid = LanguageIdResult({"en": 1.0, "sw": 0.0, "lg": 0.0}, "en", 1.0, 1.0)
        sentinel, router, speech, pushed = self.build(states, lid, mode="bridged")
        await self.feed(sentinel, 6)
        await asyncio.sleep(0.1)
        await sentinel._stop()
        self.assertEqual((router.started, router.votes), (0, []))
        self.assertEqual(len(pushed), 6)

    async def test_the_on_screen_choice_becomes_an_override_and_stops_here(self):
        lid = LanguageIdResult({}, "", 0.0, 0.0, error="x")
        sentinel, router, _, pushed = self.build([], lid)
        await sentinel.process_frame(SetLanguageFrame(language="sw"), FrameDirection.DOWNSTREAM)
        await sentinel._stop()
        self.assertEqual(router.overrides, ["sw"])
        self.assertEqual(pushed, [])

    async def test_keyword_mode_never_runs_acoustic_id(self):
        states = [VADState.SPEAKING] * 5 + [VADState.QUIET]
        lid = LanguageIdResult({"en": 1.0}, "en", 1.0, 1.0)
        with patch.dict(os.environ, {"RECEPTIONIST_LID_METHOD": "keyword"}):
            sentinel, router, speech, _ = self.build(states, lid, text="Can we speak Luganda?")
        await self.feed(sentinel, 6)
        await self.wait_vote(router)
        await sentinel._stop()
        self.assertEqual(speech.lid_calls, [])
        vote = router.votes[0][0]
        self.assertEqual((vote.top, vote.text), ("", "Can we speak Luganda?"))

    async def test_a_switch_interruption_is_marked_as_one(self):
        lid = LanguageIdResult({}, "", 0.0, 0.0, error="x")
        sentinel, _, _, pushed = self.build([], lid)
        await sentinel.interrupt_for_switch()
        await sentinel._stop()
        frames = [f for f, _ in pushed]
        self.assertEqual([type(f) for f in frames], [InterruptionFrame, InterruptionFrame])
        self.assertTrue(all(f.metadata["language_switch"] for f in frames))
        self.assertEqual({d for _, d in pushed}, {FrameDirection.DOWNSTREAM, FrameDirection.UPSTREAM})


    async def test_segments_close_to_each_other_are_one_turn(self):
        # Two segments with a pause the VAD calls the end of speech, then silence.
        states = ([VADState.SPEAKING] * 5 + [VADState.QUIET] * 3 + [VADState.SPEAKING] * 5
                  + [VADState.QUIET])
        lid = LanguageIdResult({"en": 0.1, "sw": 0.05, "lg": 0.85}, "lg", 40.0, 0.2)
        with patch.dict(os.environ, {"RECEPTIONIST_TURN_TIMEOUT_S": "0.3"}):
            sentinel, router, _, _ = self.build(states, lid)
        await self.feed(sentinel, 14)
        await asyncio.wait_for(router.turn_ended.wait(), timeout=5)
        await sentinel._stop()
        self.assertEqual(len(router.votes), 2)  # voted per segment
        self.assertEqual(len(router.turns), 1)  # but one turn
        pcm_len, segments = router.turns[0]
        self.assertEqual(segments, 2)
        self.assertEqual(pcm_len, sum(len(v[1]) for v in router.votes))


if __name__ == "__main__":
    unittest.main()
