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

from .guardrails import InputGuard, OutputGuard, redact_pii_text, STORE_RAW_PROMPTS
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
        self.name = "ura-gemma-2-9b"
        self._faq_index, self._tag_labels = _load_faq_data(_DATA_DIR)

        # Hybrid retriever (graceful degradation)
        self._retriever = HybridRetriever()
        self._retriever_ready = self._retriever.initialize()

        # OWASP LLM Top 10 guardrails
        self._input_guard = InputGuard()
        self._output_guard = OutputGuard()

        mode = "hybrid (Qdrant)" if self._retriever_ready else "keyword-only (fallback)"
        logger.info("ChatModel initialised – %s mode, %d tags", mode, len(self._faq_index))

    # -- Chat (RAG) ---------------------------------------------------------
    def generate(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 4,
        locale: str = "en",
    ) -> dict[str, Any]:
        """Return a grounded, cited answer via hybrid retrieval + guardrails."""
        t0 = time.perf_counter()

        with trace_rag_pipeline(message) as trace_ctx:
            # 1. Input guardrails (OWASP LLM01)
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

            # 2. Try hybrid retrieval (Qdrant dense+sparse RRF → cross-encoder)
            hits: list[dict[str, Any]] = []
            retrieval_mode = "keyword"

            # Auto-reconnect if Qdrant was lost after initial startup
            if not self._retriever_ready and not self._retriever._ready:
                self._retriever_ready = self._retriever.initialize()

            if self._retriever_ready:
                with trace_stage("hybrid_search"):
                    search_t0 = time.perf_counter()
                    hits = self._retriever.search(message, top_k=top_k)
                    search_ms = (time.perf_counter() - search_t0) * 1000
                if hits:
                    retrieval_mode = "hybrid"
                    record_retrieval_metrics(len(hits), search_ms)
                # Update readiness if retriever was disconnected during search
                self._retriever_ready = self._retriever._ready

            # 3. Fallback to keyword search
            if not hits:
                with trace_stage("keyword_search"):
                    kw_hits = _simple_search(message, self._faq_index, top_k=top_k)
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

            # 4. Calibrated abstention — refuse to answer when confidence too low
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
                }

            # 5. Build response with citations
            if hits:
                best = hits[0]
                reply = best.get("answer") or best.get("text", "")
                sources = list({h.get("source", "") for h in hits if h.get("source")})
                citations = HybridRetriever.build_citations(hits)
                contexts = [h.get("text") or h.get("answer", "") for h in hits]
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
            reply = self._output_guard.redact_pii(reply)
            reply = self._output_guard.sanitize(reply)

            # 7. Grounding verification (OWASP LLM09)
            faithfulness_score: float | None = None
            if contexts:
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
            "generate: mode=%s hits=%d faith=%.2f ms=%.1f locale=%s escalate=%s",
            retrieval_mode,
            len(hits),
            faithfulness_score or 0,
            elapsed_ms,
            locale,
            escalate,
        )

        return {
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
