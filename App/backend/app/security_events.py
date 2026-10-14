"""Security events in the OWASP Logging Vocabulary.

Authentication failures, authorisation denials and rate-limit hits used to
reach the logs, if at all, as free-text INFO lines ("JWT rejected: …") that
no alert could select. Each is now one structured WARNING record with a fixed
``event`` name from the OWASP Logging Vocabulary and one increment of
``ura_security_events_total{event}``, which the ``SecurityEventSpike`` alert
watches (OWASP ASVS 5.0 V16).

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
    429: "excess_rate_limit_exceeded",
}


def record_security_event(event: str, *, reason: str = "", **fields: Any) -> None:
    """Log and count one security event."""
    from .analytics import metrics

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
