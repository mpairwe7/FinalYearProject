"""WebSocket and HTTP handlers for the simulated AI phone receptionist."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..auth import AuthContext, require_role
from ..chat_ws_v2 import _resolve_ws_principal
from ..flags import flags
from ..query import SUPPORTED_LOCALES
from ..tenancy import tenant_enabled
from ..voice_consent import log_voice_event, require_voice_consent
from ..ws_concurrency import is_ws_origin_allowed, rekey_slot, release, try_acquire
from . import brief as call_brief
from . import desk, livekit, risk
from .brain import UraReceptionistBrain
from .config import (
    get_default_language,
    get_gemini_session_timeout_s,
    get_languages,
    get_max_call_s,
)
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

require_admin_access = require_role("ura_staff", "ura_admin", "ura_auditor")
logger = logging.getLogger(__name__)


def _voice_receptionist_enabled() -> bool:
    """Fail closed unless the configured production media path is ready."""
    if not flags.is_enabled("voice_receptionist"):
        return False
    if os.getenv("APP_ENV", "development").strip().lower() == "production":
        return not livekit.production_errors(enabled_override=True)
    return True


def _tenant_scope(ctx: AuthContext) -> str | None:
    """The tenant a staff request is limited to, or ``None`` when tenancy is off."""
    return (ctx.tenant_id or "default") if tenant_enabled() else None


def _control_message(raw: str) -> dict[str, Any] | None:
    """Parse a control-socket frame; anything but a JSON object is ignored.

    A stray or malformed frame is not a reason to end a live call.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


#: How long teardown waits for the Pipecat runner before cancelling it outright.
_PIPELINE_STOP_TIMEOUT_S = 5.0


async def _stop_pipeline(room: Any, task: Any) -> None:
    """Cancel the call's Pipecat task and wait briefly for its runner to finish."""
    runner = room.pipeline_runner_task
    if runner is None:
        return
    if not runner.done():
        try:
            await task.cancel()
        except Exception:
            logger.debug("Pipeline task for %s did not cancel cleanly", room.call_id, exc_info=True)
    # Shielded so a slow runner is cancelled without this coroutine waiting on
    # the cancellation; our own cancellation still propagates.
    try:
        await asyncio.wait_for(asyncio.shield(runner), timeout=_PIPELINE_STOP_TIMEOUT_S)
    except asyncio.TimeoutError:
        runner.cancel()
    except asyncio.CancelledError:
        runner.cancel()
        raise
    except Exception:
        logger.debug("Pipeline runner for %s ended with an error", room.call_id, exc_info=True)


