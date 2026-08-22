"""Sunbird/asr-whisper-large-v3-salt wiring: the language-token map, and
that _transcribe_chain reaches this tier before the LoRA/Sherpa fallbacks.

The model itself is real weights (~6GB, gated on HuggingFace) and is not
loaded here — these tests cover the parts that do not need it: which ids get
forced for which locale (see the long comment on SALT_LANGUAGE_TOKEN_IDS in
speech_service.py for why lg/nyn/ach are forced despite decoding to
unrelated Whisper-base languages), and that the chain actually tries this
tier first when it is loaded.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import speech_service as ss  # noqa: E402


class LanguageTokenMapTests(unittest.TestCase):
    def test_the_five_locales_this_deployment_offers_are_all_mapped(self) -> None:
        for locale in ("en", "sw", "lg", "nyn", "ach"):
            with self.subTest(locale=locale):
                self.assertIn(locale, ss.SALT_LANGUAGE_TOKEN_IDS)

    def test_en_and_sw_are_in_whisper_s_original_language_table(self) -> None:
        """These two must be independently correct: no A/B test backs them,
        decode-correctness is the only thing vouching for them."""
        self.assertEqual(ss.SALT_LANGUAGE_TOKEN_IDS["en"], 50259)
        self.assertEqual(ss.SALT_LANGUAGE_TOKEN_IDS["sw"], 50318)

    def test_lg_nyn_ach_are_the_model_cards_ids_despite_the_wrong_decode(self) -> None:
        """Pins the ids an A/B test justified using, not the (misleading)
        strings they decode to on this checkpoint's base Whisper vocabulary
        — see docs/runbooks/salt-speech-backends.md for the measurement."""
        self.assertEqual(ss.SALT_LANGUAGE_TOKEN_IDS["lg"], 50355)
        self.assertEqual(ss.SALT_LANGUAGE_TOKEN_IDS["nyn"], 50354)
        self.assertEqual(ss.SALT_LANGUAGE_TOKEN_IDS["ach"], 50357)

    def test_no_id_is_guessed_for_a_locale_outside_the_five(self) -> None:
        """teo/lgg are in the fine-tune per the model card, but have no
        locale slot in this deployment — must not silently appear here."""
        self.assertNotIn("teo", ss.SALT_LANGUAGE_TOKEN_IDS)
        self.assertNotIn("lgg", ss.SALT_LANGUAGE_TOKEN_IDS)


class TranscribeWhisperSaltUnitTests(unittest.TestCase):
    """`_transcribe_whisper_salt` against a stand-in model/processor —
    proves the request shape (forced language string vs `None`) is correct
    without loading real weights."""

    def _model_with_salt(self, decode_map: dict[int, str]):
        model = ss.SpeechModel.__new__(ss.SpeechModel)
        fake_model = MagicMock()
        fake_model.device = "cpu"
        fake_model.dtype = "float32"
        fake_model.generate.return_value = "IDS"
        fake_processor = MagicMock()
        fake_processor.feature_extractor.return_value.input_features.to.return_value = "FEATS"
        fake_processor.tokenizer.decode.side_effect = lambda ids: decode_map[ids[0]]
        fake_processor.batch_decode.return_value = ["hello there"]
        model._whisper_salt = (fake_model, fake_processor)
        model._decode_audio_bytes = lambda *_a, **_kw: [0.0] * 1600
        return model, fake_model, fake_processor

    def test_english_forces_the_en_token_string(self) -> None:
        model, fake_model, _ = self._model_with_salt({50259: "<|en|>"})
        with patch("torch.no_grad"):
            ss.SpeechModel._transcribe_whisper_salt(model, b"\x00" * 3200, 16000, "en")
        self.assertEqual(fake_model.generate.call_args.kwargs["language"], "<|en|>")

    def test_luganda_forces_id_50355s_string_despite_its_stale_label(self) -> None:
        model, fake_model, fake_processor = self._model_with_salt({50355: "<|ba|>"})
        with patch("torch.no_grad"):
            ss.SpeechModel._transcribe_whisper_salt(model, b"\x00" * 3200, 16000, "lg")
        # The call forces whatever id 50355 decodes to on THIS tokenizer,
        # not a hand-picked "<|lg|>" string that would not round-trip.
        fake_processor.tokenizer.decode.assert_called_with([50355])
        self.assertEqual(fake_model.generate.call_args.kwargs["language"], "<|ba|>")

    def test_a_locale_outside_the_map_gets_no_forced_language(self) -> None:
        model, fake_model, fake_processor = self._model_with_salt({})
        with patch("torch.no_grad"):
            ss.SpeechModel._transcribe_whisper_salt(model, b"\x00" * 3200, 16000, "fr")
        fake_processor.tokenizer.decode.assert_not_called()
        self.assertIsNone(fake_model.generate.call_args.kwargs["language"])

    def test_no_language_argument_at_all_also_gets_no_forcing(self) -> None:
        model, fake_model, fake_processor = self._model_with_salt({})
        with patch("torch.no_grad"):
            ss.SpeechModel._transcribe_whisper_salt(model, b"\x00" * 3200, 16000, None)
        fake_processor.tokenizer.decode.assert_not_called()
        self.assertIsNone(fake_model.generate.call_args.kwargs["language"])

    def test_no_loaded_model_returns_none_rather_than_raising(self) -> None:
        model = ss.SpeechModel.__new__(ss.SpeechModel)
        model._whisper_salt = None
        result = ss.SpeechModel._transcribe_whisper_salt(model, b"\x00" * 3200, 16000, "en")
        self.assertIsNone(result)

    def test_result_carries_the_requested_locale_and_the_salt_backend_tag(self) -> None:
        model, fake_model, _ = self._model_with_salt({50259: "<|en|>"})
        with patch("torch.no_grad"):
            result = ss.SpeechModel._transcribe_whisper_salt(model, b"\x00" * 3200, 16000, "en")
        self.assertEqual(result.backend, "whisper_salt")
        self.assertEqual(result.language, "en")
        self.assertEqual(result.text, "hello there")


class ChainOrderingTests(unittest.TestCase):
    """SALT is tier ⓪ — tried before the LoRA/accent-detection tier, and
    skipped cleanly (not crashing) when unloaded."""

    def _bare(self):
        model = ss.SpeechModel.__new__(ss.SpeechModel)
        model.enabled = True
        model._whisper_salt = None
        model._whisper_peft = None
        model._whisper_adapters: dict = {}
        model._accent_detector = None
        model._asr = None
        model._breakers = {}
        return model

    def test_salt_is_tried_before_the_lora_tier(self) -> None:
        model = self._bare()
        salt_result = ss.TranscribeResult(text="from salt", backend="whisper_salt")
        model._whisper_salt = ("model", "processor")  # truthy, just needs to be non-None
        model._transcribe_whisper_salt = MagicMock(return_value=salt_result)
        model._transcribe_whisper_peft = MagicMock(
            side_effect=AssertionError("LoRA tier reached — SALT should have won")
        )
        result = ss.SpeechModel._transcribe_chain(model, b"\x00" * 3200, 16000, "en")
        self.assertEqual(result.backend, "whisper_salt")
        model._transcribe_whisper_peft.assert_not_called()

    def test_an_unloaded_salt_tier_falls_through_without_crashing(self) -> None:
        model = self._bare()  # model._whisper_salt already None
        result = ss.SpeechModel._transcribe_chain(model, b"\x00" * 3200, 16000, "en")
        # Nothing else is configured either; the chain must still return a
        # TranscribeResult, not raise, once it runs out of tiers.
        self.assertIsInstance(result, ss.TranscribeResult)

    def test_salt_heard_nothing_still_falls_through_to_the_next_tier(self) -> None:
        model = self._bare()
        model._whisper_salt = ("model", "processor")
        model._transcribe_whisper_salt = MagicMock(
            return_value=ss.TranscribeResult(text="", backend="whisper_salt")
        )
        peft_result = ss.TranscribeResult(text="from lora", backend="whisper_peft")
        model._whisper_peft = ("model2", "processor2")
        model._transcribe_whisper_peft = MagicMock(return_value=peft_result)
        result = ss.SpeechModel._transcribe_chain(model, b"\x00" * 3200, 16000, "en")
        self.assertEqual(result.backend, "whisper_peft")


if __name__ == "__main__":
    unittest.main()
