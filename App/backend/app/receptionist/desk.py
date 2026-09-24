"""The officer's side of a live call: take it, join it, give it back, end it.

The staff console's Call Desk (docs/plans/officer-call-desk-plan.md §6.3)
drives these through ``/v1/admin/calls/{call_id}/…`` routes and the officer
audio socket. Rules they share:

* **One officer per call, first come first served.** A claim is a
  conditional UPDATE (``store.claim_call``), so two officers pressing "Take
  call" together cannot both win; the loser learns who did.
* **A claim is a promise to connect.** It lapses after
  ``RECEPTIONIST_CLAIM_TIMEOUT_S`` unless the officer's audio joins, and the
  call goes back to waiting (``call.unclaimed``).
* **Taking over from the AI goes through the queue.** Claiming a call the AI
  is still handling opens a transfer like the AI's own, so the ticket,
  ``ticket_queue`` and the lobby see it the same way.
* The caller hears who joined (``officer_joining``) and a closing line
  (``officer_closing``), in the call's current language.

All state changes on a call run under ``CallRoom.desk_lock``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..flags import flags
from .config import get_claim_timeout_s
from .hub import hub
from .phrases import phrase
from .state import CallRoom, registry
from .store import claim_call, create_turn, get_call, release_claim, update_call
from .transfer import open_transfer
from .tts import synthesize_pcm16

logger = logging.getLogger(__name__)

#: 0.5 s of 16 kHz PCM16 per binary message to the caller's player.
_PCM_CHUNK = 16000


class DeskError(Exception):
    """An officer action refused: an HTTP status, a reason, and who holds the call."""

    def __init__(self, status: int, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra


def officer_display_name(handle: str) -> str:
    """What a caller and other officers see: "Officer Nakato" for nakato@ura.go.ug.

    Give it a name, username or email — an identity provider's ``sub`` is
    often a UUID, and a caller should not hear one.
    """
    name = (handle or "").split("@")[0].strip() or "on duty"
    return f"Officer {name[:1].upper()}{name[1:]}"


def _holder_name(room: CallRoom, holder: str) -> str:
    if holder and holder == room.state.claimed_by and room.state.claimed_name:
        return room.state.claimed_name
    if holder and holder == room.state.officer_id and room.state.officer_name:
        return room.state.officer_name
    return officer_display_name(holder) if holder else ""


def _live_room(call_id: str) -> CallRoom:
    room = registry.get(call_id)
    if room is None or room.state.mode == "ended":
        raise DeskError(404, "The call is not live")
    return room


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


async def claim(
    call_id: str, officer_id: str, chat_model: Any = None, officer_name: str = ""
) -> dict[str, Any]:
    """Take a waiting call (or one the AI is handling) for *officer_id*."""
    room = _live_room(call_id)
    timeout = get_claim_timeout_s()
    name = officer_name or officer_display_name(officer_id)
    async with room.desk_lock:
        mode = room.state.mode
        if mode not in ("ai", "transferring"):
            holder = room.state.officer_id or room.state.claimed_by or ""
            raise DeskError(409, "An officer already has this call", claimed_by=holder,
                            officer_name=_holder_name(room, holder))
        if mode == "ai" and not flags.is_enabled("ticket_queue"):
            raise DeskError(409, "The officer queue is off")
        now = time.time()
        if not claim_call(call_id, officer_id, now, now - timeout):
            holder = str((get_call(call_id) or {}).get("claimed_by") or "")
            raise DeskError(409, "Another officer took this call", claimed_by=holder,
                            officer_name=_holder_name(room, holder))
        room.state.claimed_by = officer_id
        room.state.claimed_name = name
        room.state.claimed_at = now
        if mode == "ai":
            # Taking over: the AI never asked for an officer, so queue the call
            # the way it would have — ticket, row, lobby — before joining.
            _, status_event = open_transfer(room, chat_model, "officer_takeover")
            await _send_to_caller(room, status_event)
        _restart_claim_timer(room, officer_id, timeout)
    hub.publish_lobby("call.claimed", {"call_id": call_id, "officer_id": officer_id, "officer_name": name})
    return {"claimed": True, "call_id": call_id, "officer_name": name, "claim_expires_at": now + timeout}


async def release(call_id: str, officer_id: str, *, is_admin: bool = False) -> dict[str, Any]:
    """Give a claimed call back before joining it (the officer changed their mind)."""
    room = _live_room(call_id)
    async with room.desk_lock:
        if room.state.mode == "bridged":
            raise DeskError(409, "You are on this call — end it instead")
        if not room.state.claimed_by:
            raise DeskError(409, "Nobody has claimed this call")
        if room.state.claimed_by != officer_id and not is_admin:
            raise DeskError(403, "Another officer has claimed this call")
        await _release_locked(room, "released")
    return {"released": True, "call_id": call_id}


def _restart_claim_timer(room: CallRoom, officer_id: str, timeout: float) -> None:
    _cancel_claim_timer(room)
    room.claim_timer = asyncio.create_task(_expire_claim(room, officer_id, timeout))


def _cancel_claim_timer(room: CallRoom) -> None:
    timer, room.claim_timer = room.claim_timer, None
    if timer is not None and not timer.done() and timer is not asyncio.current_task():
        timer.cancel()


async def _expire_claim(room: CallRoom, officer_id: str, timeout: float) -> None:
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return
    async with room.desk_lock:
        if room.state.claimed_by != officer_id or room.state.mode != "transferring":
            return
        logger.info("Claim on %s by %s lapsed before their audio joined", room.call_id, officer_id)
        await _release_locked(room, "claim_expired")


async def _release_locked(room: CallRoom, reason: str) -> None:
    """Drop the claim (call under desk_lock). A take-over goes back to the AI, anything else to waiting."""
    release_claim(room.call_id)
    room.state.claimed_by = ""
    room.state.claimed_name = ""
    room.state.claimed_at = None
    _cancel_claim_timer(room)
    if room.state.transfer_reason == "officer_takeover" and room.state.mode == "transferring":
        # The caller never asked for a person; the AI carries on.
        room.state.mode = "ai"
        update_call(room.call_id, status="ai")
        status_event = {"type": "status", "status": "ai"}
        await _send_to_caller(room, status_event)
        hub.publish_call(room.call_id, "status", status_event)
    hub.publish_lobby("call.unclaimed", {"call_id": room.call_id, "reason": reason})


# ---------------------------------------------------------------------------
# Joining and ending
# ---------------------------------------------------------------------------


async def bridge(room: CallRoom, officer_leg: Any, officer_id: str, speech_model: Any) -> str:
    """The claimant's audio is connected: tell the caller who joined, then connect them.

    Returns the officer's display name. The AI goes quiet first (mode
    ``bridged`` closes ``OfficerOutputGate`` and stops caller audio reaching
    it), the caller's player drops whatever AI audio it still had queued,
    and only then is the joining line played.
    """
    name = room.state.claimed_name or officer_display_name(officer_id)
    now = time.time()
    async with room.desk_lock:
        _cancel_claim_timer(room)
        room.officer = officer_leg
        room.state.mode = "bridged"
        room.state.officer_id = officer_id
        room.state.officer_name = name
        room.state.bridged_at = now
    # Answered: whatever an earlier unanswered transfer left owing is settled.
    update_call(room.call_id, status="bridged", officer_id=officer_id, bridged_at=now,
                needs_callback=False, callback_reason="")
    if room.state.ticket_id:
        try:
            from .. import database as db

            db.update_ticket(room.state.ticket_id, status="assigned", assignee=officer_id)
        except Exception:
            logger.debug("Could not assign ticket %s", room.state.ticket_id, exc_info=True)
    await _send_to_caller(room, {"type": "interrupt"})
    await say_to_caller(room, phrase("officer_joining", room.state.locale, name=name), speech_model)
    status_event = {"type": "status", "status": "bridged", "officer_name": name}
    await _send_to_caller(room, status_event)
    hub.publish_call(room.call_id, "status", status_event)
    hub.publish_lobby("call.bridged", {
        "call_id": room.call_id, "status": "bridged", "officer_id": officer_id, "officer_name": name,
    })
    return name


async def end(call_id: str, officer_id: str, speech_model: Any, *, is_admin: bool = False) -> dict[str, Any]:
    """The officer ends the call: a closing line, then the caller's screen hangs up."""
    room = _live_room(call_id)
    if room.state.mode != "bridged":
        raise DeskError(409, "No officer is on this call")
    if room.state.officer_id != officer_id and not is_admin:
        raise DeskError(403, "Another officer is on this call")
    duration = await say_to_caller(room, phrase("officer_closing", room.state.locale), speech_model)
    # Let the line play out: the caller's screen stops its player on "ended".
    await asyncio.sleep(duration + 0.3)
    await hang_up_caller(room, "officer_ended")
    return {"ended": True, "call_id": call_id}