async def _refuse_officer(websocket: WebSocket, detail: str) -> None:
    """Tell an officer's console why joining stopped, then close with 4409."""
    try:
        await websocket.send_text(json.dumps({"type": "error", "detail": detail}))
        await websocket.close(code=4409)
    except Exception:
        logger.debug("Officer socket was already closed", exc_info=True)

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
        "call_stream_endpoint: flag=%s",
        _voice_receptionist_enabled(),
    )
    # 1. Flag check
    if not _voice_receptionist_enabled():
        logger.warning("call_stream_endpoint: rejected due to flag voice_receptionist being off")
        await websocket.close(code=1001)
        return

    # 2. Origin check
    origin = websocket.headers.get("origin")
    if not is_ws_origin_allowed(origin):
        logger.warning("call_stream_endpoint: rejected due to origin not allowed: %r", origin)
        await websocket.close(code=4403)
        return

    # 3. Rate-limit before accepting, then authenticate from the first
    # application message so bearer tokens never appear in the WebSocket URL.
    auth_req = flags.is_enabled("auth_required")
    client_host = websocket.client.host if websocket.client else "unknown"
    key = f"anon::{client_host}"
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
            if not isinstance(init_msg, dict) or init_msg.get("type") != "call_start":
                raise ValueError("invalid call_start message")
        except Exception:
            await websocket.send_text(
                json.dumps({"type": "error", "detail": "Missing call_start handshake", "recoverable": False})
            )
            await websocket.close(code=1003)
            return

        try:
            user_id, tenant_id, role, _ = _resolve_ws_principal(
                websocket,
                required=auth_req,
                token_override=str(init_msg.get("access_token") or ""),
            )
        except Exception:
            await websocket.send_text(json.dumps({"type": "error", "detail": "Authentication required", "recoverable": False}))
            await websocket.close(code=4401)
            return

        if user_id:
            if not rekey_slot("call", key, user_id, per_user_cap=5):
                await websocket.close(code=1013)
                return
            key = user_id

        # Both go into the call record and the staff UI: keep them to locales
        # the app knows. (A multilingual call then opens in its default
        # language regardless — see multilingual.py.)
        locale = str(init_msg.get("locale") or "en").strip().lower()
        if locale not in SUPPORTED_LOCALES:
            locale = "en"
        preferred_locale = str(init_msg.get("preferred_locale") or locale).strip().lower()
        if preferred_locale not in SUPPORTED_LOCALES:
            preferred_locale = locale
        consent_accepted = init_msg.get("voice_consent_accepted") is True

        # Consent check
        if flags.is_enabled("voice_consent"):
            if user_id:
                consent_ok = require_voice_consent(user_id, tenant_id=tenant_id or "default")
            else:
                consent_ok = consent_accepted
            if not consent_ok:
                log_voice_event(
                    user_id=user_id or "", session_id=call_id, event_type="consent_denied",
                    metadata={"purpose": "voice_recording"}, tenant_id=tenant_id or "default",
                )
                await websocket.send_text(
                    json.dumps({
                        "type": "error",
                        "detail": "Voice recording consent required before processing call.",
                        "recoverable": False,
                    })
                )
                await websocket.close(code=4403)
                return

        log_voice_event(
            user_id=user_id or "", session_id=call_id, event_type="consent_checked",
            metadata={
                "purpose": "voice_recording",
                "granted": consent_accepted or bool(user_id and flags.is_enabled("voice_consent")),
                "enforced": flags.is_enabled("voice_consent"),
                "source": "receipt" if user_id else "call_start_acknowledgement",
                "version": "1.0",
            },
            tenant_id=tenant_id or "default",
        )

        # 6. Create room & DB record
        room = await registry.create(
            call_id=call_id,
            conversation_id=f"conv_{call_id}",
            user_id=user_id or "",
            tenant_id=tenant_id or "default",
            locale=locale,
            caller_ws=websocket,
        )
        room.state.preferred_locale = preferred_locale
        caller_token = ""
        if livekit.enabled():
            room.state.livekit_room = livekit.room_name(call_id)
            room.state.livekit_caller_identity = livekit.participant_identity("caller", call_id)
            caller_token = livekit.mint_token(
                room_name=room.state.livekit_room,
                identity=room.state.livekit_caller_identity,
                name="URA taxpayer",
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
        call_brief.start(call_id)
        risk.start(call_id)
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

        # 7. Notify client ready. `language_detection` tells the call screen to
        # show the language chip: without the flag the call has one language.
        language_detection = flags.is_enabled("receptionist_language_detection")
        await websocket.send_text(
            json.dumps({
                "type": "call_ready",
                "call_id": call_id,
                "assistant_name": "URA Virtual Assistant",
                "language_detection": language_detection,
                "languages": list(get_languages()) if language_detection else [locale],
                "language": get_default_language() if language_detection else locale,
                **({
                    "media": {
                        "transport": "livekit",
                        "url": os.environ["LIVEKIT_URL"].strip(),
                        "room": room.state.livekit_room,
                        "identity": room.state.livekit_caller_identity,
                        "token": caller_token,
                    }
                } if caller_token else {}),
            })
        )

        # 8. Run pipeline if Pipecat is present, otherwise fallback loop
        try:
            task, transport, brain = build_call_pipeline(room, websocket)
            from pipecat.pipeline.runner import PipelineRunner

            # Pipecat only *reports* a dropped socket; stopping the pipeline is
            # the app's job. Without this a hung-up call ran on — Gemini
            # session, per-caller slot and all — until the configured transport
            # session timeout, and a later caller could be refused by the cap.
            room.transport = transport
            room.pipeline_task = task
            if not caller_token:
                @transport.event_handler("on_client_disconnected")
                async def _on_client_disconnected(_transport: Any, _ws: Any) -> None:
                    await task.cancel()

                @transport.event_handler("on_session_timeout")
                async def _on_session_timeout(_transport: Any, _ws: Any) -> None:
                    await room.end("timeout")
                    await task.cancel()

            runner = PipelineRunner()
            if caller_token:
                greeting_sent = False

                @transport.event_handler("on_participant_connected")
                async def _on_participant_connected(_transport: Any, identity: str) -> None:
                    nonlocal greeting_sent
                    if (
                        room.state.mode == "ended"
                        or not livekit.is_authorized_participant(room.state, identity)
                    ):
                        logger.warning(
                            "Refused a stale or unissued LiveKit participant (ref %s) in call %s",
                            livekit.log_ref(identity),
                            call_id,
                        )
                        try:
                            await livekit.revoke_participant(room.state.livekit_room, identity)
                        except Exception:
                            logger.exception(
                                "Could not revoke unissued LiveKit participant in call %s",
                                call_id,
                            )
                        return
                    if identity == room.state.livekit_caller_identity:
                        reconnect_task = room.caller_reconnect_task
                        room.caller_reconnect_task = None
                        if reconnect_task and not reconnect_task.done():
                            reconnect_task.cancel()
                        if reconnect_task:
                            try:
                                await websocket.send_text(json.dumps({
                                    "type": "status",
                                    "status": room.state.mode,
                                    "officer_name": room.state.officer_name or "",
                                }))
                            except Exception:
                                logger.debug("Caller control socket missed media reconnect status", exc_info=True)
                    room.mark_participant_joined(identity)
                    if identity == room.state.livekit_caller_identity and not greeting_sent:
                        greeting_sent = True
                        await brain.say_greeting()
                    if (
                        identity == room.state.livekit_officer_identity
                        and room.state.reconnecting_officer
                        and room.state.mode == "transferring"
                    ):
                        speech_model = None
                        try:
                            from ..main import app
                            speech_model = getattr(getattr(app, "state", None), "speech", None)
                        except Exception:
                            pass
                        try:
                            await desk.bridge(room, None, room.state.reconnecting_officer, speech_model)
                        except desk.DeskError as exc:
                            logger.info("Officer media rejoined call %s too late: %s", call_id, exc.detail)

                @transport.event_handler("on_first_participant_joined")
                async def _on_first_participant_joined(_transport: Any, identity: str) -> None:
                    nonlocal greeting_sent
                    if (
                        room.state.mode == "ended"
                        or not livekit.is_authorized_participant(room.state, identity)
                    ):
                        return
                    room.mark_participant_joined(identity)
                    if identity == room.state.livekit_caller_identity and not greeting_sent:
                        greeting_sent = True
                        await brain.say_greeting()

                @transport.event_handler("on_participant_disconnected")
                async def _on_participant_disconnected(_transport: Any, identity: str) -> None:
                    room.mark_participant_left(identity)
                    if identity == room.state.livekit_caller_identity:
                        try:
                            await websocket.send_text(json.dumps({"type": "status", "status": "reconnecting"}))
                        except Exception:
                            pass

                        async def _expire_caller_reconnect() -> None:
                            try:
                                await asyncio.sleep(20.0)
                            except asyncio.CancelledError:
                                return
                            if (
                                identity not in transport.get_participants()
                                and room.state.mode != "ended"
                            ):
                                await room.end("caller_media_disconnected")
                                await task.cancel()

                        room.caller_reconnect_task = asyncio.create_task(_expire_caller_reconnect())
                    elif identity == room.state.livekit_officer_identity and room.state.officer_id:
                        await desk.handle_officer_disconnect(room, room.state.officer_id, None)

                room.pipeline_runner_task = asyncio.create_task(runner.run(task))
                from .serializer import RequestOfficerFrame, SetLanguageFrame

                max_call_s = get_max_call_s()
                if room.state.engine == "gemini_live":
                    max_call_s = min(max_call_s, get_gemini_session_timeout_s())
                # A dropped control socket raises out of receive_text(), and
                # CallRoom.end() does not own the Pipecat runner: without this
                # finally the runner and the Gemini session outlived the call.
                try:
                    while room.state.mode != "ended":
                        if time.time() - room.state.started_at >= max_call_s:
                            await room.end("max_call_duration")
                            await task.cancel()
                            break
                        try:
                            raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                        except asyncio.TimeoutError:
                            if room.pipeline_runner_task.done():
                                await room.end("media_pipeline_stopped")
                                break
                            continue
                        data = _control_message(raw)
                        if data is None:
                            continue
                        message_type = data.get("type")
                        if message_type == "hangup":
                            await room.end("caller_hangup")
                            break
                        if message_type == "request_officer":
                            await task.queue_frame(RequestOfficerFrame(reason=str(data.get("reason") or "caller_requested")))
                        elif message_type == "set_language":
                            language = str(data.get("language") or "").strip().lower()
                            if language in ("en", "sw", "lg"):
                                await task.queue_frame(SetLanguageFrame(language=language))
                finally:
                    await _stop_pipeline(room, task)
            else:
                await brain.say_greeting()
                await runner.run(task)
        except (ImportError, RuntimeError) as pipeline_err:
            logger.warning("Pipeline fallback active: %s", pipeline_err)
            if caller_token:
                try:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "detail": "The production call media pipeline could not start.",
                        "recoverable": False,
                    }))
                except Exception:
                    pass
                await room.end("media_pipeline_unavailable")
                return
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
            call_brief.stop(call_id)
            risk.stop(call_id)
            abandoned_while_waiting = (room.state.mode == "transferring")
            if abandoned_while_waiting:
                update_call(call_id, needs_callback=1, callback_reason="caller_left_waiting", outcome="abandoned")
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
                    "abandoned_while_waiting": abandoned_while_waiting,
                },
            )
            await registry.remove(call_id)
        if key:
            release("call", key)


