"""Load deterministic prototype rows for local demo and tests.

Never runs when ``APP_ENV=production``. Idempotent on unique keys
(override query, reminder triple, feedback message_id, the fixed ids of the
quality-audit demo).

Besides the JSON seed, :func:`seed_quality_audit` gives the administrator's
service-quality audit (``/admin/quality``) something to read in a demo: a chat
that went wrong at its third answer, an officer chat rated 2/5, the AI's spoken
review on two calls, and an officer call — so every tab, and Needs review, has
an item on a fresh database.
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

    counts["quality"] = seed_quality_audit(uid)

    logger.info("prototype seed applied: %s", counts)
    return counts


#: The conversation the quality-audit demo rates: helpful, helpful, then not.
_DEMO_TURNS = (
    ("How do I register for VAT?", "Apply on the URA eTax portal under Registration, then VAT.", "up"),
    ("What documents do I need?", "Your TIN, certificate of incorporation and a recent bank statement.", "up"),
    ("What if I am a sole proprietor?", "Sole proprietors cannot register for VAT.", "down"),
)


def seed_quality_audit(uid: str) -> int:
    """Demo rows for every stream of the quality audit; returns 1 when written, 0 when already there."""
    from . import database as db
    from . import officer_chat, service_ratings
    from .receptionist.store import create_call, get_call, init_receptionist_schema, update_call

    # The demo calls need the call desk's tables, which init_db does not create here.
    init_receptionist_schema()
    if db.query_one("SELECT 1 AS ok FROM feedback WHERE message_id = ? LIMIT 1", ("proto-qa-1",)):
        return 0

    # AI answers in chat: went wrong at the third answer, with a comment.
    conv = "proto-conv-went-wrong"
    for n, (question, reply, rating) in enumerate(_DEMO_TURNS, start=1):
        turn_id = f"proto-turn-{n}"
        db.log_conversation(
            session_id="proto-session", conversation_id=conv, user_message=question, bot_reply=reply,
            sources='["vat-act.pdf"]', confidence=0.8 if rating == "up" else 0.4, topic_tag="vat_registration",
            locale="en", row_id=turn_id,
        )
        db.save_feedback(
            message_id=f"proto-qa-{n}", rating=rating,
            comment="Sole proprietors can register once they pass the threshold." if rating == "down" else "",
            user_query=question, bot_reply=reply, conversation_id=conv, turn_id=turn_id, turn_index=n,
            locale="en", topic_tag="vat_registration", sources_json='["vat-act.pdf"]',
        )

    # An officer chat the taxpayer rated 2 of 5.
    ticket = db.create_ticket(
        reason="Prototype: VAT refund chat", user_query="My VAT refund has not arrived.", user_id=uid,
        conversation_id="proto-conv-officer-chat", team="domestic_taxes",
    )
    officer = {"officer_id": "proto-officer-nakato", "officer_name": "Officer Nakato"}
    db.update_ticket(ticket["id"], status="assigned", assignee="nakato@ura.go.ug")
    ticket = db.get_ticket(ticket["id"]) or ticket
    officer_chat.note_officer(ticket, officer["officer_id"], officer["officer_name"], took_case=True)
    officer_chat.append_message(ticket, "officer", "Which period is the refund for?", **officer)
    officer_chat.append_message(ticket, "taxpayer", "March 2026.")
    officer_chat.end_chat(ticket, outcome="resolved", closing_message="It is being processed. Goodbye.", **officer)
    officer_chat.submit_csat(db.get_ticket(ticket["id"]) or ticket, score=2, comment="It took too long to get an answer.")

    # Calls: the AI's spoken review at goodbye and while transferring, and an officer call.
    calls = (
        ("proto-call-ai-end", "call_ai_review_end", {"status": "answered", "sentiment": "positive", "comment": "Yes, it was very helpful."}),
        ("proto-call-ai-transfer", "call_ai_review_transfer", {"status": "answered", "sentiment": "negative", "comment": "No, I needed a person."}),
        ("proto-call-officer", "call_officer_csat", {"status": "answered", "score": 4}),
    )
    for call_id, source, answer in calls:
        if get_call(call_id) is None:
            create_call(call_id, conversation_id=f"conv-{call_id}", user_id=uid, status="ended")
        extra = officer if source == "call_officer_csat" else {}
        if source == "call_officer_csat":
            update_call(call_id, outcome="resolved", wrapup_note="Explained the refund timeline.", target_team="domestic_taxes")
        service_ratings.request(
            source, call_id=call_id, channel="call", subject="officer" if extra else "ai",
            topic="vat_refund", locale="en", user_id=uid, team="domestic_taxes" if extra else "", **extra,
        )
        service_ratings.answer(source, call_id=call_id, **answer)
    return 1


def main() -> int:
    if (os.getenv("APP_ENV") or "").lower() == "production":
        print("refusing to seed in production")
        return 2
    print(json.dumps(seed(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
