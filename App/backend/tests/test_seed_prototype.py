"""Deterministic prototype sample rows (never in production)."""

from __future__ import annotations

import sqlite3

import pytest

from app import database as db
from app.cms import lookup
from app.seed_prototype import seed, should_seed


@pytest.fixture
def mem_db(monkeypatch: pytest.MonkeyPatch):
    conn = sqlite3.connect(":memory:", timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_get_connection", lambda: conn)
    db.init_db()
    yield db
    conn.close()


def test_should_seed_false_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SEED_PROTOTYPE", "true")
    assert should_seed() is False


def test_should_seed_off_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SEED_PROTOTYPE", "false")
    assert should_seed() is False


def test_seed_loads_overrides_reminders_and_ticket(mem_db) -> None:
    first = seed()
    assert first["overrides"] == 2
    assert first["reminders"] == 2
    assert first["tickets"] == 1
    assert first["feedback"] == 1
    assert first["quality"] == 1
    hit = lookup("What is the VAT rate?")
    assert hit is not None
    assert "18%" in hit["reply"]
    user = db.upsert_user(external_id="sandbox-taxpayer")
    inbox = db.list_reminder_inbox(str(user["id"]))
    assert len(inbox) >= 2
    tickets = db.list_tickets(limit=20)
    assert any("Prototype" in (t.get("reason") or "") for t in tickets)
    second = seed()
    assert second["tickets"] == 0
    assert second["feedback"] == 0
    assert second["quality"] == 0
    # The JSON seed's ticket, and the quality-audit demo's officer chat.
    assert len(db.list_tickets(limit=50)) == 2


def test_the_quality_audit_demo_fills_every_tab_and_needs_review(mem_db) -> None:
    from app import audit_reviews, quality_audit, service_ratings

    seed()
    conv = quality_audit.ai_chat_timeline("proto-conv-went-wrong")
    assert conv is not None and (conv["pattern"], conv["break_answer"]) == ("went_wrong", 3)
    assert service_ratings.headline("chat_officer_csat", 30)["average"] == 2.0
    assert service_ratings.headline("call_ai_review_transfer", 30)["sentiment"]["negative"] == 1
    assert service_ratings.headline("call_officer_csat", 30)["answered"] == 1
    streams = {i["stream"] for i in audit_reviews.needs_review(30, status="open", limit=100)["items"]}
    assert streams == {"ai_chat", "officer_chat", "ai_call"}
