"""Opening and timing out a transfer to a human officer.

The brain speaks the transfer line; everything else about a transfer
lives here: the ticket (through ``ChatModel._maybe_create_ticket``, so one
conversation gets one officer), the call state, the ``voice_calls`` row, and
the staff lobby event that makes the call appear on the officers' screen.
When nobody answers in time, :func:`close_transfer_on_timeout` hands the call
back to the AI and records that the caller is owed a callback; the brain
speaks the "officers are busy" line.

Callers must check the ``ticket_queue`` flag first and not come here when it
is off — :func:`open_transfer` assumes a handoff may be promised.

The lobby channel carries metadata only (topic, priority, language, wait):
never transcript text or a brief. Content goes out on the per-call channel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import brief as call_brief
from .hub import hub
from .store import update_call

logger = logging.getLogger(__name__)

#: The handoff packet's topics are snake_case keys (``objection_or_dispute``);
#: this one stands in when there is no packet to read.
DEFAULT_TOPIC = "general_tax_support"
PRIORITIES = ("low", "normal", "high", "urgent")


def case_reference(ticket_id: str) -> str:
    """The reference a taxpayer is given for *ticket_id*: ``TIC-`` and its first 8 characters.

    The chat's support cases show the same form (``lib/ticketUi.ticketRef``),
    and the staff ticket search finds a ticket by it.
    """
    return f"TIC-{ticket_id[:8].upper()}"


def _topic_and_priority(packet: dict[str, Any] | None) -> tuple[str, str]:
    """The call's topic and priority, from the handoff packet when there is one."""
    packet = packet if isinstance(packet, dict) else {}
    topic = str(packet.get("topic") or "").strip() or DEFAULT_TOPIC
    priority = str(packet.get("priority") or "").strip().lower()
    return topic, priority if priority in PRIORITIES else "normal"


def begin_wait(room: Any) -> int:
    """The call starts a new wait for an officer: ``transferring``, under a new queue epoch.

    Every way into the queue goes through here — the AI's transfer, an officer
    passing the call on, an officer's audio dropping — so a countdown started for
    an earlier wait sees the epoch change and never acts on this one.
    """
    room.state.mode = "transferring"
    room.state.queue_epoch += 1
    epoch: int = room.state.queue_epoch
    return epoch


def end_wait(room: Any) -> None:
    """The wait is over (an officer joined): its countdown stops and can no longer act."""
    room.state.queue_epoch += 1
    stop_countdown(room)


def stop_countdown(room: Any) -> None:
    """Cancel the waiting caller's countdown, unless it is the task asking."""
    task, room.state.transfer_timer_task = room.state.transfer_timer_task, None
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()


def raise_waiting_priority(room: Any, *, reason: str, priority: str = "urgent") -> bool:
    """A caller already waiting needs a person sooner: raise the call and its ticket.

    The lobby gets ``call.priority_raised`` (metadata only), which re-sorts the
    queue and brings back an alert an officer dismissed. It is not
    ``call.transfer_requested``: that resets the queue row's claim and wait, and
    a call an officer has taken — joining, or in their reconnect grace — must
    not look free to the others. The call's own channel gets ``priority`` too,
    so the officer taking it sees why. Returns False, changing nothing, when
    the call is not waiting or is already at *priority* or above.
    """
    state = room.state
    if state.mode != "transferring" or priority not in PRIORITIES:
        return False
    current = state.priority if state.priority in PRIORITIES else "normal"
    if PRIORITIES.index(current) >= PRIORITIES.index(priority):
        return False
    state.priority = priority
    state.transfer_reason = reason
    update_call(room.call_id, priority=priority, transfer_reason=reason)
    if state.ticket_id:
        _update_ticket_priority(state.ticket_id, priority)
    hub.publish_lobby("call.priority_raised", {"call_id": room.call_id, "priority": priority, "reason": reason})
    hub.publish_call(room.call_id, "priority", {"priority": priority, "reason": reason})
    return True


def _update_ticket_priority(ticket_id: str, priority: str) -> None:
    try:
        from .. import database as db

        db.update_ticket(ticket_id, priority=priority)
    except Exception:
        logger.warning("Could not raise ticket %s to %s", ticket_id, priority, exc_info=True)


def is_staff_available(target_team: str = "") -> bool:
    """Whether any officer is signed in (on shift) for *target_team* — anyone at all when empty.

    Fails open: when presence cannot be read the call is queued as before,
    because telling a caller nobody is there when someone might be is the
    worse mistake.
    """
    try:
        from .presence import count_active_officers

        return count_active_officers(target_team) > 0
    except Exception:
        logger.warning("Officer presence unavailable; queueing the call anyway", exc_info=True)
        return True


