"""Tool RAG — retrieve the top-k relevant tools per query.

2026 standard: at 15+ tools, pasting every schema into every
generation prompt wastes 3-5k tokens per turn and confuses the
model.  Instead we embed every tool description once and retrieve
the top-k most relevant per query, together with the mandatory
rails (escalate + knowledge-base search).

This module is intentionally **zero-dependency** — it uses a
simple token-overlap scorer so the feature works on any host
without sentence-transformers loaded.  When the dense model is
already available (Phase 1-13 retriever has one), callers can
inject it for semantic similarity scoring.

Feature flag: ``FLAG_TOOL_RAG`` default false — the agent falls
back to exposing all tools when off.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Tools that should ALWAYS be in the whitelist regardless of the
# retriever's opinion — these are the safety rails.
MANDATORY_RAILS = ["search_ura_knowledge_base", "escalate_to_human"]


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", text.lower()) if len(t) > 2}


@dataclass
class ToolSelection:
    """Result of one Tool RAG selection pass."""

    tool_names: list[str]
    scores: dict[str, float] = field(default_factory=dict)
    fallback_reason: str = ""

    def __contains__(self, name: str) -> bool:
        return name in self.tool_names

    def __len__(self) -> int:
        return len(self.tool_names)


class ToolRAGSelector:
    """Select top-k tools for a query using token-overlap or dense embeddings.

    Algorithm:
    1. Tokenize the query
    2. For each eligible tool (post security-trimming), score the
       overlap between query tokens and
       (tool name + description + keywords).
    3. Return the top-k scored tools *plus* the mandatory rails.

    When ``dense_model`` is provided (a sentence-transformers model),
    scoring switches to cosine similarity of embedded tool
    descriptions vs the query embedding.  The token-overlap path is
    the always-available fallback.
    """

    def __init__(self, dense_model: Any = None) -> None:
        self._dense_model = dense_model
        self._tool_embeds: dict[str, Any] | None = None

    def set_dense_model(self, model: Any) -> None:
        """Inject a sentence-transformers model at runtime."""
        self._dense_model = model
        self._tool_embeds = None  # invalidate cache

    def _score_token_overlap(self, query_tokens: set[str], tool) -> float:
        """Weighted token overlap score.

        Tool names are far more discriminating than descriptions (all
        tax tools share words like "tax", "uganda", "calculate") —
        a name match gets a 5x weight relative to a description match.
        That way "how much VAT" selects ``calculate_vat`` rather than
        tying with every other calculator.
        """
        schema = tool.schema
        name_tokens = _tokenize(schema.name.replace("_", " "))
        desc_tokens = _tokenize(schema.description) - name_tokens
        if not query_tokens:
            return 0.0
        name_hits = len(query_tokens & name_tokens)
        desc_hits = len(query_tokens & desc_tokens)
        if name_hits == 0 and desc_hits == 0:
            return 0.0
        # Name hits get a 5x weight, desc hits get 1x.  Normalise by
        # query length so scores are comparable across tools.
        weighted = (name_hits * 5 + desc_hits) / max(len(query_tokens), 1)
        return round(weighted, 4)

    def _score_dense(self, query: str, tool) -> float:
        if self._dense_model is None:
            return 0.0
        import numpy as np

        try:
            if self._tool_embeds is None:
                self._tool_embeds = {}
            name = tool.schema.name
            if name not in self._tool_embeds:
                text = f"{tool.schema.name}: {tool.schema.description}"
                self._tool_embeds[name] = self._dense_model.encode(text, normalize_embeddings=True)
            tool_emb = self._tool_embeds[name]
            query_emb = self._dense_model.encode(query, normalize_embeddings=True)
            return float(np.dot(tool_emb, query_emb))
        except Exception:
            logger.debug("Tool RAG dense scoring failed", exc_info=True)
            return 0.0

    def select(
        self,
        query: str,
        eligible_names: list[str],
        k: int = 5,
        min_score: float = 0.0,
    ) -> ToolSelection:
        """Return the top-k tool names for *query*.

        *eligible_names* is the post-authz whitelist from
        :py:meth:`MCPClient.available_for`.  Mandatory rails are
        always appended (deduplicated) at the end.
        """
        from ..tools import ToolRegistry

        if not query or not eligible_names:
            return ToolSelection(tool_names=[], fallback_reason="empty inputs")

        query_tokens = _tokenize(query)
        scores: dict[str, float] = {}
        use_dense = self._dense_model is not None

        for name in eligible_names:
            tool = ToolRegistry.get(name)
            if tool is None:
                continue
            if use_dense:
                s = self._score_dense(query, tool)
            else:
                s = self._score_token_overlap(query_tokens, tool)
            # Only record tools that actually matched — otherwise
            # a zero-score tool would prevent the fallback branch
            # from firing when the query has no relevant tools.
            if s > 0 and s >= min_score:
                scores[name] = round(s, 4)

        # Top-k by score, descending
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        selected = [name for name, _ in ranked]

        # Always add the mandatory rails (preserving order; dedupe)
        for rail in MANDATORY_RAILS:
            if rail in eligible_names and rail not in selected:
                selected.append(rail)

        if not selected:
            # Nothing scored — fall back to exposing everything eligible
            # (avoids leaving the LLM with zero tools).
            return ToolSelection(
                tool_names=eligible_names,
                scores=scores,
                fallback_reason="no tools scored above threshold",
            )

        return ToolSelection(tool_names=selected, scores=scores)
