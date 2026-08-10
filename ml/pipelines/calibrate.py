"""Model calibration + selective-prediction analysis.

A production offline mobile tax assistant must be **calibrated**: when the
model reports a confidence of 0.9 it should be right ~90% of the time.
This matters because:

1. The backend uses a confidence threshold to decide when to abstain or
   escalate to a human agent. An uncalibrated model silently defeats the
   escalation logic — either by over-trusting shaky answers (undermining
   safety) or by over-abstaining on confident answers (undermining
   usability).
2. EU AI Act Art. 13 requires "information on the accuracy, robustness
   and cybersecurity" of a deployed model. Reliability curves + ECE are
   the standard vocabulary for this.

This module operates on a JSONL of ``(confidence, correct)`` tuples,
which the caller is responsible for producing from whatever
model-evaluation loop makes sense for its task (classification head on
top of the tag classifier, RAG answer grounding score, etc.). The module
itself stays model-agnostic so it can be re-used for both the sklearn
tag classifier and any LLM-level confidence signal.

Metrics computed
----------------

- **Expected Calibration Error (ECE)** — Naeini et al. 2015. Classic
  equal-width binning of predicted confidences.
- **Maximum Calibration Error (MCE)** — worst bin.
- **Brier score** — quadratic proper scoring rule on probabilities.
- **Reliability curve** — (mean_confidence, accuracy) per bin.
- **Temperature scaling** — Guo et al. 2017. Fits a single scalar T on a
  held-out calibration split and reports post-scaling ECE.
- **Coverage-vs-accuracy curve** — for selective prediction: at each
  confidence threshold t, what fraction of inputs are answered
  (coverage) and what is the accuracy on the answered subset?
- **Recommended threshold** — smallest t that achieves a target
  accuracy (default 0.95) on the answered subset, or None if
  unreachable.

Usage
-----

    # eval_preds.jsonl has: {"confidence": 0.87, "correct": true, "language": "en", ...}
    python -m ml.pipelines.calibrate \
        --preds Results/eval_preds.jsonl \
        --target-accuracy 0.95 \
        --output-dir Results/calibration

Outputs ``calibration_report.json`` + ``reliability_curve.json`` +
``coverage_accuracy.json`` (all plain JSON, no matplotlib dependency so
the pipeline runs in headless CI without a display).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ml.scripts.repro import env_snapshot, set_global_seed, write_snapshot  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger("calibrate")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_predictions(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL of prediction records.

    Required fields per row:
      * ``confidence`` — float in [0, 1]
      * ``correct``    — bool (the prediction was correct)

    Optional fields used for slicing:
      * ``language`` — e.g. "en", "lg"
      * ``category`` / ``tag`` / ``topic``
      * ``logit``   — pre-sigmoid/softmax value, used for temperature scaling
    """
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Bad JSON at {path}:{i} — {exc}") from exc
            if "confidence" not in r or "correct" not in r:
                raise ValueError(f"Row {path}:{i} missing 'confidence' or 'correct' field")
            r["confidence"] = float(r["confidence"])
            r["correct"] = bool(r["correct"])
            rows.append(r)
    log.info("Loaded %d prediction rows from %s", len(rows), path)
    return rows


