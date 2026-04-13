#!/usr/bin/env python3


"""URA Chatbot — production data augmentation pipeline (2026).

Thin CLI wrapper around :mod:`ml.scripts.data_aug.pipeline`. All logic
lives in the modular package; this file only parses arguments, wires up
logging, and invokes :func:`run_pipeline`.

Four-stage pipeline:
    1. ingest    — load every enabled source as validated TrainingExamples
    2. normalize — Unicode NFKC + ftfy + PII redaction (inside loaders)
    3. quality   — token-aware length, heuristics, per-source caps, dedup
    4. format    — stratified split + messages JSONL / Parquet / manifest

Output layout (``--output-dir``)::

    train.messages.jsonl        canonical OpenAI-format (TRL-ready)
    val.messages.jsonl
    test.messages.jsonl
    train.parquet  val.parquet  test.parquet
    train.instruction.jsonl     legacy Alpaca format (back-compat)
    training_data.jsonl         flat legacy (fine_tune_gemma.py back-compat)
    training_data.messages.jsonl   flat canonical
    manifest.json               provenance + stats + git SHA
    DATA_CARD.md                human-readable data card

Usage examples::

    # Minimal
    python ml/scripts/data_augmentation.py \\
        --csv-dir Data/dataset \\
        --output-dir artifacts/training_data

    # Full: CSV + PDFs + Luganda + teacher QA
    python ml/scripts/data_augmentation.py \\
        --csv-dir Data/dataset \\
        --pdf-dir Data/pdfs \\
        --luganda-dir Data/TTT \\
        --teacher-qa-dir Data/teacher_qa \\
        --output-dir artifacts/training_data \\
        --pdf-workers 4 \\
        --max-pdfs 50 \\
        --tokenizer-model google/gemma-2-2b-it

Requirements::

    pip install pandas pymupdf4llm pydantic datasets pyarrow datasketch ftfy
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure the project root is on sys.path so ``ml.scripts.data_aug`` imports
# work regardless of where the script is invoked from.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ml.scripts.data_aug.pipeline import (  # noqa: E402
    PipelineConfig,
    QualityConfig,
    SplitConfig,
    run_pipeline,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _optional_path(v: str) -> Path | None:
    return Path(v) if v else None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Inputs
    p.add_argument(
        "--csv-dir", type=_optional_path, default=None, help="Directory containing FAQ CSVs"
    )
    p.add_argument("--pdf-dir", type=_optional_path, default=None, help="Directory containing PDFs")
    p.add_argument(
        "--luganda-dir", type=_optional_path, default=None, help="Directory with Luganda data"
    )
    p.add_argument(
        "--teacher-qa-dir",
        action="append",
        default=[],
        type=Path,
        help="Directory with teacher QA JSONL (may be passed multiple times)",
    )
    p.add_argument(
        "--no-refusals",
        action="store_true",
        help="Disable curated refusal / safety examples (not recommended)",
    )

    # Outputs
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/training_data"),
        help="Output directory (default: artifacts/training_data)",
    )
    p.add_argument(
        "--no-legacy-jsonl",
        action="store_true",
        help="Skip the Alpaca-style legacy JSONL outputs",
    )
    p.add_argument(
        "--no-parquet",
        action="store_true",
        help="Skip the Parquet outputs",
    )

    # PDF
    p.add_argument("--pdf-workers", type=int, default=1, help="Parallel PDF workers (processes)")
    p.add_argument("--max-pdfs", type=int, default=None, help="Process at most N PDFs")
    p.add_argument(
        "--no-pdf-corpus",
        action="store_true",
        help="Skip PDF corpus (passage) examples",
    )
    p.add_argument(
        "--no-pdf-retrieval",
        action="store_true",
        help="Skip PDF retrieval-format examples",
    )

    # Quality — heuristic
    p.add_argument(
        "--tokenizer-model",
        type=str,
        default="google/gemma-2-2b-it",
        help="Tokenizer used for token-aware length filtering (default: google/gemma-2-2b-it)",
    )
    p.add_argument("--min-tokens", type=int, default=8)
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--max-symbol-ratio", type=float, default=0.35)
    p.add_argument("--max-repetition-ratio", type=float, default=0.25)
    p.add_argument(
        "--source-cap",
        action="append",
        default=[],
        help="Per-source max rows, repeatable. Format: <source_type>=<cap> "
        "(e.g. pdf_corpus=15000, luganda_parallel=10000)",
    )

    # Quality — learned classifier (FineWeb-Edu style)
    p.add_argument(
        "--quality-classifier",
        type=Path,
        default=None,
        help="Path to a joblib quality classifier produced by "
        "ml/scripts/train_quality_classifier.py. When set, examples below "
        "--quality-threshold are dropped.",
    )
    p.add_argument(
        "--quality-threshold",
        type=float,
        default=None,
        help="Override the classifier's keep threshold (default: model's own)",
    )

    # Dedup
    p.add_argument("--near-dup-threshold", type=float, default=0.85)

    # Split
    p.add_argument("--val-ratio", type=float, default=0.08)
    p.add_argument("--test-ratio", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=42)

    # Ops
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run ingest/dedup/quality/split stages but don't write output files. "
        "Exits with the manifest printed — useful for CI validation.",
    )
    p.add_argument(
        "--imbalance-warn-ratio",
        type=float,
        default=0.70,
        help="Warn when any single source exceeds this fraction of the total "
        "(default: 0.70). Set to 0 to disable.",
    )

    p.add_argument("-v", "--verbose", action="count", default=1)
    p.add_argument("-q", "--quiet", action="store_true")

    return p


def _parse_source_caps(items: list[str]) -> dict[str, int]:
    caps: dict[str, int] = {}
    for item in items:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        try:
            caps[k.strip()] = int(v.strip())
        except ValueError:
            continue
    return caps


def _config_from_args(args: argparse.Namespace) -> PipelineConfig:
    quality = QualityConfig(
        min_tokens=args.min_tokens,
        max_tokens=args.max_tokens,
        max_symbol_ratio=args.max_symbol_ratio,
        max_repetition_ratio=args.max_repetition_ratio,
        tokenizer_model=args.tokenizer_model or None,
        source_caps=_parse_source_caps(args.source_cap),
        quality_classifier_path=args.quality_classifier,
        quality_threshold=args.quality_threshold,
    )
    split = SplitConfig(
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        seed=args.seed,
    )
    return PipelineConfig(
        csv_dir=args.csv_dir,
        pdf_dir=args.pdf_dir,
        luganda_dir=args.luganda_dir,
        teacher_qa_dirs=list(args.teacher_qa_dir or []),
        include_refusals=not args.no_refusals,
        output_dir=args.output_dir,
        emit_legacy_jsonl=not args.no_legacy_jsonl,
        emit_parquet=not args.no_parquet,
        pdf_workers=args.pdf_workers,
        max_pdfs=args.max_pdfs,
        emit_pdf_corpus=not args.no_pdf_corpus,
        emit_pdf_retrieval=not args.no_pdf_retrieval,
        quality=quality,
        near_dup_threshold=args.near_dup_threshold,
        split=split,
        seed=args.seed,
        dry_run=args.dry_run,
        imbalance_warn_ratio=args.imbalance_warn_ratio,
    )


def main() -> int:
    args = build_parser().parse_args()
    _setup_logging(0 if args.quiet else args.verbose)

    cfg = _config_from_args(args)
    manifest = run_pipeline(cfg)

    split_stats = manifest["stages"].get("split", {})
    print(
        "\n\u2714  train={train} val={val} test={test}  →  {out}".format(
            train=split_stats.get("train", "?"),
            val=split_stats.get("val", "?"),
            test=split_stats.get("test", "?"),
            out=cfg.output_dir,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
