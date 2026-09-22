"""WebSocket and HTTP handlers for the simulated AI phone receptionist."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .. import database as db
from ..auth import AuthContext, require_role
from ..chat_ws_v2 import _resolve_ws_principal

require_admin_access = require_role("ura_staff", "ura_admin", "ura_auditor")
from ..flags import flags
from ..voice_consent import log_voice_event, require_voice_consent
from ..ws_concurrency import is_ws_origin_allowed, release, try_acquire
from .brain import UraReceptionistBrain
from .config import get_max_call_s
from .hub import hub
from .metrics import get_aggregate_metrics, record_call_end_metrics
from .officer import OfficerLeg
from .pipeline import build_call_pipeline
from .state import registry
from .store import (
    create_call,
    get_call,
    get_call_with_turns,
    list_calls,
    save_call_review,
    update_call,
)
from .summary import generate_call_summary

logger = logging.getLogger(__name__)

router = APIRouter()


class ReviewCallRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    note: str = Field(default="", max_length=1000)


# ---------------------------------------------------------------------------
# 1. Taxpayer Call Stream WebSocket
# ---------------------------------------------------------------------------


async def call_stream_endpoint(websocket: WebSocket) -> None:
    """Taxpayer phone call simulation WebSocket (/v1/calls/stream)."""
    logger.info(
        "call_stream_endpoint: flag=%s, origin=%r, headers=%r",
        flags.is_enabled("voice_receptionist"),
        websocket.headers.get("origin"),
        dict(websocket.headers),
    )
    # 1. Flag check
    if not flags.is_enabled("voice_receptionist"):
        logger.warning("call_stream_endpoint: rejected due to flag voice_receptionist being off")
        await websocket.close(code=1001)
        return

    # 2. Origin check
    origin = websocket.headers.get("origin")
    if not is_ws_origin_allowed(origin):
        logger.warning("call_stream_endpoint: rejected due to origin not allowed: %r", origin)
        await websocket.close(code=4403)
        return

    # 3. Resolve principal
    auth_req = flags.is_enabled("auth_required")
    try:
        user_id, tenant_id, role, _ = _resolve_ws_principal(websocket, required=auth_req)
    except Exception as exc:
        logger.warning("call_stream_endpoint: rejected due to auth failed: %s", exc)
        await websocket.close(code=4401 if auth_req else 4403)
        return

    # 4. Connection caps
    client_host = websocket.client.host if websocket.client else "unknown"
    key = user_id or f"anon::{client_host}"
    if not try_acquire("call", key, per_user_cap=5, global_cap=16):
        await websocket.close(code=1013)
        return

    await websocket.accept()

    call_id = f"call_{uuid.uuid4().hex[:12]}"
    room = None
    try:
        # 5. Read first text frame: call_start
        try:
            raw_init = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
            init_msg = json.loads(raw_init)
        except Exception:
            await websocket.send_text(
                json.dumps({"type": "error", "detail": "Missing call_start handshake", "recoverable": False})
            )
            await websocket.close(code=1003)
            return

        locale = init_msg.get("locale", "en")
        consent_accepted = bool(init_msg.get("voice_consent_accepted"))

        # Consent check
        if flags.is_enabled("voice_consent"):
            if user_id:
                consent_ok = require_voice_consent(user_id)
            else:
                consent_ok = consent_accepted
            if not consent_ok:
                await websocket.send_text(
                    json.dumps({
                        "type": "error",
                        "detail": "Voice recording consent required before processing call.",
                        "recoverable": False,
                    })
                )
                await websocket.close(code=4403)
                return

        # 6. Create room & DB record
        room = await registry.create(
            call_id=call_id,
            conversation_id=f"conv_{call_id}",
            user_id=user_id or "",
            tenant_id=tenant_id or "default",
            locale=locale,
            caller_ws=websocket,
        )
        create_call(
            call_id=call_id,
            conversation_id=room.state.conversation_id,
            user_id=user_id or "",
            tenant_id=tenant_id or "default",
            channel="browser_sim",
            locale=locale,
            status="ai",
            started_at=room.state.started_at,
        )

        log_voice_event(user_id=user_id or "", session_id=call_id, event_type="call_started", tenant_id=tenant_id or "default")
        hub.publish_lobby(
            "call.started",
            {
                "call_id": call_id,
                "status": "ai",
                "started_at": room.state.started_at,
                "topic": "General Tax Support",
                "priority": "normal",
            },
        )

        # 7. Notify client ready
        await websocket.send_text(
            json.dumps({
                "type": "call_ready",
                "call_id": call_id,
                "assistant_name": "URA Virtual Assistant",
            })
        )

        # 8. Run pipeline if Pipecat is present, otherwise fallback loop
        try:
            task, transport, brain = build_call_pipeline(room, websocket)
            from pipecat.pipeline.runner import PipelineRunner
            runner = PipelineRunner()
            await brain.say_greeting()
            await runner.run(task)
        except (ImportError, RuntimeError) as pipeline_err:
            logger.warning("Pipeline fallback active: %s", pipeline_err)
            from ..main import app
            chat_model = getattr(getattr(app, "state", None), "model", None)
            brain = UraReceptionistBrain(room=room, chat_model=chat_model)
            await brain.say_greeting()

            while room.state.mode != "ended":
                message = await websocket.receive()
                if "bytes" in message and message["bytes"]:
                    pass  # Audio frames in fallback mode
                elif "text" in message and message["text"]:
                    data = json.loads(message["text"])
                    mtype = data.get("type")
                    if mtype == "hangup":
                        await room.end("caller_hangup")
                        break
                    if mtype == "request_officer":
                        await brain._transfer("caller_requested")

    except WebSocketDisconnect:
        logger.info("Caller disconnected: %s", call_id)
    except Exception:
        logger.exception("Error in call stream: %s", call_id)
    finally:
        if room:
            await room.end("caller_hangup")
            record_call_end_metrics(call_id, room.state)
            asyncio.create_task(asyncio.to_thread(generate_call_summary, call_id))
            log_voice_event(user_id=user_id or "", session_id=call_id, event_type="call_ended", tenant_id=tenant_id or "default")
            hub.publish_lobby(
                "call.ended",
                {
                    "call_id": call_id,
                    "status": "ended",
                    "reason": room.state.end_reason or "caller_hangup",
                },
            )
            await registry.remove(call_id)
        release("call", key)


# ---------------------------------------------------------------------------
# 2. Staff Calls Stream (Lobby & Live Transcript)
# ---------------------------------------------------------------------------


async def staff_calls_stream_endpoint(
    websocket: WebSocket,
    call_id: str | None = Query(None),
) -> None:
    """Staff WebSocket (/v1/admin/calls/stream): lobby or per-call live mode."""
    try:
        user_id, tenant_id, role, _ = _resolve_ws_principal(websocket, required=True)
    except Exception:
        await websocket.close(code=4401)
        return

    if role not in ("ura_staff", "ura_admin", "ura_auditor"):
        await websocket.close(code=4403)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()

    queue: asyncio.Queue[dict[str, Any]]
    if call_id:
        # Per-call Live mode
        log_voice_event(user_id=user_id or "", session_id=call_id, event_type="staff_viewed_call", tenant_id=tenant_id or "default")
        snapshot = get_call_with_turns(call_id) or {}
        await websocket.send_text(
            json.dumps({
                "type": "snapshot",
                "call": snapshot,
                "turns": snapshot.get("turns", []),
            })
        )
        queue = hub.subscribe_call(call_id, loop)
    else:
        # Lobby mode (metadata only)
        queue = hub.subscribe_lobby(loop)

    try:
        while True:
            # Wait for event from hub or ping timeout (25s)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25.0)
                await websocket.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping", "ts": time.time()}))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Staff call stream closed", exc_info=True)
    finally:
        if call_id:
            hub.unsubscribe_call(call_id, queue)
        else:
            hub.unsubscribe_lobby(queue)


# ---------------------------------------------------------------------------
# 3. Officer Audio Bridge WebSocket
# ---------------------------------------------------------------------------


async def officer_audio_endpoint(websocket: WebSocket, call_id: str) -> None:
    """Officer live audio bridge socket (/v1/admin/calls/{call_id}/audio)."""
    try:
        user_id, tenant_id, role, _ = _resolve_ws_principal(websocket, required=True)
    except Exception:
        await websocket.close(code=4401)
        return

    # Auditors are refused on audio bridge (view only)
    if role not in ("ura_staff", "ura_admin"):
        await websocket.close(code=4403)
        return

    room = registry.get(call_id)
    if not room or room.state.mode not in ("transferring", "ai"):
        await websocket.close(code=4404 if not room else 4400)
        return

    if room.officer is not None:
        await websocket.close(code=4409)
        return

    await websocket.accept()

    officer_handle = user_id.split("@")[0].capitalize()
    officer_name = f"Officer {officer_handle}"
    speech_model = None
    try:
        from ..main import app
        speech_model = getattr(getattr(app, "state", None), "speech", None)
    except Exception:
        pass

    officer_leg = OfficerLeg(
        ws=websocket,
        officer_id=user_id,
        officer_name=officer_name,
        room=room,
        speech_model=speech_model,
    )

    room.officer = officer_leg
    room.state.mode = "bridged"
    room.state.officer_id = user_id
    room.state.officer_name = officer_name

    update_call(call_id, status="bridged", officer_id=user_id)
    if room.state.ticket_id:
        try:
            db.update_ticket(room.state.ticket_id, status="assigned", assignee=user_id)
        except Exception:
            pass

    # Send caller bridged status (plays join chime)
    status_event = {
        "type": "status",
        "status": "bridged",
        "officer_name": officer_name,
    }
    if room.caller_ws:
        try:
            await room.caller_ws.send_text(json.dumps(status_event))
        except Exception:
            pass

    hub.publish_lobby("call.bridged", {"call_id": call_id, "status": "bridged", "officer_name": officer_name})
    hub.publish_call(call_id, "status", status_event)
    log_voice_event(user_id=user_id or "", session_id=call_id, event_type="officer_joined", tenant_id=tenant_id or "default")

    officer_leg.start()

    try:
        while room.state.mode == "bridged":
            msg = await websocket.receive()
            if "bytes" in msg and msg["bytes"]:
                await officer_leg.handle_officer_message(msg["bytes"])
            elif "text" in msg and msg["text"]:
                await officer_leg.handle_officer_message(msg["text"])
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Officer audio connection closed", exc_info=True)
    finally:
        await officer_leg.close()
        room.officer = None
        if room.state.mode == "bridged":
            await room.end("officer_hangup")


# ---------------------------------------------------------------------------
# 4. HTTP API Endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/calls")
def list_calls_endpoint(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict[str, Any]:
    """List phone calls filtered by status (live, ended, all)."""
    calls = list_calls(status=status, limit=limit, offset=offset)
    return {
        "calls": calls,
        "count": len(calls),
        "status_filter": status or "all",
        "limit": limit,
        "offset": offset,
    }


@router.get("/admin/calls/metrics")
def get_call_metrics_endpoint(
    days: int = 7,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict[str, Any]:
    """Retrieve aggregate phone receptionist performance metrics."""
    return get_aggregate_metrics(days=days)


@router.get("/admin/calls/{call_id}")
def get_call_detail_endpoint(
    call_id: str,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict[str, Any]:
    """Retrieve full call detail including turns, summary, metrics, and ticket."""
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", call_id):
        raise HTTPException(status_code=400, detail="Invalid call_id format")

    detail = get_call_with_turns(call_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Call not found")
    return detail


@router.post("/admin/calls/{call_id}/review")
def review_call_endpoint(
    call_id: str,
    body: ReviewCallRequest,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict[str, Any]:
    """Submit an officer rating (1-5) and note for a call."""
    if ctx.role not in ("ura_staff", "ura_admin"):
        raise HTTPException(status_code=403, detail="Only staff and admins can submit reviews")

    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", call_id):
        raise HTTPException(status_code=400, detail="Invalid call_id format")

    call = get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    ok = save_call_review(call_id, rating=body.rating, note=body.note)
    return {"ok": ok, "call_id": call_id, "rating": body.rating}
