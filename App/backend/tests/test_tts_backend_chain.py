"""The local TTS tier must FAIL when it has no local voice, not fake success.

Found by the trilingual coverage run for issue #303. `POST /v1/tts` returned
``backend="mock"`` for English, Luganda *and* Kiswahili from a container where
edge-tts was demonstrably working — 23 KB of real neural audio from the same
process, one Python call away.

The cause is a fallback chain that could never reach its own fallbacks.
``_synthesize_uncached`` advances from the local tier only on an exception, and
``_do_synthesize`` ended in ``_synth_mock`` — a 440 Hz sine beep added "so CI
tests have audio". So on any image without Piper/Sherpa voice packs (the slim
deploy image, and every CI container) the local tier *always succeeded*, and
the Cloudflare, Sunbird-native and edge-tts tiers below it were unreachable
code. The chain's careful ordering comments described behaviour that could not
occur.

The beep is gone rather than moved to the end of the chain. The chain already
terminates in ``backend="error"``, which ``/v1/voice/chat`` handles by
returning the text reply without audio. That is honest; a caller who hears
beeps has no way to tell the answer was never spoken.
"""

from __future__ import annotations

import struct
import sys
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import speech_service as ss  # noqa: E402


def _wav(num_samples: int = 240, rate: int = 24000) -> bytes:
    pcm = b"\x00\x00" * num_samples
    return (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )


def _model() -> ss.SpeechModel:
    """A SpeechModel with just the state the synthesis chain touches."""
    model = ss.SpeechModel.__new__(ss.SpeechModel)
    model.enabled = True
    model._tts_cache = OrderedDict()
    model._tts_cache_lock = threading.Lock()
    model._spark_tts = None  # opt-in tier; not under test here
    breaker = MagicMock()
    breaker.allow_request.return_value = True
    model._breakers = {"tts": breaker}
    model._executor = _InlineExecutor()
    return model


class _InlineExecutor:
    """Runs submitted work on the calling thread, keeping the test single-threaded."""

    class _Future:
        def __init__(self, fn, args, kwargs):
            self._fn, self._args, self._kwargs = fn, args, kwargs

        def result(self, timeout=None):  # noqa: ARG002 — signature parity only
            return self._fn(*self._args, **self._kwargs)

        def cancel(self):
            return True

    def submit(self, fn, *args, **kwargs):
        return self._Future(fn, args, kwargs)