def queue_position(room: Any) -> int:
    """The waiting caller's place in the queue, counting from 1.

    Ahead of them: calls of the same tenant still waiting for an officer (not
    claimed, not in an officer's reconnect grace) with a higher priority, or
    the same priority and an earlier wait — the order the staff console sorts
    the queue in. Delay information is honest only when it is this simple; the
    desk has no handle-time history to estimate a wait from (G186).
    """
    from .state import registry

    state = room.state

    def rank(s: Any) -> tuple[int, float]:
        priority = s.priority if s.priority in PRIORITIES else "normal"
        return (-PRIORITIES.index(priority), float(s.transfer_requested_at or 0.0))

    mine = rank(state)
    ahead = 0
    for other in registry.list_live():
        o = other.state
        if (
            other is room
            or o.mode != "transferring"
            or o.claimed_by
            or o.reconnecting_officer
            or (o.tenant_id or "default") != (state.tenant_id or "default")
        ):
            continue
        if rank(o) < mine:
            ahead += 1
    return ahead + 1


def open_transfer(
    room: Any,
    chat_model: Any,
    reason: str,
    *,
    ticket_id: str | None = None,
    handoff: dict[str, Any] | None = None,
    question: str | None = None,
    target_team: str = "",
    priority: str = "",
) -> tuple[str, dict[str, Any]]:
    """Create (or reuse) the ticket and move the call to ``transferring``.

    *priority*, when given, overrides the handoff packet's priority, for a
    caller who needs a person sooner than their topic suggests (a safety
    concern is urgent whatever it is about).

    Returns ``(ticket_id, status_event)``; the caller sends the status event
    to the caller's socket through its own pipeline.
    """
    state = room.state
    tid = ticket_id or state.ticket_id or ""
    packet: dict[str, Any] | None = handoff if isinstance(handoff, dict) else None
    if not tid and chat_model is not None:
        last_q = question or (
            state.clarify.original_question if state.clarify else "Taxpayer assistance requested on call"
        )
        try:
            built = handoff or chat_model._build_handoff_packet(message=last_q, reason=reason)
            if isinstance(built, dict):
                # Officers see "Luganda caller" before they pick the call up.
                packet = {**built, "language": state.locale}
                if priority in PRIORITIES:
                    packet["priority"] = priority
            tid = chat_model._maybe_create_ticket(
                reason=reason,
                user_query=last_q,
                bot_reply="Connecting to officer...",
                session_id=room.call_id,
                conversation_id=state.conversation_id,
                priority=packet.get("priority", "normal") if packet else "normal",
                handoff=packet,
                user_id=state.user_id,
                locale=state.locale,
                modality="voice",
            ) or ""
        except Exception:
            logger.exception("Failed creating ticket during transfer for call %s", room.call_id)

    topic, packet_priority = _topic_and_priority(packet)
    # A reused ticket has no packet to carry the override.
    priority = priority if priority in PRIORITIES else packet_priority
    waiting_since = time.time()
    begin_wait(room)
    state.ticket_id = tid or None
    # Kept on the call: an officer who passes it on, or whose audio drops,
    # puts it back in the queue with the same topic and priority.
    state.topic = topic
    state.priority = priority
    state.transfer_reason = reason
    state.transfer_requested_at = waiting_since
    state.transfer_attempts += 1

    # Build structured HandoffEnvelope v2 for the officer desk and peer agents
    try:
        from ..memory.handoff import EvidenceCard
        from ..memory.service import get_memory_service
        from .carryover import checkpoint_call_resolution

        ref = case_reference(tid) if tid else f"CALL-{room.call_id[:8].upper()}"
        summary_text = str((packet or {}).get("summary") or getattr(state, "last_caller_text", "") or reason)
        macros = [
            {"id": "ack_handoff", "label": "Acknowledge & Greet in Caller Locale", "action": f"greet_{state.locale}"},
            {"id": "verify_tin", "label": "Verify TIN / Account Status", "action": "lookup_tin_status"},
        ]
        if "dispute" in reason or "objection" in topic:
            macros.append({"id": "open_objection", "label": "Initiate Sec. 23 Objection Checklist", "action": "start_objection_flow"})
        envelope = get_memory_service().create_handoff_envelope(
            thread_id=state.conversation_id or room.call_id,
            user_id=state.user_id or "",
            objective=topic,
            target_resolution=f"Resolve {topic} ({reason})",
            tenant_id=state.tenant_id or "default",
            evidence=[
                EvidenceCard(
                    source="voice_call_summary",
                    fact=summary_text[:280],
                    citation_index=0,
                    confidence=0.9,
                )
            ],
            blockers=[reason],
            next_action_type="officer_live_takeover",
            suggested_message=f"Review caller brief ({state.locale}) and address {topic}.",
            locale=state.locale,
            source_channel="voice_call",
            caller_affect=getattr(state, "caller_affect", "calm"),
            priority=priority,
            officer_macros=macros,
            affect_trajectory=list(getattr(state, "affect_trajectory", []) or []),
        )
        envelope.case_reference = ref
        env_dict = envelope.to_dict()
        state.last_handoff_envelope = env_dict
        hub.publish_call(room.call_id, "handoff_envelope", env_dict)
        checkpoint_call_resolution(
            room,
            objective=topic,
            status="escalated_officer",
            pending_steps=[f"Officer review ({ref})"],
            handoff_envelope=env_dict,
        )
    except Exception:
        logger.debug("Failed building HandoffEnvelope v2 for call %s", room.call_id, exc_info=True)

    update_call(
        room.call_id,
        status="transferring",
        transferred=True,
        transfer_reason=reason,
        ticket_id=tid,
        topic=topic,
        priority=priority,
        transfer_requested_at=waiting_since,
        target_team=target_team,
    )
    hub.publish_lobby(
        "call.transfer_requested",
        {
            "call_id": room.call_id,
            "status": "transferring",
            "started_at": state.started_at,
            "reason": reason,
            "ticket_ref": tid,
            "topic": topic,
            "priority": priority,
            "language": state.locale,
            "waiting_since": waiting_since,
            "target_team": target_team,
            "attempt": state.transfer_attempts,
        },
    )
    status_event = {"type": "status", "status": "transferring", "ticket_ref": tid}
    hub.publish_call(room.call_id, "status", status_event)
    # The officer who takes it reads the brief first; it is usually fresh
    # already (rolling), and this brings it up to the transfer itself.
    call_brief.build_now(room.call_id)
    return tid, status_event