# ---------------------------------------------------------------------------
# 2. Staff Calls Stream (Lobby & Live Transcript)
# ---------------------------------------------------------------------------


async def staff_calls_stream_endpoint(
    websocket: WebSocket,
    call_id: str | None = Query(None),
) -> None:
    """Staff WebSocket (/v1/admin/calls/stream): lobby or per-call live mode."""
    # No phone calls on this deployment: 1001 tells the console to hide its
    # call layer for the session instead of retrying.
    if not _voice_receptionist_enabled():
        await websocket.close(code=1001)
        return
    if not is_ws_origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        auth_message = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=5.0))
        if not isinstance(auth_message, dict) or auth_message.get("type") != "authenticate":
            raise ValueError("missing WebSocket authentication message")
        user_id, tenant_id, role, _ = _resolve_ws_principal(
            websocket, required=True, token_override=str(auth_message.get("access_token") or "")
        )
    except Exception:
        await websocket.close(code=4401)
        return

    if role not in ("ura_staff", "ura_admin", "ura_auditor"):
        await websocket.close(code=4403)
        return
    loop = asyncio.get_running_loop()
    tenant_scope = (tenant_id or "default") if tenant_enabled() else None

    queue: asyncio.Queue[dict[str, Any]]
    if call_id:
        # Per-call Live mode
        snapshot = get_call_with_turns(call_id, tenant_id=tenant_scope)
        if snapshot is None:
            await websocket.close(code=4404)
            return
        log_voice_event(user_id=user_id or "", session_id=call_id, event_type="staff_viewed_call", tenant_id=tenant_id or "default")
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
        queue = hub.subscribe_lobby(loop, tenant_id=tenant_scope)

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
    if not _voice_receptionist_enabled():
        await websocket.close(code=1001)
        return
    if not is_ws_origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        auth_message = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=5.0))
        if not isinstance(auth_message, dict) or auth_message.get("type") != "authenticate":
            raise ValueError("missing WebSocket authentication message")
        user_id, tenant_id, role, _ = _resolve_ws_principal(
            websocket, required=True, token_override=str(auth_message.get("access_token") or "")
        )
    except Exception:
        await websocket.close(code=4401)
        return

    listen_mode = websocket.query_params.get("listen", "").lower() in ("true", "1")
    if listen_mode:
        if role not in ("ura_admin", "ura_auditor", "ura_staff"):
            await websocket.close(code=4403)
            return
        room = registry.get(call_id)
        if not room or room.state.mode != "bridged":
            await websocket.close(code=4404 if not room else 4400)
            return
        if tenant_enabled() and room.state.tenant_id != (tenant_id or "default"):
            await websocket.close(code=4404)
            return
        if livekit.enabled():
            observer_identity = livekit.participant_identity(
                "observer", f"{user_id}:{uuid.uuid4().hex}"
            )
            try:
                observer_token = livekit.mint_token(
                    room_name=room.state.livekit_room,
                    identity=observer_identity,
                    name=desk.officer_display_name(user_id),
                    can_publish=False,
                )
            except RuntimeError:
                await websocket.close(code=1011)
                return
            room.state.livekit_observer_identities.add(observer_identity)
            await websocket.send_text(json.dumps({
                "type": "livekit_ready",
                "url": os.environ["LIVEKIT_URL"].strip(),
                "room": room.state.livekit_room,
                "identity": observer_identity,
                "token": observer_token,
                "can_publish": False,
            }))
            try:
                while room.state.mode == "bridged":
                    try:
                        await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
            except Exception:
                pass
            finally:
                # Off the allowlist AND out of the room: the allowlist is only
                # checked when someone joins, so a listener whose console
                # closed would otherwise keep hearing the call.
                room.state.livekit_observer_identities.discard(observer_identity)
                await desk.revoke_media(room, observer_identity)
                try:
                    await websocket.close()
                except Exception:
                    pass
            return
        await websocket.send_text(json.dumps({"type": "authenticated"}))
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        room.add_listener(queue)
        try:
            while room.state.mode == "bridged":
                chunk = await queue.get()
                await websocket.send_bytes(chunk)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            room.remove_listener(queue)
            return

    # Auditors are refused on active audio bridge (view only)
    if role not in ("ura_staff", "ura_admin"):
        await websocket.close(code=4403)
        return

    room = registry.get(call_id)
    if not room or room.state.mode not in ("transferring", "ai"):
        await websocket.close(code=4404 if not room else 4400)
        return
    if tenant_enabled() and room.state.tenant_id != (tenant_id or "default"):
        await websocket.close(code=4404)
        return

    # Only the officer who claimed the call (POST …/claim), or the officer
    # reconnecting within grace period, may join it. Reserve one media-control
    # socket while holding the same lock used by claim/transfer/bridge.
    use_livekit = livekit.enabled()
    officer_identity = livekit.participant_identity("officer", user_id) if use_livekit else ""
    async with room.desk_lock:
        is_claimant = room.state.claimed_by == user_id
        is_reconnecting = room.state.reconnecting_officer == user_id
        if (
            room.state.mode not in ("transferring", "ai")
            or room.officer is not None
            or (not is_claimant and not is_reconnecting)
        ):
            await websocket.close(code=4409)
            return
        if use_livekit and room.state.livekit_officer_route_active:
            await websocket.close(code=4409)
            return
        if use_livekit and room.state.livekit_officer_identity not in ("", officer_identity):
            await websocket.close(code=4409)
            return
        if use_livekit:
            room.state.livekit_officer_route_active = True
            room.state.livekit_officer_identity = officer_identity

    if not use_livekit:
        await websocket.send_text(json.dumps({"type": "authenticated"}))
    officer_name = room.state.claimed_name or desk.officer_display_name(user_id)
    speech_model = None
    try:
        from ..main import app
        speech_model = getattr(getattr(app, "state", None), "speech", None)
    except Exception:
        pass

    if use_livekit:
        try:
            token = livekit.mint_token(
                room_name=room.state.livekit_room,
                identity=officer_identity,
                name=officer_name,
            )
        except RuntimeError:
            room.state.livekit_officer_route_active = False
            room.state.livekit_officer_identity = ""
            await websocket.close(code=1011)
            return
        try:
            await websocket.send_text(json.dumps({
                "type": "livekit_ready",
                "url": os.environ["LIVEKIT_URL"].strip(),
                "room": room.state.livekit_room,
                "identity": officer_identity,
                "token": token,
            }))
            joined = await room.wait_for_participant(officer_identity, timeout_s=20.0)
        except WebSocketDisconnect:
            room.state.livekit_officer_route_active = False
            if officer_identity not in room.participant_joined:
                room.state.livekit_officer_identity = ""
            return
        except Exception:
            room.state.livekit_officer_route_active = False
            if officer_identity not in room.participant_joined:
                room.state.livekit_officer_identity = ""
            return
        if not joined:
            room.state.livekit_officer_route_active = False
            room.state.livekit_officer_identity = ""
            await websocket.send_text(json.dumps({
                "type": "error",
                "detail": "Officer media did not join the call in time.",
            }))
            await websocket.close(code=4408)
            return

        # A reconnecting LiveKit Room normally restores the same participant
        # without opening a second control socket. The transport event handler
        # re-bridges it; a fresh claimant's first connection is bridged here.
        try:
            if room.state.mode != "bridged":
                try:
                    await desk.bridge(room, None, user_id, speech_model)
                except desk.DeskError as exc:
                    # Their media joined during the wait; it must not stay in
                    # a room whose call no longer waits for them.
                    async with room.desk_lock:
                        if room.state.livekit_officer_identity == officer_identity:
                            room.state.livekit_officer_identity = ""
                    await desk.revoke_media(room, officer_identity)
                    await _refuse_officer(websocket, exc.detail)
                    return
            log_voice_event(
                user_id=user_id or "",
                session_id=call_id,
                event_type="officer_joined",
                tenant_id=tenant_id or "default",
            )
            while room.state.mode == "bridged" or (
                room.state.mode == "transferring"
                and room.state.reconnecting_officer == user_id
            ):
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                data = _control_message(raw)
                if data is not None and data.get("type") == "hangup":
                    await desk.end(call_id, user_id, speech_model)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("Officer LiveKit control connection closed", exc_info=True)
        finally:
            room.state.livekit_officer_route_active = False
            if room.state.mode == "bridged":
                await desk.handle_officer_disconnect(room, user_id, speech_model)
            try:
                await websocket.close()
            except Exception:
                pass
        return

    officer_leg = OfficerLeg(
        ws=websocket,
        officer_id=user_id,
        officer_name=officer_name,
        room=room,
        speech_model=speech_model,
    )

    # The AI goes quiet, the caller hears who joined, then the bridge opens.
    try:
        await desk.bridge(room, officer_leg, user_id, speech_model)
    except desk.DeskError as exc:
        await _refuse_officer(websocket, exc.detail)
        return
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
            await desk.handle_officer_disconnect(room, user_id, speech_model)


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
    calls = list_calls(status=status, limit=limit, offset=offset, tenant_id=_tenant_scope(ctx))
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
    return get_aggregate_metrics(days=days, tenant_id=_tenant_scope(ctx))


@router.get("/admin/calls/{call_id}")
def get_call_detail_endpoint(
    call_id: str,
    ctx: AuthContext = Depends(require_admin_access),
) -> dict[str, Any]:
    """Retrieve full call detail including turns, summary, metrics, and ticket."""
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", call_id):
        raise HTTPException(status_code=400, detail="Invalid call_id format")

    detail = get_call_with_turns(call_id, tenant_id=_tenant_scope(ctx))
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

    call = get_call(call_id, tenant_id=_tenant_scope(ctx))
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    ok = save_call_review(call_id, rating=body.rating, note=body.note)
    return {"ok": ok, "call_id": call_id, "rating": body.rating}
