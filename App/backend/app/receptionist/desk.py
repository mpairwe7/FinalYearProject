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
* **Officers take the calls the AI hands over.** A call the AI is still
  handling is not claimed; nobody steps into it.
* **Only officers act on calls** (``ura_staff``, enforced by the routes).
  Nobody acts on a call another officer holds: administrators audit the
  service and have no override here.
* The caller hears who joined (``officer_joining``) and a closing line
  (``officer_closing``), in the call's current language.

All state changes on a call run under ``CallRoom.desk_lock``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from . import livekit, presence
from .config import get_claim_timeout_s, get_hold_update_s, get_officer_reconnect_grace_s
from .hub import hub
from .phrases import phrase
from .state import CallRoom, registry
from .store import (
    claim_call,
    create_turn,
    get_call,
    list_turns,
    release_claim,
    save_call_review,
    save_draft_wrapup_if_empty,
    update_call,
)
from .store import (
    get_caller_history as store_caller_history,
)
from .store import (
    mark_callback_done as store_callback_done,
)
from .transfer import begin_wait, end_wait
from .tts import synthesize_pcm16

logger = logging.getLogger(__name__)

#: 0.5 s of 16 kHz PCM16 per binary message to the caller's player.
_PCM_CHUNK = 16000

#: ``voice_calls.status`` values of a call that has not ended.
_LIVE_STATUSES = ("ai", "transferring", "bridged")


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
    often a UUID, and a caller should not hear one. Given only such an id
    (``auth0|6a7d…``, a bare UUID or hex string), it says "URA officer"
    rather than read the id out.
    """
    name = (handle or "").split("@")[0].strip()
    if not name:
        return "Officer on duty"
    if _looks_like_account_id(name):
        return "URA officer"
    return f"Officer {name[:1].upper()}{name[1:]}"


_ACCOUNT_ID_RE = re.compile(r"^(?:[A-Za-z0-9_-]+\|.+|[0-9a-fA-F-]{16,})$")


def _looks_like_account_id(text: str) -> bool:
    """An identity provider's subject (``auth0|…``, ``google-oauth2|…``) or a bare UUID/hex id."""
    return bool(_ACCOUNT_ID_RE.match(text))


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


async def claim(call_id: str, officer_id: str, officer_name: str = "") -> dict[str, Any]:
    """Take a call the AI handed over for *officer_id*.

    Officers get the calls the AI escalates; a call the AI is still handling
    is refused, because the caller has not asked for a person.
    """
    room = _live_room(call_id)
    timeout = get_claim_timeout_s()
    name = officer_name or officer_display_name(officer_id)
    async with room.desk_lock:
        mode = room.state.mode
        if mode not in ("ai", "transferring"):
            holder = room.state.officer_id or room.state.claimed_by or ""
            raise DeskError(409, "An officer already has this call", claimed_by=holder,
                            officer_name=_holder_name(room, holder))
        if mode == "ai":
            raise DeskError(403, "The AI is handling this call: officers take the calls it hands over")
        now = time.time()
        if not claim_call(call_id, officer_id, now, now - timeout):
            holder = str((get_call(call_id) or {}).get("claimed_by") or "")
            raise DeskError(409, "Another officer took this call", claimed_by=holder,
                            officer_name=_holder_name(room, holder))
        room.state.claimed_by = officer_id
        room.state.claimed_name = name
        room.state.claimed_at = now
        _restart_claim_timer(room, officer_id, timeout)
    hub.publish_lobby("call.claimed", {"call_id": call_id, "officer_id": officer_id, "officer_name": name})
    return {"claimed": True, "call_id": call_id, "officer_name": name, "claim_expires_at": now + timeout}