class NoLocalVoiceTests(unittest.TestCase):
    def test_the_local_tier_raises_when_no_local_model_exists(self):
        """The whole fix in one assertion: absence must be a failure."""
        with patch.object(ss, "PROJECT_ROOT", Path("/nonexistent-voice-root")):
            with self.assertRaises(ss.LocalVoiceUnavailable):
                ss.SpeechModel._do_synthesize(_model(), "Hello", "en_US-lessac-medium")

    def test_the_chain_reaches_edge_tts_when_the_local_voice_is_missing(self):
        """Before the fix this returned backend='mock' and never called edge."""
        model = _model()
        called: dict[str, object] = {}

        def _edge(self, text, language, voice=None):  # noqa: ANN001 — patched method
            called["language"] = language
            called["voice"] = voice
            return ss.SynthesizeResult(
                audio=_wav(), sample_rate=24000, num_samples=240, duration_s=0.01,
                latency_s=0.01, backend="edge_tts", voice="en-US-AriaNeural",
            )

        with patch.object(ss, "PROJECT_ROOT", Path("/nonexistent-voice-root")), \
                patch.object(ss.SpeechModel, "_synthesize_edge_tts", _edge):
            result = model.synthesize("What is the VAT rate?", language="en")

        self.assertEqual(result.backend, "edge_tts")
        self.assertTrue(result.audio)
        self.assertEqual(called["language"], "en")

    def test_a_real_local_voice_is_still_preferred_over_the_cloud(self):
        """The fix must not turn an offline deployment into a cloud-dependent one."""
        model = _model()
        local = ss.SynthesizeResult(
            audio=_wav(), sample_rate=22050, num_samples=240, duration_s=0.01,
            latency_s=0.01, backend="piper", voice="en_US-lessac-medium",
        )
        with patch.object(ss.SpeechModel, "_do_synthesize", return_value=local), \
                patch.object(ss.SpeechModel, "_synthesize_edge_tts",
                             side_effect=AssertionError("cloud tier reached")):
            result = model.synthesize("What is the VAT rate?", language="en")
        self.assertEqual(result.backend, "piper")

    def test_the_chain_still_ends_in_an_honest_error_not_a_beep(self):
        """No mock tier anywhere: silence the caller can detect beats a tone it cannot."""
        model = _model()
        with patch.object(ss, "PROJECT_ROOT", Path("/nonexistent-voice-root")), \
                patch.object(ss.SpeechModel, "_synthesize_edge_tts",
                             side_effect=RuntimeError("edge down")), \
                patch.object(ss.SpeechModel, "_cf_workers_ai_tts",
                             side_effect=RuntimeError("cf down")):
            result = model.synthesize("What is the VAT rate?", language="en")
        self.assertEqual(result.backend, "error")
        self.assertEqual(result.audio, b"")
        self.assertIn("All TTS backends failed", result.error)

    def test_a_missing_voice_pack_does_not_trip_the_tts_circuit_breaker(self):
        """"No voice installed" is a capability, not an outage.

        Recording it as a breaker failure opened `speech.tts` after four
        requests on any image without voice packs — i.e. permanently — so the
        breaker state stopped meaning anything about health and a half-open
        retry re-ran the same doomed filesystem lookup every backoff.
        """
        model = _model()
        with patch.object(ss, "PROJECT_ROOT", Path("/nonexistent-voice-root")), \
                patch.object(ss.SpeechModel, "_synthesize_edge_tts",
                             side_effect=RuntimeError("edge down")), \
                patch.object(ss.SpeechModel, "_cf_workers_ai_tts",
                             side_effect=RuntimeError("cf down")):
            model.synthesize("What is the VAT rate?", language="en")
        model._breakers["tts"].record_failure.assert_not_called()

    def test_a_real_local_failure_still_counts_against_the_breaker(self):
        """The counterweight: a crashing local backend must still open it."""
        model = _model()
        with patch.object(ss.SpeechModel, "_do_synthesize",
                          side_effect=RuntimeError("piper segfault")), \
                patch.object(ss.SpeechModel, "_synthesize_edge_tts",
                             side_effect=RuntimeError("edge down")), \
                patch.object(ss.SpeechModel, "_cf_workers_ai_tts",
                             side_effect=RuntimeError("cf down")):
            model.synthesize("What is the VAT rate?", language="en")
        model._breakers["tts"].record_failure.assert_called_once()

    def test_kiswahili_reaches_edge_with_its_own_voice(self):
        """The two fixes together: sw falls through to edge, and edge speaks sw."""
        model = _model()
        seen: dict[str, str] = {}

        def _edge(self, text, language, voice=None):  # noqa: ANN001 — patched method
            seen["voice"] = ss.resolve_edge_voice(language, voice)
            return ss.SynthesizeResult(
                audio=_wav(), sample_rate=24000, num_samples=240, duration_s=0.01,
                latency_s=0.01, backend="edge_tts", voice=seen["voice"],
            )

        from app import sunbird

        with patch.object(ss, "PROJECT_ROOT", Path("/nonexistent-voice-root")), \
                patch.object(sunbird, "is_available", return_value=False), \
                patch.object(ss.SpeechModel, "_synthesize_edge_tts", _edge):
            result = model.synthesize("EFRIS ni nini?", language="sw")

        self.assertEqual(result.backend, "edge_tts")
        self.assertEqual(seen["voice"], ss.SPEECH_SW_EDGE_VOICE)
        self.assertTrue(seen["voice"].startswith("sw-"))


