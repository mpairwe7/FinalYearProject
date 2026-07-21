"""V2 WebSocket handler for streaming voice chat (2026).

Extends the V1 protocol (:mod:`voice_ws`) with:

* **Protocol version negotiation** — ``session_start.protocol_version``
* **Image frames** — ``{type: "image_frame"}`` + binary JPEG on next message
* **Partial transcripts** — ``{type: "partial_transcript", text, stable_prefix}``
* **Vision results** — ``{type: "vision_result", ocr_text, doc_type, summary}``

Backward-compatible: V1 clients that omit ``protocol_version`` receive
identical behaviour to the V1 handler.

Feature flags: ``native_voice``, ``voice_vision_v2``

Route: ``/v2/voice/chat/stream``
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from . import ws_concurrency
from .auth.jwt_auth import JWTAuthError, JWTVerifier
from .flags import flags
from .voice_stream import VADConfig
from .voice_stream_v2 import VoiceSessionV2, VoiceStreamEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prometheus metrics (extends V1 metrics)
# ---------------------------------------------------------------------------

_v2_metrics_registered = False


def _ensure_v2_metrics() -> None:
    """Register V2-specific Prometheus metrics once."""
    global _v2_metrics_registered
    if _v2_metrics_registered:
        return
    try:
        from prometheus_client import Counter, Gauge, Histogram

        globals()["_v2_connections_total"] = Counter(
            "voice_v2_connections_total",
            "Total V2 WebSocket voice connections",
        )
        globals()["_v2_active"] = Gauge(
            "voice_v2_active_connections",
            "Currently active V2 sessions",
        )
        globals()["_v2_session_duration"] = Histogram(
            "voice_v2_session_duration_seconds",
            "Duration of V2 sessions",
            buckets=(1, 5, 15, 30, 60, 120, 300, 600),
        )
        globals()["_v2_prefetch_hit"] = Counter(
            "voice_v2_speculative_prefetch_hits_total",
            "Speculative prefetch cache hits",
        )
        globals()["_v2_prefetch_miss"] = Counter(
            "voice_v2_speculative_prefetch_misses_total",
            "Speculative prefetch cache misses",
        )
        globals()["_v2_vision_requests"] = Counter(
            "voice_v2_vision_requests_total",
            "Vision frames processed",
        )
        globals()["_v2_path_routing"] = Counter(
            "voice_v2_path_routing_total",
            "Voice path routing decisions",
            ["path"],
        )
        globals()["_v2_tts_first_byte"] = Histogram(
            "voice_v2_tts_first_byte_seconds",
            "Time-to-first-TTS-audio-byte (V2 streaming)",
            buckets=(0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5),
        )
        _v2_metrics_registered = True
    except ImportError:
        _v2_metrics_registered = True


def _v2_metric(name: str, value: float = 1.0, labels: dict | None = None) -> None:
    """Record a V2 metric (no-op if prometheus_client is absent)."""
    metric = globals().get(name)
    if metric is None:
        return
    if labels and hasattr(metric, "labels"):
        metric = metric.labels(**labels)
    if hasattr(metric, "observe"):
        metric.observe(value)
    elif hasattr(metric, "inc"):
        metric.inc(value)


def _v2_dec(name: str) -> None:
    metric = globals().get(name)
    if metric is not None and hasattr(metric, "dec"):
        metric.dec()


# ---------------------------------------------------------------------------
# Auth helper (reused from V1)
# ---------------------------------------------------------------------------


def _resolve_ws_auth(websocket: WebSocket, *, required: bool = False) -> tuple[str, str]:
    """Verify WebSocket auth when present."""
    auth_header = websocket.headers.get("authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = websocket.query_params.get("access_token", "")
    if not token:
        if required:
            raise JWTAuthError("authentication required")
        return "", "default"

    claims = JWTVerifier().verify(token)
    return str(claims.get("sub", "")), str(claims.get("tenant_id", "default"))


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------


async def voice_stream_ws_v2(websocket: WebSocket, app: object) -> None:
    """V2 WebSocket handler for ``/v2/voice/chat/stream``."""
    _ensure_v2_metrics()

    # Feature flag gate: require native_voice
    if not flags.is_enabled("native_voice"):
        await websocket.close(code=1001, reason="native_voice flag is disabled")
        return

    try:
        user_id, tenant_id = _resolve_ws_auth(
            websocket, required=flags.is_enabled("auth_required")
        )
    except JWTAuthError as exc:
        await websocket.close(code=1008, reason=f"authentication failed: {exc}")
        return

    # P1-9: cap concurrent voice sockets per-user and globally so an
    # (anonymous) client cannot exhaust ASR/TTS/LLM resources.
    socket_user_key = user_id or f"anon::{websocket.client.host if websocket.client else 'unknown'}"
    if not ws_concurrency.try_acquire(
        "voice",
        socket_user_key,
        per_user_cap=ws_concurrency.int_env("VOICE_WS_MAX_PER_USER", 3),
        global_cap=ws_concurrency.int_env("VOICE_WS_MAX_GLOBAL", 64),
    ):
        await websocket.close(code=1013, reason="voice concurrency limit reached")
        return

    await websocket.accept()
    _v2_metric("_v2_connections_total")
    _v2_metric("_v2_active")
    session_start_time = time.perf_counter()
    max_duration_s = ws_concurrency.int_env("VOICE_WS_MAX_DURATION_S", 30 * 60)
    idle_timeout_s = ws_concurrency.int_env("VOICE_WS_IDLE_TIMEOUT_S", 120)

    session: VoiceSessionV2 | None = None

    try:
        # ── session_start config frame ────────────────────────────
        config_raw = await websocket.receive_text()
        try:
            config = json.loads(config_raw)
        except json.JSONDecodeError:
            await _send_error(websocket, "Invalid JSON in session_start frame", recoverable=False)
            return

        if config.get("type") != "session_start":
            await _send_error(websocket, "First message must be session_start", recoverable=False)
            return

        # Parse config (superset of V1)
        language = config.get("language", "en")
        if not isinstance(language, str) or len(language) > 5:
            language = "en"

        voice = config.get("voice")
        conversation_id = config.get("conversation_id")
        sample_rate = int(config.get("sample_rate", 16000))
        vad_sensitivity = config.get("vad_sensitivity", "medium")
        tts_enabled = config.get("tts_enabled", True)
        top_k = min(max(int(config.get("top_k", 4)), 1), 10)
        vision_enabled = config.get("vision_enabled", False)
        protocol_version = int(config.get("protocol_version", 2))

        if sample_rate < 8000 or sample_rate > 48000:
            sample_rate = 16000

        session_user_id = user_id
        session_tenant_id = tenant_id if user_id else str(config.get("tenant_id", "default"))

        # ── Consent check (same as V1) ────────────────────────────
        if flags.is_enabled("voice_consent"):
            try:
                from .voice_consent import require_voice_consent

                if session_user_id:
                    consent_ok = require_voice_consent(session_user_id)
                else:
                    consent_ok = bool(config.get("voice_consent_accepted"))
                if not consent_ok:
                    await _send_error(
                        websocket,
                        "Voice recording consent required before processing audio.",
                        recoverable=False,
                    )
                    return
            except ImportError:
                pass

        # ── Resolve models ────────────────────────────────────────
        speech = getattr(getattr(app, "state", None), "speech", None)
        chat_model = getattr(getattr(app, "state", None), "model", None)

        if speech is None or chat_model is None:
            await _send_error(websocket, "Speech or chat model not available", recoverable=False)
            return

        # ── Create V2 session ─────────────────────────────────────
        session_id = str(uuid.uuid4())
        vad_config = VADConfig.from_sensitivity(vad_sensitivity, sample_rate=sample_rate)

        session = VoiceSessionV2(
            session_id=session_id,
            speech=speech,
            chat_model=chat_model,
            vad_config=vad_config,
            language=language,
            voice=voice,
            conversation_id=conversation_id,
            tts_enabled=tts_enabled,
            top_k=top_k,
            user_id=session_user_id,
            tenant_id=session_tenant_id,
            vision_enabled=vision_enabled and flags.is_enabled("voice_vision_v2"),
        )

        await _send_json(websocket, {
            "type": "session_ready",
            "session_id": session_id,
            "protocol_version": protocol_version,
            "capabilities": {
                "streaming_tts": flags.is_enabled("streaming_tts_v2"),
                "speculative_prefetch": flags.is_enabled("speculative_prefetch"),
                "vision": vision_enabled and flags.is_enabled("voice_vision_v2"),
            },
        })

        logger.info(
            "V2 session started (session=%s, lang=%s, sr=%d, vision=%s)",
            session_id, language, sample_rate, vision_enabled,
        )

        # Audit log
        try:
            from .voice_consent import log_voice_event

            log_voice_event(
                user_id=session_user_id,
                session_id=session_id,
                event_type="session_start",
                metadata={
                    "language": language,
                    "protocol_version": protocol_version,
                    "vision_enabled": vision_enabled,
                    "vad_sensitivity": vad_sensitivity,
                },
            )
        except Exception:
            logger.debug("Voice audit log failed for session_start", exc_info=True)

        # ── Rate limiting state ───────────────────────────────────
        audio_frame_count = 0
        frame_window_start = time.perf_counter()
        _MAX_FRAMES_PER_SEC = 100
        _MAX_BYTES_PER_FRAME = 64 * 1024
        _MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB

        expecting_image_binary = False

        # ── Main loop ─────────────────────────────────────────────
        while True:
            if time.perf_counter() - session_start_time > max_duration_s:
                await _send_json(
                    websocket, {"type": "session.expired", "reason": "max_duration_exceeded"}
                )
                break
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=idle_timeout_s
                )
            except asyncio.TimeoutError:
                await _send_json(
                    websocket, {"type": "session.expired", "reason": "idle_timeout"}
                )
                break

            if message.get("type") == "websocket.disconnect":
                break

            # Binary frame
            if "bytes" in message and message["bytes"]:
                chunk = message["bytes"]

                # Image binary follows an image_frame control message
                if expecting_image_binary:
                    expecting_image_binary = False
                    if len(chunk) > _MAX_IMAGE_BYTES:
                        await _send_error(websocket, "Image too large (max 2MB)", recoverable=True)
                        continue
                    await session.handle_image_frame(chunk)
                    _v2_metric("_v2_vision_requests")
                    continue

                # Audio frame
                if len(chunk) > _MAX_BYTES_PER_FRAME:
                    await _send_error(websocket, "Audio frame too large (max 64KB)", recoverable=True)
                    continue

                now = time.perf_counter()
                if now - frame_window_start > 1.0:
                    audio_frame_count = 0
                    frame_window_start = now
                audio_frame_count += 1
                if audio_frame_count > _MAX_FRAMES_PER_SEC:
                    continue  # silently drop excess

                async for event in session.handle_audio_chunk(chunk):
                    await _dispatch_event(websocket, event)
                    _track_v2_metrics(event)

            # Text frame = control message
            elif "text" in message and message["text"]:
                try:
                    control = json.loads(message["text"])
                except json.JSONDecodeError:
                    await _send_error(websocket, "Invalid JSON", recoverable=True)
                    continue

                msg_type = control.get("type", "")

                if msg_type == "barge_in":
                    await session.barge_in()
                    await _send_json(websocket, {"type": "audio_end"})
                    try:
                        from .voice_consent import log_voice_event

                        log_voice_event(
                            user_id=session.user_id,
                            session_id=session.session_id,
                            event_type="barge_in",
                            metadata={"turn": session._turn_count},
                        )
                    except Exception:
                        pass

                elif msg_type == "image_frame":
                    # Next binary frame is a JPEG image
                    if not session.vision_enabled:
                        await _send_error(websocket, "Vision not enabled for this session", recoverable=True)
                    else:
                        expecting_image_binary = True

                elif msg_type == "session_end":
                    break

                elif msg_type in ("ping", "pong"):
                    await _send_json(websocket, {"type": "pong"})

                else:
                    await _send_error(
                        websocket, f"Unknown message type: {msg_type}", recoverable=True,
                    )

    except WebSocketDisconnect:
        logger.info("V2 WebSocket disconnected (session=%s)", session.session_id if session else "?")
        if session:
            try:
                from .voice_consent import log_voice_event

                log_voice_event(
                    user_id=session.user_id,
                    session_id=session.session_id,
                    event_type="session_end",
                    metadata={"turns": session._turn_count, "reason": "disconnect"},
                )
            except Exception:
                pass
    except Exception:
        logger.exception("V2 WebSocket error")
        try:
            await _send_error(websocket, "Internal server error", recoverable=False)
        except Exception:
            pass
    finally:
        ws_concurrency.release("voice", socket_user_key)
        if session is not None:
            session.close()
        _v2_dec("_v2_active")
        duration = time.perf_counter() - session_start_time
        _v2_metric("_v2_session_duration", duration)
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_json(ws: WebSocket, data: dict) -> None:
    await ws.send_text(json.dumps(data, default=str))


async def _send_error(ws: WebSocket, detail: str, recoverable: bool = True) -> None:
    await _send_json(ws, {"type": "error", "detail": detail, "recoverable": recoverable})
    if not recoverable:
        try:
            await ws.close(code=1008 if "consent" in detail.lower() else 1011)
        except Exception:
            pass


async def _dispatch_event(ws: WebSocket, event: VoiceStreamEvent) -> None:
    """Route a VoiceStreamEvent to the appropriate WebSocket frame type."""
    if event.type == "audio_chunk":
        audio_bytes = event.data.get("audio", b"")
        if audio_bytes:
            await ws.send_bytes(audio_bytes)
    else:
        payload = {"type": event.type, **event.data}
        payload.pop("audio", None)
        await ws.send_text(json.dumps(payload, default=str))


def _track_v2_metrics(event: VoiceStreamEvent) -> None:
    """Record V2-specific metrics from pipeline events."""
    if event.type == "latency_report":
        data = event.data
        tts_first_s = data.get("tts_first_chunk_ms", 0) / 1000
        if tts_first_s > 0:
            _v2_metric("_v2_tts_first_byte", tts_first_s)

        voice_path = data.get("voice_path", "grounded")
        _v2_metric("_v2_path_routing", labels={"path": voice_path})

        if data.get("speculative_prefetch_used"):
            _v2_metric("_v2_prefetch_hit")
        else:
            _v2_metric("_v2_prefetch_miss")
