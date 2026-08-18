"""Population SLA clocks and collision presence."""

from __future__ import annotations

import sqlite3
import time

import pytest

from app.database import compose_sla_stats


def test_breach_counts_use_the_open_population() -> None:
    now = 2_000_000.0
    period = [
        {"created_at": now - 100, "first_response_at": now - 50, "resolved_at": 0, "reply_at": 0},
    ]
    opened = [
        {"created_at": now - 30 * 3600, "first_response_at": 0, "reply_at": 0, "status": "open"},
        {
            "created_at": now - 80 * 3600,
            "first_response_at": now - 70 * 3600,
            "reply_at": now - 26 * 3600,
            "status": "assigned",
        },
    ]
    stats = compose_sla_stats(period_rows=period, open_rows=opened, days=30, now=now)
    assert stats["breaching_first_response"] == 1
    assert stats["breaching_next_reply"] == 1
    assert stats["breaching"] == 2
    assert stats["awaiting_first_response"] == 1
    assert stats["awaiting_next_response"] == 1


def test_a_fresh_open_ticket_is_not_a_breach() -> None:
    now = time.time()
    opened = [{"created_at": now - 600, "first_response_at": 0, "reply_at": 0, "status": "open"}]
    stats = compose_sla_stats(period_rows=[], open_rows=opened, days=30, now=now)
    assert stats["breaching"] == 0
    assert stats["awaiting_first_response"] == 1


@pytest.fixture
def isolated_db(monkeypatch: pytest.MonkeyPatch):
    from app import database as db

    conn = sqlite3.connect(":memory:", timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_get_connection", lambda: conn)
    db.init_db()
    yield db
    conn.close()


def test_flag_overrides_survive_a_reload(isolated_db) -> None:
    isolated_db.save_flag_override("hyde", True)
    loaded = isolated_db.load_flag_overrides()
    assert loaded["hyde"] is True
    isolated_db.clear_flag_override("hyde")
    assert "hyde" not in isolated_db.load_flag_overrides()


def test_heartbeat_lists_the_viewer_and_expires(isolated_db) -> None:
    ticket = isolated_db.create_ticket(reason="presence")
    isolated_db.heartbeat_ticket_presence(ticket["id"], "officer@ura.go.ug")
    assert isolated_db.list_ticket_viewers(ticket["id"]) == ["officer@ura.go.ug"]
    assert isolated_db.list_ticket_viewers(ticket["id"], max_age=-1) == []
