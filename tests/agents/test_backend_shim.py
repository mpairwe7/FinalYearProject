"""The backend-agnostic query seam, and the audit ledger riding on it.

Modules that own their own tables — the audit ledger, semantic and
episodic memory, voice consent — reached ``database._get_connection()``
directly and wrote SQLite SQL against it. That bypasses the dispatch
block entirely, so on Postgres those tables stayed on a per-replica file
whatever the backend setting said.

The audit ledger is the sharp case. A hash chain split across pods
cannot be verified, and verification is the one thing it exists for.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app import database as db


class TestPlaceholderTranslation:
    def test_qmarks_become_pyformat(self):
        assert db._to_pg_placeholders("SELECT * FROM t WHERE a = ? AND b = ?") == (
            "SELECT * FROM t WHERE a = %s AND b = %s"
        )

    def test_sql_without_placeholders_is_unchanged(self):
        assert db._to_pg_placeholders("SELECT 1") == "SELECT 1"

    def test_no_migrated_module_puts_a_question_mark_inside_a_string_literal(self):
        """The translator rewrites every ``?``, including one inside quotes.

        ``WHERE note = 'why?'`` would become ``'why%s'`` and bind wrongly.
        Every ``?`` in these modules is a placeholder today; this pins
        that as the migration reaches the remaining modules.
        """
        offenders = []
        for name in ("audit/ledger.py", "audit/verifier.py"):
            source = (Path(db.__file__).parent / name).read_text()
            for literal in re.findall(r"'[^'\n]*'", source):
                if "?" in literal:
                    offenders.append(f"{name}: {literal}")
        assert not offenders, (
            f"'?' inside a SQL string literal would be rewritten as a "
            f"placeholder: {offenders}"
        )


class TestShimOnSqlite:
    def test_query_all_returns_dicts(self, tmp_db):
        tmp_db.create_ticket(reason="a", priority="high")
        rows = db.query_all("SELECT reason, priority FROM tickets")
        assert rows and rows[0]["reason"] == "a"
        assert rows[0]["priority"] == "high"

    def test_query_one_returns_none_when_empty(self, tmp_db):
        assert db.query_one("SELECT 1 AS n FROM tickets WHERE reason = ?", ("nope",)) is None

    def test_query_one_returns_the_first_row(self, tmp_db):
        tmp_db.create_ticket(reason="only")
        assert db.query_one("SELECT reason FROM tickets")["reason"] == "only"

    def test_execute_reports_affected_rows(self, tmp_db):
        created = tmp_db.create_ticket(reason="a")
        affected = db.execute(
            "UPDATE tickets SET staff_note = ? WHERE id = ?", ("noted", created["id"])
        )
        assert affected == 1

    def test_execute_script_creates_tables(self, tmp_db):
        db.execute_script(
            "CREATE TABLE IF NOT EXISTS shim_probe (a TEXT); "
            "CREATE INDEX IF NOT EXISTS idx_shim_probe ON shim_probe(a);"
        )
        assert db.query_all("SELECT * FROM shim_probe") == []

    def test_parameters_are_bound_not_interpolated(self, tmp_db):
        # A bound parameter must never be parsed as SQL.
        tmp_db.create_ticket(reason="safe")
        rows = db.query_all(
            "SELECT reason FROM tickets WHERE reason = ?", ("safe'; DROP TABLE tickets;--",)
        )
        assert rows == []
        assert db.query_all("SELECT reason FROM tickets")  # table still there


#: Every module that owns its own tables and once bypassed the dispatch.
_MIGRATED_MODULES = (
    "audit/ledger.py",
    "audit/verifier.py",
    "memory/semantic.py",
    "memory/episodic.py",
    "voice_consent.py",
)


class TestNoModuleBypassesTheSeam:
    def test_the_whole_app_is_free_of_raw_connection_reaches(self):
        """23 call sites once went straight to SQLite. None may return.

        A module that grabs ``_get_connection()`` writes to a
        per-replica file no matter what ANALYTICS_BACKEND says — which
        is how the audit ledger, memory and voice audit all ended up
        invisible on the backend production runs.
        """
        app_dir = Path(db.__file__).parent
        offenders = []
        for path in app_dir.rglob("*.py"):
            if path.name == "database.py":
                continue  # defines it
            if "_get_connection()" in path.read_text():
                offenders.append(str(path.relative_to(app_dir)))
        assert not offenders, f"modules bypassing the backend seam: {offenders}"

    @pytest.mark.parametrize("name", _MIGRATED_MODULES)
    def test_no_question_mark_inside_a_string_literal(self, name):
        source = (Path(db.__file__).parent / name).read_text()
        offenders = [lit for lit in re.findall(r"'[^'\n]*'", source) if "?" in lit]
        assert not offenders, (
            f"{name}: '?' inside a SQL string literal would be rewritten "
            f"as a placeholder: {offenders}"
        )


class TestAuditUsesTheSeam:
    def test_the_ledger_no_longer_reaches_for_a_raw_connection(self):
        for name in ("audit/ledger.py", "audit/verifier.py"):
            source = (Path(db.__file__).parent / name).read_text()
            assert "_get_connection()" not in source, (
                f"{name} still bypasses the dispatch block, so its tables "
                "stay on SQLite when ANALYTICS_BACKEND=postgres"
            )

    def test_the_chain_still_works_on_sqlite(self, tmp_db):
        from app.audit.ledger import AuditLedger
        from app.audit.verifier import verify_chain

        ledger = AuditLedger()
        first = ledger.append("chat.turn", {"q": "a"}, user_id="u1")
        second = ledger.append("chat.turn", {"q": "b"}, user_id="u1")
        assert second.prev_hash == first.row_hash
        assert ledger.count() == 2
        assert verify_chain().valid

    def test_tampering_is_still_detected_on_sqlite(self, tmp_db):
        from app.audit.ledger import AuditLedger
        from app.audit.verifier import verify_chain

        ledger = AuditLedger()
        ledger.append("chat.turn", {"q": "a"})
        ledger.append("chat.turn", {"q": "b"})
        db.execute("UPDATE audit_events SET payload = ? WHERE seq = ?", ('{"q":"X"}', 2))
        report = verify_chain()
        assert not report.valid
        assert report.breaks


@pytest.mark.skipif(
    not os.getenv("POSTGRES_DSN"),
    reason="set POSTGRES_DSN to verify the ledger on the production backend",
)
class TestAuditOnPostgres:
    """The chain has to verify on the backend production actually runs."""

    def test_append_chain_and_tamper_detection(self):
        from app.audit.ledger import AuditLedger
        from app.audit.verifier import verify_chain

        db.init_db()
        tenant = f"t-{os.getpid()}"
        ledger = AuditLedger()
        first = ledger.append("chat.turn", {"q": "a"}, tenant_id=tenant)
        second = ledger.append("chat.turn", {"q": "b"}, tenant_id=tenant)
        assert second.prev_hash == first.row_hash
        assert ledger.count(tenant_id=tenant) == 2
        assert ledger.last_row_hash(tenant_id=tenant) == second.row_hash

        rows = ledger.read(tenant_id=tenant)
        assert len(rows) == 2
        assert isinstance(rows[0]["payload"], dict)

        assert verify_chain(tenant_id=tenant).valid

        db.execute(
            "UPDATE audit_events SET payload = ? WHERE tenant_id = ? AND seq = ?",
            ('{"q":"TAMPERED"}', tenant, second.seq),
        )
        report = verify_chain(tenant_id=tenant)
        assert not report.valid, "a tampered payload went undetected on Postgres"

    def test_memory_and_voice_audit_work_on_postgres(self):
        import time as _time

        from app.memory.episodic import EpisodicMemory, EpisodicSummary
        from app.memory.semantic import SemanticMemory, UserFact

        db.init_db()
        user = f"u-{os.getpid()}"
        semantic = SemanticMemory()
        fact_id = semantic.write(
            UserFact(
                fact_id="",
                user_id=user,
                tenant_id="default",
                category="profile",
                subject="user",
                predicate="industry",
                object_value="retail",
                confidence=0.9,
                extracted_at=0,
            )
        )
        assert fact_id
        assert len(semantic.read(user)) == 1
        assert semantic.supersede(fact_id, "next")
        assert semantic.read(user) == []
        assert semantic.forget_user(user) >= 0

        episodic = EpisodicMemory()
        assert episodic.write(
            EpisodicSummary(
                summary_id="",
                user_id=user,
                tenant_id="default",
                conversation_id="c1",
                summary="asked about VAT",
                topic_tag="vat",
                sentiment="neutral",
                turn_count=3,
                created_at=_time.time(),
            )
        )
        assert len(episodic.list_for_user(user)) == 1
        assert episodic.delete_for_user(user) == 1

    def test_merkle_anchoring_works(self):
        from app.audit.ledger import AuditLedger

        db.init_db()
        tenant = f"anchor-{os.getpid()}"
        ledger = AuditLedger()
        for i in range(3):
            ledger.append("chat.turn", {"i": i}, tenant_id=tenant)
        anchor = ledger.anchor_range(1, 3, tenant_id=tenant)
        assert anchor["merkle_root"]
        assert anchor["first_seq"] == 1
