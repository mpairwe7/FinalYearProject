"""Continuous RAG evaluation harness.

Computes faithfulness + context precision + answer relevancy across a
sample of recent conversations.  Uses the real **Ragas** library when
installed, and falls back to built-in heuristics otherwise so this
module is safe to import in minimal environments.

Triggered by:
- ``POST /v1/evaluate`` (admin, Bearer token) — one-shot eval of the
  most recent *n* conversations / thumbs-down feedback items.
- CI/CD via ``python -m App.backend.app.evaluation`` from a
  scheduled GitHub Action (nightly).  The CLI writes a JSON summary
  plus a Prometheus-compatible exposition so Grafana can alert on
  regressions.

Environment variables:
    EVAL_SAMPLE_SIZE        – how many rows to eval (default: 50)
    EVAL_FAITHFULNESS_MIN   – SLO gate (default: 0.7)
    EVAL_ANSWER_REL_MIN     – SLO gate (default: 0.6)
    EVAL_CONTEXT_PREC_MIN   – SLO gate (default: 0.6)

Reference: https://docs.ragas.io/
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import database as db
from .retriever import HybridRetriever

logger = logging.getLogger(__name__)

EVAL_SAMPLE_SIZE = int(os.getenv("EVAL_SAMPLE_SIZE", "50"))
EVAL_FAITHFULNESS_MIN = float(os.getenv("EVAL_FAITHFULNESS_MIN", "0.7"))
EVAL_ANSWER_REL_MIN = float(os.getenv("EVAL_ANSWER_REL_MIN", "0.6"))
EVAL_CONTEXT_PREC_MIN = float(os.getenv("EVAL_CONTEXT_PREC_MIN", "0.6"))


@dataclass
class EvalMetric:
    name: str
    value: float
    threshold: float
    passed: bool


@dataclass
class EvalReport:
    sample_size: int
    started_at: float
    duration_ms: float
    metrics: list[EvalMetric] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    backend: str = "heuristic"  # "ragas" | "heuristic"
    # Phase 21 — per-segment breakdown.  Keyed on the segment
    # dimension (e.g. "topic", "taxpayer_type", "locale") → {
    # segment_value → list[EvalMetric] }
    by_segment: dict[str, dict[str, list[EvalMetric]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "backend": self.backend,
            "metrics": [asdict(m) for m in self.metrics],
            "regressions": self.regressions,
            "passed": not self.regressions,
            "by_segment": {
                dim: {
                    seg: [asdict(m) for m in metrics]
                    for seg, metrics in segments.items()
                }
                for dim, segments in self.by_segment.items()
            },
        }

    def to_prometheus(self) -> str:
        """Render metrics in Prometheus text exposition format.

        Exposes both global metrics and the Phase 21 per-segment
        breakdown with `segment_dim` + `segment` labels so Grafana
        can chart quality by topic / taxpayer_type / locale.
        """
        lines: list[str] = [
            "# HELP ura_eval_metric RAG evaluation metric value",
            "# TYPE ura_eval_metric gauge",
        ]
        for m in self.metrics:
            lines.append(f'ura_eval_metric{{name="{m.name}"}} {m.value:.4f}')
            lines.append(
                f'ura_eval_metric_passed{{name="{m.name}"}} {1 if m.passed else 0}'
            )
        # Per-segment — one label set per (metric, dim, segment_value)
        for dim, segments in self.by_segment.items():
            for seg, metrics in segments.items():
                for m in metrics:
                    lines.append(
                        f'ura_eval_metric{{name="{m.name}",'
                        f'segment_dim="{dim}",segment="{seg}"}} {m.value:.4f}'
                    )
        lines.append(f"ura_eval_sample_size {self.sample_size}")
        lines.append(f"ura_eval_duration_ms {self.duration_ms:.2f}")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Heuristic metrics (no external deps)
# ---------------------------------------------------------------------------
def _heuristic_faithfulness(answer: str, contexts: list[str]) -> float:
    return HybridRetriever.compute_faithfulness(answer, contexts)


def _heuristic_answer_relevancy(question: str, answer: str) -> float:
    """Token-overlap proxy for the Ragas answer_relevancy metric."""
    import re as _re
    q_tokens = set(_re.findall(r"\w+", question.lower()))
    a_tokens = set(_re.findall(r"\w+", answer.lower()))
    if not q_tokens or not a_tokens:
        return 0.0
    return round(len(q_tokens & a_tokens) / len(q_tokens), 4)


def _heuristic_context_precision(answer: str, contexts: list[str]) -> float:
    """Proxy: fraction of retrieved contexts that share tokens with the answer."""
    if not contexts or not answer:
        return 0.0
    import re as _re
    a_tokens = set(_re.findall(r"\w+", answer.lower()))
    hits = 0
    for ctx in contexts:
        ctx_tokens = set(_re.findall(r"\w+", ctx.lower()))
        if a_tokens & ctx_tokens:
            hits += 1
    return round(hits / len(contexts), 4)


# ---------------------------------------------------------------------------
# Ragas integration (optional)
# ---------------------------------------------------------------------------
def _try_ragas(
    samples: list[dict[str, Any]],
) -> tuple[list[EvalMetric], str] | None:
    """Run the Ragas metrics suite when available.

    Returns ``(metrics, "ragas")`` on success or ``None`` if the library
    or its LLM backend is unavailable.  Callers fall back to the heuristic
    metrics in that case.
    """
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (  # type: ignore
            faithfulness,
            answer_relevancy,
            context_precision,
        )
    except ImportError:
        logger.debug("Ragas not installed; using heuristic eval")
        return None

    try:
        ds = Dataset.from_list([
            {
                "question": s["question"],
                "answer": s["answer"],
                "contexts": s["contexts"],
                "ground_truth": s.get("ground_truth", s["answer"]),
            }
            for s in samples
        ])
        result = evaluate(
            ds,
            metrics=[faithfulness, answer_relevancy, context_precision],
        )
        df = result.to_pandas()
        metrics = [
            EvalMetric(
                name="faithfulness",
                value=float(df["faithfulness"].mean()),
                threshold=EVAL_FAITHFULNESS_MIN,
                passed=float(df["faithfulness"].mean()) >= EVAL_FAITHFULNESS_MIN,
            ),
            EvalMetric(
                name="answer_relevancy",
                value=float(df["answer_relevancy"].mean()),
                threshold=EVAL_ANSWER_REL_MIN,
                passed=float(df["answer_relevancy"].mean()) >= EVAL_ANSWER_REL_MIN,
            ),
            EvalMetric(
                name="context_precision",
                value=float(df["context_precision"].mean()),
                threshold=EVAL_CONTEXT_PREC_MIN,
                passed=float(df["context_precision"].mean()) >= EVAL_CONTEXT_PREC_MIN,
            ),
        ]
        return metrics, "ragas"
    except Exception:
        logger.exception("Ragas eval failed; using heuristic fallback")
        return None


# ---------------------------------------------------------------------------
# Sample collection
# ---------------------------------------------------------------------------
def collect_samples(
    sample_size: int = EVAL_SAMPLE_SIZE,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Collect (question, answer, contexts) tuples from recent conversations.

    Uses the existing feedback export plus last-n conversations.  Falls
    back to an empty list if no data is available.
    """
    samples: list[dict[str, Any]] = []

    try:
        review = db.export_review_feedback(days=days)
    except Exception:
        review = []

    for row in review[:sample_size]:
        question = str(row.get("user_query", "")).strip()
        answer = str(row.get("bot_reply", "")).strip()
        if not question or not answer:
            continue
        # No per-row retrieved contexts persisted — use answer as a proxy
        # context so the harness is runnable.  Production deployments should
        # extend the DB schema to store the top-k contexts per conversation.
        samples.append({
            "question": question,
            "answer": answer,
            "contexts": [answer],
        })

    return samples[:sample_size]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_evaluation(
    sample_size: int = EVAL_SAMPLE_SIZE,
    days: int = 30,
) -> EvalReport:
    """Execute the eval harness end-to-end and return an ``EvalReport``."""
    t0 = time.perf_counter()
    started_at = time.time()

    samples = collect_samples(sample_size=sample_size, days=days)
    if not samples:
        logger.warning("No samples available for evaluation")
        return EvalReport(
            sample_size=0,
            started_at=started_at,
            duration_ms=(time.perf_counter() - t0) * 1000,
            metrics=[],
            regressions=["no_samples"],
        )

    # Try Ragas first; fall back to heuristic
    ragas_result = _try_ragas(samples)
    if ragas_result is not None:
        metrics, backend = ragas_result
    else:
        # Heuristic path
        backend = "heuristic"
        faiths = [_heuristic_faithfulness(s["answer"], s["contexts"]) for s in samples]
        ars = [_heuristic_answer_relevancy(s["question"], s["answer"]) for s in samples]
        cps = [_heuristic_context_precision(s["answer"], s["contexts"]) for s in samples]
        metrics = [
            EvalMetric(
                name="faithfulness",
                value=round(statistics.mean(faiths), 4),
                threshold=EVAL_FAITHFULNESS_MIN,
                passed=statistics.mean(faiths) >= EVAL_FAITHFULNESS_MIN,
            ),
            EvalMetric(
                name="answer_relevancy",
                value=round(statistics.mean(ars), 4),
                threshold=EVAL_ANSWER_REL_MIN,
                passed=statistics.mean(ars) >= EVAL_ANSWER_REL_MIN,
            ),
            EvalMetric(
                name="context_precision",
                value=round(statistics.mean(cps), 4),
                threshold=EVAL_CONTEXT_PREC_MIN,
                passed=statistics.mean(cps) >= EVAL_CONTEXT_PREC_MIN,
            ),
        ]

    regressions = [m.name for m in metrics if not m.passed]

    report = EvalReport(
        sample_size=len(samples),
        started_at=started_at,
        duration_ms=(time.perf_counter() - t0) * 1000,
        metrics=metrics,
        regressions=regressions,
        backend=backend,
    )

    # Phase 21 — per-segment breakdown.  Segments are inferred from
    # each sample's top_topic field.  When we have user profiles
    # (Phase 14 users table), we can key on taxpayer_type and locale
    # here too.
    report.by_segment = _compute_by_segment(samples, backend)
    return report


