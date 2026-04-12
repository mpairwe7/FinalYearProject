"""ASR evaluation pipeline (2026).

Computes speech-recognition quality signals on a held-out eval set:

    * **WER**           — Word Error Rate (via jiwer)
    * **CER**           — Character Error Rate
    * **RTF**           — Real-Time Factor (latency / audio duration)
    * **P50/P95 latency** — response-time percentiles
    * **Per-language slicing** — en vs lg metrics separately

Emits ``Results/metrics/speech_metrics.json`` in the same shape consumed by
``ml/pipelines/quality_gates.py`` so the existing CI pattern extends cleanly.

Eval set format (JSONL)::

    {"audio_path": "Data/speech/en/001.wav", "reference": "...", "language": "en"}
    {"audio_path": "Data/speech/lg/001.wav", "reference": "...", "language": "lg"}

Usage::

    python -m ml.pipelines.evaluate_speech
    python -m ml.pipelines.evaluate_speech --eval-set Data/speech/asr_eval_lg.jsonl
    python -m ml.pipelines.evaluate_speech --dry-run
    python -m ml.pipelines.evaluate_speech --asr-backend transformers

Commercial posture: this module has no CC-BY-NC dependencies. jiwer is Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("evaluate_speech")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
_WORD_RE = None  # lazy-loaded


def _normalise(text: str) -> str:
    """Canonical text normalisation for WER scoring."""
    import re

    t = text.lower()
    t = re.sub(r"[^\w\s']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _compute_wer(refs: list[str], hyps: list[str]) -> float:
    """Return WER; if jiwer is missing, fall back to a naive implementation."""
    try:
        import jiwer  # type: ignore

        return float(
            jiwer.wer(
                [_normalise(r) for r in refs],
                [_normalise(h) for h in hyps],
            )
        )
    except ImportError:
        # Naive Levenshtein / length — good enough for smoke tests.
        errors = 0
        words_total = 0
        for r, h in zip(refs, hyps):
            rw = _normalise(r).split()
            hw = _normalise(h).split()
            words_total += len(rw)
            errors += abs(len(rw) - len(hw))
            # token overlap approximation
            common = len(set(rw) & set(hw))
            errors += max(0, max(len(rw), len(hw)) - common - abs(len(rw) - len(hw)))
        return errors / max(words_total, 1)


def _compute_cer(refs: list[str], hyps: list[str]) -> float:
    try:
        import jiwer  # type: ignore

        return float(
            jiwer.cer(
                [_normalise(r) for r in refs],
                [_normalise(h) for h in hyps],
            )
        )
    except ImportError:
        total = 0
        errors = 0
        for r, h in zip(refs, hyps):
            total += len(r)
            errors += abs(len(r) - len(h))
        return errors / max(total, 1)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


@dataclass
class EvalRecord:
    audio_path: Path
    reference: str
    language: str


def _load_eval_set(path: Path) -> list[EvalRecord]:
    if not path.exists():
        log.warning("eval set missing: %s", path)
        return []
    records: list[EvalRecord] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("skip line %d: %s", lineno, exc)
                continue
            ap = rec.get("audio_path")
            ref = rec.get("reference") or rec.get("reference_text")
            lang = rec.get("language") or rec.get("lang") or "en"
            if not ap or not ref:
                log.warning("skip line %d: missing fields", lineno)
                continue
            records.append(
                EvalRecord(
                    audio_path=(PROJECT_ROOT / ap) if not Path(ap).is_absolute() else Path(ap),
                    reference=str(ref),
                    language=str(lang),
                )
            )
    return records


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class SliceMetrics:
    language: str
    n: int
    wer: float
    cer: float
    rtf_p50: float
    rtf_p95: float
    latency_p50_ms: float
    latency_p95_ms: float


@dataclass
class SpeechMetrics:
    n: int
    wer: float
    cer: float
    rtf_mean: float
    latency_p50_ms: float
    latency_p95_ms: float
    backend: str
    slices: list[SliceMetrics] = field(default_factory=list)
    errors: int = 0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return float(values[f])
    return float(values[f] + (values[c] - values[f]) * (k - f))


def _transcribe_one(transcriber, record: EvalRecord) -> Optional[dict[str, Any]]:
    try:
        result = transcriber.transcribe_file(record.audio_path, target_sr=16000)
    except Exception as exc:
        log.warning("transcribe failed for %s: %s", record.audio_path, exc)
        return None
    return {
        "audio_path": str(record.audio_path),
        "reference": record.reference,
        "language": record.language,
        "hypothesis": result.text,
        "latency_s": result.latency_s or 0.0,
        "duration_s": result.duration_s or 0.0,
        "rtf": result.rtf or 0.0,
        "backend": result.backend,
    }


def run_evaluation(
    *,
    eval_set: Path,
    backend: str = "auto",
    dry_run: bool = False,
) -> SpeechMetrics:
    records = _load_eval_set(eval_set)
    if not records:
        log.warning("no records in %s — producing empty metrics", eval_set)
        return SpeechMetrics(
            n=0,
            wer=0.0,
            cer=0.0,
            rtf_mean=0.0,
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            backend=backend,
            errors=0,
        )

    if dry_run:
        log.info("[dry-run] would evaluate %d records", len(records))
        return SpeechMetrics(
            n=len(records),
            wer=0.0,
            cer=0.0,
            rtf_mean=0.0,
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            backend="dry-run",
        )

    from ml.scripts.asr.infer_asr import AsrTranscriber  # local import keeps this module light

    transcriber = AsrTranscriber(backend=backend)

    rows: list[dict[str, Any]] = []
    errors = 0
    for rec in records:
        row = _transcribe_one(transcriber, rec)
        if row is None:
            errors += 1
            continue
        rows.append(row)

    if not rows:
        return SpeechMetrics(
            n=0,
            wer=0.0,
            cer=0.0,
            rtf_mean=0.0,
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            backend=backend,
            errors=errors,
        )

    all_refs = [r["reference"] for r in rows]
    all_hyps = [r["hypothesis"] for r in rows]
    all_rtf = [r["rtf"] for r in rows]
    all_lat_ms = [r["latency_s"] * 1000 for r in rows]

    metrics = SpeechMetrics(
        n=len(rows),
        wer=round(_compute_wer(all_refs, all_hyps), 4),
        cer=round(_compute_cer(all_refs, all_hyps), 4),
        rtf_mean=round(statistics.fmean(all_rtf) if all_rtf else 0.0, 4),
        latency_p50_ms=round(_percentile(all_lat_ms, 0.50), 2),
        latency_p95_ms=round(_percentile(all_lat_ms, 0.95), 2),
        backend=rows[0]["backend"],
        errors=errors,
    )

    # Per-language slices
    for lang in sorted({r["language"] for r in rows}):
        lang_rows = [r for r in rows if r["language"] == lang]
        if not lang_rows:
            continue
        metrics.slices.append(
            SliceMetrics(
                language=lang,
                n=len(lang_rows),
                wer=round(
                    _compute_wer([r["reference"] for r in lang_rows], [r["hypothesis"] for r in lang_rows]),
                    4,
                ),
                cer=round(
                    _compute_cer([r["reference"] for r in lang_rows], [r["hypothesis"] for r in lang_rows]),
                    4,
                ),
                rtf_p50=round(_percentile([r["rtf"] for r in lang_rows], 0.50), 4),
                rtf_p95=round(_percentile([r["rtf"] for r in lang_rows], 0.95), 4),
                latency_p50_ms=round(_percentile([r["latency_s"] * 1000 for r in lang_rows], 0.50), 2),
                latency_p95_ms=round(_percentile([r["latency_s"] * 1000 for r in lang_rows], 0.95), 2),
            )
        )
    return metrics


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _metrics_to_dict(m: SpeechMetrics) -> dict[str, Any]:
    # Shape consumed by quality_gates.check_speech_gates (and legacy consumers).
    return {
        "n": m.n,
        "test_wer": m.wer,
        "test_cer": m.cer,
        "rtf_mean": m.rtf_mean,
        "latency": {
            "p50_ms": m.latency_p50_ms,
            "p95_ms": m.latency_p95_ms,
        },
        "backend": m.backend,
        "errors": m.errors,
        "slices": {
            s.language: {
                "n": s.n,
                "wer": s.wer,
                "cer": s.cer,
                "rtf_p50": s.rtf_p50,
                "rtf_p95": s.rtf_p95,
                "latency_p50_ms": s.latency_p50_ms,
                "latency_p95_ms": s.latency_p95_ms,
            }
            for s in m.slices
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ASR evaluation (WER/CER/RTF)")
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=PROJECT_ROOT / "Data" / "speech" / "asr_eval_en.jsonl",
    )
    parser.add_argument(
        "--metrics-out",
        type=Path,
        default=PROJECT_ROOT / "Results" / "metrics" / "speech_metrics.json",
    )
    parser.add_argument(
        "--backend", choices=("auto", "sherpa", "transformers", "mock"), default="auto"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    metrics = run_evaluation(
        eval_set=args.eval_set,
        backend=args.backend,
        dry_run=args.dry_run,
    )
    payload = _metrics_to_dict(metrics)

    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log.info("metrics -> %s", args.metrics_out)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
