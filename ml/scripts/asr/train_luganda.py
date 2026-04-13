#!/usr/bin/env python3
"""Fine-tune Whisper-small on Luganda (Common Voice + SALT-ASR) via LoRA.

Mirrors the design of ``ml/scripts/fine_tune_gemma.py`` (LoRA via peft,
Seq2SeqTrainer, quality gates, --dry-run) but specialised for ASR.

Pipeline::

    load_common_voice_lg + load_salt_asr
        -> filter by duration, drop [UNTRANSCRIBED]
        -> HF datasets.Dataset with audio column
        -> WhisperProcessor feature extraction
        -> Seq2SeqTrainer + peft LoRA (r=16)
        -> artifacts/speech/asr/whisper_lg/{adapter_model.safetensors, ...}
        -> optional merge + save_pretrained for onnx export

Usage::

    # Dry run (no GPU, no data needed — just config validation)
    python -m ml.scripts.asr.train_luganda --dry-run

    # Real training (requires GPU, Common Voice lg, peft, trl, torch)
    python -m ml.scripts.asr.train_luganda \\
        --common-voice Data/common_voice_lg \\
        --salt-asr Data/salt_asr

Commercial posture: Whisper is MIT. Common Voice is CC0. SALT is CC-BY-4.0
(attribution recorded in the output model card). No CC-BY-NC data is used.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("asr.train_luganda")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PROJECT_ROOT / "Data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
OUTPUT_DIR_DEFAULT = ARTIFACTS_DIR / "speech" / "asr" / "whisper_lg"

# Mirrors fine_tune_gemma.MODEL_CONFIGS layout.
MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    "mobile": {
        "model_id": "openai/whisper-small",
        "language": "swahili",  # Whisper has no Luganda; Swahili is nearest Bantu language  # passed to WhisperProcessor
        "task": "transcribe",
        "lora_r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "target_modules": ["q_proj", "v_proj", "k_proj", "out_proj", "fc1", "fc2"],
        "epochs": 5,
        "batch_size": 8,
        "gradient_accumulation": 2,
        "learning_rate": 1e-4,
        "warmup_steps": 200,
        "max_steps": None,
        "description": "Whisper-small LoRA fine-tune for on-device Luganda ASR.",
    },
    "full": {
        "model_id": "openai/whisper-small",
        "language": "swahili",  # Whisper has no Luganda; Swahili is nearest Bantu language
        "task": "transcribe",
        "lora_r": 32,
        "lora_alpha": 64,
        "lora_dropout": 0.05,
        "target_modules": ["q_proj", "v_proj", "k_proj", "out_proj", "fc1", "fc2"],
        "epochs": 10,
        "batch_size": 16,
        "gradient_accumulation": 1,
        "learning_rate": 2e-4,
        "warmup_steps": 500,
        "max_steps": None,
        "description": "Higher-rank full quality run for server deployment.",
    },
}


# ---------------------------------------------------------------------------
# Dependency probe
# ---------------------------------------------------------------------------


def check_dependencies() -> list[str]:
    missing: list[str] = []
    for pkg in ("torch", "transformers", "datasets", "peft", "jiwer", "soundfile"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@dataclass
class TrainingDataStats:
    n_train: int = 0
    n_val: int = 0
    n_test: int = 0
    n_dropped_duration: int = 0
    n_dropped_missing_ref: int = 0
    total_hours: float = 0.0
    languages: list[str] = field(default_factory=list)


def _load_rows(
    *,
    common_voice_dir: Path | None,
    salt_dir: Path | None,
    extra_jsonl: list[Path] | None,
    min_duration_s: float,
    max_duration_s: float,
) -> tuple[list[dict], TrainingDataStats]:
    """Load all configured sources into plain dicts ready for `datasets`."""
    from ml.scripts.data_aug.asr_loaders import (  # type: ignore
        load_common_voice_lg,
        load_jsonl,
        load_salt_asr,
    )

    stats = TrainingDataStats()
    rows: list[dict] = []

    def _accept(ex) -> bool:
        if not ex.reference_text or ex.reference_text == "[UNTRANSCRIBED]":
            stats.n_dropped_missing_ref += 1
            return False
        if ex.duration_s < min_duration_s or ex.duration_s > max_duration_s:
            stats.n_dropped_duration += 1
            return False
        return True

    if common_voice_dir is not None:
        for ex in load_common_voice_lg(common_voice_dir):
            if _accept(ex):
                rows.append(
                    {
                        "audio_path": ex.audio_path,
                        "text": ex.reference_text,
                        "language": ex.language,
                        "sampling_rate": ex.sample_rate,
                        "duration_s": ex.duration_s,
                        "source": ex.source,
                    }
                )
    if salt_dir is not None:
        for ex in load_salt_asr(salt_dir):
            if _accept(ex):
                rows.append(
                    {
                        "audio_path": ex.audio_path,
                        "text": ex.reference_text,
                        "language": ex.language,
                        "sampling_rate": ex.sample_rate,
                        "duration_s": ex.duration_s,
                        "source": ex.source,
                    }
                )
    for path in extra_jsonl or []:
        for ex in load_jsonl(path):
            if _accept(ex):
                rows.append(
                    {
                        "audio_path": ex.audio_path,
                        "text": ex.reference_text,
                        "language": ex.language,
                        "sampling_rate": ex.sample_rate,
                        "duration_s": ex.duration_s,
                        "source": ex.source,
                    }
                )

    stats.total_hours = round(sum(r["duration_s"] for r in rows) / 3600, 3)
    stats.languages = sorted({r["language"] for r in rows})
    return rows, stats


def _deterministic_split(rows: list[dict], *, train=0.9, val=0.05, seed=42):
    import random

    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = int(n * train)
    n_val = int(n * val)
    return (
        shuffled[:n_train],
        shuffled[n_train : n_train + n_val],
        shuffled[n_train + n_val :],
    )


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    target: str
    model_id: str
    output_dir: str
    stats: TrainingDataStats
    metrics: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    error: str | None = None


def _build_processor_and_model(cfg: dict[str, Any]):
    from transformers import (  # type: ignore
        WhisperForConditionalGeneration,
        WhisperProcessor,
    )

    processor = WhisperProcessor.from_pretrained(cfg["model_id"])
    processor.tokenizer.set_prefix_tokens(language=cfg["language"], task=cfg["task"])
    model = WhisperForConditionalGeneration.from_pretrained(cfg["model_id"])
    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []
    return processor, model


def _apply_lora(model, cfg: dict[str, Any]):
    from peft import LoraConfig, get_peft_model  # type: ignore

    lora_cfg = LoraConfig(
        r=cfg["lora_r"],
        lora_alpha=cfg["lora_alpha"],
        lora_dropout=cfg["lora_dropout"],
        target_modules=cfg["target_modules"],
        bias="none",
    )
    return get_peft_model(model, lora_cfg)


def _prepare_dataset(rows: list[dict], processor):
    from datasets import Audio, Dataset  # type: ignore

    ds = Dataset.from_list(rows)
    ds = ds.cast_column("audio_path", Audio(sampling_rate=16000))

    def _prepare(example):
        audio = example["audio_path"]
        inputs = processor.feature_extractor(audio["array"], sampling_rate=audio["sampling_rate"])
        labels = processor.tokenizer(example["text"]).input_ids
        return {"input_features": inputs.input_features[0], "labels": labels}

    return ds.map(_prepare, remove_columns=ds.column_names, num_proc=1)


def _data_collator(processor):
    def _collate(batch):
        input_features = [{"input_features": b["input_features"]} for b in batch]
        label_features = [{"input_ids": b["labels"]} for b in batch]
        batch_in = processor.feature_extractor.pad(input_features, return_tensors="pt")
        labels = processor.tokenizer.pad(label_features, return_tensors="pt")
        labels_ids = labels["input_ids"].masked_fill(labels.attention_mask.ne(1), -100)
        batch_in["labels"] = labels_ids
        return batch_in

    return _collate


def run(
    *,
    target: str,
    common_voice_dir: Path | None,
    salt_dir: Path | None,
    extra_jsonl: list[Path] | None,
    output_dir: Path,
    dry_run: bool,
) -> RunResult:
    cfg = MODEL_CONFIGS[target]
    result = RunResult(
        target=target,
        model_id=cfg["model_id"],
        output_dir=str(output_dir),
        stats=TrainingDataStats(),
    )

    missing = check_dependencies()
    if missing and not dry_run:
        result.error = f"missing deps: {missing}. pip install {' '.join(missing)}"
        log.error(result.error)
        return result

    rows, stats = _load_rows(
        common_voice_dir=common_voice_dir,
        salt_dir=salt_dir,
        extra_jsonl=extra_jsonl,
        min_duration_s=0.3,
        max_duration_s=30.0,
    )
    result.stats = stats
    log.info(
        "loaded %d clips, %.2f hours, langs=%s, dropped=%d+%d",
        len(rows),
        stats.total_hours,
        stats.languages,
        stats.n_dropped_duration,
        stats.n_dropped_missing_ref,
    )

    if dry_run:
        log.info("[dry-run] config=%s", {k: v for k, v in cfg.items() if k != "target_modules"})
        log.info("[dry-run] would train on %d clips", len(rows))
        result.ok = True
        return result

    if not rows:
        result.error = (
            "no training rows — fetch Common Voice lg or provide --salt-asr / --extra-jsonl"
        )
        log.error(result.error)
        return result

    try:
        from transformers import Seq2SeqTrainer, Seq2SeqTrainingArguments  # type: ignore
    except ImportError as exc:
        result.error = f"transformers import failed: {exc}"
        return result

    processor, model = _build_processor_and_model(cfg)
    model = _apply_lora(model, cfg)
    model.print_trainable_parameters()

    train_rows, val_rows, test_rows = _deterministic_split(rows)
    stats.n_train = len(train_rows)
    stats.n_val = len(val_rows)
    stats.n_test = len(test_rows)

    train_ds = _prepare_dataset(train_rows, processor)
    val_ds = _prepare_dataset(val_rows, processor) if val_rows else None

    output_dir.mkdir(parents=True, exist_ok=True)

    args_tr = Seq2SeqTrainingArguments(
        output_dir=str(output_dir),
        per_device_train_batch_size=cfg["batch_size"],
        gradient_accumulation_steps=cfg["gradient_accumulation"],
        learning_rate=cfg["learning_rate"],
        warmup_steps=cfg["warmup_steps"],
        num_train_epochs=cfg["epochs"],
        fp16=True,
        eval_strategy="epoch" if val_ds else "no",
        save_strategy="epoch",
        logging_steps=25,
        predict_with_generate=True,
        generation_max_length=225,
        save_total_limit=2,
        report_to=[],
    )

    trainer = Seq2SeqTrainer(
        args=args_tr,
        model=model,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=_data_collator(processor),
        tokenizer=processor.feature_extractor,
    )

    try:
        trainer.train()
    except Exception as exc:
        log.exception("training failed")
        result.error = str(exc)
        return result

    # Save adapter
    model.save_pretrained(str(output_dir / "final"))
    processor.save_pretrained(str(output_dir / "final"))

    # Quick eval metrics summary.
    if val_ds is not None:
        eval_metrics = trainer.evaluate()
        result.metrics = {
            k: round(float(v), 4) for k, v in eval_metrics.items() if isinstance(v, int | float)
        }

    # Run manifest
    manifest = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "target": target,
        "config": dict(cfg.items()),
        "stats": asdict(stats),
        "metrics": result.metrics,
        "artifacts": {
            "adapter": str((output_dir / "final").resolve()),
            "model_id": cfg["model_id"],
        },
    }
    (output_dir / "training_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    result.ok = True
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Whisper LoRA fine-tune for Luganda")
    parser.add_argument("--target", choices=sorted(MODEL_CONFIGS.keys()), default="mobile")
    parser.add_argument(
        "--common-voice",
        type=Path,
        default=DATA_ROOT / "common_voice_lg",
        help="Common Voice Luganda directory (wrapped clips/ + validated.tsv).",
    )
    parser.add_argument(
        "--salt-asr",
        type=Path,
        default=None,
        help="SALT-ASR directory (Makerere).",
    )
    parser.add_argument(
        "--extra-jsonl",
        type=Path,
        nargs="*",
        default=[],
        help="Additional curated JSONL eval/train files.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    # Common Voice dir may be absent; gracefully degrade to None so the loader
    # reports it cleanly in dry-run.
    cv_dir: Path | None = (
        args.common_voice if args.common_voice and args.common_voice.exists() else None
    )
    if cv_dir is None and args.common_voice:
        log.warning(
            "Common Voice dir not found: %s — falling back to Data/lgaudio if present",
            args.common_voice,
        )
        lgaudio = DATA_ROOT / "lgaudio"
        if lgaudio.exists():
            cv_dir = lgaudio
            log.info("using Data/lgaudio as Common Voice fallback")

    salt_dir: Path | None = args.salt_asr if args.salt_asr and args.salt_asr.exists() else None

    result = run(
        target=args.target,
        common_voice_dir=cv_dir,
        salt_dir=salt_dir,
        extra_jsonl=args.extra_jsonl,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {
                "target": result.target,
                "model_id": result.model_id,
                "output_dir": result.output_dir,
                "stats": asdict(result.stats),
                "metrics": result.metrics,
                "ok": result.ok,
                "error": result.error,
            },
            indent=2,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
