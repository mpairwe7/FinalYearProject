#!/usr/bin/env python3
"""Teacher -> student distillation for on-device MT.

MADLAD-400-3B is too large for mobile. This script distills it into a
smaller student by generating pseudo-labels on a distillation set and
fine-tuning a compact seq2seq model on them.

Strategy (simple, effective, 2026-standard):

    1. Load the fine-tuned MADLAD-400-3B teacher.
    2. Run it on a mix of: (a) the real Luganda parallel corpus, (b)
       backtranslated pairs, (c) monolingual Luganda text.
    3. Record (source, pseudo_target) pairs.
    4. Fine-tune a smaller student (default: MADLAD-400-3B with a lower
       LoRA rank, or a T5-small-class model) on the pseudo-labels.

The distilled student is the artifact that gets ONNX-quantized + shipped
to mobile via ``export_mt_onnx.py``.

Usage::

    python -m ml.scripts.mt.distill_mt --dry-run
    python -m ml.scripts.mt.distill_mt \\
        --teacher artifacts/mt/madlad_ura_lgen/final \\
        --student-model google-t5/t5-small \\
        --out artifacts/mt/student
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

log = logging.getLogger("mt.distill")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PROJECT_ROOT / "Data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts" / "mt"


@dataclass
class DistillStats:
    teacher_rows: int = 0
    student_rows: int = 0
    directions: list[str] = field(default_factory=list)


@dataclass
class DistillResult:
    teacher: str
    student: str
    output_dir: str
    stats: DistillStats
    metrics: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    error: Optional[str] = None


def _load_pairs(data_dir: Path, max_rows: Optional[int] = None):
    """Load real + backtranslated pairs for distillation."""
    from ml.scripts.data_aug.mt_loaders import load_parallel_directory  # type: ignore

    rows = list(load_parallel_directory(data_dir))
    bt = ARTIFACTS_DIR / "backtranslated.jsonl"
    if bt.exists():
        from ml.scripts.data_aug.mt_loaders import load_jsonl  # type: ignore
        rows += list(load_jsonl(bt))
    if max_rows is not None:
        rows = rows[:max_rows]
    return rows


def _generate_pseudolabels(teacher_dir: Path, rows, *, dry_run: bool) -> list[dict]:
    """Run the teacher on each source and record the generation."""
    if dry_run:
        return [
            {
                "source_text": r.source_text,
                "source_lang": r.source_lang,
                "target_lang": r.target_lang,
                "pseudo_target": f"[dry-run] {r.target_text}",
                "real_target": r.target_text,
            }
            for r in rows
        ]

    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"transformers import failed: {exc}") from exc

    tok = AutoTokenizer.from_pretrained(str(teacher_dir))
    model = AutoModelForSeq2SeqLM.from_pretrained(str(teacher_dir))
    pairs = []
    for r in rows:
        src = f"<2{r.target_lang}> {r.source_text}"
        inputs = tok(src, return_tensors="pt", truncation=True, max_length=512)
        outputs = model.generate(**inputs, max_new_tokens=512, num_beams=4)
        pseudo = tok.decode(outputs[0], skip_special_tokens=True)
        pairs.append({
            "source_text": r.source_text,
            "source_lang": r.source_lang,
            "target_lang": r.target_lang,
            "pseudo_target": pseudo,
            "real_target": r.target_text,
        })
    return pairs


def _train_student(
    student_model: str,
    pseudolabels: list[dict],
    out_dir: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Fine-tune the student on pseudo-labels."""
    if dry_run:
        return {"dry_run": True}

    try:
        from datasets import Dataset  # type: ignore
        from transformers import (  # type: ignore
            AutoModelForSeq2SeqLM,
            AutoTokenizer,
            DataCollatorForSeq2Seq,
            Seq2SeqTrainer,
            Seq2SeqTrainingArguments,
        )
    except ImportError as exc:
        raise RuntimeError(f"transformers import failed: {exc}") from exc

    tok = AutoTokenizer.from_pretrained(student_model)
    model = AutoModelForSeq2SeqLM.from_pretrained(student_model)

    def _prep(example):
        inputs = tok(
            f"<2{example['target_lang']}> {example['source_text']}",
            truncation=True,
            max_length=256,
        )
        labels = tok(text_target=example["pseudo_target"], truncation=True, max_length=256)
        inputs["labels"] = labels["input_ids"]
        return inputs

    ds = Dataset.from_list(pseudolabels).map(_prep, remove_columns=["source_text", "source_lang", "target_lang", "pseudo_target", "real_target"])

    out_dir.mkdir(parents=True, exist_ok=True)
    args = Seq2SeqTrainingArguments(
        output_dir=str(out_dir),
        per_device_train_batch_size=8,
        num_train_epochs=3,
        learning_rate=3e-4,
        fp16=True,
        logging_steps=25,
        save_strategy="epoch",
        save_total_limit=1,
        predict_with_generate=True,
        generation_max_length=256,
        report_to=[],
    )
    trainer = Seq2SeqTrainer(
        args=args,
        model=model,
        train_dataset=ds,
        data_collator=DataCollatorForSeq2Seq(tokenizer=tok, model=model),
        tokenizer=tok,
    )
    trainer.train()
    model.save_pretrained(str(out_dir / "final"))
    tok.save_pretrained(str(out_dir / "final"))
    return {"training_loss": trainer.state.log_history[-1].get("loss", 0.0) if trainer.state.log_history else 0.0}


def run(
    *,
    teacher_dir: Path,
    student_model: str,
    data_dir: Path,
    output_dir: Path,
    max_rows: Optional[int],
    dry_run: bool,
) -> DistillResult:
    result = DistillResult(
        teacher=str(teacher_dir),
        student=student_model,
        output_dir=str(output_dir),
        stats=DistillStats(),
    )

    if not dry_run and not teacher_dir.exists():
        result.error = f"teacher directory missing: {teacher_dir}"
        log.error(result.error)
        return result

    rows = _load_pairs(data_dir, max_rows=max_rows)
    result.stats.teacher_rows = len(rows)
    result.stats.directions = sorted({f"{r.source_lang}2{r.target_lang}" for r in rows})

    if dry_run:
        log.info(
            "[dry-run] would distill %d pairs from %s -> %s (student=%s)",
            len(rows), teacher_dir, output_dir, student_model,
        )
        result.ok = True
        return result

    try:
        pseudolabels = _generate_pseudolabels(teacher_dir, rows, dry_run=False)
        result.stats.student_rows = len(pseudolabels)
        metrics = _train_student(student_model, pseudolabels, output_dir, dry_run=False)
        result.metrics = metrics
    except Exception as exc:
        log.exception("distillation failed")
        result.error = str(exc)
        return result

    manifest = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "teacher": str(teacher_dir.resolve()),
        "student_model": student_model,
        "student_output": str((output_dir / "final").resolve()),
        "stats": asdict(result.stats),
        "metrics": result.metrics,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "distill_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    result.ok = True
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Teacher -> student MT distillation")
    parser.add_argument(
        "--teacher",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "mt" / "madlad_ura_lgen" / "final",
    )
    parser.add_argument(
        "--student-model",
        default="google-t5/t5-small",
        help="Student model id (default: google-t5/t5-small).",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_ROOT / "TTT")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ARTIFACTS_DIR / "student",
    )
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    result = run(
        teacher_dir=args.teacher,
        student_model=args.student_model,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        max_rows=args.max_rows,
        dry_run=args.dry_run,
    )
    print(json.dumps(
        {
            "teacher": result.teacher,
            "student": result.student,
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
