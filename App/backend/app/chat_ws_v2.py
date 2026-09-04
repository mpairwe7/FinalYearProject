"""V2 WebSocket handler for streaming text chat (Phase 29, 2026).

Companion to ``voice_ws_v2`` — adapts the same persistent-socket pattern
to the text path so agentic workflows can surface tool calls, retrieval
events and inline confirmations to the client without re-opening an HTTP
connection per turn.

This module ships **Phase 0 only**: lifecycle + protocol negotiation.
The handler accepts a session, returns ``session_ready``, answers
``ping`` with ``pong``, echoes a stub ``response.error`` to any
``response.create`` until later phases wire in :func:`service.run_chat_turn`,
and closes cleanly on ``session_end`` / disconnect.

Wire protocol (full spec in ``docs/ws_chat_protocol.md``)::

    -> {"type": "session_start", "conversation_id": "...", "locale": "en",
        "previous_response_id": "...", "protocol_version": 1}
    <- {"type": "session_ready", "session_id": "...", "protocol_version": 1,
        "capabilities": {...}, "resume": false}
    -> {"type": "response.create", "input": "user text", "tools": [...]}
    <- {"type": "response.error", ...}   # phase 0 stub
    -> {"type": "session_end"}

Auth and rate limiting reuse helpers from :mod:`voice_ws_v2`.

Route: ``/v2/chat/stream``  •  Feature flag: ``ws_chat``
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from . import confirm_tokens
from . import database as db
from . import service as service_module
from .auth.jwt_auth import JWTAuthError, JWTVerifier
from .flags import flags
from .voice_ws_v2 import _send_error, _send_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_metrics_registered = False


def _ensure_metrics() -> None:
    global _metrics_registered
    if _metrics_registered:
        return
    try:
        from prometheus_client import Counter, Gauge, Histogram

        globals()["_ws_chat_connections_total"] = Counter(
            "chat_ws_connections_total",
            "Total chat WebSocket connections accepted",
        )
        globals()["_ws_chat_active"] = Gauge(
            "chat_ws_active_connections",
            "Currently active chat WebSocket sessions",
        )
        globals()["_ws_chat_session_duration"] = Histogram(
            "chat_ws_session_duration_seconds",
            "Duration of chat WebSocket sessions",
            buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
        )
        _metrics_registered = True
    except ImportError:
        _metrics_registered = True


def _metric(name: str, value: float = 1.0) -> None:
    metric = globals().get(name)
    if metric is None:
        return
    if hasattr(metric, "observe"):
        metric.observe(value)
    elif hasattr(metric, "inc"):
        metric.inc(value)


def _metric_dec(name: str) -> None:
    metric = globals().get(name)
    if metric is not None and hasattr(metric, "dec"):
        metric.dec()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

# 60 min matches the OpenAI WebSocket-mode Responses API cap.
_SESSION_MAX_DURATION_S = 60 * 60
# Conservative ceiling on a single inbound control frame; tighter than the
# voice audio cap because text payloads are small.
_MAX_TEXT_FRAME_BYTES = 64 * 1024

# How many recent turns to keep in the in-memory cache.  Older turns are
# dropped from the LLM prompt window anyway; this matches the legacy
# ``db.get_recent_turns(limit=5)`` semantics.
_HISTORY_CACHE_MAX = 32

# Phase 6 — concurrent socket cap per user.  Anonymous users share the
# "anon" slot.  Override via ``WS_CHAT_MAX_PER_USER`` env var.
_PER_USER_SOCKET_CAP_DEFAULT = 5


def _resolve_per_user_cap() -> int:
    raw = os.getenv("WS_CHAT_MAX_PER_USER")
    if not raw:
        return _PER_USER_SOCKET_CAP_DEFAULT
    try:
        return max(1, int(raw))
    except ValueError:
        return _PER_USER_SOCKET_CAP_DEFAULT


_active_per_user_lock = threading.Lock()
_active_per_user: dict[str, int] = {}


def _try_acquire_socket_slot(user_key: str) -> bool:
    cap = _resolve_per_user_cap()
    with _active_per_user_lock:
        current = _active_per_user.get(user_key, 0)
        if current >= cap:
            return False
        _active_per_user[user_key] = current + 1
        return True


def _release_socket_slot(user_key: str) -> None:
    with _active_per_user_lock:
        current = _active_per_user.get(user_key, 0)
        if current <= 1:
            _active_per_user.pop(user_key, None)
        else:
            _active_per_user[user_key] = current - 1


def _resolve_ws_principal(
    websocket: WebSocket, *, required: bool = False
) -> tuple[str, str, str, list[str]]:
    """Resolve ``(user_id, tenant_id, user_role, granted_purposes)`` from the socket.

    Role and consent purposes are read **only** from the verified JWT — never
    from a client frame — so a later ``tool_call.confirm`` re-authorizes the
    submit against the real authenticated principal (P0-1).  Mirrors
    :func:`voice_ws_v2._resolve_ws_auth` for token extraction; returns the
    anonymous ``public`` principal when no token is present and auth is optional.
    """
    auth_header = websocket.headers.get("authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = websocket.query_params.get("access_token", "")
    if not token:
        if required:
            raise JWTAuthError("authentication required")
        return "", "default", "public", []

    verifier = JWTVerifier()
    claims = verifier.verify(token)
    from .auth.dependencies import resolve_role

    role = resolve_role(claims, verifier.audience)
    granted = claims.get("granted_purposes", [])
    purposes = [str(p) for p in granted] if isinstance(granted, list) else []
    return (
        str(claims.get("sub", "")),
        str(claims.get("tenant_id", "default")),
        role,
        purposes,
    )


@dataclass
class WsChatSession:
    """Phase 3 — connection-local state for a single WebSocket session.

    Holds the conversation history, last response id and any per-socket
    derived state.  Lives for the lifetime of one WebSocket; reload
    semantics on reconnect are best-effort (``previous_response_id``).
    """

    session_id: str
    conversation_id: str
    user_id: str
    tenant_id: str
    locale: str
    # Authenticated principal — resolved from the verified JWT at session_start,
    # never from a client frame.  Used to re-authorize HITL tool confirmations.
    user_role: str = "public"
    granted_purposes: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    last_response_id: str = ""
    workflow_session: Any = None  # WorkflowSession when active; loaded lazily.
    cancel_event: threading.Event = field(default_factory=threading.Event)
    resume_attempted: bool = False
    resumed: bool = False
    # Phase 4: confirmation tokens already consumed on this socket (single-use).
    consumed_confirmations: set[str] = field(default_factory=set)
    # Phase 4: in-flight confirmation slots (call_id -> proposal summary)
    pending_confirmations: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Phase 5: most recent speculative-prefetch prefix (cosmetic for now).
    last_partial_prefix: str = ""

    def append_turn(self, user_msg: str, assistant_msg: str) -> None:
        """Append a turn to the in-memory history (FIFO eviction)."""
        if user_msg:
            self.history.append({"role": "user", "content": user_msg})
        if assistant_msg:
            self.history.append({"role": "assistant", "content": assistant_msg})
        # Trim oldest pairs once we exceed the cap.
        if len(self.history) > _HISTORY_CACHE_MAX:
            self.history = self.history[-_HISTORY_CACHE_MAX:]

    def try_resume(self, previous_response_id: str) -> bool:
        """Best-effort hydrate history from the analytics DB.

        Returns True if any history was recovered.  We use the SQLite
        conversation log as the source of truth — there's no separate
        response-id index today, so the conservative behaviour is to
        load the last few turns of the same conversation.  Phase 4 of
        the broader plan adds a real response_id table; until then we
        record the attempt for observability.
        """
        self.resume_attempted = True
        if not previous_response_id or not self.conversation_id:
            return False
        try:
            rows = db.get_recent_turns(
                session_id=None,
                conversation_id=self.conversation_id,
                limit=10,
            )
        except Exception:
            logger.debug("resume: get_recent_turns failed", exc_info=True)
            return False
        if not rows:
            return False
        hydrated: list[dict[str, str]] = []
        for r in rows:
            u = r.get("user_message", "")
            b = r.get("bot_reply", "")
            if u:
                hydrated.append({"role": "user", "content": u})
            if b:
                hydrated.append({"role": "assistant", "content": b})
        self.history = hydrated
        self.last_response_id = previous_response_id
        self.resumed = True
        return True


async def _run_response_create(
    websocket: WebSocket,
    *,
    chat_model: Any,
    msg: dict[str, Any],
    session: WsChatSession,
) -> None:
    """Execute a single ``response.create`` request to completion.

    Delegates to :func:`service.run_chat_turn` and forwards the
    transport-agnostic event tuples as WS frames.  In Phase 1 this is
    sequential per socket: the receive loop blocks until the turn is
    finished or the socket disconnects.
    """
    user_input = msg.get("input")
    if not isinstance(user_input, str) or not user_input.strip():
        await _send_json(
            websocket,
            {
                "type": "response.error",
                "code": "invalid_input",
                "message": "response.create requires a non-empty `input` string.",
            },
        )
        await _send_json(websocket, {"type": "response.done"})
        return

    locale = msg.get("locale") or session.locale or "en"
    top_k = int(msg.get("top_k", 4))
    top_k = max(1, min(top_k, 10))
    request_id = msg.get("metadata", {}).get("client_request_id") if isinstance(
        msg.get("metadata"), dict
    ) else None

    # Per-turn cancel event; session-scoped event is also honored.
    cancel_event = threading.Event()

    def _alive() -> bool:
        if session.cancel_event.is_set():
            cancel_event.set()
        return websocket.client_state == WebSocketState.CONNECTED

    final_log: dict[str, Any] | None = None
    full_reply = ""

    try:
        async for event_type, payload in service_module.run_chat_turn(
            chat_model,
            message=user_input,
            conversation_id=session.conversation_id or None,
            top_k=top_k,
            locale=locale,
            session_id=session.session_id or None,
            request_id=request_id,
            user_id=session.user_id or None,
            tenant_id=session.tenant_id,
            user_role=session.user_role,
            granted_purposes=session.granted_purposes,
            should_continue=_alive,
            cancel_event=cancel_event,
            sentence_batching=False,  # WS wants low TTFT per-token frames
            conversation_history_override=list(session.history) if session.history else None,
        ):
            if event_type == "_keepalive":
                await _send_json(websocket, {"type": "ping"})
                continue
            if event_type == "_log":
                final_log = payload
                full_reply = payload.get("full_reply", "")
                continue
            if not _alive():
                cancel_event.set()
                return

            frame_type = {
                "metadata": "response.metadata",
                "token": "response.token",
                "revision": "response.revision",
                "grounding": "response.grounding",
                "done": "response.done",
                "error": "response.error",
                "retrieval.started": "response.retrieval.started",
                "retrieval.completed": "response.retrieval.completed",
                "translation.started": "response.translation.started",
                "translation.completed": "response.translation.completed",
                "iteration.started": "response.iteration.started",
                "iteration.final": "response.iteration.final",
                "tool_call.started": "response.tool_call.started",
                "tool_call.completed": "response.tool_call.completed",
                "tool_call.error": "response.tool_call.error",
            }.get(event_type, f"response.{event_type}")

            if event_type == "token":
                await _send_json(websocket, {"type": frame_type, "delta": payload})
            elif event_type == "revision":
                await _send_json(websocket, {"type": frame_type, "text": payload})
            elif event_type in ("metadata", "grounding"):
                await _send_json(websocket, {"type": frame_type, **payload})
            elif event_type == "error":
                err_payload = payload if isinstance(payload, dict) else {"message": str(payload)}
                await _send_json(websocket, {"type": frame_type, **err_payload})
            elif event_type == "tool_call.confirmation_required":
                # Phase 4: sign a single-use HMAC token and stash the
                # proposal in the session so a subsequent tool_call.confirm
                # frame can be validated and acted on.
                evt_dict = payload if isinstance(payload, dict) else {"value": payload}
                call_id = str(evt_dict.get("call_id", ""))
                tool_name = str(evt_dict.get("name", ""))
                proposal = evt_dict.get("proposal", {}) or {}
                idem_key = str(
                    evt_dict.get("idempotency_key")
                    or proposal.get("idempotency_key", "")
                )
                confirm_token = confirm_tokens.sign(
                    call_id=call_id,
                    tool_name=tool_name,
                    idempotency_key=idem_key,
                    session_id=session.session_id,
                )
                session.pending_confirmations[call_id] = {
                    "tool_name": tool_name,
                    "proposal": proposal,
                    "idempotency_key": idem_key,
                }
                await _send_json(
                    websocket,
                    {
                        "type": "response.tool_call.confirmation_required",
                        "call_id": call_id,
                        "name": tool_name,
                        "proposal": proposal,
                        "idempotency_key": idem_key,
                        "confirm_token": confirm_token,
                    },
                )
            elif event_type.startswith(("retrieval.", "iteration.", "tool_call.", "translation.")):
                evt_dict = payload if isinstance(payload, dict) else {"value": payload}
                evt_dict = {k: v for k, v in evt_dict.items() if k != "type"}
                await _send_json(websocket, {"type": frame_type, **evt_dict})
            else:  # done
                await _send_json(websocket, {"type": frame_type})

    finally:
        if final_log is not None:
            _log_ws_turn(
                session_id=session.session_id,
                conversation_id=session.conversation_id,
                user_message=user_input,
                full_reply=full_reply,
                log_payload=final_log,
                user_id=session.user_id or "",
            )
            # Update the in-memory history cache so subsequent turns can
            # skip the DB fetch.  Also pick up the conversation_id the
            # model may have minted on this turn.
            result = final_log.get("result") or {}
            new_conv = result.get("conversation_id")
            if new_conv and not session.conversation_id:
                session.conversation_id = new_conv
            if full_reply:
                session.append_turn(user_input, full_reply)


async def _handle_tool_call_confirm(
    websocket: WebSocket,
    *,
    msg: dict[str, Any],
    session: WsChatSession,
) -> None:
    """Validate a tool_call.confirm frame and execute the action.

    Flow (per Phase 4 of the agentic-WS plan):
      1. Validate the HMAC ``confirm_token`` (must match the original
         call_id + session_id and not be expired).
      2. Single-use check: reject replays against the same call_id.
      3. Look up the pending proposal stashed when we emitted the
         ``confirmation_required`` event.
      4. If approved: invoke the tool with ``submit=True`` and the same
         idempotency_key.  Forward the result.  If rejected: skip the
         invocation and emit a rejection frame.
      5. Audit log the decision when ``audit_ledger`` is enabled.
    """
    confirm_token = str(msg.get("confirm_token") or "")
    call_id = str(msg.get("call_id") or "")
    idempotency_key = str(msg.get("idempotency_key") or "")
    decision = str(msg.get("decision") or "").lower()

    if decision not in {"approve", "reject"}:
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.confirm_failed",
                "call_id": call_id,
                "reason": "decision must be approve or reject",
            },
        )
        return

    payload = confirm_tokens.verify(
        confirm_token,
        expected_call_id=call_id,
        expected_session_id=session.session_id,
    )
    if payload is None:
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.confirm_failed",
                "call_id": call_id,
                "reason": "invalid or expired confirm_token",
            },
        )
        return

    if call_id in session.consumed_confirmations:
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.confirm_failed",
                "call_id": call_id,
                "reason": "confirmation already consumed",
            },
        )
        return

    pending = session.pending_confirmations.get(call_id)
    if pending is None:
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.confirm_failed",
                "call_id": call_id,
                "reason": "no pending confirmation for this call_id",
            },
        )
        return

    tool_name = pending["tool_name"]
    proposal = pending["proposal"]
    bound_idem = pending["idempotency_key"]
    if idempotency_key and bound_idem and idempotency_key != bound_idem:
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.confirm_failed",
                "call_id": call_id,
                "reason": "idempotency_key mismatch",
            },
        )
        return

    session.consumed_confirmations.add(call_id)
    session.pending_confirmations.pop(call_id, None)

    _audit_tool_confirm(
        session=session,
        call_id=call_id,
        tool_name=tool_name,
        decision=decision,
        idempotency_key=bound_idem or idempotency_key,
    )

    if decision == "reject":
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.rejected",
                "call_id": call_id,
                "name": tool_name,
            },
        )
        return

    # Approve path — re-authorize at submit time through the MCP policy
    # boundary (role, consent, critical tier, confirmed-flag, idempotency)
    # and only then execute with submit=True.  Calling ToolRegistry directly
    # here would skip authorization entirely, letting a confirmation token —
    # which carries no role/consent — drive a privileged write (P0-1).
    try:
        from .mcp import get_client

        args = dict(proposal)
        args.pop("requires_confirmation", None)
        args["submit"] = True
        if bound_idem:
            args["idempotency_key"] = bound_idem
        call_result = get_client().call_tool(
            tool_name,
            args,
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            user_role=session.user_role,
            granted_purposes=session.granted_purposes,
            confirmed=True,
            idempotency_key=bound_idem,
        )
    except Exception as exc:
        logger.exception("tool confirmation dispatch failed")
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.confirm_failed",
                "call_id": call_id,
                "reason": f"tool dispatch error: {exc}",
            },
        )
        return

    result = (
        call_result.result
        if isinstance(call_result.result, dict)
        else {"result": call_result.result}
    )
    # Fail closed when the policy boundary rejected the submit (insufficient
    # role/consent, missing confirmation, etc.) — do NOT forward as confirmed.
    if not call_result.ok and result.get("error") == "policy_denied":
        reasons = "; ".join((result.get("policy") or {}).get("reasons", []))
        await _send_json(
            websocket,
            {
                "type": "response.tool_call.confirm_failed",
                "call_id": call_id,
                "name": tool_name,
                "reason": f"authorization denied at submit: {reasons or 'policy denied'}",
            },
        )
        return

    await _send_json(
        websocket,
        {
            "type": "response.tool_call.confirmed",
            "call_id": call_id,
            "name": tool_name,
            "result": result,
        },
    )


def _audit_tool_confirm(
    *,
    session: WsChatSession,
    call_id: str,
    tool_name: str,
    decision: str,
    idempotency_key: str,
) -> None:
    """Append an audit event for the HITL decision (when audit_ledger is on)."""
    if not flags.is_enabled("audit_ledger"):
        return
    try:
        import hashlib

        from .audit import get_ledger

        ledger = get_ledger()
        ledger.append(
            event_type="tool_confirm",
            payload={
                "session_id": session.session_id,
                "conversation_id": session.conversation_id,
                "user_id": session.user_id or "",
                "tenant_id": session.tenant_id,
                "call_id": call_id,
                "tool_name": tool_name,
                "decision": decision,
                "idempotency_key_sha256": hashlib.sha256(
                    (idempotency_key or "").encode("utf-8")
                ).hexdigest(),
            },
        )
    except Exception:
        logger.warning("audit append for tool_confirm failed", exc_info=True)


def _log_ws_turn(
    *,
    session_id: str,
    conversation_id: str,
    user_message: str,
    full_reply: str,
    log_payload: dict[str, Any],
    user_id: str = "",
) -> None:
    """Persist a chat-WS turn to the analytics DB (mirrors SSE behaviour)."""
    from .service import ChatModel as _CM

    result = log_payload.get("result") or {}
    elapsed_ms = log_payload.get("elapsed_ms", 0.0)
    try:
        db.log_conversation(
            session_id=session_id or None,
            conversation_id=result.get("conversation_id") or conversation_id or None,
            user_message=_CM.redact_for_storage(user_message),
            bot_reply=_CM.redact_for_storage(full_reply),
            sources=json.dumps(result.get("sources", []) if result else []),
            contexts=_CM.contexts_json(result),
            response_time_ms=round(elapsed_ms, 2),
            user_id=user_id,
            **flags.experiment_log_fields(
                subject=user_id or None,
                locale=str(result.get("locale") or ""),
            ),
        )
    except Exception:
        logger.warning("chat WS conversation logging failed", exc_info=True)


async def chat_stream_ws(websocket: WebSocket, app: object) -> None:
    """V2 WebSocket handler for ``/v2/chat/stream``."""
    _ensure_metrics()

    if not flags.is_enabled("ws_chat"):
        await websocket.close(code=1001, reason="ws_chat flag is disabled")
        return

    chat_model = getattr(getattr(app, "state", None), "model", None)
    if chat_model is None:
        await websocket.close(code=1011, reason="chat model not available")
        return

    try:
        user_id, tenant_id, user_role, granted_purposes = _resolve_ws_principal(websocket)
    except JWTAuthError as exc:
        await websocket.close(code=1008, reason=f"authentication failed: {exc}")
        return

    from .ws_concurrency import is_ws_origin_allowed

    origin = websocket.headers.get("origin")
    if not is_ws_origin_allowed(origin):
        await websocket.close(code=1008, reason="forbidden origin")
        return

    socket_user_key = user_id or f"anon::{websocket.client.host if websocket.client else 'unknown'}"
    if not _try_acquire_socket_slot(socket_user_key):
        await websocket.close(
            code=1013,  # Try Again Later
            reason=f"max concurrent sockets reached for {socket_user_key}",
        )
        return

    await websocket.accept()
    _metric("_ws_chat_connections_total")
    _metric("_ws_chat_active")
    session_start_time = time.perf_counter()
    session_id = ""

    try:
        # ── session_start config frame ──────────────────────────────
        config_raw = await websocket.receive_text()
        if len(config_raw) > _MAX_TEXT_FRAME_BYTES:
            await _send_error(websocket, "session_start frame too large", recoverable=False)
            return
        try:
            config = json.loads(config_raw)
        except json.JSONDecodeError:
            await _send_error(websocket, "Invalid JSON in session_start frame", recoverable=False)
            return

        if config.get("type") != "session_start":
            await _send_error(websocket, "First message must be session_start", recoverable=False)
            return

        locale = config.get("locale", "en")
        if not isinstance(locale, str) or len(locale) > 8:
            locale = "en"
        conversation_id = config.get("conversation_id") or ""
        previous_response_id = config.get("previous_response_id") or ""
        protocol_version = int(config.get("protocol_version", 1) or 1)

        session_user_id = user_id
        session_tenant_id = tenant_id if user_id else str(config.get("tenant_id", "default"))
        session_id = str(uuid.uuid4())

        session = WsChatSession(
            session_id=session_id,
            conversation_id=conversation_id or "",
            user_id=session_user_id or "",
            tenant_id=session_tenant_id,
            locale=locale,
            user_role=user_role if session_user_id else "public",
            granted_purposes=granted_purposes if session_user_id else [],
        )

        # Phase 3: best-effort resume from a prior turn.
        if previous_response_id and conversation_id:
            session.try_resume(previous_response_id)

        await _send_json(
            websocket,
            {
                "type": "session_ready",
                "session_id": session_id,
                "protocol_version": protocol_version,
                "resume": session.resumed,
                "capabilities": {
                    "agentic_events": flags.is_enabled("tool_use"),  # Phase 2
                    "tool_confirmation": True,  # Phase 4
                    "speculative_prefetch": False,  # Phase 5
                    "prefix_cache": flags.is_enabled("prefix_caching"),  # Phase 3
                    "session_resume": True,
                },
            },
        )

        logger.info(
            "chat WS session started session=%s user=%s tenant=%s conv=%s prev_resp=%s resumed=%s",
            session_id,
            session_user_id or "anon",
            session_tenant_id,
            conversation_id or "-",
            previous_response_id or "-",
            session.resumed,
        )

        # ── main loop ────────────────────────────────────────────────
        while True:
            if time.perf_counter() - session_start_time > _SESSION_MAX_DURATION_S:
                await _send_json(
                    websocket,
                    {"type": "session.expired", "reason": "max_duration_exceeded"},
                )
                break

            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                raise
            if len(raw) > _MAX_TEXT_FRAME_BYTES:
                await _send_error(websocket, "Frame too large", recoverable=True)
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(websocket, "Invalid JSON", recoverable=True)
                continue

            msg_type = msg.get("type", "")

            if msg_type in ("ping", "pong"):
                await _send_json(websocket, {"type": "pong"})

            elif msg_type == "session_end":
                break

            elif msg_type == "response.create":
                await _run_response_create(
                    websocket,
                    chat_model=chat_model,
                    msg=msg,
                    session=session,
                )

            elif msg_type == "response.cancel":
                # In Phase 1 the WS handler runs the turn sequentially, so a
                # cancel arriving here is post-turn.  Ack the contract; Phase
                # 6 adds concurrent cancellation via a background watcher.
                session.cancel_event.set()
                await _send_json(websocket, {"type": "response.cancelled"})

            elif msg_type == "tool_call.confirm":
                await _handle_tool_call_confirm(
                    websocket,
                    msg=msg,
                    session=session,
                )

            elif msg_type == "response.create_partial":
                # Phase 5 scaffolding: signal that speculative prefetch
                # could begin.  Today this records the partial input but
                # does not yet run the warm retrieval — that's wired up
                # incrementally as we measure cache-hit potential.
                partial = str(msg.get("input") or "")[:512]
                session.last_partial_prefix = partial.strip().lower()
                _metric("_ws_chat_partial_received_total")
                await _send_json(
                    websocket,
                    {"type": "response.create_partial.ack", "length": len(partial)},
                )

            elif msg_type == "session.modify":
                # Phase 5 scaffolding for capability re-negotiation
                # (e.g. switching modalities).  Today we accept the
                # frame and echo current capabilities so clients can
                # already build against the contract.
                await _send_json(
                    websocket,
                    {
                        "type": "session.modified",
                        "session_id": session.session_id,
                        "capabilities": {
                            "agentic_events": flags.is_enabled("tool_use"),
                            "tool_confirmation": True,
                            "speculative_prefetch": False,
                            "prefix_cache": flags.is_enabled("prefix_caching"),
                            "session_resume": True,
                        },
                    },
                )

            else:
                await _send_error(
                    websocket,
                    f"Unknown message type: {msg_type}",
                    recoverable=True,
                )

    except WebSocketDisconnect:
        logger.info("chat WS disconnected session=%s", session_id or "?")
    except Exception:
        logger.exception("chat WS error")
        try:
            await _send_error(websocket, "Internal server error", recoverable=False)
        except Exception:
            pass
    finally:
        _release_socket_slot(socket_user_key)
        _metric_dec("_ws_chat_active")
        duration = time.perf_counter() - session_start_time
        _metric("_ws_chat_session_duration", duration)
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000)
        except Exception:
            pass
