"""The Orpheus Luganda voice: client, place in the TTS chain, and the call's TTS.

No sidecar runs here — httpx is faked. Latency and quality of the real model
are measured by scripts/bench_orpheus_tts.py (evals/reports/orpheus_tts_*).
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orpheus_sidecar"))

from app import orpheus_tts  # noqa: E402
from app import speech_service as ss  # noqa: E402

ON = {"ORPHEUS_TTS_URL": "http://orpheus:8100"}


def reset_cooldown() -> None:
    orpheus_tts._down_until = 0.0


class SpeakerSelection(unittest.TestCase):
    def setUp(self) -> None:
        reset_cooldown()

    def test_off_without_a_url(self):
        with patch.dict(os.environ, {"ORPHEUS_TTS_URL": ""}):
            self.assertIsNone(orpheus_tts.speaker_for("lg"))
            self.assertFalse(orpheus_tts.is_configured())

    def test_luganda_by_default_and_only_luganda(self):
        with patch.dict(os.environ, ON):
            self.assertEqual(orpheus_tts.speaker_for("lg"), "salt_lug_0001")
            self.assertIsNone(orpheus_tts.speaker_for("sw"))
            self.assertIsNone(orpheus_tts.speaker_for("en"))

    def test_languages_and_speakers_come_from_env(self):
        env = {**ON, "ORPHEUS_TTS_LANGUAGES": "lg,sw", "ORPHEUS_TTS_SPEAKER_LG": "waxal_lug_0005"}
        with patch.dict(os.environ, env):
            self.assertEqual(orpheus_tts.speaker_for("lg"), "waxal_lug_0005")
            self.assertEqual(orpheus_tts.speaker_for("sw"), "waxal_swa_0006")


class SynthesizeClient(unittest.TestCase):
    def setUp(self) -> None:
        reset_cooldown()

    def test_request_shape_and_pcm_back(self):
        resp = httpx.Response(200, content=b"\x01\x00\x02\x00")
        with patch.dict(os.environ, ON), patch("httpx.post", return_value=resp) as post:
            pcm = orpheus_tts.synthesize("Oli otya?", "lg")
        self.assertEqual(pcm, b"\x01\x00\x02\x00")
        self.assertEqual(post.call_args.args[0], "http://orpheus:8100/v1/audio/speech")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"input": "Oli otya?", "voice": "salt_lug_0001", "response_format": "pcm"},
        )

    def test_an_error_status_is_unavailable(self):
        with patch.dict(os.environ, ON), patch("httpx.post", return_value=httpx.Response(503)):
            with self.assertRaises(orpheus_tts.OrpheusUnavailable):
                orpheus_tts.synthesize("Oli otya?", "lg")

    def test_a_dead_sidecar_costs_one_timeout_not_one_per_sentence(self):
        with patch.dict(os.environ, ON), patch("httpx.post", side_effect=httpx.ConnectError("refused")) as post:
            with self.assertRaises(orpheus_tts.OrpheusUnavailable):
                orpheus_tts.synthesize("one", "lg")
            with self.assertRaises(orpheus_tts.OrpheusUnavailable):
                orpheus_tts.synthesize("two", "lg")
        self.assertEqual(post.call_count, 1)

    def test_no_voice_for_the_language_never_calls_out(self):
        with patch.dict(os.environ, ON), patch("httpx.post") as post:
            with self.assertRaises(orpheus_tts.OrpheusUnavailable):
                orpheus_tts.synthesize("Hello", "en")
        post.assert_not_called()

    def test_wav_wrapping(self):
        wav = orpheus_tts.pcm16_to_wav(b"\x00\x00" * 240)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(int.from_bytes(wav[24:28], "little"), 24000)


class StreamClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_cooldown()

    async def test_odd_byte_splits_never_shift_samples(self):
        async def body():
            for chunk in (b"\x01", b"\x00\x02", b"\x00\x03\x00"):
                yield chunk

        real_client = httpx.AsyncClient

        def client_factory(timeout):
            return real_client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body())), timeout=timeout)

        with patch.dict(os.environ, ON), patch("httpx.AsyncClient", side_effect=client_factory):
            chunks = [c async for c in orpheus_tts.stream("Oli otya?", "lg")]
        self.assertTrue(all(len(c) % 2 == 0 for c in chunks))
        self.assertEqual(b"".join(chunks), b"\x01\x00\x02\x00\x03\x00")

    async def test_unreachable_before_audio_raises(self):
        real_client = httpx.AsyncClient

        def refuse(req):
            raise httpx.ConnectError("refused")

        with patch.dict(os.environ, ON), patch(
            "httpx.AsyncClient", side_effect=lambda timeout: real_client(transport=httpx.MockTransport(refuse), timeout=timeout)
        ):
            with self.assertRaises(orpheus_tts.OrpheusUnavailable):
                async for _ in orpheus_tts.stream("Oli otya?", "lg"):
                    pass


class _InlineExecutor:
    class _Future:
        def __init__(self, fn, args, kwargs):
            self._fn, self._args, self._kwargs = fn, args, kwargs

        def result(self, timeout=None):  # noqa: ARG002
            return self._fn(*self._args, **self._kwargs)

    def submit(self, fn, *args, **kwargs):
        return self._Future(fn, args, kwargs)


def chain_model() -> ss.SpeechModel:
    model = ss.SpeechModel.__new__(ss.SpeechModel)
    model.enabled = True
    model._tts_cache = OrderedDict()
    model._tts_cache_lock = threading.Lock()
    spark = MagicMock()
    import numpy as np

    spark.synthesize.return_value = (np.zeros(1600, dtype="float32"), 16000)
    model._spark_tts = spark
    breaker = MagicMock()
    breaker.allow_request.return_value = True
    model._breakers = {"tts": breaker}
    model._executor = _InlineExecutor()
    return model


class ChainOrder(unittest.TestCase):
    """Luganda: Orpheus → Spark-TTS-SALT → Sunbird, and never an English stand-in."""

    def setUp(self) -> None:
        reset_cooldown()

    def no_local_voice(self, model: ss.SpeechModel):
        return patch.object(model, "_do_synthesize", side_effect=ss.LocalVoiceUnavailable("lg"))

    def test_orpheus_answers_first_when_configured(self):
        model = chain_model()
        with patch.dict(os.environ, ON), patch.object(orpheus_tts, "synthesize", return_value=b"\x00\x00" * 2400):
            res = model._synthesize_uncached("Oli otya?", "lg-voice", "lg")
        self.assertEqual(res.backend, "orpheus_salt")
        self.assertEqual(res.sample_rate, 24000)
        self.assertEqual(res.voice, "salt_lug_0001")
        model._spark_tts.synthesize.assert_not_called()

    def test_spark_is_next_when_orpheus_is_down(self):
        model = chain_model()
        with patch.dict(os.environ, ON), patch.object(
            orpheus_tts, "synthesize", side_effect=orpheus_tts.OrpheusUnavailable("down")
        ), self.no_local_voice(model):
            res = model._synthesize_uncached("Oli otya?", "lg-voice", "lg")
        self.assertEqual(res.backend, "spark_tts_salt")

    def test_without_the_url_the_chain_is_unchanged(self):
        model = chain_model()
        with patch.dict(os.environ, {"ORPHEUS_TTS_URL": ""}), patch.object(
            orpheus_tts, "synthesize"
        ) as orpheus, self.no_local_voice(model):
            res = model._synthesize_uncached("Oli otya?", "lg-voice", "lg")
        orpheus.assert_not_called()
        self.assertEqual(res.backend, "spark_tts_salt")

    def test_cache_accessors_share_synthesize_s_cache(self):
        model = chain_model()
        result = ss.SynthesizeResult(b"RIFFxxxx", 24000, 2, 0.1, 0.2, "orpheus_salt", "salt_lug_0001")
        with patch.object(ss, "TTS_CACHE_SIZE", 8):
            self.assertIsNone(model.tts_cache_get("Lindako katono.", "v", "lg"))
            model.tts_cache_put("Lindako katono.", "v", "lg", result)
            cached = model.tts_cache_get("Lindako katono.", "v", "lg")
            self.assertEqual(cached.backend, "orpheus_salt+cache")
            with patch.object(model, "_synthesize_uncached") as uncached:
                self.assertEqual(model.synthesize("Lindako katono.", voice="v", language="lg").audio, b"RIFFxxxx")
            uncached.assert_not_called()

    def test_prewarm_counts_and_never_raises(self):
        model = chain_model()
        ok = ss.SynthesizeResult(b"RIFF", 24000, 1, 0.1, 0.1, "orpheus_salt", "v")
        with patch.object(model, "synthesize", side_effect=[ok, RuntimeError("down"), ok]):
            self.assertEqual(model.prewarm_phrases("lg", ["a", "b", "c"], voice="v"), {"cached": 2, "failed": 1})


class ReceptionistTTS(unittest.IsolatedAsyncioTestCase):
    """UraSpeechTTS: streams Luganda from Orpheus, falls back, never plays an English stand-in."""

    def setUp(self) -> None:
        reset_cooldown()
        pytest = __import__("pytest")
        pytest.importorskip("pipecat")

    def tts(self, speech: object, room_locale: str = "lg"):
        from types import SimpleNamespace

        from app.receptionist.tts import UraSpeechTTS

        room = SimpleNamespace(state=SimpleNamespace(locale=room_locale))
        tts = UraSpeechTTS(speech_model=speech, voice="en-KE-AsiliaNeural", language="en", room=room)
        tts._sample_rate = 16000  # normally set when the pipeline starts
        return tts

    async def test_language_follows_the_room(self):
        tts = self.tts(MagicMock(), room_locale="sw")
        self.assertEqual(tts.language, "sw")

    async def test_luganda_is_streamed_from_orpheus_and_cached(self):
        speech = MagicMock()
        speech.tts_cache_get.return_value = None

        async def fake_stream(text, language):
            yield b"\x00\x10" * 2400
            yield b"\x00\x10" * 2400

        with patch.dict(os.environ, ON), patch.object(orpheus_tts, "stream", fake_stream):
            frames = [f async for f in self.tts(speech).run_tts("Lindako katono.", "ctx")]
        self.assertTrue(frames)
        self.assertTrue(all(f.sample_rate == 16000 for f in frames))
        speech.synthesize.assert_not_called()
        speech.tts_cache_put.assert_called_once()
        self.assertEqual(speech.tts_cache_put.call_args.args[3].backend, "orpheus_salt")

    async def test_a_cached_line_is_not_streamed_again(self):
        speech = MagicMock()
        speech.tts_cache_get.return_value = object()
        speech.synthesize.return_value = MagicMock(audio=b"RIFF", backend="orpheus_salt+cache")
        speech._decode_audio_bytes.return_value = [0.0] * 320
        with patch.dict(os.environ, ON), patch.object(orpheus_tts, "stream") as stream:
            frames = [f async for f in self.tts(speech).run_tts("Lindako katono.", "ctx")]
        stream.assert_not_called()
        self.assertTrue(frames)

    async def test_orpheus_down_falls_back_to_the_chain(self):
        speech = MagicMock()
        speech.tts_cache_get.return_value = None
        speech.synthesize.return_value = MagicMock(audio=b"RIFF", backend="spark_tts_salt")
        speech._decode_audio_bytes.return_value = [0.0] * 320

        async def down(text, language):
            raise orpheus_tts.OrpheusUnavailable("down")
            yield b""  # pragma: no cover — makes this an async generator

        with patch.dict(os.environ, ON), patch.object(orpheus_tts, "stream", down):
            frames = [f async for f in self.tts(speech).run_tts("Oli otya?", "ctx")]
        self.assertTrue(frames)
        speech.synthesize.assert_called_once()

    async def test_an_english_voice_reading_luganda_is_dropped(self):
        speech = MagicMock()
        speech.tts_cache_get.return_value = None
        speech.synthesize.return_value = MagicMock(audio=b"RIFF", backend="edge_tts")
        speech._decode_audio_bytes.return_value = [0.0] * 320
        with patch.dict(os.environ, {"ORPHEUS_TTS_URL": "", "RECEPTIONIST_ALLOW_EDGE_STANDIN_LG": "false"}):
            frames = [f async for f in self.tts(speech).run_tts("Oli otya?", "ctx")]
        self.assertEqual(frames, [])

    async def test_the_stand_in_can_be_allowed(self):
        speech = MagicMock()
        speech.synthesize.return_value = MagicMock(audio=b"RIFF", backend="edge_tts")
        speech._decode_audio_bytes.return_value = [0.0] * 320
        with patch.dict(os.environ, {"ORPHEUS_TTS_URL": "", "RECEPTIONIST_ALLOW_EDGE_STANDIN_LG": "true"}):
            frames = [f async for f in self.tts(speech).run_tts("Oli otya?", "ctx")]
        self.assertTrue(frames)

    async def test_prewarm_uses_the_call_s_voice_so_the_cache_keys_match(self):
        from app.receptionist.config import get_tts_voice
        from app.receptionist.tts import prewarm_receptionist_phrases

        speech = MagicMock()
        speech.prewarm_phrases.return_value = {"cached": 1, "failed": 0}
        prewarm_receptionist_phrases(speech, ("lg",))
        self.assertEqual(speech.prewarm_phrases.call_args.kwargs["voice"], get_tts_voice())
        self.assertEqual(speech.prewarm_phrases.call_args.args[0], "lg")


class SidecarFrameParsing(unittest.TestCase):
    """orpheus_sidecar/codes.py — which tokens become audio, and which samples are emitted."""

    def setUp(self) -> None:
        import codes

        self.codes = codes

    def frame_tokens(self, codes7: list[int]) -> list[int]:
        c = self.codes
        return [c.AUDIO_TOKEN_LO + pos * c.CODEBOOK + code for pos, code in enumerate(codes7)]

    def test_prompt_layout(self):
        c = self.codes
        self.assertEqual(c.build_prompt_ids([1, 2]), [c.START_OF_HUMAN, 1, 2, c.END_OF_TEXT, c.END_OF_HUMAN])

    def test_only_tokens_after_start_of_speech_count(self):
        c = self.codes
        a = c.FrameAssembler()
        for tid in self.frame_tokens([1, 2, 3, 4, 5, 6, 7]):
            a.feed(tid)
        self.assertEqual(a.frames, [])
        a.feed(c.START_OF_SPEECH)
        completed = [a.feed(t) for t in self.frame_tokens([1, 2, 3, 4, 5, 6, 7])]
        self.assertEqual(completed, [False] * 6 + [True])
        self.assertEqual(a.frames, [[1, 2, 3, 4, 5, 6, 7]])

    def test_a_mis_slotted_code_is_dropped(self):
        c = self.codes
        a = c.FrameAssembler()
        a.feed(c.START_OF_SPEECH)
        a.feed(c.AUDIO_TOKEN_LO + 5)  # position 0
        a.feed(c.AUDIO_TOKEN_LO + 5)  # position 1 needs +4096: out of range, dropped
        self.assertEqual(a._partial, [5])

    def test_end_of_speech_stops_the_frame_stream(self):
        c = self.codes
        a = c.FrameAssembler()
        a.feed(c.START_OF_SPEECH)
        a.feed(c.END_OF_SPEECH)
        self.assertTrue(a.finished)
        self.assertFalse(a.feed(self.frame_tokens([1] * 7)[0]))

    def test_layers(self):
        l1, l2, l3 = self.codes.split_layers([[0, 1, 2, 3, 4, 5, 6]])
        self.assertEqual((l1, l2, l3), ([0], [1, 4], [2, 3, 5, 6]))

    def test_every_frame_is_emitted_exactly_once_in_order(self):
        c = self.codes
        for n in range(0, 12):
            with self.subTest(frames=n):
                emitted: list[int] = []
                done = 0
                for have in range(1, n + 1):
                    steps, done = c.next_steps(have, done, final=False)
                    emitted += self._frames_of(steps)
                steps, done = c.next_steps(n, done, final=True)
                emitted += self._frames_of(steps)
                self.assertEqual(emitted, list(range(n)))

    def test_first_audio_after_four_frames(self):
        steps, done = self.codes.next_steps(4, 0, final=False)
        self.assertEqual(done, 2)
        self.assertEqual(len(steps), 1)

    def _frames_of(self, steps) -> list[int]:
        f = self.codes.SAMPLES_PER_FRAME
        out: list[int] = []
        for s in steps:
            window = s.end - s.start
            hi = window * f if s.hi is None else s.hi
            out += [s.start + k for k in range(s.lo // f, hi // f)]
        return out


if __name__ == "__main__":
    unittest.main()
