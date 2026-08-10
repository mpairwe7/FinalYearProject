"""Unified ML plot generator (2026).

Reads all evaluation JSON outputs and generates a complete set of
publication-quality PNG plots for model cards, governance reports,
and CI/CD dashboards.

Covers:
  - Calibration reliability diagram (EU AI Act Art. 13)
  - ASR WER/CER per-language comparison
  - MT BLEU/chrF per-direction comparison
  - RAG faithfulness/relevancy radar chart
  - TTS quality metrics
  - Data quality histograms (source distribution, token lengths)
  - Mobile benchmark latency breakdown
  - Quality gate pass/fail dashboard
  - Training loss curves (if MLflow/JSON logs exist)

Usage::

    python -m ml.plots.generate_all \\
        --metrics-dir Results/metrics \\
        --output-dir Results/plots \\
        --format png
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Lazy imports — matplotlib is heavy, only load when needed.
_MPL_AVAILABLE = False


def _ensure_mpl():
    global _MPL_AVAILABLE
    if _MPL_AVAILABLE:
        return
    try:
        import matplotlib

        matplotlib.use("Agg")  # Non-interactive backend for CI.
        _MPL_AVAILABLE = True
    except ImportError as err:
        raise ImportError(
            "matplotlib required for plot generation: pip install matplotlib seaborn"
        ) from err


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        log.warning("metrics file not found: %s", path)
        return None
    return json.loads(path.read_text())


def _save_fig(fig, path: Path, fmt: str = "png"):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format=fmt, dpi=150, bbox_inches="tight", facecolor="white")
    log.info("saved: %s", path)
    import matplotlib.pyplot as plt

    plt.close(fig)


# ── Calibration Reliability Diagram ──────────────────────────────────


def plot_calibration(metrics_dir: Path, output_dir: Path, fmt: str = "png"):
    """Generate reliability diagram from calibration_report.json.

    This is the #1 EU AI Act Art. 13 governance artifact — shows
    whether the model's confidence scores are trustworthy.
    """
    _ensure_mpl()
    import matplotlib.pyplot as plt

    data = _load_json(metrics_dir / "calibration_report.json")
    if not data:
        return

    # Reliability curve.
    bins = data.get("reliability_curve", data.get("bins", []))
    if not bins:
        log.warning("no reliability bins in calibration data")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Left: reliability diagram.
    confidences = [b.get("mean_confidence", b.get("confidence", 0)) for b in bins]
    accuracies = [b.get("accuracy", 0) for b in bins]
    counts = [b.get("count", 0) for b in bins]

    ax1.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect calibration")
    ax1.bar(
        confidences,
        accuracies,
        width=0.08,
        alpha=0.6,
        color="#1a3a6b",
        edgecolor="white",
        label="Model",
    )
    ax1.set_xlabel("Mean Predicted Confidence")
    ax1.set_ylabel("Actual Accuracy")
    ax1.set_title("Reliability Diagram")
    ax1.legend()
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)

    ece = data.get("ece", data.get("expected_calibration_error", 0))
    mce = data.get("mce", data.get("maximum_calibration_error", 0))
    ax1.text(
        0.05,
        0.92,
        f"ECE = {ece:.3f}\nMCE = {mce:.3f}",
        transform=ax1.transAxes,
        fontsize=9,
        verticalalignment="top",
        bbox={"boxstyle": "round", "facecolor": "wheat"},
    )

    # Right: confidence histogram.
    ax2.bar(confidences, counts, width=0.08, color="#c69c2f", edgecolor="white")
    ax2.set_xlabel("Confidence Bin")
    ax2.set_ylabel("Sample Count")
    ax2.set_title("Confidence Distribution")

    fig.suptitle("Model Calibration Analysis", fontsize=14, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, output_dir / f"calibration_reliability.{fmt}", fmt)

    # Coverage-accuracy curve.
    cov_data = data.get("coverage_accuracy", [])
    if cov_data:
        fig2, ax = plt.subplots(figsize=(7, 5))
        thresholds = [c.get("threshold", 0) for c in cov_data]
        coverages = [c.get("coverage", 0) for c in cov_data]
        accs = [c.get("accuracy", 0) for c in cov_data]

        ax.plot(thresholds, coverages, "b-o", markersize=3, label="Coverage")
        ax.plot(thresholds, accs, "r-s", markersize=3, label="Accuracy")
        ax.set_xlabel("Abstention Threshold")
        ax.set_ylabel("Ratio")
        ax.set_title("Coverage vs Accuracy Trade-off")
        ax.legend()
        ax.grid(alpha=0.3)
        _save_fig(fig2, output_dir / f"coverage_accuracy.{fmt}", fmt)


# ── ASR Speech Metrics ───────────────────────────────────────────────


def plot_speech(metrics_dir: Path, output_dir: Path, fmt: str = "png"):
    """Per-language WER/CER comparison bar chart."""
    _ensure_mpl()
    import matplotlib.pyplot as plt
    import numpy as np

    data = _load_json(metrics_dir / "speech_metrics.json")
    if not data:
        return

    slices = data.get("per_language", data.get("slices", {}))
    if not slices:
        # Try flat structure.
        slices = {"overall": data}

    langs = list(slices.keys())
    wers = [slices[l].get("wer", 0) for l in langs]
    cers = [slices[l].get("cer", 0) for l in langs]

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(langs))
    w = 0.35

    bars1 = ax.bar(x - w / 2, wers, w, label="WER", color="#1a3a6b")
    bars2 = ax.bar(x + w / 2, cers, w, label="CER", color="#c69c2f")

    ax.set_xlabel("Language")
    ax.set_ylabel("Error Rate")
    ax.set_title("ASR Performance by Language")
    ax.set_xticks(x)
    ax.set_xticklabels([l.upper() for l in langs])
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # Add value labels.
    for bar in bars1:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.005,
            f"{bar.get_height():.2f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    for bar in bars2:
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.005,
            f"{bar.get_height():.2f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    # Draw quality gate thresholds.
    gate_wer = {"en": 0.15, "lg": 0.25, "sw": 0.20, "nyn": 0.30, "ach": 0.30}
    for i, l in enumerate(langs):
        if l in gate_wer:
            ax.hlines(gate_wer[l], i - 0.4, i + 0.4, colors="red", linestyles="dashed", linewidth=1)

    fig.tight_layout()
    _save_fig(fig, output_dir / f"asr_per_language.{fmt}", fmt)


# ── MT Translation Metrics ───────────────────────────────────────────


def plot_mt(metrics_dir: Path, output_dir: Path, fmt: str = "png"):
    """Per-direction BLEU/chrF comparison."""
    _ensure_mpl()
    import matplotlib.pyplot as plt

    data = _load_json(metrics_dir / "mt_metrics.json")
    if not data:
        return

    directions = data.get("per_direction", data.get("directions", {}))
    if not directions:
        directions = {"overall": data}

    dirs = list(directions.keys())
    bleus = [directions[d].get("bleu", 0) for d in dirs]
    chrfs = [directions[d].get("chrf", 0) for d in dirs]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    colors = [
        "#1a3a6b",
        "#c69c2f",
        "#2d8659",
        "#8b4513",
        "#4a0e4e",
        "#0e4a4a",
        "#4a2f0e",
        "#2f0e4a",
    ]

    # BLEU.
    bars = ax1.barh(dirs, bleus, color=colors[: len(dirs)])
    ax1.set_xlabel("BLEU Score")
    ax1.set_title("Translation Quality (BLEU)")
    for bar, val in zip(bars, bleus, strict=False):
        ax1.text(
            bar.get_width() + 0.3,
            bar.get_y() + bar.get_height() / 2,
            f"{val:.1f}",
            va="center",
            fontsize=9,
        )

    # chrF.
    bars2 = ax2.barh(dirs, chrfs, color=colors[: len(dirs)])
    ax2.set_xlabel("chrF Score")
    ax2.set_title("Translation Quality (chrF)")
    for bar, val in zip(bars2, chrfs, strict=False):
        ax2.text(
            bar.get_width() + 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{val:.2f}",
            va="center",
            fontsize=9,
        )

    fig.suptitle("Machine Translation Performance", fontsize=14, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, output_dir / f"mt_per_direction.{fmt}", fmt)


# ── RAG Quality Radar Chart ──────────────────────────────────────────


def plot_rag(metrics_dir: Path, output_dir: Path, fmt: str = "png"):
    """RAG quality radar chart showing all 8 metrics."""
    _ensure_mpl()
    import matplotlib.pyplot as plt
    import numpy as np

    data = _load_json(metrics_dir / "rag_evaluation_results.json")
    if not data:
        return

    metrics_keys = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
        "groundedness",
        "citation_accuracy",
        "numerical_accuracy",
    ]
    labels = [k.replace("_", " ").title() for k in metrics_keys]
    values = [data.get(k, 0) for k in metrics_keys]

    # Close the radar.
    values += values[:1]
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw={"polar": True})
    ax.fill(angles, values, alpha=0.25, color="#1a3a6b")
    ax.plot(angles, values, "o-", color="#1a3a6b", linewidth=2, markersize=6)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 1)
    ax.set_title("RAG Quality Metrics", pad=20, fontsize=14, fontweight="bold")

    # Add quality gate minimums as dashed ring.
    gates = [0.6, 0.7, 0.5, 0.5, 0.4, 0.4, 0.5]
    gates += gates[:1]
    ax.plot(angles, gates, "r--", linewidth=1, alpha=0.5, label="Quality Gate")
    ax.legend(loc="upper right", bbox_to_anchor=(1.2, 1.1))

    _save_fig(fig, output_dir / f"rag_radar.{fmt}", fmt)


# ── Data Quality Histograms ──────────────────────────────────────────


def plot_data_quality(metrics_dir: Path, output_dir: Path, fmt: str = "png"):
    """Data pipeline distribution charts from manifest.json."""
    _ensure_mpl()
    import matplotlib.pyplot as plt

    # Try training data manifest.
    manifest = _load_json(Path("artifacts/training_data/manifest.json"))
    if not manifest:
        manifest = _load_json(metrics_dir / "manifest.json")
    if not manifest:
        return

    stages = manifest.get("stages", {})
    dedup = stages.get("ingest_dedup", {})
    quality = stages.get("quality", {})
    split = stages.get("split", {})

    # Source distribution pie chart.
    by_source = dedup.get("by_source_type", quality.get("by_source", {}))
    if by_source:
        fig, ax = plt.subplots(figsize=(8, 6))
        labels = list(by_source.keys())
        sizes = list(by_source.values())
        colors = plt.cm.Set3(range(len(labels)))
        wedges, texts, autotexts = ax.pie(
            sizes,
            labels=labels,
            autopct="%1.1f%%",
            colors=colors,
            pctdistance=0.85,
            startangle=90,
        )
        for t in autotexts:
            t.set_fontsize(8)
        ax.set_title("Training Data by Source Type", fontsize=14, fontweight="bold")
        _save_fig(fig, output_dir / f"data_source_distribution.{fmt}", fmt)

    # Pipeline funnel.
    funnel_data = {}
    if dedup:
        funnel_data["Raw Ingested"] = dedup.get("input_count", dedup.get("total_before", 0))
        funnel_data["After Dedup"] = dedup.get("output_count", dedup.get("total_after", 0))
    if quality:
        funnel_data["After Quality"] = quality.get("output_count", 0)
    if split:
        funnel_data["Train"] = split.get("train", 0)
        funnel_data["Val"] = split.get("val", 0)
        funnel_data["Test"] = split.get("test", 0)

    if funnel_data:
        fig, ax = plt.subplots(figsize=(10, 5))
        labels = list(funnel_data.keys())
        values = list(funnel_data.values())
        bars = ax.bar(
            labels,
            values,
            color=["#1a3a6b", "#2d5f8a", "#4a87b0", "#2d8659", "#c69c2f", "#d4a84a"][: len(labels)],
        )
        for bar, val in zip(bars, values, strict=False):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 10,
                f"{val:,}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
            )
        ax.set_title("Data Pipeline Funnel", fontsize=14, fontweight="bold")
        ax.set_ylabel("Example Count")
        ax.grid(axis="y", alpha=0.3)
        _save_fig(fig, output_dir / f"data_pipeline_funnel.{fmt}", fmt)


# ── Mobile Benchmark Latency ─────────────────────────────────────────


def plot_benchmark(metrics_dir: Path, output_dir: Path, fmt: str = "png"):
    """Per-stage latency breakdown for mobile inference."""
    _ensure_mpl()
    import matplotlib.pyplot as plt

    data = _load_json(metrics_dir / "mobile_benchmark.json")
    if not data:
        return

    stages = ["asr", "mt_input", "llm", "mt_output", "tts"]
    stage_labels = ["ASR", "MT (in)", "LLM", "MT (out)", "TTS"]
    p50 = [data.get(f"{s}_p50_ms", data.get(f"{s}_latency_ms", 0)) for s in stages]
    p95 = [data.get(f"{s}_p95_ms", 0) for s in stages]

    if not any(p50):
        return

    fig, ax = plt.subplots(figsize=(9, 5))
    import numpy as np

    x = np.arange(len(stage_labels))
    w = 0.35

    ax.bar(x - w / 2, p50, w, label="P50", color="#1a3a6b")
    if any(p95):
        ax.bar(x + w / 2, p95, w, label="P95", color="#c69c2f")

    ax.set_xlabel("Pipeline Stage")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Mobile Inference Latency Budget", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(stage_labels)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # Total budget line.
    total = sum(p50)
    ax.axhline(y=5000, color="red", linestyle="--", alpha=0.5, label="5s budget")
    ax.text(
        len(stages) - 0.5,
        total + 50,
        f"Total P50: {total:.0f}ms",
        fontsize=10,
        ha="right",
        color="#1a3a6b",
        fontweight="bold",
    )

    fig.tight_layout()
    _save_fig(fig, output_dir / f"mobile_latency_breakdown.{fmt}", fmt)


# ── Quality Gate Dashboard ───────────────────────────────────────────


def plot_quality_gates(metrics_dir: Path, output_dir: Path, fmt: str = "png"):
    """Pass/fail heatmap for all quality gate families."""
    _ensure_mpl()
    import matplotlib.pyplot as plt
    import numpy as np

    # Scan for gate results.
    gate_files = sorted(metrics_dir.glob("*gates*.json")) + sorted(
        metrics_dir.glob("*quality*.json")
    )
    if not gate_files:
        return

    families = {}
    for gf in gate_files:
        data = _load_json(gf)
        if not data or "checks" not in data:
            continue
        family = data.get("family", gf.stem)
        families[family] = data["checks"]

    if not families:
        return

    # Build heatmap.
    all_checks = set()
    for checks in families.values():
        all_checks.update(checks.keys())

    check_names = sorted(all_checks)
    family_names = sorted(families.keys())

    matrix = np.full((len(family_names), len(check_names)), 0.5)  # unknown = gray
    for i, fam in enumerate(family_names):
        for j, chk in enumerate(check_names):
            if chk in families[fam]:
                matrix[i, j] = 1.0 if families[fam][chk].get("passed", False) else 0.0

    fig, ax = plt.subplots(
        figsize=(max(10, len(check_names) * 0.8), max(4, len(family_names) * 0.8))
    )
    cmap = plt.cm.RdYlGn
    ax.imshow(matrix, cmap=cmap, aspect="auto", vmin=0, vmax=1)

    ax.set_xticks(range(len(check_names)))
    ax.set_yticks(range(len(family_names)))
    ax.set_xticklabels(check_names, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(family_names, fontsize=10)
    ax.set_title("Quality Gate Dashboard", fontsize=14, fontweight="bold")

    # Add pass/fail text.
    for i in range(len(family_names)):
        for j in range(len(check_names)):
            val = matrix[i, j]
            text = "PASS" if val > 0.7 else "FAIL" if val < 0.3 else "N/A"
            ax.text(
                j,
                i,
                text,
                ha="center",
                va="center",
                fontsize=7,
                color="white" if val < 0.3 else "black",
            )

    fig.tight_layout()
    _save_fig(fig, output_dir / f"quality_gate_dashboard.{fmt}", fmt)


# ── Generate All ─────────────────────────────────────────────────────


def generate_all(
    metrics_dir: Path = Path("Results/metrics"),
    output_dir: Path = Path("Results/plots"),
    fmt: str = "png",
):
    """Generate all available plots from existing metrics."""
    _ensure_mpl()
    output_dir.mkdir(parents=True, exist_ok=True)

    generators = [
        ("calibration", plot_calibration),
        ("speech", plot_speech),
        ("mt", plot_mt),
        ("rag", plot_rag),
        ("data_quality", plot_data_quality),
        ("benchmark", plot_benchmark),
        ("quality_gates", plot_quality_gates),
    ]

    generated = 0
    for name, func in generators:
        try:
            func(metrics_dir, output_dir, fmt)
            generated += 1
        except Exception as e:
            log.warning("plot %s failed: %s", name, e)

    log.info("generated %d plot families → %s", generated, output_dir)
    return generated


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate ML evaluation plots")
    parser.add_argument("--metrics-dir", type=Path, default=Path("Results/metrics"))
    parser.add_argument("--output-dir", type=Path, default=Path("Results/plots"))
    parser.add_argument("--format", default="png", choices=["png", "svg", "pdf"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    generate_all(args.metrics_dir, args.output_dir, args.format)


if __name__ == "__main__":
    main()