async def release(call_id: str, officer_id: str) -> dict[str, Any]:
    """Give a claimed call back before joining it (the officer changed their mind)."""
    room = _live_room(call_id)
    async with room.desk_lock:
        if room.state.mode == "bridged":
            raise DeskError(409, "You are on this call — end it instead")
        if not room.state.claimed_by:
            raise DeskError(409, "Nobody has claimed this call")
        if room.state.claimed_by != officer_id:
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
    """Drop the claim (call under desk_lock): the call goes back to waiting."""
    release_claim(room.call_id)
    room.state.claimed_by = ""
    room.state.claimed_name = ""
    room.state.claimed_at = None
    _cancel_claim_timer(room)
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
        if room.state.mode == "bridged" and room.state.officer_id == officer_id:
            return room.state.officer_name or name
        # Callers check eligibility, then wait (up to 20 s for LiveKit media)
        # before bridging. The caller can hang up or the claim lapse in that
        # window, so re-check under the lock: bridging an ended call would
        # write `bridged` over `ended` and leave the officer "on" a dead call.
        if (
            not officer_id
            or room.state.mode not in ("transferring", "ai")
            or officer_id not in (room.state.claimed_by, room.state.reconnecting_officer)
        ):
            raise DeskError(409, "The call is no longer waiting for this officer")
        _cancel_claim_timer(room)
        _cancel_reconnect_timer(room)
        room.state.reconnecting_officer = None
        room.officer = officer_leg
        room.state.mode = "bridged"
        end_wait(room)  # the AI's countdown must not outlive the wait it timed
        room.state.officer_id = officer_id
        room.state.officer_name = name
        room.state.bridged_at = now
    # The officer came before the caller answered the review asked as the
    # transfer began: it is unanswered (review.py), never left pending.
    if room.state.review_state == "asking_transfer":
        from .review import settle

        settle(room)
    # Answered: whatever an earlier unanswered transfer left owing is settled.
    update_call(room.call_id, status="bridged", officer_id=officer_id, bridged_at=now,
                needs_callback=False, callback_reason="")
    presence.set_on_call(officer_id, room.call_id, display_name=name)
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


async def end(call_id: str, officer_id: str, speech_model: Any) -> dict[str, Any]:
    """The officer ends the call: a closing line, then the caller's screen hangs up.

    Two clean-up cases end a call no officer is on, at once and without a
    closing line:

    * **stale** — the row still says live but no call room exists on this
      server (it restarted mid-call), so no caller is on the line;
    * **waiting** — the caller is in the queue and no *other* officer has
      claimed it (``officer_terminated_waiting``). A call the AI is still
      handling is not ended from the desk.
    """
    try:
        room = _live_room(call_id)
    except DeskError as exc:
        row = get_call(call_id) or {}
        if exc.status != 404 or registry.get(call_id) is not None or row.get("status") not in _LIVE_STATUSES:
            # An ended call keeps the reason it ended with.
            raise
        now = time.time()
        update_call(call_id, status="ended", end_reason="officer_terminated_stale", ended_at=now)
        hub.publish_lobby("call.ended", {"call_id": call_id, "reason": "officer_terminated_stale"})
        return {"ended": True, "call_id": call_id, "stale": True}

    if room.state.mode != "bridged":
        if room.state.mode != "transferring":
            raise DeskError(409, "No officer is on this call")
        holder = room.state.claimed_by or room.state.reconnecting_officer or ""
        if holder and holder != officer_id:
            raise DeskError(403, "Another officer has claimed this call", claimed_by=holder,
                            officer_name=_holder_name(room, holder))
        # Waiting for an officer, and nobody else has it: terminate it.
        # CallRoom.end() (through hang_up_caller) does the teardown — the
        # countdown, the media room, the pipeline and the row's status. Marking
        # the room ended first, as this once did, made it skip all of that (G176).
        await hang_up_caller(room, "officer_terminated_waiting")
        hub.publish_lobby("call.ended", {"call_id": call_id, "reason": "officer_terminated_waiting"})
        asyncio.create_task(_save_draft_wrapup(call_id))
        return {"ended": True, "call_id": call_id}

    if room.state.officer_id != officer_id:
        raise DeskError(403, "Another officer is on this call")
    presence.set_wrap_up(officer_id, call_id=call_id)
    duration = await say_to_caller(room, phrase("officer_closing", room.state.locale), speech_model)
    # Let the line play out: the caller's screen stops its player on "ended".
    await asyncio.sleep(duration + 0.3)
    await hang_up_caller(room, "officer_ended")
    asyncio.create_task(_save_draft_wrapup(call_id))
    return {"ended": True, "call_id": call_id}


