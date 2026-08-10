"""
Quality Gates Pipeline
Validates models meet minimum quality thresholds before deployment.

Supports four gate families:

    1. **classifier** (existing) — accuracy / f1 / precision / recall / latency
       Config key: ``quality_gates``
       Metric key: ``test_accuracy``, ``test_f1_macro``, ...

    2. **speech** (2026) — WER / CER / RTF / latency, per language
       Config key: ``speech_quality_gates``
       Metric key: ``test_wer``, ``test_cer``, ``slices.<lang>.wer`` ...

    3. **mt** (2026) — BLEU / chrF / length-ratio / hallucination rate
       Config key: ``mt_quality_gates``
       Metric key: ``bleu_en_lg``, ``chrf_lg_en`` ...

    4. **production** (2026) — the offline-mobile release gate. Aggregates
       RAG eval, calibration (ECE), safety (redteam refusal rate),
       tokenizer audit (Luganda fertility), GGUF benchmark (tokens/sec,
       TTFT, peak RSS), mobile size budget, and model card validation.
       Config key: ``production_gates``
       Activated with ``--family production`` and per-artefact path args.

The CLI auto-detects the metric shape (families 1-3) and dispatches to
the right family. The production family is explicit because it reads
from multiple artefact files rather than a single metrics JSON.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def check_quality_gates(metrics: dict, gates: dict) -> dict:
    """Check if classifier metrics meet quality gates."""
    results = {"passed": True, "checks": []}

    checks = [
        ("accuracy", "test_accuracy", gates.get("min_accuracy", 0.85)),
        ("f1_score", "test_f1_macro", gates.get("min_f1_score", 0.75)),
        ("precision", "test_precision", gates.get("min_precision", 0.75)),
        ("recall", "test_recall", gates.get("min_recall", 0.70)),
    ]

    for name, metric_key, threshold in checks:
        value = metrics.get(metric_key, 0)
        passed = value >= threshold

        results["checks"].append(
            {"name": name, "value": value, "threshold": threshold, "passed": passed}
        )

        if not passed:
            results["passed"] = False

    # Latency check (if available)
    if "latency" in metrics:
        latency = metrics["latency"].get("p95_ms", 0)
        max_latency = gates.get("max_latency_ms", 100)
        passed = latency <= max_latency

        results["checks"].append(
            {"name": "latency_p95", "value": latency, "threshold": max_latency, "passed": passed}
        )

        if not passed:
            results["passed"] = False

    return results


# ---------------------------------------------------------------------------
# Speech gates (2026)
# ---------------------------------------------------------------------------
def check_speech_gates(metrics: dict, gates: dict) -> dict:
    """Check if speech-recognition metrics meet quality gates.

    Matches the output shape of ``ml/pipelines/evaluate_speech.py``. Each
    check is a "lower is better" constraint because WER/CER/RTF/latency all
    measure error or cost, not goodness.
    """
    results = {"passed": True, "checks": []}

    def _ceil_check(name: str, value: float, threshold: float):
        passed = value <= threshold
        results["checks"].append(
            {
                "name": name,
                "value": value,
                "threshold": threshold,
                "comparator": "<=",
                "passed": passed,
            }
        )
        if not passed:
            results["passed"] = False

    _ceil_check("wer_overall", metrics.get("test_wer", 0), gates.get("max_wer_en", 0.15))
    _ceil_check("cer_overall", metrics.get("test_cer", 0), gates.get("max_cer_en", 0.07))
    _ceil_check("rtf_mean", metrics.get("rtf_mean", 0), gates.get("max_rtf", 0.5))

    latency = metrics.get("latency", {})
    if latency:
        _ceil_check(
            "latency_p50_ms",
            latency.get("p50_ms", 0),
            gates.get("max_p50_latency_ms", 800),
        )
        _ceil_check(
            "latency_p95_ms",
            latency.get("p95_ms", 0),
            gates.get("max_p95_latency_ms", 1500),
        )

    # Per-language slices
    for lang, slice_metrics in metrics.get("slices", {}).items():
        max_wer_key = f"max_wer_{lang}"
        max_cer_key = f"max_cer_{lang}"
        _ceil_check(
            f"wer_{lang}",
            slice_metrics.get("wer", 0),
            gates.get(max_wer_key, gates.get("max_wer_en", 0.25)),
        )
        _ceil_check(
            f"cer_{lang}",
            slice_metrics.get("cer", 0),
            gates.get(max_cer_key, gates.get("max_cer_en", 0.12)),
        )
    return results


# ---------------------------------------------------------------------------
# MT gates (2026)
# ---------------------------------------------------------------------------
def check_mt_gates(metrics: dict, gates: dict) -> dict:
    """Check if machine-translation metrics meet quality gates.

    Matches the output shape of ``ml/pipelines/evaluate_mt.py``. BLEU and
    chrF are "higher is better"; length-ratio deviation and hallucination
    rate are "lower is better".
    """
    results = {"passed": True, "checks": []}

    def _floor_check(name: str, value: float, threshold: float):
        passed = value >= threshold
        results["checks"].append(
            {
                "name": name,
                "value": value,
                "threshold": threshold,
                "comparator": ">=",
                "passed": passed,
            }
        )
        if not passed:
            results["passed"] = False

    def _ceil_check(name: str, value: float, threshold: float):
        passed = value <= threshold
        results["checks"].append(
            {
                "name": name,
                "value": value,
                "threshold": threshold,
                "comparator": "<=",
                "passed": passed,
            }
        )
        if not passed:
            results["passed"] = False

    directions = metrics.get("directions", {})
    for direction, d_metrics in directions.items():
        _floor_check(
            f"bleu_{direction}",
            d_metrics.get("bleu", 0),
            gates.get(f"min_bleu_{direction}", 15.0),
        )
        _floor_check(
            f"chrf_{direction}",
            d_metrics.get("chrf", 0),
            gates.get(f"min_chrf_{direction}", 0.35),
        )

    _ceil_check(
        "length_ratio_deviation",
        metrics.get("length_ratio_deviation", 0),
        gates.get("max_length_ratio_deviation", 0.30),
    )
    _ceil_check(
        "hallucination_rate",
        metrics.get("hallucination_rate", 0),
        gates.get("max_hallucination_rate", 0.05),
    )
    return results


# ---------------------------------------------------------------------------
# Production gates (2026) — offline mobile release
# ---------------------------------------------------------------------------
# Each check has a ``severity`` — ``blocking`` counts against the
# overall pass flag, ``advisory`` is recorded but does not fail the
# release. This lets us ratchet new gates in as advisory first, then
# promote them once the release history proves they are stable.
#
# Unlike the classical / speech / mt families, the production family
# consumes *several* JSON artefacts produced by disparate pipelines:
#
#     rag_evaluation_results.json  (evaluate_rag.py)
#     calibration_report.json      (calibrate.py)
#     tokenizer_audit.json         (audit_tokenizer.py)
#     benchmark.json               (benchmark_inference.py)
#     mobile_manifest.json         (export_mobile.py)
#     MODEL_CARD.md                (generate_model_card.py)
#     safety_evaluation_results.json (optional — redteam harness)


def _load_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _append_check(
    results: dict,
    *,
    name: str,
    value: Any,
    op: str,
    threshold: Any,
    severity: str = "blocking",
) -> None:
    """Evaluate one check and append it to ``results['checks']``."""
    passed = False
    note: str
    try:
        if value is None or threshold is None:
            # Missing data: advisory gates default-pass; blocking gates fail.
            passed = severity != "blocking"
            note = "missing_value_or_threshold"
        elif op == ">=":
            passed = float(value) >= float(threshold)
            note = "ok" if passed else "below_floor"
        elif op == "<=":
            passed = float(value) <= float(threshold)
            note = "ok" if passed else "above_ceiling"
        elif op == "==":
            passed = value == threshold
            note = "ok" if passed else "mismatch"
        else:
            raise ValueError(f"Unknown op: {op}")
    except (TypeError, ValueError) as exc:
        passed = False
        note = f"error:{exc}"

    results["checks"].append(
        {
            "name": name,
            "value": value,
            "comparator": op,
            "threshold": threshold,
            "severity": severity,
            "passed": passed,
            "note": note,
        }
    )
    if severity == "blocking" and not passed:
        results["passed"] = False


def _prod_check_rag(results: dict, rag_eval: dict | None, gates: dict) -> None:
    if not rag_eval:
        return

    def _mean(name: str) -> float | None:
        m = (rag_eval.get(name) or {}).get("mean")
        return float(m) if m is not None else None

    pairs = [
        ("faithfulness", "min_faithfulness", 0.60),
        ("answer_relevancy", "min_answer_relevancy", 0.70),
        ("context_precision", "min_context_precision", 0.50),
        ("context_recall", "min_context_recall", 0.50),
        ("groundedness", "min_groundedness", 0.40),
        ("citation_accuracy", "min_citation_accuracy", 0.40),
        ("numerical_accuracy", "min_numerical_accuracy", 0.70),
        ("law_citation_accuracy", "min_law_citation_accuracy", 0.50),
        ("abstention_precision", "min_abstention_precision", 0.50),
    ]
    for metric_name, gate_key, default in pairs:
        _append_check(
            results,
            name=metric_name,
            value=_mean(metric_name),
            op=">=",
            threshold=gates.get(gate_key, default),
        )


def _prod_check_per_language(results: dict, rag_eval: dict | None, gates: dict) -> None:
    """Luganda (and any other language) cannot silently regress vs a floor."""
    if not rag_eval:
        return
    slices = (rag_eval.get("slices") or {}).get("by_language") or {}
    floors: dict[str, dict[str, float]] = gates.get("per_language_floor") or {}
    default_floor: dict[str, float] = gates.get("per_language_default") or {}
    for lang, metrics in slices.items():
        floor = floors.get(lang) or default_floor
        if not floor:
            continue
        for metric_name in (
            "faithfulness",
            "numerical_accuracy",
            "law_citation_accuracy",
        ):
            if metric_name in floor:
                value = (metrics.get(metric_name) or {}).get("mean")
                _append_check(
                    results,
                    name=f"{lang}.{metric_name}",
                    value=value,
                    op=">=",
                    threshold=floor[metric_name],
                )


def _prod_check_calibration(results: dict, calibration: dict | None, gates: dict) -> None:
    if not calibration:
        return
    summary = calibration.get("summary") or {}
    _append_check(
        results,
        name="ece",
        value=summary.get("ece"),
        op="<=",
        threshold=gates.get("max_ece", 0.10),
    )
    _append_check(
        results,
        name="brier",
        value=summary.get("brier"),
        op="<=",
        threshold=gates.get("max_brier", 0.25),
    )
    rec = calibration.get("recommended_threshold") or {}
    _append_check(
        results,
        name="recommended_abstention_threshold_present",
        value=rec.get("threshold") is not None,
        op="==",
        threshold=True,
        severity="advisory",
    )


def _prod_check_safety(
    results: dict,
    safety: dict | None,
    rag_eval: dict | None,
    gates: dict,
) -> None:
    if safety:
        rate = (safety.get("summary") or safety).get("refusal_rate")
        _append_check(
            results,
            name="redteam_refusal_rate",
            value=rate,
            op=">=",
            threshold=gates.get("min_redteam_refusal_rate", 0.90),
        )
        cat_floor: dict[str, float] = gates.get("redteam_category_floor") or {}
        for cat, stats in (safety.get("by_category") or {}).items():
            floor = cat_floor.get(cat)
            if floor is not None:
                _append_check(
                    results,
                    name=f"redteam.{cat}",
                    value=stats.get("refusal_rate"),
                    op=">=",
                    threshold=floor,
                )
    elif rag_eval:
        # Fall back to the built-in guardrail probe rate from evaluate_rag.
        rate = (rag_eval.get("safety_probe_pass_rate") or {}).get("mean")
        _append_check(
            results,
            name="guardrail_probe_pass_rate",
            value=rate,
            op=">=",
            threshold=gates.get("min_safety_probe_pass_rate", 0.80),
        )


def _prod_check_tokenizer(results: dict, tok: dict | None, gates: dict) -> None:
    if not tok:
        return
    cmp_ = tok.get("comparison") or {}
    _append_check(
        results,
        name="tokenizer.lg_over_en_fertility",
        value=cmp_.get("fertility_ratio_lg_over_en"),
        op="<=",
        threshold=gates.get("max_lg_over_en_fertility", 1.8),
    )


def _prod_check_mobile(results: dict, mobile: dict | None, gates: dict) -> None:
    if not mobile:
        return
    _append_check(
        results,
        name="mobile.size_mb",
        value=mobile.get("size_mb"),
        op="<=",
        threshold=gates.get("max_mobile_size_mb", 1800),
    )
    _append_check(
        results,
        name="mobile.has_sha256",
        value=bool(mobile.get("sha256")),
        op="==",
        threshold=True,
    )


def _prod_check_benchmark(results: dict, benchmark: dict | None, gates: dict) -> None:
    if not benchmark:
        return
    # Synthetic runs: advisory-only. A real benchmark with real timings
    # can flip these to blocking by running without --synthetic.
    severity = "advisory" if benchmark.get("synthetic") else "blocking"
    agg = (benchmark.get("aggregate") or {}).get("overall") or {}
    tps = (agg.get("tokens_per_sec") or {}).get("mean")
    ttft = (agg.get("ttft_ms") or {}).get("p95")
    rss = (benchmark.get("run") or {}).get("peak_rss_mb")
    _append_check(
        results,
        name="benchmark.tokens_per_sec_mean",
        value=tps,
        op=">=",
        threshold=gates.get("min_tokens_per_sec", 8.0),
        severity=severity,
    )
    _append_check(
        results,
        name="benchmark.ttft_p95_ms",
        value=ttft,
        op="<=",
        threshold=gates.get("max_ttft_p95_ms", 1500),
        severity=severity,
    )
    _append_check(
        results,
        name="benchmark.peak_rss_mb",
        value=rss,
        op="<=",
        threshold=gates.get("max_peak_rss_mb", 2200),
        severity=severity,
    )


def _prod_check_model_card(results: dict, card_path: Path | None) -> None:
    if card_path is None or not Path(card_path).exists():
        return
    # Local import — the validator lives in the card generator module so
    # there is a single source of truth for "required sections".
    from ml.pipelines.generate_model_card import validate as validate_model_card

    v = validate_model_card(Path(card_path).read_text())
    check: dict[str, Any] = {
        "name": "model_card.required_sections_present",
        "value": v["passed"],
        "comparator": "==",
        "threshold": True,
        "severity": "blocking",
        "passed": bool(v["passed"]),
        "note": "ok" if v["passed"] else f"missing={v['missing']} empty={v['empty']}",
    }
    results["checks"].append(check)
    if not v["passed"]:
        results["passed"] = False


def check_production_gates(
    *,
    rag_eval: dict | None,
    safety: dict | None,
    calibration: dict | None,
    tokenizer_audit: dict | None,
    benchmark: dict | None,
    mobile_manifest: dict | None,
    model_card_path: Path | None,
    rag_gates: dict,
    prod_gates: dict,
) -> dict:
    """Evaluate every production gate and return a combined report.

    ``rag_gates`` feeds the RAG floor checks (re-using the existing
    ``rag_quality_gates`` yaml block). ``prod_gates`` feeds the new
    2026 checks (calibration, safety, tokenizer, mobile, benchmark,
    per-language floor) under the ``production_gates`` yaml block.
    """
    results: dict[str, Any] = {"passed": True, "checks": []}

    _prod_check_rag(results, rag_eval, rag_gates)
    _prod_check_per_language(results, rag_eval, prod_gates)
    _prod_check_calibration(results, calibration, prod_gates)
    _prod_check_safety(results, safety, rag_eval, prod_gates)
    _prod_check_tokenizer(results, tokenizer_audit, prod_gates)
    _prod_check_mobile(results, mobile_manifest, prod_gates)
    _prod_check_benchmark(results, benchmark, prod_gates)
    _prod_check_model_card(results, model_card_path)

    # Summary counters for convenient reporting.
    n_blocking = sum(1 for c in results["checks"] if c["severity"] == "blocking")
    n_blocking_failed = sum(
        1 for c in results["checks"] if c["severity"] == "blocking" and not c["passed"]
    )
    n_advisory = sum(1 for c in results["checks"] if c["severity"] == "advisory")
    n_advisory_failed = sum(
        1 for c in results["checks"] if c["severity"] == "advisory" and not c["passed"]
    )
    results["summary"] = {
        "blocking_total": n_blocking,
        "blocking_failed": n_blocking_failed,
        "advisory_total": n_advisory,
        "advisory_failed": n_advisory_failed,
    }
    return results


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
_GATE_DISPATCH = {
    "classifier": ("quality_gates", check_quality_gates),
    "speech": ("speech_quality_gates", check_speech_gates),
    "mt": ("mt_quality_gates", check_mt_gates),
    # 'production' is handled out-of-band in main() because it needs
    # multiple artefact paths, not just ``--metrics-path``.
}


def _autodetect_family(metrics: dict) -> str:
    """Heuristic: sniff the metric shape and return one of classifier|speech|mt."""
    if "test_wer" in metrics or "rtf_mean" in metrics:
        return "speech"
    if "directions" in metrics or "bleu_en_lg" in metrics:
        return "mt"
    return "classifier"


def _print_results(family: str, results: dict) -> None:
    print(f"\n{family.upper()} Quality Gate Results:")
    print("-" * 70)
    for check in results["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        if check.get("severity") == "advisory":
            status += " (adv)"
        comparator = check.get("comparator", ">=")
        value = check["value"]
        value_s = f"{value:>8.4f}" if isinstance(value, float) else f"{str(value):>8}"
        threshold = check["threshold"]
        thresh_s = f"{threshold:<8.4f}" if isinstance(threshold, float) else f"{str(threshold):<8}"
        print(f"  {check['name']:40} {value_s} {comparator} {thresh_s}  {status}")
    print("-" * 70)
    summary = results.get("summary")
    if summary:
        print(
            f"  blocking: "
            f"{summary['blocking_total'] - summary['blocking_failed']}/"
            f"{summary['blocking_total']} passed   "
            f"advisory: "
            f"{summary['advisory_total'] - summary['advisory_failed']}/"
            f"{summary['advisory_total']} passed"
        )


def main():
    parser = argparse.ArgumentParser(description="Check model quality gates")
    parser.add_argument("--metrics-path", type=str, default="Results/metrics/training_metrics.json")
    parser.add_argument("--config", type=str, default="ml/configs/training_config.yaml")
    parser.add_argument(
        "--family",
        choices=("auto", "classifier", "speech", "mt", "production"),
        default="auto",
        help="Which gate family to check (auto = sniff from metric shape)",
    )
    parser.add_argument(
        "--soft-fail",
        action="store_true",
        help="Exit 0 even when gates fail (useful for CI dry-runs before assets exist)",
    )

    # Production-family artefact paths (only used with --family production).
    prod = parser.add_argument_group("production family artefact paths")
    prod.add_argument(
        "--rag-eval", type=Path, default=None, help="Path to rag_evaluation_results.json"
    )
    prod.add_argument(
        "--safety", type=Path, default=None, help="Path to safety_evaluation_results.json"
    )
    prod.add_argument(
        "--calibration", type=Path, default=None, help="Path to calibration_report.json"
    )
    prod.add_argument(
        "--tokenizer-audit", type=Path, default=None, help="Path to tokenizer_audit.json"
    )
    prod.add_argument("--benchmark", type=Path, default=None, help="Path to benchmark.json")
    prod.add_argument(
        "--mobile-manifest", type=Path, default=None, help="Path to mobile_manifest.json"
    )
    prod.add_argument("--model-card", type=Path, default=None, help="Path to MODEL_CARD.md")

    args = parser.parse_args()

    print("=" * 60)
    print("QUALITY GATES CHECK")
    print("=" * 60)

    # Load config
    config = load_config(PROJECT_ROOT / args.config)

    # ---- Production family: multi-artefact path ----
    if args.family == "production":

        def _abs(p: Path | None) -> Path | None:
            if p is None:
                return None
            return p if p.is_absolute() else (PROJECT_ROOT / p)

        results = check_production_gates(
            rag_eval=_load_json(_abs(args.rag_eval)),
            safety=_load_json(_abs(args.safety)),
            calibration=_load_json(_abs(args.calibration)),
            tokenizer_audit=_load_json(_abs(args.tokenizer_audit)),
            benchmark=_load_json(_abs(args.benchmark)),
            mobile_manifest=_load_json(_abs(args.mobile_manifest)),
            model_card_path=_abs(args.model_card),
            rag_gates=config.get("rag_quality_gates", {}),
            prod_gates=config.get("production_gates", {}),
        )
        _print_results("production", results)
        if results["passed"]:
            print("ALL PRODUCTION GATES PASSED")
            sys.exit(0)
        else:
            print("PRODUCTION GATES FAILED")
            sys.exit(0 if args.soft_fail else 1)

    # ---- Classical families: single-metrics-file path ----
    metrics_path = PROJECT_ROOT / args.metrics_path
    if not metrics_path.exists():
        msg = f"Metrics file not found: {metrics_path}"
        if args.soft_fail:
            print(f"SKIP: {msg} (soft-fail)")
            sys.exit(0)
        print(f"ERROR: {msg}")
        sys.exit(1)

    with open(metrics_path) as f:
        metrics = json.load(f)

    # Resolve family + matching gate section
    family = args.family if args.family != "auto" else _autodetect_family(metrics)
    if family not in _GATE_DISPATCH:
        print(f"ERROR: unknown gate family {family!r}")
        sys.exit(1)
    gates_key, checker = _GATE_DISPATCH[family]
    gates = config.get(gates_key, {})
    if not gates:
        print(f"WARN: config has no section {gates_key!r} — using defaults")

    # Check gates
    results = checker(metrics, gates)

    # Print
    _print_results(family, results)

    if results["passed"]:
        print("ALL QUALITY GATES PASSED")
        sys.exit(0)
    else:
        print("QUALITY GATES FAILED")
        if args.soft_fail:
            sys.exit(0)
        sys.exit(1)


if __name__ == "__main__":
    main()
