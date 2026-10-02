"""Unit tests for Whisper-SALT per-word confidence scoring."""

from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock, patch

import torch

from app import speech_service as ss


class WhisperSaltWordConfidenceTests(unittest.TestCase):
    def test_word_grouping_and_minimum_probability(self):
        model = ss.SpeechModel.__new__(ss.SpeechModel)
        fake_model = MagicMock()
        fake_model.device = "cpu"
        fake_model.dtype = torch.float32

        # Token sequences: 50258 (special start), 17377 (group), 2062 ( mid), 12 (-), 2707 (port), 50257 (special eos)
        seq_tensor = torch.tensor([[50258, 17377, 2062, 12, 2707, 50257]])
        scores = [torch.zeros(1)] * 5
        out = MagicMock()
        out.sequences = seq_tensor
        out.scores = scores
        fake_model.generate.return_value = out

        # Transition log-probs for the 5 generated tokens:
        # 17377 -> log(0.95)
        # 2062  -> log(0.31)
        # 12    -> log(0.80)
        # 2707  -> log(0.70)
        # 50257 -> log(0.99)
        fake_model.compute_transition_scores.return_value = torch.tensor(
            [[math.log(0.95), math.log(0.31), math.log(0.80), math.log(0.70), math.log(0.99)]]
        )

        decode_map = {
            17377: "group",
            2062: " mid",
            12: "-",
            2707: "port",
            50258: "<|startoftranscript|>",
            50257: "<|endoftext|>",
        }

        def fake_decode(token_ids, **_kwargs):
            if isinstance(token_ids, list):
                if len(token_ids) == 1:
                    return decode_map.get(token_ids[0], "")
                return "".join(decode_map.get(t, "") for t in token_ids)
            return decode_map.get(token_ids, "")

        fake_processor = MagicMock()
        fake_processor.feature_extractor.return_value.input_features.to.return_value = torch.zeros((1, 80, 3000))
        fake_processor.tokenizer.all_special_ids = {50258, 50257}
        fake_processor.tokenizer.decode.side_effect = fake_decode
        fake_processor.batch_decode.return_value = ["group mid-port"]

        model._whisper_salt = (fake_model, fake_processor)
        model._decode_audio_bytes = lambda *_a, **_kw: [0.0] * 1600

        with patch("torch.no_grad"):
            result = ss.SpeechModel._transcribe_whisper_salt(
                model, b"\x00" * 3200, 16000, "en", with_words=True
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.text, "group mid-port")
        self.assertIsNotNone(result.words)
        self.assertEqual(len(result.words), 2)

        # Word 1: "group", prob=0.95
        self.assertEqual(result.words[0].word, "group")
        self.assertAlmostEqual(result.words[0].prob, 0.95, places=2)

        # Word 2: "mid-port", min(0.31, 0.80, 0.70) = 0.31
        self.assertEqual(result.words[1].word, "mid-port")
        self.assertAlmostEqual(result.words[1].prob, 0.31, places=2)

        # Mean prob: (0.95 + 0.31) / 2 = 0.63
        self.assertAlmostEqual(result.mean_word_prob, 0.63, places=2)