# ---------------------------------------------------------------------------
# Calibration metrics
# ---------------------------------------------------------------------------
def compute_ece(
    confidences: list[float],
    corrects: list[bool],
    n_bins: int = 15,
) -> dict[str, Any]:
    """Equal-width ECE / MCE on the unit interval [0, 1].

    Returns the ECE, MCE, and a per-bin reliability curve. A perfectly
    calibrated model has ECE ≈ 0 and every ``bin_accuracy`` sitting on
    the diagonal ``bin_confidence``.
    """
    n = len(confidences)
    if n == 0:
        return {"ece": 0.0, "mce": 0.0, "bins": []}

    # Uniform bins — the original Naeini formulation.
    edges = [i / n_bins for i in range(n_bins + 1)]
    bins: list[dict[str, Any]] = []
    ece = 0.0
    mce = 0.0

    for bi in range(n_bins):
        lo, hi = edges[bi], edges[bi + 1]
        # Last bin is [lo, hi] (inclusive) — everywhere else it is [lo, hi).
        if bi < n_bins - 1:
            in_bin = [(c, y) for c, y in zip(confidences, corrects, strict=False) if lo <= c < hi]
        else:
            in_bin = [(c, y) for c, y in zip(confidences, corrects, strict=False) if lo <= c <= hi]

        size = len(in_bin)
        if size == 0:
            bins.append(
                {
                    "lo": round(lo, 4),
                    "hi": round(hi, 4),
                    "count": 0,
                    "accuracy": None,
                    "confidence": None,
                    "gap": None,
                }
            )
            continue

        mean_conf = sum(c for c, _ in in_bin) / size
        accuracy = sum(1 for _, y in in_bin if y) / size
        gap = abs(mean_conf - accuracy)
        weight = size / n

        ece += weight * gap
        mce = max(mce, gap)

        bins.append(
            {
                "lo": round(lo, 4),
                "hi": round(hi, 4),
                "count": size,
                "accuracy": round(accuracy, 4),
                "confidence": round(mean_conf, 4),
                "gap": round(gap, 4),
            }
        )

    return {
        "ece": round(ece, 4),
        "mce": round(mce, 4),
        "n_samples": n,
        "n_bins": n_bins,
        "bins": bins,
    }


def compute_brier(confidences: list[float], corrects: list[bool]) -> float:
    """Brier score — mean squared error between confidence and outcome.

    Lower is better. A random guess on balanced data gives ~0.25.
    """
    if not confidences:
        return 0.0
    total = sum((c - (1.0 if y else 0.0)) ** 2 for c, y in zip(confidences, corrects, strict=False))
    return round(total / len(confidences), 4)


# ---------------------------------------------------------------------------
# Temperature scaling
# ---------------------------------------------------------------------------
def _sigmoid(x: float) -> float:
    # Numerically stable.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _nll_binary(logits: Iterable[float], corrects: Iterable[bool]) -> float:
    """Negative log likelihood of a binary classifier.

    We operate in the binary "correct / wrong" formulation because the
    calibration task is always a binary reliability question, regardless
    of the underlying model's task. ``logit`` is the pre-sigmoid score
    of the predicted class.
    """
    total = 0.0
    n = 0
    for z, y in zip(logits, corrects, strict=False):
        p = _sigmoid(z)
        p = max(min(p, 1.0 - 1e-12), 1e-12)
        total -= math.log(p) if y else math.log(1.0 - p)
        n += 1
    return total / max(n, 1)


def fit_temperature(
    logits: list[float],
    corrects: list[bool],
    *,
    t_min: float = 0.25,
    t_max: float = 5.0,
    steps: int = 120,
) -> float:
    """Fit a scalar temperature T ∈ [t_min, t_max] by grid search on NLL.

    Grid search rather than scipy.optimize so this file has zero
    non-stdlib dependencies and runs in any Python environment.
    """
    if not logits:
        return 1.0

    best_t = 1.0
    best_nll = float("inf")
    # Log-uniform grid tends to do better than linear on temperature.
    log_lo, log_hi = math.log(t_min), math.log(t_max)
    for i in range(steps):
        t = math.exp(log_lo + (log_hi - log_lo) * i / (steps - 1))
        scaled = [z / t for z in logits]
        nll = _nll_binary(scaled, corrects)
        if nll < best_nll:
            best_nll = nll
            best_t = t

    return round(best_t, 4)


def apply_temperature(logits: list[float], temperature: float) -> list[float]:
    return [_sigmoid(z / temperature) for z in logits]


