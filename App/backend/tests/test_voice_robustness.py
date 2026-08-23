"""Voice pipeline robustness: MT degradation is surfaced (never silently
wrong-language), the LLM stage has a hard deadline, all-sentence TTS failure
is announced, TTS phrase caching works, and the Sunbird client retries with
backoff + account failover."""

from __future__ import annotations

import io
import struct
import time
import unittest
from unittest.mock import MagicMock, patch

from app.speech_service import SpeechModel, SynthesizeResult, TranscribeResult


def _utterance() -> bytes:
    return struct.pack("<800h", *([5000] * 800))


def _speech_mock(language: str = "en") -> MagicMock:
    speech = MagicMock()
    speech.transcribe.return_value = TranscribeResult(
        text="Nsaba onyambe ku musolo" if language == "lg" else "Hello",
        language=language,
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
    return speech


def _chat_mock() -> MagicMock:
    chat = MagicMock()
    chat.generate.return_value = {
        "reply": "I can help you with that.",
        "sources": [],
        "citations": [],
        "faithfulness_score": 0.8,
        "retrieval_mode": "semantic",
    }
    return chat


def _session(speech: MagicMock, chat: MagicMock):
    from app.voice_stream import VADConfig, VoiceSession

    return VoiceSession(
        session_id="test",
        speech=speech,
        chat_model=chat,
        vad_config=VADConfig(),
    )


class TestMtDegradation(unittest.IsolatedAsyncioTestCase):
    async def test_inbound_mt_failure_keeps_language_honest(self) -> None:
        from app.speech_service import TranslateResult

        speech = _speech_mock(language="lg")
        speech.translate.return_value = TranslateResult(
            text="", source_lang="lg", target_lang="en",
            latency_s=0.1, backend="mock", error="down",
        )
        chat = _chat_mock()
        session = _session(speech, chat)

        events = [e async for e in session.process_utterance(_utterance())]

        kinds = [e.type for e in events]
        self.assertIn("mt_degraded", kinds)
        degraded = next(e for e in events if e.type == "mt_degraded")
        self.assertEqual(degraded.data["direction"], "lg-en")
        # The LLM must receive the ORIGINAL text labelled with its real
        # locale — never Luganda mislabelled as English.
        self.assertEqual(chat.generate.call_args.kwargs["locale"], "lg")
        self.assertEqual(
            chat.generate.call_args.kwargs["message"], "Nsaba onyambe ku musolo"
        )
        meta = next(e for e in events if e.type == "reply_meta")
        self.assertIn("lg-en", meta.data["mt_degraded"])

    async def test_outbound_mt_failure_speaks_english_voice(self) -> None:
        from app.speech_service import TranslateResult

        speech = _speech_mock(language="lg")

        def _translate(text, source, target):
            if source == "lg":  # inbound works
                return TranslateResult(
                    text="Please help me with tax", source_lang="lg",
                    target_lang="en", latency_s=0.1, backend="mock",
                )
            raise RuntimeError("MT en->lg down")  # outbound fails

        speech.translate.side_effect = _translate
        chat = _chat_mock()
        session = _session(speech, chat)

        events = [e async for e in session.process_utterance(_utterance())]

        degraded = [e for e in events if e.type == "mt_degraded"]
        self.assertEqual([d.data["direction"] for d in degraded], ["en-lg"])
        # An English reply must be spoken by the ENGLISH voice, not the
        # Luganda one mangling it.
        tts_langs = {c.args[2] for c in speech.synthesize.call_args_list}
        self.assertEqual(tts_langs, {"en"})
        meta = next(e for e in events if e.type == "reply_meta")
        self.assertEqual(meta.data["reply_language"], "en")

    async def test_mt_retry_recovers_transient_failure(self) -> None:
        from app.speech_service import TranslateResult
        from app.voice_stream import _translate_with_retry

        speech = MagicMock()
        good = TranslateResult(
            text="ok", source_lang="lg", target_lang="en",
            latency_s=0.1, backend="mock",
        )
        speech.translate.side_effect = [RuntimeError("blip"), good]
        result = await _translate_with_retry(speech, "text", "lg", "en")
        self.assertIs(result, good)
        self.assertEqual(speech.translate.call_count, 2)

        speech.translate.side_effect = RuntimeError("hard down")
        self.assertIsNone(await _translate_with_retry(speech, "text", "lg", "en"))


class TestLlmDeadline(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_llm_yields_recoverable_error(self) -> None:
        speech = _speech_mock()
        chat = MagicMock()
        chat.generate = lambda **kwargs: time.sleep(0.5) or {}  # real fn → executor
        session = _session(speech, chat)

        with patch("app.voice_stream._VOICE_LLM_DEADLINE_S", 0.1):
            events = [e async for e in session.process_utterance(_utterance())]

        errors = [e for e in events if e.type == "error"]
        self.assertTrue(errors)
        self.assertEqual(errors[0].data["stage"], "llm")
        self.assertTrue(errors[0].data["recoverable"])


class TestTtsDegradation(unittest.IsolatedAsyncioTestCase):
    async def test_all_sentences_failing_announces_text_only(self) -> None:
        speech = _speech_mock()
        speech.synthesize.side_effect = RuntimeError("tts down")
        chat = _chat_mock()
        session = _session(speech, chat)

        events = [e async for e in session.process_utterance(_utterance())]

        kinds = [e.type for e in events]
        self.assertIn("tts_degraded", kinds)
        self.assertIn("reply_text", kinds)  # text still delivered
        self.assertNotIn("audio_start", kinds)


class TestTtsPhraseCache(unittest.TestCase):
    def _bare_model(self) -> SpeechModel:
        import threading
        from collections import OrderedDict

        model = SpeechModel.__new__(SpeechModel)
        model.enabled = True
        model._tts_cache = OrderedDict()
        model._tts_cache_lock = threading.Lock()
        return model

    def test_repeated_phrase_served_from_cache(self) -> None:
        model = self._bare_model()
        canned = SynthesizeResult(
            audio=b"RIFF" + b"\x00" * 96, sample_rate=22050, num_samples=100,
            duration_s=0.5, latency_s=0.4, backend="piper", voice="v",
        )
        with patch.object(SpeechModel, "_synthesize_uncached", return_value=canned) as synth:
            first = model.synthesize("Hello, and welcome!", voice="v", language="en")
            second = model.synthesize("Hello, and welcome!", voice="v", language="en")

        synth.assert_called_once()
        self.assertEqual(first.backend, "piper")
        self.assertEqual(second.backend, "piper+cache")
        self.assertEqual(second.latency_s, 0.0)
        self.assertEqual(second.audio, canned.audio)

    def test_failures_are_not_cached(self) -> None:
        model = self._bare_model()
        failed = SynthesizeResult(
            audio=b"", sample_rate=0, num_samples=0, duration_s=0.0,
            latency_s=0.0, backend="error", voice="v", error="All TTS backends failed",
        )
        with patch.object(SpeechModel, "_synthesize_uncached", return_value=failed) as synth:
            model.synthesize("hi there", voice="v", language="en")
            model.synthesize("hi there", voice="v", language="en")
        self.assertEqual(synth.call_count, 2)


class TestAudioSniff(unittest.TestCase):
    def test_containers_accepted_junk_rejected(self) -> None:
        from app.speech_service import _looks_like_audio

        self.assertTrue(_looks_like_audio(b"RIFF" + b"\x00" * 96))
        self.assertTrue(_looks_like_audio(b"\xff\xfb" + b"\x00" * 96))  # MPEG frame
        self.assertFalse(_looks_like_audio(b"<html>rate limited</html>" + b" " * 96))
        self.assertFalse(_looks_like_audio(b"RIFF"))  # too short


class TestCloudDeadline(unittest.TestCase):
    """One hung cloud speech tier must fail THAT tier within
    SPEECH_CLOUD_DEADLINE_S — never the whole request via a gateway 504."""

    def test_cloud_call_bounds_hung_upstream(self) -> None:
        from app import speech_service as ss

        self.assertEqual(ss._cloud_call("fast", lambda: 42), 42)
        t0 = time.time()
        with patch.object(ss, "SPEECH_CLOUD_DEADLINE_S", 0.2):
            with self.assertRaises(TimeoutError):
                ss._cloud_call("test hang", time.sleep, 5)
        self.assertLess(time.time() - t0, 2.0)

    def test_hung_sunbird_tts_degrades_to_error_json(self) -> None:
        import threading
        from collections import OrderedDict

        from app import speech_service as ss
        from app import sunbird

        model = ss.SpeechModel.__new__(ss.SpeechModel)
        model.enabled = True
        model._tts_cache = OrderedDict()
        model._tts_cache_lock = threading.Lock()
        model._spark_tts = None  # opt-in tier; not under test here
        breaker = MagicMock()
        breaker.allow_request.return_value = False  # skip the local tier
        model._breakers = {"tts": breaker}

        t0 = time.time()
        with patch.object(ss, "SPEECH_CLOUD_DEADLINE_S", 0.2), \
                patch.object(ss.SpeechModel, "_synthesize_edge_tts",
                             side_effect=RuntimeError("edge down")), \
                patch.object(sunbird, "is_available", return_value=True), \
                patch.object(sunbird, "text_to_speech",
                             side_effect=lambda *a, **k: time.sleep(5)):
            # Luganda skips the English-gated Workers AI tier.
            result = model.synthesize("Oli otya", language="lg")

        self.assertLess(time.time() - t0, 2.5)  # returned promptly, no hang
        self.assertEqual(result.backend, "error")
        self.assertIn("All TTS backends failed", result.error)


class TestVoiceChatBudget(unittest.TestCase):
    """The batch /v1/voice/chat must return the TEXT reply before the
    deployment gateway can 504 the request: once the time budget is spent,
    reply-TTS is skipped and flagged so clients narrate via /v1/tts."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from app import database as db
        from app import main as main_module

        db.init_db()
        cls.main = main_module
        cls.client = TestClient(main_module.app)

    def _stub_speech(self) -> MagicMock:
        speech = MagicMock()
        speech.transcribe.return_value = TranscribeResult(
            text="What is the VAT rate?", language="en",
            duration_s=1.0, latency_s=0.1, backend="mock",
        )
        speech.synthesize.return_value = SynthesizeResult(
            audio=b"RIFF" + b"\x00" * 200, sample_rate=22050, num_samples=200,
            duration_s=1.0, latency_s=0.1, backend="mock", voice="v",
        )
        return speech

    def _stub_model(self) -> MagicMock:
        model = MagicMock()
        model.generate.return_value = {
            "reply": "The standard VAT rate in Uganda is 18%.",
            "sources": [], "citations": [], "faithfulness_score": None,
            "retrieval_mode": "calculator", "conversation_id": "vc1",
        }
        return model

    def _post(self):
        return self.client.post(
            "/v1/voice/chat?language=en&sample_rate=16000&tts_enabled=true",
            content=b"\x00\x01" * 800,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Voice-Consent": "true",
            },
        )

    def test_spent_budget_skips_tts_but_returns_reply(self) -> None:
        speech, model = self._stub_speech(), self._stub_model()
        self.main.app.state.speech = speech
        self.main.app.state.model = model
        with patch.object(self.main, "VOICE_CHAT_BUDGET_S", 0.0):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["tts_skipped"])
        self.assertEqual(data["reply_audio_base64"], "")
        self.assertIn("18%", data["reply"])
        self.assertIsNone(data["error"])
        speech.synthesize.assert_not_called()

    def test_within_budget_narrates_inline(self) -> None:
        speech, model = self._stub_speech(), self._stub_model()
        self.main.app.state.speech = speech
        self.main.app.state.model = model
        with patch.object(self.main, "VOICE_CHAT_BUDGET_S", 999.0):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["tts_skipped"])
        self.assertTrue(data["reply_audio_base64"])
        speech.synthesize.assert_called_once()


class TestSunbirdRetry(unittest.TestCase):
    def _response(self, status: int, headers: dict | None = None):
        import httpx

        request = httpx.Request("POST", "https://api.test/tasks/translate")
        return httpx.Response(status, request=request, headers=headers or {})

    def test_retries_transient_then_succeeds(self) -> None:
        import httpx

        from app import sunbird

        client = MagicMock()
        client.post.side_effect = [self._response(503), self._response(200)]
        with patch.object(sunbird, "SUNBIRD_API_TOKEN", "tok-primary"), \
                patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", ""), \
                patch.object(sunbird, "_client_for", return_value=client), \
                patch.object(sunbird.time, "sleep") as slept:
            resp = sunbird._post("/tasks/translate", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(client.post.call_count, 2)
        slept.assert_called_once()

    def test_auth_failure_fails_over_without_retry(self) -> None:
        from app import sunbird

        primary = MagicMock()
        primary.post.return_value = self._response(401)
        fallback = MagicMock()
        fallback.post.return_value = self._response(200)

        def _client_for(token):
            return primary if token == "tok-primary" else fallback

        with patch.object(sunbird, "SUNBIRD_API_TOKEN", "tok-primary"), \
                patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", "tok-fallback"), \
                patch.object(sunbird, "_client_for", side_effect=_client_for), \
                patch.object(sunbird.time, "sleep") as slept:
            resp = sunbird._post("/tasks/translate", json={})
        self.assertEqual(resp.status_code, 200)
        primary.post.assert_called_once()  # no retry on 401
        fallback.post.assert_called_once()
        slept.assert_not_called()

    def test_rewind_uploads_reseeks_streams(self) -> None:
        from app.sunbird import _rewind_uploads

        stream = io.BytesIO(b"pcm-bytes")
        stream.read()  # consume, as a failed attempt would
        kwargs = {"files": {"audio": ("a.wav", stream)}}
        _rewind_uploads(kwargs)
        self.assertEqual(stream.read(), b"pcm-bytes")


if __name__ == "__main__":
    unittest.main()
