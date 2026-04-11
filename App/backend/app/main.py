"""URA Chatbot API – FastAPI application.

Hardened for production per OWASP LLM Top 10 (2025), NIST SSDF, and
ISO/IEC 42001:2023 security controls.  Includes analytics, feedback,
and Prometheus-compatible metrics (2026 observability standards).
"""

import json
import os
import re
import uuid
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from .models import (
    ChatRequest,
    ChatResponse,
    ClassifyRequest,
    ClassifyResponse,
    TagListResponse,
    FAQResponse,
    BatchClassifyRequest,
    BatchClassifyResponse,
    HealthResponse,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSummary,
    FeedbackCommentRequest,
    AnalyticsEvent,
    AnalyticsDashboard,
)
from .service import ChatModel
from .analytics import AnalyticsMiddleware, metrics
from . import database as db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan – replaces deprecated @app.on_event("startup")
# ---------------------------------------------------------------------------
_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{1,128}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down the ChatModel singleton."""
    # OpenTelemetry GenAI tracing (opt-in via OTEL_ENABLED=true)
    try:
        from .tracing import init_tracing

        init_tracing()
    except Exception:
        logger.warning("OpenTelemetry tracing init skipped", exc_info=True)

    # Initialise analytics database
    try:
        db.init_db()
        logger.info("Analytics database ready")
    except Exception:
        logger.exception("Analytics database initialisation failed")

    try:
        app.state.model = ChatModel()
        logger.info("ChatModel ready – %d tags loaded", len(app.state.model._faq_index))
    except Exception:
        logger.exception("ChatModel initialisation failed")
        app.state.model = None
    yield
    app.state.model = None
    logger.info("ChatModel shut down.")


# ---------------------------------------------------------------------------
# Rate limiter (2026 production: Redis-backed when SLOWAPI_STORAGE_URI set)
# ---------------------------------------------------------------------------
# Set SLOWAPI_STORAGE_URI=redis://redis:6379 for multi-worker / multi-replica
# deploys.  When unset, the limiter uses an in-process memory bucket — this
# is fine for a single-worker dev server but will NOT correctly enforce a
# shared limit across workers or Kubernetes replicas.
_RATE_LIMIT = os.getenv("RATE_LIMIT", "30/minute")
_SLOWAPI_STORAGE_URI = os.getenv("SLOWAPI_STORAGE_URI", "")

_limiter_kwargs: dict = {"key_func": get_remote_address, "default_limits": []}
if _SLOWAPI_STORAGE_URI:
    _limiter_kwargs["storage_uri"] = _SLOWAPI_STORAGE_URI
    logger.info("Rate limiter using Redis storage: %s", _SLOWAPI_STORAGE_URI.split("@")[-1])

limiter = Limiter(**_limiter_kwargs)

app = FastAPI(
    title="URA Chatbot API",
    version="1.2.0",
    description="AI-powered customer-service chatbot for the Uganda Revenue Authority",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------
def get_model(request: Request) -> ChatModel:
    """Retrieve the ChatModel from app state; 503 if unavailable."""
    model = getattr(request.app.state, "model", None)
    if model is None:
        raise HTTPException(status_code=503, detail="Model not initialized")
    return model


# ---------------------------------------------------------------------------
# CORS – hardened (no wildcard, no credentials, explicit methods)
# ---------------------------------------------------------------------------
_allowed_origins: list[str] = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Session-ID", "X-Request-ID"],
)

# Analytics middleware (must be added after CORS)
app.add_middleware(AnalyticsMiddleware)


# ---------------------------------------------------------------------------
# Security headers middleware (OWASP, NIST SSDF PW.6)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Validate X-Request-ID to prevent log injection (OWASP LLM05)
    raw_id = request.headers.get("X-Request-ID", "")
    request_id = raw_id if _REQUEST_ID_RE.match(raw_id) else str(uuid.uuid4())
    # Stash on request.state so handlers can read it without re-parsing headers
    request.state.request_id = request_id
    logger.info(
        "request  request_id=%s method=%s path=%s",
        request_id, request.method, request.url.path,
    )
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["X-Request-ID"] = request_id
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
def health_liveness() -> dict:
    """Liveness probe for orchestrators (Docker, K8s)."""
    return {"status": "alive", "version": app.version}


@app.get("/ready", response_model=HealthResponse, tags=["system"])
def health_readiness(model: ChatModel = Depends(get_model)) -> HealthResponse:
    """Readiness probe; returns 503 if model is unavailable.

    Checks both FAQ index AND Qdrant retriever health.
    """
    retrieval_mode = "hybrid" if model._retriever_ready else "keyword"
    qdrant_healthy = model._retriever.is_ready if model._retriever_ready else False
    return HealthResponse(
        status="ready" if qdrant_healthy else "degraded",
        version=app.version,
        model_loaded=True,
        tags_loaded=len(model._faq_index),
        retrieval_mode=retrieval_mode,
    )


# ---------------------------------------------------------------------------
# Chat endpoint (with conversation logging)
# ---------------------------------------------------------------------------
@app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
@limiter.limit(_RATE_LIMIT)
def chat(body: ChatRequest, request: Request, model: ChatModel = Depends(get_model)) -> ChatResponse:
    session_id = request.headers.get("X-Session-ID", "")
    request_id = getattr(request.state, "request_id", None)
    t0 = time.perf_counter()

    result = model.generate(
        message=body.message,
        conversation_id=body.conversation_id,
        top_k=body.top_k,
        locale=body.locale,
        session_id=session_id or None,
        request_id=request_id,
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Classify to get topic tag for analytics
    topic_tag = ""
    confidence = 0.0
    try:
        classify_result = model.classify(body.message, top_k=1)
        if classify_result["predictions"]:
            topic_tag = classify_result["predictions"][0]["tag"]
            confidence = classify_result["predictions"][0]["confidence"]
    except Exception:
        logger.warning("Classification failed during chat analytics", exc_info=True)
        metrics.inc("classification_errors_total")

    # Log conversation with PII redaction (privacy-by-design)
    try:
        from .service import ChatModel as _CM

        conv_id = db.log_conversation(
            session_id=session_id or None,
            user_message=_CM.redact_for_storage(body.message),
            bot_reply=_CM.redact_for_storage(result["reply"]),
            sources=json.dumps(result.get("sources", [])),
            response_time_ms=round(elapsed_ms, 2),
            confidence=confidence,
            topic_tag=topic_tag,
        )
        if not result.get("conversation_id"):
            result["conversation_id"] = conv_id
    except Exception:
        logger.warning("Conversation logging failed", exc_info=True)

    # Track escalation events
    if result.get("escalation_required"):
        metrics.inc("escalation_total")
        try:
            db.track_event(
                "escalation_required",
                json.dumps({
                    "reason": result.get("escalation_reason", ""),
                    "topic_tag": topic_tag,
                }),
                session_id=session_id or None,
            )
        except Exception:
            logger.debug("Escalation event tracking failed", exc_info=True)

    return ChatResponse(**result)


# ---------------------------------------------------------------------------
# SSE streaming chat endpoint (Phase 3)
# ---------------------------------------------------------------------------
@app.post("/v1/chat/stream", tags=["chat"])
@limiter.limit(_RATE_LIMIT)
async def chat_stream(body: ChatRequest, request: Request, model: ChatModel = Depends(get_model)):
    """Server-Sent Events streaming chat — tokens arrive progressively."""
    from . import llm as llm_module
    from . import service as service_module

    session_id = request.headers.get("X-Session-ID", "")
    request_id = getattr(request.state, "request_id", None)

    async def event_generator():
        import asyncio
        from .guardrails import OutputGuard
        from .retriever import HybridRetriever

        _output_guard = OutputGuard()
        t0 = time.perf_counter()
        full_reply = ""  # FIX BUG: initialise before branches
        result: dict = {}  # Sentinel: safe default for finally block

        try:
            # FIX LOGIC: run blocking retrieval in thread pool
            result = await asyncio.to_thread(
                model.generate_retrieval_only,
                message=body.message,
                conversation_id=body.conversation_id,
                top_k=body.top_k,
                locale=body.locale,
                session_id=session_id or None,
                request_id=request_id,
            )

            # If blocked/abstained/clarification, send single event
            if result.get("retrieval_mode") in ("blocked", "abstained", "clarification"):
                yield {"event": "metadata", "data": json.dumps({
                    "sources": result.get("sources", []),
                    "citations": result.get("citations", []),
                    "faithfulness_score": result.get("faithfulness_score"),
                    "retrieval_mode": result.get("retrieval_mode"),
                    "model": result.get("model"),
                    "conversation_id": result.get("conversation_id"),
                    "locale": result.get("locale"),
                    "escalation_required": result.get("escalation_required", False),
                    "escalation_reason": result.get("escalation_reason", ""),
                })}
                yield {"event": "token", "data": result.get("reply", "")}
                yield {"event": "done", "data": ""}
                return

            # Send metadata first
            yield {"event": "metadata", "data": json.dumps({
                "sources": result.get("sources", []),
                "citations": result.get("citations", []),
                "retrieval_mode": result.get("retrieval_mode"),
                "model": result.get("model"),
                "conversation_id": result.get("conversation_id"),
                "locale": result.get("locale"),
            })}

            # Stream LLM tokens
            hits = result.get("_hits", [])
            conversation_history = result.get("_history", [])
            rewritten_query = result.get("_rewritten", body.message)
            if llm_module.is_available() and hits:
                # Run blocking LLM stream in thread pool, routed through the
                # shared circuit breaker (Gap #16 fix — prevents the stream
                # path from bypassing breaker/timeout guarantees).
                def _stream_tokens():
                    return service_module.stream_llm_tokens(
                        query=rewritten_query,
                        passages=hits,
                        conversation_history=conversation_history or None,
                        locale=body.locale,
                    )

                tokens = await asyncio.to_thread(_stream_tokens)
                # Breaker OPEN / empty stream → fall through to single-event
                # fallback below (same branch as "llm not available")
                if not tokens:
                    yield {"event": "token", "data": result.get("reply", "")}
                    yield {"event": "done", "data": ""}
                    return
                for token in tokens:
                    # Sanitize each token chunk (OWASP LLM05)
                    sanitized = _output_guard.sanitize(token)
                    full_reply += sanitized
                    yield {"event": "token", "data": sanitized}

                # Apply PII redaction to full accumulated reply
                full_reply = _output_guard.redact_pii(full_reply)

                # Compute faithfulness + grounding on full reply
                contexts = [h.get("text") or h.get("answer", "") for h in hits]
                faith = HybridRetriever.compute_faithfulness(full_reply, contexts)
                escalate, esc_reason = _output_guard.should_escalate(faith, hits)
                yield {"event": "grounding", "data": json.dumps({
                    "faithfulness_score": faith,
                    "escalation_required": escalate,
                    "escalation_reason": esc_reason,
                })}

                # Cache the completed streaming response
                try:
                    model._cache.put(rewritten_query, {
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
                    })
                except Exception:
                    logger.debug("Stream cache store failed", exc_info=True)
            else:
                # Fallback: send best-hit answer as single token
                full_reply = result.get("reply", "")
                yield {"event": "token", "data": full_reply}

            yield {"event": "done", "data": ""}

        except Exception:
            logger.exception("SSE stream error")
            yield {"event": "error", "data": "Internal server error"}
            yield {"event": "done", "data": ""}
        finally:
            # FIX LOGIC: log conversation in finally block (runs even on disconnect)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            try:
                from .service import ChatModel as _CM
                db.log_conversation(
                    session_id=session_id or None,
                    user_message=_CM.redact_for_storage(body.message),
                    bot_reply=_CM.redact_for_storage(full_reply),
                    sources=json.dumps(result.get("sources", []) if result else []),
                    response_time_ms=round(elapsed_ms, 2),
                )
            except Exception:
                logger.warning("Stream conversation logging failed", exc_info=True)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Classification endpoints
# ---------------------------------------------------------------------------
@app.post("/classify", response_model=ClassifyResponse, tags=["classification"])
def classify(
    request: ClassifyRequest, model: ChatModel = Depends(get_model)
) -> ClassifyResponse:
    result = model.classify(text=request.text, top_k=request.top_k)
    return ClassifyResponse(**result)


@app.post("/classify/batch", response_model=BatchClassifyResponse, tags=["classification"])
def classify_batch(
    request: BatchClassifyRequest, model: ChatModel = Depends(get_model)
) -> BatchClassifyResponse:
    results = model.classify_batch(texts=request.texts)
    return BatchClassifyResponse(**results)


# ---------------------------------------------------------------------------
# Knowledge base endpoints
# ---------------------------------------------------------------------------
@app.get("/tags", response_model=TagListResponse, tags=["knowledge"])
def list_tags(model: ChatModel = Depends(get_model)) -> TagListResponse:
    result = model.list_tags()
    return TagListResponse(**result)


@app.get("/faq/{tag}", response_model=FAQResponse, tags=["knowledge"])
def get_faq(
    tag: str = Path(..., pattern=r"^[a-z][a-z0-9_]{0,63}$"),
    model: ChatModel = Depends(get_model),
) -> FAQResponse:
    result = model.get_faq(tag=tag)
    if result is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return FAQResponse(**result)


_INDEX_API_KEY = os.getenv("INDEX_API_KEY", "")


def _verify_index_auth(request: Request) -> None:
    """Require a bearer token for the indexing endpoint (OWASP LLM10)."""
    if not _INDEX_API_KEY:
        return  # auth disabled when key is not configured
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {_INDEX_API_KEY}":
        raise HTTPException(status_code=403, detail="Invalid or missing INDEX_API_KEY")


@app.post("/v1/index", tags=["knowledge"])
def trigger_indexing(
    request: Request,
    model: ChatModel = Depends(get_model),
) -> dict:
    """Trigger document re-indexing into the Qdrant vector store.

    Requires ``Authorization: Bearer <INDEX_API_KEY>`` when configured.
    Ingests all PDFs and FAQ CSVs, rebuilds the collection, and
    re-initialises the hybrid retriever.
    """
    _verify_index_auth(request)

    from .indexer import DATA_DIR, PDF_DIR, build_index, ingest_csvs, ingest_pdfs

    documents: list[dict] = []
    documents.extend(ingest_csvs(DATA_DIR))
    documents.extend(ingest_pdfs(PDF_DIR))

    if not documents:
        raise HTTPException(status_code=404, detail="No documents found to index")

    stats = build_index(documents, recreate=True)

    # Re-initialise the retriever so it picks up the new collection
    model._retriever_ready = model._retriever.initialize()
    stats["retrieval_mode"] = "hybrid" if model._retriever_ready else "keyword"

    return stats


# ---------------------------------------------------------------------------
# Feedback endpoints
# ---------------------------------------------------------------------------
@app.post("/v1/feedback", response_model=FeedbackResponse, tags=["feedback"])
def submit_feedback(body: FeedbackRequest) -> FeedbackResponse:
    """Submit thumbs-up/down feedback on a chatbot response."""
    from .service import ChatModel as _CM

    metrics.inc("feedback_total", labels={"rating": body.rating})
    result = db.save_feedback(
        message_id=body.message_id,
        rating=body.rating,
        comment=body.comment,
        session_id=body.session_id,
        user_query=_CM.redact_for_storage(body.user_query),
        bot_reply=_CM.redact_for_storage(body.bot_reply),
    )
    return FeedbackResponse(**result)


@app.patch("/v1/feedback/{message_id}/comment", tags=["feedback"])
def update_feedback_comment(
    message_id: str,
    body: FeedbackCommentRequest,
) -> dict:
    """Add a follow-up comment to existing feedback (avoids duplicate entries)."""
    from .service import ChatModel as _CM

    updated = db.update_feedback_comment(message_id, _CM.redact_for_storage(body.comment))
    if not updated:
        raise HTTPException(status_code=404, detail="Feedback entry not found or already has comment")
    return {"status": "ok", "message_id": message_id}


@app.get("/v1/feedback/summary", response_model=FeedbackSummary, tags=["feedback"])
def feedback_summary(days: int = 30) -> FeedbackSummary:
    """Aggregated feedback statistics for the specified period."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    return FeedbackSummary(**db.get_feedback_summary(days))