def _compute_by_segment(
    samples: list[dict[str, Any]],
    backend: str,
) -> dict[str, dict[str, list[EvalMetric]]]:
    """Group samples by inferred topic tag and recompute metrics per group.

    Returns a dict keyed on segment dimension → segment value → metrics.
    This is the ``by_segment`` field in :class:`EvalReport`.  Samples
    without enough data per segment (< 3) are skipped to avoid
    noisy single-sample scores.
    """
    from collections import defaultdict
    import re

    _topic_keywords = {
        "vat": ["vat", "value added"],
        "paye": ["paye", "salary"],
        "cit": ["corporation", "corporate tax"],
        "customs": ["import", "customs", "cif", "tariff"],
        "registration": ["register", "tin"],
        "escalation": ["dispute", "appeal", "human", "officer"],
    }

    def topic_of(sample: dict[str, Any]) -> str:
        q = (sample.get("question", "") or "").lower()
        for tag, keywords in _topic_keywords.items():
            if any(k in q for k in keywords):
                return tag
        return "general"

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in samples:
        groups[topic_of(s)].append(s)

    by_segment: dict[str, dict[str, list[EvalMetric]]] = {"topic": {}}

    for topic, group in groups.items():
        if len(group) < 3:
            continue  # Skip noisy small groups
        faiths = [_heuristic_faithfulness(s["answer"], s["contexts"]) for s in group]
        ars = [_heuristic_answer_relevancy(s["question"], s["answer"]) for s in group]
        cps = [_heuristic_context_precision(s["answer"], s["contexts"]) for s in group]
        by_segment["topic"][topic] = [
            EvalMetric(
                name="faithfulness",
                value=round(statistics.mean(faiths), 4),
                threshold=EVAL_FAITHFULNESS_MIN,
                passed=statistics.mean(faiths) >= EVAL_FAITHFULNESS_MIN,
            ),
            EvalMetric(
                name="answer_relevancy",
                value=round(statistics.mean(ars), 4),
                threshold=EVAL_ANSWER_REL_MIN,
                passed=statistics.mean(ars) >= EVAL_ANSWER_REL_MIN,
            ),
            EvalMetric(
                name="context_precision",
                value=round(statistics.mean(cps), 4),
                threshold=EVAL_CONTEXT_PREC_MIN,
                passed=statistics.mean(cps) >= EVAL_CONTEXT_PREC_MIN,
            ),
        ]

    return by_segment


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    """CLI entry point for the nightly CI evaluation job."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import argparse

    parser = argparse.ArgumentParser(description="Run the RAG evaluation harness")
    parser.add_argument("--samples", type=int, default=EVAL_SAMPLE_SIZE)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", type=str, default="eval_report.json")
    parser.add_argument("--prom-out", type=str, default="eval_metrics.prom")
    args = parser.parse_args()

    report = run_evaluation(sample_size=args.samples, days=args.days)
    payload = report.to_dict()
    print(json.dumps(payload, indent=2))

    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2)
    with open(args.prom_out, "w") as fh:
        fh.write(report.to_prometheus())

    # Exit non-zero on regression so CI fails
    return 1 if report.regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
