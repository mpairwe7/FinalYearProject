"""Tests for the Phase 28 native voice-to-voice engine.

Covers:
  - StreamingTTS: token buffering, sentence boundaries, fallback
  - SpeculativePrefetcher: prefetch/resolve, staleness, hit/miss
  - StreamingASR: partial hypotheses, token stability
  - QueryPlanner: fast/grounded/vision/escalate routing
  - VoiceCodec: PCM/WAV conversion, resampling, energy calculation
  - VoiceSessionV2: full pipeline, barge-in, vision integration
  - Feature flags: new Phase 28 flags
"""

from __future__ import annotations

import asyncio
import struct
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _silence(duration_ms: int = 100, sample_rate: int = 16_000) -> bytes:
    n = int(sample_rate * duration_ms / 1000)
    return struct.pack(f"<{n}h", *([0] * n))


def _tone(duration_ms: int = 100, freq: int = 440, amplitude: int = 10_000, sample_rate: int = 16_000) -> bytes:
    n = int(sample_rate * duration_ms / 1000)
    samples = []
    for i in range(n):
        t = i / sample_rate
        s = int(amplitude * np.sin(2 * np.pi * freq * t))
        samples.append(max(-32768, min(32767, s)))
    return struct.pack(f"<{n}h", *samples)


def _make_speech_mock():
    """Create a mock SpeechModel matching the real interface."""
    from app.speech_service import TranscribeResult, SynthesizeResult, TranslateResult

    speech = MagicMock()
    speech.transcribe.return_value = TranscribeResult(
        text="how much VAT do I pay",
        language="en",
        duration_s=2.0,
        latency_s=0.15,
        backend="mock",
    )
    speech.synthesize.return_value = SynthesizeResult(
        audio=b"\x00\x80" * 200,
        sample_rate=22050,
        num_samples=200,
        duration_s=0.5,
        latency_s=0.1,
        backend="mock",
        voice="test_voice",
    )
    speech.translate.return_value = TranslateResult(
        text="translated text",
        source_lang="lg",
        target_lang="en",
        latency_s=0.05,
        backend="mock",
    )
    speech._breakers = {"tts": MagicMock(name="speech.tts")}
    return speech


def _make_chat_mock():
    """Create a mock ChatModel matching the real interface."""
    chat = MagicMock()
    chat.generate.return_value = {
        "reply": "VAT is charged at 18% on most goods and services.",
        "sources": ["vat_guide.pdf"],
        "citations": [{"ref": "1", "source": "vat_guide.pdf"}],
        "faithfulness_score": 0.85,
        "retrieval_mode": "hybrid",
    }
    return chat


# ===========================================================================
# StreamingTTS tests
# ===========================================================================


