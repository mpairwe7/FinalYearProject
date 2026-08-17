"""Corrective RAG — re-retrieve when initial retrieval quality is low.

Implements a score-and-decide loop:
1. Score retrieved passages against the query
2. If average relevance is below threshold, re-retrieve with:
   - Expanded query (add domain synonyms)
   - Relaxed filters
   - Higher top_k
3. Merge and deduplicate results

Environment variables:
    CORRECTIVE_RAG_ENABLED      – enable/disable (default: true)
    CORRECTIVE_RAG_THRESHOLD    – min avg reranker score (default: 0.3)
    CORRECTIVE_RAG_MAX_RETRIES  – max re-retrieval attempts (default: 1)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

CORRECTIVE_ENABLED = os.getenv("CORRECTIVE_RAG_ENABLED", "true").lower() == "true"
try:
    CORRECTIVE_THRESHOLD = float(os.getenv("CORRECTIVE_RAG_THRESHOLD", "0.3"))
except ValueError:
    CORRECTIVE_THRESHOLD = 0.3

# P1-5: corrective threshold on the normalized [0,1] reranker scale.
try:
    CORRECTIVE_THRESHOLD_NORM = float(os.getenv("CORRECTIVE_RAG_THRESHOLD_NORM", "0.50"))
except ValueError:
    CORRECTIVE_THRESHOLD_NORM = 0.50


def _avg_score(hits: list[dict[str, Any]]) -> float:
    """Average raw reranker/RRF score — **logging only**.

    Do not compare this across two retrieval calls: it falls back from an
    unbounded cross-encoder logit (roughly -10..10) to an RRF score
    (~1/(60+rank) ≈ 0.016), so an average over reranked hits and an
    average over RRF-only hits are on different scales entirely.  Use
    :func:`_ranking_key` and :func:`_improved` for decisions.
    """
    scores = [h.get("score_rerank", h.get("score_rrf", 0.0)) for h in hits]
    return sum(scores) / max(len(scores), 1)


def _ranking_key(hit: dict[str, Any]) -> tuple[int, float]:
    """Sort key that never compares a reranker logit against an RRF score.

    Returns ``(tier, score)``.  Reranked hits occupy tier 1 and sort by
    calibrated relevance; RRF-only hits occupy tier 0 and sort among
    themselves.  Merging two retrieval calls with a bare
    ``score_rerank`` -> ``score_rrf`` fallback put every RRF-only hit
    below every reranked hit *including* ones the cross-encoder had
    scored as irrelevant, because -4.0 < 0.016.
    """
    from .retriever import hit_relevance

    relevance = hit_relevance(hit)
    if relevance is not None:
        return (1, relevance)
    try:
        return (0, float(hit.get("score_rrf", 0.0)))
    except (TypeError, ValueError):
        return (0, 0.0)


def _improved(final: list[dict[str, Any]], initial: list[dict[str, Any]]) -> bool:
    """Whether *final* is better than *initial*, on a comparable scale.

    Prefers calibrated relevance.  When neither set has a reranker signal
    the comparison is not meaningful, so re-retrieval counts as an
    improvement only if it actually returned more evidence — which is the
    one thing that is comparable without a scorer.
    """
    final_rel, initial_rel = _avg_relevance(final), _avg_relevance(initial)
    if final_rel is not None and initial_rel is not None:
        return final_rel > initial_rel
    if final_rel is not None and initial_rel is None:
        return True  # gained a reranker signal where there was none
    return len(final) > len(initial)


def _avg_relevance(hits: list[dict[str, Any]]) -> float | None:
    """Average calibrated [0,1] relevance, or ``None`` when no reranker signal
    is available (RRF-only / keyword fallback) — see :func:`retriever.hit_relevance`."""
    from .retriever import hit_relevance

    scores = [r for h in hits if (r := hit_relevance(h)) is not None]
    if not scores:
        return None
    return sum(scores) / len(scores)


def _expand_query(query: str) -> str:
    """Simple query expansion for re-retrieval."""
    from .query import correct_spelling, expand_abbreviations

    expanded = expand_abbreviations(correct_spelling(query))
    # Add "Uganda Revenue Authority" context if not present
    if "ura" not in expanded.lower() and "uganda" not in expanded.lower():
        expanded = f"{expanded} Uganda Revenue Authority"
    return expanded


def should_correct(hits: list[dict[str, Any]]) -> bool:
    """Determine if corrective re-retrieval is needed (P1-5).

    Gates on the normalized [0,1] relevance.  When no reranker signal is
    available the relevance is incomparable, so we do not trigger correction on
    a raw RRF score (which would spuriously fire on nearly every query).
    """
    from .flags import flags

    if not CORRECTIVE_ENABLED or not flags.is_enabled("corrective_rag"):
        return False
    if not hits:
        return True
    avg = _avg_relevance(hits)
    if avg is None:
        return False
    return avg < CORRECTIVE_THRESHOLD_NORM


def corrective_retrieve(
    query: str,
    retriever: Any,
    initial_hits: list[dict[str, Any]],
    top_k: int = 4,
    filters: dict[str, Any] | None = None,
    subject: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Run corrective re-retrieval if initial results are poor.

    Returns (final_hits, was_corrected).
    """
    if not should_correct(initial_hits):
        return initial_hits, False

    logger.info(
        "Corrective RAG triggered: avg_score=%.3f < threshold=%.3f",
        _avg_score(initial_hits),
        CORRECTIVE_THRESHOLD,
    )

    expanded = _expand_query(query)
    search = getattr(retriever, "search_planned", None) or retriever.search
    if search is retriever.search:
        new_hits = retriever.search(
            expanded,
            top_k=top_k + 2,
            prefetch_limit=30,
            filters=filters,
            subject=subject,
        )
    else:
        new_hits = retriever.search_planned(
            expanded,
            top_k=top_k + 2,
            prefetch_limit=30,
            filters=filters,
            subject=subject,
        )

    if not new_hits:
        return initial_hits, False

    # Merge and deduplicate by chunk_id
    seen_ids: set[str] = set()
    merged: list[dict[str, Any]] = []

    for hit in new_hits + initial_hits:
        hit_id = hit.get("id") or hit.get("chunk_id") or hit.get("text", "")[:50]
        if hit_id not in seen_ids:
            seen_ids.add(hit_id)
            merged.append(hit)

    # Re-sort on a scale that is actually comparable across the two calls.
    merged.sort(key=_ranking_key, reverse=True)

    final = merged[:top_k]
    improved = _improved(final, initial_hits)
    logger.info(
        "Corrective RAG: %s (initial_relevance=%s → corrected=%s)",
        "improved" if improved else "no improvement",
        _avg_relevance(initial_hits),
        _avg_relevance(final),
    )
    return (final if improved else initial_hits), improved


# ---------------------------------------------------------------------------
# Clarification question detection (Phase 6)
# ---------------------------------------------------------------------------
def needs_clarification(query: str, hits: list[dict[str, Any]]) -> str | None:
    """Return a clarification question if the query is ambiguous, else None.

    Only triggers for genuinely ambiguous queries — single-word queries
    with no meaningful hits. 2-3 word queries that retrieve good results
    are NOT flagged.
    """
    q = query.strip()
    words = q.split()

    # Only flag single-word queries that are pure stop words
    if len(words) == 1 and words[0].lower() in {
        "how",
        "what",
        "where",
        "when",
        "who",
        "help",
    }:
        return (
            "Could you please provide more details about your question? "
            "For example, are you asking about registration, filing, payments, "
            "or a specific tax type (VAT, PAYE, CIT)?"
        )

    # If retrieval scores are very low AND query is short, clarify
    if hits and len(words) <= 2:
        avg = _avg_score(hits)
        if avg < 0.05:
            return (
                "I found some information but I'm not confident it addresses your question. "
                "Could you rephrase or provide more context about what you need?"
            )

    return None