async def hang_up_caller(room: CallRoom, reason: str) -> None:
    """End the call and tell the caller's screen, which then closes its socket."""
    await _send_to_caller(room, {"type": "status", "status": "ended"})
    await room.end(reason)


async def say_to_caller(room: CallRoom, text: str, speech_model: Any) -> float:
    """Speak *text* to the caller outside the AI pipeline; returns its length in seconds.

    Recorded as an assistant notice in the transcript, captioned on the
    caller's screen, and published to the call's watchers.
    """
    pcm = await asyncio.to_thread(synthesize_pcm16, speech_model, text, room.state.locale)
    room.state.turn_seq += 1
    turn = create_turn(call_id=room.call_id, seq=room.state.turn_seq, speaker="assistant", kind="notice", text=text)
    hub.publish_call(room.call_id, "turn", turn)
    await _send_to_caller(room, {"type": "caption", "speaker": "assistant", "text": text, "final": True,
                                 "turn_id": room.state.turn_seq})
    if pcm and room.caller_ws is not None:
        try:
            for i in range(0, len(pcm), _PCM_CHUNK):
                await room.caller_ws.send_bytes(pcm[i : i + _PCM_CHUNK])
        except Exception:
            logger.debug("Could not play a line to the caller on %s", room.call_id, exc_info=True)
    return len(pcm) / 32000


async def _send_to_caller(room: CallRoom, message: dict[str, Any]) -> None:
    if room.caller_ws is None:
        return
    try:
        await room.caller_ws.send_text(json.dumps(message))
    except Exception:
        logger.debug("Could not reach the caller on %s", room.call_id, exc_info=True)
