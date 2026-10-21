"""Deterministic evaluation gate for evals/web_research_scenarios/scenarios.jsonl (15 scenarios)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import database as db
from app import ephemeral_pdf_sandbox as sbx
from app import scenario_planner as sp
from app import web_fx
from app import web_research as wr
from app.pdf_corpus import PDF_JSONL_SCHEMA_VERSION, _stable_id

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


def _make_pdf_bytes(pages_text: list[str]) -> bytes:
    """Build a valid multi-page PDF-1.4 document in pure Python."""
    objs: list[tuple[int, bytes]] = []
    page_ids = [4 + 2 * i for i in range(len(pages_text))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objs.append((2, f"<< /Type /Pages /Kids [{kids}] /Count {len(pages_text)} >>".encode("latin-1")))
    objs.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    for i, txt in enumerate(pages_text):
        pid = 4 + 2 * i
        cid = pid + 1
        stream_bytes = f"BT\n/F1 11 Tf\n50 750 Td\n({txt}) Tj\nET".encode("latin-1")
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>"
        ).encode("latin-1")
        content_dict = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream"
        )
        objs.append((pid, page_dict))
        objs.append((cid, content_dict))

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid, body in objs:
        offsets[oid] = len(out)
        out.extend(f"{oid} 0 obj\n".encode("latin-1"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    total_objs = len(objs) + 1
    out.extend(f"xref\n0 {total_objs}\n0000000000 65535 f \n".encode("latin-1"))
    for oid in range(1, total_objs):
        out.extend(f"{offsets[oid]:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\\n{xref_pos}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(out)


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
    """Verify every scenario triggers its expected category, executes live FX / Qdrant sync outcomes, and skips Turn 2."""
    import app.flags as flags_mod

    db_file = tmp_path / "eval_scenarios.db"
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    jsonl_dir = tmp_path / "pdf_jsonl"
    archive_dir = tmp_path / "archive"
    sandbox_dir = tmp_path / "sandbox"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    # Seed existing outdated guides in jsonl_dir for the 3 replaced_outdated scenarios
    for old_source, old_fy, fam in (
        ("TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf", "FY2024-25", "sector_guide_real_estate"),
        ("ura.go.ug-A-GUIDE-TO-TAXATION-OF-THE-AGRICULTURAL-SECTOR-VOL-1-ISSUE-4-FY-2025-2026.pdf", "FY2025-26", "sector_guide_agriculture"),
        ("ura.go.ug-Withholding-Tax-FY-2024-25-1.pdf", "FY2024-25", "withholding_tax_guide"),
    ):
        stem = Path(old_source).stem
        with (jsonl_dir / f"{stem}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for idx in range(2):
                txt = f"{stem} page {idx + 1}"
                rec = {
                    "schema_version": PDF_JSONL_SCHEMA_VERSION,
                    "record_type": "pdf_chunk",
                    "chunk_id": _stable_id(old_source, idx, txt),
                    "text": txt,
                    "contextual_prefix": f"[Document: {stem}]",
                    "heading_trail": [f"Page {idx + 1}"],
                    "source": old_source,
                    "source_sha256": f"{idx + 1}" * 64,
                    "fiscal_year": old_fy,
                    "doc_family": fam,
                    "chunk_index": idx,
                    "char_count": len(txt),
                }
                fh.write(json.dumps(rec, sort_keys=True) + "\n")

    class _StubQdrant:
        def delete(self, **_kw: Any) -> None:
            return None

        def upsert(self, *, points: list[Any], **_kw: Any) -> None:
            assert len(points) >= 1

        def scroll(self, **_kw: Any) -> tuple[list[Any], None]:
            return ([], None)

    class _StubDense:
        def encode(self, texts: list[str], show_progress_bar: bool = False) -> list[list[float]]:
            return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    class _StubRetriever:
        collection_name = "ura_knowledge_base_eval"
        _client = _StubQdrant()
        _dense_model = _StubDense()
        _sparse_encoder = None
        _reranker = None

    fx_rates = {"USD": 3785.50, "EUR": 4120.00, "GBP": 4890.00, "KES": 29.20}
    monkeypatch.setenv("FLAG_LIVE_WEB_RESEARCH", "true")
    monkeypatch.setenv("FLAG_AUTO_QDRANT_WEB_SYNC", "true")
    monkeypatch.setattr(
        flags_mod.flags,
        "is_enabled",
        lambda name, **_kw: True if name in {"live_web_research", "auto_qdrant_web_sync"} else False,
    )
    monkeypatch.setattr(
        web_fx,
        "lookup_live_exchange_rate",
        lambda base, **_kw: web_fx.LiveFXQuote(
            currency=base,
            rate_ugx=fx_rates.get(base, 3785.50),
            source="XE.com",
            fetched_at_utc="2026-10-22T09:00:00Z",
            is_live=True,
        ),
    )

    try:
        rows = _load_scenarios()
        for row in rows:
            wr.clear_web_search_cache()
            sbx.clear_sandbox_cache()
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
                verified = sp.build_verified_facts(query)
                cur = str(row.get("currency") or "USD")
                assert f"1 {cur} = UGX {fx_rates[cur]:,.2f}" in verified

            if row.get("expect_qdrant_sync"):
                fam = str(row.get("doc_family") or "general_tax")
                is_notice = fam == "public_notice"
                pdf_name = (
                    f"Public_Notice_{row['id']}_FY2026-27.pdf"
                    if is_notice
                    else f"Official_Guide_{fam}_FY2026-27.pdf"
                )
                pdf_url = f"https://ura.go.ug/en/wp-content/uploads/2026/07/{pdf_name}"
                pages = (
                    [f"PUBLIC NOTICE FY 2026-27: {row['id']} official circular text."]
                    if is_notice
                    else [f"URA Official Handbook {fam} FY 2026-27 page {p}" for p in range(1, 7)]
                )
                pdf_bytes = _make_pdf_bytes(pages)
                html = f'<a class="result__a" href="{pdf_url}">{pdf_name}</a><div class="result__snippet">Official URA FY 2026-27 document</div>'
                outcome = wr.run_web_research(
                    query,
                    hits=hits,
                    retriever=_StubRetriever(),
                    turn_id=row["id"],
                    auto_sync_qdrant=True,
                    search_http_fetcher=lambda _q, h=html: h,
                    pdf_byte_fetcher=lambda _u, _m, b=pdf_bytes: b,
                    sandbox_base_dir=sandbox_dir,
                    pdf_jsonl_dir=jsonl_dir,
                    archive_dir=archive_dir,
                )
                assert outcome.sync_outcomes, f"Expected sync outcome for {row['id']}"
                assert outcome.sync_outcomes[0]["action_taken"] == row["expected_sync_action"]
            elif row.get("expect_ledger_skip_after"):
                outcome_skip = wr.run_web_research(
                    query,
                    hits=hits,
                    retriever=_StubRetriever(),
                    turn_id=row["id"],
                    auto_sync_qdrant=True,
                    sandbox_base_dir=sandbox_dir,
                    pdf_jsonl_dir=jsonl_dir,
                    archive_dir=archive_dir,
                )
                assert outcome_skip.skipped_by_ledger is True
    finally:
        if hasattr(db._local, "conn") and db._local.conn is not None:
            db._local.conn.close()
            db._local.conn = None
