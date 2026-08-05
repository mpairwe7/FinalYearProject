"""Which taxpayers should be reminded of which deadline, and when.

Phase 20's first step. Everything up to now has been reactive: the
taxpayer asks and the system answers. A reminder is the opposite — the
system reaches out — and that is a different kind of act.

Reaching a taxpayer unasked is a **new processing purpose**, not a new
feature on an existing one. Consent to personalisation is not consent to
be contacted, so this gates on its own purpose, ``deadline_reminders``,
and the gate is the first thing the selector does rather than a check a
caller is trusted to remember.

This module decides *who and what*. It deliberately does not send
anything: email and SMS need provider credentials and a deliverability
story, and a selector that is pure and offline can be tested
exhaustively while a sender cannot. :func:`due_reminders` is the seam a
channel plugs into.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The consent purpose that permits proactive contact. Distinct from
#: ``personalization`` on purpose: agreeing to a tailored answer is not
#: agreeing to be messaged.
REMINDER_PURPOSE = "deadline_reminders"

#: How far ahead to warn, by default. Three days is enough to act on a
#: monthly return without being so early it is forgotten.
DEFAULT_LEAD_DAYS = 3

#: Ceiling on lead time. A reminder a month early is noise, and noise is
#: how a channel gets muted.
MAX_LEAD_DAYS = 30


@dataclass
class Reminder:
    """One deadline a specific taxpayer should hear about."""

    deadline_name: str
    description: str
    due_date: str
    days_until: int
    tax_types: tuple[str, ...] = ()
    #: Populated from the user's profile so a channel does not have to
    #: re-read it; empty when the profile has no display name.
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "deadline_name": self.deadline_name,
            "description": self.description,
            "due_date": self.due_date,
            "days_until": self.days_until,
            "tax_types": list(self.tax_types),
            "display_name": self.display_name,
        }

    def message(self) -> str:
        """Plain-language text a channel can send as-is."""
        when = (
            "today"
            if self.days_until == 0
            else "tomorrow"
            if self.days_until == 1
            else f"in {self.days_until} days"
        )
        lead = f"{self.display_name}, y" if self.display_name else "Y"
        return (
            f"{lead}our {self.deadline_name} filing is due {when} "
            f"({self.due_date}). {self.description}"
        )


@dataclass
class ReminderPreferences:
    """What a taxpayer has asked for. Defaults are the quiet ones."""

    enabled: bool = False
    lead_days: int = DEFAULT_LEAD_DAYS
    #: Empty means "every deadline that applies to me"; a non-empty list
    #: narrows it to those tax types.
    tax_types: tuple[str, ...] = ()
    channels: tuple[str, ...] = ("in_app",)

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> ReminderPreferences:
        """Read preferences off a user profile, clamping bad values.

        A profile written by an older client will not have these keys;
        the defaults are deliberately the quiet ones, so a missing
        preference never turns messaging *on*.
        """
        data = (profile or {}).get("reminder_preferences") or {}
        if not isinstance(data, dict):
            return cls()
        try:
            lead = int(data.get("lead_days", DEFAULT_LEAD_DAYS))
        except (TypeError, ValueError):
            lead = DEFAULT_LEAD_DAYS
        tax_types = data.get("tax_types") or []
        channels = data.get("channels") or ["in_app"]
        return cls(
            enabled=bool(data.get("enabled", False)),
            lead_days=max(0, min(lead, MAX_LEAD_DAYS)),
            tax_types=tuple(str(t).lower() for t in tax_types if str(t).strip()),
            channels=tuple(str(c) for c in channels if str(c).strip()) or ("in_app",),
        )


@dataclass
class SelectionResult:
    """What was selected, and why nothing was when nothing was."""

    reminders: list[Reminder] = field(default_factory=list)
    skipped_reason: str = ""

    def __bool__(self) -> bool:
        return bool(self.reminders)


def _applies_to(deadline_scope: str, wanted: tuple[str, ...]) -> bool:
    """Whether a deadline's scope matches the tax types a user cares about."""
    if not wanted:
        return True  # no filter set — everything that applies to them
    scope = (deadline_scope or "all").strip().lower()
    if scope == "all":
        return True
    return scope in wanted


def due_reminders(
    user_id: str,
    *,
    profile: dict[str, Any] | None = None,
    today: _dt.date | None = None,
    tenant_id: str = "default",
) -> SelectionResult:
    """Reminders this taxpayer should receive today.

    Returns an empty result with a reason rather than raising, so a
    scheduler iterating thousands of users cannot be stopped by one bad
    profile. The reason is what makes "nobody was reminded" debuggable
    instead of silent.
    """
    from . import database as db
    from .tools.calendar import upcoming_deadlines

    if not user_id:
        return SelectionResult(skipped_reason="no user id")

    # Consent first. Not a check the caller is trusted to have done:
    # reaching a taxpayer unasked is the act being authorised, so the
    # authorisation belongs at the point of selection.
    try:
        consented = db.has_active_consent(user_id, REMINDER_PURPOSE, tenant_id)
    except Exception:
        logger.exception("reminder consent lookup failed for %s", user_id[:8])
        return SelectionResult(skipped_reason="consent lookup failed")
    if not consented:
        return SelectionResult(skipped_reason="no consent for deadline_reminders")

    preferences = ReminderPreferences.from_profile(profile)
    if not preferences.enabled:
        return SelectionResult(skipped_reason="reminders not enabled")

    wanted = preferences.tax_types or tuple(
        str(t).lower() for t in (profile or {}).get("registered_tax_types", []) or []
    )
    display_name = str((profile or {}).get("display_name", "") or "")
    day = today or _dt.date.today()

    try:
        upcoming = upcoming_deadlines(within_days=preferences.lead_days, today=day)
    except Exception:
        logger.exception("deadline lookup failed")
        return SelectionResult(skipped_reason="deadline lookup failed")

    reminders = [
        Reminder(
            deadline_name=item["name"],
            description=item.get("description", ""),
            due_date=item["date"],
            days_until=int(item.get("days_until", 0)),
            tax_types=wanted,
            display_name=display_name,
        )
        for item in upcoming
        if _applies_to(str(item.get("scope", "all")), wanted)
    ]
    if not reminders:
        return SelectionResult(skipped_reason="no deadline within the lead window")
    return SelectionResult(reminders=reminders)