class LocalBeforeCloudOrderingTests(unittest.TestCase):
    """"switch from APIs to local downloaded models" — Spark-TTS-SALT (local)
    must be tried before Sunbird cloud for the locales it covers, not after.

    Before this, `prefer_native_cloud` tried Sunbird FIRST unconditionally
    for lg/sw/ach/nyn; Spark-TTS-SALT, once it existed, was appended as a
    fallback AFTER that cloud attempt. That is the wrong way round for "local
    instead of API" — this pins the corrected order instead.
    """

    def _spark_stub(self, wav_bytes=None):
        """A stand-in with the one method _try_spark_salt() calls."""
        stub = MagicMock()
        stub.synthesize.return_value = ([0.0, 0.0, 0.0, 0.0], 16000)
        return stub

    def test_spark_salt_is_tried_before_sunbird_for_a_covered_locale(self):
        # "sw", not "lg" — lg is in LOCAL_TTS_VOICES (a Piper voice exists
        # for it on paper), which routes it through tier ①.75 instead of
        # tier ⓪; see test_spark_salt_also_wins_for_the_lg_local_voice_gap_tier
        # below for that path. sw has no local Piper voice at all, so it
        # exercises tier ⓪'s prefer_native_voice gate unambiguously.
        model = _model()
        model._spark_tts = self._spark_stub()
        from app import sunbird

        with patch.object(
            sunbird, "is_available",
            side_effect=AssertionError("Sunbird reached before local Spark-TTS-SALT"),
        ):
            result = model.synthesize("Habari yako?", language="sw")
        self.assertEqual(result.backend, "spark_tts_salt")
        model._spark_tts.synthesize.assert_called_once()

    def test_sunbird_still_answers_when_spark_salt_is_not_loaded(self):
        """No checkout done (the common case today) — behaviour must be
        unchanged from before Spark-TTS-SALT existed: Sunbird cloud first."""
        model = _model()
        self.assertIsNone(model._spark_tts)  # _model()'s default
        from app import sunbird

        with patch.object(sunbird, "is_available", return_value=True), \
                patch.object(sunbird, "text_to_speech",
                              return_value={"audio_url": "http://example.invalid/a.wav"}), \
                patch.object(sunbird, "resolve_tts_voice", return_value="waxal_swa_0006"), \
                patch("httpx.get", return_value=MagicMock(status_code=200, content=_wav())):
            result = model.synthesize("Habari yako?", language="sw")
        self.assertEqual(result.backend, "sunbird_cloud")

    def test_sunbird_still_answers_when_spark_salt_fails(self):
        """Local first, but cloud is still a real fallback — not exclusion."""
        model = _model()
        model._spark_tts = self._spark_stub()
        model._spark_tts.synthesize.side_effect = RuntimeError("spark down")
        from app import sunbird

        with patch.object(sunbird, "is_available", return_value=True), \
                patch.object(sunbird, "text_to_speech",
                              return_value={"audio_url": "http://example.invalid/a.wav"}), \
                patch.object(sunbird, "resolve_tts_voice", return_value="waxal_swa_0006"), \
                patch("httpx.get", return_value=MagicMock(status_code=200, content=_wav())):
            result = model.synthesize("Habari yako?", language="sw")
        self.assertEqual(result.backend, "sunbird_cloud")

    def test_spark_salt_also_wins_for_the_lg_local_voice_gap_tier(self):
        """Tier ①.75 — the local-voice-on-paper-but-missing-pack path — gives
        Spark-TTS-SALT the same priority over Sunbird that tier ⓪ does."""
        model = _model()
        model._spark_tts = self._spark_stub()
        from app import sunbird

        with patch.object(ss, "PROJECT_ROOT", Path("/nonexistent-voice-root")), \
                patch.object(sunbird, "is_available",
                              side_effect=AssertionError("Sunbird reached before local Spark-TTS-SALT")):
            result = model.synthesize("Oli otya?", language="lg")
        self.assertEqual(result.backend, "spark_tts_salt")


if __name__ == "__main__":
    unittest.main()
