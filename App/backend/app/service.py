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

import concurrent.futures

from .agents import AgentRoute, supervisor
from .cache import SemanticCache, create_cache
from .corrective_rag import corrective_retrieve, needs_clarification
from .flags import flags
from .guardrails import InputGuard, OutputGuard, redact_pii_text, STORE_RAW_PROMPTS
from . import llm as llm_module
from . import database as db
from .query import rewrite as rewrite_query
from .resilience import CircuitBreaker
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
LLM_DEADLINE_SECONDS = float(os.getenv("LLM_DEADLINE_SECONDS", "45"))
SELF_REFLECT_ENABLED = os.getenv("SELF_REFLECT_ENABLED", "false").lower() == "true"
SELF_REFLECT_THRESHOLD = float(os.getenv("SELF_REFLECT_THRESHOLD", "0.4"))

# Shared executor for LLM calls — bounded so one slow generation cannot
# exhaust worker threads under load.  Size is small on purpose: Qwen runs
# one inference at a time per process anyway (no true batching without vLLM).
_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.getenv("LLM_MAX_CONCURRENCY", "2")),
    thread_name_prefix="llm",
)
_LLM_CIRCUIT = CircuitBreaker(
    name="llm",
    failure_threshold=3,
    reset_timeout=15.0,
    max_timeout=300.0,
)


def _call_llm_with_deadline(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    deadline_s: float = LLM_DEADLINE_SECONDS,
) -> str:
    """Run ``llm_module.generate`` under a hard wall-clock deadline.

    The generation runs on a bounded executor, guarded by a dedicated
    circuit breaker.  On timeout, breaker failure, or exception we
    return an empty string so the caller falls back to FAQ lookup.
    """
    if not _LLM_CIRCUIT.allow_request():
        logger.warning("LLM circuit breaker OPEN — skipping generation")
        return ""

    future = _LLM_EXECUTOR.submit(
        llm_module.generate,
        query=query,
        passages=passages,
        conversation_history=conversation_history,
        locale=locale,
    )
    try:
        reply = future.result(timeout=deadline_s)
        _LLM_CIRCUIT.record_success()
        return reply or ""
    except concurrent.futures.TimeoutError:
        future.cancel()  # best-effort; transformers generate may ignore
        _LLM_CIRCUIT.record_failure()
        logger.warning("LLM deadline %.1fs exceeded", deadline_s)
        return ""
    except Exception:
        _LLM_CIRCUIT.record_failure()
        logger.exception("LLM generation raised")
        return ""


def stream_llm_tokens(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
) -> list[str]:
    """Stream LLM tokens through the shared circuit breaker.

    Mirrors :func:`_call_llm_with_deadline` for the SSE streaming path.
    Returns an empty list when the breaker is OPEN, the generator raises,
    or no tokens are produced — the caller then falls back to
    returning the best-hit answer as a single event.
    """
    if not llm_module.is_available():
        return []
    if not _LLM_CIRCUIT.allow_request():
        logger.warning("LLM circuit breaker OPEN — skipping stream")
        return []

    try:
        tokens = list(
            llm_module.generate_stream(
                query=query,
                passages=passages,
                conversation_history=conversation_history,
                locale=locale,
            )
        )
        if tokens:
            _LLM_CIRCUIT.record_success()
        else:
            # Empty stream counts as a soft failure — the breaker tracks
            # it so a continually empty worker eventually trips.
            _LLM_CIRCUIT.record_failure()
        return tokens
    except Exception:
        _LLM_CIRCUIT.record_failure()
        logger.exception("LLM streaming raised")
        return []