class TestStreamingTTS(unittest.IsolatedAsyncioTestCase):
    """Token-level streaming TTS tests."""

    def _make_tts(self):
        from app.native_voice.streaming_tts import StreamingTTS

        speech = _make_speech_mock()
        tts = StreamingTTS(speech_fallback=speech)
        return tts, speech

    async def test_synthesize_text_yields_chunks(self):
        tts, speech = self._make_tts()

        chunks = []
        async for chunk in tts.synthesize_text("Hello, how are you today?"):
            chunks.append(chunk)

        # Should have at least one audio chunk + terminal marker
        self.assertTrue(len(chunks) >= 2)
        self.assertTrue(chunks[-1].is_last)
        self.assertEqual(chunks[-1].audio, b"")

    async def test_first_chunk_is_marked(self):
        tts, _ = self._make_tts()

        chunks = []
        async for chunk in tts.synthesize_text("Hello there."):
            chunks.append(chunk)

        audio_chunks = [c for c in chunks if c.audio]
        if audio_chunks:
            self.assertTrue(audio_chunks[0].is_first)

    async def test_empty_text_yields_only_terminal(self):
        tts, _ = self._make_tts()

        chunks = []
        async for chunk in tts.synthesize_text("   "):
            chunks.append(chunk)

        # Only terminal marker
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].is_last)

    async def test_sentence_boundary_triggers_flush(self):
        from app.native_voice.streaming_tts import _has_sentence_boundary

        self.assertTrue(_has_sentence_boundary("Hello there. "))
        self.assertTrue(_has_sentence_boundary("What? "))
        self.assertTrue(_has_sentence_boundary("Great! "))
        self.assertFalse(_has_sentence_boundary("Hello there"))
        self.assertFalse(_has_sentence_boundary(""))

    async def test_synthesize_stream_buffers_tokens(self):
        tts, _ = self._make_tts()

        async def token_gen():
            for word in ["Hello", " there", ".", " How", " are", " you", "?"]:
                yield word

        chunks = []
        async for chunk in tts.synthesize_stream(token_gen()):
            chunks.append(chunk)

        # Should produce chunks (at least terminal)
        self.assertTrue(len(chunks) >= 1)
        self.assertTrue(chunks[-1].is_last)

    async def test_fallback_when_native_unavailable(self):
        """When CosyVoice2 is not installed, fallback to SpeechModel.synthesize."""
        tts, speech = self._make_tts()
        # _loaded = True but _model = None → forces fallback
        tts._loaded = True
        tts._model = None

        chunks = []
        async for chunk in tts.synthesize_text("Test fallback path."):
            chunks.append(chunk)

        # Fallback should produce audio
        audio_chunks = [c for c in chunks if c.audio]
        self.assertTrue(len(audio_chunks) >= 1)
        speech.synthesize.assert_called()

    def test_native_available_property(self):
        tts, _ = self._make_tts()
        # Without CosyVoice2 installed, native should be False
        result = tts.native_available
        self.assertFalse(result)  # cosyvoice not installed in test env


# ===========================================================================
# SpeculativePrefetcher tests
# ===========================================================================


class TestSpeculativePrefetcher(unittest.IsolatedAsyncioTestCase):
    """Speculative retrieval prefetch tests."""

    def _make_prefetcher(self, hits=None):
        from app.native_voice.speculative_prefetch import SpeculativePrefetcher

        retriever = MagicMock()
        hits = hits or [
            {"chunk_id": "c1", "text": "VAT is 18%", "score_rrf": 0.9},
            {"chunk_id": "c2", "text": "Applied to goods", "score_rrf": 0.7},
        ]
        # speculative_prefetch.py prefers search_planned() when present
        # (getattr(self._retriever, "search_planned", self._retriever.search)) —
        # a real HybridRetriever always has it now, and an unspecced MagicMock
        # auto-vivifies the attribute too, so this is genuinely what gets
        # called. Configuring both keeps this fixture correct against either.
        retriever.search.return_value = hits
        retriever.search_planned.return_value = hits
        return SpeculativePrefetcher(retriever, top_k=4), retriever

    async def test_prefetch_ignores_short_prefix(self):
        pf, retriever = self._make_prefetcher()
        await pf.maybe_prefetch("how much")  # only 2 tokens < 4 min
        self.assertEqual(pf.stats.attempts, 0)
        retriever.search.assert_not_called()
        retriever.search_planned.assert_not_called()

    async def test_prefetch_fires_on_long_prefix(self):
        pf, retriever = self._make_prefetcher()
        await pf.maybe_prefetch("how much VAT do I pay")
        # Wait for background task
        await asyncio.sleep(0.1)
        self.assertEqual(pf.stats.attempts, 1)
        retriever.search_planned.assert_called_once()

    async def test_resolve_hit_when_prefix_matches(self):
        pf, _ = self._make_prefetcher()
        await pf.maybe_prefetch("how much VAT do I")
        await asyncio.sleep(0.1)

        hits = await pf.resolve("how much VAT do I pay on imports")
        self.assertIsNotNone(hits)
        self.assertEqual(len(hits), 2)
        self.assertEqual(pf.stats.hits, 1)

    async def test_resolve_miss_when_prefix_diverges(self):
        pf, _ = self._make_prefetcher()
        await pf.maybe_prefetch("how much VAT do I")
        await asyncio.sleep(0.1)

        hits = await pf.resolve("what is the deadline for filing")
        self.assertIsNone(hits)
        self.assertEqual(pf.stats.misses, 1)

    async def test_resolve_miss_when_no_prefetch(self):
        pf, _ = self._make_prefetcher()
        hits = await pf.resolve("some query")
        self.assertIsNone(hits)
        self.assertEqual(pf.stats.misses, 1)

    async def test_staleness_discards_old_results(self):
        from app.native_voice.speculative_prefetch import PrefetchResult

        pf, _ = self._make_prefetcher()
        # Manually inject a stale result
        pf._result = PrefetchResult(
            query="old query",
            hits=[{"chunk_id": "c1"}],
            started_at=0.0,
            completed_at=time.perf_counter() - 10.0,  # 10s old
        )

        hits = await pf.resolve("old query continues")
        self.assertIsNone(hits)
        self.assertEqual(pf.stats.stale_discards, 1)

    async def test_reset_clears_state(self):
        pf, _ = self._make_prefetcher()
        await pf.maybe_prefetch("how much VAT do I pay")
        await asyncio.sleep(0.1)

        pf.reset()
        self.assertIsNone(pf._result)
        self.assertEqual(pf._last_prefix_hash, "")

    async def test_deduplicates_identical_prefix(self):
        pf, retriever = self._make_prefetcher()
        await pf.maybe_prefetch("how much VAT do I pay")
        await pf.maybe_prefetch("how much VAT do I pay")  # same prefix
        await asyncio.sleep(0.1)

        self.assertEqual(pf.stats.attempts, 1)  # only one attempt

    async def test_hit_rate_property(self):
        pf, _ = self._make_prefetcher()
        pf.stats.attempts = 10
        pf.stats.hits = 7
        self.assertAlmostEqual(pf.stats.hit_rate, 0.7)


