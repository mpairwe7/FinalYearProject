"""Opening and timing out a transfer to a human officer — the part both engines share.

The cascaded brain speaks its own transfer line; Gemini Live speaks whatever
its tool result tells it to. Everything else about a transfer is the same and
lives here: the ticket (through ``ChatModel._maybe_create_ticket``, so one
conversation gets one officer), the call state, the ``voice_calls`` row, and
the staff lobby event that makes the call appear on the officers' screen.
When nobody answers in time, :func:`close_transfer_on_timeout` hands the call
back to the AI and records that the caller is owed a callback; each engine
still speaks its own "officers are busy" line.

Callers must check the ``ticket_queue`` flag first and not come here when it
is off — :func:`open_transfer` assumes a handoff may be promised.

The lobby channel carries metadata only (topic, priority, language, wait):
never transcript text or a brief. Content goes out on the per-call channel.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .hub import hub
from .store import update_call

logger = logging.getLogger(__name__)

#: The handoff packet's topics are snake_case keys (``objection_or_dispute``);
#: this one stands in when there is no packet to read.
DEFAULT_TOPIC = "general_tax_support"
PRIORITIES = ("low", "normal", "high", "urgent")


def _topic_and_priority(packet: dict[str, Any] | None) -> tuple[str, str]:
    """The call's topic and priority, from the handoff packet when there is one."""
    packet = packet if isinstance(packet, dict) else {}
    topic = str(packet.get("topic") or "").strip() or DEFAULT_TOPIC
    priority = str(packet.get("priority") or "").strip().lower()
    return topic, priority if priority in PRIORITIES else "normal"


def open_transfer(
    room: Any,
    chat_model: Any,
    reason: str,
    *,
    ticket_id: str | None = None,
    handoff: dict[str, Any] | None = None,
    question: str | None = None,
    target_team: str = "",
) -> tuple[str, dict[str, Any]]:
    """Create (or reuse) the ticket and move the call to ``transferring``.

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

    topic, priority = _topic_and_priority(packet)
    waiting_since = time.time()
    state.mode = "transferring"
    state.ticket_id = tid or None
    state.transfer_reason = reason
    state.transfer_requested_at = waiting_since
    state.transfer_attempts += 1

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
    return tid, status_event


def close_transfer_on_timeout(room: Any, ticket_ref: str) -> dict[str, Any] | None:
    """No officer answered in time: back to the AI, and the caller is owed a callback.

    Returns the status event for the caller's socket, or ``None`` when the
    call is no longer waiting (an officer joined, or it ended) — then there
    is nothing to time out and the engine says nothing.
    """
    state = room.state
    if state.mode != "transferring":
        return None
    logger.info("Transfer timeout reached for call %s", room.call_id)
    state.mode = "ai"
    update_call(
        room.call_id,
        status="ai",
        needs_callback=True,
        callback_reason="no_officer_available",
    )
    hub.publish_lobby("call.transfer_timed_out", {"call_id": room.call_id, "ticket_ref": ticket_ref})
    status_event = {"type": "status", "status": "ai", "ticket_ref": ticket_ref}
    hub.publish_call(room.call_id, "status", status_event)
    return status_event
