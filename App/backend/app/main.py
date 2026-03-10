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
# Rate limiter (Phase 1 – production hardening, 30 req/min/IP on chat)
# ---------------------------------------------------------------------------
_RATE_LIMIT = os.getenv("RATE_LIMIT", "30/minute")
limiter = Limiter(key_func=get_remote_address, default_limits=[])

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
    t0 = time.perf_counter()

    result = model.generate(
        message=body.message,
        conversation_id=body.conversation_id,
        top_k=body.top_k,
        locale=body.locale,
        session_id=session_id or None,
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

    session_id = request.headers.get("X-Session-ID", "")

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
                # Run blocking LLM stream in thread pool
                def _stream_tokens():
                    return list(llm_module.generate_stream(
                        query=rewritten_query,  # use rewritten, not original
                        passages=hits,
                        conversation_history=conversation_history or None,
                        locale=body.locale,
                    ))

                tokens = await asyncio.to_thread(_stream_tokens)
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
