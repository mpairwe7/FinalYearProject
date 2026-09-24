"""SpeechModel.identify_language — Whisper-SALT's language token as a classifier.

Against a stand-in model: the real weights are ~6 GB and gated. What matters
here is the request shape (one decoder step from <|startoftranscript|>, read
only at the candidates' SALT token ids) and that the method never raises.
The accuracy of the real model is measured by scripts/eval_language_id.py.
"""

from __future__ import annotations

import concurrent.futures
import unittest
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from app import speech_service as ss  # noqa: E402
from app.resilience import CircuitBreaker  # noqa: E402

SOT = 50258
ONE_SECOND = b"\x10\x00" * 16000


class FakeWhisper:
    """Callable like WhisperForConditionalGeneration, returning chosen logits."""

    device = "cpu"
    dtype = torch.float32

    def __init__(self, logits_by_id: dict[int, float], fail: bool = False) -> None:
        self.logits_by_id = logits_by_id
        self.fail = fail
        self.calls: list[object] = []
        self.model = SimpleNamespace(encoder=lambda feats: ("encoded", feats.shape))

    def __call__(self, encoder_outputs, decoder_input_ids):
        if self.fail:
            raise RuntimeError("CUDA error")
        self.calls.append(decoder_input_ids.tolist())
        logits = torch.full((1, 1, 51866), -20.0)
        for token_id, value in self.logits_by_id.items():
            logits[0, 0, token_id] = value
        return SimpleNamespace(logits=logits)


class FakeProcessor:
    def __init__(self) -> None:
        self.tokenizer = SimpleNamespace(convert_tokens_to_ids=lambda tok: SOT)

    def feature_extractor(self, samples, sampling_rate, return_tensors):
        return SimpleNamespace(input_features=torch.zeros(1, 128, 3000))


def model_with(fake: FakeWhisper | None) -> ss.SpeechModel:
    model = ss.SpeechModel.__new__(ss.SpeechModel)
    model._whisper_salt = (fake, FakeProcessor()) if fake else None
    model._breakers = {"lid": CircuitBreaker(name="t.lid", failure_threshold=3, reset_timeout=60.0)}
    model._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return model


class IdentifyLanguageTests(unittest.TestCase):
    ids = ss.SALT_LANGUAGE_TOKEN_IDS

    def test_probabilities_are_over_the_candidates_only(self):
        fake = FakeWhisper({self.ids["en"]: 1.0, self.ids["sw"]: 0.0, self.ids["lg"]: 3.0})
        res = model_with(fake).identify_language(ONE_SECOND)
        self.assertIsNone(res.error)
        self.assertEqual(set(res.probs), {"en", "sw", "lg"})
        self.assertEqual(res.top, "lg")
        self.assertAlmostEqual(sum(res.probs.values()), 1.0, places=3)
        self.assertGreater(res.probs["lg"], 0.8)
        self.assertAlmostEqual(res.speech_s, 1.0)

    def test_it_is_one_decoder_step_from_start_of_transcript(self):
        fake = FakeWhisper({self.ids["en"]: 2.0})
        model_with(fake).identify_language(ONE_SECOND)
        self.assertEqual(fake.calls, [[[SOT]]])

    def test_a_token_outside_the_candidates_cannot_win(self):
        # <|fr|> (50265) is far ahead, but French is not a candidate.
        fake = FakeWhisper({50265: 10.0, self.ids["en"]: 1.0, self.ids["lg"]: 0.5})
        res = model_with(fake).identify_language(ONE_SECOND, candidates=("en", "lg"))
        self.assertEqual(set(res.probs), {"en", "lg"})
        self.assertEqual(res.top, "en")

    def test_too_little_audio_is_no_vote(self):
        res = model_with(FakeWhisper({})).identify_language(b"\x10\x00" * 1600)
        self.assertEqual((res.top, res.error), ("", "too_short"))
        self.assertEqual(res.probs, {})

    def test_no_model_is_no_vote(self):
        res = model_with(None).identify_language(ONE_SECOND)
        self.assertEqual(res.error, "whisper_salt_unavailable")

    def test_one_mapped_candidate_is_not_a_choice(self):
        res = model_with(FakeWhisper({})).identify_language(ONE_SECOND, candidates=("en", "xx"))
        self.assertEqual(res.error, "need_two_mapped_candidates")

    def test_failures_never_raise_and_open_the_breaker(self):
        model = model_with(FakeWhisper({}, fail=True))
        for _ in range(3):
            res = model.identify_language(ONE_SECOND)
            self.assertTrue(res.error.startswith("lid_failed"))
        self.assertEqual(model.identify_language(ONE_SECOND).error, "breaker_open")

    def test_confidence_is_the_top_probability(self):
        res = ss.LanguageIdResult({"en": 0.2, "lg": 0.8}, "lg", 10.0, 2.0)
        self.assertEqual(res.confidence, 0.8)
        self.assertEqual(ss.LanguageIdResult({}, "", 0.0, 0.0, error="x").confidence, 0.0)


class Pcm16WavTests(unittest.TestCase):
    """Raw call PCM that looks like an MPEG frame sync must not be decoded as MP3."""

    def test_pcm_starting_like_an_mp3_header_round_trips_exactly(self):
        import numpy as np

        pcm = (np.array([-1, -1, 300, -300] * 4000, dtype="<i2")).tobytes()  # starts FF FF FF FF
        self.assertEqual(pcm[:2], b"\xff\xff")
        model = ss.SpeechModel.__new__(ss.SpeechModel)
        decoded = model._decode_audio_bytes(ss.pcm16_to_wav(pcm, 16000), target_sr=16000)
        self.assertEqual(len(decoded), 16000)
        self.assertAlmostEqual(float(decoded[2]), 300 / 32768, places=4)

    def test_identify_language_reads_raw_pcm16_exactly(self):
        fake = FakeWhisper({ss.SALT_LANGUAGE_TOKEN_IDS["en"]: 1.0})
        pcm = b"\xff\xff" * 16000
        res = model_with(fake).identify_language(pcm)
        self.assertAlmostEqual(res.speech_s, 1.0)


if __name__ == "__main__":
    unittest.main()
