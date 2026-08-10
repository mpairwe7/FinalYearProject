#!/usr/bin/env python3
"""Train a quality classifier for the URA data-augmentation pipeline.

Two input modes:

    1. ``--from-jsonl <path>`` — load a messages-format JSONL (e.g. the
       output of ``data_augmentation.py``) and bootstrap positive /
       negative labels from CSV FAQ + teacher QA rows. This is the
       production default.

    2. ``--from-csv-dir <path>`` — run the minimum of ``data_augmentation``
       (CSV loader only) and bootstrap from that. Useful when you don't
       yet have an augmented dataset but want to bootstrap the classifier
       from gold FAQs directly.

Usage::

    # Typical: after data_augmentation.py has run once
    python ml/scripts/train_quality_classifier.py \\
        --from-jsonl artifacts/training_data/train.messages.jsonl \\
        --save-path artifacts/models/quality_classifier.joblib

    # Bootstrap straight from CSVs (no PDFs, no Luganda)
    python ml/scripts/train_quality_classifier.py \\
        --from-csv-dir Data/dataset \\
        --save-path artifacts/models/quality_classifier.joblib

Then use the trained classifier in data_augmentation::

    python ml/scripts/data_augmentation.py \\
        --csv-dir Data/dataset \\
        --pdf-dir Data/pdfs \\
        --quality-classifier artifacts/models/quality_classifier.joblib \\
        --quality-threshold 0.45

Requirements::

    pip install sentence-transformers scikit-learn joblib
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from typing import TYPE_CHECKING

from ml.scripts.data_aug.loaders import load_csv_faqs  # noqa: E402
from ml.scripts.data_aug.quality_classifier import (  # noqa: E402
    DEFAULT_EMBED_MODEL,
    DEFAULT_MODEL_PATH,
    DEFAULT_THRESHOLD,
    build_bootstrap_dataset,
    train_classifier,
)
from ml.scripts.data_aug.schema import (  # noqa: E402
    Message,
    Metadata,
    SourceType,
    TaskType,
    TrainingExample,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger("train_quality_classifier")


def _row_to_example(row: dict) -> TrainingExample | None:
    """Reconstruct a TrainingExample from a messages-JSONL row."""
    msgs_raw = row.get("messages")
    if not isinstance(msgs_raw, list) or len(msgs_raw) < 2:
        return None
    try:
        msgs = [Message(role=m["role"], content=m["content"]) for m in msgs_raw]
    except Exception:
        return None
    try:
        meta = Metadata(
            source=row.get("source", "unknown"),
            source_type=SourceType(row.get("source_type", "csv_faq")),
            task=TaskType(row.get("task", "qa")),
            language=row.get("language", "en"),
            tag=row.get("tag"),
            content_hash=row.get("content_hash", "0" * 16),
            token_count=row.get("token_count"),
            quality_score=row.get("quality_score"),
            chunk_id=row.get("chunk_id"),
            doc_id=row.get("doc_id"),
            extra=row.get("extra") or {},
        )
        return TrainingExample(messages=msgs, metadata=meta)
    except Exception as exc:
        log.debug("skipping row: %s", exc)
        return None


def _load_from_jsonl(path: Path) -> Iterator[TrainingExample]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ex = _row_to_example(row)
            if ex is not None:
                yield ex


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-jsonl", type=Path, help="Messages-JSONL from data_augmentation.py")
    source.add_argument("--from-csv-dir", type=Path, help="Raw CSV FAQ directory (bootstrap)")

    p.add_argument(
        "--save-path",
        type=Path,
        default=DEFAULT_MODEL_PATH,
        help=f"Where to save the fitted classifier (default: {DEFAULT_MODEL_PATH})",
    )
    p.add_argument(
        "--embed-model",
        type=str,
        default=DEFAULT_EMBED_MODEL,
        help=f"Sentence-transformers model (default: {DEFAULT_EMBED_MODEL})",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Classifier keep threshold (default: {DEFAULT_THRESHOLD})",
    )
    p.add_argument(
        "--max-per-class",
        type=int,
        default=5000,
        help="Cap positives / negatives at this number (default: 5000)",
    )
    p.add_argument("-v", "--verbose", action="count", default=1)
    args = p.parse_args()

    level = logging.INFO if args.verbose >= 1 else logging.WARNING
    if args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Load examples from the selected source
    if args.from_jsonl:
        if not args.from_jsonl.exists():
            log.error("input not found: %s", args.from_jsonl)
            return 2
        log.info("loading from %s", args.from_jsonl)
        examples = list(_load_from_jsonl(args.from_jsonl))
    else:
        if not args.from_csv_dir.exists():
            log.error("csv dir not found: %s", args.from_csv_dir)
            return 2
        log.info("loading CSV FAQs from %s", args.from_csv_dir)
        examples = list(load_csv_faqs(args.from_csv_dir))

    log.info("loaded %d candidate examples", len(examples))
    if not examples:
        log.error("no examples found — aborting")
        return 2

    positives, negatives = build_bootstrap_dataset(examples, max_per_class=args.max_per_class)
    log.info("bootstrap: %d positives, %d negatives", len(positives), len(negatives))
    if len(positives) < 20 or len(negatives) < 20:
        log.error(
            "too few labelled examples (%d pos, %d neg) — need at least 20 each",
            len(positives),
            len(negatives),
        )
        return 3

    clf, metrics = train_classifier(
        positives,
        negatives,
        embed_model=args.embed_model,
        threshold=args.threshold,
        save_path=args.save_path,
    )

    print("\n=== TRAINING METRICS ===")
    print(json.dumps(metrics, indent=2))
    print(f"\n\u2714 classifier saved to: {args.save_path}")
    print(f"\u2714 metrics saved to:    {args.save_path.with_suffix('.metrics.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
