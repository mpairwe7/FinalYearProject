"""Four-stage pipeline orchestrator.

Stages (each pure and testable):

    1. INGEST    — collect raw examples from every source into one list
    2. NORMALIZE — (handled inside loaders via text_utils + redact_pii)
    3. QUALITY   — length/langid/heuristics/dedup/contamination
    4. FORMAT    — stratified split + write JSONL / Parquet + manifest

Public entry point: :func:`run_pipeline`.
"""

from __future__ import annotations

import json
import logging
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from ml.scripts.data_aug.dedup import dedup_all
from ml.scripts.data_aug.formatters import (
    write_legacy_instruction_jsonl,
    write_messages_jsonl,
    write_parquet,
)
from ml.scripts.data_aug.loaders import (
    load_csv_faqs,
    load_luganda_data,
    load_pdf_chunks,
    load_refusal_examples,
    load_teacher_qa,
)
from ml.scripts.data_aug.provenance import ManifestBuilder, write_data_card
from ml.scripts.data_aug.quality import QualityConfig, filter_quality
from ml.scripts.data_aug.schema import TrainingExample
from ml.scripts.data_aug.splitters import SplitConfig, stratified_split

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class PipelineConfig:
    # Inputs
    csv_dir: Optional[Path] = None
    pdf_dir: Optional[Path] = None
    luganda_dir: Optional[Path] = None
    teacher_qa_dirs: list[Path] = field(default_factory=list)
    include_refusals: bool = True

    # Outputs
    output_dir: Path = Path("artifacts/training_data")
    emit_legacy_jsonl: bool = True
    emit_parquet: bool = True

    # PDF
    pdf_workers: int = 1
    max_pdfs: Optional[int] = None
    emit_pdf_retrieval: bool = True
    emit_pdf_corpus: bool = True

    # Quality
    quality: QualityConfig = field(default_factory=QualityConfig)

    # Dedup
    near_dup_threshold: float = 0.85

    # Split
    split: SplitConfig = field(default_factory=SplitConfig)

    # Reproducibility
    seed: int = 42

    # Ops
    dry_run: bool = False
    imbalance_warn_ratio: float = 0.70

    def as_dict(self) -> dict:
        """Recursively convert to a JSON-serialisable dict.

        Path objects → strings, dataclasses → dicts, everything else
        passed through. Used by the manifest writer.
        """

        def _encode(v):
            if isinstance(v, Path):
                return str(v)
            if isinstance(v, dict):
                return {k: _encode(val) for k, val in v.items()}
            if isinstance(v, (list, tuple)):
                return [_encode(x) for x in v]
            return v

        d = asdict(self)
        return _encode(d)


# ---------------------------------------------------------------------------
# Stage 1 — ingest
# ---------------------------------------------------------------------------


def _stage_ingest(cfg: PipelineConfig) -> Iterator[TrainingExample]:
    """Yield raw examples from every enabled source. Lazy — does not
    materialise the whole set in memory until dedup pulls it."""

    # Order matters — dedup keeps the first instance of a duplicate.
    # Put highest-quality sources first so they win.

    # 1. CSV FAQs (gold)
    if cfg.csv_dir and cfg.csv_dir.exists():
        log.info("ingest: CSV FAQs from %s", cfg.csv_dir)
        yield from load_csv_faqs(cfg.csv_dir)
    else:
        log.info("ingest: CSV FAQs skipped (no csv_dir)")

    # 2. Teacher QA (also high quality if generated well)
    teacher_dirs = list(cfg.teacher_qa_dirs or [])
    if teacher_dirs:
        log.info("ingest: teacher QA from %s", [str(d) for d in teacher_dirs])
        yield from load_teacher_qa(teacher_dirs)
    else:
        log.info("ingest: teacher QA skipped (no teacher_qa_dirs)")

    # 3. Refusals (curated safety set)
    if cfg.include_refusals:
        log.info("ingest: refusal / safety examples")
        yield from load_refusal_examples()

    # 4. Luganda (translation / parallel corpus)
    if cfg.luganda_dir and cfg.luganda_dir.exists():
        log.info("ingest: Luganda from %s", cfg.luganda_dir)
        yield from load_luganda_data(cfg.luganda_dir)
    else:
        log.info("ingest: Luganda skipped")

    # 5. PDF corpus + retrieval (weakest; goes last so dedup discards rather
    #    than evicts FAQs when there's overlap)
    if cfg.pdf_dir and cfg.pdf_dir.exists() and (cfg.emit_pdf_corpus or cfg.emit_pdf_retrieval):
        log.info(
            "ingest: PDFs from %s (corpus=%s, retrieval=%s)",
            cfg.pdf_dir,
            cfg.emit_pdf_corpus,
            cfg.emit_pdf_retrieval,
        )
        yield from load_pdf_chunks(
            cfg.pdf_dir,
            max_pdfs=cfg.max_pdfs,
            workers=cfg.pdf_workers,
            as_corpus=cfg.emit_pdf_corpus,
            emit_retrieval=cfg.emit_pdf_retrieval,
        )
    else:
        log.info("ingest: PDFs skipped")


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------


