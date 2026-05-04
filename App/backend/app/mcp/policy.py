"""Dispatch-time authorization policy for MCP tool calls."""

from __future__ import annotations

from typing import Any

_READ_ROLES = {"verified_taxpayer", "ura_staff", "ura_admin", "ura_auditor"}
_WRITE_ROLES = {"verified_taxpayer", "ura_staff", "ura_admin"}
_KNOWN_RISKS = {"low", "medium", "high", "critical"}


def authorize_tool_call(
    *,
    name: str,
    risk: str,
    user_role: str = "public",
    granted_purposes: list[str] | None = None,
    user_id: str = "",
    tenant_id: str = "default",
    confirmed: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Authorize a tool call, returning a serialisable policy decision."""
    purposes = set(granted_purposes or [])
    normalized_risk = risk if risk in _KNOWN_RISKS else "unknown"
    reasons: list[str] = []

    if normalized_risk == "unknown":
        reasons.append(f"unknown risk tier '{risk}'")

    if normalized_risk in {"high", "critical"}:
        if not user_id:
            reasons.append("authenticated user required")
        if user_role not in _READ_ROLES:
            reasons.append(f"role '{user_role}' cannot access URA account tools")
        if name.startswith("ura_") and "ura_account_access" not in purposes:
            reasons.append("ura_account_access consent required")

    if normalized_risk == "critical":
        if user_role not in _WRITE_ROLES:
            reasons.append(f"role '{user_role}' cannot execute URA actions")
        if "ura_actions" not in purposes:
            reasons.append("ura_actions consent required")
        if not confirmed:
            reasons.append("explicit user confirmation required")
        if not idempotency_key:
            reasons.append("idempotency_key required")

    return {
        "allowed": not reasons,
        "reasons": reasons,
        "tool_name": name,
        "risk": normalized_risk,
        "user_role": user_role,
        "tenant_id": tenant_id or "default",
        "requires_confirmation": normalized_risk == "critical",
    }
