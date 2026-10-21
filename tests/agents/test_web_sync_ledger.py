"""Unit tests for Phase A: web_sync_ledger schema, feature flags, and streaming verified-facts parity."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app import database as db
from app import postgres as pg
from app.calculator_router import _SCENARIO_PLANNER_BYPASS_RE
from app.flags import _REGISTRY, flags


@pytest.fixture()
def isolated_sqlite_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect SQLite analytics DB to an isolated temporary directory."""
    db_file = tmp_path / "analytics_test.db"
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()
    yield db_file
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def test_phase41_web_research_flags_registered() -> None:
    """Verify live_web_research and auto_qdrant_web_sync are registered with default False."""
    all_flags = flags.all()
    assert "live_web_research" in all_flags
    assert "auto_qdrant_web_sync" in all_flags
    assert _REGISTRY["live_web_research"].default is False
    assert _REGISTRY["auto_qdrant_web_sync"].default is False


def test_web_sync_ledger_insert_lookup_and_ttl(isolated_sqlite_db: Path) -> None:
    """Verify SQLite web_sync_ledger insert, lookup by family/topic/pair, in-place upsert, and TTL expiry."""
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    past = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    rec = db.upsert_web_sync_entry(
        topic_key="real_estate_rental_tax",
        doc_family="sector_guide_real_estate",
        source_uri="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27.pdf",
        ura_url="https://ura.go.ug/en/wp-content/uploads/2026/07/Real-Estate-2026-27.pdf",
        pdf_sha256="a" * 64,
        fiscal_year="FY2026-27",
        action_taken="replaced_outdated",
        superseded_source="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf",
        expires_at=future,
    )
    assert rec["id"].startswith("wsync_")
    assert rec["doc_family"] == "sector_guide_real_estate"
    assert rec["action_taken"] == "replaced_outdated"

    # Lookup by doc_family
    by_family = db.get_active_web_sync_entry(doc_family="sector_guide_real_estate")
    assert by_family is not None
    assert by_family["source_uri"] == "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27.pdf"
    assert by_family["superseded_source"] == "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf"

    # Lookup by topic_key
    by_topic = db.get_active_web_sync_entry(topic_key="real_estate_rental_tax")
    assert by_topic is not None
    assert by_topic["id"] == rec["id"]

    # Lookup by both doc_family AND topic_key requires both to match
    by_both = db.get_active_web_sync_entry(
        doc_family="sector_guide_real_estate",
        topic_key="real_estate_rental_tax",
    )
    assert by_both is not None
    assert by_both["id"] == rec["id"]
    assert (
        db.get_active_web_sync_entry(
            doc_family="sector_guide_real_estate",
            topic_key="mismatched_topic",
        )
        is None
    )

    # Upsert same family+topic updates in place
    rec2 = db.upsert_web_sync_entry(
        topic_key="real_estate_rental_tax",
        doc_family="sector_guide_real_estate",
        source_uri="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27-REV2.pdf",
        ura_url="https://ura.go.ug/en/wp-content/uploads/2026/08/Real-Estate-Rev2.pdf",
        pdf_sha256="b" * 64,
        fiscal_year="FY2026-27",
        action_taken="verified_current",
        expires_at=future,
    )
    assert rec2["id"] == rec["id"]
    entries = db.list_web_sync_entries(limit=10)
    assert len(entries) == 1
    assert entries[0]["pdf_sha256"] == "b" * 64

    # Expired entry is not returned by get_active_web_sync_entry
    db.upsert_web_sync_entry(
        topic_key="expired_notice",
        doc_family="public_notice_waiver",
        source_uri="Public-Notice-2025.pdf",
        ura_url="https://ura.go.ug/notice.pdf",
        pdf_sha256="c" * 64,
        fiscal_year="FY2025-26",
        action_taken="added_new",
        expires_at=past,
    )
    assert db.get_active_web_sync_entry(doc_family="public_notice_waiver") is None
    assert db.get_active_web_sync_entry(topic_key="expired_notice") is None
    # Still visible in full audit list
    assert len(db.list_web_sync_entries(limit=10)) == 2


