"""URA Chatbot API – FastAPI application.

Hardened for production per OWASP LLM Top 10 (2025), NIST SSDF, and
ISO/IEC 42001:2023 security controls.  Includes analytics, feedback,
and Prometheus-compatible metrics (2026 observability standards).
"""

import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from . import database as db
from .analytics import AnalyticsMiddleware, metrics
from .models import (
    AnalyticsDashboard,
    AnalyticsEvent,
    BatchClassifyRequest,
    BatchClassifyResponse,
    ChatRequest,
    ChatResponse,
    Citation,
    ClassifyRequest,
    ClassifyResponse,
    ExportConversationRequest,
    ExportTaxSummaryRequest,
    FAQResponse,
    FeedbackCommentRequest,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSummary,
    HealthResponse,
    SpeechHealthResponse,
    SynthesizeRequest,
    SynthesizeResponse,
    TagListResponse,
    TranscribeResponse,
    TranslateRequest,
    TranslateResponse,
    VoiceChatResponse,
)
from .service import ChatModel
from .speech_service import (
    SPEECH_ASR_BACKEND,
    SPEECH_ENABLED,
    SPEECH_MT_BACKEND,
    SPEECH_TTS_BACKEND,
    SpeechModel,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Production environment validation (NIST SSDF PO.1.1)
# ---------------------------------------------------------------------------
_INSECURE_DEV_SECRET = "dev-insecure-change-me"


def _validate_production_env() -> None:
    """Refuse to start if production env has insecure defaults.

    Checks critical configuration that, if left at dev defaults, would
    create exploitable vulnerabilities in production.  Aligns with
    NIST SP 800-218 (SSDF) PO.1.1 and OWASP LLM Top 10 (2025).
    """
    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env != "production":
        return

    errors: list[str] = []

    # AUTH_DEV_SECRET must be rotated in production
    if os.getenv("AUTH_DEV_SECRET", _INSECURE_DEV_SECRET) == _INSECURE_DEV_SECRET:
        errors.append(
            "AUTH_DEV_SECRET is still the default value. "
            "Set a strong, unique secret for production."
        )

    # CORS must not be localhost in production
    cors = os.getenv("CORS_ORIGINS", "")
    if "localhost" in cors or "127.0.0.1" in cors:
        errors.append(
            "CORS_ORIGINS contains localhost. "
            "Set explicit production origins (e.g. https://chat.ura.go.ug)."
        )

    # Rate limiting should use Redis for multi-worker deployments
    workers = int(os.getenv("WORKERS", "4"))
    if workers > 1 and not os.getenv("SLOWAPI_STORAGE_URI"):
        logger.warning(
            "SLOWAPI_STORAGE_URI not set with %d workers — rate limits are per-worker only. "
            "Set SLOWAPI_STORAGE_URI=redis://... for cluster-wide enforcement.",
            workers,
        )

    # LLM_TRUST_REMOTE_CODE must stay false (OWASP LLM03)
    if os.getenv("LLM_TRUST_REMOTE_CODE", "false").lower() in ("1", "true", "yes"):
        errors.append(
            "LLM_TRUST_REMOTE_CODE=true in production is a supply-chain risk (OWASP LLM03). "
            "Pin a trusted model revision instead."
        )

    # Model revision should be pinned for reproducibility (SLSA v1.2)
    if not os.getenv("LLM_MODEL_REVISION"):
        logger.warning(
            "LLM_MODEL_REVISION not set — model downloads are not reproducible. "
            "Pin a commit SHA for SLSA v1.2 compliance."
        )

    # STORE_RAW_PROMPTS must be off in production (NDPA §19 data minimisation)
    if os.getenv("STORE_RAW_PROMPTS", "false").lower() in ("1", "true", "yes"):
        errors.append(
            "STORE_RAW_PROMPTS=true in production violates NDPA §19 data minimisation. "
            "Set STORE_RAW_PROMPTS=false."
        )

    if errors:
        msg = "PRODUCTION SAFETY CHECK FAILED — refusing to start.\n" + "\n".join(
            f"  • {e}" for e in errors
        )
        logger.critical(msg)
        raise SystemExit(msg)

    logger.info("Production environment validation passed (%d warnings suppressed)", 0)


# ---------------------------------------------------------------------------
# Lifespan – replaces deprecated @app.on_event("startup")
# ---------------------------------------------------------------------------
_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{1,128}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down the ChatModel singleton."""
    # Production safety gate — blocks startup on insecure config
    _validate_production_env()

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

    # Speech pipeline (ASR + MT + TTS). Fails soft: backend still boots when
    # speech assets are missing, so the text API is never blocked by speech.
    if SPEECH_ENABLED:
        try:
            app.state.speech = SpeechModel()
            logger.info(
                "SpeechModel ready (asr=%s tts=%s mt=%s)",
                SPEECH_ASR_BACKEND,
                SPEECH_TTS_BACKEND,
                SPEECH_MT_BACKEND,
            )
        except Exception:
            logger.exception("SpeechModel initialisation failed — speech endpoints will 503")
            app.state.speech = None
    else:
        app.state.speech = None
        logger.info("Speech pipeline disabled via SPEECH_ENABLED=false")

    yield
    app.state.model = None
    try:
        if getattr(app.state, "speech", None) is not None:
            app.state.speech.close()
    except Exception:
        logger.warning("SpeechModel close raised", exc_info=True)
    app.state.speech = None
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


def get_speech_model(request: Request) -> SpeechModel:
    """Retrieve the SpeechModel from app state; 503 if unavailable."""
    speech = getattr(request.app.state, "speech", None)
    if speech is None:
        raise HTTPException(
            status_code=503,
            detail="Speech pipeline disabled or failed to initialise (set SPEECH_ENABLED=true)",
        )
    return speech


# ---------------------------------------------------------------------------
# CORS – hardened (no wildcard, no credentials, explicit methods)
# ---------------------------------------------------------------------------
_allowed_origins: list[str] = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()
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
        request_id,
        request.method,
        request.url.path,
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
@limiter.limit("120/minute")
def health_liveness(request: Request) -> dict:
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
def chat(
    body: ChatRequest, request: Request, model: ChatModel = Depends(get_model)
) -> ChatResponse:
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
                json.dumps(
                    {
                        "reason": result.get("escalation_reason", ""),
                        "topic_tag": topic_tag,
                    }
                ),
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
                yield {
                    "event": "metadata",
                    "data": json.dumps(
                        {
                            "sources": result.get("sources", []),
                            "citations": result.get("citations", []),
                            "faithfulness_score": result.get("faithfulness_score"),
                            "retrieval_mode": result.get("retrieval_mode"),
                            "model": result.get("model"),
                            "conversation_id": result.get("conversation_id"),
                            "locale": result.get("locale"),
                            "escalation_required": result.get("escalation_required", False),
                            "escalation_reason": result.get("escalation_reason", ""),
                        }
                    ),
                }
                yield {"event": "token", "data": result.get("reply", "")}
                yield {"event": "done", "data": ""}
                return

            # Send metadata first
            yield {
                "event": "metadata",
                "data": json.dumps(
                    {
                        "sources": result.get("sources", []),
                        "citations": result.get("citations", []),
                        "retrieval_mode": result.get("retrieval_mode"),
                        "model": result.get("model"),
                        "conversation_id": result.get("conversation_id"),
                        "locale": result.get("locale"),
                    }
                ),
            }

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
                yield {
                    "event": "grounding",
                    "data": json.dumps(
                        {
                            "faithfulness_score": faith,
                            "escalation_required": escalate,
                            "escalation_reason": esc_reason,
                        }
                    ),
                }

                # Cache the completed streaming response
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
                        },
                    )
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
# Speech endpoints (2026 — ASR / TTS / MT)
# ---------------------------------------------------------------------------
# Audio in/out uses raw bytes to avoid the base64 tax on the fast path.
# JSON responses carry a base64-encoded audio payload so the same route
# can be consumed from a simple JavaScript fetch().
@app.post("/v1/asr", response_model=TranscribeResponse, tags=["speech"])
@limiter.limit(_RATE_LIMIT)
async def transcribe_audio(
    request: Request,
    speech: SpeechModel = Depends(get_speech_model),
) -> TranscribeResponse:
    """Transcribe raw PCM audio posted as the request body.

    Pass ``sample_rate`` and optional ``language`` as query parameters. The
    request body must be raw PCM (int16 little-endian or float32, 1 channel).
    """
    sample_rate_raw = request.query_params.get("sample_rate", "16000")
    try:
        sample_rate = int(sample_rate_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="sample_rate must be an integer")
    if not 8000 <= sample_rate <= 48000:
        raise HTTPException(status_code=400, detail="sample_rate must be in [8000, 48000]")
    language = request.query_params.get("language")
    if language is not None and not re.match(r"^[a-z]{2}$", language):
        raise HTTPException(status_code=400, detail="language must be an ISO 639-1 code")

    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio body")
    # Hard cap: ~2 minutes at 16 kHz int16 stereo — protects the executor pool.
    MAX_BYTES = 16 * 1024 * 1024
    if len(audio_bytes) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="audio exceeds 16 MiB limit")

    result = speech.transcribe(audio_bytes, sample_rate=sample_rate, language=language)
    metrics.inc("speech_asr_total")
    if result.latency_s:
        metrics.observe("speech_asr_latency_s", result.latency_s)
    if result.error:
        metrics.inc("speech_asr_errors_total")
    return TranscribeResponse(
        text=result.text,
        language=result.language,
        duration_s=result.duration_s,
        latency_s=result.latency_s,
        rtf=result.rtf,
        backend=result.backend,
        error=result.error,
    )


@app.post("/v1/tts", response_model=SynthesizeResponse, tags=["speech"])
@limiter.limit(_RATE_LIMIT)
async def synthesize_audio(
    request: Request,
    body: SynthesizeRequest,
    speech: SpeechModel = Depends(get_speech_model),
) -> SynthesizeResponse:
    """Synthesize text to WAV audio. Returns base64-encoded WAV bytes."""
    import base64

    result = speech.synthesize(text=body.text, voice=body.voice, language=body.language)
    metrics.inc("speech_tts_total")
    if result.latency_s:
        metrics.observe("speech_tts_latency_s", result.latency_s)
    if result.error:
        metrics.inc("speech_tts_errors_total")
    return SynthesizeResponse(
        sample_rate=result.sample_rate,
        num_samples=result.num_samples,
        duration_s=result.duration_s,
        latency_s=result.latency_s,
        backend=result.backend,
        voice=result.voice,
        audio_base64=base64.b64encode(result.audio).decode("ascii") if result.audio else "",
        error=result.error,
    )


@app.post("/v1/translate", response_model=TranslateResponse, tags=["speech"])
@limiter.limit(_RATE_LIMIT)
async def translate_text(
    request: Request,
    body: TranslateRequest,
    speech: SpeechModel = Depends(get_speech_model),
) -> TranslateResponse:
    """Machine-translate text between English and Luganda."""
    if body.source_lang == body.target_lang:
        return TranslateResponse(
            text=body.text,
            source_lang=body.source_lang,
            target_lang=body.target_lang,
            latency_s=0.0,
            backend="passthrough",
        )
    result = speech.translate(
        text=body.text,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
    )
    metrics.inc("speech_mt_total")
    if result.latency_s:
        metrics.observe("speech_mt_latency_s", result.latency_s)
    if result.error:
        metrics.inc("speech_mt_errors_total")
    return TranslateResponse(
        text=result.text,
        source_lang=result.source_lang,
        target_lang=result.target_lang,
        latency_s=result.latency_s,
        backend=result.backend,
        error=result.error,
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@app.post("/v1/export/conversation", tags=["export"])
def export_conversation(body: ExportConversationRequest) -> Response:
    """Export a conversation as a branded PDF."""
    from .pdf_export import generate_conversation_pdf

    pdf_bytes = generate_conversation_pdf(
        body.messages,
        title=body.title,
        session_id=body.session_id,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ura_conversation_{int(time.time())}.pdf"',
        },
    )


@app.post("/v1/export/tax-summary", tags=["export"])
def export_tax_summary(body: ExportTaxSummaryRequest) -> Response:
    """Export a tax calculation summary as a branded PDF."""
    from .pdf_export import generate_tax_summary_pdf

    pdf_bytes = generate_tax_summary_pdf(
        body.calculation,
        taxpayer_ref=body.taxpayer_ref,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ura_tax_summary_{int(time.time())}.pdf"',
        },
    )


@app.get("/v1/speech/health", response_model=SpeechHealthResponse, tags=["speech"])
def speech_health(request: Request) -> SpeechHealthResponse:
    """Report whether the speech pipeline is ready to serve requests."""
    speech = getattr(request.app.state, "speech", None)
    enabled = SPEECH_ENABLED and speech is not None and speech.is_ready()
    return SpeechHealthResponse(
        status="ready" if enabled else "unavailable",
        enabled=SPEECH_ENABLED,
        asr_backend=SPEECH_ASR_BACKEND,
        tts_backend=SPEECH_TTS_BACKEND,
        mt_backend=SPEECH_MT_BACKEND,
    )


@app.post("/v1/voice/chat", response_model=VoiceChatResponse, tags=["speech"])
@limiter.limit(_RATE_LIMIT)
async def voice_chat(
    request: Request,
    speech: SpeechModel = Depends(get_speech_model),
    model: ChatModel = Depends(get_model),
) -> VoiceChatResponse:
    """Compound voice pipeline: audio -> ASR -> [MT] -> LLM -> [MT] -> TTS -> audio.

    The request body is raw PCM audio (int16 LE, mono). Query parameters carry
    the voice-chat metadata (language, voice, top_k, conversation_id, tts_enabled).
    """
    import asyncio
    import base64

    t_start = time.perf_counter()

    # --- Input validation (mirrors /v1/asr strictness) -----------------------
    language = request.query_params.get("language", "en")
    if not re.match(r"^[a-z]{2}$", language):
        raise HTTPException(
            status_code=400, detail="language must be an ISO 639-1 code (e.g. en, lg)"
        )

    voice = request.query_params.get("voice") or None
    if voice and not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", voice):
        raise HTTPException(status_code=400, detail="voice must match [a-zA-Z0-9_-]{1,64}")

    try:
        top_k = int(request.query_params.get("top_k", "4"))
    except ValueError:
        raise HTTPException(status_code=400, detail="top_k must be an integer")
    if not 1 <= top_k <= 10:
        raise HTTPException(status_code=400, detail="top_k must be in [1, 10]")

    conversation_id = request.query_params.get("conversation_id") or None
    if conversation_id and not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id format invalid")

    tts_enabled = request.query_params.get("tts_enabled", "true").lower() == "true"

    sample_rate_raw = request.query_params.get("sample_rate", "16000")
    try:
        sample_rate = int(sample_rate_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="sample_rate must be an integer")
    if not 8000 <= sample_rate <= 48000:
        raise HTTPException(status_code=400, detail="sample_rate must be in [8000, 48000]")

    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio body")
    if len(audio_bytes) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="audio exceeds 16 MiB limit")

    # Collect per-stage errors so they surface in the response
    stage_errors: list[str] = []
    session_id = request.headers.get("X-Session-ID") or None

    # --- 1. ASR ---------------------------------------------------------------
    asr_result = speech.transcribe(audio_bytes, sample_rate=sample_rate, language=language)
    asr_latency = asr_result.latency_s or 0.0
    metrics.inc("speech_asr_total")
    if asr_result.latency_s:
        metrics.observe("speech_asr_latency_s", asr_result.latency_s)
    if asr_result.error:
        metrics.inc("speech_asr_errors_total")
        return VoiceChatResponse(
            transcript="",
            error=f"ASR failed: {asr_result.error}",
            asr_latency_s=round(asr_latency, 3),
            asr_backend=asr_result.backend,
            total_latency_s=round(time.perf_counter() - t_start, 3),
        )
    transcript = asr_result.text
    detected_lang = asr_result.language or language

    # Guard: empty transcript (user said nothing / noise)
    if not transcript.strip():
        return VoiceChatResponse(
            transcript="",
            transcript_language=detected_lang,
            error="No speech detected. Please speak clearly and try again.",
            asr_latency_s=round(asr_latency, 3),
            asr_backend=asr_result.backend,
            total_latency_s=round(time.perf_counter() - t_start, 3),
        )

    # --- 2. MT (Luganda -> English) if user speaks Luganda --------------------
    mt_latency = 0.0
    mt_backend = ""
    chat_text = transcript
    if detected_lang == "lg":
        mt_result = speech.translate(transcript, source_lang="lg", target_lang="en")
        mt_latency += mt_result.latency_s
        mt_backend = mt_result.backend
        metrics.inc("speech_mt_total")
        if mt_result.latency_s:
            metrics.observe("speech_mt_latency_s", mt_result.latency_s)
        if mt_result.error:
            metrics.inc("speech_mt_errors_total")
            stage_errors.append(f"MT(lg->en): {mt_result.error}")
            logger.warning("Voice chat MT lg->en failed: %s", mt_result.error)
        else:
            chat_text = mt_result.text

    # --- 3. LLM chat ---------------------------------------------------------
    t_llm = time.perf_counter()
    chat_result = await asyncio.to_thread(
        model.generate,
        message=chat_text,
        conversation_id=conversation_id,
        top_k=top_k,
        locale="en",
        session_id=session_id,
        request_id=getattr(request.state, "request_id", None),
    )
    llm_latency = time.perf_counter() - t_llm
    reply_text = chat_result.get("reply", "")

    # --- 4. MT (English -> Luganda) if user language is Luganda ---------------
    if detected_lang == "lg" and reply_text:
        mt_result = speech.translate(reply_text, source_lang="en", target_lang="lg")
        mt_latency += mt_result.latency_s
        mt_backend = mt_backend or mt_result.backend
        metrics.inc("speech_mt_total")
        if mt_result.latency_s:
            metrics.observe("speech_mt_latency_s", mt_result.latency_s)
        if mt_result.error:
            metrics.inc("speech_mt_errors_total")
            stage_errors.append(f"MT(en->lg): {mt_result.error}")
            logger.warning("Voice chat MT en->lg failed: %s", mt_result.error)
        else:
            reply_text = mt_result.text

    # --- 5. TTS (synthesize reply in user's language) -------------------------
    tts_latency = 0.0
    tts_backend = ""
    audio_b64 = ""
    tts_sample_rate = 0
    tts_duration = 0.0
    if tts_enabled and reply_text:
        tts_result = speech.synthesize(text=reply_text, voice=voice, language=detected_lang)
        tts_latency = tts_result.latency_s
        tts_backend = tts_result.backend
        tts_sample_rate = tts_result.sample_rate
        tts_duration = tts_result.duration_s
        metrics.inc("speech_tts_total")
        if tts_result.latency_s:
            metrics.observe("speech_tts_latency_s", tts_result.latency_s)
        if tts_result.error:
            metrics.inc("speech_tts_errors_total")
            stage_errors.append(f"TTS: {tts_result.error}")
            logger.warning("Voice chat TTS failed: %s", tts_result.error)
        elif tts_result.audio:
            audio_b64 = base64.b64encode(tts_result.audio).decode("ascii")

    total_latency = time.perf_counter() - t_start
    metrics.observe("speech_voice_chat_latency_s", total_latency)

    # Safe citation parsing — malformed dicts must not crash the response
    safe_citations = []
    for c in chat_result.get("citations", []):
        try:
            safe_citations.append(Citation(**c) if isinstance(c, dict) else c)
        except Exception:
            logger.debug("Skipping malformed citation: %s", c)

    # Log voice conversation for analytics (mirrors /v1/chat logging)
    try:
        from .service import ChatModel as _CM

        db.log_conversation(
            session_id=session_id,
            user_message=_CM.redact_for_storage(transcript),
            bot_reply=_CM.redact_for_storage(reply_text),
            sources=json.dumps(chat_result.get("sources", [])),
            response_time_ms=round(total_latency * 1000, 2),
        )
    except Exception:
        logger.warning("Voice conversation logging failed", exc_info=True)

    return VoiceChatResponse(
        transcript=transcript,
        transcript_language=detected_lang,
        reply=reply_text,
        reply_audio_base64=audio_b64,
        sample_rate=tts_sample_rate,
        duration_s=tts_duration,
        sources=chat_result.get("sources", []),
        citations=safe_citations,
        faithfulness_score=chat_result.get("faithfulness_score"),
        retrieval_mode=chat_result.get("retrieval_mode", "keyword"),
        asr_latency_s=round(asr_latency, 3),
        mt_latency_s=round(mt_latency, 3),
        llm_latency_s=round(llm_latency, 3),
        tts_latency_s=round(tts_latency, 3),
        total_latency_s=round(total_latency, 3),
        asr_backend=asr_result.backend,
        tts_backend=tts_backend,
        mt_backend=mt_backend,
        error="; ".join(stage_errors) if stage_errors else None,
    )


# ---------------------------------------------------------------------------
# Classification endpoints
# ---------------------------------------------------------------------------
@app.post("/classify", response_model=ClassifyResponse, tags=["classification"])
def classify(request: ClassifyRequest, model: ChatModel = Depends(get_model)) -> ClassifyResponse:
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
        raise HTTPException(
            status_code=404, detail="Feedback entry not found or already has comment"
        )
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


@app.get("/v1/analytics/comparison", tags=["analytics"])
@limiter.limit(_RATE_LIMIT)
def analytics_comparison(request: Request, days: int = 30, dimension: str = "topic") -> dict:
    """Quality comparison by segment dimension (topic, locale, taxpayer_type).

    Returns per-segment average confidence and response time for visualization
    in the analytics dashboard comparison charts.

    Aligned with: NIST AI RMF MAP 2.3 (bias measurement), ACM §1.4 (fairness).
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be 1..365")
    if dimension not in ("topic", "locale", "retrieval_mode"):
        raise HTTPException(status_code=400, detail="dimension must be topic|locale|retrieval_mode")

    stats = db.get_conversation_stats(days)
    top_topics = stats.get("top_topics", [])

    if dimension == "topic":
        return {
            "dimension": "topic",
            "period_days": days,
            "segments": [
                {"name": t["tag"], "conversations": t["count"]}
                for t in top_topics
            ],
        }

    return {"dimension": dimension, "period_days": days, "segments": []}


@app.get("/metrics", tags=["system"])
@limiter.limit("30/minute")
def prometheus_metrics(request: Request) -> PlainTextResponse:
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
            "ura_eval_metric",
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
    granted = [db.grant_consent(row["id"], p, body.version) for p in body.purposes]
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
