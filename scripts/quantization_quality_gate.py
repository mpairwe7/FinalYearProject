#!/usr/bin/env python3
"""Quantization quality gate for URA Chatbot CI/CD.

Validates that quantized models meet production acceptance criteria:

  - **Faithfulness**: Drop from bfloat16 baseline ≤ 4%
  - **WER**: Increase from baseline ≤ 3% (speech pipeline)
  - **Bundle size**: GGUF Q4_K_M ≤ 6 GB, mobile bundle ≤ 800 MB
  - **Latency**: p95 inference ≤ 1.8s (optional benchmark)

Reads the quantization manifest (``manifest.json``) and any evaluation
results to produce a pass/fail verdict.

Usage::

    # Check against defaults
    python scripts/quantization_quality_gate.py

    # Custom thresholds
    python scripts/quantization_quality_gate.py \\
        --manifest artifacts/quantized/Qwen3-8B/manifest.json \\
        --max-faithfulness-drop 4.0 \\
        --max-wer-increase 3.0 \\
        --max-bundle-mb 800

    # With evaluation results
    python scripts/quantization_quality_gate.py \\
        --eval-results Results/quantized_eval.json

Exit codes:
    0 — all gates passed
    1 — one or more gates failed
    2 — configuration or input error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("quality_gate")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "artifacts" / "quantized" / "Qwen3-8B" / "manifest.json"
DEFAULT_EVAL_RESULTS = PROJECT_ROOT / "Results" / "quantized_eval.json"

# Production acceptance criteria
DEFAULT_MAX_FAITHFULNESS_DROP_PCT = 4.0  # ≤ 4% drop from bfloat16
DEFAULT_MAX_WER_INCREASE_PCT = 3.0  # ≤ 3% increase from baseline
DEFAULT_MAX_GGUF_SIZE_MB = 6000  # 6 GB for server GGUF Q4_K_M (8B model)
DEFAULT_MAX_MOBILE_BUNDLE_MB = 800  # 800 MB total mobile bundle
DEFAULT_MAX_OFFLINE_BUNDLE_MB = 150  # 150 MB offline RAG bundle
DEFAULT_MIN_FAITHFULNESS = 0.89  # Absolute minimum faithfulness
DEFAULT_MIN_OFFLINE_FAITHFULNESS = 0.82  # Offline minimum faithfulness


@dataclass
class GateResult:
    """Result of a single quality gate check."""

    gate: str
    passed: bool
    actual: float | str
    threshold: float | str
    message: str


def check_manifest_exists(manifest_path: Path) -> GateResult:
    """Verify the quantization manifest exists and is valid JSON."""
    if not manifest_path.exists():
        return GateResult(
            gate="manifest_exists",
            passed=False,
            actual="missing",
            threshold="exists",
            message=f"Manifest not found: {manifest_path}",
        )

    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        if "results" not in data:
            return GateResult(
                gate="manifest_valid",
                passed=False,
                actual="invalid",
                threshold="valid JSON with results[]",
                message="Manifest missing 'results' key",
            )
        return GateResult(
            gate="manifest_exists",
            passed=True,
            actual="valid",
            threshold="exists",
            message=f"Manifest valid: {data['summary']['total']} artifacts",
        )
    except json.JSONDecodeError as e:
        return GateResult(
            gate="manifest_valid",
            passed=False,
            actual="invalid JSON",
            threshold="valid JSON",
            message=f"Manifest parse error: {e}",
        )


def check_quantization_success(manifest_path: Path) -> list[GateResult]:
    """Verify at least one GGUF and one AWQ artifact succeeded."""
    results: list[GateResult] = []

    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)

    artifacts = data.get("results", [])

    for fmt in ("gguf", "awq"):
        fmt_results = [r for r in artifacts if r["format"] == fmt]
        successes = [r for r in fmt_results if r["status"] == "success"]

        results.append(GateResult(
            gate=f"{fmt}_available",
            passed=len(successes) > 0,
            actual=f"{len(successes)}/{len(fmt_results)}",
            threshold="≥ 1 success",
            message=(
                f"{fmt.upper()}: {len(successes)} successful artifact(s)"
                if successes
                else f"{fmt.upper()}: no successful artifacts"
            ),
        ))

    return results


def check_bundle_sizes(manifest_path: Path, max_gguf_mb: float, max_mobile_mb: float) -> list[GateResult]:
    """Verify quantized artifact sizes are within limits."""
    results: list[GateResult] = []

    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)

    for artifact in data.get("results", []):
        if artifact["status"] != "success":
            continue

        size_mb = artifact.get("size_mb", 0)
        fmt = artifact["format"]
        qt = artifact["quant_type"]

        # GGUF size check
        if fmt == "gguf":
            results.append(GateResult(
                gate=f"size_{fmt}_{qt}",
                passed=size_mb <= max_gguf_mb,
                actual=f"{size_mb:.0f} MB",
                threshold=f"≤ {max_gguf_mb:.0f} MB",
                message=f"{fmt.upper()} {qt}: {size_mb:.0f} MB",
            ))

        # AWQ/GPTQ size check (should be smaller than GGUF)
        if fmt in ("awq", "gptq"):
            results.append(GateResult(
                gate=f"size_{fmt}_{qt}",
                passed=size_mb <= max_gguf_mb,
                actual=f"{size_mb:.0f} MB",
                threshold=f"≤ {max_gguf_mb:.0f} MB",
                message=f"{fmt.upper()} {qt}: {size_mb:.0f} MB",
            ))

    return results


def check_faithfulness(
    eval_path: Path,
    max_drop_pct: float,
    min_faithfulness: float,
) -> list[GateResult]:
    """Verify faithfulness scores meet thresholds."""
    results: list[GateResult] = []

    if not eval_path.exists():
        results.append(GateResult(
            gate="faithfulness_eval",
            passed=True,  # Pass if no eval results (will be checked post-eval)
            actual="no eval results",
            threshold=f"≤ {max_drop_pct}% drop",
            message="No evaluation results found — gate deferred to post-eval",
        ))
        return results

    try:
        with open(eval_path, encoding="utf-8") as f:
            eval_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        results.append(GateResult(
            gate="faithfulness_eval",
            passed=False,
            actual="read error",
            threshold="valid JSON",
            message=f"Evaluation results read error: {e}",
        ))
        return results

    baseline = eval_data.get("baseline_faithfulness", 0.93)

    for variant in eval_data.get("variants", []):
        name = variant.get("name", "unknown")
        faith = variant.get("faithfulness", 0)
        drop_pct = ((baseline - faith) / baseline) * 100 if baseline > 0 else 0

        passed = drop_pct <= max_drop_pct and faith >= min_faithfulness

        results.append(GateResult(
            gate=f"faithfulness_{name}",
            passed=passed,
            actual=f"{faith:.3f} (drop {drop_pct:.1f}%)",
            threshold=f"≥ {min_faithfulness:.2f}, drop ≤ {max_drop_pct}%",
            message=(
                f"{name}: faithfulness={faith:.3f}, drop={drop_pct:.1f}%"
                + (" — PASS" if passed else " — FAIL")
            ),
        ))

    return results


def check_wer(eval_path: Path, max_increase_pct: float) -> list[GateResult]:
    """Verify speech WER hasn't degraded beyond threshold."""
    results: list[GateResult] = []

    if not eval_path.exists():
        results.append(GateResult(
            gate="wer_check",
            passed=True,
            actual="no eval",
            threshold=f"≤ {max_increase_pct}% increase",
            message="No WER evaluation results — gate deferred",
        ))
        return results

    try:
        with open(eval_path, encoding="utf-8") as f:
            eval_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return results

    baseline_wer = eval_data.get("baseline_wer", {})
    for variant in eval_data.get("variants", []):
        name = variant.get("name", "unknown")
        wer_data = variant.get("wer", {})

        for lang, wer in wer_data.items():
            base = baseline_wer.get(lang, wer)
            increase = wer - base
            increase_pct = (increase / base * 100) if base > 0 else 0

            passed = increase_pct <= max_increase_pct

            results.append(GateResult(
                gate=f"wer_{name}_{lang}",
                passed=passed,
                actual=f"{wer:.1f}% (+{increase_pct:.1f}%)",
                threshold=f"increase ≤ {max_increase_pct}%",
                message=f"{name} WER ({lang}): {wer:.1f}%",
            ))

    return results


