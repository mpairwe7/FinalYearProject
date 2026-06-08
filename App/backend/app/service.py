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

import asyncio
import concurrent.futures
import csv
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any, Callable

from . import database as db
from . import llm as llm_module
from .agents import AgentRoute, supervisor
from .agents.supervisor import _GREETING_WORDS, _GREETING_PHRASES
from .cache import create_cache
from .claim_verifier import verify_claims
from .corrective_rag import corrective_retrieve, needs_clarification
from .flags import flags
from .guardrails import STORE_RAW_PROMPTS, InputGuard, OutputGuard, redact_pii_text
from .memory import get_memory_service
from .query import detect_language, rewrite as rewrite_query
from .resilience import CircuitBreaker
from .retriever import HybridRetriever
from .tracing import record_retrieval_metrics, record_token_usage, trace_rag_pipeline, trace_stage
from .workflows.registry import WorkflowRegistry, WorkflowSession, auto_load_flows

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
from ._root import PROJECT_ROOT as _PROJECT_ROOT

_DEFAULT_DATA_DIR = str(_PROJECT_ROOT / "Data" / "dataset")
_DATA_DIR = Path(os.getenv("DATA_DIR", _DEFAULT_DATA_DIR)).resolve()
# Guard against path traversal via DATA_DIR env var
if not _DATA_DIR.is_relative_to(_PROJECT_ROOT):
    logger.warning("DATA_DIR %s escapes project root; falling back to default", _DATA_DIR)
    _DATA_DIR = Path(_DEFAULT_DATA_DIR).resolve()

GROUNDING_THRESHOLD = float(os.getenv("GROUNDING_THRESHOLD", "0.3"))
LLM_DEADLINE_SECONDS = float(os.getenv("LLM_DEADLINE_SECONDS", "45"))
SELF_REFLECT_ENABLED = os.getenv("SELF_REFLECT_ENABLED", "false").lower() == "true"
SELF_REFLECT_THRESHOLD = float(os.getenv("SELF_REFLECT_THRESHOLD", "0.4"))
_WORKFLOW_FLOWS_DIR = Path(__file__).resolve().parent / "workflows" / "flows"
_WORKFLOW_CANCEL_WORDS = {"cancel", "stop", "quit", "exit", "nevermind", "never mind"}
_WORKFLOW_SENSITIVE_SLOTS = {"nin", "company_reg", "ngo_reg", "phone", "email"}
_INFORMATIONAL_WORKFLOW_QUERY_RE = re.compile(
    r"\b(?:how\s+(?:do|can)\s+i|what\s+are\s+the\s+steps|what\s+is\s+the\s+process|"
    r"requirements?|procedure|where\s+do\s+i)\b",
    re.IGNORECASE,
)
_EXPLICIT_WORKFLOW_START_RE = re.compile(
    r"\b(?:start|begin|launch|open|proceed|continue|guide\s+me|walk\s+me\s+through|"
    r"help\s+me\s+(?:apply|register|file|submit))\b",
    re.IGNORECASE,
)
_ACCOUNT_QUERY_RE = re.compile(
    r"\b(my\s+tin|my\s+filing|my\s+return|my\s+account|my\s+balance)\b",
    re.IGNORECASE,
)
_CUSTOMS_QUERY_RE = re.compile(
    r"\b(import|export|customs|bill\s+of\s+lading|cif|tariff|clearance)\b",
    re.IGNORECASE,
)
_OBJECTION_QUERY_RE = re.compile(
    r"\b(dispute|objection|appeal|assessment|audit|fraud|lawyer|court)\b",
    re.IGNORECASE,
)
_TIN_REGISTRATION_QUERY_RE = re.compile(
    r"\b(?:register|get|obtain|apply)\b.*\btin\b|\btin\b.*\b(?:register|get|obtain|apply)\b",
    re.IGNORECASE,
)
_RETURN_FILING_QUERY_RE = re.compile(
    r"\b(?:file|submit|lodge)\b.*\b(?:return|returns)\b|\b(?:return|returns)\b.*\b(?:file|submit|lodge)\b",
    re.IGNORECASE,
)
_REGISTRATION_QUERY_RE = re.compile(
    r"\b(register|registration|get a tin|tin registration|apply for tin|obtain a tin)\b",
    re.IGNORECASE,
)

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
    personalization_context: str = "",
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
        personalization_context=personalization_context,
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
    personalization_context: str = "",
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, None]:
    """Stream LLM tokens through the shared circuit breaker.

    Mirrors :func:`_call_llm_with_deadline` for the SSE streaming path.
    Yields nothing when the breaker is OPEN, the generator raises,
    or no tokens are produced — the caller then falls back to
    returning the best-hit answer as a single event.

    Passing ``cancel_event`` lets the caller cooperatively stop token
    generation at the next yield boundary (e.g. when an SSE client
    disconnects or a WS client emits ``response.cancel``).  The
    underlying transformer thread cannot be killed; the next decoded
    token is the cancellation latency floor.
    """
    if not llm_module.is_available():
        return
    if not _LLM_CIRCUIT.allow_request():
        logger.warning("LLM circuit breaker OPEN — skipping stream")
        return

    saw_tokens = False
    try:
        for token in llm_module.generate_stream(
            query=query,
            passages=passages,
            conversation_history=conversation_history,
            locale=locale,
            personalization_context=personalization_context,
        ):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("LLM stream cancelled by caller")
                break
            if not token:
                continue
            saw_tokens = True
            yield token

        if saw_tokens:
            _LLM_CIRCUIT.record_success()
        else:
            # Empty stream counts as a soft failure — the breaker tracks
            # it so a continually empty worker eventually trips.
            _LLM_CIRCUIT.record_failure()
    except Exception:
        _LLM_CIRCUIT.record_failure()
        logger.exception("LLM streaming raised")
        return


