"""Tell someone a ticket exists.

Until now the escalation pipeline ended at a database row. Nothing
forwarded it: an officer had to poll ``GET /v1/admin/tickets`` and
notice. A taxpayer told "a human will follow up" had no one waiting on
the other side, and the queue's oldest, angriest tickets were exactly
the ones nobody had been told about.

Two rules shape this module.

**The notification never carries the transcript.** It carries the
ticket id, its priority, topic and sentiment — enough to decide who
picks it up and how urgently. The conversation itself stays behind the
authenticated admin API, because a webhook is an external system and
the transcript is the taxpayer's tax affairs. Pushing it off-platform
to make an officer's life marginally easier is the wrong trade, and it
would put PII somewhere the erasure path cannot reach.

**Delivery failure never costs the ticket.** The row is committed
before this runs, so a dead webhook degrades to "the ticket is in the
queue, nobody was paged" — the pre-existing behaviour — rather than a
lost escalation. Dispatch happens on a daemon thread so a slow endpoint
cannot delay the taxpayer's reply.

Environment:
    ESCALATION_WEBHOOK_URL      – POST target; unset disables delivery
    ESCALATION_WEBHOOK_TOKEN    – bearer token, sent as a header
    ESCALATION_WEBHOOK_TIMEOUT  – seconds (default 5)
    ESCALATION_WEBHOOK_MIN_PRIORITY – lowest priority to send (default 'normal')
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

#: Ordered low → high so a minimum-priority filter is a comparison.
PRIORITY_ORDER: tuple[str, ...] = ("low", "normal", "high", "urgent")
_PRIORITY_RANK = {name: i for i, name in enumerate(PRIORITY_ORDER)}

_TIMEOUT_S = float(os.getenv("ESCALATION_WEBHOOK_TIMEOUT", "5"))

#: Keys allowed out of the building.  An allowlist rather than a
#: blocklist: a field added to the ticket later must be opted in
#: deliberately, not leak because nobody remembered to exclude it.
_NOTIFY_FIELDS = (
    "id",
    "priority",
    "status",
    "reason",
    "conversation_id",
    "created_at",
    "team",
)
_HANDOFF_FIELDS = ("topic", "sentiment", "transfer_style", "turns_before_handoff", "summary")


#: Which team owns each handoff topic.  ``_handoff_topic`` already
#: classifies every escalation; nothing acted on it, so an officer
#: watching the queue saw customs disputes next to TIN registrations and
#: had to triage by reading. Overridable per topic with
#: ``ESCALATION_TEAM_<TOPIC>`` so a deployment can match its own org
#: chart without a code change.
_DEFAULT_TEAMS: dict[str, str] = {
    "objection_or_dispute": "disputes",
    "account_specific": "taxpayer_accounts",
    "customs": "customs",
    "registration": "registration",
    "general_tax_support": "general",
}

#: Where a topic we do not recognise goes.  Never drop a ticket on the
#: floor because the classifier returned something new.
FALLBACK_TEAM = "general"


def team_for_topic(topic: str) -> str:
    """Team that should pick up an escalation classified as *topic*."""
    key = (topic or "").strip().lower()
    override = os.getenv(f"ESCALATION_TEAM_{key.upper()}", "").strip()
    if override:
        return override
    return _DEFAULT_TEAMS.get(key, FALLBACK_TEAM)


def known_teams() -> list[str]:
    """Distinct team names in effect, for the admin filter."""
    return sorted({team_for_topic(topic) for topic in _DEFAULT_TEAMS} | {FALLBACK_TEAM})


def _webhook_url() -> str:
    return os.getenv("ESCALATION_WEBHOOK_URL", "").strip()


def _min_priority() -> str:
    value = os.getenv("ESCALATION_WEBHOOK_MIN_PRIORITY", "normal").strip().lower()
    return value if value in _PRIORITY_RANK else "normal"


def meets_priority(priority: str, minimum: str | None = None) -> bool:
    """Whether *priority* is at or above the configured floor."""
    floor = _PRIORITY_RANK.get(minimum or _min_priority(), 1)
    return _PRIORITY_RANK.get((priority or "normal").lower(), 1) >= floor


def build_payload(ticket: dict[str, Any]) -> dict[str, Any]:
    """Metadata an officer needs to triage, and nothing more.

    Deliberately excludes ``transcript``, ``user_query`` and
    ``bot_reply`` — those are the taxpayer's words and stay behind the
    admin API. ``summary`` is the one piece of conversation-derived text
    included, because triage without it is guesswork; it is already
    PII-redacted upstream by ``redact_for_storage``.
    """
    handoff = ticket.get("handoff") or {}
    payload = {key: ticket.get(key) for key in _NOTIFY_FIELDS if ticket.get(key) is not None}
    payload.update(
        {key: handoff[key] for key in _HANDOFF_FIELDS if handoff.get(key) is not None}
    )
    payload["event"] = "escalation.created"
    return payload


def _post(payload: dict[str, Any], url: str) -> None:
    """Deliver one notification.  Never raises."""
    try:
        import httpx

        headers = {"Content-Type": "application/json"}
        token = os.getenv("ESCALATION_WEBHOOK_TOKEN", "").strip()
        if token:
            # Header, never a query string: an httpx error carries the
            # request URL into logs and tracebacks.
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            response = client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            logger.warning(
                "escalation webhook rejected ticket %s (HTTP %s)",
                payload.get("id", "?"),
                response.status_code,
            )
        else:
            logger.info("escalation webhook delivered ticket %s", payload.get("id", "?"))
    except Exception:
        # The ticket is already committed; a failed page is a degraded
        # notification, not a lost escalation.
        logger.warning("escalation webhook failed for ticket %s", payload.get("id", "?"),
                       exc_info=True)


def _post_quietly(payload: dict[str, Any], url: str) -> None:
    """Thread target that cannot let anything escape.

    :func:`_post` already swallows transport errors, but it is the
    thread's entry point: a defect *in* it — or in anything it imports —
    would otherwise surface as an unhandled thread exception rather than
    a log line. Notification is best-effort by design; making that true
    at the boundary rather than assuming it costs three lines.
    """
    try:
        _post(payload, url)
    except Exception:
        logger.warning("escalation notification thread failed", exc_info=True)


def notify_ticket_created(ticket: dict[str, Any], *, blocking: bool = False) -> bool:
    """Announce a new ticket.  Returns whether a delivery was attempted.

    ``blocking`` exists for tests; the request path always dispatches on
    a daemon thread so a slow endpoint cannot hold up the taxpayer's
    reply.
    """
    url = _webhook_url()
    if not url:
        return False
    if not ticket.get("id"):
        return False
    if not meets_priority(str(ticket.get("priority", "normal"))):
        logger.debug("escalation webhook skipped ticket %s (below priority floor)", ticket["id"])
        return False

    payload = build_payload(ticket)
    if blocking:
        _post_quietly(payload, url)
        return True
    threading.Thread(
        target=_post_quietly,
        args=(payload, url),
        name=f"escalation-notify-{str(ticket['id'])[:8]}",
        daemon=True,
    ).start()
    return True
