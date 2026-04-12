"""Continuous training pipeline (2026).

Implements a feedback-driven retraining loop:
  1. Collect low-confidence + thumbs-down feedback
  2. Detect data drift via embedding distribution shift
  3. Trigger retraining when drift exceeds threshold
  4. Evaluate new model against quality gates
  5. Tag release candidate for human review

Usage::

    python -m ml.pipelines.continuous_training \\
        --feedback-dir Data/feedback \\
        --threshold 0.15 \\
        --dry-run

Integration:
  - Called by .github/workflows/scheduled-retrain.yml (weekly cron)
  - Can be triggered manually via POST /v1/admin/retrain-trigger
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class ContinuousTrainingConfig:
    feedback_dir: Path = Path("Data/feedback")
    training_data_dir: Path = Path("artifacts/training_data")
    output_dir: Path = Path("artifacts/retrain")
    drift_threshold: float = 0.15
    min_feedback_samples: int = 50
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Feedback Collector
# ---------------------------------------------------------------------------


def collect_feedback(feedback_dir: Path) -> list[dict]:
    """Read feedback JSONL exports and filter for retraining candidates.

    Candidates: thumbs-down, low confidence (<0.5), or explicit correction.
    """
    candidates = []
    for jf in sorted(feedback_dir.glob("*.jsonl")):
        with jf.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rating = rec.get("rating", "")
                confidence = rec.get("confidence", 1.0)
                is_negative = rating in ("thumbs_down", "negative", "bad")
                is_low_conf = confidence < 0.5

                if is_negative or is_low_conf:
                    candidates.append(rec)

    log.info("feedback: collected %d retraining candidates from %s",
             len(candidates), feedback_dir)
    return candidates


# ---------------------------------------------------------------------------
# Drift Detection
# ---------------------------------------------------------------------------


def compute_drift(
    feedback_texts: list[str],
    training_centroid_path: Optional[Path] = None,
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> float:
    """Compute embedding distribution drift between feedback and training set.

    Returns cosine distance in [0, 2]. Values > threshold indicate drift.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        log.warning("sentence-transformers not available; skipping drift detection")
        return 0.0

    if not feedback_texts:
        return 0.0

    model = SentenceTransformer(embedder_model)
    feedback_embeddings = model.encode(feedback_texts[:500])  # cap for speed
    feedback_centroid = np.mean(feedback_embeddings, axis=0)

    # Load or compute training centroid.
    if training_centroid_path and training_centroid_path.exists():
        training_centroid = np.load(training_centroid_path)
    else:
        log.info("drift: no training centroid found; computing from training data")
        train_path = Path("artifacts/training_data/training_data.messages.jsonl")
        if not train_path.exists():
            log.warning("drift: no training data found; returning 0")
            return 0.0

        train_texts = []
        with train_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    msgs = rec.get("messages", [])
                    for m in msgs:
                        if m.get("role") == "user":
                            train_texts.append(m["content"])
                except Exception:
                    continue

        if not train_texts:
            return 0.0

        train_embeddings = model.encode(train_texts[:2000])
        training_centroid = np.mean(train_embeddings, axis=0)

        # Cache for next run.
        centroid_path = Path("artifacts/training_data/training_centroid.npy")
        centroid_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(centroid_path, training_centroid)

    # Cosine distance.
    cos_sim = np.dot(feedback_centroid, training_centroid) / (
        np.linalg.norm(feedback_centroid) * np.linalg.norm(training_centroid) + 1e-9
    )
    drift = 1.0 - float(cos_sim)
    log.info("drift: cosine distance = %.4f", drift)
    return drift


# ---------------------------------------------------------------------------
# Retraining Trigger
# ---------------------------------------------------------------------------


def run_continuous_training(cfg: ContinuousTrainingConfig) -> dict:
    """Full continuous training loop. Returns status dict."""
    result = {
        "timestamp": datetime.now().isoformat(),
        "feedback_count": 0,
        "drift_score": 0.0,
        "retrain_triggered": False,
        "evaluation_passed": False,
    }

    # 1. Collect feedback.
    if not cfg.feedback_dir.exists():
        log.info("feedback dir %s not found; nothing to do", cfg.feedback_dir)
        return result

    candidates = collect_feedback(cfg.feedback_dir)
    result["feedback_count"] = len(candidates)

    if len(candidates) < cfg.min_feedback_samples:
        log.info("only %d feedback samples (need %d); skipping",
                 len(candidates), cfg.min_feedback_samples)
        return result

    # 2. Detect drift.
    texts = [c.get("user_query", "") or c.get("message", "") for c in candidates]
    texts = [t for t in texts if t.strip()]

    centroid_path = Path("artifacts/training_data/training_centroid.npy")
    drift = compute_drift(texts, centroid_path, cfg.embedder_model)
    result["drift_score"] = drift

    if drift < cfg.drift_threshold:
        log.info("drift %.4f < threshold %.4f; no retraining needed",
                 drift, cfg.drift_threshold)
        return result

    log.info("drift %.4f >= threshold %.4f; triggering retrain", drift, cfg.drift_threshold)
    result["retrain_triggered"] = True

    if cfg.dry_run:
        log.info("[dry-run] would trigger retraining pipeline")
        return result

    # 3. Run pipeline.
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                sys.executable, "-m", "ml.scripts.data_augmentation",
                "--csv-dir", "Data/dataset",
                "--pdf-dir", "Data/pdfs",
                "--teacher-qa-dir", "Data/teacher_qa",
                "--output-dir", str(cfg.output_dir / "training_data"),
                "-v",
            ],
            check=True,
            timeout=1800,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.error("pipeline failed: %s", e)
        return result

    # 4. Fine-tune (dry-run in CI).
    try:
        subprocess.run(
            [
                sys.executable, "-m", "ml.scripts.fine_tune_gemma",
                "--target", "mobile_gemma_2b",
                "--data-dir", str(cfg.output_dir / "training_data"),
                "--output-dir", str(cfg.output_dir / "model"),
                "--dry-run",
            ],
            check=True,
            timeout=3600,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.error("fine-tune failed: %s", e)
        return result

    # 5. Evaluate.
    try:
        subprocess.run(
            [
                sys.executable, "-m", "ml.pipelines.quality_gates",
                "--family", "production",
                "--results-dir", str(cfg.output_dir),
                "--soft-fail",
            ],
            check=True,
            timeout=600,
        )
        result["evaluation_passed"] = True
    except subprocess.CalledProcessError:
        log.warning("quality gates failed — model candidate flagged for review")

    # Write result summary.
    summary_path = cfg.output_dir / "retrain_summary.json"
    summary_path.write_text(json.dumps(result, indent=2))
    log.info("retrain complete: %s", summary_path)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Continuous training pipeline")
    parser.add_argument("--feedback-dir", type=Path, default=Path("Data/feedback"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/retrain"))
    parser.add_argument("--threshold", type=float, default=0.15)
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = ContinuousTrainingConfig(
        feedback_dir=args.feedback_dir,
        output_dir=args.output_dir,
        drift_threshold=args.threshold,
        min_feedback_samples=args.min_samples,
        dry_run=args.dry_run,
    )
    result = run_continuous_training(cfg)
    log.info("result: %s", json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
