"""Load deterministic prototype rows for local demo and tests.

Never runs when ``APP_ENV=production``. Idempotent on unique keys
(override query, reminder triple, feedback message_id).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ._root import PROJECT_ROOT

logger = logging.getLogger(__name__)

SEED_PATH = Path(
    os.getenv(
        "PROTOTYPE_SEED_PATH",
        str(PROJECT_ROOT / "Data" / "eval" / "prototype_seed.json"),
    )
)


def should_seed() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    if (os.getenv("APP_ENV") or "development").lower() == "production":
        return False
    raw = (os.getenv("SEED_PROTOTYPE") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def load_seed(path: Path | None = None) -> dict[str, Any]:
    target = path or SEED_PATH
    data = json.loads(target.read_text())
    if not isinstance(data, dict):
        raise ValueError("prototype seed must be a JSON object")
    return data


def seed(path: Path | None = None) -> dict[str, int]:
    from . import cms
    from . import database as db
    from .reminders import Reminder
    from .notify import dispatch

    payload = load_seed(path)
    user_spec = payload.get("user") or {}
    user = db.upsert_user(
        external_id=str(user_spec.get("external_id") or "sandbox-taxpayer"),
        email=str(user_spec.get("email") or ""),
        role=str(user_spec.get("role") or "verified_taxpayer"),
    )
    uid = str(user["id"])

    counts = {"overrides": 0, "reminders": 0, "outbox": 0, "tickets": 0, "feedback": 0}
    for row in payload.get("overrides") or []:
        cms.upsert(
            str(row.get("query") or ""),
            str(row.get("reply") or ""),
            source_url=str(row.get("source_url") or ""),
            created_by="prototype-seed",
        )
        counts["overrides"] += 1

    for row in payload.get("reminders") or []:
        db.upsert_reminder_inbox(
            uid,
            str(row.get("deadline_name") or ""),
            str(row.get("due_date") or ""),
            str(row.get("message") or ""),
        )
        counts["reminders"] += 1

    for row in payload.get("outbox") or []:
        channel = str(row.get("channel") or "email")
        if db.query_one(
            "SELECT 1 AS ok FROM notification_outbox WHERE user_id = ? AND channel = ? LIMIT 1",
            (uid, channel),
        ):
            continue
        reminder = Reminder(
            deadline_name="prototype",
            description=str(row.get("message") or ""),
            due_date="2026-08-20",
            days_until=2,
        )
        dispatch(uid, reminder, channels=(channel,))
        counts["outbox"] += 1

    for row in payload.get("tickets") or []:
        reason = str(row.get("reason") or "prototype")
        if db.query_one("SELECT 1 AS ok FROM tickets WHERE reason = ? LIMIT 1", (reason,)):
            continue
        db.create_ticket(
            reason=reason,
            user_query=str(row.get("user_query") or ""),
            priority=str(row.get("priority") or "normal"),
            user_id=uid,
        )
        counts["tickets"] += 1

    for row in payload.get("feedback") or []:
        mid = str(row.get("message_id") or "proto-fb")
        if db.query_one("SELECT 1 AS ok FROM feedback WHERE message_id = ? LIMIT 1", (mid,)):
            continue
        db.save_feedback(
            message_id=mid,
            rating=str(row.get("rating") or "down"),
            comment=str(row.get("comment") or ""),
            user_query=str(row.get("user_query") or ""),
            bot_reply=str(row.get("bot_reply") or ""),
        )
        counts["feedback"] += 1

    logger.info("prototype seed applied: %s", counts)
    return counts


def main() -> int:
    if (os.getenv("APP_ENV") or "").lower() == "production":
        print("refusing to seed in production")
        return 2
    print(json.dumps(seed(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