async def hold(call_id: str, officer_id: str, on: bool, speech_model: Any = None) -> dict[str, Any]:
    """Put an active call on hold or resume it.

    The caller is told they are on hold, then reassured every
    ``RECEPTIONIST_HOLD_UPDATE_S`` until the officer resumes: a silent hold
    reads as a dropped call (G187). Resuming says nothing — the officer speaks.
    """
    room = _live_room(call_id)
    async with room.desk_lock:
        if room.state.mode != "bridged" and not (not on and getattr(room.state, "on_hold", False)):
            raise DeskError(409, "Call is not currently bridged with an officer")
        if room.state.officer_id != officer_id:
            raise DeskError(403, "Another officer is on this call")
        now = time.time()
        if on:
            if getattr(room.state, "on_hold", False):
                return {"hold": True, "call_id": call_id}
            room.state.on_hold = True
            room.state.hold_started_at = now
            update_call(call_id, hold_started_at=now)
            if room.officer is not None:
                room.officer.on_hold = True
            await say_to_caller(room, phrase("on_hold", room.state.locale), speech_model)
            # The call can end while that line plays (CallRoom.end takes its own lock).
            if room.state.mode == "bridged" and room.state.on_hold:
                _start_hold_reassurance(room, speech_model)
            status_event = {"type": "status", "status": "on_hold"}
            await _send_to_caller(room, status_event)
            hub.publish_call(call_id, "hold", {"on": True})
            hub.publish_call(call_id, "status", status_event)
            hub.publish_lobby("call.on_hold", {"call_id": call_id, "on": True})
            return {"hold": True, "call_id": call_id}
        else:
            if not getattr(room.state, "on_hold", False):
                return {"hold": False, "call_id": call_id, "hold_total_s": room.state.hold_total_s}
            room.state.on_hold = False
            _stop_hold_reassurance(room)
            held_s = now - (room.state.hold_started_at or now)
            room.state.hold_total_s = (room.state.hold_total_s or 0.0) + max(0.0, held_s)
            room.state.hold_started_at = None
            update_call(call_id, hold_started_at=None, hold_total_s=room.state.hold_total_s)
            if room.officer is not None:
                room.officer.on_hold = False
            status_event = {"type": "status", "status": "bridged", "officer_name": room.state.officer_name}
            await _send_to_caller(room, status_event)
            hub.publish_call(call_id, "hold", {"on": False})
            hub.publish_call(call_id, "status", status_event)
            hub.publish_lobby("call.on_hold", {"call_id": call_id, "on": False})
            return {"hold": False, "call_id": call_id, "hold_total_s": room.state.hold_total_s}


