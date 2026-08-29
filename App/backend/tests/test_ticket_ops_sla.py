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


def test_unassigned_counts_the_live_queue_not_a_page_of_it() -> None:
    """The overview's "Unassigned" tile reads this.

    It used to be derived in the browser from the twenty rows that page
    loads, so a queue of thousands of unclaimed cases reported twenty.
    It belongs here, beside the other counts taken over the live
    open+assigned population rather than the period.
    """
    now = 2_000_000.0
    opened = [
        {"created_at": now - 100, "first_response_at": 0, "reply_at": 0,
         "status": "open", "assignee": ""},
        {"created_at": now - 200, "first_response_at": 0, "reply_at": 0,
         "status": "open", "assignee": None},
        {"created_at": now - 300, "first_response_at": now - 250, "reply_at": 0,
         "status": "assigned", "assignee": "officer@ura.go.ug"},
    ]
    stats = compose_sla_stats(period_rows=[], open_rows=opened, days=30, now=now)
    assert stats["unassigned"] == 2


def test_unassigned_reads_positionally_for_the_postgres_tuples() -> None:
    """Postgres rows arrive as tuples, so the column order is the contract.

    ``SELECT created_at, first_response_at, reply_at, status, assignee`` —
    assignee last, because the first four are already read by index.
    """
    now = 2_000_000.0
    opened = [
        (now - 100, 0.0, 0.0, "open", ""),
        (now - 200, now - 150, 0.0, "assigned", "officer@ura.go.ug"),
    ]
    stats = compose_sla_stats(period_rows=[], open_rows=opened, days=30, now=now)
    assert stats["unassigned"] == 1
    assert stats["awaiting_first_response"] == 1


def test_a_row_without_an_assignee_column_does_not_raise() -> None:
    """A short row reads as unassigned rather than taking the endpoint down."""
    now = 2_000_000.0
    opened = [(now - 100, 0.0, 0.0, "open")]
    stats = compose_sla_stats(period_rows=[], open_rows=opened, days=30, now=now)
    assert stats["unassigned"] == 1


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


def test_feedback_summary_returns_the_question_it_promises(isolated_db) -> None:
    """/analytics shows this under "Taxpayer question".

    The column is stored (and PII-redacted) on write, but the read query
    selected ``message_id`` instead, so the console rendered an empty
    cell beside a note promising the question verbatim.
    """
    isolated_db.save_feedback(
        message_id="m-1",
        rating="down",
        comment="not what I asked",
        user_query="What is the VAT registration threshold?",
    )
    recent = isolated_db.get_feedback_summary(days=30)["recent"]
    assert len(recent) == 1
    assert recent[0]["user_query"] == "What is the VAT registration threshold?"
    # message_id stays: the feedback row is keyed on it.
    assert recent[0]["message_id"] == "m-1"


def test_sla_stats_reads_unassigned_from_the_table(isolated_db) -> None:
    """End to end over SQLite, not just the pure composer."""
    claimed = isolated_db.create_ticket(reason="claimed", user_query="a")
    isolated_db.create_ticket(reason="unclaimed", user_query="b")
    isolated_db.update_ticket(claimed["id"], assignee="officer@ura.go.ug", status="assigned")
    assert isolated_db.sla_stats(days=30)["unassigned"] == 1


def test_heartbeat_lists_the_viewer_and_expires(isolated_db) -> None:
    ticket = isolated_db.create_ticket(reason="presence")
    isolated_db.heartbeat_ticket_presence(ticket["id"], "officer@ura.go.ug")
    assert isolated_db.list_ticket_viewers(ticket["id"]) == ["officer@ura.go.ug"]
    assert isolated_db.list_ticket_viewers(ticket["id"], max_age=-1) == []
