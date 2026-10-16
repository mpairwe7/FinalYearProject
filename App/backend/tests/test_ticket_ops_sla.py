"""Population SLA clocks and collision presence."""

from __future__ import annotations

import sqlite3
import time

import pytest

from app.database import compose_sla_stats


@pytest.fixture
def desk_always_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desk that never closes: working time is wall time, for tests about the population."""
    monkeypatch.setenv("CXP_OPEN_HOUR", "0")
    monkeypatch.setenv("CXP_CLOSE_HOUR", "24")
    monkeypatch.delenv("CXP_WORKING_DAYS", raising=False)
    monkeypatch.delenv("CXP_CLOSED_DATES", raising=False)


@pytest.fixture
def default_desk(monkeypatch: pytest.MonkeyPatch) -> None:
    """07:00–23:00, every day, Kampala time."""
    for name in ("CXP_OPEN_HOUR", "CXP_CLOSE_HOUR", "CXP_WORKING_DAYS", "CXP_CLOSED_DATES", "CXP_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)


def _kampala(day: int, hour: int) -> float:
    """A moment in October 2026, Kampala time (UTC+3, no daylight saving)."""
    import datetime as dt

    return dt.datetime(2026, 10, day, hour, tzinfo=dt.timezone(dt.timedelta(hours=3))).timestamp()


@pytest.mark.usefixtures("default_desk")
def test_the_breach_clock_stops_while_the_desk_is_closed() -> None:
    """G197: a handoff raised at 22:00 has had one hour of an officer's time by 08:00 — not ten."""
    now = _kampala(17, 8)
    opened = [
        # Raised the evening before: 22:00–23:00 and 07:00–08:00 count, two hours.
        {"created_at": _kampala(16, 22), "first_response_at": 0, "reply_at": 0, "status": "open"},
        # Raised two days before: well over 24 working hours.
        {"created_at": _kampala(15, 9), "first_response_at": 0, "reply_at": 0, "status": "open"},
    ]
    stats = compose_sla_stats(period_rows=[], open_rows=opened, days=30, now=now)
    assert stats["breaching_first_response"] == 1
    assert stats["sla_clock"] == "officer_hours"
    assert stats["officer_hours"].startswith("07:00–23:00")


@pytest.mark.usefixtures("default_desk")
def test_the_next_reply_clock_stops_overnight_too() -> None:
    now = _kampala(17, 8)
    opened = [
        {
            "created_at": _kampala(16, 9),
            "first_response_at": _kampala(16, 10),
            "reply_at": _kampala(16, 22),  # the officer last wrote at 22:00
            "status": "assigned",
        },
    ]
    stats = compose_sla_stats(period_rows=[], open_rows=opened, days=30, now=now)
    assert stats["breaching_next_reply"] == 0  # two working hours since, not ten


@pytest.mark.usefixtures("desk_always_open")
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