# ---------------------------------------------------------------------------
# Analytics & metrics endpoints
# ---------------------------------------------------------------------------
@app.post("/v1/analytics/event", tags=["analytics"])
def track_analytics_event(body: AnalyticsEvent) -> dict:
    """Track a client-side analytics event."""
    db.track_event(
        event_type=body.event_type,
        event_data=json.dumps(body.event_data),
        session_id=body.session_id,
    )
    return {"status": "ok"}


@app.get("/v1/analytics/dashboard", response_model=AnalyticsDashboard, tags=["analytics"])
def analytics_dashboard(days: int = 30) -> AnalyticsDashboard:
    """Comprehensive analytics dashboard data."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")

    snap = metrics.snapshot()
    return AnalyticsDashboard(
        uptime_seconds=snap["uptime_seconds"],
        requests={
            "counters": snap["counters"],
            "latency": snap["histograms"],
        },
        chat={
            "event_counts": db.get_event_counts(days),
        },
        sessions=db.get_session_stats(days),
        conversations=db.get_conversation_stats(days),
        feedback=db.get_feedback_summary(days),
    )


@app.get("/metrics", tags=["system"])
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus-compatible metrics endpoint."""
    return PlainTextResponse(
        content=metrics.to_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# Continuous evaluation — admin-only
# ---------------------------------------------------------------------------
@app.post("/v1/evaluate", tags=["admin"])
def run_eval(request: Request, sample_size: int = 50, days: int = 30) -> dict:
    """Run the RAG evaluation harness on recent conversations.

    Returns the full ``EvalReport`` as JSON.  Requires the same
    ``Authorization: Bearer <INDEX_API_KEY>`` header as ``/v1/index``.
    Also writes each metric to the in-process Prometheus store so
    Grafana can chart ``ura_eval_metric{name=...}`` alongside
    request metrics.
    """
    _verify_index_auth(request)
    if sample_size < 1 or sample_size > 500:
        raise HTTPException(status_code=400, detail="sample_size must be 1..500")
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be 1..365")

    from .evaluation import run_evaluation

    report = run_evaluation(sample_size=sample_size, days=days)
    for m in report.metrics:
        metrics.observe(
            f"ura_eval_metric",
            m.value,
            labels={"name": m.name, "backend": report.backend},
        )
    return report.to_dict()


# ---------------------------------------------------------------------------
# Phase 14-D — ticket queue (admin-only)
# ---------------------------------------------------------------------------
@app.get("/v1/admin/tickets", tags=["admin"])
def list_tickets_endpoint(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List escalation tickets for URA staff triage.

    Gated by the same ``Authorization: Bearer <INDEX_API_KEY>`` header
    as ``/v1/index`` and ``/v1/evaluate``.  Filter by status via the
    query string: ``?status=open``.
    """
    _verify_index_auth(request)
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1..500")
    if offset < 0 or offset > 100_000:
        raise HTTPException(status_code=400, detail="offset out of range")
    if status and status not in ("open", "assigned", "resolved", "wontfix"):
        raise HTTPException(status_code=400, detail="invalid status")

    rows = db.list_tickets(status=status, limit=limit, offset=offset)
    return {
        "count": len(rows),
        "status_filter": status or "all",
        "limit": limit,
        "offset": offset,
        "tickets": rows,
    }


@app.get("/v1/admin/tickets/stats", tags=["admin"])
def ticket_stats_endpoint(request: Request, days: int = 30) -> dict:
    """Aggregate ticket statistics for the admin dashboard."""
    _verify_index_auth(request)
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be 1..365")
    return db.ticket_stats(days=days)


@app.get("/v1/admin/tickets/{ticket_id}", tags=["admin"])
def get_ticket_endpoint(
    request: Request,
    ticket_id: str = Path(..., pattern=r"^[a-f0-9-]{1,64}$"),
) -> dict:
    """Fetch a single ticket by id."""
    _verify_index_auth(request)
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket


@app.patch("/v1/admin/tickets/{ticket_id}", tags=["admin"])
def update_ticket_endpoint(
    request: Request,
    ticket_id: str = Path(..., pattern=r"^[a-f0-9-]{1,64}$"),
    status: str | None = None,
    assignee: str | None = None,
    staff_note: str | None = None,
    priority: str | None = None,
) -> dict:
    """Update a ticket's status/assignee/note/priority."""
    _verify_index_auth(request)
    ok = db.update_ticket(
        ticket_id,
        status=status,
        assignee=assignee,
        staff_note=staff_note,
        priority=priority,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="no-op or invalid update")
    return {"status": "ok", "ticket_id": ticket_id}


# ---------------------------------------------------------------------------
# Phase 14 (2026) — /v1/me/* identity, profile, consent, subject rights
# ---------------------------------------------------------------------------
from .auth import AuthContext, current_user, require_user  # noqa: E402
from .auth.models import (  # noqa: E402
    ConsentGrantRequest,
    ConsentWithdrawRequest,
    ProfileUpdateRequest,
)


@app.get("/v1/me", tags=["me"])
def me_whoami(ctx: AuthContext = Depends(current_user)) -> dict:
    """Return the current auth context (or anonymous)."""
    if not ctx.is_authenticated:
        return {"authenticated": False, "role": "public", "tenant_id": "default"}
    # Refresh last_seen + upsert on every whoami call
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    return {
        "authenticated": True,
        "user_id": row["id"],
        "external_id": row["external_id"],
        "tenant_id": row["tenant_id"],
        "email": row["email"],
        "role": row["role"],
        "granted_purposes": ctx.user.granted_purposes,
    }


@app.get("/v1/me/profile", tags=["me"])
def me_get_profile(ctx: AuthContext = Depends(require_user)) -> dict:
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    profile = db.get_user_profile(row["id"])
    if profile is None:
        profile = db.upsert_user_profile(row["id"], {})
    return {"user_id": row["id"], **profile}


@app.put("/v1/me/profile", tags=["me"])
def me_update_profile(
    body: ProfileUpdateRequest,
    ctx: AuthContext = Depends(require_user),
) -> dict:
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    profile = db.upsert_user_profile(row["id"], updates)
    return {"user_id": row["id"], **profile}


@app.get("/v1/me/consents", tags=["me"])
def me_list_consents(ctx: AuthContext = Depends(require_user)) -> dict:
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    return {"user_id": row["id"], "consents": db.get_active_consents(row["id"])}


@app.post("/v1/me/consents/grant", tags=["me"])
def me_grant_consent(
    body: ConsentGrantRequest,
    ctx: AuthContext = Depends(require_user),
) -> dict:
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    granted = [
        db.grant_consent(row["id"], p, body.version)
        for p in body.purposes
    ]
    return {"user_id": row["id"], "granted": granted}


@app.post("/v1/me/consents/withdraw", tags=["me"])
def me_withdraw_consent(
    body: ConsentWithdrawRequest,
    ctx: AuthContext = Depends(require_user),
) -> dict:
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    withdrawn = {p: db.withdraw_consent(row["id"], p) for p in body.purposes}
    return {"user_id": row["id"], "withdrawn": withdrawn}


@app.get("/v1/me/export", tags=["me"])
def me_export(ctx: AuthContext = Depends(require_user)) -> dict:
    """UDPA 2019 data-portability export."""
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    return db.export_user_data(row["id"])


@app.delete("/v1/me", tags=["me"])
def me_forget(ctx: AuthContext = Depends(require_user)) -> dict:
    """UDPA 2019 right-to-erasure endpoint.

    Deletes every PII-bearing row for the user EXCEPT the audit
    ledger (which is immutably hash-chained).  The erasure itself
    is logged to the ledger as a tombstone event.
    """
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    counts = db.delete_user_cascade(row["id"])
    return {"deleted": counts, "external_id": ctx.user.user_id}
