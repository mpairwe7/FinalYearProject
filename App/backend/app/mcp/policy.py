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

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

READ_ROLES = ("verified_taxpayer", "ura_staff", "ura_admin", "ura_auditor")
WRITE_ROLES = ("verified_taxpayer", "ura_staff", "ura_admin")
KNOWN_RISKS = ("low", "medium", "high", "critical")

MAX_TRANSACTION_CEILING_UGX = float(os.getenv("MAX_TRANSACTION_CEILING_UGX", "50000000.0"))
MCP_MAX_CRITICAL_ACTIONS_PER_HOUR = int(os.getenv("MCP_MAX_CRITICAL_ACTIONS_PER_HOUR", "10"))

# Thread-safe sliding-window velocity tracker for critical actions
_velocity_lock = threading.Lock()
_user_action_history: dict[str, list[float]] = {}


def reset_velocity_tracker() -> None:
    """Clear velocity history (useful for test isolation)."""
    with _velocity_lock:
        _user_action_history.clear()


def _check_and_record_velocity(user_id: str, limit: int = MCP_MAX_CRITICAL_ACTIONS_PER_HOUR, window_s: float = 3600.0) -> bool:
    if not user_id or limit <= 0:
        return True
    now = time.time()
    with _velocity_lock:
        history = [t for t in _user_action_history.get(user_id, []) if now - t < window_s]
        if len(history) >= limit:
            _user_action_history[user_id] = history
            return False
        history.append(now)
        _user_action_history[user_id] = history
        return True


def _extract_amount_from_args(arguments: dict[str, Any] | None) -> float | None:
    """Extract financial transaction amounts from tool argument payload."""
    if not arguments or not isinstance(arguments, dict):
        return None
    for key in ("amount_ugx", "amount", "total_ugx", "total", "payment_amount", "gross_amount"):
        val = arguments.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
        if isinstance(val, str):
            clean = val.replace(",", "").replace("UGX", "").replace("ugx", "").strip()
            try:
                parsed = float(clean)
                if parsed > 0:
                    return parsed
            except ValueError:
                pass
    return None

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
    arguments: dict[str, Any] | None = None,
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

    # 2026 Blast Radius & Monetary Ceilings (NIST / OWASP LLM06)
    amount_ugx = _extract_amount_from_args(arguments)
    if amount_ugx is not None and amount_ugx > MAX_TRANSACTION_CEILING_UGX:
        if user_role != "ura_admin":
            reasons.append(
                f"transaction amount UGX {amount_ugx:,.0f} exceeds session ceiling "
                f"(UGX {MAX_TRANSACTION_CEILING_UGX:,.0f}); requires supervisor approval"
            )

    needs_confirmation = (
        requires_confirmation if requires_confirmation is not None else normalized_risk == "critical"
    )
    if needs_confirmation:
        if not confirmed:
            reasons.append("explicit user confirmation required")
        if not idempotency_key:
            reasons.append("idempotency_key required")
        if confirmed and user_id:
            if not _check_and_record_velocity(user_id):
                reasons.append(
                    f"critical action velocity limit exceeded (max {MCP_MAX_CRITICAL_ACTIONS_PER_HOUR} per hour)"
                )

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
