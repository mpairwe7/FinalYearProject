"""Dispatch-time authorization policy for MCP tool calls.

The security boundary is here, not in discovery.  Discovery filtering
(``MCPClient.available_for``) shapes what the model is offered; this
function decides what actually runs, and it must hold even when a
prompt-injected model names a tool it was never shown.

Authorization is driven by what a tool **declares** about itself —
``required_scopes``, ``allowed_roles``, ``requires_confirmation`` — not
by pattern-matching its name.  The previous rule granted URA account
access to anything whose name started with ``ura_``, which is both
over-broad (any future ``ura_*`` tool inherits the grant) and
under-broad (a URA-touching tool named otherwise gets nothing).  When a
tool declares nothing and sits above the ``low`` risk tier, the risk
tier's defaults apply and the call is denied unless they are met.
"""

from __future__ import annotations

from typing import Any

READ_ROLES = ("verified_taxpayer", "ura_staff", "ura_admin", "ura_auditor")
WRITE_ROLES = ("verified_taxpayer", "ura_staff", "ura_admin")
KNOWN_RISKS = ("low", "medium", "high", "critical")

#: Fallback requirements for a tool that declares no roles of its own.
#: ``low`` is unrestricted; everything above it needs an authenticated
#: caller in an appropriate role.
_RISK_DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    "low": (),
    "medium": (),
    "high": READ_ROLES,
    "critical": WRITE_ROLES,
}


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
    required_scopes: tuple[str, ...] | None = None,
    allowed_roles: tuple[str, ...] | None = None,
    scope_exempt_roles: tuple[str, ...] = (),
    requires_confirmation: bool | None = None,
) -> dict[str, Any]:
    """Authorize a tool call, returning a serialisable policy decision.

    *required_scopes*, *allowed_roles* and *requires_confirmation* come
    from the tool's own :class:`~app.tools.ToolSchema`.  Passing ``None``
    for a field means "the tool declared nothing", and the risk tier's
    default applies.
    """
    purposes = set(granted_purposes or [])
    normalized_risk = risk if risk in KNOWN_RISKS else "unknown"
    reasons: list[str] = []

    if normalized_risk == "unknown":
        # An unrecognised tier is treated as the strictest one: an
        # unknown risk is not a licence to skip the checks.
        reasons.append(f"unknown risk tier '{risk}'")

    if normalized_risk in ("high", "critical", "unknown") and not user_id:
        reasons.append("authenticated user required")

    effective_roles = (
        tuple(allowed_roles)
        if allowed_roles
        else _RISK_DEFAULT_ROLES.get(normalized_risk, WRITE_ROLES)
    )
    if effective_roles and user_role not in effective_roles:
        reasons.append(f"role '{user_role}' cannot call '{name}' (allowed: {', '.join(effective_roles)})")

    if required_scopes and user_role not in scope_exempt_roles:
        for scope in required_scopes:
            if scope not in purposes:
                reasons.append(f"{scope} consent required")

    needs_confirmation = (
        requires_confirmation if requires_confirmation is not None else normalized_risk == "critical"
    )
    if needs_confirmation:
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
        "required_scopes": list(required_scopes or ()),
        "allowed_roles": list(effective_roles),
        "requires_confirmation": needs_confirmation,
    }
