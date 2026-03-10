"""URA Chatbot service layer — production hybrid RAG.

This module provides the ``ChatModel`` singleton that backs every API
endpoint.  It loads the FAQ CSV knowledge base into memory (for tag
classification and as a keyword-search fallback) and, when a Qdrant
vector store is available, performs hybrid dense + BM25 retrieval with
cross-encoder reranking and passage-level grounding verification.

Architecture (2026 RAG best practice)::

    User query
      → InputGuard (OWASP LLM01 prompt-injection check)
      → HybridRetriever.search (dense + sparse RRF → cross-encoder rerank)
      → fallback: _simple_search (keyword overlap)
      → passage-level citation assembly
      → OutputGuard (PII redaction, grounding check – LLM02/LLM05/LLM09)
      → ChatResponse with citations + faithfulness score

References:
  - Lewis et al. "Retrieval-Augmented Generation" (nlp.cs.ucl.ac.uk)
  - OWASP LLM Top 10 (owasp.org)
  - RAGAS docs (docs.ragas.io)
"""

from __future__ import annotations

import csv
import logging
import os
import time
from pathlib import Path
from typing import Any

from .cache import SemanticCache
from .corrective_rag import corrective_retrieve, needs_clarification
from .guardrails import InputGuard, OutputGuard, redact_pii_text, STORE_RAW_PROMPTS
from . import llm as llm_module
from . import database as db
from .query import rewrite as rewrite_query
from .retriever import HybridRetriever
from .tracing import record_retrieval_metrics, record_token_usage, trace_rag_pipeline, trace_stage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_DATA_DIR = str(Path(__file__).resolve().parents[3] / "Data" / "dataset")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = Path(os.getenv("DATA_DIR", _DEFAULT_DATA_DIR)).resolve()
# Guard against path traversal via DATA_DIR env var
if not _DATA_DIR.is_relative_to(_PROJECT_ROOT):
    logger.warning("DATA_DIR %s escapes project root; falling back to default", _DATA_DIR)
    _DATA_DIR = Path(_DEFAULT_DATA_DIR).resolve()

GROUNDING_THRESHOLD = float(os.getenv("GROUNDING_THRESHOLD", "0.3"))


