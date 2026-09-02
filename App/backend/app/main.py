"""URA Chatbot API – FastAPI application.

Hardened for production per OWASP LLM Top 10 (2025), NIST SSDF, and
ISO/IEC 42001:2023 security controls.  Includes analytics, feedback,
and Prometheus-compatible metrics (2026 observability standards).
"""

import asyncio
import contextlib
import datetime
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse
from starlette.websockets import WebSocket

# Proxy header validation — prevents IP rate-limit bypass via forged
# X-Forwarded-For headers (CVE-mitigation: rate-limit header spoofing)
try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    _HAS_PROXY_MIDDLEWARE = True
except ImportError:  # pragma: no cover – uvicorn always present at runtime
    _HAS_PROXY_MIDDLEWARE = False

from . import database as db
from . import documents
from .analytics import AnalyticsMiddleware, metrics
from .auth import AuthContext, current_user, optional_user, require_role, require_user
from .auth.models import ConsentGrantRequest, ConsentWithdrawRequest, ProfileUpdateRequest
from .authority import authority_required, get_authority_status
from .escalation_notify import known_teams
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
    EscalationRequest,
    EscalationResponse,
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
from .query import gate_locale
from .seed_prototype import seed as _seed_prototype
from .seed_prototype import should_seed as _should_seed
from .service import ChatModel, localize_reply
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
_INSECURE_DEV_SECRET = "dev-insecure-change-me"  # noqa: S105  # pragma: allowlist secret

# Wall-clock budget for the batch /v1/voice/chat pipeline. Once spent, the
# reply-TTS leg is skipped (tts_skipped=True) so the text reply still beats
# the deployment's gateway timeout; the client narrates via /v1/tts instead.
VOICE_CHAT_BUDGET_S = float(os.getenv("VOICE_CHAT_BUDGET_S", "50"))
_RETENTION_CLEANUP_INTERVAL_SECONDS = max(
    60, int(os.getenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "3600"))
)


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

    for flag_name in ("auth_required", "multi_tenant", "audit_ledger", "ticket_queue"):
        if not _production_flag_enabled(flag_name):
            errors.append(f"FLAG_{flag_name.upper()} must not be disabled in production.")

    from .production_readiness import gap_gate_errors

    errors.extend(gap_gate_errors())

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
    # An in-container Qdrant sidecar is a legitimate production topology for the
    # single-container deployments (Crane Cloud, HF Space): they cannot reach a
    # managed Qdrant at all, and the collection is baked into the image at build
    # time, so localhost is the intended target rather than a misconfiguration.
    # It stays opt-in so an accidental localhost URL is still caught, and the
    # durability caveat the original check was guarding still applies — the
    # sidecar's storage is read-mostly and rebuilt with the image, never written
    # to by the app.
    qdrant_sidecar = os.getenv("QDRANT_SIDECAR", "false").lower() in ("1", "true", "yes", "on")
    qdrant_url = os.getenv("QDRANT_URL", "")
    qdrant_is_local = any(h in qdrant_url for h in ("localhost", "127.0.0.1", "[::1]"))
    if not qdrant_url:
        errors.append(
            "QDRANT_URL must point at an external/managed Qdrant in production "
            "(the in-container default is not durable)."
        )
    elif qdrant_is_local and not qdrant_sidecar:
        errors.append(
            "QDRANT_URL must not be localhost in production — use an external/managed "
            "Qdrant, or set QDRANT_SIDECAR=true if this deployment runs the in-image "
            "Qdrant sidecar."
        )
    # A sidecar on loopback needs no API key: it is not reachable from outside the
    # container, and requiring one would only add a secret with nothing to protect.
    if qdrant_url and not os.getenv("QDRANT_API_KEY") and not (qdrant_sidecar and qdrant_is_local):
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


def _apply_persisted_flag_overrides(overrides: dict[str, bool]) -> None:
    """Replay durable flag overrides without weakening production controls.

    Startup validates environment flags before the analytics database is
    available. The durable overrides are read immediately afterwards, and an
    in-memory override wins over the production-on default. A stale ``false``
    value for a protected control would therefore undo that validation unless
    it is rejected here.
    """
    from .flags import flags as flag_reg
    from .flags import is_protected

    if (os.getenv("APP_ENV") or "development").lower() == "production":
        disabled = sorted(
            name for name, enabled in overrides.items() if is_protected(name) and not enabled
        )
        if disabled:
            message = (
                "PRODUCTION SAFETY CHECK FAILED — refusing to start. "
                "Persisted flag override(s) disable protected control(s): "
                + ", ".join(disabled)
                + ". Remove the override(s) or set them true before starting."
            )
            logger.critical(message)
            raise SystemExit(message)

    for name, enabled in overrides.items():
        try:
            flag_reg.set(name, enabled)
        except KeyError:
            # A row for a removed flag has no effect and must not block an
            # otherwise safe upgrade.
            continue


def _initialize_analytics_database() -> None:
    """Initialize persistence, failing closed when production storage is unavailable.

    Conversations, tickets, consent receipts, and the audit ledger are all
    stored through this database. Continuing after a production connection or
    schema failure would serve requests without the controls production mode
    claims to enforce.
    """
    try:
        db.init_db()
        logger.info("Analytics database ready")

        if _should_seed():
            try:
                logger.info("prototype seed: %s", _seed_prototype())
            except Exception:
                logger.exception("prototype seed skipped")
        overrides = db.load_flag_overrides()
    except Exception as exc:
        logger.exception("Analytics database initialisation failed")
        if (os.getenv("APP_ENV") or "development").lower() == "production":
            message = (
                "PRODUCTION SAFETY CHECK FAILED — refusing to start because the analytics "
                "database is unavailable. Audit, tenancy, consent, and ticket controls "
                "cannot run without it."
            )
            logger.critical(message)
            raise SystemExit(message) from exc
        return

    _apply_persisted_flag_overrides(overrides)


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