def run_pipeline(cfg: PipelineConfig) -> dict:
    """Run all four stages. Returns the manifest dict."""

    # Determinism hook — anything that uses `random` downstream (e.g. load
    # ordering, split shuffles) will pick up this seed.
    random.seed(cfg.seed)

    repo_root = Path(__file__).resolve().parents[3]
    manifest = ManifestBuilder(config=cfg.as_dict(), repo_root=repo_root)

    # Track inputs for the manifest (hash every file we'll read).
    if cfg.csv_dir and cfg.csv_dir.exists():
        manifest.record_input_directory(cfg.csv_dir, "csv_faq", ["*.csv"])
    if cfg.pdf_dir and cfg.pdf_dir.exists():
        manifest.record_input_directory(cfg.pdf_dir, "pdf", ["*.pdf"])
    if cfg.luganda_dir and cfg.luganda_dir.exists():
        manifest.record_input_directory(
            cfg.luganda_dir, "luganda", ["*.jsonl", "*.csv", "*.txt"]
        )
    for d in cfg.teacher_qa_dirs or []:
        if d.exists():
            manifest.record_input_directory(d, "teacher_qa", ["*.jsonl"])

    # --- Stage 1: ingest + dedup ------------------------------------------------
    log.info("=" * 70)
    log.info("STAGE 1: INGEST + DEDUPLICATE")
    log.info("=" * 70)
    raw_iter = _stage_ingest(cfg)
    deduped, dedup_stats = dedup_all(raw_iter, near_threshold=cfg.near_dup_threshold)
    log.info("ingest/dedup: %d examples after dedup", len(deduped))
    manifest.record_stage("ingest_dedup", dedup_stats.as_dict())

    if not deduped:
        log.error("No training examples produced!")
        sys.exit(1)

    # --- Stage 2: quality ---------------------------------------------------
    log.info("=" * 70)
    log.info("STAGE 2: QUALITY FILTER")
    log.info("=" * 70)
    filtered, q_stats = filter_quality(deduped, cfg.quality)
    log.info("quality: %d → %d examples", q_stats.input_count, q_stats.output_count)
    manifest.record_stage("quality", q_stats.as_dict())

    if not filtered:
        log.error("No examples left after quality filtering!")
        sys.exit(1)

    # Imbalance warning — if any one source dominates, warn and suggest caps.
    if filtered and cfg.imbalance_warn_ratio > 0:
        from collections import Counter

        by_src = Counter(ex.metadata.source_type.value for ex in filtered)
        total = sum(by_src.values())
        for src, n in by_src.most_common():
            ratio = n / total
            if ratio >= cfg.imbalance_warn_ratio:
                log.warning(
                    "imbalance: source '%s' = %d rows (%.1f%% of total). "
                    "Consider --source-cap %s=<N> to balance.",
                    src, n, ratio * 100, src,
                )
            break  # only flag the dominant source

    # --- Stage 3: stratified split ------------------------------------------
    log.info("=" * 70)
    log.info("STAGE 3: STRATIFIED SPLIT + CONTAMINATION SCAN")
    log.info("=" * 70)
    train, val, test, split_stats = stratified_split(filtered, cfg.split)
    log.info("split: train=%d val=%d test=%d", len(train), len(val), len(test))
    manifest.record_stage("split", split_stats.as_dict())

    if cfg.dry_run:
        log.warning("DRY RUN — skipping file writes. Stages recorded:")
        for name, payload in manifest.stages.items():
            log.warning("  %s: %s", name, payload)
        return manifest.build()

    # --- Stage 4: format + write --------------------------------------------
    log.info("=" * 70)
    log.info("STAGE 4: FORMAT + WRITE")
    log.info("=" * 70)

    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)

    splits = {"train": train, "val": val, "test": test}
    for name, rows in splits.items():
        jsonl_path = out / f"{name}.messages.jsonl"
        n = write_messages_jsonl(rows, jsonl_path)
        manifest.record_output(jsonl_path, f"{name}_messages", rows=n)

        if cfg.emit_legacy_jsonl:
            legacy_path = out / f"{name}.instruction.jsonl"
            n_l = write_legacy_instruction_jsonl(rows, legacy_path)
            manifest.record_output(legacy_path, f"{name}_legacy", rows=n_l)

        if cfg.emit_parquet:
            parquet_path = out / f"{name}.parquet"
            n_p = write_parquet(rows, parquet_path)
            if n_p is not None:
                manifest.record_output(parquet_path, f"{name}_parquet", rows=n_p)

    # Back-compat: also emit the legacy flat paths that fine_tune_gemma.py
    # still searches for.
    if cfg.emit_legacy_jsonl:
        flat_instruction = out / "training_data.jsonl"
        n_f = write_legacy_instruction_jsonl(train + val, flat_instruction)
        manifest.record_output(flat_instruction, "legacy_flat_training", rows=n_f)

    flat_messages = out / "training_data.messages.jsonl"
    n_f2 = write_messages_jsonl(train + val, flat_messages)
    manifest.record_output(flat_messages, "messages_flat_training", rows=n_f2)

    # --- Manifest + data card -----------------------------------------------
    manifest_path = out / "manifest.json"
    manifest_dict = manifest.build()
    manifest_path.write_text(
        json.dumps(manifest_dict, indent=2) + "\n", encoding="utf-8"
    )
    log.info("manifest: %s", manifest_path)
    write_data_card(manifest_dict, out / "DATA_CARD.md")

    log.info("=" * 70)
    log.info("DONE — %d train / %d val / %d test", len(train), len(val), len(test))
    log.info("=" * 70)
    return manifest_dict
