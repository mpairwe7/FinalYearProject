"""The Postgres backend must return the same shape as SQLite.

Signature parity was not enough. ``postgres.get_ticket`` selected an
explicit column list that had drifted from the table it queries: the
``tickets`` DDL grew a ``user_id`` column and the SELECT list did not,
so on the backend production mandates the field simply vanished from
every ticket dict. Nothing raised — a caller doing ``ticket["user_id"]``
got a KeyError at runtime, and the officer view was quietly missing it.

A signature check cannot see that. Comparing the declared columns
against the declared SELECT list can, and needs no database.

The live round-trip check runs only when ``POSTGRES_DSN`` is set — the
CI runner has no Postgres, but a developer with one gets the stronger
assertion for free.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app import postgres as pg

#: Columns held back from a SELECT on purpose, with the reason.
_INTENTIONALLY_UNSELECTED = {
    # The queue view must not ship a full conversation per row; the
    # detail view (_TICKET_COLUMNS_FULL) does select it.
    "transcript_json",
}


def _declared_columns(table: str) -> set[str]:
    """Column names from the CREATE TABLE block plus later ADD COLUMNs."""
    source = Path(pg.__file__).read_text()
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n    \);",
        source,
        re.S,
    )
    assert match, f"no CREATE TABLE for {table} in postgres.py"
    columns = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        name = line.split()[0]
        # A column line is `name TYPE ...`.  Constraint continuation
        # lines ("CHECK(status IN (...))", "UNIQUE(a, b)") are not
        # columns, and their first token is not a bare identifier.
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name, re.I):
            continue
        if name.upper() in {"CHECK", "UNIQUE", "PRIMARY", "FOREIGN", "REFERENCES", "CONSTRAINT"}:
            continue
        columns.add(name)
    columns |= set(
        re.findall(rf"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS (\w+)", source)
    )
    return columns


def _select_list(constant: str) -> set[str]:
    return {c.strip() for c in getattr(pg, constant).split(",") if c.strip()}


class TestTicketColumnParity:
    def test_the_detail_select_covers_every_declared_column(self):
        declared = _declared_columns("tickets")
        selected = _select_list("_TICKET_COLUMNS_FULL")
        missing = sorted(declared - selected)
        assert not missing, (
            f"tickets columns never selected by get_ticket: {missing}. "
            "A column added to the DDL but not to the SELECT list vanishes "
            "from every ticket dict on Postgres."
        )

    def test_the_queue_select_holds_back_only_what_it_means_to(self):
        declared = _declared_columns("tickets")
        selected = _select_list("_TICKET_COLUMNS")
        held_back = declared - selected
        assert held_back == _INTENTIONALLY_UNSELECTED, (
            f"queue view column drift: {sorted(held_back)}"
        )

    def test_the_select_lists_name_no_column_that_does_not_exist(self):
        declared = _declared_columns("tickets")
        phantom = sorted(_select_list("_TICKET_COLUMNS_FULL") - declared)
        assert not phantom, f"SELECT names columns not in the DDL: {phantom}"

    def test_conversations_carries_what_log_conversation_writes(self):
        # The same class of bug one table over: log_conversation was
        # missing `contexts` and `user_id` entirely.
        declared = _declared_columns("conversations")
        for column in ("contexts", "user_id", "conversation_id"):
            assert column in declared, f"conversations.{column} missing on Postgres"


@pytest.mark.skipif(
    not os.getenv("POSTGRES_DSN"),
    reason="set POSTGRES_DSN to run the live backend round-trip",
)
class TestLiveBackendRoundTrip:
    """Executes the real SQL. Only runs where a Postgres is available."""

    def test_a_ticket_survives_a_write_read_round_trip(self):
        pg.init_db()
        conversation = f"conv-{os.getpid()}"
        pg.log_conversation(
            session_id="s",
            conversation_id=conversation,
            user_message="hello",
            bot_reply="hi",
            contexts="[]",
            user_id="sub-1",
        )
        transcript = pg.get_conversation_transcript(conversation_id=conversation)
        assert len(transcript) == 1

        created = pg.create_ticket(
            reason="human requested",
            conversation_id=conversation,
            transcript=transcript,
            user_id="sub-1",
            priority="high",
        )
        fetched = pg.get_ticket(created["id"])
        assert fetched is not None
        assert fetched["user_id"] == "sub-1"
        assert len(fetched["transcript"]) == 1

    def test_the_two_backends_return_the_same_keys(self):
        import sqlite3

        from app import database as db

        pg.init_db()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        original = db._get_connection
        db._get_connection = lambda: conn
        try:
            db.init_db()
            sqlite_ticket = db.get_ticket(db.create_ticket(reason="x")["id"])
        finally:
            db._get_connection = original
        pg_ticket = pg.get_ticket(pg.create_ticket(reason="x")["id"])
        assert sqlite_ticket is not None and pg_ticket is not None
        assert set(sqlite_ticket) == set(pg_ticket), (
            f"backend shape drift — sqlite-only: "
            f"{sorted(set(sqlite_ticket) - set(pg_ticket))}, "
            f"postgres-only: {sorted(set(pg_ticket) - set(sqlite_ticket))}"
        )
