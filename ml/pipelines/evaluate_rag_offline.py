"""Offline RAG evaluation pipeline — nightly batch evaluation.

Runs against a golden QA evaluation set and computes RAGAS-style metrics:
- Faithfulness (token overlap grounding)
- Answer relevancy (semantic similarity)
- Context precision (relevant passages in top-k)
- Context recall (coverage of golden answer)
- MRR (Mean Reciprocal Rank)
- Hit@K (presence of correct passage)

Designed to run as a nightly CI job or cron task with regression gates.

Usage:
    python -m ml.pipelines.evaluate_rag_offline \\
        --eval-set Data/eval/rag_eval.jsonl \\
        --output Results/eval/rag_offline_report.json \\
        --fail-on-regression
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Quality gate thresholds (configurable)
DEFAULT_THRESHOLDS = {
    "faithfulness_min": 0.6,
    "answer_relevancy_min": 0.5,
    "context_precision_min": 0.4,
    "mrr_min": 0.5,
    "hit_at_k_min": 0.7,
}


def load_eval_set(path: Path) -> list[dict[str, Any]]:
    """Load JSONL evaluation set, normalising the field names.

    Canonical shape: ``{"query": ..., "expected_answer": ..., "relevant_sources": [...]}``.

    ``Data/eval/rag_eval.jsonl`` is shared with :mod:`ml.pipelines.evaluate_rag`
    and uses ``question``/``ground_truth`` instead, which used to raise
    ``KeyError: 'query'`` here — so this pipeline had never run against the
    repository's own evaluation set. Both spellings are accepted rather than
    migrating the file, because the other pipeline reads the original names.

    ``relevant_sources`` is the ground-truth relevance label. It is absent from
    the current set, and without it the ranking metrics (MRR, Hit@K, context
    precision) are structurally zero — :func:`check_regression` therefore
    refuses to gate on them rather than reporting a meaningless failure.
    """
    entries = []
    with open(path) as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            query = record.get("query") or record.get("question") or ""
            if not query:
                raise ValueError(f"{path}:{line_number}: entry has neither 'query' nor 'question'")
            record["query"] = query
            record.setdefault(
                "expected_answer", record.get("ground_truth") or record.get("answer") or ""
            )
            entries.append(record)
    labelled = sum(1 for e in entries if e.get("relevant_sources"))
    logger.info(
        "Loaded %d evaluation entries from %s (%d with relevance labels)",
        len(entries),
        path,
        labelled,
    )
    if not labelled:
        logger.warning(
            "No entry carries 'relevant_sources', so MRR / Hit@K / context precision "
            "cannot be measured. Add relevance labels to gate on retrieval ranking."
        )
    return entries


def compute_token_overlap(text_a: str, text_b: str) -> float:
    """Token-level overlap score between two texts."""
    import re

    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / max(len(tokens_a), len(tokens_b))


def evaluate_single(
    entry: dict[str, Any],
    service: Any,
) -> dict[str, Any]:
    """Evaluate a single query against the RAG pipeline."""
    query = entry["query"]
    expected = entry.get("expected_answer", "")
    relevant_sources = set(entry.get("relevant_sources", []))

    t0 = time.perf_counter()
    result = service.generate(message=query, top_k=4, locale="en")
    latency_ms = (time.perf_counter() - t0) * 1000

    reply = result.get("reply", "")
    sources = result.get("sources", [])
    faithfulness = result.get("faithfulness_score") or 0.0

    # Answer relevancy (token overlap with expected)
    answer_relevancy = compute_token_overlap(reply, expected) if expected else 0.0

    # Context precision (fraction of retrieved sources that are relevant)
    if sources and relevant_sources:
        precision = len(set(sources) & relevant_sources) / len(sources)
    else:
        precision = 0.0

    # Context recall (fraction of relevant sources retrieved)
    if relevant_sources:
        recall = len(set(sources) & relevant_sources) / len(relevant_sources)
    else:
        recall = 1.0 if not sources else 0.0

    # MRR (1/rank of first relevant source)
    mrr = 0.0
    for i, s in enumerate(sources, 1):
        if s in relevant_sources:
            mrr = 1.0 / i
            break

    # Hit@K
    hit_at_k = 1.0 if set(sources) & relevant_sources else 0.0

    return {
        "query": query,
        "reply": reply[:200],
        "faithfulness": faithfulness,
        "answer_relevancy": round(answer_relevancy, 4),
        "context_precision": round(precision, 4),
        "context_recall": round(recall, 4),
        "mrr": round(mrr, 4),
        "hit_at_k": round(hit_at_k, 4),
        "latency_ms": round(latency_ms, 1),
        "retrieval_mode": result.get("retrieval_mode", ""),
        "num_sources": len(sources),
    }


def run_evaluation(
    eval_set: list[dict[str, Any]],
    service: Any,
) -> dict[str, Any]:
    """Run full evaluation and compute aggregate metrics."""
    results = []
    for i, entry in enumerate(eval_set):
        logger.info("Evaluating %d/%d: %s", i + 1, len(eval_set), entry["query"][:60])
        try:
            r = evaluate_single(entry, service)
            results.append(r)
        except Exception:
            logger.exception("Failed to evaluate: %s", entry["query"][:60])
            results.append({"query": entry["query"], "error": True})

    # Aggregate metrics
    valid = [r for r in results if "error" not in r]
    if not valid:
        return {"error": "No valid evaluations", "results": results}

    aggregates = {
        "faithfulness_mean": round(statistics.mean(r["faithfulness"] for r in valid), 4),
        "answer_relevancy_mean": round(statistics.mean(r["answer_relevancy"] for r in valid), 4),
        "context_precision_mean": round(statistics.mean(r["context_precision"] for r in valid), 4),
        "context_recall_mean": round(statistics.mean(r["context_recall"] for r in valid), 4),
        "mrr_mean": round(statistics.mean(r["mrr"] for r in valid), 4),
        "hit_at_k_mean": round(statistics.mean(r["hit_at_k"] for r in valid), 4),
        "latency_mean_ms": round(statistics.mean(r["latency_ms"] for r in valid), 1),
        "latency_p95_ms": round(
            sorted(r["latency_ms"] for r in valid)[min(int(len(valid) * 0.95), len(valid) - 1)], 1
        ),
        "total_evaluated": len(valid),
        "total_errors": len(results) - len(valid),
    }

    return {"aggregates": aggregates, "results": results}


#: Metrics that are only meaningful when the evaluation set carries
#: ``relevant_sources``. Gating on them without labels would fail every run at
#: exactly 0.0 and say nothing about retrieval quality.
_LABEL_DEPENDENT_CHECKS = frozenset({"context_precision_mean", "mrr_mean", "hit_at_k_mean"})


def check_regression(
    aggregates: dict[str, float],
    thresholds: dict[str, float],
    *,
    has_relevance_labels: bool = True,
) -> list[str]:
    """Check if metrics pass quality gates. Returns list of failures."""
    failures = []
    checks = [
        ("faithfulness_mean", "faithfulness_min"),
        ("answer_relevancy_mean", "answer_relevancy_min"),
        ("context_precision_mean", "context_precision_min"),
        ("mrr_mean", "mrr_min"),
        ("hit_at_k_mean", "hit_at_k_min"),
    ]
    for metric_key, threshold_key in checks:
        if not has_relevance_labels and metric_key in _LABEL_DEPENDENT_CHECKS:
            logger.warning(
                "Skipping %s gate: evaluation set has no relevance labels", metric_key
            )
            continue
        actual = aggregates.get(metric_key, 0)
        threshold = thresholds.get(threshold_key, 0)
        if actual < threshold:
            failures.append(f"{metric_key}={actual:.4f} < {threshold_key}={threshold:.4f}")
    return failures


def main():
    parser = argparse.ArgumentParser(description="Offline RAG evaluation")
    parser.add_argument("--eval-set", type=Path, required=True, help="Path to JSONL eval set")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "Results" / "eval" / "rag_offline_report.json"
    )
    parser.add_argument(
        "--fail-on-regression", action="store_true", help="Exit non-zero if quality gates fail"
    )
    args = parser.parse_args()

    if not args.eval_set.exists():
        logger.error("Evaluation set not found: %s", args.eval_set)
        sys.exit(1)

    # Import service (requires backend dependencies)
    sys.path.insert(0, str(PROJECT_ROOT))
    from App.backend.app.service import ChatModel

    logger.info("Initialising ChatModel for offline evaluation...")
    service = ChatModel()

    eval_set = load_eval_set(args.eval_set)
    report = run_evaluation(eval_set, service)

    # Write report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Report written to %s", args.output)

    # Print summary
    if "aggregates" in report:
        agg = report["aggregates"]
        logger.info("=== Evaluation Summary ===")
        for k, v in agg.items():
            logger.info("  %s: %s", k, v)

        # Regression check
        if args.fail_on_regression:
            failures = check_regression(
                agg,
                DEFAULT_THRESHOLDS,
                has_relevance_labels=any(e.get("relevant_sources") for e in eval_set),
            )
            if failures:
                logger.error("QUALITY GATE FAILURES:")
                for f in failures:
                    logger.error("  %s", f)
                sys.exit(1)
            else:
                logger.info("All quality gates PASSED")


if __name__ == "__main__":
    main()
