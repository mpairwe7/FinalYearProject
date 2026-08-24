"""The first taxpayer to press Listen should not pay for a cold path.

Reported as "the listening model takes quite a while to load and speak back".
``SpeechModel.__init__`` builds the model objects, but everything downstream
of them is lazy — the Spark-TTS codec, the edge-tts session, the Sunbird httpx
client — so whoever asked first absorbed all of it. Warm-up moves that cost to
startup, on a background thread, where nobody is waiting.

What matters about warm-up is not that it succeeds. It is that it can never
fail a boot: a Space with no egress, a gated model repo, a Sunbird outage must
all leave the service running and the speech endpoints answering exactly as
they did before.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app import speech_service
from app.speech_service import SpeechModel, SynthesizeResult


def _stub_model() -> SpeechModel:
    """A SpeechModel with the heavy init skipped."""
    with mock.patch.object(SpeechModel, "_init_models", return_value=None):
        return SpeechModel()


class WarmupTest(unittest.TestCase):
    def test_it_synthesizes_once_per_configured_locale(self):
        model = _stub_model()
        model.enabled = True
        with mock.patch.object(
            speech_service, "SPEECH_WARMUP_LOCALES", ("en", "lg", "sw")
        ), mock.patch.object(model, "synthesize") as synth:
            synth.return_value = SynthesizeResult(
                audio=b"RIFF", sample_rate=16000, num_samples=1, duration_s=0.1,
                latency_s=0.1, backend="piper", voice="v", error=None,
            )
            outcomes = model.warmup()
        self.assertEqual(outcomes, {"en": "piper", "lg": "piper", "sw": "piper"})
        self.assertEqual(synth.call_count, 3)

    def test_a_failing_locale_is_recorded_and_the_rest_still_run(self):
        model = _stub_model()
        model.enabled = True
        ok = SynthesizeResult(
            audio=b"RIFF", sample_rate=16000, num_samples=1, duration_s=0.1,
            latency_s=0.1, backend="piper", voice="v", error=None,
        )
        with mock.patch.object(
            speech_service, "SPEECH_WARMUP_LOCALES", ("en", "lg")
        ), mock.patch.object(
            model, "synthesize", side_effect=[RuntimeError("no egress"), ok]
        ):
            outcomes = model.warmup()
        self.assertTrue(outcomes["en"].startswith("error:"))
        self.assertEqual(outcomes["lg"], "piper")

    def test_a_backend_error_result_is_reported_not_raised(self):
        model = _stub_model()
        model.enabled = True
        failed = SynthesizeResult(
            audio=b"", sample_rate=0, num_samples=0, duration_s=0.0,
            latency_s=0.0, backend="error", voice="", error="All TTS backends failed",
        )
        with mock.patch.object(
            speech_service, "SPEECH_WARMUP_LOCALES", ("lg",)
        ), mock.patch.object(model, "synthesize", return_value=failed):
            outcomes = model.warmup()
        self.assertEqual(outcomes, {"lg": "error: All TTS backends failed"})

    def test_a_disabled_pipeline_does_no_work(self):
        model = _stub_model()
        model.enabled = False
        with mock.patch.object(model, "synthesize") as synth:
            self.assertEqual(model.warmup(), {})
        synth.assert_not_called()


if __name__ == "__main__":
    unittest.main()