def _log_egress_reachability() -> None:
    """Record, once at startup, which external hosts this pod can resolve.

    Diagnosing this from outside was guesswork. A single log line —
    "Gemini: gateway.ai.cloudflare.com unreachable (ConnectError)" — was the
    only evidence that the Cloudflare AI Gateway was unavailable on the Space,
    and it appeared only when a request happened to take that path. Crane Cloud
    exposes no log API at all, so there the same question could not be answered
    even in principle.

    DNS is the thing worth reporting, because that is the documented failure
    mode this deployment already carries a workaround for: doh_resolver's own
    docstring describes a pod with outbound TCP/443 open and no working upstream
    resolver, where every hostname fails before TCP connect. Resolution succeeds
    or fails in milliseconds and needs no credentials, so it is cheap enough to
    run unconditionally and says which side of that line this pod is on.

    Never fatal: a diagnostic that can stop startup is worse than no diagnostic.
    """
    import socket  # noqa: PLC0415 — startup-only

    hosts = [
        "gateway.ai.cloudflare.com",   # CF AI Gateway (Gemini, Workers AI)
        "api.cloudflare.com",          # Vectorize, Workers AI direct
        "generativelanguage.googleapis.com",  # Gemini direct — the working path today
    ]
    vllm = os.getenv("VLLM_BASE_URL", "")
    if vllm:
        with contextlib.suppress(Exception):
            host = urlparse(vllm).hostname
            if host:
                hosts.append(host)

    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(3.0)
    try:
        for host in hosts:
            t0 = time.perf_counter()
            try:
                socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
                logger.info("egress: %s resolves (%.0fms)", host, (time.perf_counter() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001 — the failure IS the finding
                logger.warning(
                    "egress: %s does NOT resolve (%s) — anything routed through it will fail",
                    host, type(exc).__name__,
                )
    finally:
        socket.setdefaulttimeout(previous)


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
            logger.info("DoH: enabled (USE_DOH=true) — external names resolve via 1.1.1.1")
        else:
            logger.info("DoH: disabled (USE_DOH is not true) — using the pod resolver")
    except Exception:
        logger.warning("DoH resolver activation skipped", exc_info=True)

    _log_egress_reachability()

    # Production safety gate — blocks startup on insecure config
    _validate_production_env()

    # OpenTelemetry GenAI tracing (opt-in via OTEL_ENABLED=true)
    try:
        from .tracing import init_tracing

        init_tracing()
    except Exception:
        logger.warning("OpenTelemetry tracing init skipped", exc_info=True)

    # Initialise analytics database. Development can still offer degraded text
    # chat, while production must not run without its persistence controls.
    _initialize_analytics_database()

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
            # Warm the synthesis chain off the request path. The model objects
            # are built above, but the tiers behind them are lazy — the
            # Spark-TTS codec, the edge-tts session, the Sunbird client — and
            # without this the first taxpayer to press Listen waits for all of
            # it. On a background thread, so a slow or unreachable tier delays
            # nobody's startup; failures are logged and change nothing.
            from .speech_service import SPEECH_WARMUP

            if SPEECH_WARMUP:
                def _warm_speech(speech: SpeechModel = app.state.speech) -> None:
                    outcomes = speech.warmup()
                    logger.info("SpeechModel warm-up: %s", outcomes or "skipped")

                threading.Thread(
                    target=_warm_speech, name="speech-warmup", daemon=True
                ).start()
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

    # Startup alone is insufficient: an otherwise idle pod would retain
    # expired documents and in-memory data indefinitely. The job itself is
    # idempotent, including when several replicas run it at once.
    from .retention import run_retention_cleanup

    def _run_retention_cleanup_guarded() -> None:
        """Housekeeping must never decide whether the service starts or stays up.

        Retention cleanup reaches the analytics database, the memory store and
        the document registry. Any of those can be unavailable — a read-only
        volume, a misresolved path, a locked SQLite file — and an unguarded call
        here took the whole pod down: the exception propagated out of lifespan,
        uvicorn reported "Application startup failed", and the container
        crash-looped without ever serving. That is strictly worse than retaining
        expired rows for one interval, and it is inconsistent with how every
        other optional subsystem in this lifespan degrades.

        The failure is logged at error level with a traceback, so a store that
        cannot be cleaned is loud in the logs rather than silent.
        """
        try:
            run_retention_cleanup()
        except Exception:
            logger.error("Retention cleanup failed; continuing without it", exc_info=True)

    _run_retention_cleanup_guarded()
    retention_stop = asyncio.Event()

    async def _retention_loop() -> None:
        while True:
            try:
                await asyncio.wait_for(
                    retention_stop.wait(), timeout=_RETENTION_CLEANUP_INTERVAL_SECONDS
                )
                return
            except TimeoutError:
                _run_retention_cleanup_guarded()
            except asyncio.CancelledError:
                return

    retention_task = asyncio.create_task(_retention_loop(), name="retention-cleanup")
    try:
        yield
    finally:
        retention_stop.set()
        retention_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retention_task
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
# What a taxpayer is told after asking for a person. English source text —
# localize_reply renders it into their language on the way out, the same path
# every chat answer takes, so these get the figure and collapse guards too.
#
# Deliberately concrete about what happens next and where the answer will
# appear. "An officer will be in touch" is the sentence that makes people
# phone the contact centre and start over, which is the outcome escalation
# exists to avoid.
_ESCALATION_CREATED_MESSAGE = (
    "A URA officer has been asked to look at this. Their reply will appear "
    "here in this conversation, so you do not need to start again. You can "
    "also call URA toll-free on 0800 117 000 or 0800 217 000."
)
_ESCALATION_REUSED_MESSAGE = (
    "A URA officer is already looking at this conversation. Their reply will "
    "appear here, so there is no need to ask again. You can also call URA "
    "toll-free on 0800 117 000 or 0800 217 000."
)
_ESCALATION_QUEUE_OFF_MESSAGE = (
    "This assistant cannot pass your question to an officer right now. "
    "Please call URA toll-free on 0800 117 000 or 0800 217 000, or visit "
    "ura.go.ug."
)

_RATE_LIMIT = os.getenv("RATE_LIMIT", "30/minute")
_EXPORT_RATE_LIMIT = os.getenv("EXPORT_RATE_LIMIT", "10/minute")
_DOCUMENT_RATE_LIMIT = os.getenv("DOCUMENT_RATE_LIMIT", "10/minute")
_SLOWAPI_STORAGE_URI = os.getenv("SLOWAPI_STORAGE_URI", "")
_DOCUMENT_MULTIPART_OVERHEAD_BYTES = int(
    os.getenv("DOCUMENT_MULTIPART_OVERHEAD_BYTES", str(512 * 1024))
)
_DOCUMENT_UPLOAD_CHUNK_BYTES = 64 * 1024

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


def _reject_oversized_document_request(request: Request) -> None:
    """Fail before multipart parsing when a client provides an oversized body."""
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        content_length = int(raw_length)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None
    if content_length < 0:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header")
    max_request_bytes = documents.MAX_FILE_BYTES + _DOCUMENT_MULTIPART_OVERHEAD_BYTES
    if content_length > max_request_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Request exceeds the {max_request_bytes // (1024 * 1024)} MiB document upload limit",
        )


async def _read_document_upload_bounded(upload: Any) -> bytes:
    """Read an upload in chunks, enforcing the byte limit before a full copy exists."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_DOCUMENT_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > documents.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {documents.MAX_FILE_BYTES // (1024 * 1024)} MiB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
# Proxy header validation — rate-limit IP spoofing mitigation
# ---------------------------------------------------------------------------
# When behind a trusted reverse proxy (nginx, Cloudflare, K8s ingress),
# only accept X-Forwarded-For from known gateway IPs. Without this,
# attackers can forge headers to rotate their apparent IP and bypass
# per-IP rate limits. TRUSTED_PROXY_HOSTS env is a comma-separated list
# of IPs or CIDR ranges.  Defaults to loopback only (single-container).
if _HAS_PROXY_MIDDLEWARE:
    _trusted_hosts: list[str] = [
        h.strip()
        for h in os.getenv("TRUSTED_PROXY_HOSTS", "127.0.0.1,::1").split(",")
        if h.strip()
    ]
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_hosts)
    logger.info("ProxyHeadersMiddleware enabled")


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
    # "hybrid" only when dense vectors are actually in play. A retriever that
    # fell back to SPARSE-ONLY — no embedder stamp on the collection, or no
    # sentence-transformers in the image — still had _retriever_ready True, so
    # this reported "hybrid" while BM25 did all the work. That is the field
    # operators read to decide whether retrieval is healthy, and on the
    # deployed Space it was saying yes while dense was off.
    #
    # "vector" is its own label, checked BEFORE _sparse_only: the CPU-only
    # deployments serve a sparse-only Qdrant collection whenever Vectorize is
    # unavailable, so _sparse_only stays True even while Vectorize is actively
    # serving dense queries (initialize() keeps that Qdrant state as a fallback
    # rather than discarding it). Reporting "sparse" there would be the same
    # false-negative this field was fixed to stop making. "vector" is also
    # deliberately not folded into "hybrid": Vectorize is dense-only with a
    # client-side lexical re-score, not Qdrant's reranked dense+BM25 fusion,
    # and operators reading this field should be able to tell them apart.
    if not model._retriever_ready:
        retrieval_mode = "keyword"
    elif getattr(model._retriever, "_vectorize_mode", False):
        retrieval_mode = "vector"
    elif getattr(model._retriever, "_sparse_only", False):
        retrieval_mode = "sparse"
    else:
        retrieval_mode = "hybrid"
    qdrant_healthy = model._retriever.is_ready if model._retriever_ready else False
    # Optional capabilities report the backend they actually resolved to. Both
    # of these degrade silently by design — detection drops to a character
    # heuristic, translation drops to serving English — and both degradations
    # shipped unnoticed. An operator should not have to ask the assistant a
    # Luganda question to find out.
    from . import sunbird
    from .query import language_detection_backend

    capabilities = {
        "language_detection": language_detection_backend(),
        "translation": "sunbird" if sunbird.is_available() else "unavailable",
        # Not the same question as "translation": Sunbird's per-account retry
        # falls over to a second account, and with only one configured there
        # is nowhere to fall over to. "fallback" alone means the primary token
        # is missing, not that the fallback is doing its job.
        "sunbird_accounts": sunbird.account_summary(),
        "retrieval": retrieval_mode,
    }
    return HealthResponse(
        status="ready" if qdrant_healthy else "degraded",
        version=app.version,
        model_loaded=True,
        tags_loaded=len(model._faq_index),
        retrieval_mode=retrieval_mode,
        capabilities=capabilities,
    )


@app.get("/v1/authority/status", tags=["admin"])
def authority_status(
    _: AuthContext = Depends(require_role("ura_staff", "ura_admin", "ura_auditor")),
) -> dict[str, Any]:
    """Return authority-manifest validation status for release/ops checks."""
    return get_authority_status()


@app.get("/v1/index/freshness", tags=["system"])
def index_freshness() -> dict[str, Any]:
    """Last corpus-hash check (G27). Does not re-hash on this request.

    Cron writes the status file: ``python -m app.freshness --check --write-status``.
    Missing file means the check has not run yet — not that the index is fresh.
    """
    from .freshness import load_status

    status = load_status()
    if status is None:
        return {"ok": None, "snapshot_missing": True, "checked_at": None}
    return status


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

    attachments = documents.resolve_attachments(
        body.attachment_ids, session_id=session_id, user_id=ctx.user_id
    )
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
            **_experiment_log_fields(ctx.user_id or "", result.get("locale") or body.locale or ""),
        )
    except Exception:
        logger.warning("Conversation logging failed", exc_info=True)

    # Track escalation events
    if result.get("escalation_required"):
        metrics.inc("escalation_total")
        if ctx.authenticated and db.has_active_consent(ctx.user_id, "analytics"):
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
                    user_id=ctx.user_id,
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
    attachments = documents.resolve_attachments(
        body.attachment_ids, session_id=session_id, user_id=ctx.user_id
    )

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
            if event_type.startswith("translation."):
                # Reply localization is the slow leg of a non-English turn and
                # the client shows it as its own phase. Not buffered into
                # agent_trace: it is a presentation cue, not a retrieval step.
                yield {"event": "phase", "data": event_type}
                continue
            if event_type.startswith(("retrieval.", "iteration.", "tool_call.")):
                # Retrieval boundaries go out live, as a name and nothing else.
                # The client needs them to say what it is doing right now, and
                # the buffered agent_trace below cannot serve that: it is held
                # back until just before `grounding`, which is after generation
                # has already streamed. The payloads still go into that summary
                # unchanged — this only adds a frame, it does not divert one.
                if event_type in ("retrieval.started", "retrieval.completed"):
                    yield {"event": "phase", "data": event_type}
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


def _experiment_log_fields(user_id: str = "", locale: str = "") -> dict[str, str]:
    """Flag variants + locale persisted on each turn (G26)."""
    from .flags import flags

    return flags.experiment_log_fields(subject=user_id or None, locale=locale)


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
            **_experiment_log_fields(user_id, result.get("locale") or body.locale or ""),
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
    # 639-1 OR 639-3: two of the five locales the picker offers (nyn, ach) have
    # no two-letter code, so a {2}-only rule rejected Runyankole and Acholi
    # speech outright. Every request model in models.py already allows {2,3}.
    if language is not None and not re.match(r"^[a-z]{2,3}$", language):
        raise HTTPException(status_code=400, detail="language must be an ISO 639-1/639-3 code")

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
@limiter.limit(_EXPORT_RATE_LIMIT)
def export_conversation(
    request: Request,
    body: ExportConversationRequest,
    _ctx: AuthContext = Depends(optional_user),
) -> Response:
    """Export a conversation as a branded PDF."""
    from .pdf_export import generate_conversation_pdf

    started = time.perf_counter()
    pdf_bytes = generate_conversation_pdf(
        [message.model_dump() for message in body.messages],
        title=body.title,
        session_id=body.session_id,
    )
    metrics.inc("pdf_exports_total", labels={"kind": "conversation"})
    metrics.observe("pdf_export_bytes", len(pdf_bytes), labels={"kind": "conversation"})
    metrics.observe(
        "pdf_export_duration_ms", (time.perf_counter() - started) * 1000, labels={"kind": "conversation"}
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ura_conversation_{int(time.time())}.pdf"',
        },
    )


@app.post("/v1/export/tax-summary", tags=["export"])
@limiter.limit(_EXPORT_RATE_LIMIT)
def export_tax_summary(
    request: Request,
    body: ExportTaxSummaryRequest,
    _ctx: AuthContext = Depends(current_user),
) -> Response:
    """Export a tax calculation summary as a branded PDF."""
    from .pdf_export import generate_tax_summary_pdf

    started = time.perf_counter()
    pdf_bytes = generate_tax_summary_pdf(
        body.calculation.model_dump(),
        taxpayer_ref=body.taxpayer_ref,
    )
    metrics.inc("pdf_exports_total", labels={"kind": "tax_summary"})
    metrics.observe("pdf_export_bytes", len(pdf_bytes), labels={"kind": "tax_summary"})
    metrics.observe(
        "pdf_export_duration_ms", (time.perf_counter() - started) * 1000, labels={"kind": "tax_summary"}
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
@limiter.limit(_DOCUMENT_RATE_LIMIT)
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

    _reject_oversized_document_request(request)
    session_id = request.headers.get("X-Session-ID", "")
    if not session_id and not ctx.user_id:
        raise HTTPException(
            status_code=422,
            detail="X-Session-ID or an authenticated identity is required for document uploads",
        )
    try:
        form = await request.form(
            max_files=1,
            max_fields=8,
            max_part_size=documents.MAX_FILE_BYTES,
        )
    except Exception as err:
        logger.info("Document multipart parsing rejected: %s", err)
        raise HTTPException(status_code=422, detail="Invalid or oversized multipart document upload") from err
    uploads = form.getlist("file")
    upload = uploads[0] if len(uploads) == 1 else None
    if upload is None or isinstance(upload, str):
        raise HTTPException(status_code=422, detail="Missing 'file' part in form data")

    data = await _read_document_upload_bounded(upload)
    if not data:
        raise HTTPException(status_code=422, detail="Empty file")
    if len(data) > documents.MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {documents.MAX_FILE_BYTES // (1024 * 1024)} MiB limit",
        )

    started = time.perf_counter()
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
        detail = str(err)
        code = 422
        if "malware" in detail.lower():
            code = 422
        raise HTTPException(status_code=code, detail=detail) from err
    finally:
        metrics.observe("document_analysis_duration_ms", (time.perf_counter() - started) * 1000)

    metrics.inc("documents_analyzed_total")
    ocr_status = str(record.meta.get("ocr_status", "not_used"))
    ocr_backend = str(record.meta.get("ocr_backend", "not_used"))
    metrics.inc("document_ocr_total", labels={"backend": ocr_backend, "status": ocr_status})
    if record.meta.get("ocr_regions"):
        metrics.observe("document_ocr_regions", float(record.meta["ocr_regions"]))
    return DocumentAnalysisResponse(**record.to_response_payload())


@app.get("/v1/documents/{document_id}/report", tags=["documents"])
@limiter.limit(_DOCUMENT_RATE_LIMIT)
def document_report(
    request: Request,
    document_id: str = Path(..., pattern=r"^[a-f0-9]{32}$"),
    _ctx: AuthContext = Depends(optional_user),
) -> Response:
    """Download the branded PDF analysis report for an analysed document."""
    record = documents.get_document(
        document_id,
        session_id=request.headers.get("X-Session-ID", ""),
        user_id=_ctx.user_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found or expired")

    # Lazy import AFTER the 404 check so unknown/expired ids stay 404 even
    # on a runtime without fpdf2.
    from .pdf_export import generate_document_report_pdf

    started = time.perf_counter()
    pdf_bytes = generate_document_report_pdf(record.to_report_payload())
    metrics.inc("pdf_exports_total", labels={"kind": "document_report"})
    metrics.observe("pdf_export_bytes", len(pdf_bytes), labels={"kind": "document_report"})
    metrics.observe(
        "pdf_export_duration_ms", (time.perf_counter() - started) * 1000, labels={"kind": "document_report"}
    )
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


@app.get("/v1/speech/voices", tags=["speech"])
def speech_voices(_ctx: AuthContext = Depends(optional_user)) -> dict:
    """The narration voices a caller may choose, per locale.

    Served rather than hardcoded in the client for one reason: the client cannot
    know which speakers this deployment can actually reach. The Ugandan voices
    come from Sunbird's catalog and only work when Sunbird is configured; the
    English ones are edge-tts neural voices that need no key. A picker built
    from a hardcoded list would keep offering voices after the backend lost the
    ability to serve them, and the person choosing one would get an English
    fallback reading Luganda with nothing to say why.

    `default: true` marks the speaker used when a caller names none.
    """
    from . import sunbird
    from .speech_service import en_edge_voice_choices

    sunbird_ready = sunbird.is_available()
    voices: dict[str, list[dict]] = {}

    for locale, tags in sunbird.TTS_VOICE_CATALOG.items():
        # Sunbird's speakers are native for the Ugandan languages it is trained
        # on — not for English, where `salt_eng_0001` is the last-resort voice
        # edge-tts exists to avoid (see speech_service). Advertising it as a
        # native English speaker would recommend the worst option on offer.
        native = locale != "en"
        entries = [
            {
                "id": tag,
                "provider": "sunbird",
                "native": native,
                "default": False,  # decided once per locale, below
                "available": sunbird_ready,
            }
            for tag in tags
        ]
        if entries:
            voices.setdefault(locale, []).extend(entries)

    # English also has the edge-tts neural voices, which are what actually
    # serves English (Sunbird's English voice is a last resort) — so they are
    # listed first and one of them is the English default.
    # The deployment's configured voice leads, then the rest of the choices.
    # This is the same list resolve_edge_voice() accepts, so every English voice
    # offered here is one synthesis will actually use.
    edge_entries = [
        {
            "id": name,
            "provider": "edge_tts",
            "native": False,
            "default": False,
            "available": True,
        }
        for name in en_edge_voice_choices()
    ]
    voices["en"] = edge_entries + voices.get("en", [])

    # Exactly one default per locale, decided here rather than per-provider.
    # Marking each provider's own default gave English two (edge's Aria and
    # Sunbird's salt_eng_0001), and a picker showing two "default" chips has to
    # pick one arbitrarily. The head of the list is the speaker the synthesis
    # chain actually reaches first, which is what "default" has to mean.
    for entries in voices.values():
        for i, entry in enumerate(entries):
            entry["default"] = i == 0

    return {"voices": voices, "sunbird_configured": sunbird_ready}


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
    if not re.match(r"^[a-z]{2,3}$", language):
        raise HTTPException(
            status_code=400,
            detail="language must be an ISO 639-1/639-3 code (e.g. en, lg, nyn, ach)",
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
            **_experiment_log_fields(ctx.user_id or "", chat_result.get("locale") or ""),
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


@app.websocket("/v1/admin/tickets/stream")
async def admin_ticket_stream_ws(websocket: WebSocket) -> None:
    """Live escalation events for staff.

    Read-only and staff-only; carries triage metadata, never a
    transcript. ``?team=customs`` narrows it to one queue. See
    ``ticket_ws.py``.
    """
    from .ticket_ws import ticket_stream_ws

    await ticket_stream_ws(websocket)


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

    from .faq_corpus import CorpusValidationError
    from .indexer import (
        DATA_DIR,
        FAQ_JSONL_DIR,
        TEACHER_QA_DIR,
        build_index,
        ingest_faq_jsonls,
        ingest_teacher_qa_jsonls,
    )

    try:
        documents: list[dict] = []
        documents.extend(ingest_faq_jsonls(DATA_DIR, FAQ_JSONL_DIR))
        documents.extend(ingest_teacher_qa_jsonls(TEACHER_QA_DIR))
    except CorpusValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Vector corpus validation failed: {exc}") from exc

    if not documents:
        raise HTTPException(status_code=404, detail="No FAQ or teacher-QA JSONL documents found to index")

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
def _relay_upstream_call(op: str, fn: Callable[[], dict]) -> dict:
    """Run a relay op, turning an upstream Cloudflare failure into a clean 502.

    Without this, an ordinary and already-anticipated failure — Workers AI
    intermittently rejecting a call, the same failure mode every direct
    caller already retries/falls back around — propagated as an unhandled
    500. That is indistinguishable from "the relay endpoint itself is
    broken" to the caller (``relay_client.py``), and pollutes error tracking
    with tracebacks for a condition the system already has a designed
    fallback for (the caller's own circuit breaker + keyword-search
    fallback). 502 (Bad Gateway) is the correct status for "the upstream this
    endpoint relays to failed" — the relay itself worked.
    """
    try:
        return fn()
    except Exception as exc:
        logger.warning("cf-relay %s: upstream call failed: %s", op, exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Upstream Cloudflare call failed: {exc}") from exc


@app.post("/internal/cf-relay/workers-ai-embed", include_in_schema=False)
def cf_relay_workers_ai_embed(request: Request, body: CFRelayEmbedRequest) -> dict:
    _require_relay_key(request)
    from .providers import gateway as _gw

    # No caller-supplied model — always the retrieval embedding model
    # (gateway.workers_ai_embed's own default); see CFRelayEmbedRequest.
    vectors = _relay_upstream_call("workers-ai-embed", lambda: {"vectors": _gw.workers_ai_embed(body.texts)})
    return vectors


@app.post("/internal/cf-relay/vectorize-query", include_in_schema=False)
def cf_relay_vectorize_query(request: Request, body: CFRelayVectorizeQueryRequest) -> dict:
    _require_relay_key(request)
    from .providers import vectorize as _vz

    return _relay_upstream_call(
        "vectorize-query",
        lambda: {
            "hits": _vz.vectorize_query(body.vector, top_k=body.top_k, vector_filter=body.vector_filter)
        },
    )


@app.post("/internal/cf-relay/workers-ai-chat", include_in_schema=False)
def cf_relay_workers_ai_chat(request: Request, body: CFRelayChatRequest) -> dict:
    _require_relay_key(request)
    from .providers import gateway as _gw
    from .providers import routing as _routing

    # Resolve the slot to an actual model id via a fixed dict lookup — the
    # string that reaches gateway.workers_ai_chat (and the Cloudflare URL it
    # builds) always originates in routing.py, never in the request body.
    # See CFRelayChatRequest for why this indirection exists.
    model = _routing.CHAT_MODEL_SLOTS[body.model_slot]
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    return _relay_upstream_call(
        "workers-ai-chat",
        lambda: {
            "text": _gw.workers_ai_chat(
                messages, model, max_tokens=body.max_tokens, temperature=body.temperature
            )
        },
    )


# ---------------------------------------------------------------------------
# Feedback endpoints
# ---------------------------------------------------------------------------
@app.post("/v1/feedback", response_model=FeedbackResponse, tags=["feedback"])
def submit_feedback(
    body: FeedbackRequest,
    ctx: AuthContext = Depends(current_user),
) -> FeedbackResponse:
    """Submit thumbs-up/down feedback on a chatbot response."""
    from .service import ChatModel as _CM

    if not ctx.authenticated or not db.has_active_consent(ctx.user_id, "analytics"):
        raise HTTPException(status_code=403, detail="analytics consent is required for feedback")
    metrics.inc("feedback_total", labels={"rating": body.rating})
    result = db.save_feedback(
        message_id=body.message_id,
        rating=body.rating,
        comment=body.comment,
        session_id=body.session_id,
        user_id=ctx.user_id,
        user_query=_CM.redact_for_storage(body.user_query),
        bot_reply=_CM.redact_for_storage(body.bot_reply),
    )
    return FeedbackResponse(**result)


@app.post("/v1/escalate", response_model=EscalationResponse, tags=["chat"])
@limiter.limit(_RATE_LIMIT)
def request_human_officer(
    request: Request,
    body: EscalationRequest,
    model: ChatModel = Depends(get_model),
    ctx: AuthContext = Depends(optional_user),
) -> EscalationResponse:
    """Hand this conversation to a human URA officer, because the taxpayer asked.

    Every other route into the ticket queue is a judgement the system makes on
    the taxpayer's behalf: the supervisor's ESCALATE route, the response judge
    escalating a low-confidence answer, the ``escalate_to_human`` tool the
    model may call mid-turn. Someone who has simply decided the assistant
    cannot help them had no way to say so — they were told to phone a number
    that starts the conversation over. This is the missing direction.

    Reuses ``_maybe_create_ticket``, which is what makes the ticket worth
    anything rather than a row in a table: it snapshots the transcript for the
    officer, redacts it, routes it to the owning team, notifies, publishes to
    the live staff stream — and, crucially, reuses an already-open ticket for
    this conversation, so a taxpayer who asks three times gets one officer
    rather than three each starting from the beginning.

    Open to unauthenticated callers on purpose. A taxpayer who cannot get an
    answer is exactly the person least likely to have an account, and asking
    them to make one first is a worse failure than the one they are reporting.
    The rate limit and the redaction path are what bound the abuse surface.

    Honours the ``ticket_queue`` flag (see AGENTS.md): with the queue off this
    answers ``ok: false`` and says how to reach URA instead of claiming a
    handoff that will never arrive.

    Depends on the ChatModel, so it 503s when the model failed to initialise.
    That is a deliberate consequence of reusing ``_maybe_create_ticket`` rather
    than growing a second, thinner path into the ticket table that would drift
    from it — the transcript snapshot, the redaction, the team routing and the
    open-ticket reuse all live there. It is reachable in practice: the control
    is offered under an assistant turn, which means the model answered. The
    client treats any non-`ok` response the same way, by showing the
    contact-centre numbers.
    """
    from .flags import flags

    locale = gate_locale(body.locale)
    if not flags.is_enabled("ticket_queue"):
        metrics.inc("escalation_requested_total", labels={"outcome": "queue_disabled"})
        return EscalationResponse(
            ok=False,
            status="queue_disabled",
            message=localize_reply(_ESCALATION_QUEUE_OFF_MESSAGE, locale),
        )

    reason = (body.reason or "").strip() or "The taxpayer asked to speak to a URA officer."
    conversation_id = body.conversation_id or ""
    handoff: dict[str, Any] = {
        "topic": "general",
        "summary": reason[:500],
        "priority": "normal",
        # Named so an officer opening the queue can tell an answer the system
        # doubted from a person who asked for help — they need different
        # first replies.
        "requested_by": "taxpayer",
    }
    ticket_id = model._maybe_create_ticket(
        reason=reason,
        user_query=reason,
        bot_reply="",
        session_id=body.session_id,
        conversation_id=conversation_id,
        priority="normal",
        handoff=handoff,
        user_id=ctx.user_id or "",
    )
    if not ticket_id:
        # _maybe_create_ticket logs the failure as ESCALATION LOST. The
        # taxpayer must not be told a human is coming when none is.
        metrics.inc("escalation_requested_total", labels={"outcome": "failed"})
        return EscalationResponse(
            ok=False,
            status="failed",
            message=localize_reply(_ESCALATION_QUEUE_OFF_MESSAGE, locale),
        )

    reused = bool(handoff.get("reused_existing_ticket"))
    metrics.inc(
        "escalation_requested_total",
        labels={"outcome": "reused" if reused else "created"},
    )
    return EscalationResponse(
        ok=True,
        ticket_id=ticket_id,
        status="open",
        reused_existing=reused,
        message=localize_reply(
            _ESCALATION_REUSED_MESSAGE if reused else _ESCALATION_CREATED_MESSAGE,
            locale,
        ),
    )


@app.patch("/v1/feedback/{message_id}/comment", tags=["feedback"])
def update_feedback_comment(
    message_id: str,
    body: FeedbackCommentRequest,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    """Add a follow-up comment to existing feedback (avoids duplicate entries)."""
    from .service import ChatModel as _CM

    if not ctx.authenticated or not db.has_active_consent(ctx.user_id, "analytics"):
        raise HTTPException(status_code=403, detail="analytics consent is required for feedback")
    updated = db.update_feedback_comment(
        message_id, _CM.redact_for_storage(body.comment), user_id=ctx.user_id
    )
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
    ctx: AuthContext = Depends(current_user),
) -> dict:
    """Track a client-side analytics event."""
    if not ctx.authenticated or not db.has_active_consent(ctx.user_id, "analytics"):
        return {"status": "ignored", "reason": "analytics_consent_required"}
    from .guardrails import redact_pii_text

    db.track_event(
        event_type=body.event_type,
        event_data=redact_pii_text(json.dumps(body.event_data)),
        session_id=body.session_id,
        user_id=ctx.user_id,
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
    priority: str | None = None,
    team: str | None = None,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """List escalation tickets for URA staff triage.

    Requires an authenticated staff/admin user, or the configured
    operator key as a break-glass fallback. Filter via the query
    string: ``?status=open``, ``?priority=urgent``.

    Ordered urgent-first, then oldest within a priority, so a waiting
    taxpayer moves up the queue rather than being buried by newer
    arrivals. Rows carry a short ``handoff`` preview but **not** the
    transcript — fetch a single ticket for that.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1..500")
    if offset < 0 or offset > 100_000:
        raise HTTPException(status_code=400, detail="offset out of range")
    if status and status not in ("open", "assigned", "resolved", "wontfix"):
        raise HTTPException(status_code=400, detail="invalid status")
    if priority and priority not in ("low", "normal", "high", "urgent"):
        raise HTTPException(status_code=400, detail="invalid priority")

    rows = db.list_tickets(
        status=status, limit=limit, offset=offset, priority=priority, team=team
    )
    return {
        "count": len(rows),
        "status_filter": status or "all",
        "priority_filter": priority or "all",
        "team_filter": team or "all",
        "teams": known_teams(),
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


@app.get("/v1/admin/tickets/sla", tags=["admin"])
def ticket_sla_endpoint(
    request: Request,
    days: int = 30,
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Time-to-first-response and time-to-resolution for the queue.

    Medians, not means: one ticket left over a holiday weekend would
    otherwise make the whole queue look broken.
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be 1..365")
    return db.sla_stats(days=days)


@app.post("/v1/admin/tickets/{ticket_id}/presence", tags=["admin"])
def ticket_presence_endpoint(
    request: Request,
    ticket_id: str = Path(..., pattern=r"^[a-f0-9-]{1,64}$"),
    ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Heartbeat: this officer has the case open (collision lock)."""
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    viewer = ""
    if ctx.user:
        viewer = (ctx.user.email or ctx.user.user_id or "").strip()
    if not viewer:
        viewer = "ops"
    db.heartbeat_ticket_presence(ticket_id, viewer)
    return {"status": "ok", "viewers": db.list_ticket_viewers(ticket_id)}


@app.get("/v1/admin/flags", tags=["admin"])
def list_flags_endpoint(
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Replica flag registry — what is on, without SSH."""
    from .flags import _REGISTRY, flags as flag_reg, is_protected

    items = []
    for name in sorted(_REGISTRY):
        meta = flag_reg.describe(name)
        items.append(
            {
                **meta,
                "enabled": flag_reg.is_enabled(name),
                "protected": is_protected(name),
            }
        )
    return {"flags": items, "overrides_are_ephemeral": False, "scope": "this_replica"}


@app.patch("/v1/admin/flags/{name}", tags=["admin"])
def set_flag_endpoint(
    name: str,
    enabled: bool,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """In-process override. Cluster-wide still needs FLAG_* on every replica."""
    from .flags import flags as flag_reg, is_protected

    if ctx.user and ctx.role != "ura_admin":
        raise HTTPException(status_code=403, detail="only ura_admin may toggle flags")
    if is_protected(name):
        raise HTTPException(status_code=400, detail="this flag cannot be toggled from the UI")
    try:
        flag_reg.set(name, enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown flag") from None
    db.save_flag_override(name, enabled)
    return {
        "name": name,
        "enabled": flag_reg.is_enabled(name),
        "overridden": True,
        "ephemeral": False,
        "scope": "this_replica",
    }


@app.delete("/v1/admin/flags/{name}", tags=["admin"])
def clear_flag_endpoint(
    name: str,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Drop the override so the flag follows ``FLAG_*`` / its default again.

    The other half of the PATCH above. An in-process override beats the
    ``FLAG_*`` env var by design — that is how an operator stops a bad
    release now — and the row is replayed into the registry on every
    boot, so without this a flag touched once from the console shadows
    its environment for good. Setting it back to the default value is
    not the same thing: it stays overridden and keeps winning.
    """
    from .flags import _REGISTRY, flags as flag_reg, is_protected

    if ctx.user and ctx.role != "ura_admin":
        raise HTTPException(status_code=403, detail="only ura_admin may toggle flags")
    if name not in _REGISTRY:
        raise HTTPException(status_code=404, detail="unknown flag")
    # Symmetric with PATCH: a safety flag cannot be set from a browser, so
    # it has no console-set override to drop.
    if is_protected(name):
        raise HTTPException(status_code=400, detail="this flag cannot be toggled from the UI")
    # Durable row first, then this replica. The other order left the replica
    # cleared while the override survived in the database, so the endpoint
    # returned an error and the next restart re-applied the override the
    # operator had just removed. Persisting first fails closed instead: nothing
    # changes anywhere. (The PATCH path above writes in the opposite order, but
    # its failure mode is safe — an override that was never persisted is simply
    # lost on restart rather than resurrected.)
    db.clear_flag_override(name)
    flag_reg.clear(name)
    return {
        "name": name,
        "enabled": flag_reg.is_enabled(name),
        "overridden": False,
        "scope": "this_replica",
    }


@app.get("/v1/admin/overrides", tags=["admin"])
def list_overrides_endpoint(
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    return {"overrides": db.list_answer_overrides(), "exact_match": True}


@app.put("/v1/admin/overrides", tags=["admin"])
def put_override_endpoint(
    body: dict,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    from . import cms

    if ctx.user and ctx.role != "ura_admin":
        raise HTTPException(status_code=403, detail="only ura_admin may edit overrides")
    try:
        row = cms.upsert(
            str(body.get("query") or ""),
            str(body.get("reply") or ""),
            source_url=str(body.get("source_url") or ""),
            created_by=ctx.user.user_id if ctx.user else "",
            enabled=bool(body.get("enabled", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return row


@app.delete("/v1/admin/overrides/{override_id}", tags=["admin"])
def delete_override_endpoint(
    override_id: str,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    if ctx.user and ctx.role != "ura_admin":
        raise HTTPException(status_code=403, detail="only ura_admin may edit overrides")
    ok = db.delete_answer_override(override_id)
    if not ok:
        raise HTTPException(status_code=404, detail="override not found")
    return {"ok": True, "id": override_id}


@app.get("/v1/admin/outbox", tags=["admin"])
def list_outbox_endpoint(
    _ctx: AuthContext = Depends(require_admin_access),
    limit: int = 50,
) -> dict:
    """Mock email/SMS queue. provider=mock; nothing is sent."""
    return {"items": db.list_notification_outbox(limit=limit), "live": False}


@app.get("/v1/admin/tickets/{ticket_id}", tags=["admin"])
def get_ticket_endpoint(
    request: Request,
    ticket_id: str = Path(..., pattern=r"^[a-f0-9-]{1,64}$"),
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Fetch a single ticket, including the conversation transcript.

    The transcript is the snapshot taken when the ticket was raised, so
    it is still here after ``conversations`` has been purged.
    """
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    ticket["viewers"] = db.list_ticket_viewers(ticket_id)
    return ticket


@app.patch("/v1/admin/tickets/{ticket_id}", tags=["admin"])
def update_ticket_endpoint(
    request: Request,
    ticket_id: str = Path(..., pattern=r"^[a-f0-9-]{1,64}$"),
    status: str | None = None,
    assignee: str | None = None,
    staff_note: str | None = None,
    priority: str | None = None,
    officer_reply: str | None = None,
    _ctx: AuthContext = Depends(require_admin_access),
) -> dict:
    """Update a ticket's status/assignee/note/priority/reply.

    ``officer_reply`` is shown to the **taxpayer** when they next open
    the conversation; ``staff_note`` stays internal. They are separate
    fields on purpose — an officer's candid note is not something the
    taxpayer should ever read.
    """
    ok = db.update_ticket(
        ticket_id,
        status=status,
        assignee=assignee,
        staff_note=staff_note,
        priority=priority,
        officer_reply=officer_reply,
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
    import time as _time

    from .voice_consent import get_voice_audit_log, voice_audit_stats

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
    from .tools.ura_account import account_api_status

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
        "account_api": account_api_status(),
    }


@app.get("/v1/me/reminders", tags=["me"])
def me_list_reminders(ctx: AuthContext = Depends(require_user)) -> dict:
    """In-app deadline inbox. Does not send email or SMS."""
    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    return {"reminders": db.list_reminder_inbox(row["id"])}


@app.post("/v1/me/reminders/refresh", tags=["me"])
def me_refresh_reminders(ctx: AuthContext = Depends(require_user)) -> dict:
    """Run the selector and persist matches to the inbox."""
    from .reminders import refresh_inbox

    row = db.upsert_user(
        external_id=ctx.user.user_id,
        tenant_id=ctx.tenant_id,
        email=ctx.user.email,
        role=ctx.role,
    )
    profile = db.get_user_profile(row["id"]) or {}
    return refresh_inbox(row["id"], profile, tenant_id=ctx.tenant_id)


@app.get("/v1/me/account", tags=["me"])
def me_account(ctx: AuthContext = Depends(require_user)) -> dict:
    """Sandbox or live account snapshot. Mock is never labeled live."""
    from .tools.ura_account import UraAccountProfileTool, account_api_status

    status = account_api_status()
    taxpayer_id = ctx.user.user_id if ctx.user else ""
    result = UraAccountProfileTool().execute(taxpayer_id=taxpayer_id)
    return {**status, **result, "user_id": taxpayer_id}


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
    if "analytics" in body.purposes:
        withdrawn["analytics_data"] = db.delete_user_analytics(ctx.user.user_id)
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
    data["documents"] = documents.export_user_documents(ctx.user.user_id)
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
    counts["documents"] = sum(documents.forget_user_documents(ctx.user.user_id).values())
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

    # Extract image (hard cap: 40 MiB)
    image_file = body.get("image")
    image_bytes = await image_file.read() if image_file else b""
    if len(image_bytes) > 40 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image exceeds 40 MiB limit")

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
