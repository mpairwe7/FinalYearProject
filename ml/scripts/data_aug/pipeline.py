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
from ml.scripts.data_aug.provenance import (
    ManifestBuilder,
    summarise_synthetic_ratio,
    write_croissant_metadata,
    write_data_card,
)
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

    # Web crawl (Phase 1 — opt-in).
    crawl_dir: Optional[Path] = None
    enable_web_crawl: bool = False

    # Online corpora (Phase 2 — JW300, OPUS, etc.)
    online_corpus_dirs: list[Path] = field(default_factory=list)

    # Translated FAQ directories (Phase 3 — sw/nyn/ach).
    translated_faq_dirs: list[Path] = field(default_factory=list)

    # Speech-pipeline sources (2026 — optional). When set, the ingest stage
    # additionally emits side-manifests listing ASR/MT examples so the
    # speech pipeline can pick them up without re-scanning every file.
    # These sources DO NOT flow into the chat training_data output — they
    # are recorded in a separate ``speech_manifest.json`` so that the
    # existing LLM training pipeline remains byte-identical.
    asr_common_voice_dir: Optional[Path] = None
    asr_salt_dir: Optional[Path] = None
    mt_parallel_dir: Optional[Path] = None

    # Outputs
    output_dir: Path = Path("artifacts/training_data")
    emit_legacy_jsonl: bool = True
    emit_parquet: bool = True
    # When true, write a supplementary speech manifest alongside the
    # chat-training outputs so the speech pipeline can discover counts
    # without re-scanning Data/.
    emit_speech_manifest: bool = True

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

    # Phase 2 — canonical retrieval embedder. Written to manifest so
    # downstream serving code can verify it has the same encoder.
    canonical_embedder: str = "sentence-transformers/all-MiniLM-L6-v2"

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

    # 5. Web crawl (opt-in, above PDFs since it's fresher content)
    if cfg.crawl_dir and cfg.crawl_dir.exists():
        from ml.scripts.data_aug.crawler import load_crawled_pages

        log.info("ingest: web crawl from %s", cfg.crawl_dir)
        yield from load_crawled_pages(cfg.crawl_dir)
    else:
        log.info("ingest: web crawl skipped")

    # 6. Online corpora (JW300, OPUS — multilingual parallel data)
    for corpus_dir in cfg.online_corpus_dirs:
        if corpus_dir.exists():
            from ml.scripts.data_aug.dataset_downloader import load_online_corpus

            log.info("ingest: online corpus from %s", corpus_dir)
            yield from load_online_corpus(corpus_dir)

    # 7. Translated FAQs (sw/nyn/ach)
    for tfd in cfg.translated_faq_dirs:
        if tfd.exists():
            log.info("ingest: translated FAQs from %s", tfd)
            yield from load_csv_faqs(tfd)

    # 8. PDF corpus + retrieval (weakest; goes last so dedup discards rather
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

    # Phase 2 — synthetic composition audit. Recorded in the manifest for
    # downstream consumers (data card, model card, EU AI Act Art. 10 record).
    synthetic_summary = summarise_synthetic_ratio(train + val + test)
    log.info(
        "synthetic composition: %d/%d rows synthetic (%.1f%%)",
        synthetic_summary["synthetic_rows"],
        synthetic_summary["total_rows"],
        synthetic_summary["synthetic_ratio"] * 100,
    )
    manifest.record_stage("synthetic", synthetic_summary)

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

    # --- Manifest + data card + Croissant -----------------------------------
    manifest_path = out / "manifest.json"
    manifest_dict = manifest.build()
    manifest_path.write_text(
        json.dumps(manifest_dict, indent=2) + "\n", encoding="utf-8"
    )
    log.info("manifest: %s", manifest_path)
    write_data_card(manifest_dict, out / "DATA_CARD.md")
    # Phase 2 — Croissant 1.0 JSON-LD for HF Hub / ML Commons compliance
    write_croissant_metadata(manifest_dict, out / "croissant.json")

    # Speech-pipeline side manifest (2026). Additive only — never touches
    # the chat training outputs, so the existing LLM pipeline is unchanged.
    if cfg.emit_speech_manifest:
        _write_speech_manifest(cfg, out / "speech_manifest.json")

    log.info("=" * 70)
    log.info("DONE — %d train / %d val / %d test", len(train), len(val), len(test))
    log.info("=" * 70)
    return manifest_dict


# ---------------------------------------------------------------------------
# Speech manifest (side-car, additive)
# ---------------------------------------------------------------------------


def _write_speech_manifest(cfg: "PipelineConfig", out_path: Path) -> None:
    """Probe optional ASR/MT sources and write a small JSON manifest.

    This does NOT materialise the rows — it only reports shape so the
    speech training scripts can decide whether to run. Keeps the chat
    pipeline byte-identical to the pre-2026 behaviour.
    """
    payload: dict = {
        "schema_version": "2026.1",
        "asr": {},
        "mt": {},
    }

    if cfg.asr_common_voice_dir and cfg.asr_common_voice_dir.exists():
        try:
            from ml.scripts.data_aug.asr_loaders import load_common_voice_lg

            rows = list(load_common_voice_lg(cfg.asr_common_voice_dir))
            hours = round(sum(r.duration_s for r in rows) / 3600, 3)
            payload["asr"]["common_voice"] = {
                "path": str(cfg.asr_common_voice_dir),
                "n_clips": len(rows),
                "hours": hours,
                "languages": sorted({r.language for r in rows}),
            }
        except Exception as exc:
            log.warning("speech manifest: common voice probe failed: %s", exc)
    if cfg.asr_salt_dir and cfg.asr_salt_dir.exists():
        try:
            from ml.scripts.data_aug.asr_loaders import load_salt_asr

            rows = list(load_salt_asr(cfg.asr_salt_dir))
            payload["asr"]["salt"] = {
                "path": str(cfg.asr_salt_dir),
                "n_clips": len(rows),
                "hours": round(sum(r.duration_s for r in rows) / 3600, 3),
                "languages": sorted({r.language for r in rows}),
            }
        except Exception as exc:
            log.warning("speech manifest: salt probe failed: %s", exc)

    if cfg.mt_parallel_dir and cfg.mt_parallel_dir.exists():
        try:
            from ml.scripts.data_aug.mt_loaders import load_parallel_directory

            rows = list(load_parallel_directory(cfg.mt_parallel_dir))
            payload["mt"]["parallel"] = {
                "path": str(cfg.mt_parallel_dir),
                "n_pairs": len(rows),
                "directions": sorted({f"{r.source_lang}_{r.target_lang}" for r in rows}),
            }
        except Exception as exc:
            log.warning("speech manifest: mt probe failed: %s", exc)

    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log.info("speech manifest: %s", out_path)
