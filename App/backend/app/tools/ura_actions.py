"""Confirmed URA action proposal interface.

Critical tools must never be executed just because the LLM suggested them.
This module creates an auditable action proposal and only attempts a configured
submit endpoint after MCP policy has confirmed auth, consent, explicit user
confirmation, and idempotency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from . import Tool, ToolRegistry, ToolSchema


class UraActionProposalTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ura_action_proposal",
            description=(
                "Create or submit a confirmed URA action proposal such as filing support "
                "or account update. Requires MCP critical-action authorization."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action_type": {"type": "string"},
                    "payload": {"type": "object"},
                    "idempotency_key": {"type": "string"},
                    "submit": {
                        "type": "boolean",
                        "description": "False returns a proposal only; true submits to configured API.",
                    },
                },
                "required": ["action_type", "payload", "idempotency_key"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "submitted": {"type": "boolean"},
                    "configured": {"type": "boolean"},
                    "proposal": {"type": "object"},
                    "message": {"type": "string"},
                    "error": {"type": "string"},
                },
            },
            risk="critical",
            requires_confirmation=True,
            namespace="ura_actions",
            required_scopes=("ura_account_access", "ura_actions"),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=True,
            open_world=True,
        )

    def execute(
        self,
        action_type: str = "",
        payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
        submit: bool = False,
    ) -> dict[str, Any]:
        action_type = str(action_type or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not action_type:
            return {"ok": False, "error": "action_type is required"}
        if not idempotency_key:
            return {"ok": False, "error": "idempotency_key is required"}

        proposal = {
            "action_type": action_type,
            "payload": payload or {},
            "idempotency_key": idempotency_key,
            "requires_confirmation": True,
        }
        if not submit:
            return {
                "ok": True,
                "submitted": False,
                "proposal": proposal,
                "message": "Action proposal created; submit requires confirmed MCP authorization.",
            }

        base_url = os.getenv("URA_ACTION_API_BASE", "").rstrip("/")
        token = os.getenv("URA_ACTION_API_TOKEN", "")
        if not base_url or not token:
            return {
                "ok": False,
                "submitted": False,
                "configured": False,
                "proposal": proposal,
                "error": "URA action API is not configured",
            }

        body = json.dumps(proposal).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/actions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - configured URL only
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "submitted": False,
                "configured": True,
                "status": exc.code,
                "proposal": proposal,
                "error": "URA action API error",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "submitted": False,
                "configured": True,
                "proposal": proposal,
                "error": f"URA action submit failed: {type(exc).__name__}",
            }
        return {"ok": True, "submitted": True, "proposal": proposal, "result": result}


ToolRegistry.register(UraActionProposalTool())