def _call_llm_agentic(  # noqa: PLR0913 — all args are request-scoped config
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    *,
    tool_names: list[str] | None = None,
    max_iterations: int | None = None,
    personalization_context: str = "",
    tenant_id: str = "default",
    user_id: str = "",
    user_role: str = "public",
    granted_purposes: list[str] | None = None,
    deadline_s: float = LLM_DEADLINE_SECONDS * 2,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
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

    if max_iterations is None:
        max_iterations = _resolve_tool_max_iterations()

    future = _LLM_EXECUTOR.submit(
        llm_module.generate_with_tools,
        query=query,
        passages=passages or None,
        tool_names=tool_names,
        conversation_history=conversation_history,
        locale=locale,
        max_iterations=max_iterations,
        personalization_context=personalization_context,
        tenant_id=tenant_id,
        user_id=user_id,
        user_role=user_role,
        granted_purposes=granted_purposes or [],
        event_callback=event_callback,
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


# Phase 2: the historical default was 3 (UX defense — agentic UX was opaque
# so we capped runaway loops).  With Phase 2 events streamed to the client,
# the UI shows every iteration, so we raise the default to 10 and let
# operators override via LLM_TOOL_MAX_ITER up to a hard cap of 20.
_LLM_TOOL_MAX_ITER_HARD_CAP = 20
_LLM_TOOL_MAX_ITER_DEFAULT = 10


def _resolve_tool_max_iterations() -> int:
    """Resolve the per-turn tool-iteration budget from env."""
    raw = os.getenv("LLM_TOOL_MAX_ITER")
    if not raw:
        return _LLM_TOOL_MAX_ITER_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        logger.warning("LLM_TOOL_MAX_ITER=%r is not an int; using default", raw)
        return _LLM_TOOL_MAX_ITER_DEFAULT
    return max(1, min(value, _LLM_TOOL_MAX_ITER_HARD_CAP))


# Phase 6: per-turn deadline.  Default 120 s gives the agentic loop room
# for several tool round trips while still bounding worst-case latency.
# A turn that exceeds the deadline is cancelled cooperatively at the
# next token / await boundary.  Set to 0 to disable.
_TURN_DEADLINE_DEFAULT_S = 120


def _resolve_turn_deadline() -> float:
    raw = os.getenv("AGENTIC_TURN_DEADLINE_S")
    if raw is None:
        return _TURN_DEADLINE_DEFAULT_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("AGENTIC_TURN_DEADLINE_S=%r is not numeric; using default", raw)
        return _TURN_DEADLINE_DEFAULT_S


# ---------------------------------------------------------------------------
# Transport-agnostic chat turn generator (Phase 1 of the WS upgrade)
# ---------------------------------------------------------------------------

# Bounded queue for streamed tokens.  Unbounded queues are an OOM hazard
# when a slow client lets the LLM pump out faster than tokens are drained.
_STREAM_QUEUE_MAX = 256


def _apply_output_guards(
    model: Any,
    *,
    message: str,
    reply: str,
    hits: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    session_id: str | None,
    conversation_id: str,
    output_guard: Any,
    existing_handoff: dict[str, Any] | None = None,
    existing_ticket_id: str = "",
) -> dict[str, Any]:
    """Run the full post-generation guard pipeline for a streamed turn.

    Faithfulness → claim verification → response judge → grounded revision →
    escalation → handoff → ticket.  This is called by BOTH the token-streaming
    branch and the agentic (tool-use) branch of :func:`run_chat_turn` so the
    two paths enforce an identical safety bar — previously the agentic branch
    only computed faithfulness and skipped the judge, revision, claim
    verification, and escalation/handoff/ticket steps (P0-3).

    ``reply`` is assumed already PII-redacted + sanitized by the caller.  The
    returned ``reply`` may differ (a grounded revision was substituted); when
    ``revised`` is True the caller should emit a ``("revision", reply)`` event.
    """
    contexts = [h.get("text") or h.get("answer", "") for h in hits]
    faith = HybridRetriever.compute_faithfulness(reply, contexts)
    escalate, esc_reason = output_guard.should_escalate(faith, hits)

    claim_report: dict[str, Any] | None = None
    if reply and hits and citations:
        try:
            claim_report = verify_claims(reply, citations, hits)
        except Exception:
            logger.debug("claim verification failed", exc_info=True)
            claim_report = None

    response_judge = model._evaluate_response_judge(
        message=message,
        reply=reply,
        hits=hits,
        citations=citations,
        faithfulness_score=faith,
        escalation_required=escalate,
        escalation_reason=esc_reason,
        claim_report=claim_report,
    )

    revised = False
    if response_judge.get("decision") == "revise" and response_judge.get("revised_reply"):
        reply = output_guard.sanitize(output_guard.redact_pii(response_judge["revised_reply"]))
        faith = HybridRetriever.compute_faithfulness(reply, contexts)
        escalate, esc_reason = output_guard.should_escalate(faith, hits)
        response_judge["applied_revision"] = True
        response_judge["final_decision"] = "escalate" if escalate else "approve"
        revised = True
        # Re-verify the substituted text so a revision can't smuggle in
        # unsupported claims.
        if citations:
            try:
                claim_report = verify_claims(reply, citations, hits)
                if claim_report.get("decision") == "escalate":
                    response_judge["final_decision"] = "escalate"
            except Exception:
                logger.debug("post-revision claim verification failed", exc_info=True)
    else:
        response_judge["final_decision"] = response_judge.get("decision", "approve")

    if response_judge.get("final_decision") == "escalate":
        escalate = True
        if not esc_reason:
            esc_reason = "; ".join(response_judge.get("reasons") or [])
    if claim_report is not None:
        response_judge["claim_verification"] = claim_report
    response_judge.pop("revised_reply", None)

    handoff = existing_handoff
    if escalate and not handoff:
        handoff = model._build_handoff_packet(
            message=message,
            reason=esc_reason,
            conversation_history=conversation_history or None,
            hits=hits,
            faithfulness_score=faith,
        )
    ticket_id = existing_ticket_id
    if escalate and not ticket_id:
        ticket_id = model._maybe_create_ticket(
            reason=esc_reason,
            user_query=message,
            bot_reply=reply,
            session_id=session_id or None,
            conversation_id=conversation_id or "",
            priority=(handoff or {}).get("priority", "normal"),
            handoff=handoff,
            response_judge=response_judge,
        )

    return {
        "reply": reply,
        "faithfulness": faith,
        "escalate": escalate,
        "escalation_reason": esc_reason,
        "response_judge": response_judge,
        "handoff": handoff,
        "ticket_id": ticket_id,
        "revised": revised,
        "claim_report": claim_report,
    }


async def run_chat_turn(  # noqa: PLR0912, PLR0915 — long but mirrors SSE generator one-to-one
    model: Any,
    *,
    message: str,
    conversation_id: str | None,
    top_k: int,
    locale: str,
    session_id: str | None,
    request_id: str | None,
    user_id: str | None,
    tenant_id: str,
    should_continue: Callable[[], "asyncio.Future[bool] | bool"] | None = None,
    cancel_event: threading.Event | None = None,
    sentence_batching: bool = True,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    user_role: str = "public",
    granted_purposes: list[str] | None = None,
    conversation_history_override: list[dict[str, str]] | None = None,
    turn_deadline_s: float | None = None,
) -> "AsyncIterator[tuple[str, Any]]":
    """Run a single chat turn and stream event tuples to the caller.

    This is the transport-agnostic core that backs both SSE
    (``/v1/chat/stream``) and WebSocket (``/v2/chat/stream``).  Yielded
    tuples have the shape ``(event_type, payload)``:

    * ``("metadata", dict)`` — pre-token metadata (sources, citations, ...).
    * ``("token", str)``     — a sanitized text chunk.
    * ``("revision", str)``  — judge revised the full reply; new text.
    * ``("grounding", dict)``— faithfulness, escalation, handoff, judge.
    * ``("done", "")``       — terminal frame for a turn.
    * ``("error", dict)``    — payload has ``code`` and ``message``.
    * ``("_log", dict)``     — internal final frame for adapter logging.
                               Payload: ``{"result", "full_reply", "elapsed_ms"}``.
                               Adapters consume this and do not forward it.

    Parameters
    ----------
    sentence_batching:
        SSE keeps this ``True`` to match historical behaviour; WS sets
        ``False`` for lower TTFT (per-token frames pass through the same
        OutputGuard sanitiser).
    should_continue:
        Optional callable polled between awaits.  Returning ``False``
        (or an awaitable that resolves to ``False``) cancels the turn.
        SSE wires this to ``request.is_disconnected``; WS sets it from
        its socket-state check.
    cancel_event:
        Optional :class:`threading.Event`.  When set (by ``response.cancel``,
        client disconnect, or a future deadline), token generation halts at
        the next decoded token.  Created internally if absent.
    event_callback:
        Phase 2 hook; ignored in Phase 1.
    """
    import inspect

    from .guardrails import OutputGuard

    _output_guard = OutputGuard()
    t0 = time.perf_counter()
    full_reply = ""
    result: dict[str, Any] = {}
    cancel_event = cancel_event or threading.Event()
    deadline_s = turn_deadline_s if turn_deadline_s is not None else _resolve_turn_deadline()

    async def _should_stop() -> bool:
        if cancel_event.is_set():
            return True
        if deadline_s > 0 and (time.perf_counter() - t0) > deadline_s:
            logger.warning("run_chat_turn deadline %.1fs exceeded", deadline_s)
            cancel_event.set()
            return True
        if should_continue is None:
            return False
        outcome = should_continue()
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return not bool(outcome)

    try:
        yield (
            "retrieval.started",
            {"top_k": top_k, "query_preview": message[:200]},
        )

        result = await asyncio.to_thread(
            model.generate_retrieval_only,
            message=message,
            conversation_id=conversation_id,
            top_k=top_k,
            locale=locale,
            session_id=session_id or None,
            request_id=request_id,
            user_id=user_id or None,
            tenant_id=tenant_id,
            conversation_history_override=conversation_history_override,
        )

        yield (
            "retrieval.completed",
            {
                "hit_count": len(result.get("_hits", []) or []),
                "retrieval_mode": result.get("retrieval_mode"),
                "sources": result.get("sources", []),
            },
        )

        # Short-circuit branches: blocked / abstained / clarification /
        # workflow / escalated all skip the LLM stream and return a
        # single bundled payload.
        if result.get("retrieval_mode") in (
            "blocked",
            "abstained",
            "clarification",
            "workflow",
            "escalated",
        ):
            yield ("metadata", _metadata_payload(result, include_short_circuit=True))
            full_reply = result.get("reply", "")
            yield ("token", full_reply)
            yield ("done", "")
            yield (
                "_log",
                {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
            )
            return

        yield ("metadata", _metadata_payload(result, include_short_circuit=False))

        hits = result.get("_hits", [])
        conversation_history = result.get("_history", [])
        rewritten_query = result.get("_rewritten", message)
        personalization_context = result.get("_personalization_context", "")

        # ── Phase 2: optional agentic branch ─────────────────────────
        # When tool_use is enabled, run the bounded tool-calling loop
        # and surface every tool event as part of the same stream.  The
        # final answer text is yielded as a single token frame because
        # the agentic path produces a complete reply (no per-token
        # streaming for tool calls — that's a tradeoff documented in
        # docs/ws_chat_protocol.md).
        if llm_module.is_available() and hits and flags.is_enabled("tool_use"):
            async for event in _stream_agentic_turn(
                rewritten_query=rewritten_query,
                hits=hits,
                conversation_history=conversation_history,
                locale=locale,
                personalization_context=str(personalization_context or ""),
                tenant_id=tenant_id,
                user_id=user_id or "",
                user_role=user_role,
                granted_purposes=granted_purposes or [],
                cancel_event=cancel_event,
                _output_guard=_output_guard,
            ):
                if event[0] == "_full_reply":
                    full_reply = event[1]
                    continue
                yield event

            if full_reply:
                # P0-3: run the SAME post-generation guard pipeline the
                # token-streaming branch uses (judge + claim verification +
                # grounded revision + escalation/handoff/ticket), not just a
                # bare faithfulness score.
                guard = _apply_output_guards(
                    model,
                    message=message,
                    reply=full_reply,
                    hits=hits,
                    citations=result.get("citations", []),
                    conversation_history=conversation_history,
                    session_id=session_id,
                    conversation_id=result.get("conversation_id") or conversation_id or "",
                    output_guard=_output_guard,
                    existing_handoff=result.get("handoff"),
                    existing_ticket_id=result.get("ticket_id", ""),
                )
                full_reply = guard["reply"]
                if guard["revised"]:
                    yield ("revision", full_reply)
                result["handoff"] = guard["handoff"]
                result["response_judge"] = guard["response_judge"]
                result["ticket_id"] = guard["ticket_id"]
                yield (
                    "grounding",
                    {
                        "faithfulness_score": guard["faithfulness"],
                        "escalation_required": guard["escalate"],
                        "escalation_reason": guard["escalation_reason"],
                        "agent_role": result.get("agent_role", "rag_answerer"),
                        "handoff": guard["handoff"],
                        "response_judge": guard["response_judge"],
                        "next_actions": result.get("next_actions", []),
                        "ticket_id": guard["ticket_id"],
                    },
                )
                yield ("done", "")
                yield (
                    "_log",
                    {
                        "result": result,
                        "full_reply": full_reply,
                        "elapsed_ms": (time.perf_counter() - t0) * 1000,
                    },
                )
                return

        if llm_module.is_available() and hits:
            loop = asyncio.get_running_loop()
            token_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue(
                maxsize=_STREAM_QUEUE_MAX
            )

            def _pump_tokens() -> None:
                try:
                    for token in stream_llm_tokens(
                        query=rewritten_query,
                        passages=hits,
                        conversation_history=conversation_history or None,
                        locale=locale,
                        personalization_context=str(personalization_context or ""),
                        cancel_event=cancel_event,
                    ):
                        # Bounded queue: if the consumer is slow, block here
                        # rather than balloon memory.  call_soon_threadsafe
                        # schedules an awaitable put on the event loop.
                        future = asyncio.run_coroutine_threadsafe(
                            token_queue.put(("token", token)), loop
                        )
                        try:
                            future.result(timeout=30)
                        except Exception:
                            # Consumer gone / loop closed / queue full beyond
                            # backpressure window — stop pumping.
                            return
                finally:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            token_queue.put(("done", None)), loop
                        ).result(timeout=5)
                    except Exception:
                        pass

            threading.Thread(target=_pump_tokens, daemon=True).start()

            saw_streamed_token = False
            pending_stream_chunk = ""

            def _flush_stream_chunk(*, force: bool = False) -> str:
                nonlocal pending_stream_chunk, full_reply, saw_streamed_token
                if not pending_stream_chunk:
                    return ""
                if (
                    sentence_batching
                    and not force
                    and not re.search(r"(?:\n\s*\n|[.!?](?:\s|$))", pending_stream_chunk)
                ):
                    return ""
                sanitized = _output_guard.sanitize(pending_stream_chunk)
                pending_stream_chunk = ""
                if not sanitized:
                    return ""
                saw_streamed_token = True
                full_reply += sanitized
                return sanitized

            while True:
                if await _should_stop():
                    logger.info("chat turn cancelled mid-stream")
                    cancel_event.set()
                    return
                try:
                    event_type, payload = await asyncio.wait_for(
                        token_queue.get(), timeout=15
                    )
                except asyncio.TimeoutError:
                    # Heartbeat: yield an empty token tuple so adapters can
                    # send keepalive comments without inventing a new event.
                    yield ("_keepalive", "")
                    continue

                if event_type == "done":
                    final_chunk = _flush_stream_chunk(force=True)
                    if final_chunk:
                        yield ("token", final_chunk)
                    break

                pending_stream_chunk += payload or ""
                sanitized = _flush_stream_chunk()
                if not sanitized:
                    continue
                yield ("token", sanitized)

            if not saw_streamed_token:
                # Pump produced nothing (breaker open or empty stream).
                full_reply = result.get("reply", "")
                yield ("token", full_reply)
                yield ("done", "")
                yield (
                    "_log",
                    {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
                )
                return

            full_reply = _output_guard.redact_pii(full_reply)

            guard = _apply_output_guards(
                model,
                message=message,
                reply=full_reply,
                hits=hits,
                citations=result.get("citations", []),
                conversation_history=conversation_history,
                session_id=session_id,
                conversation_id=result.get("conversation_id") or conversation_id or "",
                output_guard=_output_guard,
                existing_handoff=result.get("handoff"),
                existing_ticket_id=result.get("ticket_id", ""),
            )
            full_reply = guard["reply"]
            faith = guard["faithfulness"]
            escalate = guard["escalate"]
            esc_reason = guard["escalation_reason"]
            response_judge = guard["response_judge"]
            handoff = guard["handoff"]
            ticket_id = guard["ticket_id"]
            if guard["revised"]:
                yield ("revision", full_reply)

            result["handoff"] = handoff
            result["response_judge"] = response_judge
            result["ticket_id"] = ticket_id

            yield (
                "grounding",
                {
                    "faithfulness_score": faith,
                    "escalation_required": escalate,
                    "escalation_reason": esc_reason,
                    "agent_role": result.get("agent_role", "rag_answerer"),
                    "handoff": handoff,
                    "response_judge": response_judge,
                    "next_actions": result.get("next_actions", []),
                    "ticket_id": ticket_id,
                },
            )

            try:
                model._cache.put(
                    rewritten_query,
                    {
                        "reply": full_reply,
                        "sources": result.get("sources", []),
                        "citations": result.get("citations", []),
                        "faithfulness_score": faith,
                        "retrieval_mode": result.get("retrieval_mode"),
                        "model": result.get("model"),
                        "conversation_id": result.get("conversation_id"),
                        "locale": result.get("locale"),
                        "escalation_required": escalate,
                        "escalation_reason": esc_reason,
                        "agent_role": result.get("agent_role", "rag_answerer"),
                        "handoff": handoff,
                        "response_judge": response_judge,
                        "next_actions": result.get("next_actions", []),
                        "ticket_id": ticket_id,
                    },
                )
            except Exception:
                logger.debug("Stream cache store failed", exc_info=True)
        else:
            full_reply = result.get("reply", "")
            yield ("token", full_reply)

        yield ("done", "")
        yield (
            "_log",
            {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
        )

    except Exception:
        logger.exception("run_chat_turn error")
        yield ("error", {"code": "internal", "message": "Internal server error"})
        yield ("done", "")
        yield (
            "_log",
            {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
        )


async def _stream_agentic_turn(  # noqa: PLR0913 — request-scoped configuration
    *,
    rewritten_query: str,
    hits: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str,
    tenant_id: str,
    user_id: str,
    user_role: str,
    granted_purposes: list[str],
    cancel_event: threading.Event,
    _output_guard: Any,
) -> "AsyncIterator[tuple[str, Any]]":
    """Run the agentic tool-call loop and stream its events.

    Wraps :func:`_call_llm_agentic` in a coroutine task so we can drain
    its ``event_callback`` events while it's still running.  Yields:

    * ``("tool_call.started", dict)``
    * ``("tool_call.completed", dict)``
    * ``("tool_call.error", dict)``
    * ``("iteration.started", dict)``
    * ``("iteration.final", dict)``
    * ``("token", str)`` — the final answer text (single chunk)
    * ``("_full_reply", str)`` — internal: full text for the caller
                                 to pass to the grounding stage.
    """
    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)

    def _emit(ev: dict[str, Any]) -> None:
        if cancel_event.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(event_queue.put(ev), loop).result(
                timeout=5
            )
        except Exception:
            logger.debug("agentic event drop", exc_info=True)

    async def _run_in_thread() -> dict[str, Any]:
        return await asyncio.to_thread(
            _call_llm_agentic,
            query=rewritten_query,
            passages=hits,
            conversation_history=conversation_history,
            locale=locale,
            personalization_context=personalization_context,
            tenant_id=tenant_id,
            user_id=user_id,
            user_role=user_role,
            granted_purposes=granted_purposes,
            event_callback=_emit,
        )

    agentic_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(_run_in_thread())

    try:
        while not agentic_task.done():
            try:
                ev = await asyncio.wait_for(event_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if cancel_event.is_set():
                    agentic_task.cancel()
                    return
                continue
            yield (ev.get("type", "tool_event"), ev)

        # Drain anything queued after the task finished.
        while not event_queue.empty():
            ev = event_queue.get_nowait()
            yield (ev.get("type", "tool_event"), ev)

        agentic = agentic_task.result()
    except Exception:
        logger.exception("agentic stream failed")
        yield ("error", {"code": "agentic_failed", "message": "Tool loop error"})
        return

    text = (agentic or {}).get("text", "")
    if not text:
        # Nothing to say — yield an empty marker and let the caller
        # fall back to the regular streaming path on the next branch.
        return

    sanitized = _output_guard.sanitize(_output_guard.redact_pii(text))
    if sanitized:
        yield ("token", sanitized)
        yield ("_full_reply", sanitized)


def _metadata_payload(result: dict[str, Any], *, include_short_circuit: bool) -> dict[str, Any]:
    """Build the ``metadata`` event payload from a retrieval result."""
    payload: dict[str, Any] = {
        "sources": result.get("sources", []),
        "citations": result.get("citations", []),
        "retrieval_mode": result.get("retrieval_mode"),
        "model": result.get("model"),
        "conversation_id": result.get("conversation_id"),
        "locale": result.get("locale"),
        "agent_role": result.get("agent_role", "rag_answerer"),
        "response_judge": result.get("response_judge"),
        "next_actions": result.get("next_actions", []),
        "ticket_id": result.get("ticket_id", ""),
    }
    if include_short_circuit:
        payload.update(
            {
                "faithfulness_score": result.get("faithfulness_score"),
                "escalation_required": result.get("escalation_required", False),
                "escalation_reason": result.get("escalation_reason", ""),
                "workflow": result.get("workflow"),
                "handoff": result.get("handoff"),
            }
        )
    return payload


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


_STOP_WORDS = frozenset(
    "a an the is are was were be been am do does did will would shall should "
    "can could may might must have has had of in on at to for with by from "
    "and or not no nor but so if then than that this these those it its i me "
    "my we our you your he she they them their what which who whom how when "
    "where why all each every any some".split()
)


def _simple_search(
    query: str,
    faq_index: dict[str, list[dict[str, str]]],
    top_k: int = 4,
) -> list[dict[str, str]]:
    """Keyword-based retrieval fallback: score each FAQ by content-word overlap.

    Stop words are excluded so that domain terms (TIN, VAT, register, etc.)
    dominate the scoring.  Each returned dict includes a ``_overlap`` key.
    """
    query_tokens = set(query.lower().split()) - _STOP_WORDS
    if not query_tokens:
        query_tokens = set(query.lower().split())  # fallback: keep all
    scored: list[tuple[float, dict[str, str]]] = []

    for entries in faq_index.values():
        for entry in entries:
            q_tokens = set(entry["question"].lower().split()) - _STOP_WORDS
            a_tokens = set(entry["answer"].lower().split()) - _STOP_WORDS
            overlap = len(query_tokens & (q_tokens | a_tokens))
            if overlap > 0:
                scored.append((overlap, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for overlap, item in scored[:top_k]:
        out = dict(item)
        out["_overlap"] = overlap
        results.append(out)
    return results


def _faq_hits_to_retrieval_hits(entries: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Convert FAQ index rows into the retrieval-hit shape used downstream."""
    hits: list[dict[str, Any]] = []
    for entry in entries:
        tag = str(entry.get("tag") or "")
        hits.append(
            {
                "text": f"Question: {entry['question']}\nAnswer: {entry['answer']}",
                "answer": entry["answer"],
                "question": entry["question"],
                "source": entry["source"],
                "chunk_id": "",
                "page": "",
                "section": tag,
                "doc_type": "csv",
                "score_rrf": float(entry.get("_overlap", 0.0) or 0.0),
            }
        )
    return hits


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
        self.name = llm_module.LLM_MODEL or "unknown"
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

        self._workflow_count = 0
        if _WORKFLOW_FLOWS_DIR.is_dir():
            try:
                self._workflow_count = auto_load_flows(_WORKFLOW_FLOWS_DIR)
            except Exception:
                logger.exception("Workflow auto-load failed from %s", _WORKFLOW_FLOWS_DIR)

        mode = "hybrid (Qdrant)" if self._retriever_ready else "keyword-only (fallback)"
        gen_mode = f"LLM ({self.name})" if self._llm_available else "FAQ lookup (fallback)"
        logger.info(
            "ChatModel initialised – %s mode, %s gen, %d tags, %d workflows",
            mode,
            gen_mode,
            len(self._faq_index),
            self._workflow_count,
        )

    @staticmethod
    def _workflow_view(
        session: WorkflowSession,
        *,
        name: str,
        status: str,
        pending_slot: str = "",
    ) -> dict[str, Any]:
        """Return UI-safe workflow metadata without echoing sensitive slot values."""
        filled_slots = [k for k in session.slots if session.slots.get(k) not in ("", None)]
        masked_slots = sorted(set(filled_slots) & _WORKFLOW_SENSITIVE_SLOTS)
        visible_slots = sorted(set(filled_slots) - _WORKFLOW_SENSITIVE_SLOTS)
        return {
            "id": session.workflow_id,
            "name": name,
            "status": status,
            "current_step_idx": session.current_step_idx,
            "filled_slots": visible_slots,
            "masked_slots": masked_slots,
            "pending_slot": pending_slot,
            "completed": status == "completed",
        }

    @staticmethod
    def _default_next_actions(
        *,
        agent_role: str,
        workflow: dict[str, Any] | None = None,
        handoff: dict[str, Any] | None = None,
        escalation_required: bool = False,
    ) -> list[str]:
        if workflow and workflow.get("status") == "active":
            return [
                "Reply with the requested detail to continue the guided process.",
                "Send 'cancel' if you want to leave this workflow and ask a different question.",
            ]
        if handoff:
            return [
                "Prepare the listed reference details before speaking to a URA officer.",
                "Use the URA Contact Centre if you need immediate human assistance.",
            ]
        if agent_role == "clarification_agent":
            return ["Reply with the missing detail so I can answer more precisely."]
        if escalation_required:
            return [
                "Review the cited URA sources before acting on this answer.",
                "Ask for human support if your case is account-specific or time-sensitive.",
            ]
        return []

    @staticmethod
    def _has_inline_citations(reply: str) -> bool:
        return bool(re.search(r"\[\d+\]", reply or ""))

    def _load_personalization_state(self, user_id: str | None) -> dict[str, Any] | None:
        """Return consent-gated profile + memory context for personalization."""
        if not user_id or not flags.is_enabled("memory_enabled"):
            return None

        try:
            snapshot = get_memory_service().read_all(user_id, purpose="personalization")
        except Exception:
            logger.debug("personalization read failed", exc_info=True)
            return None

        if not snapshot.consent_granted:
            return None

        profile = db.get_user_profile(user_id) or {}
        lines: list[str] = []
        prefill_slots: dict[str, Any] = {}

        display_name = str(profile.get("display_name", "")).strip()
        if display_name:
            lines.append(f"- Preferred name: {display_name}")

        taxpayer_type = str(profile.get("taxpayer_type", "")).strip().lower()
        if taxpayer_type and taxpayer_type != "unknown":
            lines.append(f"- Taxpayer type: {taxpayer_type.replace('_', ' ')}")
            prefill_slots["taxpayer_type"] = taxpayer_type

        detail_level = str(profile.get("detail_level", "")).strip().lower()
        if detail_level:
            lines.append(f"- Preferred explanation depth: {detail_level}")

        registered = profile.get("registered_tax_types") or []
        if isinstance(registered, list) and registered:
            lines.append(f"- Registered tax types: {', '.join(str(t) for t in registered[:4])}")

        fact_labels = {
            "taxpayer_type": "Known taxpayer type",
            "industry": "Industry",
            "registered_tax": "Registered tax",
            "registered_vat": "VAT registration",
            "primary_language": "Preferred language",
        }
        seen_facts: set[tuple[str, str]] = set()
        for fact in snapshot.facts[:5]:
            label = fact_labels.get(fact.category)
            value = str(fact.object_value).strip()
            if not label or not value:
                continue
            key = (label, value.lower())
            if key in seen_facts:
                continue
            seen_facts.add(key)
            lines.append(f"- {label}: {value}")
            if fact.category == "taxpayer_type" and "taxpayer_type" not in prefill_slots:
                prefill_slots["taxpayer_type"] = value.lower()

        for episode in snapshot.episodic[:2]:
            summary = str(episode.get("summary", "")).strip()
            topic = str(episode.get("topic_tag", "")).strip()
            if not summary:
                continue
            summary = summary[:140] + ("..." if len(summary) > 140 else "")
            if topic:
                lines.append(f"- Recent topic ({topic}): {summary}")
            else:
                lines.append(f"- Recent topic: {summary}")

        working = snapshot.working if isinstance(snapshot.working, dict) else {}
        last_topic = str(working.get("last_topic", "")).strip()
        if last_topic:
            lines.append(f"- Last conversation topic: {last_topic}")

        prompt_context = "\n".join(lines)
        if not prompt_context and not prefill_slots:
            return None

        return {
            "consent_granted": True,
            "profile": profile,
            "prefill_slots": prefill_slots,
            "prompt_context": prompt_context,
        }

    @staticmethod
    def _apply_personalization_to_workflow(
        session: WorkflowSession,
        personalization: dict[str, Any] | None,
    ) -> None:
        if not personalization:
            return
        for slot_name, value in (personalization.get("prefill_slots") or {}).items():
            if slot_name and value not in ("", None) and slot_name not in session.slots:
                session.slots[slot_name] = value

    @staticmethod
    def _content_tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower())) - _STOP_WORDS

    @staticmethod
    def _extract_grounded_answer_text(hit: dict[str, Any]) -> str:
        answer = str(hit.get("answer", "") or "").strip()
        if answer:
            return " ".join(answer.split())
        text = str(hit.get("text", "") or "").strip()
        if text.lower().startswith("question:") and "\nanswer:" in text.lower():
            parts = re.split(r"\nanswer:\s*", text, maxsplit=1, flags=re.IGNORECASE)
            text = parts[1] if len(parts) == 2 else text
        return " ".join(text.split())

    @classmethod
    def _build_grounded_revision(
        cls,
        hits: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        query: str = "",
    ) -> str:
        excerpts: list[str] = []
        query_tokens = cls._content_tokens(query)

        def rank(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
            idx, hit = item
            body = f"{hit.get('question') or ''} {hit.get('answer') or hit.get('text') or ''}"
            tokens = cls._content_tokens(body)
            overlap = len(query_tokens & tokens) if query_tokens else 0
            priority = 0
            if "tin" in query.lower() and "instant tin" in body.lower():
                priority += 8
            if "return" in query.lower() and "file a return" in body.lower():
                priority += 8
            if str(hit.get("source", "")).lower() in {
                "ura_objection_appeals_faqs.csv",
                "ura_double_taxation_agreements_faqs.csv",
            }:
                priority -= 8
            return (overlap + priority + float(hit.get("score_rrf") or 0.0) / 100.0, -idx)

        ranked_hits = [hit for _, hit in sorted(enumerate(hits), key=rank, reverse=True)]
        for hit in ranked_hits[:2]:
            text = cls._extract_grounded_answer_text(hit)
            if not text:
                continue
            trimmed = text[:800].rsplit(" ", 1)[0] if len(text) > 800 else text
            ref = ""
            for c in citations:
                if str(c.get("source", "")) == str(hit.get("source", "")):
                    ref = str(c.get("ref", "")).strip()
                    break
            if ref:
                excerpts.append(f"{trimmed} {ref}".strip())
            else:
                excerpts.append(trimmed)
        if not excerpts:
            return ""
        if len(excerpts) == 1:
            return f"Based on the URA guidance I retrieved, {excerpts[0]}".strip()
        return "Based on the URA guidance I retrieved:\n\n- " + "\n- ".join(excerpts)

    def _finalize_reply(self, reply: str) -> str:
        """Apply response-side safety cleanup to generated, revised, and cached text."""
        cleaned = self._output_guard.redact_pii(str(reply or ""))
        cleaned = self._output_guard.sanitize(cleaned)
        leakage = self._output_guard.check_prompt_leakage(cleaned)
        return leakage.sanitized_text

    def _finalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return a shallow copy with a production-safe user-facing reply."""
        out = dict(result)
        out["reply"] = self._finalize_reply(str(out.get("reply", "")))
        return out

    def _priority_faq_hits(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        """Inject high-precision FAQ hits for common procedures that reranking can miss."""
        if not _TIN_REGISTRATION_QUERY_RE.search(query):
            if not _RETURN_FILING_QUERY_RE.search(query):
                return []
            candidates: list[dict[str, str]] = []
            for tag in ("processes_systems", "taxpayer_starter_pack", "taxation_handbook_fy2025_26"):
                for entry in self._faq_index.get(tag, []):
                    text = f"{entry['question']} {entry['answer']}".lower()
                    if "return" in text and any(
                        term in text for term in ("file a return", "return filing", "due")
                    ):
                        enriched = dict(entry)
                        enriched["tag"] = tag
                        enriched["_overlap"] = "99"
                        candidates.append(enriched)

            candidates.sort(
                key=lambda e: (
                    "how do i file a return" in e["question"].lower(),
                    "what is a return filing" in e["question"].lower(),
                    len(e["answer"]),
                ),
                reverse=True,
            )
            return _faq_hits_to_retrieval_hits(candidates[:top_k])

        candidates: list[dict[str, str]] = []
        for tag in ("instant_tin_application", "processes_systems", "taxpayer_starter_pack"):
            for entry in self._faq_index.get(tag, []):
                text = f"{entry['question']} {entry['answer']}".lower()
                if "tin" in text and any(
                    term in text for term in ("register", "registration", "apply", "get a tin")
                ):
                    enriched = dict(entry)
                    enriched["tag"] = tag
                    enriched["_overlap"] = "99"
                    candidates.append(enriched)

        if not candidates:
            return []

        def score(entry: dict[str, str]) -> tuple[int, int]:
            question = entry["question"].lower()
            text = f"{entry['question']} {entry['answer']}".lower()
            exact = int("how do i apply for an instant tin" in question)
            procedure = int("go to ura.go.ug" in text and "get a tin" in text)
            return (exact + procedure, len(text))

        candidates.sort(key=score, reverse=True)
        return _faq_hits_to_retrieval_hits(candidates[:top_k])

    def _deterministic_procedure_reply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> str:
        """Return vetted procedural answers for common tasks without LLM synthesis."""
        citation_by_source: dict[str, str] = {}
        for c in citations:
            citation_by_source.setdefault(str(c.get("source", "")), str(c.get("ref", "")).strip())

        if _TIN_REGISTRATION_QUERY_RE.search(query):
            apply_hit = next(
                (
                    h
                    for h in hits
                    if h.get("source") == "ura_instant_tin_application_faqs.csv"
                    and "apply for an instant tin" in str(h.get("question", "")).lower()
                ),
                None,
            )
            help_hit = next(
                (
                    h
                    for h in hits
                    if h.get("source") == "ura_instant_tin_application_faqs.csv"
                    and "contact" in str(h.get("question", "")).lower()
                ),
                None,
            )
            if apply_hit:
                ref = citation_by_source.get(str(apply_hit.get("source", "")), "[1]")
                contact = (
                    "For help, call URA toll-free 0800 117 000 / 0800 217 000, "
                    "WhatsApp 0772 140 000, or use https://ura.go.ug."
                )
                if help_hit:
                    contact_ref = citation_by_source.get(str(help_hit.get("source", "")), ref)
                    contact = f"{self._extract_grounded_answer_text(help_hit)} {contact_ref}"
                return (
                    "To register for an instant TIN, go to ura.go.ug, click Get a TIN, "
                    "choose Instant TIN, select Individual, enter your NIN and personal details, "
                    f"confirm you are not a robot, and submit. {ref}\n\n{contact}"
                )

        if _RETURN_FILING_QUERY_RE.search(query):
            file_hit = next(
                (
                    h
                    for h in hits
                    if h.get("source") == "ura_processes_systems_faqs.csv"
                    and "how do i file a return" in str(h.get("question", "")).lower()
                ),
                None,
            )
            due_hit = next(
                (
                    h
                    for h in hits
                    if "return" in str(h.get("question", "")).lower()
                    and "due" in str(h.get("question", "")).lower()
                ),
                None,
            )
            if file_hit:
                ref = citation_by_source.get(str(file_hit.get("source", "")), "[1]")
                lines = [
                    f"To file your annual tax return: {self._extract_grounded_answer_text(file_hit)} {ref}",
                ]
                if due_hit:
                    due_ref = citation_by_source.get(str(due_hit.get("source", "")), ref)
                    lines.append(f"Due date guidance: {self._extract_grounded_answer_text(due_hit)} {due_ref}")
                lines.append(
                    "For help, contact URA at https://ura.go.ug, toll-free 0800 117 000 / "
                    "0800 217 000, or WhatsApp 0772 140 000."
                )
                return "\n\n".join(lines)

        return ""

    def _evaluate_response_judge(
        self,
        *,
        message: str,
        reply: str,
        hits: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        faithfulness_score: float | None,
        escalation_required: bool,
        escalation_reason: str,
        claim_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Classify the draft reply as approve / revise / escalate."""
        reasons: list[str] = []
        decision = "approve"

        if escalation_required:
            decision = "escalate"
            if escalation_reason:
                reasons.append(escalation_reason)

        if decision != "escalate" and _ACCOUNT_QUERY_RE.search(message):
            decision = "escalate"
            reasons.append("account-specific query needs authenticated lookup or human review")

        if not reply.strip():
            reasons.append("reply was empty")

        if decision != "escalate" and hits and citations and not self._has_inline_citations(reply):
            reasons.append("reply did not expose visible citation markers")
            # Only revise if faithfulness is also below threshold — a well-grounded
            # answer without explicit [N] markers is acceptable.
            if faithfulness_score is not None and faithfulness_score < 0.5:
                decision = "revise"

        if faithfulness_score is not None:
            if faithfulness_score < 0.2:
                decision = "escalate"
                reasons.append("grounding confidence is critically low")
            elif decision != "escalate" and faithfulness_score < max(GROUNDING_THRESHOLD, 0.35):
                decision = "revise"
                reasons.append("grounding confidence is below the release threshold")

        if claim_report:
            claim_decision = str(claim_report.get("decision", "approve"))
            if claim_decision == "escalate":
                decision = "escalate"
                reasons.append("claim verification found unsupported factual claims")
            elif claim_decision == "revise" and decision != "escalate":
                decision = "revise"
                if claim_report.get("uncited_claims"):
                    reasons.append("claim verification found uncited factual claims")
                if claim_report.get("unsupported_claims"):
                    reasons.append("claim verification found weakly supported factual claims")

        revised_reply = ""
        if decision == "revise":
            revised_reply = self._build_grounded_revision(hits, citations, message)
            if not revised_reply:
                decision = "escalate"
                reasons.append("no deterministic grounded fallback was available")

        if faithfulness_score is None:
            confidence_band = "medium" if decision == "approve" else "low"
        elif faithfulness_score >= 0.65 and decision == "approve":
            confidence_band = "high"
        elif faithfulness_score >= 0.35 and decision != "escalate":
            confidence_band = "medium"
        else:
            confidence_band = "low"

        return {
            "decision": decision,
            "final_decision": decision,
            "applied_revision": False,
            "reasons": reasons,
            "confidence_band": confidence_band,
            "revised_reply": revised_reply,
        }

    def _maybe_create_ticket(
        self,
        *,
        reason: str,
        user_query: str,
        bot_reply: str,
        session_id: str | None,
        conversation_id: str,
        priority: str = "normal",
        handoff: dict[str, Any] | None = None,
        response_judge: dict[str, Any] | None = None,
    ) -> str:
        """Persist a structured escalation ticket when the queue is enabled."""
        if not flags.is_enabled("ticket_queue"):
            return ""
        if not reason and not handoff:
            final_decision = str((response_judge or {}).get("final_decision", "")).lower()
            if final_decision != "escalate":
                return ""
        try:
            ticket = db.create_ticket(
                reason=reason,
                user_query=self.redact_for_storage(user_query),
                bot_reply=self.redact_for_storage(bot_reply),
                session_id=session_id or None,
                conversation_id=conversation_id,
                priority=priority,
                handoff=handoff,
                response_judge=response_judge,
            )
            ticket_id = ticket.get("id", "")
            if handoff is not None and ticket_id:
                handoff["ticket_id"] = ticket_id
            return ticket_id
        except Exception:
            logger.exception("failed to persist escalation ticket")
            return ""

    def _persist_personalization_turn(
        self,
        *,
        user_id: str | None,
        conversation_id: str,
        message: str,
        reply: str,
        agent_role: str,
        personalization: dict[str, Any] | None,
        workflow: dict[str, Any] | None = None,
    ) -> None:
        """Update working memory and absorb the latest consented turn."""
        if not user_id or not personalization or not personalization.get("consent_granted"):
            return
        try:
            memsvc = get_memory_service()
            memsvc.update_working(
                user_id,
                last_topic=workflow.get("name") if workflow else agent_role,
                last_agent_role=agent_role,
                last_conversation_id=conversation_id,
            )
            turns = [
                {"role": "user", "content": message},
                {"role": "assistant", "content": reply},
            ]
            memsvc.absorb_conversation(user_id, conversation_id, turns)
        except Exception:
            logger.debug("personalization persistence failed", exc_info=True)

    @staticmethod
    def _handoff_topic(message: str, reason: str = "") -> str:
        text = f"{message} {reason}".strip()
        if _OBJECTION_QUERY_RE.search(text):
            return "objection_or_dispute"
        if _ACCOUNT_QUERY_RE.search(text):
            return "account_specific"
        if _CUSTOMS_QUERY_RE.search(text):
            return "customs"
        if _REGISTRATION_QUERY_RE.search(text):
            return "registration"
        return "general_tax_support"

    @classmethod
    def _handoff_required_details(cls, topic: str) -> list[str]:
        lookup = {
            "objection_or_dispute": [
                "assessment or objection reference number",
                "tax type and filing period",
                "supporting notices or correspondence",
            ],
            "account_specific": [
                "TIN or registered taxpayer email",
                "tax type and return period",
                "any reference number shown in the URA portal",
            ],
            "customs": [
                "entry or declaration number",
                "consignment or bill of lading reference",
                "goods description and customs value details",
            ],
            "registration": [
                "taxpayer type (individual, company, or NGO)",
                "registration number or NIN where applicable",
                "preferred callback channel",
            ],
            "general_tax_support": [
                "the exact URA process you are trying to complete",
                "the tax type involved",
                "any deadline or notice you are working against",
            ],
        }
        return lookup.get(topic, lookup["general_tax_support"])

    def _build_handoff_packet(
        self,
        *,
        message: str,
        reason: str,
        conversation_history: list[dict[str, str]] | None = None,
        hits: list[dict[str, Any]] | None = None,
        faithfulness_score: float | None = None,
        ticket_id: str = "",
    ) -> dict[str, Any]:
        """Create a structured, UI-safe packet for human triage."""
        topic = self._handoff_topic(message, reason)
        priority = "normal"
        lowered_reason = reason.lower()
        if any(term in lowered_reason for term in ("legal", "dispute", "appeal", "audit", "fraud")):
            priority = "high"
        if faithfulness_score is not None and faithfulness_score < 0.2:
            priority = "high"
        if topic == "account_specific" and priority == "normal":
            priority = "high"

        redacted_context = [
            self.redact_for_storage(turn.get("user_message", ""))[:180]
            for turn in (conversation_history or [])[-2:]
            if turn.get("user_message")
        ]
        sources = []
        for hit in (hits or [])[:3]:
            source = str(hit.get("source", "")).strip()
            if source and source not in sources:
                sources.append(source)

        summary = (
            f"User needs human help with {topic.replace('_', ' ')}. "
            f"Reason: {reason or 'low-confidence automated handling'}."
        )
        if redacted_context:
            summary += f" Recent context: {' | '.join(redacted_context)}."
        if faithfulness_score is not None:
            summary += f" Faithfulness score: {faithfulness_score:.2f}."

        return {
            "summary": summary,
            "topic": topic,
            "priority": priority,
            "ticket_id": ticket_id,
            "required_details": self._handoff_required_details(topic),
            "recent_context": redacted_context,
            "sources_reviewed": sources,
            "contact_channels": [
                "https://ura.go.ug",
                "0800 117 000 / 0800 217 000",
                "WhatsApp 0772 140 000",
            ],
        }

    @staticmethod
    def _handoff_packet_text(packet: dict[str, Any]) -> str:
        lines = [
            f"Handoff summary: {packet.get('summary', '')}",
            f"Priority: {packet.get('priority', 'normal')}",
        ]
        required = packet.get("required_details") or []
        if required:
            lines.append("Required details: " + "; ".join(required))
        sources = packet.get("sources_reviewed") or []
        if sources:
            lines.append("Sources reviewed: " + ", ".join(sources))
        return "\n".join(lines)[:2000]

    @staticmethod
    def _restore_workflow_session(row: dict[str, Any]) -> WorkflowSession:
        return WorkflowSession(
            workflow_id=str(row.get("workflow_id", "")),
            current_step_idx=int(row.get("current_step_idx") or 0),
            slots=dict(row.get("slots") or {}),
            completed=str(row.get("status", "")) == "completed",
        )

    def _advance_workflow(
        self,
        session: WorkflowSession,
        user_input: str,
    ) -> tuple[Any, list[str]]:
        """Advance a workflow and execute any deterministic tool steps inline."""
        tool_messages: list[str] = []
        turn = WorkflowRegistry.advance(session, user_input)
        while turn.tool_call:
            try:
                from .mcp import get_client  # noqa: PLC0415

                call = get_client().call_tool(
                    turn.tool_call.get("name", ""),
                    turn.tool_call.get("arguments", {}) or {},
                    user_role="public",
                )
                result = call.result
            except Exception:
                logger.exception("workflow tool execution failed")
                result = {"ok": False, "error": "workflow tool execution failed"}
            explanation = result.get("explanation") or result.get("message") or ""
            if explanation:
                tool_messages.append(str(explanation))
            turn = WorkflowRegistry.advance(session, "")
        return turn, tool_messages

    def _maybe_handle_workflow(
        self,
        *,
        message: str,
        rewritten: str,
        thread_id: str,
        locale: str,
        personalization: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Start or continue a durable guided workflow when appropriate."""
        if not flags.is_enabled("workflows") or self._workflow_count <= 0:
            return None

        persisted = db.get_workflow_session(thread_id)
        if persisted and persisted.get("status") == "active":
            session = self._restore_workflow_session(persisted)
            self._apply_personalization_to_workflow(session, personalization)
            wf = WorkflowRegistry.get(session.workflow_id)
            if wf is None:
                db.complete_workflow_session(thread_id, status="cancelled")
                return None
            user_input = (message or "").strip()
            if user_input.lower() in _WORKFLOW_CANCEL_WORDS:
                db.complete_workflow_session(thread_id, status="cancelled")
                workflow = self._workflow_view(
                    session,
                    name=wf.name,
                    status="cancelled",
                )
                return {
                    "reply": (
                        f"I've stopped the {wf.name} workflow. Ask a new URA question whenever "
                        "you're ready."
                    ),
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "workflow",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "workflow_guide",
                    "workflow": workflow,
                    "next_actions": ["Ask a new question or restart the guided process later."],
                }

            turn, tool_messages = self._advance_workflow(session, user_input)
            status = "completed" if (session.completed or turn.is_complete) else "active"
            prompt = turn.question or persisted.get("last_prompt", "") or f"Let's continue the {wf.name} workflow."
            if tool_messages:
                prompt = "\n\n".join(tool_messages + [prompt]).strip()
            if status == "completed":
                db.upsert_workflow_session(
                    thread_id,
                    session.workflow_id,
                    session.current_step_idx,
                    session.slots,
                    status="completed",
                    last_prompt=prompt,
                )
            else:
                db.upsert_workflow_session(
                    thread_id,
                    session.workflow_id,
                    session.current_step_idx,
                    session.slots,
                    status="active",
                    last_prompt=prompt,
                )
            workflow = self._workflow_view(
                session,
                name=wf.name,
                status=status,
                pending_slot=turn.slot_name,
            )
            return {
                "reply": prompt,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "workflow",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "workflow_guide",
                "workflow": workflow,
                "next_actions": self._default_next_actions(
                    agent_role="workflow_guide",
                    workflow=workflow,
                ),
            }

        matched = WorkflowRegistry.match_trigger(message) or WorkflowRegistry.match_trigger(rewritten)
        if matched is None:
            return None
        combined_query = f"{message or ''} {rewritten or ''}".strip()
        if (
            _INFORMATIONAL_WORKFLOW_QUERY_RE.search(combined_query)
            and not _EXPLICIT_WORKFLOW_START_RE.search(combined_query)
        ):
            return None

        session = WorkflowRegistry.create_session(matched.id)
        if session is None:
            return None
        self._apply_personalization_to_workflow(session, personalization)

        turn, tool_messages = self._advance_workflow(session, "")
        prompt = turn.question or f"Let's start the {matched.name} workflow."
        if tool_messages:
            prompt = "\n\n".join(tool_messages + [prompt]).strip()
        status = "completed" if (session.completed or turn.is_complete) else "active"
        db.upsert_workflow_session(
            thread_id,
            session.workflow_id,
            session.current_step_idx,
            session.slots,
            status=status,
            last_prompt=prompt,
        )
        workflow = self._workflow_view(
            session,
            name=matched.name,
            status=status,
            pending_slot=turn.slot_name,
        )
        reply = (
            f"I can guide you through the {matched.name} process step by step.\n\n{prompt}"
            if prompt
            else f"I can guide you through the {matched.name} process step by step."
        )
        return {
            "reply": reply,
            "sources": [],
            "citations": [],
            "faithfulness_score": None,
            "retrieval_mode": "workflow",
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": False,
            "escalation_reason": "",
            "agent_role": "workflow_guide",
            "workflow": workflow,
            "next_actions": self._default_next_actions(
                agent_role="workflow_guide",
                workflow=workflow,
            ),
        }

    # -- Chat (RAG) ---------------------------------------------------------
    def generate(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 6,
        locale: str = "en",
        session_id: str | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        user_role: str = "public",
        granted_purposes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a grounded, cited answer via hybrid retrieval + guardrails."""
        t0 = time.perf_counter()
        thread_id = conversation_id or str(uuid.uuid4())
        agent_role = "rag_answerer"

        with trace_rag_pipeline(message, request_id=request_id) as trace_ctx:
            timings = trace_ctx["timings"]
            trace_ctx["user_id"] = user_id or ""
            trace_ctx["tenant_id"] = tenant_id or "default"

            # 0. Multi-turn memory — fetch recent conversation history (Phase 4)
            conversation_history: list[dict[str, str]] = []
            history_session_id = None if conversation_id else session_id
            if conversation_id or history_session_id:
                try:
                    conversation_history = db.get_recent_turns(
                        session_id=history_session_id,
                        conversation_id=conversation_id,
                        limit=5,
                    )
                except Exception:
                    logger.debug("Failed to fetch conversation history", exc_info=True)

            # 0b. Query rewriting — spell correction, abbreviation expansion,
            #     coreference resolution from history (Phase 4)
            with trace_stage("query_rewrite", timings=timings):
                rewritten = rewrite_query(message, history=conversation_history or None)

            # 0c. Language detection — auto-detect user's language for
            #     adapter routing and locale-aware responses.
            if locale == "en":
                with trace_stage("lang_detect", timings=timings):
                    detected_locale = detect_language(message)
                    if detected_locale != "en":
                        locale = detected_locale
                        logger.info("Auto-detected locale: %s", locale)

            personalization = self._load_personalization_state(user_id)
            cache_allowed = personalization is None

            # 1. Input guardrails FIRST (OWASP LLM01) — check original message
            with trace_stage("input_guard", timings=timings):
                guard = self._input_guard.check(message)
            if not guard.allowed:
                blocked = {
                    "reply": guard.reason,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "blocked",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "safety_guard",
                    "next_actions": ["Rephrase your request as a legitimate URA support question."],
                }
                self._audit_turn(
                    message=message, result=blocked, session_id=session_id, trace_ctx=trace_ctx
                )
                return blocked

            if flags.is_enabled("workflows"):
                with trace_stage("workflow_router", timings=timings):
                    workflow_result = self._maybe_handle_workflow(
                        message=message,
                        rewritten=rewritten,
                        thread_id=thread_id,
                        locale=locale,
                        personalization=personalization,
                    )
                if workflow_result:
                    trace_ctx["agent_role"] = "workflow_guide"
                    self._audit_turn(
                        message=message,
                        result=workflow_result,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return workflow_result

            # 1a2. Greeting detection — always active, independent of agentic_mode
            _q_lower = message.strip().lower().strip("!.?,")
            _q_words = message.strip().split()
            if len(_q_words) <= 3 and (
                _q_lower in _GREETING_WORDS
                or _q_lower in _GREETING_PHRASES
                or all(w.lower().strip("!.?,") in _GREETING_WORDS for w in _q_words)
            ):
                greeted = {
                    "reply": (
                        "Hello! I'm the URA Digital Assistant. I can help you with "
                        "tax registration, filing returns, payments, customs, and more. "
                        "What would you like to know?"
                    ),
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "greeting",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "greeting_agent",
                    "next_actions": [
                        "Ask about TIN registration",
                        "Learn about VAT",
                        "File a tax return",
                    ],
                }
                self._audit_turn(
                    message=message,
                    result=greeted,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return greeted

            # 1b. Semantic cache check AFTER guardrails (Phase 5)
            if cache_allowed:
                with trace_stage("cache_lookup", timings=timings):
                    cached = self._cache.get(rewritten, locale=locale)
                if cached:
                    logger.info("generate: cache HIT for query=%s", message[:50])
                    return self._finalize_result({
                        **cached,
                        "conversation_id": thread_id,
                        "locale": locale,
                    })

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
                trace_ctx["agent_route_confidence"] = (
                    route_decision.route_confidence
                    if hasattr(route_decision, "route_confidence")
                    else route_decision.confidence
                )
                logger.info(
                    "supervisor: route=%s confidence=%.2f reason=%s",
                    route_decision.route.value,
                    route_decision.confidence,
                    route_decision.reason,
                )

                # Early returns — CLARIFY and ESCALATE don't need retrieval.
                if route_decision.route == AgentRoute.GREET:
                    greeted = {
                        "reply": (
                            "Hello! I'm the URA Digital Assistant. I can help you with "
                            "tax registration, filing returns, payments, customs, and more. "
                            "What would you like to know?"
                        ),
                        "sources": [],
                        "citations": [],
                        "faithfulness_score": None,
                        "retrieval_mode": "greeting",
                        "model": self.name,
                        "conversation_id": thread_id,
                        "locale": locale,
                        "escalation_required": False,
                        "escalation_reason": "",
                        "agent_role": "greeting_agent",
                        "next_actions": [
                            "Ask about TIN registration",
                            "Learn about VAT",
                            "File a tax return",
                        ],
                    }
                    self._audit_turn(
                        message=message,
                        result=greeted,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return greeted
                if route_decision.route == AgentRoute.CLARIFY:
                    clarified = {
                        "reply": route_decision.clarification_question
                        or (
                            "Could you provide a bit more detail about your "
                            "question? I can help with VAT, PAYE, customs, "
                            "registration, or specific tax types."
                        ),
                        "sources": [],
                        "citations": [],
                        "faithfulness_score": None,
                        "retrieval_mode": "clarification",
                        "model": self.name,
                        "conversation_id": thread_id,
                        "locale": locale,
                        "escalation_required": False,
                        "escalation_reason": "",
                        "agent_role": "clarification_agent",
                        "next_actions": self._default_next_actions(
                            agent_role="clarification_agent",
                        ),
                    }
                    self._audit_turn(
                        message=message,
                        result=clarified,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return clarified
                if route_decision.route == AgentRoute.ESCALATE:
                    ticket_id = ""
                    handoff = None
                    response_judge = {
                        "decision": "escalate",
                        "final_decision": "escalate",
                        "applied_revision": False,
                        "reasons": [route_decision.reason] if route_decision.reason else [],
                        "confidence_band": "low",
                    }
                    if flags.is_enabled("handoff_summaries"):
                        handoff = self._build_handoff_packet(
                            message=message,
                            reason=route_decision.reason,
                            conversation_history=conversation_history or None,
                        )

                    reply = (
                        "This looks like a question best handled by a URA "
                        "officer. I've flagged it for human review"
                    )
                    ticket_id = self._maybe_create_ticket(
                        reason=route_decision.reason,
                        user_query=message,
                        bot_reply=reply,
                        session_id=session_id,
                        conversation_id=thread_id,
                        priority=(handoff or {}).get("priority", "normal"),
                        handoff=handoff,
                        response_judge=response_judge,
                    )
                    if ticket_id:
                        trace_ctx["ticket_id"] = ticket_id
                    if ticket_id:
                        reply += f" (ticket {ticket_id[:8]})"
                    reply += (
                        " — you can also contact URA directly at "
                        "https://ura.go.ug or via the Contact Centre."
                    )
                    escalated = {
                        "reply": reply,
                        "sources": [],
                        "citations": [],
                        "faithfulness_score": None,
                        "retrieval_mode": "escalated",
                        "model": self.name,
                        "conversation_id": thread_id,
                        "locale": locale,
                        "escalation_required": True,
                        "escalation_reason": route_decision.reason,
                        "agent_role": "escalation_triage",
                        "handoff": handoff,
                        "response_judge": response_judge,
                        "next_actions": self._default_next_actions(
                            agent_role="escalation_triage",
                            handoff=handoff,
                            escalation_required=True,
                        ),
                        "ticket_id": ticket_id,
                    }
                    self._audit_turn(
                        message=message,
                        result=escalated,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return escalated

                # TOOLS / SPECIALIST routes force the agentic LLM path
                # downstream, with the supervisor's suggested tool whitelist.
                if route_decision.route in (
                    AgentRoute.TOOLS,
                    AgentRoute.TAX_SPECIALIST,
                    AgentRoute.CUSTOMS_SPECIALIST,
                ):
                    force_agentic = True
                    route_role_map = {
                        AgentRoute.TOOLS: "tool_specialist",
                        AgentRoute.TAX_SPECIALIST: "tax_specialist",
                        AgentRoute.CUSTOMS_SPECIALIST: "customs_specialist",
                    }
                    agent_role = route_role_map.get(route_decision.route, "tool_specialist")
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

            # 3. Fallback to keyword search if Qdrant returned nothing
            if not hits:
                with trace_stage("keyword_search_fallback", timings=timings):
                    kw_hits = _simple_search(rewritten, self._faq_index, top_k=top_k)
                    hits = [
                        {
                            "text": f"Question: {h['question']}\nAnswer: {h['answer']}",
                            "answer": h["answer"],
                            "question": h["question"],
                            "source": h["source"],
                            "chunk_id": "",
                            "page": "",
                            "section": h.get("tag", ""),
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

            # 3b2. Language-aware retrieval boosting — when the detected
            #      locale is non-English, boost hits whose metadata
            #      matches the detected language (e.g. Luganda FAQ sources).
            if locale != "en" and hits:
                locale_keywords = {
                    "lg": {"luganda", "oluganda", "lg"},
                    "sw": {"swahili", "kiswahili", "sw"},
                    "nyn": {"runyankole", "nkore", "nyn"},
                    "ach": {"acholi", "ach"},
                }
                boost_terms = locale_keywords.get(locale, set())
                if boost_terms:
                    for h in hits:
                        source = (h.get("source") or "").lower()
                        text_preview = (h.get("text") or "")[:200].lower()
                        if any(t in source or t in text_preview for t in boost_terms):
                            h["score_rrf"] = h.get("score_rrf", 0.5) + 0.3
                    # Re-sort by boosted score
                    hits.sort(key=lambda x: x.get("score_rrf", 0), reverse=True)

            # 3c. Always blend top FAQ keyword hits AFTER corrective RAG
            #     so precise CSV FAQ steps are never filtered out by reranking.
            with trace_stage("faq_blend", timings=timings):
                kw_hits = _simple_search(rewritten, self._faq_index, top_k=2)
                priority_hits = self._priority_faq_hits(rewritten, top_k=2)
                seen_texts = {h.get("text", "")[:80] for h in hits}
                for h in priority_hits:
                    if h.get("text", "")[:80] not in seen_texts:
                        hits.insert(0, h)
                        seen_texts.add(h.get("text", "")[:80])
                        retrieval_mode = "faq_priority"
                for h in kw_hits:
                    faq_text = f"Question: {h['question']}\nAnswer: {h['answer']}"
                    if faq_text[:80] not in seen_texts:
                        hits.append({
                            "text": faq_text,
                            "answer": h["answer"],
                            "question": h["question"],
                            "source": h["source"],
                            "chunk_id": "",
                            "page": "",
                            "section": h.get("tag", ""),
                            "doc_type": "csv",
                            "score_rrf": 0.5,
                        })
                        seen_texts.add(faq_text[:80])

            # 3c. Clarification check — ask for more details if query is ambiguous
            clarification = needs_clarification(message, hits)
            if clarification:
                clarify_result = {
                    "reply": clarification,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "clarification",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "clarification_agent",
                    "next_actions": self._default_next_actions(
                        agent_role="clarification_agent",
                    ),
                }
                self._audit_turn(
                    message=message,
                    result=clarify_result,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return clarify_result

            deterministic_reply = ""
            deterministic_sources: list[str] = []
            deterministic_citations: list[dict[str, Any]] = []
            if hits:
                deterministic_sources = list({h.get("source", "") for h in hits if h.get("source")})
                deterministic_citations = HybridRetriever.build_citations(hits)
                deterministic_reply = self._deterministic_procedure_reply(
                    rewritten, hits, deterministic_citations
                )
            if deterministic_reply:
                reply = self._finalize_reply(deterministic_reply)
                result = {
                    "reply": reply,
                    "sources": deterministic_sources,
                    "citations": deterministic_citations,
                    "faithfulness_score": 1.0,
                    "retrieval_mode": retrieval_mode,
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": agent_role,
                    "handoff": None,
                    "response_judge": {
                        "decision": "approve",
                        "final_decision": "approve",
                        "applied_revision": False,
                        "reasons": [],
                        "confidence_band": "high",
                    },
                    "next_actions": self._default_next_actions(agent_role=agent_role),
                    "ticket_id": "",
                }
                if cache_allowed:
                    self._cache.put(rewritten, result)
                self._persist_personalization_turn(
                    user_id=user_id,
                    conversation_id=thread_id,
                    message=message,
                    reply=reply,
                    agent_role=agent_role,
                    personalization=personalization,
                )
                self._audit_turn(
                    message=message,
                    result=result,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return result

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
                handoff = None
                response_judge = {
                    "decision": "escalate" if escalate else "approve",
                    "final_decision": "escalate" if escalate else "approve",
                    "applied_revision": False,
                    "reasons": [esc_reason] if esc_reason else [],
                    "confidence_band": "low",
                }
                if flags.is_enabled("handoff_summaries") and escalate:
                    handoff = self._build_handoff_packet(
                        message=message,
                        reason=esc_reason,
                        conversation_history=conversation_history or None,
                        hits=hits,
                    )
                ticket_id = self._maybe_create_ticket(
                    reason=esc_reason,
                    user_query=message,
                    bot_reply=reply,
                    session_id=session_id,
                    conversation_id=thread_id,
                    priority=(handoff or {}).get("priority", "normal"),
                    handoff=handoff,
                    response_judge=response_judge,
                )
                abstained = {
                    "reply": reply,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "abstained",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": escalate,
                    "escalation_reason": esc_reason,
                    "agent_role": agent_role,
                    "handoff": handoff,
                    "response_judge": response_judge,
                    "next_actions": self._default_next_actions(
                        agent_role=agent_role,
                        handoff=handoff,
                        escalation_required=escalate,
                    ),
                    "ticket_id": ticket_id,
                }
                self._audit_turn(
                    message=message, result=abstained, session_id=session_id, trace_ctx=trace_ctx
                )
                return abstained

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
                                personalization_context=(
                                    (personalization or {}).get("prompt_context", "")
                                ),
                                tenant_id=tenant_id or "default",
                                user_id=user_id or "",
                                user_role=user_role,
                                granted_purposes=granted_purposes or [],
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
                                personalization_context=(
                                    (personalization or {}).get("prompt_context", "")
                                ),
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
                                citations = [
                                    c for c in citations if c["ref"].strip("[]") in cited_refs
                                ]
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
                reply = self._finalize_reply(reply)
                leakage = self._output_guard.check_prompt_leakage(reply)
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
                            personalization_context=(personalization or {}).get(
                                "prompt_context", ""
                            ),
                        )
                    if revised:
                        reply = self._output_guard.sanitize(self._output_guard.redact_pii(revised))
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
            escalate, esc_reason = self._output_guard.should_escalate(faithfulness_score, hits)
            claim_report = None
            if hits and citations and reply:
                with trace_stage("claim_verification", timings=timings):
                    claim_report = verify_claims(reply, citations, hits)
                    trace_ctx["claim_verification"] = {
                        "decision": claim_report.get("decision"),
                        "score": claim_report.get("score"),
                        "claim_count": claim_report.get("claim_count"),
                        "unsupported_count": len(claim_report.get("unsupported_claims") or []),
                        "uncited_count": len(claim_report.get("uncited_claims") or []),
                    }
            response_judge = self._evaluate_response_judge(
                message=message,
                reply=reply,
                hits=hits,
                citations=citations,
                faithfulness_score=faithfulness_score,
                escalation_required=escalate,
                escalation_reason=esc_reason,
                claim_report=claim_report,
            )
            if claim_report is not None:
                response_judge["claim_verification"] = claim_report
            if response_judge["decision"] == "revise" and response_judge.get("revised_reply"):
                reply = self._output_guard.sanitize(
                    self._output_guard.redact_pii(response_judge["revised_reply"])
                )
                if contexts:
                    faithfulness_score = HybridRetriever.compute_faithfulness(reply, contexts)
                escalate, esc_reason = self._output_guard.should_escalate(faithfulness_score, hits)
                response_judge["applied_revision"] = True
                response_judge["final_decision"] = "escalate" if escalate else "approve"
                if faithfulness_score is not None:
                    response_judge["confidence_band"] = (
                        "high" if faithfulness_score >= 0.65 else "medium"
                    )
                if citations:
                    claim_report = verify_claims(reply, citations, hits)
                    response_judge["claim_verification"] = claim_report
                    if claim_report.get("decision") == "escalate":
                        response_judge["final_decision"] = "escalate"
                        response_judge.setdefault("reasons", []).append(
                            "claim verification still found unsupported factual claims"
                        )
            else:
                response_judge["final_decision"] = response_judge["decision"]

            if response_judge["final_decision"] == "escalate" and not esc_reason:
                esc_reason = "; ".join(response_judge.get("reasons") or []) or (
                    "response_judge requested human review"
                )
            if response_judge["final_decision"] == "escalate":
                escalate = True
            response_judge.pop("revised_reply", None)

            handoff = None
            if flags.is_enabled("handoff_summaries") and escalate:
                handoff = self._build_handoff_packet(
                    message=message,
                    reason=esc_reason,
                    conversation_history=conversation_history or None,
                    hits=hits,
                    faithfulness_score=faithfulness_score,
                )
            ticket_id = self._maybe_create_ticket(
                reason=esc_reason,
                user_query=message,
                bot_reply=reply,
                session_id=session_id,
                conversation_id=thread_id,
                priority=(handoff or {}).get("priority", "normal"),
                handoff=handoff,
                response_judge=response_judge,
            )
            if ticket_id:
                trace_ctx["ticket_id"] = ticket_id

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
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": escalate,
            "escalation_reason": esc_reason,
            "agent_role": agent_role,
            "handoff": handoff,
            "response_judge": response_judge,
            "next_actions": self._default_next_actions(
                agent_role=agent_role,
                handoff=handoff,
                escalation_required=escalate,
            ),
            "ticket_id": ticket_id,
        }
        result = self._finalize_result(result)
        reply = result["reply"]

        # Store in semantic cache (Phase 5)
        if cache_allowed and retrieval_mode not in ("blocked", "abstained"):
            self._cache.put(rewritten, result)

        self._persist_personalization_turn(
            user_id=user_id,
            conversation_id=thread_id,
            message=message,
            reply=reply,
            agent_role=agent_role,
            personalization=personalization,
        )

        # Phase 21 — audit ledger append (happy path).
        self._audit_turn(
            message=message,
            result=result,
            session_id=session_id,
            trace_ctx=trace_ctx,
        )

        return result

    # -- Audit helper (Phase 21) -------------------------------------
    def _audit_turn(
        self,
        *,
        message: str,
        result: dict[str, Any],
        session_id: str | None,
        trace_ctx: dict[str, Any] | None = None,
    ) -> None:
        """Append an immutable audit event for this turn.

        Called from every return site in :py:meth:`generate` so
        blocked / clarification / escalated / abstained / happy-path
        outcomes all end up in the ledger.  Payload excludes raw
        query/reply content (we store SHA-256 hashes) so the
        audit chain is useful for regulatory replay without becoming
        a second PII store.  Gated on ``FLAG_AUDIT_LEDGER``.

        Failures are swallowed — a broken audit DB must never
        block a user response.
        """
        if not flags.is_enabled("audit_ledger"):
            return
        try:
            import hashlib as _hashlib

            from .audit import get_ledger

            trace_ctx = trace_ctx or {}
            reply = result.get("reply", "") or ""
            payload = {
                "query_sha256": _hashlib.sha256((message or "").encode("utf-8")).hexdigest(),
                "reply_sha256": _hashlib.sha256(reply.encode("utf-8")).hexdigest(),
                "retrieval_mode": result.get("retrieval_mode", ""),
                "num_sources": len(result.get("sources", [])),
                "num_citations": len(result.get("citations", [])),
                "faithfulness_score": result.get("faithfulness_score"),
                "escalation_required": bool(result.get("escalation_required")),
                "escalation_reason": result.get("escalation_reason", ""),
                "model": result.get("model", self.name),
                "locale": result.get("locale", "en"),
                "conversation_id": result.get("conversation_id") or "",
                "input_tokens": llm_module.count_tokens(message),
                "output_tokens": llm_module.count_tokens(reply),
                "tool_calls": trace_ctx.get("tool_calls", []),
                "tool_iterations": trace_ctx.get("tool_iterations", 0),
                "agent_route": trace_ctx.get("agent_route", ""),
                "ticket_id": result.get("ticket_id", ""),
            }
            audit_tenant_id = str(trace_ctx.get("tenant_id") or "default")[:128]
            audit_user_id = str(trace_ctx.get("user_id") or session_id or "")[:128]
            get_ledger().append(
                event_type="generate",
                payload=payload,
                tenant_id=audit_tenant_id,
                user_id=audit_user_id,
            )
        except Exception:
            logger.debug("audit ledger append failed", exc_info=True)

    def generate_retrieval_only(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 6,
        locale: str = "en",
        session_id: str | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        conversation_history_override: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Run retrieval + guardrails but skip LLM generation (for SSE streaming).

        Returns the same dict as ``generate()`` but with ``_hits`` and
        ``_history`` included so the streaming endpoint can pass them to
        the LLM stream.

        Passing ``conversation_history_override`` (Phase 3 of the WS
        upgrade) skips the SQLite history fetch — callers with their own
        in-memory cache (e.g. a long-lived WebSocket session) avoid one
        DB round trip per turn.
        """
        thread_id = conversation_id or str(uuid.uuid4())
        agent_role = "rag_answerer"

        # Multi-turn memory (Phase 4 -> overridable in Phase 29)
        conversation_history: list[dict[str, str]] = []
        if conversation_history_override is not None:
            conversation_history = list(conversation_history_override)
        else:
            history_session_id = None if conversation_id else session_id
            if conversation_id or history_session_id:
                try:
                    conversation_history = db.get_recent_turns(
                        session_id=history_session_id,
                        conversation_id=conversation_id,
                        limit=5,
                    )
                except Exception:
                    logger.debug("Failed to fetch conversation history", exc_info=True)

        # Query rewriting (Phase 4)
        rewritten = rewrite_query(message, history=conversation_history or None)

        # Language detection — auto-detect for adapter routing
        if locale == "en":
            detected_locale = detect_language(message)
            if detected_locale != "en":
                locale = detected_locale
                logger.info("Auto-detected locale: %s (streaming)", locale)

        personalization = self._load_personalization_state(user_id)
        cache_allowed = personalization is None

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
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "safety_guard",
                "next_actions": ["Rephrase your request as a legitimate URA support question."],
                "_hits": [],
                "_history": [],
            }

        workflow_result = self._maybe_handle_workflow(
            message=message,
            rewritten=rewritten,
            thread_id=thread_id,
            locale=locale,
            personalization=personalization,
        )
        if workflow_result:
            return {
                **workflow_result,
                "_hits": [],
                "_history": conversation_history,
                "_rewritten": rewritten,
                "_personalization_context": (personalization or {}).get("prompt_context", ""),
            }

        # Greeting detection — always active (streaming path)
        _q_lower_s = message.strip().lower().strip("!.?,")
        _q_words_s = message.strip().split()
        if len(_q_words_s) <= 3 and (
            _q_lower_s in _GREETING_WORDS
            or _q_lower_s in _GREETING_PHRASES
            or all(w.lower().strip("!.?,") in _GREETING_WORDS for w in _q_words_s)
        ):
            return {
                "reply": (
                    "Hello! I'm the URA Digital Assistant. I can help you with "
                    "tax registration, filing returns, payments, customs, and more. "
                    "What would you like to know?"
                ),
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "greeting",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "greeting_agent",
                "next_actions": [
                    "Ask about TIN registration",
                    "Learn about VAT",
                    "File a tax return",
                ],
                "_hits": [],
                "_history": [],
            }

        # Semantic cache check (Phase 5)
        if cache_allowed:
            cached = self._cache.get(rewritten, locale=locale)
            if cached:
                return self._finalize_result({
                    **cached,
                    "conversation_id": thread_id,
                    "locale": locale,
                })

        route_decision = None
        if flags.is_enabled("agentic_mode"):
            route_decision = supervisor.classify(
                rewritten,
                has_conversation_history=bool(conversation_history),
            )
            if route_decision.route == AgentRoute.CLARIFY:
                return {
                    "reply": route_decision.clarification_question
                    or (
                        "Could you share a bit more context? For example, are you "
                        "asking about VAT, PAYE, customs, registration, or a specific tax type?"
                    ),
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "clarification",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "clarification_agent",
                    "next_actions": self._default_next_actions(
                        agent_role="clarification_agent",
                    ),
                    "_hits": [],
                    "_history": [],
                }
            if route_decision.route == AgentRoute.ESCALATE:
                ticket_id = ""
                handoff = None
                response_judge = {
                    "decision": "escalate",
                    "final_decision": "escalate",
                    "applied_revision": False,
                    "reasons": [route_decision.reason] if route_decision.reason else [],
                    "confidence_band": "low",
                }
                if flags.is_enabled("handoff_summaries"):
                    handoff = self._build_handoff_packet(
                        message=message,
                        reason=route_decision.reason,
                        conversation_history=conversation_history or None,
                    )
                reply = (
                    "This looks like a question best handled by a URA officer. "
                    "Please use the Contact Centre or request human follow-up."
                )
                ticket_id = self._maybe_create_ticket(
                    reason=route_decision.reason,
                    user_query=message,
                    bot_reply=reply,
                    session_id=session_id,
                    conversation_id=thread_id,
                    priority=(handoff or {}).get("priority", "normal"),
                    handoff=handoff,
                    response_judge=response_judge,
                )
                return {
                    "reply": reply,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "escalated",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": True,
                    "escalation_reason": route_decision.reason,
                    "agent_role": "escalation_triage",
                    "handoff": handoff,
                    "response_judge": response_judge,
                    "next_actions": self._default_next_actions(
                        agent_role="escalation_triage",
                        handoff=handoff,
                        escalation_required=True,
                    ),
                    "ticket_id": ticket_id,
                    "_hits": [],
                    "_history": [],
                    "_personalization_context": (personalization or {}).get("prompt_context", ""),
                }
            if route_decision.route == AgentRoute.TOOLS:
                agent_role = "tool_specialist"
            elif route_decision.route == AgentRoute.TAX_SPECIALIST:
                agent_role = "tax_specialist"
            elif route_decision.route == AgentRoute.CUSTOMS_SPECIALIST:
                agent_role = "customs_specialist"

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
                    "chunk_id": "", "page": "", "section": h.get("tag", ""),
                    "doc_type": "csv", "score_rrf": 0.0,
                }
                for h in kw_hits
            ]

        # Corrective RAG (Phase 6)
        if hits and self._retriever_ready:
            hits, was_corrected = corrective_retrieve(rewritten, self._retriever, hits, top_k=top_k)
            if was_corrected:
                retrieval_mode = "hybrid_corrected"

        # Language-aware retrieval boosting (streaming path)
        if locale != "en" and hits:
            locale_keywords = {
                "lg": {"luganda", "oluganda", "lg"},
                "sw": {"swahili", "kiswahili", "sw"},
                "nyn": {"runyankole", "nkore", "nyn"},
                "ach": {"acholi", "ach"},
            }
            boost_terms = locale_keywords.get(locale, set())
            if boost_terms:
                for h in hits:
                    source = (h.get("source") or "").lower()
                    text_preview = (h.get("text") or "")[:200].lower()
                    if any(t in source or t in text_preview for t in boost_terms):
                        h["score_rrf"] = h.get("score_rrf", 0.5) + 0.3
                hits.sort(key=lambda x: x.get("score_rrf", 0), reverse=True)

        # Blend top FAQ keyword hits after corrective RAG
        kw_hits = _simple_search(rewritten, self._faq_index, top_k=2)
        seen_texts = {h.get("text", "")[:80] for h in hits}
        for h in kw_hits:
            faq_text = f"Question: {h['question']}\nAnswer: {h['answer']}"
            if faq_text[:80] not in seen_texts:
                hits.append({
                    "text": faq_text, "answer": h["answer"],
                    "question": h["question"], "source": h["source"],
                    "chunk_id": "", "page": "", "section": h.get("tag", ""),
                    "doc_type": "csv", "score_rrf": 0.5,
                })
                seen_texts.add(faq_text[:80])

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
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "clarification_agent",
                "next_actions": self._default_next_actions(
                    agent_role="clarification_agent",
                ),
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
            handoff = None
            response_judge = {
                "decision": "escalate" if escalate else "approve",
                "final_decision": "escalate" if escalate else "approve",
                "applied_revision": False,
                "reasons": [esc_reason] if esc_reason else [],
                "confidence_band": "low",
            }
            if flags.is_enabled("handoff_summaries") and escalate:
                handoff = self._build_handoff_packet(
                    message=message,
                    reason=esc_reason,
                    conversation_history=conversation_history or None,
                    hits=hits,
                )
            ticket_id = self._maybe_create_ticket(
                reason=esc_reason,
                user_query=message,
                bot_reply=reply,
                session_id=session_id,
                conversation_id=thread_id,
                priority=(handoff or {}).get("priority", "normal"),
                handoff=handoff,
                response_judge=response_judge,
            )
            return {
                "reply": reply,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "abstained",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": escalate,
                "escalation_reason": esc_reason,
                "agent_role": agent_role,
                "handoff": handoff,
                "response_judge": response_judge,
                "next_actions": self._default_next_actions(
                    agent_role=agent_role,
                    handoff=handoff,
                    escalation_required=escalate,
                ),
                "ticket_id": ticket_id,
                "_hits": [],
                "_history": [],
                "_personalization_context": (personalization or {}).get("prompt_context", ""),
            }

        sources = list({h.get("source", "") for h in hits if h.get("source")})
        citations = HybridRetriever.build_citations(hits)
        best = hits[0] if hits else {}
        reply = best.get("answer") or best.get("text", "")

        # Escalation check (same as sync path)
        escalate, esc_reason = self._output_guard.should_escalate(None, hits)
        response_judge = self._evaluate_response_judge(
            message=message,
            reply=reply,
            hits=hits,
            citations=citations,
            faithfulness_score=None,
            escalation_required=escalate,
            escalation_reason=esc_reason,
        )
        if response_judge["decision"] == "revise" and response_judge.get("revised_reply"):
            reply = response_judge["revised_reply"]
            response_judge["applied_revision"] = True
            response_judge["final_decision"] = "approve"
        else:
            response_judge["final_decision"] = response_judge["decision"]
        if response_judge["final_decision"] == "escalate":
            escalate = True
            if not esc_reason:
                esc_reason = "; ".join(response_judge.get("reasons") or [])
        response_judge.pop("revised_reply", None)
        handoff = None
        if flags.is_enabled("handoff_summaries") and escalate:
            handoff = self._build_handoff_packet(
                message=message,
                reason=esc_reason,
                conversation_history=conversation_history or None,
                hits=hits,
            )
        ticket_id = self._maybe_create_ticket(
            reason=esc_reason,
            user_query=message,
            bot_reply=reply,
            session_id=session_id,
            conversation_id=thread_id,
            priority=(handoff or {}).get("priority", "normal"),
            handoff=handoff,
            response_judge=response_judge,
        )

        return {
            "reply": reply,
            "sources": sources,
            "citations": citations,
            "faithfulness_score": None,
            "retrieval_mode": retrieval_mode,
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": escalate,
            "escalation_reason": esc_reason,
            "agent_role": agent_role,
            "handoff": handoff,
            "response_judge": response_judge,
            "next_actions": self._default_next_actions(
                agent_role=agent_role,
                handoff=handoff,
                escalation_required=escalate,
            ),
            "ticket_id": ticket_id,
            "_hits": hits,
            "_history": conversation_history,
            "_rewritten": rewritten,
            "_personalization_context": (personalization or {}).get("prompt_context", ""),
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
