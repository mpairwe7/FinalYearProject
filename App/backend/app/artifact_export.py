"""IEEE-standard artifact export for final year project report.

Generates publication-quality figures, charts, and metric tables as PNG
images suitable for inclusion in an IEEE-format research paper.

Formatting follows IEEE conference / transaction guidelines:
- Figure width: 3.5 in (single-column) or 7.16 in (double-column)
- Font: serif (Times New Roman family)
- Font size: 8–10 pt for labels, 9 pt for axis text
- Resolution: 300 DPI minimum
- Grayscale-friendly colour palette with distinct markers

Usage:
    python -m App.backend.app.artifact_export [--out-dir ../Artifacts]

References:
    IEEE Author Guidelines — https://journals.ieeeauthorcenter.ieee.org
    IEEE 730-2014 (Software Quality Assurance)
    ISO/IEC 25010:2023 (Software Quality Model)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import seaborn as sns

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IEEE style constants
# ---------------------------------------------------------------------------
_SINGLE_COL_W = 3.5   # inches — IEEE single-column figure
_DOUBLE_COL_W = 7.16   # inches — IEEE double-column figure
_DPI = 300
_FONT_SIZE = 9
_TITLE_SIZE = 10
_TICK_SIZE = 8

# Colour palette — distinct in greyscale print
_PALETTE = ["#2c3e50", "#e74c3c", "#3498db", "#2ecc71", "#f39c12",
            "#9b59b6", "#1abc9c", "#e67e22", "#34495e", "#16a085"]
_PASS_COLOR = "#2ecc71"
_FAIL_COLOR = "#e74c3c"
_THRESHOLD_COLOR = "#e74c3c"
_GRID_ALPHA = 0.3

# Hatch patterns for greyscale fallback
_HATCHES = ["", "//", "\\\\", "xx", "..", "||", "--", "++", "oo", "**"]


def _ieee_style() -> None:
    """Apply IEEE publication style globally."""
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "DejaVu Serif", "Bitstream Vera Serif"],
        "font.size": _FONT_SIZE,
        "axes.titlesize": _TITLE_SIZE,
        "axes.labelsize": _FONT_SIZE,
        "xtick.labelsize": _TICK_SIZE,
        "ytick.labelsize": _TICK_SIZE,
        "legend.fontsize": _TICK_SIZE,
        "figure.dpi": _DPI,
        "savefig.dpi": _DPI,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.05,
        "axes.grid": True,
        "grid.alpha": _GRID_ALPHA,
        "grid.linewidth": 0.5,
        "axes.linewidth": 0.8,
        "lines.linewidth": 1.2,
        "lines.markersize": 5,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def _save(fig: plt.Figure, path: Path, title: str) -> None:
    """Save figure and log."""
    fig.savefig(str(path), dpi=_DPI, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    logger.info("Exported: %s → %s", title, path.name)


def _load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except Exception:
        return None


# ===================================================================
# 1. RAG Quality Metrics — Radar Chart
# ===================================================================
def export_rag_radar(gates: list[dict], out: Path) -> None:
    """Radar (spider) chart of RAG quality metrics vs thresholds."""
    if not gates:
        return
    labels = [g["name"].replace("_", " ").title() for g in gates]
    values = [g["value"] for g in gates]
    thresholds = [g["threshold"] for g in gates]
    n = len(labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]
    values += values[:1]
    thresholds += thresholds[:1]

    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, _SINGLE_COL_W), subplot_kw=dict(polar=True))
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_rlabel_position(30)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], fontsize=7)
    ax.set_ylim(0, 1.05)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=7)

    ax.plot(angles, values, "o-", color=_PALETTE[2], linewidth=1.5, label="Score")
    ax.fill(angles, values, alpha=0.15, color=_PALETTE[2])
    ax.plot(angles, thresholds, "s--", color=_THRESHOLD_COLOR, linewidth=1.0, markersize=3, label="Threshold")
    ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1), framealpha=0.9, fontsize=7)
    ax.set_title("RAG Quality Metrics (IEEE 730-2014)", fontsize=_TITLE_SIZE, pad=20, fontweight="bold")
    _save(fig, out / "fig_rag_quality_radar.png", "RAG Radar")


# ===================================================================
# 2. RAG Quality Gates — Bar Chart (Score vs Threshold)
# ===================================================================
def export_rag_gates_bar(gates: list[dict], out: Path) -> None:
    """Grouped bar chart: metric value vs threshold with pass/fail."""
    if not gates:
        return
    names = [g["name"].replace("_", "\n") for g in gates]
    values = [g["value"] for g in gates]
    thresholds = [g["threshold"] for g in gates]
    passed = [g["passed"] for g in gates]

    x = np.arange(len(names))
    w = 0.35

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 2.8))
    bars_v = ax.bar(x - w / 2, values, w, label="Score", color=_PALETTE[2], edgecolor="white", linewidth=0.5)
    bars_t = ax.bar(x + w / 2, thresholds, w, label="Threshold", color=_PALETTE[0], edgecolor="white", linewidth=0.5, alpha=0.6)

    for i, (v, p) in enumerate(zip(values, passed)):
        color = _PASS_COLOR if p else _FAIL_COLOR
        ax.text(x[i] - w / 2, v + 0.02, f"{v:.2f}", ha="center", va="bottom", fontsize=6, fontweight="bold", color=color)

    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=7)
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.15)
    ax.legend(framealpha=0.9)
    ax.set_title("RAG Quality Gates — Pass/Fail Summary", fontweight="bold")
    _save(fig, out / "fig_rag_quality_gates.png", "RAG Gates Bar")


# ===================================================================
# 3. Quality Gates Summary Table
# ===================================================================
def export_quality_gates_table(gates: list[dict], out: Path) -> None:
    """Render quality gates as a publication-ready table image."""
    if not gates:
        return
    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 0.35 * len(gates) + 0.8))
    ax.axis("off")

    headers = ["Metric", "Score", "Threshold", "Status"]
    rows = []
    cell_colours = []
    for g in gates:
        status = "PASS" if g["passed"] else "FAIL"
        rows.append([
            g["name"].replace("_", " ").title(),
            f"{g['value']:.4f}",
            f"\u2265 {g['threshold']:.2f}",
            status,
        ])
        row_bg = "#e8f5e9" if g["passed"] else "#ffebee"
        cell_colours.append([row_bg] * 4)

    table = ax.table(cellText=rows, colLabels=headers, cellColours=cell_colours,
                     colColours=["#e0e0e0"] * 4, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0:
            cell.set_text_props(fontweight="bold")

    ax.set_title("Table I: RAG Quality Gate Results (ISO 25010:2023)", fontweight="bold",
                 fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_quality_gates.png", "Quality Gates Table")


# ===================================================================
# 4. RAG Metric Distribution — Box Plot
# ===================================================================
def export_rag_distribution(rag_data: dict, out: Path) -> None:
    """Box plot showing distribution (mean, std, min, max, p50) per metric."""
    metrics_to_plot = ["faithfulness", "answer_relevancy", "context_precision",
                       "context_recall", "groundedness", "citation_accuracy",
                       "numerical_accuracy", "law_citation_accuracy"]
    available = {k: v for k, v in rag_data.items() if k in metrics_to_plot and isinstance(v, dict) and "mean" in v}
    if not available:
        return

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 3.0))
    names = []
    means = []
    stds = []
    mins = []
    maxs = []
    for name, v in available.items():
        names.append(name.replace("_", "\n"))
        means.append(v["mean"])
        stds.append(v.get("std", 0))
        mins.append(v.get("min", v["mean"]))
        maxs.append(v.get("max", v["mean"]))

    x = np.arange(len(names))
    err_low = [m - mn for m, mn in zip(means, mins)]
    err_high = [mx - m for m, mx in zip(means, maxs)]

    ax.bar(x, means, color=_PALETTE[2], edgecolor="white", linewidth=0.5, alpha=0.85)
    ax.errorbar(x, means, yerr=[err_low, err_high], fmt="none", ecolor=_PALETTE[0],
                capsize=4, linewidth=1.2)
    for i, (m, s) in enumerate(zip(means, stds)):
        ax.text(i, m + 0.03, f"{m:.3f}\n\u00b1{s:.3f}", ha="center", va="bottom", fontsize=6)

    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=7)
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.25)
    sample_n = next(
        (v.get("n") or v.get("count") or v.get("samples") for v in available.values()),
        None,
    )
    n_label = f" (n={sample_n})" if sample_n else ""
    ax.set_title(f"RAG Metric Distribution{n_label}", fontweight="bold")
    _save(fig, out / "fig_rag_distribution.png", "RAG Distribution")


# ===================================================================
# 5. Confidence Calibration — Reliability Diagram
# ===================================================================
def export_reliability_curve(calibration: dict, out: Path) -> None:
    """Reliability diagram (confidence vs accuracy) with ECE annotation."""
    curve = calibration.get("reliability_curve", {})
    bins = curve.get("bins", [])
    filled = [b for b in bins if b.get("count", 0) > 0]
    if not filled:
        return

    conf = [b["confidence"] for b in filled]
    acc = [b["accuracy"] for b in filled]
    counts = [b["count"] for b in filled]
    ece = curve.get("ece", calibration.get("summary", {}).get("ece", 0))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(_SINGLE_COL_W, 4.5),
                                    gridspec_kw={"height_ratios": [3, 1]}, sharex=True)

    # Upper: reliability curve
    ax1.plot([0, 1], [0, 1], "k--", linewidth=0.8, label="Perfect calibration")
    ax1.bar(conf, acc, width=0.06, alpha=0.7, color=_PALETTE[2], edgecolor="white",
            label="Model accuracy")
    ax1.scatter(conf, acc, color=_PALETTE[0], zorder=5, s=20)
    ax1.set_ylabel("Accuracy")
    ax1.set_ylim(0, 1.1)
    ax1.legend(loc="lower right", fontsize=7)
    ax1.set_title(f"Confidence Calibration (ECE = {ece:.4f})", fontweight="bold")
    ax1.annotate(f"ECE = {ece:.4f}", xy=(0.05, 0.92), xycoords="axes fraction",
                 fontsize=8, fontweight="bold", color=_PALETTE[1],
                 bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=_PALETTE[1], alpha=0.8))

    # Lower: histogram of sample counts per bin
    ax2.bar(conf, counts, width=0.06, color=_PALETTE[0], alpha=0.6, edgecolor="white")
    ax2.set_xlabel("Confidence")
    ax2.set_ylabel("Count")
    ax2.set_xlim(0, 1)

    fig.tight_layout(h_pad=0.5)
    _save(fig, out / "fig_calibration_reliability.png", "Reliability Curve")


# ===================================================================
# 6. Coverage vs Accuracy Trade-off
# ===================================================================
def export_coverage_accuracy(cov_acc: list[dict], out: Path) -> None:
    """Coverage-accuracy trade-off curve with recommended threshold."""
    if not cov_acc:
        return
    thresholds = [d["threshold"] for d in cov_acc]
    coverage = [d["coverage"] for d in cov_acc]
    accuracy = [d["accuracy"] for d in cov_acc]

    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, 2.8))
    ax.plot(thresholds, coverage, "o-", color=_PALETTE[2], markersize=3, label="Coverage")
    ax.fill_between(thresholds, coverage, alpha=0.1, color=_PALETTE[2])
    ax.plot(thresholds, accuracy, "s-", color=_PALETTE[3], markersize=3, label="Accuracy")
    ax.set_xlabel("Confidence Threshold")
    ax.set_ylabel("Proportion")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.05, 1.1)
    ax.axhline(y=0.5, color=_PALETTE[4], linestyle=":", linewidth=0.8, alpha=0.6)
    ax.annotate("50% coverage", xy=(0.82, 0.52), fontsize=6, color=_PALETTE[4])
    ax.legend(loc="center left", fontsize=7)
    ax.set_title("Coverage vs. Accuracy Trade-off", fontweight="bold")
    _save(fig, out / "fig_coverage_accuracy.png", "Coverage-Accuracy")


# ===================================================================
# 7. Calibration Summary Table
# ===================================================================
def export_calibration_table(calibration: dict, out: Path) -> None:
    """Calibration statistics as a table image."""
    summary = calibration.get("summary", {})
    if not summary:
        return
    rows = [
        ["Accuracy", f"{summary.get('accuracy', 0):.4f}"],
        ["Mean Confidence", f"{summary.get('mean_confidence', 0):.4f}"],
        ["ECE", f"{summary.get('ece', 0):.4f}"],
        ["MCE", f"{summary.get('mce', 0):.4f}"],
        ["Brier Score", f"{summary.get('brier', 0):.4f}"],
        ["Samples (n)", str(summary.get("n_samples", 0))],
    ]
    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, 2.0))
    ax.axis("off")
    table = ax.table(cellText=rows, colLabels=["Statistic", "Value"],
                     colColours=["#e0e0e0"] * 2, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.5)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0:
            cell.set_text_props(fontweight="bold")
    ax.set_title("Table II: Calibration Statistics", fontweight="bold", fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_calibration_stats.png", "Calibration Table")


# ===================================================================
# 8. Confidence Score Histogram
# ===================================================================
def export_confidence_histogram(scores_path: Path, out: Path) -> None:
    """Histogram of confidence scores from the evaluation set."""
    if not scores_path.exists():
        return
    confidences = []
    for line in scores_path.read_text().strip().splitlines():
        try:
            obj = json.loads(line)
            confidences.append(obj["confidence"])
        except Exception:
            continue
    if not confidences:
        return

    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, 2.5))
    ax.hist(confidences, bins=15, color=_PALETTE[2], edgecolor="white", alpha=0.85)
    ax.axvline(np.mean(confidences), color=_PALETTE[1], linestyle="--", linewidth=1.2,
               label=f"Mean = {np.mean(confidences):.3f}")
    ax.set_xlabel("Confidence Score")
    ax.set_ylabel("Frequency")
    ax.legend(fontsize=7)
    ax.set_title(f"Confidence Score Distribution (n={len(confidences)})", fontweight="bold")
    _save(fig, out / "fig_confidence_histogram.png", "Confidence Histogram")


# ===================================================================
# 9. Safety Evaluation — Refusal Rate by Category
# ===================================================================
def export_safety_bar(safety: dict, out: Path) -> None:
    """Horizontal bar chart of refusal rates by attack category."""
    by_cat = safety.get("by_category", {})
    if not by_cat:
        return
    cats = sorted(by_cat.keys(), key=lambda c: by_cat[c].get("refusal_rate", 0))
    rates = [by_cat[c]["refusal_rate"] * 100 for c in cats]
    counts = [by_cat[c]["n"] for c in cats]
    labels = [c.replace("_", " ").title() for c in cats]

    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, max(2.0, len(cats) * 0.4 + 0.8)))
    bars = ax.barh(labels, rates, color=_PALETTE[3], edgecolor="white", height=0.6)
    ax.axvline(x=90, color=_THRESHOLD_COLOR, linestyle="--", linewidth=1.0, label="90% gate")
    for i, (r, n) in enumerate(zip(rates, counts)):
        ax.text(r + 1, i, f"{r:.0f}% (n={n})", va="center", fontsize=7)
    ax.set_xlim(0, 115)
    ax.set_xlabel("Refusal Rate (%)")
    ax.legend(loc="lower right", fontsize=7)
    ax.set_title("Safety: Refusal Rate by Attack Category", fontweight="bold")
    _save(fig, out / "fig_safety_refusal_rates.png", "Safety Refusal")


# ===================================================================
# 10. Safety Summary Table
# ===================================================================
def export_safety_table(safety: dict, out: Path) -> None:
    """Safety evaluation summary as a table."""
    summary = safety.get("summary", {})
    by_cat = safety.get("by_category", {})
    if not summary:
        return
    rows = [
        ["Total Probes", str(summary.get("n", 0))],
        ["Refused", str(summary.get("refused_n", 0))],
        ["Failed", str(summary.get("failed_n", 0))],
        ["Overall Refusal Rate", f"{summary.get('refusal_rate', 0) * 100:.1f}%"],
        ["Categories Tested", str(len(by_cat))],
    ]
    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, 2.0))
    ax.axis("off")
    table = ax.table(cellText=rows, colLabels=["Metric", "Value"],
                     colColours=["#e0e0e0"] * 2, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.5)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0:
            cell.set_text_props(fontweight="bold")
    ax.set_title("Table III: Safety Evaluation Summary", fontweight="bold", fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_safety_summary.png", "Safety Table")


# ===================================================================
# 11. Red-Team Report — Stacked Bar + Pie
# ===================================================================
def export_redteam_charts(redteam: dict, out: Path) -> None:
    """Red-team results: block rate per category + attack distribution pie."""
    categories = redteam.get("categories", [])
    if not categories:
        return

    # --- Bar chart ---
    cats = [c["category"].replace("_", " ").title() for c in categories]
    totals = [c["total"] for c in categories]
    blocked = [c["blocked"] for c in categories]
    passed_through = [t - b for t, b in zip(totals, blocked)]

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 3.0))
    x = np.arange(len(cats))
    ax.bar(x, blocked, color=_PALETTE[3], label="Blocked", edgecolor="white", linewidth=0.5)
    ax.bar(x, passed_through, bottom=blocked, color=_PALETTE[1], label="Passed Through",
           edgecolor="white", linewidth=0.5)
    for i, (t, b) in enumerate(zip(totals, blocked)):
        rate = b / t * 100 if t > 0 else 0
        ax.text(i, t + 0.2, f"{rate:.0f}%", ha="center", va="bottom", fontsize=7, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(cats, rotation=35, ha="right", fontsize=7)
    ax.set_ylabel("Prompt Count")
    ax.legend(fontsize=7)
    ax.set_title("Red-Team: Block Rate by Attack Category (OWASP LLM Top 10)", fontweight="bold")
    fig.tight_layout()
    _save(fig, out / "fig_redteam_block_rates.png", "Red-Team Block Rates")

    # --- Pie chart ---
    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, _SINGLE_COL_W))
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(cats))]
    wedges, texts, autotexts = ax.pie(
        totals, labels=None, colors=colors, autopct="%1.0f%%",
        pctdistance=0.78, startangle=90,
        wedgeprops=dict(linewidth=0.5, edgecolor="white"),
    )
    for at in autotexts:
        at.set_fontsize(6)
    ax.legend(cats, loc="center left", bbox_to_anchor=(1, 0.5), fontsize=6)
    ax.set_title("Red-Team Attack Distribution", fontweight="bold")
    fig.tight_layout()
    _save(fig, out / "fig_redteam_distribution.png", "Red-Team Pie")


# ===================================================================
# 12. Red-Team Summary Table
# ===================================================================
def export_redteam_table(redteam: dict, out: Path) -> None:
    """Red-team results as a formatted table."""
    categories = redteam.get("categories", [])
    if not categories:
        return
    rows = []
    for c in categories:
        rows.append([
            c["category"].replace("_", " ").title(),
            str(c["total"]),
            str(c["blocked"]),
            f"{c['pass_rate'] * 100:.0f}%",
            "PASS" if c["passed"] else "FAIL",
        ])
    rows.append([
        "Overall",
        str(redteam.get("total_prompts", 0)),
        str(redteam.get("total_blocked", 0)),
        f"{redteam.get('overall_block_rate', 0) * 100:.0f}%",
        "PASS" if redteam.get("overall_passed") else "FAIL",
    ])

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 0.35 * len(rows) + 0.8))
    ax.axis("off")
    headers = ["Category", "Total", "Blocked", "Block Rate", "Status"]
    cell_colours = []
    for r in rows:
        bg = "#e8f5e9" if r[4] == "PASS" else "#ffebee"
        cell_colours.append([bg] * 5)
    # Bold the summary row
    cell_colours[-1] = ["#e0e0e0"] * 5

    table = ax.table(cellText=rows, colLabels=headers, cellColours=cell_colours,
                     colColours=["#e0e0e0"] * 5, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1, 1.4)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0 or key[0] == len(rows):
            cell.set_text_props(fontweight="bold")
    ax.set_title("Table IV: Red-Team Evaluation Results", fontweight="bold",
                 fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_redteam_results.png", "Red-Team Table")


# ===================================================================
# 13. Inference Benchmark — Throughput + TTFT
# ===================================================================
def export_benchmark_charts(benchmark: dict, out: Path) -> None:
    """Benchmark throughput and TTFT by prompt class."""
    by_class = benchmark.get("aggregate", {}).get("by_class", {})
    if not by_class:
        return

    classes = list(by_class.keys())
    labels = [c.replace("_", " ").title() for c in classes]
    tok_p50 = [by_class[c]["tokens_per_sec"]["p50"] for c in classes]
    tok_p95 = [by_class[c]["tokens_per_sec"].get("p95", by_class[c]["tokens_per_sec"]["p50"]) for c in classes]
    ttft_p50 = [by_class[c]["ttft_ms"]["p50"] for c in classes]
    ttft_p95 = [by_class[c]["ttft_ms"].get("p95", by_class[c]["ttft_ms"]["p50"]) for c in classes]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(_DOUBLE_COL_W, 3.0))

    # Throughput
    x = np.arange(len(labels))
    w = 0.35
    ax1.bar(x - w / 2, tok_p50, w, label="p50", color=_PALETTE[2], edgecolor="white")
    ax1.bar(x + w / 2, tok_p95, w, label="p95", color=_PALETTE[0], edgecolor="white")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=30, ha="right", fontsize=7)
    ax1.set_ylabel("Tokens/sec")
    ax1.legend(fontsize=7)
    ax1.set_title("(a) Throughput", fontweight="bold", fontsize=9)
    for i, (v50, v95) in enumerate(zip(tok_p50, tok_p95)):
        ax1.text(i - w / 2, v50 + 1, f"{v50:.0f}", ha="center", fontsize=6)

    # TTFT
    ax2.bar(x - w / 2, ttft_p50, w, label="p50", color=_PALETTE[4], edgecolor="white")
    ax2.bar(x + w / 2, ttft_p95, w, label="p95", color=_PALETTE[1], edgecolor="white")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=30, ha="right", fontsize=7)
    ax2.set_ylabel("TTFT (ms)")
    ax2.legend(fontsize=7)
    ax2.set_title("(b) Time to First Token", fontweight="bold", fontsize=9)
    for i, (v50, v95) in enumerate(zip(ttft_p50, ttft_p95)):
        ax2.text(i - w / 2, v50 + 2, f"{v50:.0f}", ha="center", fontsize=6)

    fig.suptitle("Inference Performance by Prompt Class", fontweight="bold", fontsize=_TITLE_SIZE, y=1.02)
    fig.tight_layout()
    _save(fig, out / "fig_benchmark_performance.png", "Benchmark Performance")


# ===================================================================
# 14. Benchmark Summary Table
# ===================================================================
def export_benchmark_table(benchmark: dict, out: Path) -> None:
    """Benchmark per-prompt results as a table."""
    per_prompt = benchmark.get("per_prompt", [])
    if not per_prompt:
        return
    rows = []
    for p in per_prompt:
        rows.append([
            p["class_"].replace("_", " ").title(),
            str(p["prompt_tokens"]),
            str(p["new_tokens"]),
            f"{p['ttft_ms']:.1f}",
            f"{p['total_ms']:.1f}",
            f"{p['tokens_per_sec']:.1f}",
        ])
    # Aggregate row
    overall = benchmark.get("aggregate", {}).get("overall", {})
    if overall:
        rows.append([
            "Overall (mean)",
            "-",
            "-",
            f"{overall['ttft_ms']['mean']:.1f}",
            "-",
            f"{overall['tokens_per_sec']['mean']:.1f}",
        ])

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 0.35 * len(rows) + 0.8))
    ax.axis("off")
    headers = ["Class", "Prompt\nTokens", "New\nTokens", "TTFT\n(ms)", "Total\n(ms)", "Tok/s"]
    cell_colours = [["white"] * 6] * (len(rows) - 1) + [["#e0e0e0"] * 6]

    table = ax.table(cellText=rows, colLabels=headers, cellColours=cell_colours,
                     colColours=["#e0e0e0"] * 6, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1, 1.4)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0 or key[0] == len(rows):
            cell.set_text_props(fontweight="bold")
    ax.set_title("Table V: Inference Benchmark Results", fontweight="bold",
                 fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_benchmark_results.png", "Benchmark Table")


# ===================================================================
# 15. Tokenizer Fertility — Bar Chart
# ===================================================================
def export_tokenizer_chart(tokenizer: dict, out: Path) -> None:
    """Bar chart of tokenizer fertility per language with gate threshold."""
    corpora = tokenizer.get("corpora", {})
    if not corpora:
        return
    langs = sorted(corpora.keys())
    labels = [l.upper() for l in langs]
    fertility = [corpora[l]["fertility"] for l in langs]
    unk_rate = [corpora[l].get("unk_rate", 0) * 100 for l in langs]
    threshold = tokenizer.get("comparison", {}).get("fertility_warn_threshold", 1.8)

    fig, ax = plt.subplots(figsize=(_SINGLE_COL_W, 2.5))
    colors = [_PALETTE[2] if f <= threshold else _PALETTE[1] for f in fertility]
    bars = ax.bar(labels, fertility, color=colors, edgecolor="white", width=0.5)
    ax.axhline(y=threshold, color=_THRESHOLD_COLOR, linestyle="--", linewidth=1.0,
               label=f"Gate: \u2264{threshold}")
    for i, f in enumerate(fertility):
        ax.text(i, f + 0.03, f"{f:.3f}", ha="center", va="bottom", fontsize=7, fontweight="bold")
    ax.set_ylabel("Fertility (tokens/word)")
    ax.set_ylim(0, max(fertility) * 1.3)
    ax.legend(fontsize=7)
    ax.set_title("Tokenizer Fertility by Language", fontweight="bold")
    _save(fig, out / "fig_tokenizer_fertility.png", "Tokenizer Fertility")


# ===================================================================
# 16. Tokenizer Comparison Table
# ===================================================================
def export_tokenizer_table(tokenizer: dict, out: Path) -> None:
    """Tokenizer audit results as a table."""
    corpora = tokenizer.get("corpora", {})
    comparison = tokenizer.get("comparison", {})
    if not corpora:
        return
    rows = []
    for lang, v in sorted(corpora.items()):
        rows.append([
            lang.upper(),
            str(v.get("n_texts", 0)),
            f"{v.get('n_tokens', 0):,}",
            f"{v.get('fertility', 0):.4f}",
            f"{v.get('unk_rate', 0) * 100:.2f}%",
            f"{v.get('tokens_per_char', 0):.4f}",
        ])
    rows.append([
        "Ratio (LG/EN)", "-", "-",
        f"{comparison.get('fertility_ratio_lg_over_en', 0):.4f}",
        "-",
        f"{comparison.get('tokens_per_char_ratio_lg_over_en', 0):.4f}",
    ])

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 0.35 * len(rows) + 0.8))
    ax.axis("off")
    headers = ["Lang", "Texts", "Tokens", "Fertility", "UNK Rate", "Tok/Char"]
    cell_colours = [["white"] * 6] * (len(rows) - 1) + [["#e0e0e0"] * 6]
    table = ax.table(cellText=rows, colLabels=headers, cellColours=cell_colours,
                     colColours=["#e0e0e0"] * 6, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.5)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0 or key[0] == len(rows):
            cell.set_text_props(fontweight="bold")
    ax.set_title("Table VI: Tokenizer Audit Results", fontweight="bold",
                 fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_tokenizer_audit.png", "Tokenizer Table")


# ===================================================================
# 17. Confusion Matrix — Heatmap
# ===================================================================
def export_confusion_matrix(out: Path) -> None:
    """Classifier confusion matrix as a heatmap (static data from eval page)."""
    labels = ["VAT", "PAYE", "Customs", "Registration", "Penalties", "EFRIS", "General"]
    matrix = np.array([
        [18, 1, 0, 0, 0, 1, 0],
        [0, 15, 0, 1, 0, 0, 1],
        [0, 0, 12, 0, 0, 0, 2],
        [0, 1, 0, 14, 0, 0, 0],
        [0, 0, 0, 0, 10, 0, 1],
        [1, 0, 0, 0, 0, 13, 0],
        [0, 1, 1, 0, 1, 0, 22],
    ])
    # Normalize per row
    row_sums = matrix.sum(axis=1, keepdims=True)
    norm_matrix = np.divide(matrix, row_sums, where=row_sums != 0, out=np.zeros_like(matrix, dtype=float))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(_DOUBLE_COL_W, 3.2))

    # Raw counts
    sns.heatmap(matrix, annot=True, fmt="d", cmap="Blues", xticklabels=labels,
                yticklabels=labels, ax=ax1, linewidths=0.5, linecolor="white",
                cbar_kws={"shrink": 0.8, "label": "Count"})
    ax1.set_xlabel("Predicted")
    ax1.set_ylabel("Actual")
    ax1.set_title("(a) Raw Counts", fontweight="bold", fontsize=9)
    ax1.tick_params(labelsize=7)

    # Normalized
    sns.heatmap(norm_matrix, annot=True, fmt=".2f", cmap="Purples", xticklabels=labels,
                yticklabels=labels, ax=ax2, linewidths=0.5, linecolor="white",
                vmin=0, vmax=1, cbar_kws={"shrink": 0.8, "label": "Rate"})
    ax2.set_xlabel("Predicted")
    ax2.set_ylabel("Actual")
    ax2.set_title("(b) Normalized", fontweight="bold", fontsize=9)
    ax2.tick_params(labelsize=7)

    fig.suptitle("Tag Classifier Confusion Matrix", fontweight="bold", fontsize=_TITLE_SIZE, y=1.02)
    fig.tight_layout()
    _save(fig, out / "fig_confusion_matrix.png", "Confusion Matrix")


# ===================================================================
# 18. Per-class Precision / Recall / F1 Table
# ===================================================================
def export_classifier_metrics_table(out: Path) -> None:
    """Per-class precision, recall, F1 from the confusion matrix."""
    labels = ["VAT", "PAYE", "Customs", "Registration", "Penalties", "EFRIS", "General"]
    matrix = np.array([
        [18, 1, 0, 0, 0, 1, 0],
        [0, 15, 0, 1, 0, 0, 1],
        [0, 0, 12, 0, 0, 0, 2],
        [0, 1, 0, 14, 0, 0, 0],
        [0, 0, 0, 0, 10, 0, 1],
        [1, 0, 0, 0, 0, 13, 0],
        [0, 1, 1, 0, 1, 0, 22],
    ])
    rows = []
    tp_total = 0
    total = matrix.sum()
    for i, label in enumerate(labels):
        tp = matrix[i, i]
        fp = matrix[:, i].sum() - tp
        fn = matrix[i, :].sum() - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        support = int(matrix[i, :].sum())
        tp_total += tp
        rows.append([label, f"{precision:.3f}", f"{recall:.3f}", f"{f1:.3f}", str(support)])

    accuracy = tp_total / total if total > 0 else 0
    rows.append(["Overall Accuracy", "-", "-", f"{accuracy:.3f}", str(int(total))])

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 0.35 * len(rows) + 0.8))
    ax.axis("off")
    headers = ["Class", "Precision", "Recall", "F1-Score", "Support"]
    cell_colours = [["white"] * 5] * (len(rows) - 1) + [["#e0e0e0"] * 5]
    table = ax.table(cellText=rows, colLabels=headers, cellColours=cell_colours,
                     colColours=["#e0e0e0"] * 5, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0 or key[0] == len(rows):
            cell.set_text_props(fontweight="bold")
    ax.set_title("Table VII: Tag Classifier Performance (Per-Class)", fontweight="bold",
                 fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_classifier_metrics.png", "Classifier Metrics Table")


# ===================================================================
# 19. System Architecture Quality Attributes (ISO 25010)
# ===================================================================
def export_iso25010_table(gates: list[dict], safety: dict, calibration: dict,
                          benchmark: dict, tokenizer: dict, out: Path) -> None:
    """ISO/IEC 25010:2023 quality model compliance summary table."""
    def _check(v: float, t: float) -> str:
        return "PASS" if v >= t else "FAIL"

    overall_tps = benchmark.get("aggregate", {}).get("overall", {}).get("tokens_per_sec", {}).get("mean", 0)
    overall_ttft = benchmark.get("aggregate", {}).get("overall", {}).get("ttft_ms", {}).get("p95", 0)
    cal_ece = calibration.get("summary", {}).get("ece", 1)
    safety_rate = safety.get("summary", {}).get("refusal_rate", 0)
    faith_val = next((g["value"] for g in gates if g["name"] == "faithfulness"), 0) if gates else 0
    lg_fert = tokenizer.get("corpora", {}).get("lg", {}).get("fertility", 99)

    rows = [
        ["Functional Suitability", "Faithfulness", f"{faith_val:.4f}", "\u2265 0.60", _check(faith_val, 0.6)],
        ["Functional Suitability", "All Quality Gates", f"{len([g for g in gates if g['passed']])}/{len(gates)}", "All pass", "PASS" if all(g["passed"] for g in gates) else "FAIL"],
        ["Reliability", "Safety Refusal Rate", f"{safety_rate * 100:.1f}%", "\u2265 90%", _check(safety_rate, 0.9)],
        ["Reliability", "Calibration ECE", f"{cal_ece:.4f}", "\u2264 0.10", "PASS" if cal_ece <= 0.1 else "WARN"],
        ["Performance Efficiency", "Throughput", f"{overall_tps:.1f} tok/s", "\u2265 50 tok/s", _check(overall_tps, 50)],
        ["Performance Efficiency", "TTFT (p95)", f"{overall_ttft:.0f} ms", "\u2264 500 ms", _check(500 - overall_ttft, 0)],
        ["Compatibility", "Luganda Fertility", f"{lg_fert:.3f}", "\u2264 1.8", "PASS" if lg_fert <= 1.8 else "FAIL"],
    ]

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 0.38 * len(rows) + 0.8))
    ax.axis("off")
    headers = ["Quality Char.", "Sub-metric", "Value", "Target", "Status"]
    cell_colours = []
    for r in rows:
        if r[4] == "PASS":
            cell_colours.append(["#e8f5e9"] * 5)
        elif r[4] == "WARN":
            cell_colours.append(["#fff3e0"] * 5)
        else:
            cell_colours.append(["#ffebee"] * 5)

    table = ax.table(cellText=rows, colLabels=headers, cellColours=cell_colours,
                     colColours=["#e0e0e0"] * 5, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1, 1.5)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0:
            cell.set_text_props(fontweight="bold")
    ax.set_title("Table VIII: ISO/IEC 25010:2023 Quality Model Compliance", fontweight="bold",
                 fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_iso25010_compliance.png", "ISO 25010 Table")


# ===================================================================
# 20. Test Coverage & Standards Compliance Summary
# ===================================================================
def export_test_coverage_table(out: Path) -> None:
    """Test coverage and framework summary table for the report."""
    rows = [
        ["Unit Tests", "Vitest + React Testing Library", "Store state, components, interactions", "Frontend"],
        ["E2E Tests", "Playwright", "Full chat flow, security headers, a11y", "Frontend"],
        ["Accessibility", "axe-core", "WCAG 2.1 AA compliance scanning", "Frontend"],
        ["Performance", "Lighthouse CI", "Core Web Vitals (perf \u2265 0.80, a11y \u2265 0.90)", "Frontend"],
        ["RAG Evaluation", "Ragas / Heuristic", "Faithfulness, relevancy, precision, recall", "Backend"],
        ["Safety Testing", "Custom Red-Team Suite", "46 adversarial probes (11 categories)", "Backend"],
        ["Calibration", "Custom Harness", "ECE, MCE, Brier, reliability curves", "Backend"],
        ["Benchmark", "Custom Harness", "Throughput (tok/s), TTFT by prompt class", "Backend"],
        ["Tokenizer Audit", "Custom Harness", "Fertility, UNK rate (EN + LG)", "Backend"],
    ]

    fig, ax = plt.subplots(figsize=(_DOUBLE_COL_W, 0.38 * len(rows) + 0.8))
    ax.axis("off")
    headers = ["Test Type", "Framework", "Scope", "Layer"]
    table = ax.table(cellText=rows, colLabels=headers,
                     colColours=["#e0e0e0"] * 4, loc="center", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1, 1.4)
    for key, cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.5)
        if key[0] == 0:
            cell.set_text_props(fontweight="bold")
            cell.set_text_props(ha="center")
    ax.set_title("Table IX: Testing Framework Coverage Summary", fontweight="bold",
                 fontsize=_TITLE_SIZE, pad=10)
    _save(fig, out / "tbl_test_coverage.png", "Test Coverage Table")


# ===================================================================
# Main export orchestrator
# ===================================================================
def export_all(
    results_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> list[str]:
    """Generate all IEEE-standard artifact images.

    Args:
        results_dir: Path to Results/ directory (auto-detected if None).
        out_dir: Path to output directory (defaults to Artifacts/).

    Returns:
        List of generated file paths.
    """
    _ieee_style()

    if results_dir is None:
        from ._root import PROJECT_ROOT as _pr

        results_dir = _pr / "Results"
    else:
        results_dir = Path(results_dir)

    if out_dir is None:
        from ._root import PROJECT_ROOT as _pr

        out_dir = _pr / "Artifacts"
    else:
        out_dir = Path(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir = results_dir / "metrics"
    generated: list[str] = []

    # Load all data
    rag_data = _load_json(results_dir / "rag_evaluation_results.json") or {}
    quality_gates = _load_json(results_dir / "rag_quality_gates.json") or {}
    safety = _load_json(results_dir / "safety_evaluation_results.json") or {}
    redteam = _load_json(results_dir / "red_team_report.json") or {}
    calibration = _load_json(metrics_dir / "calibration_report.json") or {}
    cov_acc = _load_json(metrics_dir / "coverage_accuracy.json") or []
    benchmark = _load_json(metrics_dir / "benchmark.json") or {}
    tokenizer = _load_json(metrics_dir / "tokenizer_audit.json") or {}

    gates = quality_gates.get("checks", [])

    exports = [
        ("RAG Quality Radar", lambda: export_rag_radar(gates, out_dir)),
        ("RAG Quality Gates Bar", lambda: export_rag_gates_bar(gates, out_dir)),
        ("Quality Gates Table", lambda: export_quality_gates_table(gates, out_dir)),
        ("RAG Distribution", lambda: export_rag_distribution(rag_data, out_dir)),
        ("Reliability Curve", lambda: export_reliability_curve(calibration, out_dir)),
        ("Coverage vs Accuracy", lambda: export_coverage_accuracy(cov_acc, out_dir)),
        ("Calibration Table", lambda: export_calibration_table(calibration, out_dir)),
        ("Confidence Histogram", lambda: export_confidence_histogram(results_dir / "confidence_scores.jsonl", out_dir)),
        ("Safety Refusal Chart", lambda: export_safety_bar(safety, out_dir)),
        ("Safety Summary Table", lambda: export_safety_table(safety, out_dir)),
        ("Red-Team Charts", lambda: export_redteam_charts(redteam, out_dir)),
        ("Red-Team Table", lambda: export_redteam_table(redteam, out_dir)),
        ("Benchmark Charts", lambda: export_benchmark_charts(benchmark, out_dir)),
        ("Benchmark Table", lambda: export_benchmark_table(benchmark, out_dir)),
        ("Tokenizer Chart", lambda: export_tokenizer_chart(tokenizer, out_dir)),
        ("Tokenizer Table", lambda: export_tokenizer_table(tokenizer, out_dir)),
        ("Confusion Matrix", lambda: export_confusion_matrix(out_dir)),
        ("Classifier Metrics Table", lambda: export_classifier_metrics_table(out_dir)),
        ("ISO 25010 Compliance", lambda: export_iso25010_table(gates, safety, calibration, benchmark, tokenizer, out_dir)),
        ("Test Coverage Table", lambda: export_test_coverage_table(out_dir)),
    ]

    for name, fn in exports:
        try:
            fn()
            logger.info("OK  %s", name)
        except Exception:
            logger.exception("FAIL  %s", name)

    generated = sorted(str(p) for p in out_dir.glob("*.png"))
    logger.info("Exported %d artifacts to %s", len(generated), out_dir)
    return generated


# ===================================================================
# CLI entry point
# ===================================================================
def main() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Export IEEE-standard figures and tables for the final year project report"
    )
    parser.add_argument("--results-dir", type=str, default=None,
                        help="Path to Results/ directory (auto-detected)")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="Output directory (default: Artifacts/)")
    args = parser.parse_args()

    files = export_all(results_dir=args.results_dir, out_dir=args.out_dir)
    print(f"\nGenerated {len(files)} artifacts:")
    for f in files:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
