"""Unit tests for ``ml/scripts/fine_tune_gemma.py`` (2026 LoRA fine-tuner).

Coverage:
    * Helpers — find_sibling_splits, find_dataset_manifest, _digest_file,
      _try_flash_attention_2, find_training_data, load_jsonl
    * Messages rendering — _messages_to_gemma_text, _messages_to_llama_text
    * Format functions — every branch of format_for_gemma / llama / t5
      (messages, instruction/Alpaca, question/answer, prompt/completion,
      empty fallback)
    * CLI parser — smoke tests for new flags + value parsing
    * SFTConfig construction — verifies the exact kwargs ``train()`` passes
      are accepted by the installed TRL ``SFTConfig``
    * train() signature — verifies the public callable shape
    * setup_model_and_tokenizer + apply_lora — slow integration tests
      using ``hf-internal-testing/tiny-random-LlamaForCausalLM`` (≈1 MB).
      Gated by ``@pytest.mark.slow`` and skipped automatically if torch /
      peft / transformers are not importable.

Run only fast tests:
    pytest tests/test_fine_tune_gemma.py -m "not slow"

Run everything (downloads ~1 MB tiny model from HF):
    pytest tests/test_fine_tune_gemma.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FT_GEMMA_PATH = PROJECT_ROOT / "ml" / "scripts" / "fine_tune_gemma.py"


# ---------------------------------------------------------------------------
# Module loader fixture — fine_tune_gemma is a script, not an importable
# package, so we load it via importlib.util once per session.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ft():
    spec = importlib.util.spec_from_file_location("ft_gemma", str(FT_GEMMA_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_digest_file_is_stable(self, ft, tmp_path):
        p = tmp_path / "x.txt"
        p.write_bytes(b"hello world")
        d1 = ft._digest_file(p)
        d2 = ft._digest_file(p)
        assert d1 == d2
        assert len(d1) == 12  # short SHA-256 prefix
        # Distinct content → distinct digest
        p2 = tmp_path / "y.txt"
        p2.write_bytes(b"hello world!")
        assert ft._digest_file(p2) != d1

    def test_find_sibling_splits_resolves_val_and_test(self, ft, tmp_path):
        train = tmp_path / "train.messages.jsonl"
        val = tmp_path / "val.messages.jsonl"
        test = tmp_path / "test.messages.jsonl"
        for p in (train, val, test):
            p.write_text("{}\n")

        v, t = ft.find_sibling_splits(train)
        assert v == val
        assert t == test

    def test_find_sibling_splits_returns_none_when_missing(self, ft, tmp_path):
        train = tmp_path / "train.messages.jsonl"
        train.write_text("{}\n")
        v, t = ft.find_sibling_splits(train)
        assert v is None
        assert t is None

    def test_find_sibling_splits_returns_none_for_non_train_file(self, ft, tmp_path):
        # Filename does not contain "train"
        train = tmp_path / "training_data.messages.jsonl"
        train.write_text("{}\n")
        v, t = ft.find_sibling_splits(train)
        # The function looks for "train" substring; "training_data" contains it,
        # so val/test resolutions are valdataing_data / testing_data — nonexistent.
        assert v is None
        assert t is None

    def test_find_sibling_splits_only_val(self, ft, tmp_path):
        # Val exists, test does not — should return (val_path, None)
        train = tmp_path / "train.messages.jsonl"
        val = tmp_path / "val.messages.jsonl"
        train.write_text("{}\n")
        val.write_text("{}\n")

        v, t = ft.find_sibling_splits(train)
        assert v == val
        assert t is None

    def test_find_dataset_manifest_present(self, ft, tmp_path):
        train = tmp_path / "train.messages.jsonl"
        train.write_text("{}\n")
        manifest = tmp_path / "manifest.json"
        manifest.write_text("{}")
        assert ft.find_dataset_manifest(train) == manifest

    def test_find_dataset_manifest_missing(self, ft, tmp_path):
        train = tmp_path / "train.messages.jsonl"
        train.write_text("{}\n")
        assert ft.find_dataset_manifest(train) is None

    def test_try_flash_attention_2_returns_none_or_string(self, ft):
        # Either flash_attn is installed and returns "flash_attention_2"
        # or it's missing and returns None. Both are valid.
        result = ft._try_flash_attention_2()
        assert result is None or result == "flash_attention_2"

    def test_load_jsonl_skips_invalid_lines(self, ft, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text(
            '{"a": 1}\n'
            "not json\n"
            "\n"  # blank
            '{"b": 2}\n'
        )
        rows = ft.load_jsonl(p)
        assert rows == [{"a": 1}, {"b": 2}]

    def test_find_training_data_prefers_messages_jsonl(self, ft, tmp_path, monkeypatch):
        # Create a fake artifacts/training_data layout in a temp dir
        artifacts = tmp_path / "artifacts" / "training_data"
        artifacts.mkdir(parents=True)
        legacy = tmp_path / "artifacts" / "training_data.jsonl"
        messages = artifacts / "train.messages.jsonl"
        legacy.write_text("{}\n")
        messages.write_text("{}\n")

        monkeypatch.setattr(ft, "ARTIFACTS_DIR", tmp_path / "artifacts")
        monkeypatch.setattr(ft, "DATA_ROOT", tmp_path / "Data")

        result = ft.find_training_data()
        # train.messages.jsonl is at the top of TRAINING_DATA_FILES, and
        # artifacts/training_data is at the top of search_dirs, so it wins.
        assert result == messages


# ---------------------------------------------------------------------------
# Messages rendering — direct tests of the helper functions
# ---------------------------------------------------------------------------


class TestMessagesToGemmaText:
    def test_basic_user_assistant_pair(self, ft):
        msgs = [
            {"role": "user", "content": "What is VAT?"},
            {"role": "assistant", "content": "18 percent."},
        ]
        out = ft._messages_to_gemma_text(msgs)
        assert "<start_of_turn>user" in out
        assert "<start_of_turn>model" in out
        assert "What is VAT?" in out
        assert "18 percent." in out

    def test_system_is_folded_into_first_user(self, ft):
        # Gemma-2 has no system role — its template folds system into user.
        msgs = [
            {"role": "system", "content": "You are a tax assistant."},
            {"role": "user", "content": "What is VAT?"},
            {"role": "assistant", "content": "18%"},
        ]
        out = ft._messages_to_gemma_text(msgs)
        # System and user appear together inside the user turn (matches
        # tokenizer.apply_chat_template for gemma-2-it).
        assert "You are a tax assistant." in out
        assert "What is VAT?" in out
        # "system" role tag is NOT present (folded away)
        assert "<start_of_turn>system" not in out

    def test_assistant_alias_model_role_accepted(self, ft):
        # Some sources emit role="model" instead of "assistant"; we accept both.
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "model", "content": "Hello"},
        ]
        out = ft._messages_to_gemma_text(msgs)
        assert "<start_of_turn>model" in out
        assert "Hello" in out

    def test_multi_turn_dialog(self, ft):
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        out = ft._messages_to_gemma_text(msgs)
        # Both turns rendered with end_of_turn markers
        assert out.count("<start_of_turn>user") == 2
        assert out.count("<start_of_turn>model") == 2

    def test_empty_content_is_skipped(self, ft):
        msgs = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A"},
        ]
        out = ft._messages_to_gemma_text(msgs)
        # Empty system shouldn't blow up; should just be ignored
        assert "Q" in out
        assert "A" in out


class TestMessagesToLlamaText:
    def test_keeps_system_as_proper_role(self, ft):
        # Llama-3 has a real system role — must NOT fold into user.
        msgs = [
            {"role": "system", "content": "You are a tax assistant."},
            {"role": "user", "content": "VAT?"},
            {"role": "assistant", "content": "18%"},
        ]
        out = ft._messages_to_llama_text(msgs)
        assert "<|start_header_id|>system<|end_header_id|>" in out
        assert "<|start_header_id|>user<|end_header_id|>" in out
        assert "<|start_header_id|>assistant<|end_header_id|>" in out
        assert "<|begin_of_text|>" in out
        # All three contents preserved
        assert "You are a tax assistant." in out
        assert "VAT?" in out
        assert "18%" in out

    def test_eot_after_each_turn(self, ft):
        msgs = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A"},
        ]
        out = ft._messages_to_llama_text(msgs)
        # Each rendered turn should be terminated by <|eot_id|>
        assert out.count("<|eot_id|>") == 2

    def test_unknown_role_falls_back_to_user(self, ft):
        msgs = [
            {"role": "weird", "content": "garbage"},
            {"role": "assistant", "content": "OK"},
        ]
        out = ft._messages_to_llama_text(msgs)
        # "weird" gets coerced to user
        assert "<|start_header_id|>user<|end_header_id|>" in out
        assert "garbage" in out


# ---------------------------------------------------------------------------
# format_for_gemma — every branch
# ---------------------------------------------------------------------------


class TestFormatForGemma:
    def test_messages_branch_takes_priority(self, ft):
        # If both messages and instruction are present, messages wins.
        ex = {
            "messages": [
                {"role": "user", "content": "msg q"},
                {"role": "assistant", "content": "msg a"},
            ],
            "instruction": "instr q",
            "output": "instr a",
        }
        out = ft.format_for_gemma(ex)
        assert "msg q" in out["text"]
        assert "instr q" not in out["text"]

    def test_already_gemma_text_passthrough(self, ft):
        ex = {"text": "<start_of_turn>user\nQ<end_of_turn>\n<start_of_turn>model\nA<end_of_turn>"}
        out = ft.format_for_gemma(ex)
        assert out["text"] == ex["text"]

    def test_alpaca_instruction(self, ft):
        ex = {"instruction": "What is VAT?", "input": "", "output": "18%"}
        out = ft.format_for_gemma(ex)
        assert "What is VAT?" in out["text"]
        assert "<start_of_turn>user" in out["text"]
        assert "<start_of_turn>model" in out["text"]
        assert "18%" in out["text"]

    def test_alpaca_with_input_context(self, ft):
        ex = {
            "instruction": "Summarise the passage",
            "input": "URA collects taxes.",
            "output": "Tax collection.",
        }
        out = ft.format_for_gemma(ex)
        # Both instruction and input are concatenated into the user turn
        assert "Summarise the passage" in out["text"]
        assert "URA collects taxes." in out["text"]
        assert "Tax collection." in out["text"]

    def test_question_answer_format(self, ft):
        ex = {"question": "Q?", "answer": "A."}
        out = ft.format_for_gemma(ex)
        assert "Q?" in out["text"]
        assert "A." in out["text"]
        assert "<start_of_turn>user" in out["text"]

    def test_prompt_completion_format(self, ft):
        ex = {"prompt": "P", "completion": "C"}
        out = ft.format_for_gemma(ex)
        assert "P" in out["text"]
        assert "C" in out["text"]

    def test_empty_example_returns_empty_text(self, ft):
        ex = {"foo": "bar"}
        out = ft.format_for_gemma(ex)
        assert out == {"text": ""}


# ---------------------------------------------------------------------------
# format_for_llama — every branch
# ---------------------------------------------------------------------------


class TestFormatForLlama:
    def test_messages_branch_uses_llama_template(self, ft):
        ex = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "u"},
                {"role": "assistant", "content": "a"},
            ]
        }
        out = ft.format_for_llama(ex)
        assert "<|begin_of_text|>" in out["text"]
        assert "<|start_header_id|>system<|end_header_id|>" in out["text"]

    def test_alpaca_instruction(self, ft):
        ex = {"instruction": "Q?", "input": "", "output": "A."}
        out = ft.format_for_llama(ex)
        assert "<|begin_of_text|>" in out["text"]
        assert "<|start_header_id|>user<|end_header_id|>" in out["text"]
        assert "Q?" in out["text"]
        assert "A." in out["text"]

    def test_alpaca_with_input(self, ft):
        ex = {"instruction": "summarise", "input": "context here", "output": "summary"}
        out = ft.format_for_llama(ex)
        assert "summarise" in out["text"]
        assert "context here" in out["text"]
        assert "summary" in out["text"]

    def test_question_answer_format(self, ft):
        ex = {"question": "Q?", "answer": "A."}
        out = ft.format_for_llama(ex)
        assert "Q?" in out["text"]
        assert "A." in out["text"]
        assert "<|eot_id|>" in out["text"]

    def test_prompt_completion_format(self, ft):
        ex = {"prompt": "P", "completion": "C"}
        out = ft.format_for_llama(ex)
        assert "P" in out["text"]
        assert "C" in out["text"]

    def test_empty_example_returns_empty(self, ft):
        out = ft.format_for_llama({"x": "y"})
        assert out == {"text": ""}


# ---------------------------------------------------------------------------
# format_for_t5 — every branch
# ---------------------------------------------------------------------------


class TestFormatForT5:
    def test_messages_branch(self, ft):
        ex = {
            "messages": [
                {"role": "system", "content": "ignored for t5"},
                {"role": "user", "content": "What is VAT?"},
                {"role": "assistant", "content": "18%"},
            ]
        }
        out = ft.format_for_t5(ex)
        assert "input_text" in out and "target_text" in out
        # System turn dropped (T5 has no chat role concept)
        assert "ignored for t5" not in out["input_text"]
        assert "What is VAT?" in out["input_text"]
        assert out["target_text"] == "18%"

    def test_alpaca_instruction(self, ft):
        ex = {"instruction": "Q", "input": "", "output": "A"}
        out = ft.format_for_t5(ex)
        assert out == {"input_text": "question: Q", "target_text": "A"}

    def test_alpaca_with_input(self, ft):
        ex = {"instruction": "Q", "input": "ctx", "output": "A"}
        out = ft.format_for_t5(ex)
        assert "question: Q" in out["input_text"]
        assert "context: ctx" in out["input_text"]
        assert out["target_text"] == "A"

    def test_question_answer_format(self, ft):
        ex = {"question": "Q?", "answer": "A."}
        out = ft.format_for_t5(ex)
        assert out == {"input_text": "question: Q?", "target_text": "A."}

    def test_empty_returns_empty_strings(self, ft):
        out = ft.format_for_t5({"foo": "bar"})
        assert out == {"input_text": "", "target_text": ""}

    def test_messages_with_only_system_returns_empty(self, ft):
        ex = {"messages": [{"role": "system", "content": "only system"}]}
        out = ft.format_for_t5(ex)
        # No user/assistant → falls through to empty
        assert out == {"input_text": "", "target_text": ""}


# ---------------------------------------------------------------------------
# CLI parser smoke tests
# ---------------------------------------------------------------------------


class TestCLIParser:
    def _build(self, ft):
        # main() builds the parser inline; we re-create it the same way by
        # calling the function but capturing its argparse instance via the
        # public main entry point with --help (which exits 0). Easier:
        # extract the parser by inspecting main()'s code is fragile, so we
        # smoke-test by invoking with --help in a subprocess.
        import subprocess

        return subprocess.run(
            [sys.executable, str(FT_GEMMA_PATH), "--help"],
            capture_output=True, text=True, check=False,
        )

    def test_help_lists_new_2026_flags(self, ft):
        result = self._build(ft)
        assert result.returncode == 0
        out = result.stdout
        # 2026 additions
        for flag in (
            "--use-rslora",
            "--use-dora",
            "--neftune-alpha",
            "--weight-decay",
            "--seed",
            "--resume-from-checkpoint",
            "--report-to",
            "--push-to-hub",
            "--save-merged",
            "--no-group-by-length",
        ):
            assert flag in out, f"missing flag {flag} in --help output"

    def test_help_lists_legacy_flags(self, ft):
        result = self._build(ft)
        out = result.stdout
        for flag in ("--target", "--data", "--epochs", "--batch-size", "--lora-r"):
            assert flag in out, f"missing legacy flag {flag} in --help output"


# ---------------------------------------------------------------------------
# SFTConfig kwargs compatibility
# ---------------------------------------------------------------------------
#
# Reproduces the exact ``common_args`` + ``sft_only`` dicts that ``train()``
# constructs and asserts SFTConfig(**) accepts them. This is the regression
# guard for the TRL 1.0 migration — if a future TRL version drops one of
# our kwargs, this test will fail loudly with the offending key name.


class TestSFTConfigCompatibility:
    def _expected_kwargs(self):
        common_args = dict(
            output_dir="/tmp/sft_test",
            num_train_epochs=3,
            per_device_train_batch_size=4,
            per_device_eval_batch_size=4,
            gradient_accumulation_steps=4,
            learning_rate=2e-4,
            warmup_ratio=0.03,
            logging_steps=10,
            save_steps=50,
            eval_steps=50,
            eval_strategy="steps",
            save_strategy="steps",
            save_total_limit=3,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            bf16=False,
            fp16=False,
            optim="adamw_torch",  # don't require bnb in tests
            report_to="none",
            lr_scheduler_type="cosine",
            seed=42,
            data_seed=42,
            gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            max_grad_norm=1.0,
            weight_decay=0.01,
            dataloader_num_workers=0,
            dataloader_pin_memory=False,
            logging_first_step=True,
            eval_accumulation_steps=16,
            group_by_length=True,
            save_safetensors=True,
        )
        sft_only = dict(
            max_length=2048,
            packing=True,
            completion_only_loss=True,
            dataset_text_field="text",
            neftune_noise_alpha=5.0,
            dataset_num_proc=1,
            remove_unused_columns=True,
        )
        return common_args, sft_only

    def test_sftconfig_accepts_all_kwargs(self, ft):
        trl = pytest.importorskip("trl")
        SFTConfig = trl.SFTConfig
        common_args, sft_only = self._expected_kwargs()
        cfg = SFTConfig(**common_args, **sft_only)
        # Spot-check critical fields landed
        assert cfg.max_length == 2048
        assert cfg.packing is True
        assert cfg.completion_only_loss is True
        assert cfg.gradient_checkpointing_kwargs == {"use_reentrant": False}
        assert cfg.weight_decay == 0.01
        assert cfg.seed == 42
        assert cfg.group_by_length is True

    def test_sfttrainer_signature_has_processing_class(self, ft):
        trl = pytest.importorskip("trl")
        import inspect

        sig = inspect.signature(trl.SFTTrainer.__init__)
        params = set(sig.parameters.keys())
        assert "processing_class" in params, (
            "TRL >= 0.12 should expose processing_class on SFTTrainer"
        )
        # Deprecated kwargs should NOT be present
        for banned in ("tokenizer", "dataset_text_field", "max_seq_length", "packing"):
            assert banned not in params, (
                f"SFTTrainer.__init__ unexpectedly still accepts {banned!r}; "
                "the train() function may need an update"
            )


# ---------------------------------------------------------------------------
# train() public signature
# ---------------------------------------------------------------------------


class TestTrainSignature:
    def test_train_required_params(self, ft):
        import inspect

        sig = inspect.signature(ft.train)
        params = list(sig.parameters.keys())
        # Positional / keyword args we depend on from main()
        for required in (
            "model", "tokenizer", "train_dataset", "eval_dataset", "output_dir",
        ):
            assert required in params

    def test_train_has_2026_kwargs(self, ft):
        import inspect

        sig = inspect.signature(ft.train)
        params = sig.parameters
        for kw in (
            "use_rslora", "use_dora", "neftune_alpha", "report_to",
            "resume_from_checkpoint", "push_to_hub", "save_merged",
            "group_by_length", "seed", "weight_decay", "dataset_metadata",
        ):
            assert kw in params, f"train() missing kwarg {kw}"

    def test_apply_lora_signature_has_rslora_dora(self, ft):
        import inspect

        sig = inspect.signature(ft.apply_lora)
        assert "use_rslora" in sig.parameters
        assert "use_dora" in sig.parameters

    def test_setup_model_signature_has_distributed(self, ft):
        import inspect

        sig = inspect.signature(ft.setup_model_and_tokenizer)
        assert "distributed" in sig.parameters


# ---------------------------------------------------------------------------
# Slow integration tests — use a 1MB tiny test model
# ---------------------------------------------------------------------------
#
# These tests download ``hf-internal-testing/tiny-random-LlamaForCausalLM``
# (~1 MB) once and cache it. They are marked ``slow`` so CI can opt out
# with ``-m "not slow"``.

TINY_MODEL = "hf-internal-testing/tiny-random-LlamaForCausalLM"


def _have_torch_and_peft() -> bool:
    try:
        import torch  # noqa: F401
        import peft  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _peft_jit_works() -> bool:
    """Probe whether PEFT can wrap a tiny model on this host.

    On systems without Python dev headers (``Python.h``), PEFT/Triton's
    JIT compilation fails the moment ``get_peft_model`` is called. We
    detect that here once and skip the LoRA integration tests gracefully
    rather than failing them with a confusing C compile error.
    """
    if not _have_torch_and_peft():
        return False
    try:
        import torch
        from transformers import AutoModelForCausalLM
        from peft import LoraConfig, get_peft_model

        # Force CPU so Triton isn't triggered.
        with torch.no_grad():
            tiny = AutoModelForCausalLM.from_pretrained(
                TINY_MODEL,
                torch_dtype=torch.float32,
            )
            cfg = LoraConfig(
                r=2,
                lora_alpha=4,
                target_modules=["q_proj", "v_proj"],
                task_type="CAUSAL_LM",
            )
            _ = get_peft_model(tiny, cfg)
        return True
    except Exception:
        return False


# Probe once per session — PEFT JIT compilation is the main blocker on
# bare-metal hosts without python3-dev installed.
_PEFT_JIT_OK = None


def _peft_works() -> bool:
    global _PEFT_JIT_OK
    if _PEFT_JIT_OK is None:
        _PEFT_JIT_OK = _peft_jit_works()
    return _PEFT_JIT_OK


@pytest.mark.slow
@pytest.mark.skipif(not _have_torch_and_peft(), reason="torch/peft/transformers not installed")
class TestSetupModelTokenizer:
    def test_loads_tiny_llama_without_quantization(self, ft):
        # 4-bit quant requires bitsandbytes + GPU; tiny test model on CPU.
        model, tokenizer, is_t5 = ft.setup_model_and_tokenizer(
            TINY_MODEL,
            use_4bit=False,
            use_8bit=False,
            max_seq_length=128,
            distributed=False,
        )
        assert is_t5 is False
        assert model is not None
        assert tokenizer is not None
        # Pad token must be set after load (we either reuse EOS or UNK)
        assert tokenizer.pad_token_id is not None
        # Right padding required for causal LM training
        assert tokenizer.padding_side == "right"
        # max_seq_length should be applied to tokenizer
        assert tokenizer.model_max_length >= 128

    def test_distributed_does_not_set_device_map(self, ft, monkeypatch):
        # Capture model_kwargs that would be passed to from_pretrained.
        captured = {}

        from transformers import AutoModelForCausalLM

        original = AutoModelForCausalLM.from_pretrained

        def _spy(model_id, **kwargs):
            captured.update(kwargs)
            return original(model_id, **kwargs)

        monkeypatch.setattr(AutoModelForCausalLM, "from_pretrained", _spy)

        ft.setup_model_and_tokenizer(
            TINY_MODEL,
            use_4bit=False,
            use_8bit=False,
            max_seq_length=128,
            distributed=True,  # ← key
        )
        # In distributed mode, device_map must NOT be set (DDP places the
        # model on each process's local GPU itself)
        assert "device_map" not in captured

    def test_non_distributed_sets_device_map_when_cuda_available(
        self, ft, monkeypatch
    ):
        import torch

        captured = {}
        from transformers import AutoModelForCausalLM

        original = AutoModelForCausalLM.from_pretrained

        def _spy(model_id, **kwargs):
            captured.update(kwargs)
            return original(model_id, **kwargs)

        monkeypatch.setattr(AutoModelForCausalLM, "from_pretrained", _spy)
        ft.setup_model_and_tokenizer(
            TINY_MODEL,
            use_4bit=False,
            use_8bit=False,
            max_seq_length=128,
            distributed=False,
        )
        if torch.cuda.is_available():
            assert captured.get("device_map") == "auto"
        else:
            # On CPU we don't set device_map
            assert "device_map" not in captured


@pytest.mark.slow
@pytest.mark.skipif(
    not _have_torch_and_peft(),
    reason="torch/peft/transformers not installed",
)
class TestApplyLora:
    @classmethod
    def setup_class(cls):
        if not _peft_works():
            pytest.skip(
                "PEFT JIT compilation unavailable (likely missing python3-dev "
                "headers); skipping LoRA integration tests. The format / "
                "signature / SFTConfig tests still cover the API surface.",
                allow_module_level=False,
            )

    def test_apply_basic_lora(self, ft):
        model, _, _ = ft.setup_model_and_tokenizer(
            TINY_MODEL, use_4bit=False, use_8bit=False, max_seq_length=128,
            distributed=False,
        )
        wrapped = ft.apply_lora(
            model, is_t5=False, config={"r": 4, "lora_alpha": 8},
        )
        # Trainable param count should be a small fraction of total
        trainable = sum(p.numel() for p in wrapped.parameters() if p.requires_grad)
        total = sum(p.numel() for p in wrapped.parameters())
        assert trainable > 0
        assert trainable < total
        # PEFT injects "lora_A"/"lora_B" parameters
        param_names = {n for n, _ in wrapped.named_parameters() if "lora" in n.lower()}
        assert param_names, "expected at least one LoRA parameter"

    def test_apply_lora_with_rslora_flag(self, ft):
        model, _, _ = ft.setup_model_and_tokenizer(
            TINY_MODEL, use_4bit=False, use_8bit=False, max_seq_length=128,
            distributed=False,
        )
        wrapped = ft.apply_lora(
            model, is_t5=False, config={"r": 4, "lora_alpha": 8},
            use_rslora=True,
        )
        # PeftModel exposes peft_config — check use_rslora was set
        peft_cfg = next(iter(wrapped.peft_config.values()))
        assert getattr(peft_cfg, "use_rslora", False) is True

    def test_apply_lora_with_dora_flag(self, ft):
        model, _, _ = ft.setup_model_and_tokenizer(
            TINY_MODEL, use_4bit=False, use_8bit=False, max_seq_length=128,
            distributed=False,
        )
        wrapped = ft.apply_lora(
            model, is_t5=False, config={"r": 4, "lora_alpha": 8},
            use_dora=True,
        )
        peft_cfg = next(iter(wrapped.peft_config.values()))
        assert getattr(peft_cfg, "use_dora", False) is True


# ---------------------------------------------------------------------------
# Trainer construction smoke test (no real training)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not _have_torch_and_peft(),
    reason="torch/peft/transformers not installed",
)
class TestTrainConstruction:
    """Verify ``train()`` can construct the SFTTrainer + SFTConfig
    successfully. We monkeypatch ``trainer.train`` to a no-op so the test
    completes in seconds without ever doing a backwards pass."""

    @classmethod
    def setup_class(cls):
        if not _peft_works():
            pytest.skip(
                "PEFT JIT compilation unavailable; skipping trainer "
                "construction integration test. The SFTConfig kwargs test "
                "(non-slow) still verifies the trainer's expected API.",
                allow_module_level=False,
            )


    def _make_messages_dataset(self, n: int = 8):
        from datasets import Dataset

        rows = [
            {
                "messages": [
                    {"role": "user", "content": f"What is tax {i}?"},
                    {"role": "assistant", "content": f"Tax {i} is computed at the standard rate."},
                ],
                "source": "test",
                "source_type": "csv_faq",
                "task": "qa",
                "language": "en",
                "content_hash": f"{i:016x}",
                "text": f"<start_of_turn>user\nWhat is tax {i}?<end_of_turn>\n"
                        f"<start_of_turn>model\nTax {i} is computed.<end_of_turn>",
            }
            for i in range(n)
        ]
        return Dataset.from_list(rows)

    def test_trainer_constructs_with_2026_kwargs(self, ft, tmp_path, monkeypatch):
        model, tokenizer, _ = ft.setup_model_and_tokenizer(
            TINY_MODEL, use_4bit=False, use_8bit=False, max_seq_length=128,
            distributed=False,
        )
        model = ft.apply_lora(
            model, is_t5=False, config={"r": 4, "lora_alpha": 8},
            use_rslora=True,
        )

        train_ds = self._make_messages_dataset(8)
        eval_ds = self._make_messages_dataset(4)

        # Replace trainer.train with a no-op so we don't actually fine-tune.
        from trl import SFTTrainer

        original_train = SFTTrainer.train

        def _no_op_train(self, *a, **kw):
            # Pretend training happened: write a fake state.
            class _S:
                log_history = [{"loss": 1.0, "step": 1}]
            self.state = _S()
            return None

        monkeypatch.setattr(SFTTrainer, "train", _no_op_train)
        # Also stub evaluate so we don't actually eval
        monkeypatch.setattr(SFTTrainer, "evaluate", lambda self, *a, **kw: {"eval_loss": 1.0})
        # Stub save_model to write a marker file (avoid pickling tiny model)
        monkeypatch.setattr(
            SFTTrainer, "save_model",
            lambda self, output_dir: Path(output_dir).mkdir(parents=True, exist_ok=True),
        )

        out_dir = tmp_path / "trainer_out"
        ft.train(
            model, tokenizer, train_ds, eval_ds, out_dir,
            max_seq_length=128,
            num_epochs=1,
            batch_size=2,
            gradient_accumulation_steps=1,
            learning_rate=1e-4,
            warmup_ratio=0.03,
            weight_decay=0.01,
            model_type="gemma",
            is_t5=False,
            lora_r=4,
            lora_alpha=8,
            lora_dropout=0.05,
            use_rslora=True,
            neftune_alpha=5.0,
            report_to="none",
            group_by_length=False,  # tiny dataset; avoids edge cases
            seed=42,
            dataset_metadata={"train_sha256": "deadbeef" * 1},
        )

        # Verify training_config.json was written with the dataset metadata
        cfg_path = out_dir / "training_config.json"
        assert cfg_path.exists()
        cfg = json.loads(cfg_path.read_text())
        assert cfg["pipeline_version"] == "2026.1.0"
        assert cfg["dataset_metadata"]["train_sha256"].startswith("deadbeef")
        assert cfg["lora_config"]["use_rslora"] is True
        assert cfg["weight_decay"] == 0.01
        assert cfg["seed"] == 42

        # Restore (paranoia)
        monkeypatch.setattr(SFTTrainer, "train", original_train)
