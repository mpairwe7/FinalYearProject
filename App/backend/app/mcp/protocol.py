"""MCP 2026-07-28 wire constants and per-request ``_meta``.

The spec requires every client request to carry
``io.modelcontextprotocol/protocolVersion`` and
``io.modelcontextprotocol/clientCapabilities`` so a stateless server
can authorize without an ``initialize`` handshake. Vendor identity
stays under ``ug.go.ura.chatbot/``.
"""

from __future__ import annotations

from typing import Any

MCP_PROTOCOL_VERSION = "2026-07-28"
CLIENT_NAME = "ura-chatbot"

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"

DEFAULT_CLIENT_CAPABILITIES: dict[str, Any] = {
    "elicitation": {},
    "roots": {"listChanged": False},
}


def request_meta(
    *,
    tenant_id: str = "default",
    user_id: str = "",
    user_role: str = "public",
    call_id: str = "",
) -> dict[str, Any]:
    """The ``_meta`` block every stateless request carries."""
    return {
        META_PROTOCOL_VERSION: MCP_PROTOCOL_VERSION,
        META_CLIENT_INFO: {"name": CLIENT_NAME, "version": "2.0"},
        META_CLIENT_CAPABILITIES: dict(DEFAULT_CLIENT_CAPABILITIES),
        "ug.go.ura.chatbot/tenantId": tenant_id,
        "ug.go.ura.chatbot/userId": user_id,
        "ug.go.ura.chatbot/userRole": user_role,
        "ug.go.ura.chatbot/callId": call_id,
    }


def missing_required_meta(meta: dict[str, Any] | None) -> list[str]:
    """Return the spec-required ``_meta`` keys that are absent or empty."""
    payload = meta or {}
    missing: list[str] = []
    if not payload.get(META_PROTOCOL_VERSION):
        missing.append(META_PROTOCOL_VERSION)
    if not isinstance(payload.get(META_CLIENT_CAPABILITIES), dict):
        missing.append(META_CLIENT_CAPABILITIES)
    return missing
