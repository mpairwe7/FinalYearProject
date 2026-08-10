"""WebSocket endpoint for streaming voice chat (2026).

Protocol (see ``voice_stream.py`` for the engine):

Client -> Server::

    {type: "session_start", language, voice, conversation_id, sample_rate, vad_sensitivity}
    [binary frames: PCM16 LE mono audio, 20ms recommended]
    {type: "barge_in"}
    {type: "session_end"}

Server -> Client::

    {type: "session_ready", session_id}
    {type: "vad_state", speaking: bool}
    {type: "transcript_final", text, language, latency_s, backend}
    {type: "audio_start", sample_rate}
    [binary frames: TTS audio chunks]
    {type: "audio_end"}
    {type: "reply_text", text, chunk_index}
    {type: "reply_meta", sources, citations, faithfulness_score, conversation_id}
    {type: "latency_report", asr_ms, mt_ms, llm_ms, tts_first_chunk_ms, total_ms}
    {type: "error", detail, recoverable}
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
from .voice_stream import VADConfig, VoiceSession, VoiceStreamEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prometheus metric helpers (lazy import to avoid circular deps)
# ---------------------------------------------------------------------------

_metrics_registered = False


def _ensure_metrics() -> None:
    """Register voice-specific Prometheus metrics once."""
    global _metrics_registered
    if _metrics_registered:
        return
    try:
        from prometheus_client import Counter, Gauge, Histogram

        # These are module-level singletons — safe to create once.
        globals()["_ws_connections_total"] = Counter(
            "voice_ws_connections_total",
            "Total WebSocket voice connections opened",
        )
        globals()["_ws_active"] = Gauge(
            "voice_ws_active_connections",
            "Currently active WebSocket voice sessions",
        )
        globals()["_ws_session_duration"] = Histogram(
            "voice_ws_session_duration_seconds",
            "Duration of each WebSocket voice session",
            buckets=(1, 5, 15, 30, 60, 120, 300, 600),
        )
        globals()["_stream_asr_latency"] = Histogram(
            "voice_stream_asr_latency_seconds",
            "ASR latency in streaming mode",
            buckets=(0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0),
        )
        globals()["_stream_tts_first_chunk"] = Histogram(
            "voice_stream_tts_first_chunk_seconds",
            "Time-to-first-TTS-audio-byte",
            buckets=(0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0),
        )
        globals()["_stream_total_latency"] = Histogram(
            "voice_stream_total_latency_seconds",
            "End-to-end turn latency (utterance end -> last TTS byte)",
            buckets=(0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 5.0),
        )
        globals()["_barge_in_total"] = Counter(
            "voice_barge_in_total",
            "Number of barge-in interruptions",
        )
        globals()["_vad_utterances_total"] = Counter(
            "voice_vad_utterances_total",
            "Number of utterances detected by VAD",
        )
        globals()["_consent_denied_total"] = Counter(
            "voice_consent_denied_total",
            "Consent check failures",
        )
        _metrics_registered = True
    except ImportError:
        _metrics_registered = True  # skip silently if prometheus_client absent


def _inc_metric(name: str, value: float = 1.0) -> None:
    metric = globals().get(name)
    if metric is None:
        return
    if hasattr(metric, "observe"):
        metric.observe(value)
    elif hasattr(metric, "inc"):
        metric.inc(value)


def _dec_metric(name: str) -> None:
    metric = globals().get(name)
    if metric is not None and hasattr(metric, "dec"):
        metric.dec()


def _resolve_ws_auth(websocket: WebSocket, *, required: bool = False) -> tuple[str, str]:
    """Verify WebSocket auth when present and return ``(user_id, tenant_id)``."""
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


async def voice_stream_ws(websocket: WebSocket, app: object) -> None:
    """WebSocket handler for ``/v1/voice/chat/stream``.

    Called from ``main.py``.  Manages the full session lifecycle.
    """
    _ensure_metrics()

    # ── Feature flag gate ──────────────────────────────────────────
    if not flags.is_enabled("voice_streaming"):
        await websocket.close(code=1001, reason="voice_streaming flag is disabled")
        return

    try:
        authenticated_user_id, authenticated_tenant_id = _resolve_ws_auth(
            websocket, required=flags.is_enabled("auth_required")
        )
    except JWTAuthError as exc:
        await websocket.close(code=1008, reason=f"authentication failed: {exc}")
        return

    # P1-9: cap concurrent voice sockets per-user and globally.
    socket_user_key = (
        authenticated_user_id
        or f"anon::{websocket.client.host if websocket.client else 'unknown'}"
    )
    if not ws_concurrency.try_acquire(
        "voice",
        socket_user_key,
        per_user_cap=ws_concurrency.int_env("VOICE_WS_MAX_PER_USER", 3),
        global_cap=ws_concurrency.int_env("VOICE_WS_MAX_GLOBAL", 64),
    ):
        await websocket.close(code=1013, reason="voice concurrency limit reached")
        return

    await websocket.accept()
    _inc_metric("_ws_connections_total")
    _inc_metric("_ws_active")
    session_start_time = time.perf_counter()
    max_duration_s = ws_concurrency.int_env("VOICE_WS_MAX_DURATION_S", 30 * 60)
    idle_timeout_s = ws_concurrency.int_env("VOICE_WS_IDLE_TIMEOUT_S", 120)

    session: VoiceSession | None = None

    try:
        # ── Wait for session_start config frame ────────────────────
        config_raw = await websocket.receive_text()
        try:
            config = json.loads(config_raw)
        except json.JSONDecodeError:
            await _send_error(websocket, "Invalid JSON in session_start frame", recoverable=False)
            return

        if config.get("type") != "session_start":
            await _send_error(websocket, "First message must be session_start", recoverable=False)
            return

        # ── Validate config ────────────────────────────────────────
        language = config.get("language", "en")
        if not isinstance(language, str) or len(language) != 2:
            language = "en"

        voice = config.get("voice")
        conversation_id = config.get("conversation_id")
        sample_rate = int(config.get("sample_rate", 16000))
        vad_sensitivity = config.get("vad_sensitivity", "medium")
        tts_enabled = config.get("tts_enabled", True)
        top_k = min(max(int(config.get("top_k", 4)), 1), 10)
        session_user_id = authenticated_user_id
        session_tenant_id = authenticated_tenant_id if authenticated_user_id else str(
            config.get("tenant_id", "default")
        )

        # Clamp sample_rate
        if sample_rate < 8000 or sample_rate > 48000:
            sample_rate = 16000

        # ── Consent check ──────────────────────────────────────────
        if flags.is_enabled("voice_consent"):
            try:
                from .voice_consent import require_voice_consent

                if session_user_id:
                    consent_ok = require_voice_consent(session_user_id)
                else:
                    consent_ok = bool(config.get("voice_consent_accepted"))
                if not consent_ok:
                    _inc_metric("_consent_denied_total")
                    await _send_error(
                        websocket,
                        "Voice recording consent required before processing audio.",
                        recoverable=False,
                    )
                    return
            except ImportError:
                pass  # voice_consent module not yet available

        # ── Resolve speech + chat model ────────────────────────────
        speech = getattr(getattr(app, "state", None), "speech", None)
        chat_model = getattr(getattr(app, "state", None), "model", None)

        if speech is None or chat_model is None:
            await _send_error(websocket, "Speech or chat model not available", recoverable=False)
            return

        # ── Create session ─────────────────────────────────────────
        session_id = str(uuid.uuid4())
        vad_config = VADConfig.from_sensitivity(vad_sensitivity, sample_rate=sample_rate)

        session = VoiceSession(
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
        )

        await _send_json(websocket, {"type": "session_ready", "session_id": session_id})
        logger.info(
            "Voice session started (session=%s, lang=%s, sr=%d, vad=%s)",
            session_id,
            language,
            sample_rate,
            vad_sensitivity,
        )

        # Audit trail — log session start
        try:
            from .voice_consent import log_voice_event

            log_voice_event(
                user_id=session_user_id,
                session_id=session_id,
                event_type="session_start",
                metadata={
                    "language": language,
                    "vad_sensitivity": vad_sensitivity,
                    "tts_enabled": tts_enabled,
                    "sample_rate": sample_rate,
                },
            )
        except Exception:
            logger.debug("Voice audit log failed for session_start", exc_info=True)

        # ── Rate limiting state (per-session audio flood protection) ──
        _audio_frame_count = 0
        _audio_frame_window_start = time.perf_counter()
        _MAX_AUDIO_FRAMES_PER_SEC = 100  # 100 × 20ms = 2s of audio/sec max
        _MAX_AUDIO_BYTES_PER_FRAME = 64 * 1024  # 64 KB per frame

        # ── Main loop ──────────────────────────────────────────────
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

            # Binary frame = audio chunk
            if "bytes" in message and message["bytes"]:
                chunk = message["bytes"]

                # Per-frame size limit
                if len(chunk) > _MAX_AUDIO_BYTES_PER_FRAME:
                    await _send_error(websocket, "Audio frame too large (max 64KB)", recoverable=True)
                    continue

                # Sliding-window rate limit
                now = time.perf_counter()
                if now - _audio_frame_window_start > 1.0:
                    _audio_frame_count = 0
                    _audio_frame_window_start = now
                _audio_frame_count += 1
                if _audio_frame_count > _MAX_AUDIO_FRAMES_PER_SEC:
                    # Drop excess frames silently to avoid flooding ASR
                    continue

                async for event in session.handle_audio_chunk(chunk):
                    await _dispatch_event(websocket, event)
                    # Track metrics from latency reports
                    if event.type == "latency_report":
                        _track_latency_metrics(event.data)
                    elif event.type == "transcript_final" and event.data.get("text"):
                        _inc_metric("_vad_utterances_total")

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
                    _inc_metric("_barge_in_total")
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

                elif msg_type == "session_end":
                    break

                elif msg_type in ("ping", "pong"):
                    # Application-level keepalive — respond silently
                    await _send_json(websocket, {"type": "pong"})

                else:
                    await _send_error(
                        websocket,
                        f"Unknown message type: {msg_type}",
                        recoverable=True,
                    )

    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected (session=%s)", session.session_id if session else "?")
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
        logger.exception("Voice WebSocket error")
        try:
            await _send_error(websocket, "Internal server error", recoverable=False)
        except Exception:
            pass
    finally:
        ws_concurrency.release("voice", socket_user_key)
        if session is not None:
            session.close()
        _dec_metric("_ws_active")
        duration = time.perf_counter() - session_start_time
        _inc_metric("_ws_session_duration", duration)
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_json(ws: WebSocket, data: dict) -> None:
    """Send a JSON text frame."""
    await ws.send_text(json.dumps(data))


async def _send_error(ws: WebSocket, detail: str, recoverable: bool = True) -> None:
    """Send an error event and optionally close."""
    await _send_json(ws, {"type": "error", "detail": detail, "recoverable": recoverable})
    if not recoverable:
        try:
            await ws.close(code=1008 if "consent" in detail.lower() else 1011)
        except Exception:
            pass


async def _dispatch_event(ws: WebSocket, event: VoiceStreamEvent) -> None:
    """Route a VoiceStreamEvent to the appropriate WebSocket frame type."""
    if event.type == "audio_chunk":
        # Audio data is sent as binary frames
        audio_bytes = event.data.get("audio", b"")
        if audio_bytes:
            await ws.send_bytes(audio_bytes)
    else:
        # Everything else is JSON
        payload = {"type": event.type, **event.data}
        # Remove non-serializable fields
        payload.pop("audio", None)
        await ws.send_text(json.dumps(payload, default=str))


def _track_latency_metrics(data: dict) -> None:
    """Record latency histogram observations from a latency_report event."""
    asr_s = data.get("asr_ms", 0) / 1000
    tts_first_s = data.get("tts_first_chunk_ms", 0) / 1000
    total_s = data.get("total_ms", 0) / 1000

    if asr_s > 0:
        _inc_metric("_stream_asr_latency", asr_s)
    if tts_first_s > 0:
        _inc_metric("_stream_tts_first_chunk", tts_first_s)
    if total_s > 0:
        _inc_metric("_stream_total_latency", total_s)