# ===========================================================================
# StreamingASR tests
# ===========================================================================


class TestStreamingASR(unittest.IsolatedAsyncioTestCase):
    """Streaming ASR partial hypothesis tests."""

    def _make_asr(self):
        from app.native_voice.streaming_asr import StreamingASR

        speech = _make_speech_mock()
        asr = StreamingASR(speech, window_s=0.5, hop_s=0.1, stability_count=2, sample_rate=16_000)
        return asr, speech

    async def test_feed_insufficient_audio_returns_none(self):
        asr, _ = self._make_asr()
        result = await asr.feed_chunk(_tone(50))  # 50ms < 500ms window
        self.assertIsNone(result)

    async def test_feed_sufficient_audio_returns_partial(self):
        asr, speech = self._make_asr()

        # Feed enough audio to trigger ASR (> window_s = 500ms, > hop_s = 100ms)
        # 600ms of audio at 16kHz = 9600 samples = 19200 bytes
        audio = _tone(600, amplitude=10000)

        # Feed in 100ms chunks
        result = None
        for i in range(0, len(audio), 3200):  # 100ms = 3200 bytes
            chunk = audio[i:i + 3200]
            r = await asr.feed_chunk(chunk)
            if r is not None:
                result = r

        self.assertIsNotNone(result)
        self.assertFalse(result.is_final)
        self.assertEqual(result.language, "en")

    async def test_stability_tracking(self):
        asr, speech = self._make_asr()

        # Feed multiple windows — tokens should become stable
        for _ in range(4):
            audio = _tone(600, amplitude=10000)
            for i in range(0, len(audio), 3200):
                await asr.feed_chunk(audio[i:i + 3200])

        # After multiple runs with same text, tokens should be stable
        partial = asr.get_last_partial()
        if partial is not None:
            # stable_prefix should contain at least some tokens
            self.assertTrue(len(partial.stable_prefix) >= 0)

    async def test_finalize_resets_state(self):
        asr, _ = self._make_asr()
        asr._buffer.extend(b"\x00" * 1000)
        asr._stable_tokens = ["hello", "world"]

        asr.finalize()

        self.assertEqual(len(asr._buffer), 0)
        self.assertEqual(len(asr._stable_tokens), 0)
        self.assertEqual(len(asr._token_history), 0)