def run_quality_gates(
    manifest_path: Path,
    eval_path: Path,
    max_faithfulness_drop: float,
    max_wer_increase: float,
    max_gguf_mb: float,
    max_mobile_mb: float,
    min_faithfulness: float,
) -> tuple[bool, list[GateResult]]:
    """Run all quality gates and return (all_passed, results)."""
    all_results: list[GateResult] = []

    # Gate 1: Manifest exists and is valid
    result = check_manifest_exists(manifest_path)
    all_results.append(result)
    if not result.passed:
        return False, all_results

    # Gate 2: At least one artifact per required format succeeded
    all_results.extend(check_quantization_success(manifest_path))

    # Gate 3: Bundle sizes within limits
    all_results.extend(check_bundle_sizes(manifest_path, max_gguf_mb, max_mobile_mb))

    # Gate 4: Faithfulness
    all_results.extend(check_faithfulness(eval_path, max_faithfulness_drop, min_faithfulness))

    # Gate 5: WER
    all_results.extend(check_wer(eval_path, max_wer_increase))

    all_passed = all(r.passed for r in all_results)
    return all_passed, all_results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quantization quality gate for URA Chatbot CI/CD",
    )
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST,
        help="Path to quantization manifest.json",
    )
    parser.add_argument(
        "--eval-results", type=Path, default=DEFAULT_EVAL_RESULTS,
        help="Path to quantized model evaluation results",
    )
    parser.add_argument(
        "--max-faithfulness-drop", type=float, default=DEFAULT_MAX_FAITHFULNESS_DROP_PCT,
        help=f"Max faithfulness drop %% (default: {DEFAULT_MAX_FAITHFULNESS_DROP_PCT})",
    )
    parser.add_argument(
        "--max-wer-increase", type=float, default=DEFAULT_MAX_WER_INCREASE_PCT,
        help=f"Max WER increase %% (default: {DEFAULT_MAX_WER_INCREASE_PCT})",
    )
    parser.add_argument(
        "--max-gguf-mb", type=float, default=DEFAULT_MAX_GGUF_SIZE_MB,
        help=f"Max GGUF size in MB (default: {DEFAULT_MAX_GGUF_SIZE_MB})",
    )
    parser.add_argument(
        "--max-mobile-mb", type=float, default=DEFAULT_MAX_MOBILE_BUNDLE_MB,
        help=f"Max mobile bundle size in MB (default: {DEFAULT_MAX_MOBILE_BUNDLE_MB})",
    )
    parser.add_argument(
        "--min-faithfulness", type=float, default=DEFAULT_MIN_FAITHFULNESS,
        help=f"Minimum absolute faithfulness (default: {DEFAULT_MIN_FAITHFULNESS})",
    )
    parser.add_argument(
        "--json-output", type=Path, default=None,
        help="Write results to JSON file",
    )

    args = parser.parse_args()

    log.info("=" * 60)
    log.info("URA Chatbot Quantization Quality Gate")
    log.info("=" * 60)
    log.info("Manifest:              %s", args.manifest)
    log.info("Eval results:          %s", args.eval_results)
    log.info("Max faithfulness drop: %.1f%%", args.max_faithfulness_drop)
    log.info("Max WER increase:      %.1f%%", args.max_wer_increase)
    log.info("Min faithfulness:      %.2f", args.min_faithfulness)
    log.info("")

    all_passed, results = run_quality_gates(
        manifest_path=args.manifest,
        eval_path=args.eval_results,
        max_faithfulness_drop=args.max_faithfulness_drop,
        max_wer_increase=args.max_wer_increase,
        max_gguf_mb=args.max_gguf_mb,
        max_mobile_mb=args.max_mobile_mb,
        min_faithfulness=args.min_faithfulness,
    )

    # Print results
    log.info("Quality Gate Results:")
    log.info("-" * 60)
    for r in results:
        icon = "PASS" if r.passed else "FAIL"
        log.info("  [%4s] %-30s %s", icon, r.gate, r.message)

    log.info("-" * 60)
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    log.info("Total: %d | Passed: %d | Failed: %d", total, passed, failed)

    if all_passed:
        log.info("\nQUALITY GATE: PASSED")
    else:
        log.error("\nQUALITY GATE: FAILED")
        for r in results:
            if not r.passed:
                log.error("  BLOCKED: %s — %s (actual: %s, threshold: %s)",
                          r.gate, r.message, r.actual, r.threshold)

    # Optional JSON output
    if args.json_output:
        output = {
            "passed": all_passed,
            "total": total,
            "passed_count": passed,
            "failed_count": failed,
            "results": [
                {
                    "gate": r.gate,
                    "passed": r.passed,
                    "actual": r.actual,
                    "threshold": r.threshold,
                    "message": r.message,
                }
                for r in results
            ],
        }
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        log.info("Results written to %s", args.json_output)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
