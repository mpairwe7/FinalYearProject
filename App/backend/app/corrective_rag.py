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


def _avg_score(hits: list[dict[str, Any]]) -> float:
    """Average reranker score of retrieved hits."""
    scores = [h.get("score_rerank", h.get("score_rrf", 0.0)) for h in hits]
    return sum(scores) / max(len(scores), 1)


def _expand_query(query: str) -> str:
    """Simple query expansion for re-retrieval."""
    from .query import correct_spelling, expand_abbreviations

    expanded = expand_abbreviations(correct_spelling(query))
    # Add "Uganda Revenue Authority" context if not present
    if "ura" not in expanded.lower() and "uganda" not in expanded.lower():
        expanded = f"{expanded} Uganda Revenue Authority"
    return expanded


def should_correct(hits: list[dict[str, Any]]) -> bool:
    """Determine if corrective re-retrieval is needed."""
    if not CORRECTIVE_ENABLED:
        return False
    if not hits:
        return True
    return _avg_score(hits) < CORRECTIVE_THRESHOLD


def corrective_retrieve(
    query: str,
    retriever: Any,
    initial_hits: list[dict[str, Any]],
    top_k: int = 4,
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
    new_hits = retriever.search(expanded, top_k=top_k + 2, prefetch_limit=30)

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

    # Re-sort by best available score
    merged.sort(
        key=lambda h: h.get("score_rerank", h.get("score_rrf", 0.0)),
        reverse=True,
    )

    final = merged[:top_k]
    improved = _avg_score(final) > _avg_score(initial_hits)
    logger.info(
        "Corrective RAG: %s (initial=%.3f → corrected=%.3f)",
        "improved" if improved else "no improvement",
        _avg_score(initial_hits),
        _avg_score(final),
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
        "hi",
        "hello",
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