def _load_faq_data(data_dir: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    """Load all ``ura_*_faqs.csv`` files into an in-memory FAQ index.

    Returns ``(faq_index, tag_labels)`` where *faq_index* is keyed by tag and
    *tag_labels* maps tag IDs to human-readable names.
    """
    faq_index: dict[str, list[dict[str, str]]] = {}
    tag_labels: dict[str, str] = {}

    if not data_dir.is_dir():
        logger.warning("FAQ data directory not found: %s", data_dir)
        return faq_index, tag_labels

    for csv_path in sorted(data_dir.glob("ura_*_faqs.csv")):
        tag = csv_path.stem.replace("ura_", "").replace("_faqs", "")
        label = tag.replace("_", " ").title()
        tag_labels[tag] = label

        entries: list[dict[str, str]] = []
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    q = (row.get("question") or row.get("Question") or "").strip()
                    a = (row.get("answer") or row.get("Answer") or "").strip()
                    if q and a:
                        entries.append({"question": q, "answer": a, "source": csv_path.name})
        except Exception:
            logger.exception("Failed to load %s", csv_path)

        if entries:
            faq_index[tag] = entries
            logger.info("Loaded %d FAQs from %s (tag=%s)", len(entries), csv_path.name, tag)

    logger.info(
        "FAQ index ready – %d tags, %d total entries",
        len(faq_index),
        sum(len(v) for v in faq_index.values()),
    )
    return faq_index, tag_labels


def _simple_search(
    query: str,
    faq_index: dict[str, list[dict[str, str]]],
    top_k: int = 4,
) -> list[dict[str, str]]:
    """Keyword-based retrieval fallback: score each FAQ by word overlap with *query*."""
    query_tokens = set(query.lower().split())
    scored: list[tuple[float, dict[str, str]]] = []

    for entries in faq_index.values():
        for entry in entries:
            q_tokens = set(entry["question"].lower().split())
            a_tokens = set(entry["answer"].lower().split())
            overlap = len(query_tokens & (q_tokens | a_tokens))
            if overlap > 0:
                scored.append((overlap, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------
class ChatModel:
    """Unified service that backs all API endpoints.

    On initialisation it loads the FAQ CSV corpus into memory and attempts
    to connect to Qdrant for hybrid retrieval.  If Qdrant is unavailable
    the service degrades gracefully to keyword-only search.
    """

    def __init__(self) -> None:
        self.name = "ura-qwen2.5-3b-instruct"
        self._faq_index, self._tag_labels = _load_faq_data(_DATA_DIR)

        # Hybrid retriever (graceful degradation)
        self._retriever = HybridRetriever()
        self._retriever_ready = self._retriever.initialize()

        # OWASP LLM Top 10 guardrails
        self._input_guard = InputGuard()
        self._output_guard = OutputGuard()

        # LLM generation (Phase 2 — true RAG)
        self._llm_available = llm_module.is_available()

        # Semantic cache (Phase 5 — cost optimization)
        self._cache = SemanticCache()
        if self._retriever_ready and self._retriever._dense_model:
            self._cache.set_model(self._retriever._dense_model)

        mode = "hybrid (Qdrant)" if self._retriever_ready else "keyword-only (fallback)"
        gen_mode = "LLM (Qwen2.5-3B)" if self._llm_available else "FAQ lookup (fallback)"
        logger.info("ChatModel initialised – %s mode, %s gen, %d tags", mode, gen_mode, len(self._faq_index))

    # -- Chat (RAG) ---------------------------------------------------------
    def generate(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 4,
        locale: str = "en",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a grounded, cited answer via hybrid retrieval + guardrails."""
        t0 = time.perf_counter()

        with trace_rag_pipeline(message) as trace_ctx:
            timings = trace_ctx["timings"]

            # 0. Multi-turn memory — fetch recent conversation history (Phase 4)
            conversation_history: list[dict[str, str]] = []
            if session_id:
                try:
                    conversation_history = db.get_recent_turns(session_id, limit=5)
                except Exception:
                    logger.debug("Failed to fetch conversation history", exc_info=True)

            # 0b. Query rewriting — spell correction, abbreviation expansion,
            #     coreference resolution from history (Phase 4)
            with trace_stage("query_rewrite", timings=timings):
                rewritten = rewrite_query(message, history=conversation_history or None)

            # 1. Input guardrails FIRST (OWASP LLM01) — check original message
            with trace_stage("input_guard", timings=timings):
                guard = self._input_guard.check(message)
            if not guard.allowed:
                return {
                    "reply": guard.reason,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "blocked",
                    "model": self.name,
                    "conversation_id": conversation_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                }

            # 1b. Semantic cache check AFTER guardrails (Phase 5)
            with trace_stage("cache_lookup", timings=timings):
                cached = self._cache.get(rewritten, locale=locale)
            if cached:
                logger.info("generate: cache HIT for query=%s", message[:50])
                return cached

            # 2. Try hybrid retrieval using rewritten query
            hits: list[dict[str, Any]] = []
            retrieval_mode = "keyword"

            # Auto-reconnect if Qdrant was lost after initial startup
            if not self._retriever_ready and not self._retriever._ready:
                self._retriever_ready = self._retriever.initialize()

            if self._retriever_ready:
                with trace_stage("hybrid_search", timings=timings):
                    search_t0 = time.perf_counter()
                    hits = self._retriever.search(rewritten, top_k=top_k)
                    search_ms = (time.perf_counter() - search_t0) * 1000
                if hits:
                    retrieval_mode = "hybrid"
                    record_retrieval_metrics(len(hits), search_ms)
                # Update readiness if retriever was disconnected during search
                self._retriever_ready = self._retriever._ready

            # 3. Fallback to keyword search
            if not hits:
                with trace_stage("keyword_search", timings=timings):
                    kw_hits = _simple_search(rewritten, self._faq_index, top_k=top_k)
                    hits = [
                        {
                            "text": f"Question: {h['question']}\nAnswer: {h['answer']}",
                            "answer": h["answer"],
                            "question": h["question"],
                            "source": h["source"],
                            "chunk_id": "",
                            "page": "",
                            "section": "",
                            "doc_type": "csv",
                            "score_rrf": 0.0,
                        }
                        for h in kw_hits
                    ]

            # 3b. Corrective RAG — re-retrieve if quality is low (Phase 6)
            if hits and self._retriever_ready:
                with trace_stage("corrective_rag", timings=timings):
                    hits, was_corrected = corrective_retrieve(
                        rewritten, self._retriever, hits, top_k=top_k
                    )
                    if was_corrected:
                        retrieval_mode = "hybrid_corrected"

            # 3c. Clarification check — ask for more details if query is ambiguous
            clarification = needs_clarification(message, hits)
            if clarification:
                return {
                    "reply": clarification,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "clarification",
                    "model": self.name,
                    "conversation_id": conversation_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                }

            # 4. Calibrated abstention — refuse to answer when confidence too low
            with trace_stage("abstention_check", timings=timings):
                should_abstain = self._output_guard.should_abstain(hits)
            if should_abstain:
                reply = (
                    "I don't have enough information to answer this question reliably. "
                    "Please contact URA directly at https://ura.go.ug or call "
                    "the URA Contact Centre for assistance."
                )
                escalate, esc_reason = self._output_guard.should_escalate(None, hits)
                return {
                    "reply": reply,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "abstained",
                    "model": self.name,
                    "conversation_id": conversation_id,
                    "locale": locale,
                    "escalation_required": escalate,
                    "escalation_reason": esc_reason,
                }

            # 5. Build response with citations
            if hits:
                sources = list({h.get("source", "") for h in hits if h.get("source")})
                citations = HybridRetriever.build_citations(hits)
                contexts = [h.get("text") or h.get("answer", "") for h in hits]

                # Phase 2: LLM synthesis from top-k passages (true RAG)
                if self._llm_available:
                    with trace_stage("llm_generate", timings=timings):
                        reply = llm_module.generate(
                            query=rewritten,
                            passages=hits,
                            conversation_history=conversation_history or None,
                            locale=locale,
                        )
                    if not reply:
                        # Fallback to best-hit answer if LLM fails
                        best = hits[0]
                        reply = best.get("answer") or best.get("text", "")
                else:
                    # FAQ lookup fallback (no LLM configured)
                    best = hits[0]
                    reply = best.get("answer") or best.get("text", "")
            else:
                reply = (
                    "I could not find a specific answer in the URA knowledge base. "
                    "Please try rephrasing your question, or contact URA directly at "
                    "https://ura.go.ug for assistance."
                )
                sources = []
                citations = []
                contexts = []

            # 6. Output guardrails (OWASP LLM02 + LLM05)
            with trace_stage("output_guard", timings=timings):
                reply = self._output_guard.redact_pii(reply)
                reply = self._output_guard.sanitize(reply)

            # 7. Grounding verification (OWASP LLM09)
            faithfulness_score: float | None = None
            if contexts:
                with trace_stage("grounding", timings=timings):
                    faith = HybridRetriever.compute_faithfulness(reply, contexts)
                    faithfulness_score = faith
                    grounding = self._output_guard.check_grounding(
                        reply, contexts, GROUNDING_THRESHOLD
                    )
                    reply = grounding.sanitized_text
                    trace_ctx["faithfulness"] = faith

            # 8. Escalation check
            escalate, esc_reason = self._output_guard.should_escalate(
                faithfulness_score, hits
            )

            trace_ctx["num_sources"] = len(sources)
            trace_ctx["locale"] = locale

            # Record estimated token usage (word-count proxy)
            prompt_tokens = len(message.split())
            completion_tokens = len(reply.split())
            record_token_usage(prompt_tokens, completion_tokens)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "generate: mode=%s hits=%d faith=%.2f ms=%.1f locale=%s escalate=%s stages=%s",
            retrieval_mode,
            len(hits),
            faithfulness_score or 0,
            elapsed_ms,
            locale,
            escalate,
            trace_ctx.get("timings", {}),
        )

        result = {
            "reply": reply,
            "sources": sources,
            "citations": citations,
            "faithfulness_score": faithfulness_score,
            "retrieval_mode": retrieval_mode,
            "model": self.name,
            "conversation_id": conversation_id,
            "locale": locale,
            "escalation_required": escalate,
            "escalation_reason": esc_reason,
        }

        # Store in semantic cache (Phase 5)
        if retrieval_mode not in ("blocked", "abstained"):
            self._cache.put(rewritten, result)

        return result

    def generate_retrieval_only(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 4,
        locale: str = "en",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Run retrieval + guardrails but skip LLM generation (for SSE streaming).

        Returns the same dict as ``generate()`` but with ``_hits`` and
        ``_history`` included so the streaming endpoint can pass them to
        the LLM stream.
        """
        # Multi-turn memory (Phase 4)
        conversation_history: list[dict[str, str]] = []
        if session_id:
            try:
                conversation_history = db.get_recent_turns(session_id, limit=5)
            except Exception:
                logger.debug("Failed to fetch conversation history", exc_info=True)

        # Query rewriting (Phase 4)
        rewritten = rewrite_query(message, history=conversation_history or None)

        # Input guardrails (OWASP LLM01)
        guard = self._input_guard.check(message)
        if not guard.allowed:
            return {
                "reply": guard.reason,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "blocked",
                "model": self.name,
                "conversation_id": conversation_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "_hits": [],
                "_history": [],
            }

        # Semantic cache check (Phase 5)
        cached = self._cache.get(rewritten, locale=locale)
        if cached:
            return cached

        hits: list[dict[str, Any]] = []
        retrieval_mode = "keyword"

        if not self._retriever_ready and not self._retriever._ready:
            self._retriever_ready = self._retriever.initialize()

        if self._retriever_ready:
            hits = self._retriever.search(rewritten, top_k=top_k)
            if hits:
                retrieval_mode = "hybrid"
            self._retriever_ready = self._retriever._ready

        if not hits:
            kw_hits = _simple_search(rewritten, self._faq_index, top_k=top_k)
            hits = [
                {
                    "text": f"Question: {h['question']}\nAnswer: {h['answer']}",
                    "answer": h["answer"],
                    "question": h["question"],
                    "source": h["source"],
                    "chunk_id": "",
                    "page": "",
                    "section": "",
                    "doc_type": "csv",
                    "score_rrf": 0.0,
                }
                for h in kw_hits
            ]

        # Corrective RAG (Phase 6)
        if hits and self._retriever_ready:
            hits, was_corrected = corrective_retrieve(
                rewritten, self._retriever, hits, top_k=top_k
            )
            if was_corrected:
                retrieval_mode = "hybrid_corrected"

        # Clarification check (Phase 6)
        clarification = needs_clarification(message, hits)
        if clarification:
            return {
                "reply": clarification,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "clarification",
                "model": self.name,
                "conversation_id": conversation_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "_hits": [],
                "_history": [],
            }

        if self._output_guard.should_abstain(hits):
            reply = (
                "I don't have enough information to answer this question reliably. "
                "Please contact URA directly at https://ura.go.ug or call "
                "the URA Contact Centre for assistance."
            )
            escalate, esc_reason = self._output_guard.should_escalate(None, hits)
            return {
                "reply": reply,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "abstained",
                "model": self.name,
                "conversation_id": conversation_id,
                "locale": locale,
                "escalation_required": escalate,
                "escalation_reason": esc_reason,
                "_hits": [],
                "_history": [],
            }

        sources = list({h.get("source", "") for h in hits if h.get("source")})
        citations = HybridRetriever.build_citations(hits)
        best = hits[0] if hits else {}
        reply = best.get("answer") or best.get("text", "")

        # Escalation check (same as sync path)
        escalate, esc_reason = self._output_guard.should_escalate(None, hits)

        return {
            "reply": reply,
            "sources": sources,
            "citations": citations,
            "faithfulness_score": None,
            "retrieval_mode": retrieval_mode,
            "model": self.name,
            "conversation_id": conversation_id,
            "locale": locale,
            "escalation_required": escalate,
            "escalation_reason": esc_reason,
            "_hits": hits,
            "_history": conversation_history,
            "_rewritten": rewritten,
        }

    @staticmethod
    def redact_for_storage(text: str) -> str:
        """Redact PII before database persistence (privacy-by-design)."""
        if STORE_RAW_PROMPTS:
            return text
        return redact_pii_text(text)

    # -- Classification -----------------------------------------------------
    def classify(self, text: str, top_k: int = 1) -> dict[str, Any]:
        """Classify *text* against known FAQ tags by keyword overlap."""
        t0 = time.perf_counter()
        query_tokens = set(text.lower().split())
        tag_scores: dict[str, float] = {}

        for tag, entries in self._faq_index.items():
            score = 0.0
            for entry in entries:
                tokens = set(entry["question"].lower().split())
                score += len(query_tokens & tokens)
            if score > 0:
                tag_scores[tag] = score

        total = sum(tag_scores.values()) or 1.0
        ranked = sorted(tag_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        predictions = [
            {
                "tag": tag,
                "confidence": round(score / total, 4),
                "label": self._tag_labels.get(tag, tag.replace("_", " ").title()),
            }
            for tag, score in ranked
        ]

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"predictions": predictions, "processing_time_ms": round(elapsed_ms, 2)}

    def classify_batch(self, texts: list[str]) -> dict[str, Any]:
        """Classify multiple texts in one call."""
        t0 = time.perf_counter()
        results = []
        for text in texts:
            single = self.classify(text, top_k=1)
            if single["predictions"]:
                p = single["predictions"][0]
                results.append({"text": text, "tag": p["tag"], "confidence": p["confidence"]})
            else:
                results.append({"text": text, "tag": "unknown", "confidence": 0.0})

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"results": results, "processing_time_ms": round(elapsed_ms, 2)}

    # -- Tags & FAQ ---------------------------------------------------------
    def list_tags(self) -> dict[str, Any]:
        """Return all known FAQ tags."""
        tags = [
            {
                "id": tag,
                "name": self._tag_labels.get(tag, tag.replace("_", " ").title()),
                "description": f"Questions about {self._tag_labels.get(tag, tag).lower()}",
            }
            for tag in sorted(self._faq_index)
        ]
        return {"tags": tags, "total": len(tags)}

    def get_faq(self, tag: str) -> dict[str, Any] | None:
        """Return FAQ entries for a specific *tag*."""
        entries = self._faq_index.get(tag)
        if entries is None:
            return None
        return {"tag": tag, "faqs": entries, "total": len(entries)}
