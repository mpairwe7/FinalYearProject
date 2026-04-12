#!/usr/bin/env python3
"""LoRA fine-tune MADLAD-400 on Luganda <-> English parallel data.

Mirrors the Gemma fine-tune script but targets MADLAD-400 (Apache-2.0).
Consumes MTExample rows produced by :mod:`ml.scripts.data_aug.mt_loaders`.

Pipeline::

    load_parallel_directory(Data/TTT)
        -> deduplicate by content_hash
        -> stratified_split by doc_id (88/8/4)
        -> HF datasets.Dataset with (source, target) columns
        -> AutoTokenizer + LoRA peft
        -> Seq2SeqTrainer
        -> artifacts/mt/madlad_ura_lgen/{adapter, manifest}

Usage::

    # Dry run — no download, no training
    python -m ml.scripts.mt.finetune_mt --dry-run

    # Real run
    python -m ml.scripts.mt.finetune_mt --data-dir Data/TTT
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("mt.finetune")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PROJECT_ROOT / "Data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
MT_CACHE_DIR = ARTIFACTS_DIR / "mt" / "models"
OUTPUT_DIR_DEFAULT = ARTIFACTS_DIR / "mt" / "madlad_ura_lgen"


MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    "mobile": {
        "model_id": "google/madlad400-3b-mt",
        "lora_r": 8,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "target_modules": ["q", "k", "v", "o", "wi", "wo"],  # T5-style
        "epochs": 3,
        "batch_size": 4,
        "gradient_accumulation": 4,
        "learning_rate": 2e-4,
        "warmup_steps": 200,
        "max_source_length": 256,
        "max_target_length": 256,
    },
    "full": {
        "model_id": "google/madlad400-3b-mt",
        "lora_r": 32,
        "lora_alpha": 64,
        "lora_dropout": 0.05,
        "target_modules": ["q", "k", "v", "o", "wi", "wo"],
        "epochs": 5,
        "batch_size": 8,
        "gradient_accumulation": 2,
        "learning_rate": 1e-4,
        "warmup_steps": 500,
        "max_source_length": 512,
        "max_target_length": 512,
    },
}


@dataclass
class RunStats:
    n_train: int = 0
    n_val: int = 0
    n_test: int = 0
    n_duplicates_dropped: int = 0
    languages: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    target: str
    model_id: str
    output_dir: str
    stats: RunStats
    metrics: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    error: Optional[str] = None


def check_dependencies() -> list[str]:
    missing: list[str] = []
    for pkg in ("torch", "transformers", "datasets", "peft", "sacrebleu"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def _resolve_model_path(cfg: dict[str, Any]) -> str:
    """Use the local cache if present, else the HF repo_id."""
    cache = MT_CACHE_DIR / cfg["model_id"].replace("/", "__")
    return str(cache) if cache.exists() else cfg["model_id"]


def _dedupe_by_hash(rows):
    seen = set()
    deduped = []
    dropped = 0
    for row in rows:
        if row.content_hash in seen:
            dropped += 1
            continue
        seen.add(row.content_hash)
        deduped.append(row)
    return deduped, dropped


def _build_dataset(rows, tokenizer, cfg):
    from datasets import Dataset  # type: ignore

    def _iter_pairs():
        for ex in rows:
            # MADLAD uses target-language tags like "<2lg>"; we prefix both
            # directions so the model learns En->Lg and Lg->En from the same pair.
            yield {
                "source_text": f"<2{ex.target_lang}> {ex.source_text}",
                "target_text": ex.target_text,
            }
            yield {
                "source_text": f"<2{ex.source_lang}> {ex.target_text}",
                "target_text": ex.source_text,
            }

    ds = Dataset.from_list(list(_iter_pairs()))

    def _prepare(example):
        model_inputs = tokenizer(
            example["source_text"],
            max_length=cfg["max_source_length"],
            truncation=True,
        )
        labels = tokenizer(
            text_target=example["target_text"],
            max_length=cfg["max_target_length"],
            truncation=True,
        )
        model_inputs["labels"] = labels["input_ids"]
        return model_inputs

    return ds.map(_prepare, remove_columns=ds.column_names)


def run(
    *,
    target: str,
    data_dir: Path,
    output_dir: Path,
    dry_run: bool,
) -> RunResult:
    cfg = MODEL_CONFIGS[target]
    stats = RunStats()
    result = RunResult(
        target=target,
        model_id=cfg["model_id"],
        output_dir=str(output_dir),
        stats=stats,
    )

    missing = check_dependencies()
    if missing and not dry_run:
        result.error = f"missing deps: {missing}"
        log.error(result.error)
        return result

    from ml.scripts.data_aug.mt_loaders import (  # type: ignore
        load_parallel_directory,
        stratified_split,
    )

    rows = list(load_parallel_directory(data_dir))
    rows, dropped = _dedupe_by_hash(rows)
    stats.n_duplicates_dropped = dropped
    stats.languages = sorted({ex.source_lang for ex in rows} | {ex.target_lang for ex in rows})
    log.info(
        "loaded %d unique pairs (dropped %d duplicates), langs=%s",
        len(rows), dropped, stats.languages,
    )

    if not rows:
        result.error = f"no parallel data found under {data_dir}"
        log.error(result.error)
        return result

    train_rows, val_rows, test_rows = stratified_split(rows)
    stats.n_train = len(train_rows)
    stats.n_val = len(val_rows)
    stats.n_test = len(test_rows)

    if dry_run:
        log.info("[dry-run] train=%d val=%d test=%d", stats.n_train, stats.n_val, stats.n_test)
        log.info("[dry-run] target=%s config=%s", target, {k: v for k, v in cfg.items() if k != "target_modules"})
        result.ok = True
        return result

    try:
        from transformers import (  # type: ignore
            AutoModelForSeq2SeqLM,
            AutoTokenizer,
            DataCollatorForSeq2Seq,
            Seq2SeqTrainer,
            Seq2SeqTrainingArguments,
        )
        from peft import LoraConfig, get_peft_model  # type: ignore
    except ImportError as exc:
        result.error = f"import failed: {exc}"
        return result

    model_src = _resolve_model_path(cfg)
    tokenizer = AutoTokenizer.from_pretrained(model_src)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_src)

    lora_cfg = LoraConfig(
        r=cfg["lora_r"],
        lora_alpha=cfg["lora_alpha"],
        lora_dropout=cfg["lora_dropout"],
        target_modules=cfg["target_modules"],
        bias="none",
        task_type="SEQ_2_SEQ_LM",
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    train_ds = _build_dataset(train_rows, tokenizer, cfg)
    val_ds = _build_dataset(val_rows, tokenizer, cfg) if val_rows else None

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
        generation_max_length=cfg["max_target_length"],
        save_total_limit=2,
        report_to=[],
    )

    trainer = Seq2SeqTrainer(
        args=args_tr,
        model=model,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=DataCollatorForSeq2Seq(tokenizer=tokenizer, model=model),
        tokenizer=tokenizer,
    )

    try:
        trainer.train()
    except Exception as exc:
        log.exception("training failed")
        result.error = str(exc)
        return result

    model.save_pretrained(str(output_dir / "final"))
    tokenizer.save_pretrained(str(output_dir / "final"))

    if val_ds is not None:
        eval_metrics = trainer.evaluate()
        result.metrics = {k: round(float(v), 4) for k, v in eval_metrics.items() if isinstance(v, (int, float))}

    manifest = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "target": target,
        "config": cfg,
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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MADLAD LoRA fine-tune for Luganda <-> English")
    parser.add_argument("--target", choices=sorted(MODEL_CONFIGS.keys()), default="mobile")
    parser.add_argument("--data-dir", type=Path, default=DATA_ROOT / "TTT")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    result = run(
        target=args.target,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )
    print(json.dumps(
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
    ))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
