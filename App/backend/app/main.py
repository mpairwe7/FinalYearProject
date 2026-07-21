"""URA Chatbot API – FastAPI application.

Hardened for production per OWASP LLM Top 10 (2025), NIST SSDF, and
ISO/IEC 42001:2023 security controls.  Includes analytics, feedback,
and Prometheus-compatible metrics (2026 observability standards).
"""

import datetime
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocket
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from . import database as db
from . import documents
from .analytics import AnalyticsMiddleware, metrics
from .authority import authority_required, get_authority_status
from .auth import AuthContext, current_user, optional_user, require_role, require_user
from .auth.models import ConsentGrantRequest, ConsentWithdrawRequest, ProfileUpdateRequest
from .models import (
    AnalyticsDashboard,
    AnalyticsEvent,
    BatchClassifyRequest,
    BatchClassifyResponse,
    CFRelayChatRequest,
    CFRelayEmbedRequest,
    CFRelayVectorizeQueryRequest,
    ChatRequest,
    ChatResponse,
    Citation,
    ClassifyRequest,
    ClassifyResponse,
    DocumentAnalysisResponse,
    ExportConversationRequest,
    ExportTaxSummaryRequest,
    FAQResponse,
    FeedbackCommentRequest,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSummary,
    HealthResponse,
    OfflineAdminStats,
    OfflineStatusResponse,
    OfflineSyncRequest,
    OfflineSyncResponse,
    QuantizedModelsResponse,
    SpeechHealthResponse,
    SynthesizeRequest,
    SynthesizeResponse,
    TagListResponse,
    TranscribeResponse,
    TranslateRequest,
    TranslateResponse,
    VoiceChatResponse,
    VoiceVisionChatResponse,
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
_APP_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
_APP_LOGGER = logging.getLogger("app")
_APP_LOGGER.setLevel(_APP_LOG_LEVEL)
if not _APP_LOGGER.handlers:
    _APP_HANDLER = logging.StreamHandler()
    _APP_HANDLER.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    _APP_LOGGER.addHandler(_APP_HANDLER)
_APP_LOGGER.propagate = False

# ---------------------------------------------------------------------------
# Production environment validation (NIST SSDF PO.1.1)
# ---------------------------------------------------------------------------
_INSECURE_DEV_SECRET = "dev-insecure-change-me"  # noqa: S105

# Wall-clock budget for the batch /v1/voice/chat pipeline. Once spent, the
# reply-TTS leg is skipped (tts_skipped=True) so the text reply still beats
# the deployment's gateway timeout; the client narrates via /v1/tts instead.
VOICE_CHAT_BUDGET_S = float(os.getenv("VOICE_CHAT_BUDGET_S", "50"))


def _truthy_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


def _production_flag_enabled(name: str) -> bool:
    env_name = f"FLAG_{name.upper()}"
    val = os.getenv(env_name)
    if val is None:
        return True
    return val.lower() in ("1", "true", "yes", "on")


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

    auth_alg = os.getenv("AUTH_ALG", "HS256").upper()
    if auth_alg != "RS256":
        errors.append("AUTH_ALG must be RS256 in production; HS256 is dev-only.")
    for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"):
        if not os.getenv(name):
            errors.append(f"{name} must be set for production OIDC/JWT verification.")

    for flag_name in ("auth_required", "multi_tenant", "audit_ledger"):
        if not _production_flag_enabled(flag_name):
            errors.append(f"FLAG_{flag_name.upper()} must not be disabled in production.")

    # Cloudflare/Gemini fallbacks: if the flag is explicitly on, the credentials
    # it needs must be present (otherwise the fallback silently no-ops in prod).
    # NB: use an explicit-on check (not _production_flag_enabled, which treats
    # "unset" as on — that semantics is only for must-be-on security flags).
    if os.getenv("FLAG_CLOUDFLARE_FALLBACK", "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from .providers import config as _cloud_cfg

            if not _cloud_cfg.is_cloudflare_configured():
                errors.append(
                    "FLAG_CLOUDFLARE_FALLBACK=true but Cloudflare is not fully configured "
                    "(need CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, CF_AIG_GATEWAY, CF_AIG_TOKEN)."
                )
            if (
                os.getenv("DENSE_FALLBACK_BACKEND", "").strip().lower() == "workers_ai"
                and not _cloud_cfg.is_vectorize_configured()
            ):
                errors.append("DENSE_FALLBACK_BACKEND=workers_ai requires VECTORIZE_INDEX.")
            gemini_used = (
                os.getenv("LLM_FALLBACK_BACKEND", "").strip().lower() == "gemini"
                or os.getenv("TRANSLATE_FALLBACK_BACKEND", "").strip().lower() == "gemini"
            )
            if gemini_used and not _cloud_cfg.is_gemini_configured():
                errors.append(
                    "A *_FALLBACK_BACKEND=gemini is set but GEMINI_API_KEY (+ AI Gateway) is missing."
                )
        except Exception:
            errors.append(
                "FLAG_CLOUDFLARE_FALLBACK=true but the providers package failed to import."
            )

    # CORS must not be localhost in production
    cors = os.getenv("CORS_ORIGINS", "")
    if not cors:
        errors.append("CORS_ORIGINS must list explicit production origins.")
    if "*" in {origin.strip() for origin in cors.split(",")}:
        errors.append("CORS_ORIGINS must not contain wildcard origins in production.")
    if "localhost" in cors or "127.0.0.1" in cors or "ngrok" in cors:
        errors.append(
            "CORS_ORIGINS contains a development tunnel/local origin. "
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
        errors.append(
            "LLM_MODEL_REVISION is not set. Pin a model commit SHA for reproducible deploys."
        )

    if os.getenv("LLM_BACKEND", "local").lower() == "local":
        if not _truthy_env("LLM_SERIALIZE_LOCAL_GENERATION", "true"):
            errors.append(
                "LLM_SERIALIZE_LOCAL_GENERATION=false is unsafe with local mutable model state."
            )

    # STORE_RAW_PROMPTS must be off in production (NDPA §19 data minimisation)
    if os.getenv("STORE_RAW_PROMPTS", "false").lower() in ("1", "true", "yes"):
        errors.append(
            "STORE_RAW_PROMPTS=true in production violates NDPA §19 data minimisation. "
            "Set STORE_RAW_PROMPTS=false."
        )

    if os.getenv("ANALYTICS_BACKEND", "sqlite").lower() != "postgres":
        errors.append("ANALYTICS_BACKEND must be postgres in production.")
    if not os.getenv("POSTGRES_DSN"):
        errors.append("POSTGRES_DSN must be set in production.")

    index_key = os.getenv("INDEX_API_KEY", "")
    if index_key in ("", "dev-index-key"):
        errors.append("INDEX_API_KEY must be a strong non-dev operator token in production.")

    # P0-4: vectors must live in an external/managed Qdrant, not the ephemeral
    # in-container default that is wiped on restart.
    qdrant_url = os.getenv("QDRANT_URL", "")
    if not qdrant_url:
        errors.append(
            "QDRANT_URL must point at an external/managed Qdrant in production "
            "(the in-container default is not durable)."
        )
    elif any(h in qdrant_url for h in ("localhost", "127.0.0.1", "[::1]")):
        errors.append(
            "QDRANT_URL must not be localhost in production — use an external/managed Qdrant."
        )
    if qdrant_url and not os.getenv("QDRANT_API_KEY"):
        errors.append("QDRANT_API_KEY must be set when QDRANT_URL is configured in production.")

    # P0-4: the audit ledger and conversation memory are SQLite-backed via
    # ANALYTICS_DB_DIR even when analytics use Postgres, so that directory MUST
    # be a mounted persistent volume — otherwise the tamper-evident audit trail
    # and user memory are wiped on every container restart.
    data_dir = os.getenv("ANALYTICS_DB_DIR", "")
    if not data_dir:
        errors.append(
            "ANALYTICS_DB_DIR must be set to a mounted persistent volume in production "
            "(the SQLite-backed audit ledger and memory are otherwise lost on restart)."
        )
    elif not os.path.isabs(data_dir) or data_dir.startswith(("/tmp", "/var/tmp", "/dev/shm")):  # nosec B108 ephemeral-dir denylist, not temp-file creation  # noqa: S108
        errors.append(
            "ANALYTICS_DB_DIR must be an absolute path on a persistent volume in production "
            f"(got {data_dir!r}; ephemeral or relative paths are not durable)."
        )

    for redis_env in ("REDIS_URL", "SLOWAPI_STORAGE_URI"):
        redis_url = os.getenv(redis_env, "")
        if redis_url.startswith(("redis://", "rediss://")) and "@" not in redis_url:
            errors.append(f"{redis_env} must include Redis credentials in production.")

    if _truthy_env("SPEECH_ENABLED", "true") and not _production_flag_enabled("voice_consent"):
        errors.append("FLAG_VOICE_CONSENT must not be disabled when speech is enabled.")

    # Phase 6 of the agentic-WS rollout: the new WebSocket endpoint must
    # never serve anonymous traffic in production.
    if _production_flag_enabled("ws_chat") and not _production_flag_enabled("auth_required"):
        errors.append(
            "FLAG_WS_CHAT=true requires FLAG_AUTH_REQUIRED=true in production "
            "(anonymous WebSocket chat is not allowed)."
        )
    # The confirmation HMAC secret must come from real config in prod;
    # leaving the env unset would fall back to a per-process random key
    # and break any cross-replica confirmation flow.
    if _production_flag_enabled("ws_chat") and not os.getenv("WS_CONFIRM_HMAC_SECRET"):
        errors.append(
            "WS_CONFIRM_HMAC_SECRET must be set in production when FLAG_WS_CHAT=true "
            "(per-process random fallback is not safe across replicas)."
        )
    # P1-9: voice sockets spin up ASR/TTS/LLM work per connection and must
    # likewise never serve anonymous traffic in production.
    for _voice_flag in ("native_voice", "voice_streaming"):
        if _production_flag_enabled(_voice_flag) and not _production_flag_enabled("auth_required"):
            errors.append(
                f"FLAG_{_voice_flag.upper()}=true requires FLAG_AUTH_REQUIRED=true in production "
                "(anonymous voice WebSocket is not allowed)."
            )

    if authority_required():
        authority = get_authority_status()
        if not authority.get("ok"):
            detail = "; ".join(authority.get("errors") or ["authority manifest not ok"])
            errors.append(f"Fresh authority manifest required: {detail}.")

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


def _request_truthy_header(request: Request, name: str) -> bool:
    return request.headers.get(name, "").lower() in ("1", "true", "yes", "on")


def _require_voice_processing_consent(request: Request, ctx: AuthContext) -> None:
    """Enforce voice consent for both authenticated and anonymous users."""
    from .flags import flags

    if not flags.is_enabled("voice_consent"):
        return

    if ctx.user_id:
        from .voice_consent import require_voice_consent

        if require_voice_consent(ctx.user_id):
            return
        raise HTTPException(status_code=403, detail="voice recording consent required")

    if _request_truthy_header(request, "X-Voice-Consent"):
        return
    raise HTTPException(status_code=403, detail="anonymous voice consent header required")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down the ChatModel singleton."""
    # DNS-over-HTTPS workaround — must run BEFORE any outbound HTTPS (model
    # init, retriever, Cloudflare fallback, vLLM). On Crane Cloud / RENU the pod
    # has no working upstream DNS; this routes external hostname resolution
    # through 1.1.1.1 over TCP/443. No-op unless USE_DOH=true (doh_resolver.py).
    try:
        from . import doh_resolver

        if doh_resolver.is_enabled():
            doh_resolver.activate()
    except Exception:
        logger.warning("DoH resolver activation skipped", exc_info=True)

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
            # Give speech service access to the already-loaded LLM for
            # prompted translation (avoids loading a separate MT model).
            app.state.speech._chat_model = app.state.model
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

    # Voice consent schema (Phase 23) — creates voice_audit_log table.
    try:
        from .voice_consent import init_voice_consent_schema

        init_voice_consent_schema()
    except Exception:
        logger.debug("Voice consent schema init skipped", exc_info=True)

    # Offline RAG pipeline (Phase 25) — loads FAISS + ONNX bundle if present.
    from .flags import flags as _flags

    if _flags.is_enabled("offline_rag"):
        try:
            from .offline_rag import OfflineRAGPipeline

            app.state.offline_rag = OfflineRAGPipeline()
            if app.state.offline_rag.initialize():
                logger.info(
                    "Offline RAG ready: v%s, %d passages",
                    app.state.offline_rag.bundle_version,
                    app.state.offline_rag.passage_count,
                )
            else:
                logger.info("Offline RAG bundle not found — offline mode unavailable")
        except Exception:
            logger.warning("Offline RAG init failed", exc_info=True)
            app.state.offline_rag = None
    else:
        app.state.offline_rag = None

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
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3300").split(",") if o.strip()
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
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
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


@app.get("/v1/authority/status", tags=["admin"])
def authority_status(
    _: AuthContext = Depends(require_role("ura_staff", "ura_admin", "ura_auditor")),
) -> dict[str, Any]:
    """Return authority-manifest validation status for release/ops checks."""
    return get_authority_status()


# ---------------------------------------------------------------------------
# Chat endpoint (with conversation logging)
# ---------------------------------------------------------------------------
@app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
@limiter.limit(_RATE_LIMIT)
def chat(
    body: ChatRequest,
    request: Request,
    model: ChatModel = Depends(get_model),
    ctx: AuthContext = Depends(optional_user),
) -> ChatResponse:
    session_id = request.headers.get("X-Session-ID", "")
    request_id = getattr(request.state, "request_id", None)
    t0 = time.perf_counter()

    attachments = documents.resolve_attachments(body.attachment_ids, session_id=session_id)
    result = model.generate(
        message=body.message,
        conversation_id=body.conversation_id,
        top_k=body.top_k,
        locale=body.locale,
        session_id=session_id or None,
        request_id=request_id,
        user_id=ctx.user_id or None,
        tenant_id=ctx.tenant_id,
        user_role=ctx.role,
        granted_purposes=ctx.user.granted_purposes if ctx.user else [],
        attachments=attachments or None,
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

        db.log_conversation(
            session_id=session_id or None,
            conversation_id=result.get("conversation_id"),
            user_id=ctx.user_id or "",
            user_message=_CM.redact_for_storage(body.message),
            bot_reply=_CM.redact_for_storage(result["reply"]),
            sources=json.dumps(result.get("sources", [])),
            contexts=_CM.contexts_json(result),
            response_time_ms=round(elapsed_ms, 2),
            confidence=confidence,
            topic_tag=topic_tag,
        )
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
async def chat_stream(
    body: ChatRequest,
    request: Request,
    model: ChatModel = Depends(get_model),
    ctx: AuthContext = Depends(optional_user),
):
    """Server-Sent Events streaming chat — tokens arrive progressively.

    Thin adapter around :func:`service.run_chat_turn`.  Maps the
    transport-agnostic ``(event_type, payload)`` tuples into SSE frames.
    """
    from . import service as service_module

    session_id = request.headers.get("X-Session-ID", "")
    request_id = getattr(request.state, "request_id", None)
    attachments = documents.resolve_attachments(body.attachment_ids, session_id=session_id)

    async def event_generator():
        # Phase 2: SSE buffers agentic events into a compact ``agent_trace``
        # summary emitted just before ``grounding``.  Live tool-call frames
        # are exclusive to the WS path.
        agent_trace: list[dict[str, Any]] = []
        async for event_type, payload in service_module.run_chat_turn(
            model,
            message=body.message,
            conversation_id=body.conversation_id,
            top_k=body.top_k,
            locale=body.locale,
            session_id=session_id or None,
            request_id=request_id,
            user_id=ctx.user_id or None,
            tenant_id=ctx.tenant_id,
            should_continue=lambda: _sse_not_disconnected(request),
            sentence_batching=True,  # SSE keeps historical behaviour
            user_role=getattr(ctx, "role", "public"),
            granted_purposes=getattr(ctx, "granted_purposes", []) or [],
            attachments=attachments or None,
        ):
            if event_type == "_keepalive":
                yield {
                    "comment": f"ping - {datetime.datetime.now(datetime.timezone.utc).isoformat()}"
                }
                continue
            if event_type == "_log":
                _log_stream_conversation(body, session_id, payload, user_id=ctx.user_id or "")
                continue
            if event_type.startswith(("retrieval.", "iteration.", "tool_call.")):
                # Buffer for the agent_trace summary; do not forward live.
                event_dict = payload if isinstance(payload, dict) else {"value": payload}
                agent_trace.append({"type": event_type, **{k: v for k, v in event_dict.items() if k != "type"}})
                continue
            if event_type == "grounding" and agent_trace:
                yield {"event": "agent_trace", "data": json.dumps(agent_trace)}
                agent_trace = []
            if event_type == "metadata" or event_type == "grounding":
                yield {"event": event_type, "data": json.dumps(payload)}
            elif event_type == "error":
                yield {"event": "error", "data": payload.get("message", "Internal server error")}
            else:  # token / revision / done
                yield {"event": event_type, "data": payload if isinstance(payload, str) else ""}

        # Flush trailing trace (e.g. if grounding was skipped).
        if agent_trace:
            yield {"event": "agent_trace", "data": json.dumps(agent_trace)}

    return EventSourceResponse(event_generator())


async def _sse_not_disconnected(request: Request) -> bool:
    """Adapter for ``run_chat_turn.should_continue`` over Starlette HTTP."""
    return not (await request.is_disconnected())


def _log_stream_conversation(
    body: ChatRequest,
    session_id: str,
    log_payload: dict[str, Any],
    user_id: str = "",
) -> None:
    """Mirror the old SSE ``finally`` block — log to analytics DB."""
    from .service import ChatModel as _CM

    result = log_payload.get("result") or {}
    full_reply = log_payload.get("full_reply", "")
    elapsed_ms = log_payload.get("elapsed_ms", 0.0)
    try:
        db.log_conversation(
            session_id=session_id or None,
            conversation_id=result.get("conversation_id"),
            user_message=_CM.redact_for_storage(body.message),
            bot_reply=_CM.redact_for_storage(full_reply),
            sources=json.dumps(result.get("sources", []) if result else []),
            contexts=_CM.contexts_json(result),
            response_time_ms=round(elapsed_ms, 2),
            user_id=user_id,
        )
    except Exception:
        logger.warning("Stream conversation logging failed", exc_info=True)


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
    ctx: AuthContext = Depends(optional_user),
) -> TranscribeResponse:
    """Transcribe raw PCM audio posted as the request body.

    Pass ``sample_rate`` and optional ``language`` as query parameters. The
    request body must be raw PCM (int16 little-endian or float32, 1 channel).
    """
    sample_rate_raw = request.query_params.get("sample_rate", "16000")
    try:
        sample_rate = int(sample_rate_raw)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="sample_rate must be an integer") from err
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
    _require_voice_processing_consent(request, ctx)

    # Audio format auto-detected: WAV, WebM/Opus, OGG, MP3, or raw PCM.
    # Content-Type header is advisory; the decoder sniffs the magic bytes.
    content_type = request.headers.get("content-type", "application/octet-stream")
    logger.debug(
        "ASR: %d bytes, content-type=%s, sample_rate=%d, language=%s",
        len(audio_bytes), content_type, sample_rate, language or "auto",
    )

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
    _ctx: AuthContext = Depends(optional_user),
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
    _ctx: AuthContext = Depends(optional_user),
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
def export_conversation(
    body: ExportConversationRequest,
    _ctx: AuthContext = Depends(optional_user),
) -> Response:
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
def export_tax_summary(
    body: ExportTaxSummaryRequest,
    _ctx: AuthContext = Depends(current_user),
) -> Response:
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


# ---------------------------------------------------------------------------
# Document attachments (analysis + report)
# ---------------------------------------------------------------------------


@app.post(
    "/v1/documents/analyze",
    response_model=DocumentAnalysisResponse,
    tags=["documents"],
)
@limiter.limit(_RATE_LIMIT)
async def analyze_uploaded_document(
    request: Request,
    ctx: AuthContext = Depends(optional_user),
) -> DocumentAnalysisResponse:
    """Analyse an uploaded document (PDF, DOCX, XLSX, CSV, image, or text).

    Accepts multipart/form-data with a single ``file`` part. Extracts text
    and tables, classifies the document against the URA taxonomy, and pulls
    URA-specific fields (TINs, UGX amounts, dates, references).

    The returned ``document_id`` can be passed in chat ``attachment_ids``
    to ground answers on the document, and used with
    ``GET /v1/documents/{document_id}/report`` to download a PDF report.
    Documents are held in memory only and expire after a TTL.
    """
    import asyncio

    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        raise HTTPException(status_code=422, detail="Missing 'file' part in form data")

    data = await upload.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty file")
    if len(data) > documents.MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {documents.MAX_FILE_BYTES // (1024 * 1024)} MiB limit",
        )

    session_id = request.headers.get("X-Session-ID", "")
    try:
        record = await asyncio.to_thread(
            documents.analyze_document,
            data,
            upload.filename or "document",
            upload.content_type or "",
            session_id=session_id,
            user_id=ctx.user_id or "",
        )
    except documents.UnsupportedDocumentError as err:
        raise HTTPException(status_code=415, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err

    metrics.inc("documents_analyzed_total")
    return DocumentAnalysisResponse(**record.to_response_payload())


@app.get("/v1/documents/{document_id}/report", tags=["documents"])
@limiter.limit(_RATE_LIMIT)
def document_report(
    request: Request,
    document_id: str = Path(..., pattern=r"^[a-f0-9]{32}$"),
    _ctx: AuthContext = Depends(optional_user),
) -> Response:
    """Download the branded PDF analysis report for an analysed document."""
    record = documents.get_document(
        document_id, session_id=request.headers.get("X-Session-ID", "")
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found or expired")

    # Lazy import AFTER the 404 check so unknown/expired ids stay 404 even
    # on a runtime without fpdf2.
    from .pdf_export import generate_document_report_pdf

    pdf_bytes = generate_document_report_pdf(record.to_report_payload())
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ura_document_report_{document_id[:8]}.pdf"'
            ),
        },
    )


@app.get("/v1/speech/health", response_model=SpeechHealthResponse, tags=["speech"])
def speech_health(
    request: Request,
    _ctx: AuthContext = Depends(optional_user),
) -> SpeechHealthResponse:
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
    ctx: AuthContext = Depends(optional_user),
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
    except ValueError as err:
        raise HTTPException(status_code=400, detail="top_k must be an integer") from err
    if not 1 <= top_k <= 10:
        raise HTTPException(status_code=400, detail="top_k must be in [1, 10]")

    conversation_id = request.query_params.get("conversation_id") or None
    if conversation_id and not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id format invalid")

    tts_enabled = request.query_params.get("tts_enabled", "true").lower() == "true"

    sample_rate_raw = request.query_params.get("sample_rate", "16000")
    try:
        sample_rate = int(sample_rate_raw)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="sample_rate must be an integer") from err
    if not 8000 <= sample_rate <= 48000:
        raise HTTPException(status_code=400, detail="sample_rate must be in [8000, 48000]")

    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio body")
    if len(audio_bytes) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="audio exceeds 16 MiB limit")
    _require_voice_processing_consent(request, ctx)

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
        user_id=ctx.user_id or None,
        tenant_id=ctx.tenant_id,
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
    # Budget guard: on slow speech tiers (cloud Sunbird can take 30s+ per
    # call) the four-stage pipeline can outlive the deployment's gateway
    # timeout and the client receives a 504 with NOTHING — worse than a
    # text-only reply. When the request has already burned the budget,
    # return the text now (tts_skipped=True) and let the client fetch the
    # narration as a separate single-leg /v1/tts request.
    tts_latency = 0.0
    tts_backend = ""
    audio_b64 = ""
    tts_sample_rate = 0
    tts_duration = 0.0
    tts_skipped = False
    if tts_enabled and reply_text:
        elapsed_s = time.perf_counter() - t_start
        if elapsed_s > VOICE_CHAT_BUDGET_S:
            tts_skipped = True
            metrics.inc("speech_tts_skipped_total")
            logger.info(
                "Voice chat reply-TTS skipped: %.1fs elapsed exceeds %.0fs budget",
                elapsed_s,
                VOICE_CHAT_BUDGET_S,
            )
        else:
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
            conversation_id=chat_result.get("conversation_id") or conversation_id,
            user_id=ctx.user_id or "",
            user_message=_CM.redact_for_storage(transcript),
            bot_reply=_CM.redact_for_storage(reply_text),
            sources=json.dumps(chat_result.get("sources", [])),
            contexts=_CM.contexts_json(chat_result),
            response_time_ms=round(total_latency * 1000, 2),
        )
    except Exception:
        logger.warning("Voice conversation logging failed", exc_info=True)

    return VoiceChatResponse(
        transcript=transcript,
        transcript_language=detected_lang,
        conversation_id=chat_result.get("conversation_id") or conversation_id,
        reply=reply_text,
        reply_audio_base64=audio_b64,
        tts_skipped=tts_skipped,
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
# Streaming voice chat (Phase 23 — WebSocket)
# ---------------------------------------------------------------------------


@app.websocket("/v1/voice/chat/stream")
async def voice_chat_stream_ws(websocket: WebSocket) -> None:
    """WebSocket endpoint for streaming voice chat with VAD + barge-in.

    Gated by the ``voice_streaming`` feature flag.  See ``voice_ws.py``
    for the full protocol specification.
    """
    from .voice_ws import voice_stream_ws

    await voice_stream_ws(websocket, app)


@app.websocket("/v2/voice/chat/stream")
async def voice_chat_stream_ws_v2(websocket: WebSocket) -> None:
    """V2 WebSocket — native voice-to-voice with streaming TTS + vision.

    Gated by the ``native_voice`` feature flag.  Extends the V1 protocol
    with partial transcripts, speculative prefetch, token-level TTS
    (CosyVoice2), and parallel vision encoding.  See ``voice_ws_v2.py``.
    """
    from .voice_ws_v2 import voice_stream_ws_v2

    await voice_stream_ws_v2(websocket, app)


@app.websocket("/v2/chat/stream")
async def chat_stream_ws_v2(websocket: WebSocket) -> None:
    """V2 WebSocket — persistent text chat with agentic event surface.

    Gated by the ``ws_chat`` feature flag.  See ``chat_ws_v2.py`` and
    ``docs/ws_chat_protocol.md``.  Phase 0 ships lifecycle + protocol
    negotiation only; ``response.create`` returns a ``not_implemented``
    error until Phase 1 wires the existing chat pipeline through.
    """
    from .chat_ws_v2 import chat_stream_ws

    await chat_stream_ws(websocket, app)


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


def _has_valid_ops_key(request: Request) -> bool:
    """Return True when the request presents the configured ops API key."""
    if not _INDEX_API_KEY:
        return False
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {_INDEX_API_KEY}"


def _require_ops_key(request: Request) -> None:
    """Require the configured bearer token for operator-only endpoints."""
    if not _INDEX_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="INDEX_API_KEY not configured for operator endpoint",
        )
    if not _has_valid_ops_key(request):
        raise HTTPException(status_code=403, detail="Invalid or missing INDEX_API_KEY")


def _require_relay_key(request: Request) -> None:
    """Require the configured bearer token for the Cloudflare relay endpoints.

    Deliberately a separate secret from ``INDEX_API_KEY`` — this endpoint lets
    another deployment (e.g. the HF Space, when its own egress to Cloudflare
    is blocked) make Cloudflare calls through this one, so it gets its own
    credential rather than reusing an unrelated operator key.
    """
    from .providers.config import get_cloud_settings

    secret = get_cloud_settings().cf_relay_secret.get_secret_value()
    if not secret:
        raise HTTPException(status_code=503, detail="CF_RELAY_SECRET not configured")
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {secret}":
        raise HTTPException(status_code=403, detail="Invalid or missing relay credentials")


def require_admin_access(
    request: Request,
) -> AuthContext:
    """Require an authenticated staff user or a valid operator API key.

    Ticket triage endpoints should not be public. During OIDC rollout we
    still allow the existing operator key as a break-glass path, but only
    when it is explicitly configured.
    """
    if _has_valid_ops_key(request):
        return AuthContext()

    ctx = current_user(request, request.headers.get("Authorization"))
    if not ctx.is_authenticated:
        if _INDEX_API_KEY:
            raise HTTPException(status_code=401, detail="authentication required")
        raise HTTPException(
            status_code=503,
            detail="admin endpoint unavailable: configure OIDC staff auth or INDEX_API_KEY",
        )
    if ctx.user and ctx.user.is_staff:
        return ctx
    raise HTTPException(status_code=403, detail="staff role required")


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
    _require_ops_key(request)

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
# Cloudflare relay — lets another deployment (e.g. the HF Space, when its own
# egress to Cloudflare is blocked) make Vectorize/Workers AI calls through
# this one. Requires ``Authorization: Bearer <CF_RELAY_SECRET>`` (a dedicated
# secret, separate from the real Cloudflare token, which never leaves this
# process — see providers/config.py). Deliberately narrow: these three ops are
# exactly what dense-retrieval fallback and the cloud-primary LLM chain need
# (embed the query, search Vectorize, run a chat completion) and nothing else
# is exposed, so there is no open-ended forwarding surface to worry about.
# ---------------------------------------------------------------------------
@app.post("/internal/cf-relay/workers-ai-embed", include_in_schema=False)
def cf_relay_workers_ai_embed(request: Request, body: CFRelayEmbedRequest) -> dict:
    _require_relay_key(request)
    from .providers import gateway as _gw

    # No caller-supplied model — always the retrieval embedding model
    # (gateway.workers_ai_embed's own default); see CFRelayEmbedRequest.
    vectors = _gw.workers_ai_embed(body.texts)
    return {"vectors": vectors}


@app.post("/internal/cf-relay/vectorize-query", include_in_schema=False)
def cf_relay_vectorize_query(request: Request, body: CFRelayVectorizeQueryRequest) -> dict:
    _require_relay_key(request)
    from .providers import vectorize as _vz

    hits = _vz.vectorize_query(body.vector, top_k=body.top_k, vector_filter=body.vector_filter)
    return {"hits": hits}


@app.post("/internal/cf-relay/workers-ai-chat", include_in_schema=False)
def cf_relay_workers_ai_chat(request: Request, body: CFRelayChatRequest) -> dict:
    _require_relay_key(request)
    from .providers import gateway as _gw

    # body.model already validated against routing.ALLOWED_CHAT_MODELS —
    # see CFRelayChatRequest._model_must_be_allowlisted.
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    text = _gw.workers_ai_chat(
        messages, body.model, max_tokens=body.max_tokens, temperature=body.temperature
    )
    return {"text": text}


# ---------------------------------------------------------------------------
# Feedback endpoints
# ---------------------------------------------------------------------------
@app.post("/v1/feedback", response_model=FeedbackResponse, tags=["feedback"])
def submit_feedback(
    body: FeedbackRequest,
    _ctx: AuthContext = Depends(current_user),
) -> FeedbackResponse:
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
    _ctx: AuthContext = Depends(current_user),
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
def feedback_summary(
    days: int = 30,
    _ctx: AuthContext = Depends(require_admin_access),
) -> FeedbackSummary:
    """Aggregated feedback statistics for the specified period."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    return FeedbackSummary(**db.get_feedback_summary(days))


# ---------------------------------------------------------------------------
# Analytics & metrics endpoints
# ---------------------------------------------------------------------------
@app.post("/v1/analytics/event", tags=["analytics"])
def track_analytics_event(
    body: AnalyticsEvent,
    _ctx: AuthContext = Depends(current_user),
) -> dict:
    """Track a client-side analytics event."""
    db.track_event(
        event_type=body.event_type,
        event_data=json.dumps(body.event_data),
        session_id=body.session_id,
    )
    return {"status": "ok"}


@app.get("/v1/analytics/dashboard", response_model=AnalyticsDashboard, tags=["analytics"])
def analytics_dashboard(
    days: int = 30,
    _ctx: AuthContext = Depends(require_admin_access),
) -> AnalyticsDashboard:
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
def analytics_comparison(
    request: Request,
    days: int = 30,
    dimension: str = "topic",
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
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
            "segments": [{"name": t["tag"], "conversations": t["count"]} for t in top_topics],
        }

    return {"dimension": dimension, "period_days": days, "segments": []}


@app.get("/metrics", tags=["system"])
@limiter.limit("30/minute")
def prometheus_metrics(
    request: Request,
    _ctx: AuthContext = Depends(require_admin_access),
) -> PlainTextResponse:
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
    _require_ops_key(request)
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
    ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """List escalation tickets for URA staff triage.

    Requires an authenticated staff/admin user, or the configured
    operator key as a break-glass fallback. Filter by status via the
    query string: ``?status=open``.
    """
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
        "auth_mode": "user" if ctx.is_authenticated else "ops_key",
    }


@app.get("/v1/admin/tickets/stats", tags=["admin"])
def ticket_stats_endpoint(
    request: Request,
    days: int = 30,
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Aggregate ticket statistics for the admin dashboard."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be 1..365")
    return db.ticket_stats(days=days)


@app.get("/v1/admin/tickets/{ticket_id}", tags=["admin"])
def get_ticket_endpoint(
    request: Request,
    ticket_id: str = Path(..., pattern=r"^[a-f0-9-]{1,64}$"),
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Fetch a single ticket by id."""
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
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Update a ticket's status/assignee/note/priority."""
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


@app.get("/v1/admin/voice_audit", tags=["admin"])
def voice_audit_endpoint(
    request: Request,
    user_id: str | None = None,
    session_id: str | None = None,
    days: int = 30,
    limit: int = 100,
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Voice audit log for regulatory compliance and admin review."""
    from .voice_consent import get_voice_audit_log, voice_audit_stats

    import time as _time

    since = _time.time() - (days * 86400) if days > 0 else None
    entries = get_voice_audit_log(
        user_id=user_id,
        session_id=session_id,
        since=since,
        limit=min(limit, 500),
    )
    stats = voice_audit_stats(days=days)
    return {"entries": entries, "stats": stats}


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
    # UDPA: withdrawal must cease processing — purge the personalization memory
    # built under that consent (future reads are already consent-gated).
    if "personalization" in body.purposes:
        from .memory.service import get_memory_service

        get_memory_service().forget_user(ctx.user.user_id)
    return {"user_id": row["id"], "withdrawn": withdrawn}


@app.get("/v1/me/export", tags=["me"])
def me_export(ctx: AuthContext = Depends(require_user)) -> dict:
    """UDPA 2019 data-portability export — identity, profile, consents, chat
    history (+ escalation tickets), and personalization memory facts."""
    from .memory.service import get_memory_service

    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    data = db.export_user_data(row["id"], external_id=ctx.user.user_id)
    data["facts"] = get_memory_service().export_user(ctx.user.user_id)["facts"]
    return data


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
    from .memory.service import get_memory_service

    counts = db.delete_user_cascade(row["id"], external_id=ctx.user.user_id)
    counts["memory"] = sum(get_memory_service().forget_user(ctx.user.user_id).values())
    return {"deleted": counts, "external_id": ctx.user.user_id}


# ---------------------------------------------------------------------------
# Evaluation results — serves pre-computed Results/ JSON for the dashboard
# ---------------------------------------------------------------------------
@app.get("/v1/evaluation/results", tags=["evaluation"])
def evaluation_results(_ctx: AuthContext = Depends(require_admin_access)) -> dict:
    """Serve all pre-computed evaluation metrics for the IEEE-standard dashboard.

    Reads JSON files from the ``Results/`` directory relative to the
    project root and returns a consolidated bundle.
    """
    import json as _json
    from pathlib import Path

    from ._root import PROJECT_ROOT as _pr2

    results_dir = _pr2 / "Results"
    metrics_dir = results_dir / "metrics"

    def _load(path: Path) -> dict | list | None:
        try:
            return _json.loads(path.read_text()) if path.exists() else None
        except Exception:
            return None

    return {
        "rag_evaluation": _load(results_dir / "rag_evaluation_results.json"),
        "rag_quality_gates": _load(results_dir / "rag_quality_gates.json"),
        "safety_evaluation": _load(results_dir / "safety_evaluation_results.json"),
        "red_team_report": _load(results_dir / "red_team_report.json"),
        "calibration": _load(metrics_dir / "calibration_report.json"),
        "reliability_curve": _load(metrics_dir / "reliability_curve.json"),
        "coverage_accuracy": _load(metrics_dir / "coverage_accuracy.json"),
        "benchmark": _load(metrics_dir / "benchmark.json"),
        "tokenizer_audit": _load(metrics_dir / "tokenizer_audit.json"),
        "speech_metrics": _load(metrics_dir / "speech_metrics.json"),
        "mt_metrics": _load(metrics_dir / "mt_metrics.json"),
        "tts_metrics": _load(metrics_dir / "tts_metrics.json"),
    }


# ---------------------------------------------------------------------------
# IEEE artifact export — generates figures/tables as PNG images
# ---------------------------------------------------------------------------
@app.post("/v1/export/artifacts", tags=["evaluation"])
def export_artifacts(request: Request) -> dict:
    """Generate IEEE-standard figures and tables as PNG images.

    Reads all pre-computed Results/ JSON files and renders publication-
    quality charts, graphs, and metric tables into the Artifacts/ folder
    for inclusion in the final year project report.

    Requires the same ``Authorization: Bearer <INDEX_API_KEY>`` header
    as ``/v1/index`` and ``/v1/evaluate``.
    """
    _require_ops_key(request)

    from .artifact_export import export_all

    files = export_all()
    return {
        "status": "ok",
        "count": len(files),
        "files": files,
    }


# ---------------------------------------------------------------------------
# Quantized Models (Phase 24)
# ---------------------------------------------------------------------------


@app.get("/v1/models/quantized", response_model=QuantizedModelsResponse, tags=["models"])
@limiter.limit("60/minute")
def list_quantized_models(
    request: Request,
    _ctx: AuthContext = Depends(current_user),
) -> QuantizedModelsResponse:
    """List available quantized model versions and their metadata.

    Feature-flagged behind ``FLAG_QUANTIZATION``.
    """
    from .flags import flags
    from .models import QuantizedModelInfo

    if not flags.is_enabled("quantization"):
        return QuantizedModelsResponse(models=[], total=0)

    models: list[QuantizedModelInfo] = []

    # Scan artifacts/quantized for manifest files
    from ._root import PROJECT_ROOT as _pr

    quantized_dir = _pr / "artifacts" / "quantized"
    if quantized_dir.exists():
        for manifest_path in quantized_dir.rglob("manifest.json"):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
                for result in manifest.get("results", []):
                    if result.get("status") != "success":
                        continue
                    models.append(QuantizedModelInfo(
                        name=f"{result.get('model', 'unknown').split('/')[-1]}-{result.get('quant_type', '')}",
                        format=result.get("format", "unknown"),
                        quant_type=result.get("quant_type", "unknown"),
                        size_mb=result.get("size_mb", 0),
                        sha256=result.get("sha256", ""),
                        created_at=result.get("created_at", ""),
                        status="available",
                    ))
            except Exception:
                logger.debug("Failed to read quantized manifest: %s", manifest_path, exc_info=True)

    return QuantizedModelsResponse(
        models=models,
        total=len(models),
        baseline_faithfulness=0.93,
    )


# ---------------------------------------------------------------------------
# Offline RAG (Phase 25)
# ---------------------------------------------------------------------------


@app.get("/v1/offline/status", response_model=OfflineStatusResponse, tags=["offline"])
@limiter.limit("60/minute")
def offline_status(
    request: Request,
    _ctx: AuthContext = Depends(current_user),
) -> OfflineStatusResponse:
    """Get offline bundle availability and sync status.

    Feature-flagged behind ``FLAG_OFFLINE_RAG``.
    """
    from .flags import flags
    from .models import OfflineBundleInfo

    if not flags.is_enabled("offline_rag"):
        return OfflineStatusResponse(available=False)

    from .offline_bundle import BundleManager

    manager = BundleManager()
    info = manager.get_info()

    bundle_info = None
    if info.available:
        bundle_info = OfflineBundleInfo(
            version=info.version,
            size_bytes=info.size_bytes,
            size_mb=info.size_mb,
            passage_count=info.passage_count,
            index_dim=info.index_dim,
            sha256=info.sha256,
            created_at=info.created_at,
            min_app_version=info.min_app_version,
        )

    return OfflineStatusResponse(
        available=info.available,
        bundle=bundle_info,
        sync_enabled=flags.is_enabled("offline_sync"),
    )


@app.post("/v1/offline/sync", response_model=OfflineSyncResponse, tags=["offline"])
@limiter.limit("10/minute")
def offline_sync(
    body: OfflineSyncRequest,
    request: Request,
    _ctx: AuthContext = Depends(current_user),
) -> OfflineSyncResponse:
    """Compute delta sync for a client's offline bundle.

    Client sends its current version + chunk hashes; server returns
    only the changed chunks.  Feature-flagged behind ``FLAG_OFFLINE_SYNC``.
    """
    from .flags import flags

    if not flags.is_enabled("offline_sync"):
        raise HTTPException(
            status_code=404,
            detail="Offline sync is disabled (FLAG_OFFLINE_SYNC=false)",
        )

    from .offline_sync import OfflineSyncEngine, SyncEvent

    engine = OfflineSyncEngine()
    if not engine.initialize():
        raise HTTPException(status_code=503, detail="Sync engine not available")

    t0 = time.perf_counter()
    delta = engine.compute_delta(
        client_version=body.client_version,
        client_chunk_hashes=body.client_chunk_hashes,
        max_download_bytes=body.max_download_bytes,
    )
    duration = time.perf_counter() - t0

    # Record sync event
    engine.record_sync(SyncEvent(
        device_id=body.device_id,
        client_version=body.client_version,
        server_version=delta.server_version,
        sync_type="full" if delta.needs_full_sync else "delta",
        chunks_sent=len(delta.changed_chunks),
        bytes_sent=delta.total_download_bytes,
        duration_s=round(duration, 3),
        timestamp=time.time(),
    ))

    return OfflineSyncResponse(
        server_version=delta.server_version,
        needs_full_sync=delta.needs_full_sync,
        changed_chunks=delta.changed_chunks,
        deleted_chunk_ids=delta.deleted_chunk_ids,
        total_download_bytes=delta.total_download_bytes,
        estimated_sync_seconds=delta.estimated_sync_seconds,
    )


@app.get("/v1/offline/bundle", tags=["offline"])
@limiter.limit("5/minute")
def download_offline_bundle(
    request: Request,
    _ctx: AuthContext = Depends(current_user),
):
    """Download the latest offline RAG bundle.

    Returns the compressed bundle archive for offline use.
    Feature-flagged behind ``FLAG_OFFLINE_BUNDLE_API``.
    """
    from .flags import flags

    if not flags.is_enabled("offline_bundle_api"):
        raise HTTPException(
            status_code=404,
            detail="Offline bundle API is disabled (FLAG_OFFLINE_BUNDLE_API=false)",
        )

    from .offline_bundle import BundleManager

    manager = BundleManager()
    bundle_path = manager.get_bundle_path()

    if bundle_path is None or not bundle_path.exists():
        raise HTTPException(status_code=404, detail="No offline bundle available")

    from fastapi.responses import FileResponse

    size = bundle_path.stat().st_size
    manager.record_download(size)
    metrics.inc("offline_bundle_downloads_total")

    return FileResponse(
        path=str(bundle_path),
        media_type="application/gzip",
        filename=bundle_path.name,
        headers={
            "Content-Length": str(size),
            "X-Bundle-Version": manager.get_info().version,
        },
    )


# ---------------------------------------------------------------------------
# Voice + Vision (Phase 27)
# ---------------------------------------------------------------------------


@app.post("/v1/voice/vision/chat", response_model=VoiceVisionChatResponse, tags=["voice"])
@limiter.limit(_RATE_LIMIT)
async def voice_vision_chat(
    request: Request,
    model: ChatModel = Depends(get_model),
    speech: SpeechModel = Depends(get_speech_model),
    ctx: AuthContext = Depends(optional_user),
) -> VoiceVisionChatResponse:
    """Compound voice + vision chat: audio + image -> ASR -> OCR -> LLM -> TTS.

    Accepts multipart/form-data with:
    - ``audio``: Raw PCM16 audio bytes
    - ``image``: JPEG/PNG image bytes (optional)
    - ``language``, ``voice``, ``top_k``, ``conversation_id``

    Feature-flagged behind ``FLAG_VOICE_VISION``.
    """
    from .flags import flags

    if not flags.is_enabled("voice_vision"):
        raise HTTPException(
            status_code=404,
            detail="Voice vision mode is disabled (FLAG_VOICE_VISION=false)",
        )
    _require_voice_processing_consent(request, ctx)

    import asyncio

    t0 = time.perf_counter()
    body = await request.form()

    language = str(body.get("language", "en"))
    voice = body.get("voice")
    top_k = int(body.get("top_k", "4"))
    conversation_id = body.get("conversation_id")
    tts_enabled = str(body.get("tts_enabled", "true")).lower() == "true"
    ocr_enabled = str(body.get("ocr_enabled", "true")).lower() == "true"

    # Extract audio (hard cap: 16 MiB — same as /v1/asr)
    audio_file = body.get("audio")
    audio_bytes = await audio_file.read() if audio_file else b""
    if len(audio_bytes) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio exceeds 16 MiB limit")

    # Extract image (hard cap: 10 MiB)
    image_file = body.get("image")
    image_bytes = await image_file.read() if image_file else b""
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MiB limit")

    transcript = ""
    ocr_text = ""
    asr_latency = 0.0
    ocr_latency = 0.0

    # Step 1: ASR (parallel with OCR)
    async def do_asr():
        nonlocal transcript, asr_latency
        if audio_bytes:
            t_asr = time.perf_counter()
            result = await asyncio.to_thread(
                speech.transcribe, audio_bytes, language=language,
            )
            asr_latency = time.perf_counter() - t_asr
            transcript = result.text if result else ""

    async def do_ocr():
        nonlocal ocr_text, ocr_latency
        if image_bytes and ocr_enabled:
            t_ocr = time.perf_counter()

            def _run_ocr() -> str:
                try:
                    import io as _io

                    from PIL import Image

                    img = Image.open(_io.BytesIO(image_bytes))
                    import pytesseract

                    return pytesseract.image_to_string(img, lang="eng")
                except ImportError:
                    return "[OCR unavailable — install pytesseract]"
                except Exception as e:
                    return f"[OCR error: {e}]"

            ocr_text = await asyncio.to_thread(_run_ocr)
            ocr_latency = time.perf_counter() - t_ocr

    await asyncio.gather(do_asr(), do_ocr())

    # Step 2: Combine transcript + OCR text for LLM
    combined_query = transcript
    if ocr_text:
        combined_query += f"\n\n[Document text]: {ocr_text[:2000]}"

    if not combined_query.strip():
        return VoiceVisionChatResponse(
            error="No audio transcript or document text detected",
            total_latency_s=round(time.perf_counter() - t0, 3),
        )

    # Step 3: LLM generation
    t_llm = time.perf_counter()
    result = await asyncio.to_thread(
        model.generate,
        message=combined_query,
        conversation_id=conversation_id or None,
        top_k=top_k,
        locale=language,
        user_id=ctx.user_id or None,
        tenant_id=ctx.tenant_id,
    )
    llm_latency = time.perf_counter() - t_llm

    reply = result.get("reply", "")

    # Step 4: TTS
    tts_latency = 0.0
    reply_audio = ""
    sample_rate = 0
    duration_s = 0.0

    if tts_enabled and reply:
        t_tts = time.perf_counter()
        tts_result = await asyncio.to_thread(
            speech.synthesize, reply, language=language, voice=str(voice) if voice else None,
        )
        tts_latency = time.perf_counter() - t_tts
        if tts_result and tts_result.audio and not tts_result.error:
            import base64 as _b64

            reply_audio = _b64.b64encode(tts_result.audio).decode("ascii")
            sample_rate = tts_result.sample_rate
            duration_s = tts_result.duration_s

    total_latency = time.perf_counter() - t0

    return VoiceVisionChatResponse(
        transcript=transcript,
        ocr_text=ocr_text[:500],
        reply=reply,
        reply_audio_base64=reply_audio,
        sample_rate=sample_rate,
        duration_s=duration_s,
        sources=result.get("sources", []),
        citations=[
            Citation(**c) if isinstance(c, dict) else c
            for c in result.get("citations", [])
        ],
        faithfulness_score=result.get("faithfulness_score"),
        retrieval_mode=result.get("retrieval_mode", "keyword"),
        conversation_id=result.get("conversation_id"),
        asr_latency_s=round(asr_latency, 3),
        ocr_latency_s=round(ocr_latency, 3),
        llm_latency_s=round(llm_latency, 3),
        tts_latency_s=round(tts_latency, 3),
        total_latency_s=round(total_latency, 3),
    )


# ---------------------------------------------------------------------------
# Admin: Offline Statistics (Phase 25)
# ---------------------------------------------------------------------------


@app.get("/v1/admin/offline_stats", response_model=OfflineAdminStats, tags=["admin"])
def admin_offline_stats(
    request: Request,
    _ctx: AuthContext = Depends(require_admin_access),
) -> OfflineAdminStats:
    """Aggregate offline usage statistics for the admin dashboard.

    Combines bundle manager stats + sync engine stats.
    """
    from .offline_bundle import BundleManager
    from .offline_sync import OfflineSyncEngine

    bundle_mgr = BundleManager()
    bundle_stats = bundle_mgr.get_stats()

    sync_engine = OfflineSyncEngine()
    sync_engine.initialize()
    sync_stats = sync_engine.get_stats()

    return OfflineAdminStats(
        total_bundles_served=bundle_stats.get("total_bundles_served", 0),
        total_syncs_completed=sync_stats.get("total_syncs", 0),
        total_sync_bytes=sync_stats.get("total_bytes_sent", 0),
        active_offline_devices=sync_stats.get("unique_devices", 0),
        avg_sync_duration_s=sync_stats.get("avg_duration_s", 0.0),
        bundle_version=bundle_stats.get("bundle_version", ""),
        bundle_size_mb=bundle_stats.get("bundle_size_mb", 0.0),
        passage_count=bundle_stats.get("passage_count", 0),
        last_bundle_built_at=bundle_stats.get("last_built_at", ""),
    )
