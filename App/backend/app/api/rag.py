"""RAG APIs: chat over REST, SSE and WebSocket, conversation titles, and the FAQ catalogue."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sse_starlette.sse import EventSourceResponse
from starlette.websockets import WebSocket

from .. import documents, officer_hours
from ..analytics import metrics, record_chat_turn
from ..auth import AuthContext, optional_user
from ..db import database as db
from ..middleware.rate_limiter import RATE_LIMIT, limiter
from ..rag.pipeline import ChatModel
from ..rag.query import gate_locale
from .deps import experiment_log_fields, get_model
from .models import ChatRequest, ChatResponse, ChatTitleRequest, ChatTitleResponse, FAQResponse, TagListResponse

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Chat endpoint (with conversation logging)
# ---------------------------------------------------------------------------
def _resolve_chat_connectors(body: ChatRequest, ctx: AuthContext) -> tuple[list[str], list[dict[str, str]]]:
    """The selected services this turn may use, and a notice for each one it may not.

    Nothing outside the caller's current catalog is ever dispatched (fail
    closed), but an unusable selection no longer fails the turn: a service
    that stops answering, or that this account may no longer use, is left
    out and named in ``connector_notices`` so the client can say so and
    update the chip. It used to answer 403/409, which the client's retry
    met again, and every later message read "could not reach the URA
    knowledge base" until the taxpayer found the chip and removed it.
    """
    if not body.connector_namespaces:
        return [], []
    from ..mcp import get_client
    from ..mcp.chat_connectors import resolve_chat_connector_selection

    selection = resolve_chat_connector_selection(
        get_client(),
        body.connector_namespaces,
        user_role=ctx.role,
        granted_purposes=ctx.user.granted_purposes if ctx.user else [],
    )
    for skipped in selection.skipped:
        metrics.inc("chat_connector_skipped_total", labels={"reason": skipped["reason"]})
    return selection.usable, selection.skipped


@router.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
@limiter.limit(RATE_LIMIT)
def chat(
    body: ChatRequest,
    request: Request,
    model: ChatModel = Depends(get_model),
    ctx: AuthContext = Depends(optional_user),
) -> ChatResponse:
    connector_namespaces, connector_notices = _resolve_chat_connectors(body, ctx)
    session_id = request.headers.get("X-Session-ID") or body.conversation_id or ""
    request_id = getattr(request.state, "request_id", None)
    # The answer's id in the conversation log, returned with it so a rating
    # names the row it rates (see FeedbackRequest.turn_id).
    turn_id = uuid.uuid4().hex
    t0 = time.perf_counter()

    attachments = documents.resolve_attachments(
        body.attachment_ids, session_id=session_id, user_id=ctx.user_id
    )
    result = model.generate(
        message=body.message,
        conversation_id=body.conversation_id,
        top_k=body.top_k,
        locale=body.locale or "",
        locale_explicit=body.locale_explicit,
        session_id=session_id or None,
        request_id=request_id,
        user_id=ctx.user_id or None,
        tenant_id=ctx.tenant_id,
        user_role=ctx.role,
        granted_purposes=ctx.user.granted_purposes if ctx.user else [],
        attachments=attachments or None,
        connector_namespaces=connector_namespaces,
        portal_context=body.portal_context.model_dump() if body.portal_context else None,
        portal_task_submission=body.portal_task_submission,
        channel="rest",
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    record_chat_turn(result, elapsed_ms=elapsed_ms, channel="rest")

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
        from ..rag.pipeline import ChatModel as _CM

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
            tenant_id=ctx.tenant_id or "default",
            row_id=turn_id,
            **experiment_log_fields(ctx.user_id or "", result.get("locale") or body.locale or "en"),
            **_CM.english_forms(result, served_reply=str(result.get("reply") or "")),
        )
    except Exception:
        logger.warning("Conversation logging failed", exc_info=True)

    # Durable escalation event for consented users (the counter is in record_chat_turn)
    if result.get("escalation_required"):
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

    # With a ticket, whether officers are on duty now and when they are next (G196).
    # A fresh dict: the result may be the answer cache's own.
    served = officer_hours.attach_status({**result, "turn_id": turn_id, "connector_notices": connector_notices})
    return ChatResponse(**served)


# ---------------------------------------------------------------------------
# SSE streaming chat endpoint (Phase 3)
# ---------------------------------------------------------------------------
@router.post("/v1/chat/stream", tags=["chat"])
@limiter.limit(RATE_LIMIT)
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
    from ..rag import pipeline as pipeline_module

    connector_namespaces, connector_notices = _resolve_chat_connectors(body, ctx)

    session_id = request.headers.get("X-Session-ID") or body.conversation_id or ""
    request_id = getattr(request.state, "request_id", None)
    attachments = documents.resolve_attachments(
        body.attachment_ids, session_id=session_id, user_id=ctx.user_id
    )
    # Minted before generation: it goes out in the ``metadata`` frame, long
    # before the turn is logged, so a rating can name the row it rates.
    turn_id = uuid.uuid4().hex

    async def event_generator():
        # Phase 2: SSE buffers agentic events into a compact ``agent_trace``
        # summary emitted just before ``grounding``.  Live tool-call frames
        # are exclusive to the WS path.
        from ..guardrails import OutputGuard, StreamingDLPFilter, user_stated_identifiers

        agent_trace: list[dict[str, Any]] = []
        stated_ids = user_stated_identifiers(body.message)
        dlp_filter = StreamingDLPFilter(allowed_values=stated_ids)
        async for event_type, payload in pipeline_module.run_chat_turn(
            model,
            message=body.message,
            conversation_id=body.conversation_id,
            top_k=body.top_k,
            locale=body.locale or "",
            locale_explicit=body.locale_explicit,
            session_id=session_id or None,
            request_id=request_id,
            user_id=ctx.user_id or None,
            tenant_id=ctx.tenant_id,
            channel="sse",
            should_continue=lambda: _sse_not_disconnected(request),
            sentence_batching=True,  # SSE keeps historical behaviour
            user_role=getattr(ctx, "role", "public"),
            granted_purposes=ctx.user.granted_purposes if ctx.user else [],
            attachments=attachments or None,
            connector_namespaces=connector_namespaces,
            portal_context=body.portal_context.model_dump() if body.portal_context else None,
            portal_task_submission=body.portal_task_submission,
        ):
            if event_type == "_keepalive":
                yield {
                    "comment": f"ping - {datetime.datetime.now(datetime.timezone.utc).isoformat()}"
                }
                continue
            if event_type == "_log":
                await asyncio.to_thread(
                    _log_stream_conversation,
                    body,
                    session_id,
                    payload,
                    user_id=ctx.user_id or "",
                    tenant_id=ctx.tenant_id,
                    turn_id=turn_id,
                )
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
                safe_trace_fields = {
                    "name",
                    "call_id",
                    "iteration",
                    "iterations",
                    "truncated",
                    "tool_call_count",
                    "admission",
                    "ok",
                    "elapsed_ms",
                    # Which connector a tool call went to, whether it is
                    # a simulator, its display name and how the lookup
                    # ended: a namespace, a label and two enums, no payload.
                    "connector",
                    "connector_mode",
                    "connector_label",
                    "connector_outcome",
                }
                agent_trace.append(
                    {
                        "type": event_type,
                        **{
                            key: value
                            for key, value in event_dict.items()
                            if key in safe_trace_fields
                        },
                    }
                )
                continue
            if event_type == "grounding" and agent_trace:
                yield {"event": "agent_trace", "data": json.dumps(agent_trace)}
                agent_trace = []
            if event_type == "metadata":
                frame = {**payload, "turn_id": turn_id}
                if connector_notices:
                    frame["connector_notices"] = connector_notices
                yield {"event": event_type, "data": json.dumps(frame)}
            elif event_type == "grounding":
                yield {"event": event_type, "data": json.dumps(payload)}
            elif event_type == "error":
                yield {"event": "error", "data": payload.get("message", "Internal server error")}
            elif event_type == "token":
                chunk = payload if isinstance(payload, str) else ""
                sanitized_chunk = dlp_filter.process_chunk(chunk)
                if sanitized_chunk:
                    yield {"event": "token", "data": sanitized_chunk}
            elif event_type == "done":
                trailing = dlp_filter.flush()
                if trailing:
                    yield {"event": "token", "data": trailing}
                yield {"event": "done", "data": payload if isinstance(payload, str) else ""}
            elif event_type == "revision":
                dlp_filter.flush()
                sanitized_rev = OutputGuard.redact_pii(payload if isinstance(payload, str) else "", allowed_values=stated_ids)
                yield {"event": "revision", "data": sanitized_rev}
            else:
                yield {"event": event_type, "data": payload if isinstance(payload, str) else ""}

        # Flush trailing trace (e.g. if grounding was skipped).
        if agent_trace:
            yield {"event": "agent_trace", "data": json.dumps(agent_trace)}

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse_not_disconnected(request: Request) -> bool:
    """Adapter for ``run_chat_turn.should_continue`` over Starlette HTTP."""
    return not (await request.is_disconnected())


def _log_stream_conversation(
    body: ChatRequest,
    session_id: str,
    log_payload: dict[str, Any],
    user_id: str = "",
    tenant_id: str = "default",
    turn_id: str | None = None,
) -> None:
    """Mirror the old SSE ``finally`` block — log to analytics DB.

    Turn metrics and the audit row are recorded by ``service.run_chat_turn``
    itself, for every streaming transport alike.
    """
    from ..rag.pipeline import ChatModel as _CM

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
            tenant_id=tenant_id or "default",
            row_id=turn_id,
            **experiment_log_fields(user_id, result.get("locale") or body.locale or "en"),
            **_CM.english_forms(
                result,
                served_reply=full_reply,
                english_reply=str(log_payload.get("english_reply") or ""),
            ),
        )
    except Exception:
        logger.warning("Stream conversation logging failed", exc_info=True)


@router.post("/v1/chat/title", response_model=ChatTitleResponse, tags=["chat"])
@limiter.limit(RATE_LIMIT)
async def chat_title(
    request: Request,
    body: ChatTitleRequest,
    _ctx: AuthContext = Depends(optional_user),
) -> ChatTitleResponse:
    """A few-word summary title for a conversation, for the chat sidebar.

    The client asks once per conversation, after the first answer lands — the
    way ChatGPT, Claude and Gemini name a chat. A null title means no usable
    one came back; the client keeps the provisional title it already shows.
    """
    from ..chat_title import generate_title
    from ..guard_friction import principal_key

    user_text = next(message.content for message in body.messages if message.role == "user")
    assistant_text = next((message.content for message in body.messages if message.role == "assistant"), "")
    # No session or conversation reaches this route, so an anonymous caller is
    # keyed on the client address for strike friction (G464), hashed and in memory only.
    principal = principal_key(
        user_id=_ctx.user_id if _ctx.authenticated else None,
        tenant_id=_ctx.tenant_id,
        client_address=request.client.host if request.client else None,
    )
    title, source = await asyncio.to_thread(
        generate_title, user_text, assistant_text, gate_locale(body.locale), principal=principal
    )
    metrics.inc("chat_title_total", labels={"source": source})
    return ChatTitleResponse(title=title, source=source)


@router.websocket("/v2/chat/stream")
async def chat_stream_ws_v2(websocket: WebSocket) -> None:
    """V2 WebSocket — persistent text chat with agentic event surface.

    Gated by the ``ws_chat`` feature flag.  See ``chat_ws_v2.py`` and
    ``docs/ws_chat_protocol.md``.  Phase 0 ships lifecycle + protocol
    negotiation only; ``response.create`` returns a ``not_implemented``
    error until Phase 1 wires the existing chat pipeline through.
    """
    from ..chat_ws_v2 import chat_stream_ws

    await chat_stream_ws(websocket, websocket.app)


# ---------------------------------------------------------------------------
# Knowledge base endpoints
# ---------------------------------------------------------------------------
@router.get("/tags", response_model=TagListResponse, tags=["knowledge"])
def list_tags(model: ChatModel = Depends(get_model)) -> TagListResponse:
    result = model.list_tags()
    return TagListResponse(**result)


@router.get("/faq/{tag}", response_model=FAQResponse, tags=["knowledge"])
def get_faq(
    tag: str = Path(..., pattern=r"^[a-z][a-z0-9_]{0,63}$"),
    model: ChatModel = Depends(get_model),
) -> FAQResponse:
    result = model.get_faq(tag=tag)
    if result is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return FAQResponse(**result)
