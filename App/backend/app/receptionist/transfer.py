"""Opening a transfer to a human officer — the part both engines share.

The cascaded brain speaks its own transfer line; Gemini Live speaks whatever
its tool result tells it to. Everything else about a transfer is the same and
lives here: the ticket (through ``ChatModel._maybe_create_ticket``, so one
conversation gets one officer), the call state, the ``voice_calls`` row, and
the staff lobby event that makes the call appear on the officers' screen.

Callers must check the ``ticket_queue`` flag first and not come here when it
is off — this function assumes a handoff may be promised.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .hub import hub
from .store import update_call

logger = logging.getLogger(__name__)


def open_transfer(
    room: Any,
    chat_model: Any,
    reason: str,
    *,
    ticket_id: str | None = None,
    handoff: dict[str, Any] | None = None,
    question: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Create (or reuse) the ticket and move the call to ``transferring``.

    Returns ``(ticket_id, status_event)``; the caller sends the status event
    to the caller's socket through its own pipeline.
    """
    state = room.state
    tid = ticket_id or state.ticket_id or ""
    if not tid and chat_model is not None:
        last_q = question or (
            state.clarify.original_question if state.clarify else "Taxpayer assistance requested on call"
        )
        try:
            packet = handoff or chat_model._build_handoff_packet(message=last_q, reason=reason)
            if isinstance(packet, dict):
                # Officers see "Luganda caller" before they pick the call up.
                packet = {**packet, "language": state.locale}
            tid = chat_model._maybe_create_ticket(
                reason=reason,
                user_query=last_q,
                bot_reply="Connecting to officer...",
                session_id=room.call_id,
                conversation_id=state.conversation_id,
                priority=packet.get("priority", "normal") if isinstance(packet, dict) else "normal",
                handoff=packet,
                user_id=state.user_id,
                locale=state.locale,
                modality="voice",
            ) or ""
        except Exception:
            logger.exception("Failed creating ticket during transfer for call %s", room.call_id)

    state.mode = "transferring"
    state.ticket_id = tid or None
    state.transfer_reason = reason
    state.transfer_requested_at = time.time()

    update_call(
        room.call_id,
        status="transferring",
        transferred=True,
        transfer_reason=reason,
        ticket_id=tid,
    )
    hub.publish_lobby(
        "call.transfer_requested",
        {
            "call_id": room.call_id,
            "status": "transferring",
            "started_at": state.started_at,
            "reason": reason,
            "ticket_id": tid,
            "topic": "General Tax Support",
            "priority": "normal",
            "language": state.locale,
        },
    )
    status_event = {"type": "status", "status": "transferring", "ticket_ref": tid}
    hub.publish_call(room.call_id, "status", status_event)
    return tid, status_event
