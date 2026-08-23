"""Sunbird/spark-tts-salt wiring: speaker-id table, availability gating, and
the prompt/token-parsing contract — the parts that do not need the actual
~2GB LLM + BiCodec weights or CUDA (this backend is off by default and has
not been verified end to end in this project; see the module docstring in
app.spark_tts_salt and docs/runbooks/salt-speech-backends.md for why).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import spark_tts_salt as sts  # noqa: E402


class SpeakerIdTableTests(unittest.TestCase):
    def test_the_four_confirmed_locales_are_mapped(self) -> None:
        for locale, expected in (("lg", 248), ("sw", 246), ("ach", 241), ("nyn", 243)):
            with self.subTest(locale=locale):
                self.assertEqual(sts.speaker_id_for(locale), expected)

    def test_english_has_no_default_id(self) -> None:
        """The model card claims English coverage but its training table
        lists no speaker id for it — guessing one repeats the exact mistake
        already caught and reverted for the ASR model's language tokens."""
        with self.assertRaises(KeyError):
            sts.speaker_id_for("en")

    def test_an_unrelated_locale_also_raises(self) -> None:
        with self.assertRaises(KeyError):
            sts.speaker_id_for("fr")

    def test_env_override_replaces_the_default(self) -> None:
        """The override mechanism (int(os.getenv(...)) per locale at import
        time) without reloading the real module — reload's cross-test state
        leakage is a bigger risk than the thing being tested here."""
        with patch.dict(sts.SPARK_TTS_SPEAKER_IDS, {"lg": 999}):
            self.assertEqual(sts.speaker_id_for("lg"), 999)
        self.assertEqual(sts.speaker_id_for("lg"), 248)  # restored


class LoadGatingTests(unittest.TestCase):
    """load() must fail closed — cheaply, with no heavy import — at every
    stage short of a real, complete setup."""

    def test_disabled_by_default_raises_without_importing_torch(self) -> None:
        with patch.object(sts, "SPARK_TTS_ENABLED", False):
            with self.assertRaises(sts.SparkTtsUnavailable):
                sts.load()

    def test_enabled_but_no_repo_dir_raises(self) -> None:
        with patch.object(sts, "SPARK_TTS_ENABLED", True), \
                patch.object(sts, "SPARK_TTS_REPO_DIR", None):
            with self.assertRaises(sts.SparkTtsUnavailable):
                sts.load()

    def test_enabled_with_a_repo_dir_missing_the_sparktts_package_raises(self) -> None:
        with patch.object(sts, "SPARK_TTS_ENABLED", True), \
                patch.object(sts, "SPARK_TTS_REPO_DIR", "/nonexistent-spark-tts-checkout"):
            with self.assertRaises(sts.SparkTtsUnavailable) as ctx:
                sts.load()
            self.assertIn("sparktts", str(ctx.exception))


class SynthesizePromptAndParsingTests(unittest.TestCase):
    """The per-call path against a stand-in model/tokenizer/audio_tokenizer
    — proves the prompt format and token-regex parsing match the model
    card's documented contract without needing real weights."""

    def _backend(self, generated_suffix: str) -> sts.SparkTtsSalt:
        tokenizer = MagicMock()
        tokenizer.eos_token_id = 999
        call_inputs = MagicMock()
        call_inputs.to.return_value = {"input_ids": MagicMock(shape=(1, 5))}
        tokenizer.return_value = call_inputs
        tokenizer.batch_decode.return_value = [generated_suffix]
        model = MagicMock()
        model.generate.return_value = MagicMock()
        model.generate.return_value.__getitem__.return_value = "SLICED"
        audio_tokenizer = MagicMock()

        import numpy as np

        audio_tokenizer.detokenize.return_value = np.zeros(1600, dtype="float32")
        return sts.SparkTtsSalt(model, tokenizer, audio_tokenizer, "cpu", 16000)

    def test_the_prompt_uses_the_documented_control_token_shape(self) -> None:
        backend = self._backend("<|bicodec_global_1|><|bicodec_semantic_2|>")
        # Real torch runs here (already a hard dependency) — only enough to
        # build two tiny tensors from short int lists, so there is nothing
        # worth mocking away.
        backend.synthesize("Oli otya?", language="lg")
        prompt = backend.tokenizer.call_args.args[0][0]
        self.assertEqual(
            prompt,
            "248: <|task_tts|><|start_content|>Oli otya?<|end_content|><|start_global_token|>",
        )

    def test_an_unmapped_language_raises_before_touching_the_model(self) -> None:
        backend = self._backend("")
        with self.assertRaises(sts.SparkTtsUnavailable):
            backend.synthesize("Hello", language="en")
        backend.model.generate.assert_not_called()

    def test_semantic_and_global_tokens_are_parsed_by_the_documented_regex(self) -> None:
        text = "noise<|bicodec_global_42|>more<|bicodec_semantic_7|><|bicodec_semantic_9|>"
        self.assertEqual(sts._GLOBAL_TOKEN_RE.findall(text), ["42"])
        self.assertEqual(sts._SEMANTIC_TOKEN_RE.findall(text), ["7", "9"])

    def test_a_generation_with_no_bicodec_tokens_raises_rather_than_returning_silence(self) -> None:
        backend = self._backend("no control tokens in this output at all")
        with self.assertRaises(sts.SparkTtsUnavailable):
            backend.synthesize("Oli otya?", language="lg")


if __name__ == "__main__":
    unittest.main()
