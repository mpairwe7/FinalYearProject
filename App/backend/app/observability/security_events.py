"""Security events in the OWASP Logging Vocabulary.

Authentication failures, authorisation denials and rate-limit hits used to
reach the logs, if at all, as free-text INFO lines ("JWT rejected: …") that
no alert could select. Each is now one structured WARNING record with a fixed
``event`` name from the OWASP Logging Vocabulary and one increment of
``ura_security_events_total{event}`` (OWASP ASVS 5.0 V16). The alerts that
watch it are in ``monitoring/alerting-rules.yml`` (group ``ura-security``):
``AuthenticationFailureSpike``, ``AuthorizationFailureSpike``,
``GuardrailProbingCampaign`` and ``IndirectInjectionOrUntrustedLinkSpike``.
An event no rule selects still reaches the dashboard and the logs, but pages
no one.

Records carry the request id (added by the logging filter), the method, the
route template, the status and the authenticated actor when there is one.
They do not carry the client IP or the token.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger("app.security")

#: HTTP status -> OWASP Logging Vocabulary event.
EVENT_BY_STATUS = {
    401: "authn_login_fail",
    403: "authz_fail",
    413: "input_validation_payload_too_large",
    429: "excess_rate_limit_exceeded",
}

#: Bounded MCP security event kinds (OWASP ASVS 5.0 V16 / OWASP MCP Top 10).
MCP_SECURITY_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "mcp_integrity_fail",
        "mcp_output_blocked",
        "mcp_idempotency_conflict",
        "mcp_request_state_fail",
    }
)


#: Guardrail events (ADR-0029); the reason carries the channel.
GUARDRAIL_SECURITY_EVENTS: frozenset[str] = frozenset(
    {
        "guardrail_prompt_injection",
        "guardrail_harmful_intent",
        "guardrail_indirect_injection",
        "guardrail_untrusted_link",
        "guardrail_prompt_leakage",
        "guardrail_strike_cooldown",
    }
)

#: Every event name this module is given: a closed vocabulary, reserved at the
#: metrics series cap so no alert on one of them can be folded into silence.
SECURITY_EVENT_NAMES: frozenset[str] = (
    frozenset(EVENT_BY_STATUS.values()) | MCP_SECURITY_EVENT_KINDS | GUARDRAIL_SECURITY_EVENTS
)
_reserved = False


def record_mcp_security_event(
    kind: str,
    *,
    tool_name: str = "",
    namespace: str = "",
    reason: str = "",
    actor: str = "",
) -> None:
    """Record a bounded MCP boundary security event."""
    safe_kind = kind if kind in MCP_SECURITY_EVENT_KINDS else "mcp_integrity_fail"
    record_security_event(
        safe_kind,
        reason=reason,
        tool_name=tool_name,
        namespace=namespace,
        actor=actor,
    )


def record_security_event(event: str, *, reason: str = "", **fields: Any) -> None:
    """Log and count one security event."""
    global _reserved
    from .analytics import metrics

    if not _reserved:
        metrics.reserve("security_events_total", "event", SECURITY_EVENT_NAMES)
        _reserved = True
    metrics.inc("security_events_total", labels={"event": event})
    logger.warning(
        "security event %s%s",
        event,
        f" ({reason})" if reason else "",
        extra={"event": event, "security_reason": reason, **fields},
    )


def record_http_security_event(request: Request, status_code: int, route: str) -> None:
    """Record the event a 401, 403 or 429 response stands for (no-op otherwise)."""
    event = EVENT_BY_STATUS.get(status_code)
    if event is None:
        return
    ctx = getattr(request.state, "auth", None)
    actor = getattr(ctx, "user_id", "") if getattr(ctx, "authenticated", False) else ""
    record_security_event(
        event,
        reason=str(getattr(request.state, "security_reason", "") or ""),
        http_method=request.method,
        http_route=route,
        http_status=status_code,
        actor=actor,
    )
