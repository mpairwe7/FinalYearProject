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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow HTTP redirects when carrying a URA API Bearer token (G431)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"Redirect refused ({code})", headers, fp)


_MAX_URA_API_RESPONSE_BYTES = 65_536


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
                "required": ["ok"],
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

    def execute(self, taxpayer_id: str = "", user_id: str = "", tenant_id: str = "default") -> dict[str, Any]:
        taxpayer_id = str(taxpayer_id or "").strip()
        if not taxpayer_id:
            return {"ok": False, "error": "taxpayer_id is required"}

        if user_id:
            import re as _re

            m_tin = _re.search(r"\b(\d{10})\b", str(user_id))
            if m_tin and m_tin.group(1) != taxpayer_id:
                return {
                    "ok": False,
                    "error": (
                        f"Access refused under Section 75 of the Tax Procedures Code Act (TPCA) Cap. 343 "
                        f"(Official Secrecy): session bound to TIN {m_tin.group(1)} cannot read TIN {taxpayer_id}."
                    ),
                }

        from ..ura_account_mock import account_mode, lookup_mock

        mode = account_mode()
        if mode == "mock":
            return lookup_mock(taxpayer_id)

        base_url = os.getenv("URA_ACCOUNT_API_BASE", "").rstrip("/")
        token = os.getenv("URA_ACCOUNT_API_TOKEN", "")
        parsed = urllib.parse.urlparse(base_url)
        if mode != "live" or parsed.scheme != "https" or not parsed.netloc or not token:
            return {
                "ok": False,
                "configured": False,
                "live": False,
                "source": "none",
                "mode": mode,
                "error": "URA account API is not configured",
            }

        url = f"{base_url}/taxpayers/{urllib.parse.quote(taxpayer_id)}/profile"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        opener = urllib.request.build_opener(_NoRedirectHandler)
        try:
            with opener.open(req, timeout=5) as resp:  # nosec B310 # noqa: S310 - configured HTTPS URL without redirects
                raw = resp.read(_MAX_URA_API_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_URA_API_RESPONSE_BYTES:
                    return {"ok": False, "configured": True, "error": "URA API response exceeded byte cap"}
                payload = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {"ok": False, "configured": True, "status": exc.code, "error": "URA API error"}
        except Exception as exc:  # noqa: BLE001 - fail closed and structured
            return {
                "ok": False,
                "configured": True,
                "error": f"URA account lookup failed: {type(exc).__name__}",
            }

        return {
            "ok": True,
            "configured": True,
            "live": True,
            "source": "ura_api",
            "mode": "live",
            "profile": payload,
        }


def account_api_status() -> dict[str, Any]:
    """Describe the account connector. Mock is never reported as live."""
    from ..ura_account_mock import account_mode, live_credentials_configured

    mode = account_mode()
    live = mode == "live" and live_credentials_configured()
    if mode == "mock":
        return {
            "configured": False,
            "live": False,
            "mode": "mock",
            "source": "mock",
            "error": None,
        }
    return {
        "configured": live,
        "live": live,
        "mode": mode,
        "source": "ura_api" if live else "none",
        "error": None if live else "URA account API is not configured",
    }


ToolRegistry.register(UraAccountProfileTool())