def _call_llm_agentic(  # noqa: PLR0913 — all args are request-scoped config
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    *,
    tool_names: list[str] | None = None,
    max_iterations: int = 3,
    deadline_s: float = LLM_DEADLINE_SECONDS * 2,
) -> dict[str, Any]:
    """Run :func:`llm_module.generate_with_tools` under breaker + deadline.

    Returns the same dict shape as ``generate_with_tools``:
    ``{"text", "tool_calls", "iterations", "truncated"}``.  On breaker
    OPEN, timeout, or exception, returns an empty result so the caller
    can fall back to the non-agentic path (``_call_llm_with_deadline``).

    Deadline is doubled by default because the tool-call loop runs
    multiple generations — a typical 2-hop call takes ~2x a single
    generate().
    """
    empty = {"text": "", "tool_calls": [], "iterations": 0, "truncated": False}
    if not _LLM_CIRCUIT.allow_request():
        logger.warning("LLM circuit breaker OPEN — skipping agentic path")
        return empty

    future = _LLM_EXECUTOR.submit(
        llm_module.generate_with_tools,
        query=query,
        passages=passages or None,
        tool_names=tool_names,
        conversation_history=conversation_history,
        locale=locale,
        max_iterations=max_iterations,
    )
    try:
        result = future.result(timeout=deadline_s)
        if result and result.get("text"):
            _LLM_CIRCUIT.record_success()
            return result
        # Empty text counts as a soft failure
        _LLM_CIRCUIT.record_failure()
        return result or empty
    except concurrent.futures.TimeoutError:
        future.cancel()
        _LLM_CIRCUIT.record_failure()
        logger.warning("agentic LLM deadline %.1fs exceeded", deadline_s)
        return empty
    except Exception:
        _LLM_CIRCUIT.record_failure()
        logger.exception("agentic LLM generation raised")
        return empty


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

        # Semantic cache — factory selects in-process vs Redis backend
        # based on CACHE_BACKEND env (see cache.py).
        self._cache = create_cache()
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
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a grounded, cited answer via hybrid retrieval + guardrails."""
        t0 = time.perf_counter()

        with trace_rag_pipeline(message, request_id=request_id) as trace_ctx:
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

            # 1c. Phase 14-C — supervisor routing.  When FLAG_AGENTIC_MODE
            #     is on, the supervisor classifies the query and routes
            #     to one of: RAG (default), TOOLS, a specialist, CLARIFY
            #     (ask for details), or ESCALATE (human handoff).
            route_decision = None
            force_agentic = False
            force_tool_whitelist: list[str] | None = None
            if flags.is_enabled("agentic_mode"):
                with trace_stage("supervisor", timings=timings):
                    route_decision = supervisor.classify(
                        rewritten,
                        has_conversation_history=bool(conversation_history),
                    )
                trace_ctx["agent_route"] = route_decision.route.value
                trace_ctx["agent_route_confidence"] = route_decision.route_confidence if hasattr(route_decision, "route_confidence") else route_decision.confidence
                logger.info(
                    "supervisor: route=%s confidence=%.2f reason=%s",
                    route_decision.route.value,
                    route_decision.confidence,
                    route_decision.reason,
                )

                # Early returns — CLARIFY and ESCALATE don't need retrieval.
                if route_decision.route == AgentRoute.CLARIFY:
                    return {
                        "reply": route_decision.clarification_question or (
                            "Could you provide a bit more detail about your "
                            "question? I can help with VAT, PAYE, customs, "
                            "registration, or specific tax types."
                        ),
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
                if route_decision.route == AgentRoute.ESCALATE:
                    return {
                        "reply": (
                            "This looks like a question best handled by a URA "
                            "officer. I've flagged it for human review — you "
                            "can also contact URA directly at https://ura.go.ug "
                            "or via the Contact Centre."
                        ),
                        "sources": [],
                        "citations": [],
                        "faithfulness_score": None,
                        "retrieval_mode": "escalated",
                        "model": self.name,
                        "conversation_id": conversation_id,
                        "locale": locale,
                        "escalation_required": True,
                        "escalation_reason": route_decision.reason,
                    }

                # TOOLS / SPECIALIST routes force the agentic LLM path
                # downstream, with the supervisor's suggested tool whitelist.
                if route_decision.route in (
                    AgentRoute.TOOLS,
                    AgentRoute.TAX_SPECIALIST,
                    AgentRoute.CUSTOMS_SPECIALIST,
                ):
                    force_agentic = True
                    if route_decision.suggested_tools:
                        force_tool_whitelist = list(route_decision.suggested_tools)
                    trace_ctx["specialist"] = route_decision.route.value

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
                    # Phase 14-B/C: agentic path is active when either
                    # FLAG_TOOL_USE is on (tool calling for everyone), or
                    # the supervisor routed this specific request to it
                    # (force_agentic).  The supervisor can also narrow
                    # the tool whitelist (force_tool_whitelist).
                    use_agentic = force_agentic or flags.is_enabled("tool_use")
                    if use_agentic:
                        with trace_stage("llm_agentic", timings=timings):
                            agentic = _call_llm_agentic(
                                query=rewritten,
                                passages=hits,
                                conversation_history=conversation_history or None,
                                locale=locale,
                                tool_names=force_tool_whitelist,
                            )
                        reply = agentic.get("text", "")
                        if agentic.get("tool_calls"):
                            trace_ctx["tool_calls"] = [
                                tc.get("name") for tc in agentic["tool_calls"]
                            ]
                            trace_ctx["tool_iterations"] = agentic.get("iterations", 0)
                    else:
                        with trace_stage("llm_generate", timings=timings):
                            reply = _call_llm_with_deadline(
                                query=rewritten,
                                passages=hits,
                                conversation_history=conversation_history or None,
                                locale=locale,
                            )
                    # Optional structured-output parse (LLM_STRUCTURED_OUTPUT=true)
                    if reply and llm_module.LLM_STRUCTURED_OUTPUT and not use_agentic:
                        valid_refs = [str(i) for i in range(1, len(hits) + 1)]
                        parsed = llm_module.parse_structured_reply(reply, valid_refs)
                        if parsed["structured"]:
                            reply = parsed["answer"]
                            # Filter citations to refs the model actually cited
                            cited_refs = set(parsed["citations"])
                            if cited_refs:
                                citations = [c for c in citations if c["ref"].strip("[]") in cited_refs]
                            trace_ctx["structured_output"] = True
                            if parsed["abstain"]:
                                retrieval_mode = "abstained"
                    if not reply:
                        # Fallback to best-hit answer if LLM fails, times out,
                        # or the circuit breaker is open
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

            # 6. Output guardrails (OWASP LLM02 + LLM05 + LLM07)
            with trace_stage("output_guard", timings=timings):
                reply = self._output_guard.redact_pii(reply)
                reply = self._output_guard.sanitize(reply)
                # LLM07 — system prompt leakage
                leakage = self._output_guard.check_prompt_leakage(reply)
                reply = leakage.sanitized_text
                if leakage.flags:
                    trace_ctx["prompt_leakage"] = True

            # 7. Grounding verification (OWASP LLM09)
            faithfulness_score: float | None = None
            reflect_fired = False
            if contexts:
                with trace_stage("grounding", timings=timings):
                    faith = HybridRetriever.compute_faithfulness(reply, contexts)
                    faithfulness_score = faith

                # 7b. Self-reflection — regenerate once if grounding is weak
                # (Self-RAG, 2023/2024).  Feature-flagged off by default
                # because it doubles LLM latency when triggered.
                # Honours FLAG_SELF_REFLECT / legacy SELF_REFLECT_ENABLED env.
                if (
                    (flags.is_enabled("self_reflect") or SELF_REFLECT_ENABLED)
                    and self._llm_available
                    and faith is not None
                    and faith < SELF_REFLECT_THRESHOLD
                ):
                    with trace_stage("self_reflect", timings=timings):
                        revise_query = (
                            f"Previous answer:\n{reply}\n\n"
                            f"Check whether every factual claim is supported "
                            f"by the retrieved passages. Rewrite the answer "
                            f"so that every claim is grounded, or explicitly "
                            f"say which parts cannot be verified.\n\n"
                            f"Original question: {rewritten}"
                        )
                        revised = _call_llm_with_deadline(
                            query=revise_query,
                            passages=hits,
                            conversation_history=conversation_history or None,
                            locale=locale,
                        )
                    if revised:
                        reply = self._output_guard.sanitize(
                            self._output_guard.redact_pii(revised)
                        )
                        leakage = self._output_guard.check_prompt_leakage(reply)
                        reply = leakage.sanitized_text
                        faith = HybridRetriever.compute_faithfulness(reply, contexts)
                        faithfulness_score = faith
                        reflect_fired = True

                with trace_stage("grounding", timings=timings):
                    grounding = self._output_guard.check_grounding(
                        reply, contexts, GROUNDING_THRESHOLD
                    )
                    reply = grounding.sanitized_text
                    trace_ctx["faithfulness"] = faith
                    if reflect_fired:
                        trace_ctx["self_reflected"] = True

            # 8. Escalation check
            escalate, esc_reason = self._output_guard.should_escalate(
                faithfulness_score, hits
            )

            trace_ctx["num_sources"] = len(sources)
            trace_ctx["locale"] = locale

            # Record real token usage (gen_ai.usage.input_tokens /
            # gen_ai.usage.output_tokens).  Uses the loaded Qwen tokenizer
            # via llm_module.count_tokens; falls back to word count when
            # the tokenizer is not available (LLM disabled).
            input_tokens = llm_module.count_tokens(message)
            output_tokens = llm_module.count_tokens(reply)
            trace_ctx["input_tokens"] = input_tokens
            trace_ctx["output_tokens"] = output_tokens
            record_token_usage(input_tokens, output_tokens)

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
        request_id: str | None = None,
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