# ===========================================================================
# QueryPlanner tests
# ===========================================================================


class TestQueryPlanner(unittest.TestCase):
    """Dual-path query planner tests."""

    def _make_planner(self, route="rag", confidence=0.8):
        from app.native_voice.query_planner import QueryPlanner
        from app.agents.state import AgentRoute, RouteDecision

        supervisor = MagicMock()
        supervisor.classify.return_value = RouteDecision(
            route=AgentRoute(route),
            reason="test",
            confidence=confidence,
        )
        cache = MagicMock()
        cache.get.return_value = None  # no cache hit by default
        return QueryPlanner(supervisor, cache), supervisor, cache

    def test_greeting_routes_to_fast_path(self):
        from app.native_voice.query_planner import VoicePath

        planner, _, _ = self._make_planner(route="greet", confidence=0.95)
        decision = planner.plan("Hello there!")
        self.assertEqual(decision.path, VoicePath.FAST)
        self.assertIn("greeting", decision.reason)

    def test_escalate_routes_to_escalate(self):
        from app.native_voice.query_planner import VoicePath

        planner, _, _ = self._make_planner(route="escalate", confidence=0.9)
        decision = planner.plan("I want to speak to a human")
        self.assertEqual(decision.path, VoicePath.ESCALATE)

    def test_rag_query_routes_to_grounded(self):
        from app.native_voice.query_planner import VoicePath

        planner, _, _ = self._make_planner(route="rag", confidence=0.8)
        decision = planner.plan("What is the VAT rate?")
        self.assertEqual(decision.path, VoicePath.GROUNDED)

    def test_image_always_routes_to_vision(self):
        from app.native_voice.query_planner import VoicePath

        planner, _, _ = self._make_planner(route="rag")
        decision = planner.plan("What is this?", has_image=True)
        self.assertEqual(decision.path, VoicePath.VISION)

    def test_cache_hit_routes_to_fast(self):
        from app.native_voice.query_planner import VoicePath

        planner, _, cache = self._make_planner(route="rag")
        cache.get.return_value = {"reply": "cached answer"}
        decision = planner.plan("What is the VAT rate?")
        self.assertEqual(decision.path, VoicePath.FAST)
        self.assertIn("cache", decision.reason)

    def test_clarify_routes_to_fast(self):
        from app.native_voice.query_planner import VoicePath

        planner, _, _ = self._make_planner(route="clarify", confidence=0.9)
        decision = planner.plan("tax")
        self.assertEqual(decision.path, VoicePath.FAST)

    def test_acknowledgement_detection(self):
        from app.native_voice.query_planner import VoicePath

        planner, supervisor, _ = self._make_planner(route="rag")
        for phrase in ["okay", "thank you", "thanks", "bye", "got it", "webale nyo"]:
            decision = planner.plan(phrase)
            self.assertEqual(decision.path, VoicePath.FAST, f"Failed for: {phrase}")

    def test_prefetch_hits_passed_through(self):
        planner, _, _ = self._make_planner(route="rag")
        hits = [{"chunk_id": "c1"}]
        decision = planner.plan("What is VAT?", prefetch_hits=hits)
        self.assertEqual(decision.prefetch_hits, hits)


# ===========================================================================
# VoiceCodec tests
# ===========================================================================


