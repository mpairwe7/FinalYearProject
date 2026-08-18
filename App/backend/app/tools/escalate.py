"""Escalation tool — route a conversation to a human URA officer.

Creates a row in the ``tickets`` table and returns a structured
confirmation.  This is a *medium-risk* tool: it doesn't write to
URA systems, but it does persist user input to the ticket queue
so it runs through the same PII-redaction path as conversation
logging.

The LLM is told (in the system-prompt tool-use suffix) to call
this tool when:

- The user explicitly asks for a human ("can I speak to someone")
- A dispute / appeal / assessment question comes up
- The bot itself can't find enough grounding after 2+ tool calls
- The user's question involves a specific TIN / account and the
  bot has no URA account tool to serve it

The supervisor agent (Phase C) also routes requests here via its
ESCALATE route — this tool is for cases the supervisor's rules
missed but the LLM recognised mid-conversation.
"""

from __future__ import annotations

import logging
from typing import Any

from . import Tool, ToolRegistry, ToolSchema

logger = logging.getLogger(__name__)


class EscalateToHumanTool(Tool):
    """Create an escalation ticket for human follow-up."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="escalate_to_human",
            description=(
                "Route this conversation to a human URA officer by "
                "creating an escalation ticket.  Use ONLY when the "
                "user explicitly asks for a human, when the question "
                "involves a dispute / appeal / audit, or when you've "
                "exhausted all available tools without finding a "
                "confident answer.  Never escalate casual questions — "
                "try search_ura_knowledge_base first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": (
                            "Concise justification (1-2 sentences) for why "
                            "this needs human review.  Will be shown to the "
                            "URA officer who picks up the ticket."
                        ),
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "normal", "high", "urgent"],
                        "description": (
                            "Urgency. 'urgent' for time-sensitive or legal "
                            "matters; 'high' for disputes; 'normal' for "
                            "most escalations; 'low' for general staff review."
                        ),
                        "default": "normal",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what the user is asking for.",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "ticket_id": {"type": "string"},
                    "status": {"type": "string"},
                    "priority": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": "string"},
                },
            },
            risk="medium",
            namespace="core",
            required_scopes=("ticket_escalation",),
            scope_exempt_roles=(
                "verified_taxpayer",
                "ura_staff",
                "ura_admin",
                "ura_auditor",
            ),
            read_only=False,
            idempotent=False,

            # The UI should display a confirmation prompt before
            # the ticket is actually created.  For now we create
            # unconditionally but the field is set so a future UI
            # can read it.
            requires_confirmation=False,
        )

    def execute(
        self,
        reason: str,
        priority: str = "normal",
        summary: str = "",
    ) -> dict[str, Any]:
        try:
            from .. import database as db  # noqa: PLC0415
            from ..flags import flags  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"database unavailable: {e}"}

        if not flags.is_enabled("ticket_queue"):
            return {
                "ok": False,
                "error": "ticket_queue is off — escalation was not persisted",
            }

        # We don't have access to the request-scoped session_id /
        # conversation_id here — the calling service layer will
        # enrich them via the `escalation_context` kwarg when
        # available (see service.py Phase 14-D hook).  This tool
        # creates the ticket with whatever it has; the service layer
        # may also call db.create_ticket directly for supervisor-
        # driven escalations.
        try:
            ticket = db.create_ticket(
                reason=reason,
                user_query=(summary or reason)[:2000],
                bot_reply="",
                priority=priority,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("escalate_to_human: ticket creation failed")
            return {"ok": False, "error": f"could not create ticket: {e}"}

        return {
            "ok": True,
            "ticket_id": ticket.get("id", ""),
            "status": "open",
            "priority": ticket.get("priority", priority),
            "message": (
                f"Ticket {ticket.get('id', '')[:8]} created at priority "
                f"{priority}. A URA officer will review this. You may "
                f"also contact URA directly at https://ura.go.ug."
            ),
        }


ToolRegistry.register(EscalateToHumanTool())
