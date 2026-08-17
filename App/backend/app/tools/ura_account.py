"""Fail-closed URA account read interface.

This is the in-process implementation behind the MCP abstraction. It only
returns live account data when a real URA account API base URL and token are
configured; otherwise it fails closed so the assistant cannot fabricate account
balances, filings, or TIN status.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import Tool, ToolRegistry, ToolSchema


class UraAccountProfileTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ura_account_profile",
            description=(
                "Fetch authenticated taxpayer account profile/status from the configured "
                "URA account API. Requires verified auth, ura_account_access consent, "
                "and production API credentials."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "taxpayer_id": {
                        "type": "string",
                        "description": "Authenticated taxpayer identifier or TIN.",
                    }
                },
                "required": ["taxpayer_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "configured": {"type": "boolean"},
                    "profile": {"type": "object"},
                    "error": {"type": "string"},
                },
            },
            risk="high",
            namespace="ura_account",
            required_scopes=("ura_account_access",),
            allowed_roles=(
                "verified_taxpayer",
                "ura_staff",
                "ura_admin",
                "ura_auditor",
            ),
            open_world=True,

        )

    def execute(self, taxpayer_id: str = "") -> dict[str, Any]:
        taxpayer_id = str(taxpayer_id or "").strip()
        if not taxpayer_id:
            return {"ok": False, "error": "taxpayer_id is required"}

        base_url = os.getenv("URA_ACCOUNT_API_BASE", "").rstrip("/")
        token = os.getenv("URA_ACCOUNT_API_TOKEN", "")
        if not base_url or not token:
            return {
                "ok": False,
                "configured": False,
                "error": "URA account API is not configured",
            }

        url = f"{base_url}/taxpayers/{urllib.parse.quote(taxpayer_id)}/profile"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - configured URL only
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {"ok": False, "configured": True, "status": exc.code, "error": "URA API error"}
        except Exception as exc:  # noqa: BLE001 - fail closed and structured
            return {
                "ok": False,
                "configured": True,
                "error": f"URA account lookup failed: {type(exc).__name__}",
            }

        return {"ok": True, "configured": True, "profile": payload}


ToolRegistry.register(UraAccountProfileTool())