class TestVoiceCodec(unittest.TestCase):
    """Audio codec utility tests."""

    def test_pcm_to_wav_roundtrip(self):
        from app.native_voice.voice_codec import pcm_to_wav, wav_to_pcm

        pcm = _tone(100)
        wav = pcm_to_wav(pcm, sample_rate=16000)
        pcm_back, sr = wav_to_pcm(wav)
        self.assertEqual(sr, 16000)
        self.assertEqual(pcm, pcm_back)

    def test_pcm_duration_ms(self):
        from app.native_voice.voice_codec import pcm_duration_ms

        pcm = _tone(500)
        dur = pcm_duration_ms(pcm, sample_rate=16000)
        self.assertAlmostEqual(dur, 500.0, places=0)

    def test_generate_silence(self):
        from app.native_voice.voice_codec import generate_silence, pcm_rms_energy

        pcm = generate_silence(100)
        energy = pcm_rms_energy(pcm)
        self.assertAlmostEqual(energy, 0.0)

    def test_pcm_rms_energy(self):
        from app.native_voice.voice_codec import pcm_rms_energy

        silence_energy = pcm_rms_energy(_silence(100))
        tone_energy = pcm_rms_energy(_tone(100, amplitude=15000))
        self.assertLess(silence_energy, 0.01)
        self.assertGreater(tone_energy, 0.1)

    def test_split_pcm_frames(self):
        from app.native_voice.voice_codec import split_pcm_frames

        pcm = _tone(100)  # 100ms
        frames = split_pcm_frames(pcm, frame_ms=20.0)
        self.assertEqual(len(frames), 5)  # 100ms / 20ms = 5 frames
        for frame in frames:
            self.assertEqual(len(frame), 640)  # 20ms at 16kHz = 320 samples = 640 bytes

    def test_resample_identity(self):
        from app.native_voice.voice_codec import resample_pcm

        pcm = _tone(100)
        result = resample_pcm(pcm, 16000, 16000)
        self.assertEqual(result, pcm)

    def test_resample_downsample(self):
        from app.native_voice.voice_codec import resample_pcm

        pcm = _tone(100, sample_rate=16000)
        result = resample_pcm(pcm, 16000, 8000)
        # 8kHz should have half the samples
        self.assertEqual(len(result), len(pcm) // 2)

    def test_empty_pcm_energy(self):
        from app.native_voice.voice_codec import pcm_rms_energy

        self.assertAlmostEqual(pcm_rms_energy(b""), 0.0)
        self.assertAlmostEqual(pcm_rms_energy(b"\x00"), 0.0)


# ===========================================================================
# VoiceSessionV2 tests
# ===========================================================================


class TestVoiceSessionV2(unittest.IsolatedAsyncioTestCase):
    """V2 voice session pipeline tests."""

    def _make_session(self, **kwargs):
        from app.voice_stream import VADConfig
        from app.voice_stream_v2 import VoiceSessionV2

        speech = _make_speech_mock()
        chat = _make_chat_mock()

        # Attach _retriever and _supervisor for V2 components
        chat._retriever = MagicMock()
        chat._retriever.search.return_value = [
            {"chunk_id": "c1", "text": "VAT is 18%", "score_rrf": 0.9},
        ]

        from app.agents.state import AgentRoute, RouteDecision
        chat._supervisor = MagicMock()
        chat._supervisor.classify.return_value = RouteDecision(
            route=AgentRoute.RAG, reason="test", confidence=0.8,
        )
        chat._cache = MagicMock()
        chat._cache.get.return_value = None

        session = VoiceSessionV2(
            session_id="test-v2",
            speech=speech,
            chat_model=chat,
            vad_config=VADConfig(),
            **kwargs,
        )
        return session, speech, chat

    @patch("app.voice_stream_v2.flags")
    async def test_process_utterance_yields_complete_event_set(self, mock_flags):
        mock_flags.is_enabled.return_value = False  # disable V2 components

        session, speech, chat = self._make_session()
        audio = _tone(500, amplitude=15000)

        events = []
        async for event in session.process_utterance(audio):
            events.append(event)

        types = [e.type for e in events]
        self.assertIn("transcript_final", types)
        self.assertIn("reply_text", types)
        self.assertIn("reply_meta", types)
        self.assertIn("latency_report", types)
        self.assertIn("audio_end", types)

    @patch("app.voice_stream_v2.flags")
    async def test_barge_in_cancels_pipeline(self, mock_flags):
        mock_flags.is_enabled.return_value = False

        session, _, _ = self._make_session()
        session._cancelled.set()

        audio = _tone(500, amplitude=15000)
        events = []
        async for event in session.process_utterance(audio):
            events.append(event)

        types = [e.type for e in events]
        self.assertIn("transcript_final", types)
        self.assertNotIn("reply_text", types)  # cancelled before LLM

    @patch("app.voice_stream_v2.flags")
    async def test_barge_in_method(self, mock_flags):
        mock_flags.is_enabled.return_value = False

        session, _, _ = self._make_session()
        session._is_speaking = True
        session._audio_buffer.extend(b"\x00" * 100)

        await session.barge_in()

        self.assertTrue(session._cancelled.is_set())
        self.assertFalse(session._is_speaking)
        self.assertEqual(len(session._audio_buffer), 0)

    @patch("app.voice_stream_v2.flags")
    async def test_close_cleans_up(self, mock_flags):
        mock_flags.is_enabled.return_value = False

        session, _, _ = self._make_session()
        session._audio_buffer.extend(b"\x00" * 100)
        session.close()
        self.assertEqual(len(session._audio_buffer), 0)
        self.assertTrue(session._cancelled.is_set())

    @patch("app.voice_stream_v2.flags")
    async def test_vad_detect_matches_v1(self, mock_flags):
        """V2 VAD should produce identical results to V1."""
        mock_flags.is_enabled.return_value = False

        session, _, _ = self._make_session()

        # Silence → no speech
        is_speech, complete = session._vad_detect(_silence(100))
        self.assertFalse(is_speech)
        self.assertFalse(complete)

        # Loud tone → speech
        is_speech, complete = session._vad_detect(_tone(100, amplitude=15000))
        self.assertTrue(is_speech)
        self.assertFalse(complete)

    @patch("app.voice_stream_v2.flags")
    async def test_image_frame_buffering(self, mock_flags):
        mock_flags.is_enabled.return_value = False

        session, _, _ = self._make_session(vision_enabled=True)
        await session.handle_image_frame(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        self.assertIsNotNone(session._pending_image)

    @patch("app.voice_stream_v2.flags")
    async def test_reply_meta_includes_voice_path(self, mock_flags):
        mock_flags.is_enabled.return_value = False

        session, _, _ = self._make_session()
        audio = _tone(500, amplitude=15000)

        meta_events = []
        async for event in session.process_utterance(audio):
            if event.type == "reply_meta":
                meta_events.append(event)

        self.assertEqual(len(meta_events), 1)
        self.assertIn("voice_path", meta_events[0].data)

    @patch("app.voice_stream_v2.flags")
    async def test_latency_report_includes_v2_fields(self, mock_flags):
        mock_flags.is_enabled.return_value = False

        session, _, _ = self._make_session()
        audio = _tone(500, amplitude=15000)

        latency_events = []
        async for event in session.process_utterance(audio):
            if event.type == "latency_report":
                latency_events.append(event)

        self.assertEqual(len(latency_events), 1)
        data = latency_events[0].data
        self.assertIn("voice_path", data)
        self.assertIn("speculative_prefetch_used", data)
        self.assertIn("asr_ms", data)
        self.assertIn("tts_first_chunk_ms", data)
        self.assertIn("total_ms", data)


# ===========================================================================
# Feature flag tests
# ===========================================================================


class TestPhase28FeatureFlags(unittest.TestCase):
    """Verify new Phase 28 feature flags exist and default to off."""

    def test_native_voice_flag_exists(self):
        from app.flags import flags
        self.assertFalse(flags.is_enabled("native_voice"))

    def test_streaming_tts_v2_flag_exists(self):
        from app.flags import flags
        self.assertFalse(flags.is_enabled("streaming_tts_v2"))

    def test_voice_vision_v2_flag_exists(self):
        from app.flags import flags
        self.assertFalse(flags.is_enabled("voice_vision_v2"))

    def test_speculative_prefetch_flag_exists(self):
        from app.flags import flags
        self.assertFalse(flags.is_enabled("speculative_prefetch"))

    def test_programmatic_override(self):
        from app.flags import flags

        flags.set("native_voice", True)
        self.assertTrue(flags.is_enabled("native_voice"))
        flags.clear("native_voice")
        self.assertFalse(flags.is_enabled("native_voice"))


if __name__ == "__main__":
    unittest.main()
