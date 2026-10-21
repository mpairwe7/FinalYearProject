"""Deterministic evaluation gate for evals/web_research_scenarios/scenarios.jsonl (15 scenarios)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import database as db
from app import scenario_planner as sp
from app import web_research as wr

SCENARIOS_PATH = Path(__file__).resolve().parents[2] / "evals" / "web_research_scenarios" / "scenarios.jsonl"


def _load_scenarios() -> list[dict]:
    assert SCENARIOS_PATH.is_file(), f"Missing {SCENARIOS_PATH}"
    rows = []
    with SCENARIOS_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s:
                rows.append(json.loads(s))
    return rows


def test_web_research_scenarios_schema_and_coverage() -> None:
    """Verify scenarios.jsonl contains exactly 15 well-formed scenarios covering all 8 trigger categories."""
    rows = _load_scenarios()
    assert len(rows) == 15
    seen_ids: set[str] = set()
    categories: set[str] = set()
    fx_currencies: set[str] = set()

    for row in rows:
        sid = str(row.get("id") or "").strip()
        assert sid and sid not in seen_ids
        seen_ids.add(sid)
        assert row.get("query")
        cat = str(row.get("expected_trigger") or "")
        assert cat in wr.ALL_TRIGGER_CATEGORIES
        categories.add(cat)
        if row.get("currency"):
            fx_currencies.add(str(row["currency"]))

    assert categories == set(wr.ALL_TRIGGER_CATEGORIES)
    assert {"USD", "EUR", "GBP", "KES"} <= fx_currencies


def test_web_research_scenarios_triggers_and_turn2_ledger_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify every scenario triggers its expected category, plans FX tools, and honours Turn 2 web_sync_ledger skip."""
    import app.flags as flags_mod

    db_file = tmp_path / "eval_scenarios.db"
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    monkeypatch.setenv("FLAG_LIVE_WEB_RESEARCH", "true")
    monkeypatch.setenv("FLAG_AUTO_QDRANT_WEB_SYNC", "true")
    monkeypatch.setattr(
        flags_mod.flags,
        "is_enabled",
        lambda name, **_kw: True if name in {"live_web_research", "auto_qdrant_web_sync"} else False,
    )

    rows = _load_scenarios()
    for row in rows:
        query = str(row["query"])
        hits = []
        if row.get("stale_hit_source"):
            hits.append(
                {
                    "text": f"Passage from {row['stale_hit_source']}",
                    "source": row["stale_hit_source"],
                    "fiscal_year": row.get("stale_hit_fy", "FY2024-25"),
                    "score_norm": 0.78,
                }
            )
        triggers = wr.detect_research_triggers(query, hits=hits)
        cats = {t.category for t in triggers}
        assert row["expected_trigger"] in cats, f"Scenario {row['id']} missing trigger {row['expected_trigger']} (got {cats})"

        if row.get("expect_live_fx"):
            specs = sp.plan_tools(query)
            planned_names = [s.tool for s in specs]
            for expected_tool in row.get("expected_tools") or []:
                assert expected_tool in planned_names, f"Scenario {row['id']} missing tool {expected_tool}"

        # Simulate Qdrant sync completion for web_sync_05 so web_sync_06 tests Turn-2 ledger skip
        if row["id"] == "web_sync_05_real_estate_guide_fy26_27":
            db.upsert_web_sync_entry(
                topic_key="sector_guide_real_estate_2026",
                doc_family="sector_guide_real_estate",
                source_uri="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27.pdf",
                ura_url="https://ura.go.ug/en/wp-content/uploads/2026/07/TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27.pdf",
                pdf_sha256="e" * 64,
                fiscal_year="FY2026-27",
                action_taken="replaced_outdated",
                superseded_source=row["stale_hit_source"],
            )
        elif row.get("expect_ledger_skip_after"):
            stale_trigger = next(t for t in triggers if t.category == wr.TRIGGER_STALE_CORPUS)
            assert wr.should_skip_web_search(stale_trigger) is True
