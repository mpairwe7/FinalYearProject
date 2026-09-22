"""Unit tests for the call pipeline taps (live captions to the caller)."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from app.receptionist.taps import (
    InputAudioRawFrame,
    LivePartialTranscriptTap,
    OutputTransportMessageUrgentFrame,
    TranscriptTap,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

# 0.6 s at 16 kHz mono int16 — the tap's minimum decodable buffer.
ONE_SECOND_PCM = b"\x01\x00" * 16000


def make_room() -> Any:
    return SimpleNamespace(
        call_id="call_test",
        state=SimpleNamespace(turn_seq=4, turn_words=[], mode="ai"),
    )


class RecordingSpeech:
    """Speech model stub returning a scripted sequence of hypotheses."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls: list[int] = []

    def transcribe(self, pcm: bytes, sample_rate: int, language: str, with_words: bool) -> Any:
        self.calls.append(len(pcm))
        text = self.texts[min(len(self.calls) - 1, len(self.texts) - 1)]
        return SimpleNamespace(text=text)


class CaptureMixin:
    """Collects the frames a tap pushes instead of forwarding them."""

    def attach(self, tap: Any) -> list[Any]:
        pushed: list[Any] = []

        async def push_frame(frame: Any, direction: Any = None) -> None:
            pushed.append(frame)

        tap.push_frame = push_frame  # type: ignore[method-assign]
        return pushed


class TestTranscriptTap(unittest.IsolatedAsyncioTestCase, CaptureMixin):
    async def test_final_transcript_is_captioned_urgently(self):
        room = make_room()
        tap = TranscriptTap(room=room)
        pushed = self.attach(tap)

        await tap.process_frame(TranscriptionFrame(text="What is the VAT rate?", user_id="u", timestamp="0"))

        captions = [f for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)]
        self.assertEqual(len(captions), 1)
        self.assertEqual(
            captions[0].message,
            {
                "type": "caption",
                "speaker": "caller",
                "text": "What is the VAT rate?",
                "final": True,
                "turn_id": 5,
            },
        )


class TestLivePartialTranscriptTap(unittest.IsolatedAsyncioTestCase, CaptureMixin):
    def build(self, texts: list[str], **kwargs: Any):
        room = make_room()
        speech = RecordingSpeech(texts)
        tap = LivePartialTranscriptTap(
            room=room, speech_model=speech, language="en", interval_s=0.0, **kwargs
        )
        return room, speech, tap, self.attach(tap)

    async def speak(self, tap: Any, pcm: bytes = ONE_SECOND_PCM) -> None:
        """Feed one audio frame and let any scheduled decode finish."""
        await tap.process_frame(InputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1))
        if tap._pending is not None:
            await tap._pending

    async def test_emits_interim_caption_while_caller_is_still_speaking(self):
        _room, speech, tap, pushed = self.build(["what is the"])

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)

        captions = [f.message for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)]
        self.assertEqual(
            captions,
            [{"type": "caption", "speaker": "caller", "text": "what is the", "final": False, "turn_id": 5}],
        )
        self.assertEqual(speech.calls, [len(ONE_SECOND_PCM)])

    async def test_each_decode_sees_the_whole_utterance_so_far(self):
        _room, speech, tap, pushed = self.build(["what is", "what is the VAT rate"])

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)
        await self.speak(tap)

        self.assertEqual(speech.calls, [len(ONE_SECOND_PCM), 2 * len(ONE_SECOND_PCM)])
        texts = [f.message["text"] for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)]
        self.assertEqual(texts, ["what is", "what is the VAT rate"])

    async def test_unchanged_hypothesis_is_not_resent(self):
        _room, _speech, tap, pushed = self.build(["what is the VAT rate"])

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)
        await self.speak(tap)

        captions = [f for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)]
        self.assertEqual(len(captions), 1)

    async def test_partial_finishing_after_the_turn_is_dropped(self):
        """A late decode must not overwrite the final transcript on screen."""
        _room, _speech, tap, pushed = self.build(["stale hypothesis"])

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await tap.process_frame(
            InputAudioRawFrame(audio=ONE_SECOND_PCM, sample_rate=16000, num_channels=1)
        )
        pending = tap._pending
        await tap.process_frame(VADUserStoppedSpeakingFrame())
        if pending is not None:
            await pending

        self.assertEqual([f for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)], [])

    async def test_turn_starts_with_the_audio_from_just_before_vad_fired(self):
        """Silero triggers mid-syllable; the pre-roll keeps the opening word."""
        _room, speech, tap, _pushed = self.build(["full sentence"], preroll_s=0.25)
        half_second = b"\x01\x00" * 8000

        await self.speak(tap, pcm=half_second)  # not speaking yet: pre-roll only
        self.assertEqual(speech.calls, [])

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)

        # 0.25 s of pre-roll (trimmed from the 0.5 s seen) plus the 1 s turn.
        self.assertEqual(speech.calls, [int(0.25 * 32000) + len(ONE_SECOND_PCM)])

    async def test_audio_outside_a_speaking_turn_is_ignored(self):
        _room, speech, tap, pushed = self.build(["should not run"])

        await self.speak(tap)

        self.assertEqual(speech.calls, [])
        self.assertEqual([f for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)], [])

    async def test_buffer_shorter_than_the_minimum_is_not_decoded(self):
        _room, speech, tap, _pushed = self.build(["too short"])

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap, pcm=b"\x01\x00" * 800)  # 50 ms

        self.assertEqual(speech.calls, [])

    async def test_utterance_past_the_whisper_window_stops_partials(self):
        _room, speech, tap, _pushed = self.build(["x"], max_audio_s=1.5)

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)
        await self.speak(tap)  # buffer is now 2 s, past the cap

        self.assertEqual(speech.calls, [len(ONE_SECOND_PCM)])

    async def test_new_turn_starts_from_an_empty_buffer(self):
        _room, speech, tap, _pushed = self.build(["first", "second"])

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)
        await tap.process_frame(UserStoppedSpeakingFrame())
        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)

        self.assertEqual(speech.calls, [len(ONE_SECOND_PCM), len(ONE_SECOND_PCM)])

    async def test_disabled_without_a_local_speech_model(self):
        room = make_room()
        tap = LivePartialTranscriptTap(room=room, speech_model=None, language="en")
        pushed = self.attach(tap)

        self.assertFalse(tap.enabled)
        await tap.process_frame(VADUserStartedSpeakingFrame())
        await tap.process_frame(
            InputAudioRawFrame(audio=ONE_SECOND_PCM, sample_rate=16000, num_channels=1)
        )

        # Frames still flow on; only the extra captions are gone.
        self.assertEqual(len(pushed), 2)
        self.assertEqual([f for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)], [])

    async def test_transcription_failure_is_swallowed(self):
        room = make_room()

        class Boom:
            def transcribe(self, *_args: Any, **_kwargs: Any) -> Any:
                raise RuntimeError("model unavailable")

        tap = LivePartialTranscriptTap(
            room=room, speech_model=Boom(), language="en", interval_s=0.0
        )
        pushed = self.attach(tap)

        await tap.process_frame(VADUserStartedSpeakingFrame())
        await self.speak(tap)

        self.assertEqual([f for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)], [])


if __name__ == "__main__":
    unittest.main()