async def transfer(
    call_id: str,
    officer_id: str,
    team: str = "",
    target_officer_id: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Transfer the call to another team or available officer."""
    room = _live_room(call_id)
    async with room.desk_lock:
        if room.state.mode != "bridged":
            raise DeskError(409, "Call is not currently bridged with an officer")
        if room.state.officer_id != officer_id:
            raise DeskError(403, "Another officer is on this call")
        now = time.time()
        previous_identity = room.state.livekit_officer_identity
        if room.state.livekit_room and previous_identity:
            try:
                await livekit.revoke_participant(room.state.livekit_room, previous_identity)
            except Exception as exc:
                # room.call_id is the id this server issued, not the request path.
                logger.exception("Could not revoke LiveKit access for call %s", room.call_id)
                raise DeskError(
                    503,
                    "Call transfer is temporarily unavailable; media access could not be revoked",
                ) from exc
            room.state.livekit_officer_identity = ""
            room.state.livekit_officer_route_active = False
        if room.officer is not None:
            await room.officer.close()
            room.officer = None

        if getattr(room.state, "on_hold", False):
            held_s = now - (room.state.hold_started_at or now)
            room.state.hold_total_s = (room.state.hold_total_s or 0.0) + max(0.0, held_s)
            room.state.on_hold = False
            room.state.hold_started_at = None
            _stop_hold_reassurance(room)

        from_officer = room.state.officer_name or officer_display_name(officer_id)
        begin_wait(room)
        room.state.transfer_reason = "officer_transfer"
        room.state.target_team = team
        room.state.transfer_requested_at = now
        room.state.transfer_attempts += 1
        room.state.claimed_by = ""
        room.state.claimed_name = ""
        room.state.claimed_at = None
        room.state.officer_id = None
        room.state.officer_name = None

        if note:
            room.state.turn_seq += 1
            turn = create_turn(
                call_id=room.call_id,
                seq=room.state.turn_seq,
                speaker="officer",
                kind="notice",
                text=f"Transferred by {from_officer}: {note}",
            )
            hub.publish_call(room.call_id, "turn", turn)

        update_call(
            call_id,
            status="transferring",
            target_team=team,
            transfer_requested_at=now,
            transferred=1,
            transfer_reason="officer_transfer",
            claimed_by="",
            claimed_at=None,
            officer_id=None,
            hold_started_at=None,
            hold_total_s=room.state.hold_total_s,
        )

        presence.set_wrap_up(officer_id, call_id=call_id)
        asyncio.create_task(_save_draft_wrapup(call_id))

        status_event = {"type": "status", "status": "transferring"}
        await _send_to_caller(room, status_event)
        hub.publish_call(call_id, "status", status_event)

        hub.publish_lobby(
            "call.transfer_requested",
            {
                "call_id": call_id,
                "reason": "officer_transfer",
                "topic": room.state.topic or "general_tax_support",
                "priority": room.state.priority or "normal",
                "language": room.state.locale or "en",
                "ticket_ref": room.state.ticket_id or "",
                "waiting_since": now,
                "target_team": team,
                "attempt": room.state.transfer_attempts,
                "from_officer": from_officer,
            },
        )
        _start_countdown(room)
        return {"transferred": True, "call_id": call_id, "target_team": team}


def _start_countdown(room: CallRoom) -> None:
    """A call back in the queue gets the waiting caller's countdown: hold lines, then a callback.

    Without it a call an officer passed on, or whose officer dropped, waited in
    silence until the call's ceiling (G179).
    """
    if room.brain is not None:
        room.brain.start_queue_countdown(room.state.ticket_id or "")


def _start_hold_reassurance(room: CallRoom, speech_model: Any) -> None:
    _stop_hold_reassurance(room)
    every = get_hold_update_s()
    if every:
        room.hold_timer = asyncio.create_task(_reassure_while_held(room, speech_model, every))


def _stop_hold_reassurance(room: CallRoom) -> None:
    timer, room.hold_timer = room.hold_timer, None
    if timer is not None and not timer.done() and timer is not asyncio.current_task():
        timer.cancel()


async def _reassure_while_held(room: CallRoom, speech_model: Any, every: float) -> None:
    """While the officer holds the call, the caller hears they have not been forgotten.

    A line that cannot be spoken is logged and the next one tried: ending the
    loop on it would leave the rest of the hold silent, the failure this exists
    to prevent.
    """
    while True:
        await asyncio.sleep(every)
        if room.state.mode != "bridged" or not room.state.on_hold:
            return
        try:
            await say_to_caller(room, phrase("officer_hold_update", room.state.locale), speech_model)
        except Exception:  # noqa: BLE001 — cancellation is not an Exception, so it still ends the loop
            logger.warning("Hold reassurance for call %s could not be spoken", room.call_id, exc_info=True)


def _caller_satisfaction(score: Any, status: str | None) -> tuple[str, int | None] | None:
    """What the officer recorded of the caller's satisfaction: ``(status, score)``, or None.

    ``answered`` with a score of 1–5, or ``not_said`` (the caller did not say)
    with none. Neither given is a wrap-up from before the field existed, and
    records nothing.
    """
    status = (status or "").strip() or None
    if score is None and status is None:
        return None
    if status in (None, "answered"):
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise DeskError(400, "caller_satisfaction must be a whole number from 1 to 5")
        return "answered", score
    if status == "not_said":
        if score is not None:
            raise DeskError(400, "A caller who didn't say has no caller_satisfaction score")
        return "no_answer", None
    raise DeskError(400, "caller_satisfaction_status must be 'answered' or 'not_said'")


async def wrapup(
    call_id: str,
    officer_id: str,
    outcome: str,
    note: str,
    ticket_action: str | None = None,
    rating: int | None = None,
    rating_note: str | None = None,
    caller_satisfaction: Any = None,
    caller_satisfaction_status: str | None = None,
    officer_name: str = "",
) -> dict[str, Any]:
    """Save call outcome and wrap-up notes, apply ticket action, restore presence.

    *caller_satisfaction* is what the caller told the officer when asked
    before the call ended, on the same 5-level scale taxpayers rate officer
    chats with; it is the officer's service the administrators audit
    (``service_ratings`` ``call_officer_csat``). *rating* is separate: the
    officer's own rating of how the AI handled the call before the transfer.
    A wrap-up posted again keeps the first satisfaction recorded.
    """
    call = get_call(call_id)
    if not call:
        raise DeskError(404, "Call not found")
    if ticket_action not in (None, "", "create", "resolve", "keep_open"):
        raise DeskError(400, "Invalid ticket action")
    ticket_id = call.get("ticket_id")
    if ticket_action:
        from ..flags import flags

        if not flags.is_enabled("ticket_queue"):
            raise DeskError(409, "Ticket queue is unavailable")
        if ticket_action in ("resolve", "keep_open") and not ticket_id:
            raise DeskError(409, "Call has no linked ticket")
    valid_outcomes = {"resolved", "follow_up", "callback", "referred", "abandoned", "ai_resolved"}
    if outcome not in valid_outcomes:
        raise DeskError(400, f"Invalid outcome '{outcome}'")
    satisfaction = _caller_satisfaction(caller_satisfaction, caller_satisfaction_status)

    now = time.time()
    needs_callback = 1 if outcome == "callback" else 0
    callback_reason = "officer_outcome" if outcome == "callback" else ""

    update_kwargs: dict[str, Any] = {
        "outcome": outcome,
        "wrapup_note": note,
        "wrapup_at": now,
    }
    if needs_callback:
        update_kwargs["needs_callback"] = 1
        update_kwargs["callback_reason"] = callback_reason

    if ticket_action:
        from .. import database as db

        try:
            if ticket_action == "resolve" and ticket_id:
                db.update_ticket(ticket_id, status="resolved", staff_note=note)
            elif ticket_action == "keep_open" and ticket_id:
                db.update_ticket(ticket_id, staff_note=note)
            elif ticket_action == "create":
                if not ticket_id:
                    t = db.create_ticket(
                        tenant_id=call.get("tenant_id") or "default",
                        conversation_id=call.get("conversation_id") or None,
                        locale=call.get("locale") or "en",
                        user_id=call.get("user_id") or "",
                        user_query=note or call.get("topic") or "Phone inquiry",
                        team=call.get("target_team") or "general",
                        priority=call.get("priority") or "normal",
                        reason=f"Call wrapup: {outcome}",
                        modality="voice",
                    )
                    update_kwargs["ticket_id"] = t["id"]
                else:
                    db.update_ticket(ticket_id, staff_note=note)
        except Exception as exc:
            logger.warning("Failed applying ticket action '%s' on %s", ticket_action, call_id, exc_info=True)
            raise DeskError(503, "Ticket action could not be completed") from exc

    update_call(call_id, **update_kwargs)

    if rating is not None:
        try:
            save_call_review(call_id, rating=int(rating), note=rating_note or "")
        except Exception:
            logger.debug("Failed saving review on wrapup for %s", call_id, exc_info=True)

    satisfaction_recorded = False
    if satisfaction is not None:
        from .. import service_ratings

        status, score = satisfaction
        try:
            service_ratings.request(
                "call_officer_csat",
                call_id=call_id,
                channel="call",
                subject="officer",
                tenant_id=call.get("tenant_id") or "default",
                conversation_id=call.get("conversation_id") or "",
                officer_id=officer_id or "",
                officer_name=officer_name,
                team=call.get("target_team") or "",
                topic=call.get("topic") or "",
                locale=call.get("locale") or "",
                user_id=call.get("user_id") or "",
            )
            satisfaction_recorded = service_ratings.answer(
                "call_officer_csat", call_id=call_id, status=status, score=score
            )
        except Exception:
            logger.warning("Failed recording caller satisfaction on wrapup for %s", call_id, exc_info=True)

    presence.finish_wrap_up(officer_id)

    hub.publish_lobby("call.wrapped_up", {
        "call_id": call_id,
        "outcome": outcome,
        "officer_id": officer_id,
    })
    result: dict[str, Any] = {"wrapped_up": True, "call_id": call_id, "outcome": outcome}
    if satisfaction is not None:
        result["caller_satisfaction"] = {
            "status": satisfaction[0],
            "score": satisfaction[1],
            "recorded": satisfaction_recorded,
        }
    return result


async def callback_done(call_id: str, officer_id: str, note: str = "") -> dict[str, Any]:
    """Mark an open callback as completed by an officer."""
    call = get_call(call_id)
    if not call:
        raise DeskError(404, "Call not found")
    ok = store_callback_done(call_id, done_by=officer_id, note=note)
    if not ok:
        raise DeskError(400, "Could not mark callback done")
    return {"ok": True, "call_id": call_id}


def caller_history(call_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
    """Retrieve previous calls and tickets for the same taxpayer."""
    return store_caller_history(call_id, tenant_id=tenant_id)


def draft_wrapup_note(call_id: str) -> str:
    """Draft a concise 3-5 sentence English wrap-up note from the brief and turns."""
    call = get_call(call_id)
    if not call:
        return ""
    brief = call.get("brief") or {}
    turns = list_turns(call_id)

    goal = ""
    why = ""
    still_open = ""
    if isinstance(brief, dict):
        if isinstance(brief.get("caller_goal"), dict):
            goal = str(brief["caller_goal"].get("text") or "")
        if isinstance(brief.get("why_officer"), dict):
            why = str(brief["why_officer"].get("text") or "")
        opens = brief.get("still_open") or []
        if opens and isinstance(opens[0], dict):
            still_open = str(opens[0].get("text") or "")

    caller_utterances = [t.get("text", "") for t in turns if t.get("speaker") == "caller" and t.get("kind") == "utterance"]
    officer_utterances = [t.get("text", "") for t in turns if t.get("speaker") == "officer" and t.get("kind") == "utterance"]

    sentences: list[str] = []
    if goal:
        sentences.append(f"Taxpayer inquiry: {goal.rstrip('.')}.")
    elif caller_utterances:
        first = caller_utterances[0].rstrip('.')
        sentences.append(f"Taxpayer inquired about: {first}.")
    else:
        sentences.append("Taxpayer contacted URA telephone support.")

    if why:
        sentences.append(f"Reason for escalation: {why.rstrip('.')}.")

    if officer_utterances:
        sentences.append("Officer speech is recorded in the transcript; review it for the advice given.")

    if still_open:
        sentences.append(f"Follow-up context: {still_open.rstrip('.')}.")
    else:
        sentences.append("Resolution and taxpayer understanding are not verified; officer review is required.")

    return " ".join(sentences)


async def _save_draft_wrapup(call_id: str) -> None:
    try:
        note = draft_wrapup_note(call_id)
        if note:
            save_draft_wrapup_if_empty(call_id, note)
    except Exception:
        logger.debug("Failed drafting wrapup note for %s", call_id, exc_info=True)


async def handle_officer_disconnect(room: CallRoom, officer_id: str, speech_model: Any) -> None:
    """The officer's connection dropped mid-call without an explicit End."""
    grace_s = get_officer_reconnect_grace_s()
    async with room.desk_lock:
        if room.state.mode != "bridged" or room.state.officer_id != officer_id:
            return
        logger.info("Officer %s disconnected from call %s; starting %.0fs grace period", officer_id, room.call_id, grace_s)
        begin_wait(room)
        room.state.reconnecting_officer = officer_id
        room.officer = None
        try:
            await say_to_caller(room, phrase("officer_reconnecting", room.state.locale), speech_model)
        except Exception:
            pass
        status_event = {"type": "status", "status": "reconnecting"}
        await _send_to_caller(room, status_event)
        hub.publish_call(room.call_id, "status", status_event)
        hub.publish_lobby("call.officer_disconnected", {"call_id": room.call_id, "officer_id": officer_id, "grace_s": grace_s})
        _start_reconnect_timer(room, officer_id, grace_s)


def _start_reconnect_timer(room: CallRoom, officer_id: str, grace_s: float) -> None:
    _cancel_reconnect_timer(room)
    room.state.reconnect_timer = asyncio.create_task(_expire_reconnect_grace(room, officer_id, grace_s))


def _cancel_reconnect_timer(room: CallRoom) -> None:
    timer = getattr(room.state, "reconnect_timer", None)
    if timer is not None and not timer.done() and timer is not asyncio.current_task():
        timer.cancel()
    room.state.reconnect_timer = None


async def _expire_reconnect_grace(room: CallRoom, officer_id: str, grace_s: float) -> None:
    try:
        await asyncio.sleep(grace_s)
    except asyncio.CancelledError:
        return
    async with room.desk_lock:
        if getattr(room.state, "reconnecting_officer", None) != officer_id or room.state.mode != "transferring":
            return
        # Keep the claim while revoking media. Publishing availability first
        # lets the next officer join while the previous one can still listen.
        stale_identity = room.state.livekit_officer_identity
        room.state.livekit_officer_identity = ""
        room.state.livekit_officer_route_active = False
        if not await revoke_media(room, stale_identity):
            room.state.reconnecting_officer = None
            room.state.officer_id = None
            room.state.officer_name = None
            try:
                presence.finish_wrap_up(officer_id)
            except Exception:
                logger.warning("Could not restore officer presence on call %s", room.call_id, exc_info=True)
            return
        # The caller can hang up while the media service is responding.
        if room.state.mode != "transferring":
            room.state.reconnecting_officer = None
            room.state.officer_id = None
            room.state.officer_name = None
            try:
                presence.finish_wrap_up(officer_id)
            except Exception:
                logger.warning("Could not restore officer presence on call %s", room.call_id, exc_info=True)
            return
        logger.info("Reconnect grace period expired for %s on %s; releasing to queue", officer_id, room.call_id)
        room.state.reconnecting_officer = None
        # The lapsed officer is off the call: nobody may treat them as its
        # officer any more (the desk's terminate path once crashed on it, G176).
        room.state.officer_id = None
        room.state.officer_name = None
        # The lapsed officer's media identity would otherwise stay on the
        # allowlist and refuse the next officer's audio (4409): the call sat
        # in the queue unanswerable.
        await _release_locked(room, "officer_disconnected")
        begin_wait(room)
        room.state.turn_seq += 1
        turn = create_turn(
            call_id=room.call_id,
            seq=room.state.turn_seq,
            speaker="assistant",
            kind="notice",
            text="Previous officer disconnected. Waiting for officer.",
        )
        hub.publish_call(room.call_id, "turn", turn)
        await _send_to_caller(room, {"type": "status", "status": "transferring"})
        hub.publish_lobby(
            "call.transfer_requested",
            {
                "call_id": room.call_id,
                "reason": "officer_disconnected",
                "topic": room.state.topic or "general_tax_support",
                "priority": room.state.priority or "normal",
                "language": room.state.locale or "en",
                "ticket_ref": room.state.ticket_id or "",
                "waiting_since": time.time(),
                "attempt": room.state.transfer_attempts,
            },
        )
        _start_countdown(room)
    # Their presence still said on_call for a call they had left.
    try:
        presence.finish_wrap_up(officer_id)
    except Exception:
        logger.warning("Could not restore the lapsed officer's presence on call %s", room.call_id, exc_info=True)


async def revoke_media(room: CallRoom, identity: str) -> bool:
    """Revoke access before reuse; terminate the call if isolation is uncertain."""
    if not (room.state.livekit_room and identity):
        return True
    try:
        await asyncio.wait_for(
            livekit.revoke_participant(room.state.livekit_room, identity), timeout=5.0,
        )
        return True
    except Exception:
        logger.warning(
            "Could not revoke LiveKit participant (ref %s) on call %s",
            livekit.log_ref(identity),
            room.call_id,
            exc_info=True,
        )
        await room.end("media_revocation_failed")
        return False


async def hang_up_caller(room: CallRoom, reason: str) -> None:
    """End the call and tell the caller's screen, which then closes its socket."""
    await _send_to_caller(room, {"type": "status", "status": "ended"})
    await room.end(reason)


async def say_to_caller(room: CallRoom, text: str, speech_model: Any) -> float:
    """Speak *text* to the caller outside the AI pipeline; returns its length in seconds.

    Recorded as an assistant notice in the transcript, captioned on the
    caller's screen, and published to the call's watchers.
    """
    # A missing speech backend is a supported degraded mode: the caption still
    # goes out, but there is no reason to create a default-executor worker for
    # a synthesis function that will immediately return empty audio.
    pcm = (
        await asyncio.to_thread(synthesize_pcm16, speech_model, text, room.state.locale)
        if speech_model is not None and text
        else b""
    )
    room.state.turn_seq += 1
    turn = create_turn(call_id=room.call_id, seq=room.state.turn_seq, speaker="assistant", kind="notice", text=text)
    hub.publish_call(room.call_id, "turn", turn)
    await _send_to_caller(room, {"type": "caption", "speaker": "assistant", "text": text, "final": True,
                                 "turn_id": room.state.turn_seq})
    if pcm and room.state.livekit_room and room.pipeline_task is not None:
        try:
            from pipecat.frames.frames import OutputAudioRawFrame

            for i in range(0, len(pcm), _PCM_CHUNK):
                await room.transport.send_audio(
                    OutputAudioRawFrame(
                        audio=pcm[i : i + _PCM_CHUNK], sample_rate=16000, num_channels=1
                    )
                )
        except Exception:
            logger.exception("Could not queue a notice into the LiveKit pipeline for %s", room.call_id)
    elif pcm and room.caller_ws is not None:
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
