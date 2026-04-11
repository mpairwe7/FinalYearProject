"""Expose the existing hybrid retriever as a first-class agent tool.

Without this, the supervisor agent would have to special-case RAG
vs. tools.  With it, RAG is just another tool in the registry: the
supervisor can call ``search_ura_knowledge_base`` like any other
function, the retriever returns structured passages, and the LLM
decides what to do with them.

This keeps the Phase 1-13 retrieval stack intact — no re-indexing,
no re-plumbing — while making it available to tool-using agents.
"""

from __future__ import annotations

import logging
from typing import Any

from . import Tool, ToolSchema, ToolRegistry

logger = logging.getLogger(__name__)

# Module-level singleton holding a shared HybridRetriever instance.
# We lazily construct one on first call so importing this module
# doesn't force a connection to Qdrant or load sentence-transformers.
_retriever: Any = None
_retriever_ready: bool = False


def _get_retriever() -> Any:
    """Lazy-init a shared HybridRetriever.

    We deliberately do NOT share ``ChatModel._retriever`` — the
    tools package must be usable from contexts that don't hold a
    ChatModel (e.g. the evaluation harness, offline workers).
    The retriever is cheap-ish to instantiate once per process.
    """
    global _retriever, _retriever_ready
    if _retriever is not None:
        return _retriever
    try:
        from ..retriever import HybridRetriever

        _retriever = HybridRetriever()
        _retriever_ready = _retriever.initialize()
        if _retriever_ready:
            logger.info("RAG tool: HybridRetriever initialised")
        else:
            logger.warning("RAG tool: HybridRetriever initialisation failed — returning empty results")
    except Exception:
        logger.exception("RAG tool: HybridRetriever import/init error")
        _retriever = None
        _retriever_ready = False
    return _retriever


class SearchKnowledgeBaseTool(Tool):
    """Search the indexed URA knowledge base for relevant passages."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="search_ura_knowledge_base",
            description=(
                "Search the Uganda Revenue Authority knowledge base "
                "for passages relevant to a query. Uses hybrid dense + "
                "BM25 retrieval with cross-encoder reranking. Returns a "
                "list of cited passages with source file, section, and "
                "relevance score. **Prefer this tool whenever the user "
                "asks a factual question about URA services, tax policy, "
                "registration, procedures, or specific tax types.** "
                "Do NOT invent answers — always ground them in the "
                "passages returned by this tool."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The user's question or a rephrased search query.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many passages to return (1-10). Default 4.",
                        "default": 4,
                    },
                    "filter_doc_type": {
                        "type": "string",
                        "enum": ["csv", "pdf", "any"],
                        "description": "Restrict to FAQ CSV entries, PDF chunks, or any doc type (default).",
                        "default": "any",
                    },
                    "filter_tag": {
                        "type": "string",
                        "description": (
                            "Restrict to passages tagged with a specific topic "
                            "(e.g. 'vat', 'paye', 'customs_valuation'). Leave "
                            "empty for no filter."
                        ),
                    },
                },
                "required": ["query"],
            },
            risk="low",
        )

    def execute(
        self,
        query: str,
        top_k: int = 4,
        filter_doc_type: str = "any",
        filter_tag: str | None = None,
    ) -> dict[str, Any]:
        retriever = _get_retriever()
        if retriever is None or not _retriever_ready:
            return {
                "ok": False,
                "error": "knowledge base retriever unavailable (Qdrant unreachable or collection missing)",
                "results": [],
            }

        top_k = max(1, min(int(top_k), 10))
        filters: dict[str, Any] = {}
        if filter_doc_type and filter_doc_type != "any":
            filters["doc_type"] = filter_doc_type
        if filter_tag:
            filters["tag"] = filter_tag

        try:
            hits = retriever.search(
                query,
                top_k=top_k,
                filters=filters or None,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("RAG tool: search failed")
            return {"ok": False, "error": f"search failed: {e}", "results": []}

        results: list[dict[str, Any]] = []
        for i, h in enumerate(hits, 1):
            text = (h.get("text") or h.get("answer") or "")[:800]
            results.append({
                "ref": f"[{i}]",
                "source": h.get("source", "unknown"),
                "section": h.get("section", ""),
                "page": h.get("page", ""),
                "doc_type": h.get("doc_type", ""),
                "score": round(
                    float(h.get("score_rerank") or h.get("score_rrf") or 0.0),
                    4,
                ),
                "passage": text,
            })

        return {
            "ok": True,
            "query": query,
            "top_k": top_k,
            "filter_doc_type": filter_doc_type,
            "filter_tag": filter_tag or "",
            "count": len(results),
            "results": results,
        }


ToolRegistry.register(SearchKnowledgeBaseTool())