# ---------------------------------------------------------------------------
# Selective prediction
# ---------------------------------------------------------------------------
def coverage_accuracy_curve(
    confidences: list[float],
    corrects: list[bool],
    *,
    thresholds: list[float] | None = None,
) -> list[dict[str, float]]:
    """Report (threshold, coverage, accuracy) for a sweep of thresholds.

    At each threshold t, a row is *answered* iff its confidence ≥ t. The
    accuracy is computed only over answered rows. A tax assistant that
    abstains when confidence is below t is exactly this operating point.
    """
    if thresholds is None:
        thresholds = [i / 20 for i in range(21)]  # 0, 0.05, …, 1.0

    n = len(confidences)
    curve: list[dict[str, float]] = []
    for t in thresholds:
        answered = [(c, y) for c, y in zip(confidences, corrects, strict=False) if c >= t]
        coverage = len(answered) / n if n else 0.0
        accuracy = sum(1 for _, y in answered if y) / len(answered) if answered else 0.0
        curve.append(
            {
                "threshold": round(t, 4),
                "coverage": round(coverage, 4),
                "accuracy": round(accuracy, 4),
                "n_answered": len(answered),
            }
        )
    return curve


def recommend_threshold(
    curve: list[dict[str, float]],
    *,
    target_accuracy: float = 0.95,
    min_coverage: float = 0.5,
) -> dict[str, Any]:
    """Pick the smallest threshold whose answered accuracy ≥ target.

    Also respects ``min_coverage`` — if the only thresholds that hit the
    target accuracy answer fewer than ``min_coverage`` of inputs, the
    recommendation is ``None`` with an explanation (the model is not
    reliable enough to answer that large a fraction at that accuracy).
    """
    eligible = [
        p for p in curve if p["accuracy"] >= target_accuracy and p["coverage"] >= min_coverage
    ]
    if not eligible:
        return {
            "threshold": None,
            "target_accuracy": target_accuracy,
            "min_coverage": min_coverage,
            "reason": "No threshold satisfies both target_accuracy and min_coverage",
        }
    # Smallest threshold (maximises coverage while meeting the accuracy floor).
    winner = min(eligible, key=lambda p: p["threshold"])
    return {
        "threshold": winner["threshold"],
        "target_accuracy": target_accuracy,
        "min_coverage": min_coverage,
        "coverage_at_threshold": winner["coverage"],
        "accuracy_at_threshold": winner["accuracy"],
        "n_answered": winner["n_answered"],
    }


