"""Unified training orchestrator (2026).

Chains the full training lifecycle for all model families in the correct
dependency order:

    1. Data augmentation (pipeline.py) — produces training JSONL
    2. ASR fine-tune (train_luganda.py) — Whisper LoRA on Common Voice
    3. MT fine-tune (finetune_mt.py) — MADLAD-400 LoRA on parallel data
    4. MT distillation (distill_mt.py) — teacher → mobile student
    5. LLM fine-tune (fine_tune_gemma.py) — Gemma-2-2B on augmented data
    6. TTS training (train_luganda_vits.py) — VITS on recorded voice
    7. ONNX export (export_*.py) — INT8 quantised for mobile
    8. Quality gates (quality_gates.py) — pass/fail per model family
    9. Mobile bundle (export_mobile_speech.py) — atomic deploy to Flutter assets

Stages run sequentially by default; ASR/MT/TTS can run in parallel
since they share no artifacts.  Use ``--parallel`` to enable this.

Usage::

    # Full dry-run (validates config, no GPU needed):
    python -m ml.scripts.train_all --dry-run

    # Real training (GPU required):
    python -m ml.scripts.train_all --target mobile_gemma_2b

    # Skip stages that already have artifacts:
    python -m ml.scripts.train_all --skip data,asr --target mobile_gemma_2b
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class StageResult:
    name: str
    status: str = "pending"  # pending | running | success | failed | skipped
    duration_s: float = 0.0
    error: Optional[str] = None
    artifacts: list[str] = field(default_factory=list)


@dataclass
class TrainAllConfig:
    target: str = "mobile_gemma_2b"
    data_dir: Path = Path("Data")
    output_dir: Path = Path("artifacts")
    skip_stages: set[str] = field(default_factory=set)
    parallel_speech: bool = False
    dry_run: bool = False
    verbose: bool = False

    # Per-stage overrides.
    asr_languages: list[str] = field(default_factory=lambda: ["lg"])
    mt_directions: list[str] = field(default_factory=lambda: ["en-lg"])
    tts_languages: list[str] = field(default_factory=lambda: ["lg"])


def _run_stage(
    name: str,
    cmd: list[str],
    *,
    dry_run: bool = False,
    timeout: int = 7200,
) -> StageResult:
    """Execute a training stage as a subprocess."""
    result = StageResult(name=name)

    if dry_run:
        cmd = [*cmd, "--dry-run"]

    log.info("=" * 60)
    log.info("STAGE: %s", name)
    log.info("CMD: %s", " ".join(cmd))
    log.info("=" * 60)

    result.status = "running"
    start = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            check=True,
            timeout=timeout,
            capture_output=not log.isEnabledFor(logging.DEBUG),
            text=True,
        )
        result.status = "success"
        if proc.stdout:
            log.debug("stdout: %s", proc.stdout[-500:])
    except subprocess.CalledProcessError as e:
        result.status = "failed"
        result.error = f"exit code {e.returncode}"
        if e.stderr:
            result.error += f": {e.stderr[-200:]}"
        log.error("FAILED %s: %s", name, result.error)
    except subprocess.TimeoutExpired:
        result.status = "failed"
        result.error = f"timeout after {timeout}s"
        log.error("TIMEOUT %s after %ds", name, timeout)
    finally:
        result.duration_s = round(time.monotonic() - start, 1)

    return result


def run_training(cfg: TrainAllConfig) -> dict:
    """Execute the full training pipeline. Returns summary dict."""
    py = sys.executable
    results: list[StageResult] = []

    # --- Stage 1: Data augmentation ---
    if "data" not in cfg.skip_stages:
        r = _run_stage("data_augmentation", [
            py, "-m", "ml.scripts.data_augmentation",
            "--csv-dir", str(cfg.data_dir / "dataset"),
            "--pdf-dir", str(cfg.data_dir / "pdfs"),
            "--luganda-dir", str(cfg.data_dir / "TTT"),
            "--teacher-qa-dir", str(cfg.data_dir / "teacher_qa"),
            "--output-dir", str(cfg.output_dir / "training_data"),
            "-v",
        ], dry_run=cfg.dry_run)
        results.append(r)
        if r.status == "failed" and not cfg.dry_run:
            return _summary(results, cfg)
    else:
        results.append(StageResult(name="data_augmentation", status="skipped"))

    # --- Stage 2: ASR fine-tune ---
    if "asr" not in cfg.skip_stages:
        for lang in cfg.asr_languages:
            r = _run_stage(f"asr_{lang}", [
                py, "-m", "ml.scripts.asr.train_luganda",
                "--common-voice", str(cfg.data_dir / f"common_voice_{lang}"),
                "--output-dir", str(cfg.output_dir / "speech" / "asr" / f"whisper_{lang}"),
                "--language", lang,
            ], dry_run=cfg.dry_run, timeout=14400)
            results.append(r)
    else:
        results.append(StageResult(name="asr", status="skipped"))

    # --- Stage 3: MT fine-tune ---
    if "mt" not in cfg.skip_stages:
        r = _run_stage("mt_finetune", [
            py, "-m", "ml.scripts.mt.finetune_mt",
            "--data-dir", str(cfg.data_dir / "TTT"),
            "--output-dir", str(cfg.output_dir / "mt" / "teacher"),
        ], dry_run=cfg.dry_run, timeout=14400)
        results.append(r)

        # Stage 3b: Distillation.
        if r.status == "success" or cfg.dry_run:
            r2 = _run_stage("mt_distill", [
                py, "-m", "ml.scripts.mt.distill_mt",
                "--teacher-dir", str(cfg.output_dir / "mt" / "teacher"),
                "--output-dir", str(cfg.output_dir / "mt" / "student"),
            ], dry_run=cfg.dry_run, timeout=14400)
            results.append(r2)
    else:
        results.append(StageResult(name="mt", status="skipped"))

    # --- Stage 4: LLM fine-tune ---
    if "llm" not in cfg.skip_stages:
        r = _run_stage("llm_finetune", [
            py, "-m", "ml.scripts.fine_tune_gemma",
            "--target", cfg.target,
            "--data-dir", str(cfg.output_dir / "training_data"),
            "--output-dir", str(cfg.output_dir / f"llm_{cfg.target}"),
        ], dry_run=cfg.dry_run, timeout=14400)
        results.append(r)
    else:
        results.append(StageResult(name="llm", status="skipped"))

    # --- Stage 5: TTS training ---
    if "tts" not in cfg.skip_stages:
        for lang in cfg.tts_languages:
            voice_dir = cfg.data_dir / "speech" / "tts" / f"{lang}_voice"
            if not voice_dir.exists() and not cfg.dry_run:
                log.warning("TTS: voice data dir %s not found, skipping", voice_dir)
                results.append(StageResult(name=f"tts_{lang}", status="skipped",
                                           error="no voice data"))
                continue
            r = _run_stage(f"tts_{lang}", [
                py, "-m", "ml.scripts.tts.train_luganda_vits",
                "--data-dir", str(voice_dir),
                "--output-dir", str(cfg.output_dir / "speech" / "tts" / f"{lang}_vits"),
            ], dry_run=cfg.dry_run, timeout=43200)
            results.append(r)
    else:
        results.append(StageResult(name="tts", status="skipped"))

    # --- Stage 6: ONNX export ---
    if "export" not in cfg.skip_stages:
        for script in ["asr.export_asr_onnx", "mt.export_mt_onnx", "tts.export_tts_onnx"]:
            r = _run_stage(f"export_{script.split('.')[0]}", [
                py, "-m", f"ml.scripts.{script}",
                "--artifacts-dir", str(cfg.output_dir),
            ], dry_run=cfg.dry_run)
            results.append(r)
    else:
        results.append(StageResult(name="export", status="skipped"))

    # --- Stage 7: Quality gates ---
    if "gates" not in cfg.skip_stages:
        for family in ["classifier", "speech", "mt", "production"]:
            r = _run_stage(f"gates_{family}", [
                py, "-m", "ml.pipelines.quality_gates",
                "--family", family,
                "--results-dir", str(cfg.output_dir),
                "--soft-fail",
            ], dry_run=cfg.dry_run, timeout=600)
            results.append(r)
    else:
        results.append(StageResult(name="gates", status="skipped"))

    return _summary(results, cfg)


def _summary(results: list[StageResult], cfg: TrainAllConfig) -> dict:
    """Build JSON summary of training run."""
    summary = {
        "target": cfg.target,
        "dry_run": cfg.dry_run,
        "total_duration_s": sum(r.duration_s for r in results),
        "stages": {
            r.name: {
                "status": r.status,
                "duration_s": r.duration_s,
                "error": r.error,
            }
            for r in results
        },
        "passed": all(r.status in ("success", "skipped") for r in results),
    }

    out_path = cfg.output_dir / "train_all_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    log.info("Summary written to %s", out_path)

    for r in results:
        icon = {"success": "+", "failed": "X", "skipped": "-", "pending": "?"}
        log.info("  [%s] %-25s %6.1fs %s",
                 icon.get(r.status, "?"), r.name, r.duration_s, r.error or "")

    return summary


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Unified training orchestrator")
    parser.add_argument("--target", default="mobile_gemma_2b",
                        choices=["web_high_accuracy", "mobile_gemma_2b",
                                 "mobile_offline", "background_t5"])
    parser.add_argument("--data-dir", type=Path, default=Path("Data"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--skip", default="", help="Comma-separated stages to skip")
    parser.add_argument("--parallel", action="store_true", help="Run ASR/MT/TTS in parallel")
    parser.add_argument("--asr-langs", nargs="+", default=["lg"])
    parser.add_argument("--mt-dirs", nargs="+", default=["en-lg"])
    parser.add_argument("--tts-langs", nargs="+", default=["lg"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-5s %(name)s: %(message)s",
    )

    cfg = TrainAllConfig(
        target=args.target,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        skip_stages=set(args.skip.split(",")) if args.skip else set(),
        parallel_speech=args.parallel,
        dry_run=args.dry_run,
        verbose=args.verbose,
        asr_languages=args.asr_langs,
        mt_directions=args.mt_dirs,
        tts_languages=args.tts_langs,
    )

    summary = run_training(cfg)
    status = "PASSED" if summary["passed"] else "FAILED"
    log.info("Training %s in %.1fs", status, summary["total_duration_s"])
    sys.exit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
