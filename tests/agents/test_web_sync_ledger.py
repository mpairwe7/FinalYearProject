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
    all_flags = flags.all()
    assert "live_web_research" in all_flags
    assert "auto_qdrant_web_sync" in all_flags
    assert _REGISTRY["live_web_research"].default is False
    assert _REGISTRY["auto_qdrant_web_sync"].default is False


def test_web_sync_ledger_insert_lookup_and_ttl(isolated_sqlite_db: Path) -> None:
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


def test_web_sync_ledger_validation_and_postgres_parity(isolated_sqlite_db: Path) -> None:
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


def test_scenario_planner_bypass_regex_matches_compared_to_and_versus() -> None:
    assert _SCENARIO_PLANNER_BYPASS_RE.search(
        "How much PAYE as a tax resident compared to a non-resident?"
    )
    assert _SCENARIO_PLANNER_BYPASS_RE.search(
        "Calculate resident versus non-resident PAYE on UGX 22,200,000"
    )


def test_streaming_apply_output_guards_honors_verified_facts() -> None:
    from app.guardrails import OutputGuard
    from app.service import _apply_output_guards

    class _StubModel:
        def _evaluate_response_judge(self, **kwargs: Any) -> dict[str, Any]:
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
            return {}

        def _maybe_create_ticket(self, **kwargs: Any) -> str:
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
        message="Calculate resident vs non-resident PAYE on UGX 22,200,000 per month",
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
