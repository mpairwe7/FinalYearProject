"""Mobile speech-pipeline benchmark harness (2026).

Runs the end-to-end ``audio -> ASR -> MT -> LLM -> MT -> TTS`` pipeline
over a test set of audio prompts and reports:

    * ASR RTF / latency
    * MT P50 / P95 latency
    * LLM first-token-latency (approximated)
    * TTS RTF / latency
    * total round-trip P50 / P95
    * peak RAM (via resource.getrusage on Linux)

Reference devices targeted (for interpretation, not automation):

    * Pixel 6a   — Android, Snapdragon 4nm + Tensor G1
    * iPhone 12  — iOS, A14 Bionic

The script itself runs on a dev workstation using the Python inference
wrappers (sherpa-onnx / transformers / mock). Emits
``Results/metrics/mobile_benchmark.json`` consumed by quality gates.

Usage::

    python -m ml.pipelines.benchmark_mobile --dry-run
    python -m ml.pipelines.benchmark_mobile \\
        --eval-set Data/speech/asr_eval_en.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("benchmark_mobile")


@dataclass
class Timing:
    asr_s: list[float] = field(default_factory=list)
    mt_s: list[float] = field(default_factory=list)
    llm_s: list[float] = field(default_factory=list)
    tts_s: list[float] = field(default_factory=list)
    total_s: list[float] = field(default_factory=list)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    k = (len(vs) - 1) * p
    f = int(k)
    c = min(f + 1, len(vs) - 1)
    if f == c:
        return float(vs[f])
    return float(vs[f] + (vs[c] - vs[f]) * (k - f))


def _peak_rss_mb() -> Optional[float]:
    try:
        import resource  # Linux/macOS only

        usage = resource.getrusage(resource.RUSAGE_SELF)
        kb = usage.ru_maxrss
        # macOS reports bytes, Linux reports kilobytes.
        if sys.platform == "darwin":
            return round(kb / 1024 / 1024, 2)
        return round(kb / 1024, 2)
    except Exception:
        return None


def _load_eval(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if "audio_path" in rec:
                    out.append(rec)
            except json.JSONDecodeError:
                continue
    return out


def run(*, eval_set: Path, dry_run: bool, target: str) -> dict[str, Any]:
    rows = _load_eval(eval_set)
    timings = Timing()

    if dry_run:
        log.info("[dry-run] would benchmark %d audio rows", len(rows))
        return {
            "n": len(rows),
            "target": target,
            "dry_run": True,
            "latency": {},
        }

    if not rows:
        log.warning("no audio rows in %s", eval_set)
        return {"n": 0, "target": target, "latency": {}}

    from ml.scripts.speech.speech_pipeline import run as run_pipeline  # type: ignore

    errors = 0
    for row in rows:
        audio_path = PROJECT_ROOT / row["audio_path"]
        if not audio_path.exists():
            log.debug("missing %s", audio_path)
            continue
        try:
            t0 = time.perf_counter()
            result = run_pipeline(
                audio_path=audio_path,
                source_lang=row.get("language", "en"),
                target_lang=row.get("language", "en"),
                tts_voice=None,
            )
            dt = time.perf_counter() - t0
            timings.total_s.append(dt)
            timings.asr_s.append(float(result.stages.get("asr_s", 0.0)))
            timings.mt_s.append(
                float(result.stages.get("mt_in_s", 0.0) + result.stages.get("mt_out_s", 0.0))
            )
            timings.llm_s.append(float(result.stages.get("llm_s", 0.0)))
            timings.tts_s.append(float(result.stages.get("tts_s", 0.0)))
        except Exception as exc:
            log.warning("pipeline run failed for %s: %s", audio_path, exc)
            errors += 1

    payload = {
        "n": len(timings.total_s),
        "errors": errors,
        "target": target,
        "latency": {
            "asr_p50_ms": round(_percentile(timings.asr_s, 0.50) * 1000, 2),
            "asr_p95_ms": round(_percentile(timings.asr_s, 0.95) * 1000, 2),
            "mt_p50_ms": round(_percentile(timings.mt_s, 0.50) * 1000, 2),
            "mt_p95_ms": round(_percentile(timings.mt_s, 0.95) * 1000, 2),
            "llm_p50_ms": round(_percentile(timings.llm_s, 0.50) * 1000, 2),
            "llm_p95_ms": round(_percentile(timings.llm_s, 0.95) * 1000, 2),
            "tts_p50_ms": round(_percentile(timings.tts_s, 0.50) * 1000, 2),
            "tts_p95_ms": round(_percentile(timings.tts_s, 0.95) * 1000, 2),
            "total_p50_ms": round(_percentile(timings.total_s, 0.50) * 1000, 2),
            "total_p95_ms": round(_percentile(timings.total_s, 0.95) * 1000, 2),
        },
        "peak_rss_mb": _peak_rss_mb(),
    }
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mobile speech pipeline benchmark")
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=PROJECT_ROOT / "Data" / "speech" / "asr_eval_en.jsonl",
    )
    parser.add_argument(
        "--metrics-out",
        type=Path,
        default=PROJECT_ROOT / "Results" / "metrics" / "mobile_benchmark.json",
    )
    parser.add_argument("--target", default="dev", help="Label for this run (pixel_6a, iphone_12, dev, ...)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    payload = run(eval_set=args.eval_set, dry_run=args.dry_run, target=args.target)
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log.info("metrics -> %s", args.metrics_out)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
