"""Reminder channels (G14). In-app is live; email/SMS write a mock outbox.

No SES or Africa's Talking call is made. Rows are labeled ``provider=mock``
so a later sender can drain the outbox without this module inventing a
delivery network.
"""

from __future__ import annotations

import logging
from typing import Any

from .reminders import Reminder, ReminderPreferences

logger = logging.getLogger(__name__)

SUPPORTED = frozenset({"in_app", "email", "sms"})


def dispatch(
    user_id: str,
    reminder: Reminder,
    *,
    channels: tuple[str, ...] = ("in_app",),
) -> list[dict[str, Any]]:
    from . import database as db

    written: list[dict[str, Any]] = []
    for raw in channels:
        channel = str(raw or "").strip().lower()
        if channel not in SUPPORTED:
            continue
        if channel == "in_app":
            continue
        row = db.enqueue_notification(
            user_id=user_id,
            channel=channel,
            provider="mock",
            payload={
                "deadline_name": reminder.deadline_name,
                "due_date": reminder.due_date,
                "message": reminder.message(),
                "live": False,
            },
        )
        if row:
            written.append(row)
    return written


def dispatch_selection(
    user_id: str,
    reminders: list[Reminder],
    preferences: ReminderPreferences,
) -> int:
    count = 0
    for item in reminders:
        count += len(dispatch(user_id, item, channels=preferences.channels))
    return count