def test_web_sync_ledger_validation_and_postgres_parity(
    isolated_sqlite_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify input validation, signature parity, and PostgreSQL recording-pool SQL generation."""
    with pytest.raises(ValueError, match="topic_key or doc_family"):
        db.upsert_web_sync_entry(
            topic_key="",
            doc_family="",
            source_uri="doc.pdf",
            ura_url="https://ura.go.ug/doc.pdf",
            pdf_sha256="1" * 64,
        )
    with pytest.raises(ValueError, match="action_taken"):
        db.upsert_web_sync_entry(
            topic_key="vat",
            doc_family="vat_guide",
            source_uri="doc.pdf",
            ura_url="https://ura.go.ug/doc.pdf",
            pdf_sha256="1" * 64,
            action_taken="invalid_action",
        )

    for fn_name in (
        "upsert_web_sync_entry",
        "get_active_web_sync_entry",
        "list_web_sync_entries",
    ):
        assert hasattr(pg, fn_name), f"postgres.py missing {fn_name}"
        assert list(inspect.signature(getattr(db, fn_name)).parameters) == list(
            inspect.signature(getattr(pg, fn_name)).parameters
        )

    # When pool is None, upsert_web_sync_entry raises RuntimeError rather than silently dropping the write
    monkeypatch.setattr(pg, "_get_pool", lambda: None)
    with pytest.raises(RuntimeError, match="PostgreSQL connection pool is unavailable"):
        pg.upsert_web_sync_entry(
            topic_key="vat",
            doc_family="vat_guide",
            source_uri="doc.pdf",
            ura_url="https://ura.go.ug/doc.pdf",
            pdf_sha256="1" * 64,
        )

    # Recording pool test for PostgreSQL SQL and bound parameters
    executed: list[tuple[str, Any]] = []
    fake_row = (
        "wsync_1234",
        "vat_topic",
        "vat_guide",
        "VAT-Guide-2026-27.pdf",
        "https://ura.go.ug/vat.pdf",
        "d" * 64,
        "FY2026-27",
        "replaced_outdated",
        "VAT-Guide-2024-25.pdf",
        "2026-10-21T12:00:00.000000Z",
        "2026-10-28T12:00:00.000000Z",
    )

    class _FakeCursor:
        """Fake psycopg cursor recording SQL statements and parameters."""

        def __enter__(self) -> _FakeCursor:
            """Enter context manager."""
            return self

        def __exit__(self, *args: Any) -> None:
            """Exit context manager."""

        def execute(self, sql: str, params: Any = None) -> None:
            """Record executed SQL and parameters."""
            executed.append((sql, params))

        def fetchone(self) -> Any:
            """Return single fake row."""
            return fake_row

        def fetchall(self) -> list[Any]:
            """Return list of fake rows."""
            return [fake_row]

    class _FakeConn:
        """Fake psycopg connection."""

        def __init__(self) -> None:
            """Initialize committed state."""
            self.committed = False

        def __enter__(self) -> _FakeConn:
            """Enter context manager."""
            return self

        def __exit__(self, *args: Any) -> None:
            """Exit context manager."""

        def cursor(self) -> _FakeCursor:
            """Return fake cursor."""
            return _FakeCursor()

        def commit(self) -> None:
            """Record commit."""
            self.committed = True

    class _FakePool:
        """Fake psycopg connection pool."""

        def __init__(self) -> None:
            """Initialize connection holder."""
            self.conn = _FakeConn()

        def connection(self) -> _FakeConn:
            """Return fake connection."""
            return self.conn

    fake_pool = _FakePool()
    monkeypatch.setattr(pg, "_get_pool", lambda: fake_pool)

    pg_rec = pg.upsert_web_sync_entry(
        topic_key="vat_topic",
        doc_family="vat_guide",
        source_uri="VAT-Guide-2026-27.pdf",
        ura_url="https://ura.go.ug/vat.pdf",
        pdf_sha256="d" * 64,
        fiscal_year="FY2026-27",
        action_taken="replaced_outdated",
        superseded_source="VAT-Guide-2024-25.pdf",
    )
    assert pg_rec["doc_family"] == "vat_guide"
    assert fake_pool.conn.committed is True
    assert "ON CONFLICT(id) DO UPDATE" in executed[-1][0]

    active_pg = pg.get_active_web_sync_entry(
        doc_family="vat_guide",
        topic_key="vat_topic",
        now_iso="2026-10-22T00:00:00Z",
    )
    assert active_pg is not None
    assert active_pg["source_uri"] == "VAT-Guide-2026-27.pdf"
    assert "WHERE doc_family = %s AND topic_key = %s AND expires_at > %s" in executed[-1][0]
    assert executed[-1][1] == ("vat_guide", "vat_topic", "2026-10-22T00:00:00.000000Z")

    listed_pg = pg.list_web_sync_entries(limit=5)
    assert len(listed_pg) == 1
    assert listed_pg[0]["id"] == "wsync_1234"


def test_scenario_planner_bypass_regex_matches_compared_to_and_versus() -> None:
    """Verify _SCENARIO_PLANNER_BYPASS_RE matches 'compared to' and 'versus'."""
    assert _SCENARIO_PLANNER_BYPASS_RE.search(
        "How much PAYE as a tax resident compared to a non-resident?"
    )
    assert _SCENARIO_PLANNER_BYPASS_RE.search(
        "Calculate resident versus non-resident PAYE on UGX 22,200,000"
    )


def test_streaming_apply_output_guards_honors_verified_facts() -> None:
    """Verify _apply_output_guards passes verified_facts and calculation_message to judge and claim verifier."""
    from app.guardrails import OutputGuard
    from app.service import _apply_output_guards

    class _StubModel:
        """Minimal stub model for testing _apply_output_guards."""

        def _evaluate_response_judge(self, **kwargs: Any) -> dict[str, Any]:
            """Verify verified_facts reaches the response judge."""
            assert "verified_facts" in kwargs
            assert "8,608,000" in kwargs["verified_facts"]
            return {
                "decision": "approve",
                "final_decision": "approve",
                "applied_revision": False,
                "reasons": [],
                "confidence_band": "high",
            }

        def _build_handoff_packet(self, **kwargs: Any) -> dict[str, Any]:
            """Return empty handoff packet."""
            return {}

        def _maybe_create_ticket(self, **kwargs: Any) -> str:
            """Return empty ticket id."""
            return ""

    hits = [
        {
            "text": "Individual PAYE rates in Uganda range from 10% to 40% for chargeable income above UGX 10,000,000.",
            "source": "Taxation Handbook FY 2025-26.pdf",
            "score": 0.85,
        }
    ]
    citations = [{"index": 1, "source": "Taxation Handbook FY 2025-26.pdf"}]
    verified_facts = (
        "## Verified Statutory Facts & Calculations (Code-Computed — Must Cite Verbatim)\n"
        "- Resident PAYE on UGX 22,200,000: UGX 8,608,000/month\n"
        "- Non-Resident PAYE on UGX 22,200,000: UGX 8,712,000/month\n"
    )
    reply = (
        "For a monthly gross salary of UGX 22,200,000, the resident PAYE is UGX 8,608,000 "
        "and the non-resident PAYE is UGX 8,712,000 [1]."
    )
    guard_out = _apply_output_guards(
        _StubModel(),
        message="Bala omusolo gwa PAYE",
        calculation_message="Calculate resident vs non-resident PAYE on UGX 22,200,000 per month",
        reply=reply,
        hits=hits,
        citations=citations,
        conversation_history=[],
        session_id="sess-1",
        conversation_id="conv-1",
        output_guard=OutputGuard(),
        verified_facts=verified_facts,
    )
    assert guard_out["escalate"] is False
    assert "8,608,000" in guard_out["reply"]
    assert "8,712,000" in guard_out["reply"]