# ---------------------------------------------------------------------------
# Sliced calibration (per-language / per-category)
# ---------------------------------------------------------------------------
def slice_calibration(
    rows: list[dict[str, Any]],
    axis: str,
    *,
    n_bins: int,
    min_slice_n: int = 20,
) -> dict[str, dict[str, Any]]:
    """Compute ECE / Brier per bucket on a categorical axis.

    Buckets with fewer than ``min_slice_n`` rows are skipped to avoid
    noisy "100% accuracy on 2 rows" reports.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = (r.get(axis) or "unknown").lower()
        buckets.setdefault(key, []).append(r)

    out: dict[str, dict[str, Any]] = {}
    for key, rs in sorted(buckets.items()):
        if len(rs) < min_slice_n:
            continue
        confs = [r["confidence"] for r in rs]
        ys = [r["correct"] for r in rs]
        ece = compute_ece(confs, ys, n_bins=n_bins)
        out[key] = {
            "n": len(rs),
            "accuracy": round(sum(1 for y in ys if y) / len(ys), 4),
            "mean_confidence": round(sum(confs) / len(confs), 4),
            "ece": ece["ece"],
            "mce": ece["mce"],
            "brier": compute_brier(confs, ys),
        }
    return out


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def run(
    rows: list[dict[str, Any]],
    *,
    n_bins: int = 15,
    target_accuracy: float = 0.95,
    min_coverage: float = 0.5,
) -> dict[str, Any]:
    """Full calibration + selective-prediction report on ``rows``."""
    confs = [r["confidence"] for r in rows]
    ys = [r["correct"] for r in rows]

    raw_ece = compute_ece(confs, ys, n_bins=n_bins)
    brier = compute_brier(confs, ys)

    # Temperature scaling — only if the caller provided raw logits.
    logits = [r.get("logit") for r in rows]
    scaled_ece = None
    temperature = None
    if all(z is not None for z in logits):
        temperature = fit_temperature([float(z) for z in logits], ys)
        scaled_confs = apply_temperature([float(z) for z in logits], temperature)
        scaled_ece = compute_ece(scaled_confs, ys, n_bins=n_bins)

    curve = coverage_accuracy_curve(confs, ys)
    recommendation = recommend_threshold(
        curve, target_accuracy=target_accuracy, min_coverage=min_coverage
    )

    return {
        "summary": {
            "n_samples": len(rows),
            "accuracy": round(sum(1 for y in ys if y) / len(ys), 4) if ys else 0.0,
            "mean_confidence": round(sum(confs) / len(confs), 4) if confs else 0.0,
            "ece": raw_ece["ece"],
            "mce": raw_ece["mce"],
            "brier": brier,
            "temperature": temperature,
            "ece_post_scaling": scaled_ece["ece"] if scaled_ece else None,
        },
        "reliability_curve": raw_ece,
        "reliability_curve_scaled": scaled_ece,
        "coverage_accuracy": curve,
        "recommended_threshold": recommendation,
        "by_language": slice_calibration(rows, "language", n_bins=n_bins),
        "by_category": slice_calibration(
            rows,
            "category" if any("category" in r for r in rows) else "tag",
            n_bins=n_bins,
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Calibration + selective-prediction analysis")
    parser.add_argument(
        "--preds",
        type=Path,
        required=True,
        help="JSONL with per-row {confidence, correct, [logit, language, category]}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("Results/calibration"),
    )
    parser.add_argument("--n-bins", type=int, default=15)
    parser.add_argument("--target-accuracy", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--fail-on-ece",
        type=float,
        default=None,
        help="If set, exit non-zero when raw ECE exceeds this value.",
    )
    args = parser.parse_args()

    set_global_seed(args.seed)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_predictions(args.preds)
    if not rows:
        log.error("No prediction rows to calibrate.")
        sys.exit(1)

    report = run(
        rows,
        n_bins=args.n_bins,
        target_accuracy=args.target_accuracy,
        min_coverage=args.min_coverage,
    )
    report["_env"] = env_snapshot(script="calibrate")
    report["_input"] = str(args.preds)

    (output_dir / "calibration_report.json").write_text(json.dumps(report, indent=2))
    (output_dir / "reliability_curve.json").write_text(
        json.dumps(report["reliability_curve"], indent=2)
    )
    (output_dir / "coverage_accuracy.json").write_text(
        json.dumps(report["coverage_accuracy"], indent=2)
    )
    write_snapshot(report["_env"], output_dir / "env_snapshots")

    s = report["summary"]
    print("=" * 60)
    print("CALIBRATION REPORT")
    print("=" * 60)
    print(f"  samples            {s['n_samples']}")
    print(f"  accuracy           {s['accuracy']:.4f}")
    print(f"  mean confidence    {s['mean_confidence']:.4f}")
    print(f"  ECE                {s['ece']:.4f}")
    print(f"  MCE                {s['mce']:.4f}")
    print(f"  Brier              {s['brier']:.4f}")
    if s["temperature"] is not None:
        print(
            f"  temperature        {s['temperature']:.4f}  "
            f"(post-scaling ECE: {s['ece_post_scaling']:.4f})"
        )
    rec = report["recommended_threshold"]
    if rec.get("threshold") is not None:
        print(
            f"  recommended t      {rec['threshold']:.2f} "
            f"→ coverage {rec['coverage_at_threshold']:.2f}, "
            f"accuracy {rec['accuracy_at_threshold']:.3f}"
        )
    else:
        print(f"  recommended t      <none> ({rec.get('reason', '')})")
    print(f"  report written to  {output_dir / 'calibration_report.json'}")

    if args.fail_on_ece is not None and s["ece"] > args.fail_on_ece:
        log.error("ECE %.4f exceeds --fail-on-ece %.4f", s["ece"], args.fail_on_ece)
        sys.exit(2)


if __name__ == "__main__":
    main()
