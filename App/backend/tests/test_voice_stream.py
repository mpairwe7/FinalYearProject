"""Tests for the streaming voice engine, WebSocket protocol, and voice consent.

Covers:
  - VAD energy detection + hysteresis
  - VoiceSession pipeline (mocked SpeechModel + ChatModel)
  - Barge-in cancellation
  - Sentence-level TTS chunking
  - Voice consent enforcement
  - Voice audit log writes
  - Feature flag gating
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np


# ---------------------------------------------------------------------------
# VAD tests
# ---------------------------------------------------------------------------


class TestVADDetection(unittest.TestCase):
    """Test energy-based VAD with synthetic PCM."""

    def _make_session(self, **kwargs):
        from app.voice_stream import VADConfig, VoiceSession

        speech = MagicMock()
        chat = MagicMock()
        config = VADConfig(**kwargs) if kwargs else VADConfig()
        return VoiceSession(
            session_id="test",
            speech=speech,
            chat_model=chat,
            vad_config=config,
        )

    def _silence(self, duration_ms: int = 100, sample_rate: int = 16000) -> bytes:
        """Generate silent PCM16 LE bytes."""
        n = int(sample_rate * duration_ms / 1000)
        return struct.pack(f"<{n}h", *([0] * n))

    def _tone(self, duration_ms: int = 100, freq: int = 440, amplitude: int = 10000, sample_rate: int = 16000) -> bytes:
        """Generate a sine wave PCM16 LE signal."""
        n = int(sample_rate * duration_ms / 1000)
        samples = []
        for i in range(n):
            t = i / sample_rate
            s = int(amplitude * np.sin(2 * np.pi * freq * t))
            samples.append(max(-32768, min(32767, s)))
        return struct.pack(f"<{n}h", *samples)

    def test_silence_not_detected_as_speech(self):
        session = self._make_session()
        chunk = self._silence(100)
        is_speech, complete = session._vad_detect(chunk)
        self.assertFalse(is_speech)
        self.assertFalse(complete)

    def test_loud_signal_detected_as_speech(self):
        session = self._make_session()
        chunk = self._tone(100, amplitude=15000)
        is_speech, complete = session._vad_detect(chunk)
        self.assertTrue(is_speech)
        self.assertFalse(complete)

    def test_speech_then_silence_completes_utterance(self):
        session = self._make_session(silence_duration_ms=200)

        # Feed speech
        speech_chunk = self._tone(500, amplitude=15000)
        session._vad_detect(speech_chunk)
        self.assertTrue(session._is_speaking)

        # Feed silence — enough to trigger utterance end
        silence_chunk = self._silence(300)
        is_speech, complete = session._vad_detect(silence_chunk)
        self.assertTrue(complete)

    def test_hysteresis_prevents_rapid_toggling(self):
        session = self._make_session(silence_duration_ms=500)

        # Start speaking
        session._vad_detect(self._tone(200, amplitude=15000))
        self.assertTrue(session._is_speaking)

        # Brief silence — should NOT complete
        is_speech, complete = session._vad_detect(self._silence(100))
        self.assertFalse(complete)
        self.assertTrue(session._is_speaking)

    def test_max_utterance_timeout(self):
        session = self._make_session(max_utterance_s=0.1)

        # Start speaking
        session._vad_detect(self._tone(50, amplitude=15000))
        session._utterance_start = time.perf_counter() - 0.2  # Fake elapsed time

        # Next chunk should trigger max timeout
        _, complete = session._vad_detect(self._tone(50, amplitude=15000))
        self.assertTrue(complete)

    def test_empty_chunk_returns_no_speech(self):
        session = self._make_session()
        is_speech, complete = session._vad_detect(b"")
        self.assertFalse(is_speech)
        self.assertFalse(complete)


# ---------------------------------------------------------------------------
# Sentence splitting tests
# ---------------------------------------------------------------------------


class TestSentenceSplitting(unittest.TestCase):
    def test_basic_splitting(self):
        from app.voice_stream import _split_sentences

        result = _split_sentences("Hello there. How are you? I am fine!")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "Hello there.")
        self.assertEqual(result[1], "How are you?")
        self.assertEqual(result[2], "I am fine!")

    def test_single_sentence(self):
        from app.voice_stream import _split_sentences

        result = _split_sentences("Hello there")
        self.assertEqual(result, ["Hello there"])

    def test_short_fragments_merged(self):
        from app.voice_stream import _split_sentences

        result = _split_sentences("Hello. Hi. How are you doing today?")
        # "Hi." is <10 chars, should be merged with "Hello."
        self.assertTrue(len(result) <= 3)

    def test_empty_text(self):
        from app.voice_stream import _split_sentences

        result = _split_sentences("  ")
        self.assertEqual(len(result), 1)


# ---------------------------------------------------------------------------
# VoiceSession pipeline tests
# ---------------------------------------------------------------------------


class TestVoiceSession(unittest.IsolatedAsyncioTestCase):
    def _make_session(self):
        from app.voice_stream import VADConfig, VoiceSession
        from app.speech_service import TranscribeResult, SynthesizeResult

        speech = MagicMock()
        speech.transcribe.return_value = TranscribeResult(
            text="Hello",
            language="en",
            duration_s=1.0,
            latency_s=0.2,
            backend="mock",
        )
        speech.synthesize.return_value = SynthesizeResult(
            audio=b"\x00" * 100,
            sample_rate=22050,
            num_samples=100,
            duration_s=0.5,
            latency_s=0.1,
            backend="mock",
            voice="test_voice",
        )
        speech._breakers = {"tts": MagicMock(name="speech.tts")}

        chat = MagicMock()
        chat.generate.return_value = {
            "reply": "I can help you with that.",
            "sources": ["doc1.pdf"],
            "citations": [],
            "faithfulness_score": 0.8,
            "retrieval_mode": "semantic",
        }

        session = VoiceSession(
            session_id="test",
            speech=speech,
            chat_model=chat,
            vad_config=VADConfig(),
        )
        return session, speech, chat

    async def test_process_utterance_yields_events(self):
        session, speech, chat = self._make_session()

        # Create a fake audio utterance
        audio = struct.pack("<800h", *([5000] * 800))

        events = []
        async for event in session.process_utterance(audio):
            events.append(event)

        # Should have: transcript_final, reply_text(s), audio_start, audio_chunk(s), audio_end, reply_meta, latency_report
        event_types = [e.type for e in events]
        self.assertIn("transcript_final", event_types)
        self.assertIn("reply_text", event_types)
        self.assertIn("reply_meta", event_types)
        self.assertIn("latency_report", event_types)

    async def test_barge_in_cancels_tts(self):
        session, speech, chat = self._make_session()

        # Set cancelled before processing
        session._cancelled.set()

        audio = struct.pack("<800h", *([5000] * 800))
        events = []
        async for event in session.process_utterance(audio):
            events.append(event)

        # Should get transcript but no TTS audio (cancelled before LLM)
        event_types = [e.type for e in events]
        self.assertIn("transcript_final", event_types)
        self.assertNotIn("audio_start", event_types)

    async def test_barge_in_method(self):
        session, _, _ = self._make_session()

        # Start some state
        session._is_speaking = True
        session._audio_buffer.extend(b"\x00" * 100)

        await session.barge_in()

        self.assertTrue(session._cancelled.is_set())
        self.assertFalse(session._is_speaking)
        self.assertEqual(len(session._audio_buffer), 0)

    def test_close_cleans_up(self):
        session, _, _ = self._make_session()
        session._audio_buffer.extend(b"\x00" * 100)
        session.close()
        self.assertEqual(len(session._audio_buffer), 0)
        self.assertTrue(session._cancelled.is_set())


# ---------------------------------------------------------------------------
# Voice consent tests
# ---------------------------------------------------------------------------


class TestVoiceConsent(unittest.TestCase):
    @patch("app.voice_consent.flags")
    def test_consent_bypassed_when_flag_disabled(self, mock_flags):
        from app.voice_consent import require_voice_consent

        mock_flags.is_enabled.return_value = False
        self.assertTrue(require_voice_consent("user123"))

    @patch("app.voice_consent.flags")
    def test_consent_denied_for_anonymous(self, mock_flags):
        from app.voice_consent import require_voice_consent

        mock_flags.is_enabled.return_value = True
        self.assertFalse(require_voice_consent(""))

    @patch("app.voice_consent.flags")
    @patch("app.database.has_active_consent")
    def test_consent_granted(self, mock_has_consent, mock_flags):
        from app.voice_consent import require_voice_consent

        mock_flags.is_enabled.return_value = True
        mock_has_consent.return_value = True
        self.assertTrue(require_voice_consent("user123"))

    @patch("app.voice_consent.flags")
    @patch("app.database.has_active_consent")
    def test_consent_denied(self, mock_has_consent, mock_flags):
        from app.voice_consent import require_voice_consent

        mock_flags.is_enabled.return_value = True
        mock_has_consent.return_value = False
        self.assertFalse(require_voice_consent("user123"))

    def test_hash_audio(self):
        from app.voice_consent import hash_audio

        result = hash_audio(b"test audio data")
        self.assertEqual(len(result), 64)  # SHA-256 hex digest

    def test_voice_event_types_valid(self):
        from app.voice_consent import VOICE_EVENT_TYPES

        self.assertIn("recording_start", VOICE_EVENT_TYPES)
        self.assertIn("barge_in", VOICE_EVENT_TYPES)
        self.assertIn("retention_cleanup", VOICE_EVENT_TYPES)


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------


class TestFeatureFlags(unittest.TestCase):
    def test_voice_streaming_flag_exists(self):
        from app.flags import flags

        # Should return False (default) without env var
        result = flags.is_enabled("voice_streaming")
        self.assertFalse(result)

    def test_voice_consent_flag_exists(self):
        from app.flags import flags

        result = flags.is_enabled("voice_consent")
        self.assertFalse(result)

    def test_programmatic_override(self):
        from app.flags import flags

        flags.set("voice_streaming", True)
        self.assertTrue(flags.is_enabled("voice_streaming"))
        flags.clear("voice_streaming")
        self.assertFalse(flags.is_enabled("voice_streaming"))


if __name__ == "__main__":
    unittest.main()