#: Why a waiting call became a callback: nobody answered in time, nobody was
#: signed in to answer (G180) — outside working hours, nobody could be (G195) —
#: or the caller asked to be called back (G186).
CALLBACK_REASONS = (
    "no_officer_available",
    "no_officer_online",
    "outside_working_hours",
    "caller_requested_callback",
)


def close_transfer_on_timeout(
    room: Any, ticket_ref: str, *, callback_reason: str = "no_officer_available"
) -> dict[str, Any] | None:
    """The wait ends without an officer: back to the AI, and the caller is owed a callback.

    *callback_reason* is one of :data:`CALLBACK_REASONS`. Returns the status
    event for the caller's socket, or ``None`` when the call is no longer
    waiting (an officer joined, or it ended) — then there is nothing to close
    and the engine says nothing.
    """
    state = room.state
    if state.mode != "transferring":
        return None
    logger.info("Wait on call %s ends as a callback (%s)", room.call_id, callback_reason)
    state.mode = "ai"
    state.queue_epoch += 1
    update_call(
        room.call_id,
        status="ai",
        needs_callback=True,
        callback_reason=callback_reason,
    )
    try:
        from .. import database as db
        from ..flags import flags
        from .carryover import checkpoint_call_resolution

        ref = case_reference(ticket_ref) if ticket_ref else f"CALL-{room.call_id[:8].upper()}"
        checkpoint_call_resolution(
            room,
            objective=getattr(state, "topic", "") or "Voice transfer callback",
            status="awaiting_user_action",
            pending_steps=[f"Officer callback owed ({ref})"],
        )
        uid = str(getattr(state, "user_id", "") or "")
        if uid and flags.is_enabled("proactive_resolution_continuation", subject=uid):
            db.upsert_reminder_inbox(
                user_id=uid,
                deadline_name=f"Callback Scheduled: {ref}",
                due_date=time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)),
                message=f"All officers were busy during call {ref}. An officer callback or web continuation is queued.",
            )
    except Exception:
        logger.debug("close_transfer_on_timeout continuation hook skipped", exc_info=True)
    hub.publish_lobby("call.transfer_timed_out", {"call_id": room.call_id, "ticket_ref": ticket_ref})
    status_event = {"type": "status", "status": "ai", "ticket_ref": ticket_ref}
    hub.publish_call(room.call_id, "status", status_event)
    return status_event
