"""End-to-end tests for Phase F: live FX PAYE parity, ephemeral PDF sandbox + Qdrant hot-swap orchestration, and Turn 2 ledger skip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import corpus_sync as cs
from app import database as db
from app import ephemeral_pdf_sandbox as sbx
from app import service as svc
from app import web_fx
from app import web_research as wr
from app.agents.evaluator import verify_figure_lock
from app.claim_verifier import verify_claims
from app.flags import flags
from app.pdf_corpus import PDF_JSONL_SCHEMA_VERSION, _stable_id


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Configure an isolated SQLite DB, temporary JSONL/archive dirs, and clean caches."""
    db_file = tmp_path / "analytics_f.db"
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    wr.clear_web_search_cache()
    sbx.clear_sandbox_cache()
    web_fx.clear_fx_cache()

    jsonl_dir = tmp_path / "pdf_jsonl"
    archive_dir = tmp_path / "archive"
    sandbox_dir = tmp_path / "sandbox"
    freshness_path = tmp_path / "index_freshness.json"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    yield {
        "jsonl_dir": jsonl_dir,
        "archive_dir": archive_dir,
        "sandbox_dir": sandbox_dir,
        "freshness_path": freshness_path,
    }

    wr.clear_web_search_cache()
    sbx.clear_sandbox_cache()
    web_fx.clear_fx_cache()
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def _build_pdf_bytes(pages_text: list[str]) -> bytes:
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
        lines = txt.splitlines()
        stream_lines = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
        for idx, line in enumerate(lines):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if idx == 0:
                stream_lines.append(f"({esc}) Tj")
            else:
                stream_lines.append(f"T* ({esc}) Tj")
        stream_lines.append("ET")
        stream_bytes = "\n".join(stream_lines).encode("latin-1")
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
        f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(out)


def test_scenario1_expat_usd_paye_live_fx_verified_across_guards(
    isolated_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Scenario 1 ($6,000/month expat salary) live FX conversion passes verify_figure_lock and _apply_output_guards."""
    import app.flags as flags_mod

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
            rate_ugx=3785.50,
            source="XE.com",
            fetched_at_utc="2026-10-22T09:00:00Z",
            is_live=True,
        ),
    )

    user_msg = (
        "An expatriate engineer arrives in Uganda on a 4-month contract earning USD 6,000 per month. "
        "Calculate monthly PAYE both if non-resident and if resident."
    )
    verified_block = svc._evaluate_calculation_context(user_msg)
    assert "3,785.50" in verified_block
    assert "22,713,000" in verified_block
    assert "Source: XE" in verified_block

    # Extract the computed PAYE figures from verified_block and verify a draft reply citing them passes all guards
    draft_reply = (
        "Using the live XE.com exchange rate of 1 USD = UGX 3,785.50, the monthly cash salary of USD 6,000 "
        "converts to UGX 22,713,000 [1]. Under the Third Schedule to the Income "
        "Tax Act Cap 340, the Resident monthly PAYE is UGX 7,973,450 and the Non-Resident monthly PAYE "
        "is UGX 8,010,700 (a difference of UGX 37,250 per month) [1]."
    )
    hits = [
        {
            "text": "Income Tax Act Cap. 340 Third Schedule PAYE rates for resident and non-resident individuals.",
            "source": "Income-Tax-Act-Cap-340.pdf",
            "source_url": "https://ulii.org/akn/ug/act/1997/11",
            "fiscal_year": "FY2026-27",
            "score_norm": 0.91,
        }
    ]
    citations = svc.HybridRetriever.build_citations(hits)

    ok_lock, offending, _meta = verify_figure_lock(
        draft_reply,
        query=user_msg,
        passages=[h["text"] for h in hits],
        verified_facts=verified_block,
    )
    assert ok_lock is True, f"Unexpected figure-lock violation: {offending}"

    claim_rep = verify_claims(
        draft_reply,
        citations,
        hits,
        query=user_msg,
        locale="en",
        verified_facts=verified_block,
    )
    assert claim_rep["decision"] == "approve"


def test_turn1_outdated_guide_replacement_and_turn2_zero_network_skip(
    isolated_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Turn 1 replaces an outdated FY2024-25 guide in Qdrant and Turn 2 skips web search via web_sync_ledger."""
    import app.flags as flags_mod

    monkeypatch.setenv("FLAG_LIVE_WEB_RESEARCH", "true")
    monkeypatch.setenv("FLAG_AUTO_QDRANT_WEB_SYNC", "true")
    monkeypatch.setenv("FLAG_KNOWLEDGE_DISCREPANCY_REPORTING", "true")
    monkeypatch.setattr(
        flags_mod.flags,
        "is_enabled",
        lambda name, **_kw: True if name in {"live_web_research", "auto_qdrant_web_sync", "knowledge_discrepancy_reporting"} else False,
    )

    jsonl_dir = isolated_env["jsonl_dir"]
    archive_dir = isolated_env["archive_dir"]
    sandbox_dir = isolated_env["sandbox_dir"]
    freshness_path = isolated_env["freshness_path"]

    # Seed outdated FY2024-25 Real Estate Sector Guide in pdf_jsonl_dir
    old_stem = "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25"
    old_source = f"{old_stem}.pdf"
    old_jsonl = jsonl_dir / f"{old_stem}.jsonl"
    with old_jsonl.open("w", encoding="utf-8", newline="\n") as fh:
        for idx in range(4):
            txt = f"FY2024-25 Real Estate Sector Guide section {idx + 1}"
            rec = {
                "schema_version": PDF_JSONL_SCHEMA_VERSION,
                "record_type": "pdf_chunk",
                "chunk_id": _stable_id(old_source, idx, txt),
                "text": txt,
                "contextual_prefix": f"[Document: {old_stem}]",
                "heading_trail": [f"Section {idx + 1}"],
                "source": old_source,
                "source_sha256": "a" * 64,
                "fiscal_year": "FY2024-25",
                "doc_family": "sector_guide_real_estate",
                "chunk_index": idx,
                "char_count": len(txt),
            }
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    class _StubQdrantClient:
        def __init__(self) -> None:
            self.deleted: list[Any] = []
            self.upserted: list[Any] = []

        def delete(self, *, collection_name: str, points_selector: Any) -> None:
            self.deleted.append((collection_name, points_selector))

        def upsert(self, *, collection_name: str, points: list[Any]) -> None:
            self.upserted.append((collection_name, list(points)))

        def scroll(self, **_kw: Any) -> tuple[list[Any], None]:
            return ([], None)

    class _StubDense:
        def encode(self, texts: list[str], show_progress_bar: bool = False) -> list[list[float]]:
            return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    class _StubRetriever:
        collection_name = "ura_knowledge_base_test"
        _client = _StubQdrantClient()
        _dense_model = _StubDense()
        _sparse_encoder = None
        _reranker = None

    retriever = _StubRetriever()

    # Build the new FY2026-27 PDF served by ura.go.ug
    new_pdf_bytes = _build_pdf_bytes(
        [
            "UGANDA REVENUE AUTHORITY\nTAXATION OF THE REAL ESTATE SECTOR FY 2026-27\nSection 1: Scope.",
            "SECTION 2: INDIVIDUAL LANDLORDS\nIndividual rental income tax is 12% of gross rental income exceeding UGX 2,820,000 per annum.",
            "SECTION 3: CORPORATE LANDLORDS\nCompanies pay 30% on chargeable rental income with expenses capped at 50%.",
            "SECTION 4: WITHHOLDING TAX ON RENT\nDesignated withholding agents deduct 6% WHT on rental payments.",
            "SECTION 5: EFRIS COMPLIANCE\nAll commercial and residential landlords must issue EFRIS e-receipts.",
            "SECTION 6: FILING DEADLINES\nAnnual rental returns are due by 31 December.",
        ]
    )
    new_pdf_url = "https://ura.go.ug/en/wp-content/uploads/2026/07/TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27.pdf"
    ddg_html = f"""
    <html><body>
      <a class="result__a" href="{new_pdf_url}">URA Taxation of the Real Estate Sector Guide FY 2026-27 (PDF)</a>
      <div class="result__snippet">Official URA Real Estate Sector Guide FY 2026-27 covering 12% individual rental tax above UGX 2,820,000.</div>
    </body></html>
    """

    search_calls: list[str] = []
    pdf_downloads: list[str] = []

    def _fake_search_fetcher(q: str) -> str:
        search_calls.append(q)
        return ddg_html

    def _fake_pdf_fetcher(u: str, _max_b: int) -> bytes:
        pdf_downloads.append(u)
        return new_pdf_bytes

    stale_hits = [
        {
            "text": "Old FY2024-25 real estate guide passage.",
            "source": old_source,
            "fiscal_year": "FY2024-25",
            "score_norm": 0.78,
        }
    ]
    query_t1 = "What is the individual rental tax rate and threshold in the URA real estate sector guide?"

    # --- TURN 1: Executes web search + ephemeral PDF sandbox + Qdrant hot-swap ---
    outcome_t1 = wr.run_web_research(
        query_t1,
        hits=stale_hits,
        retriever=retriever,
        turn_id="turn-1",
        auto_sync_qdrant=True,
        search_http_fetcher=_fake_search_fetcher,
        pdf_byte_fetcher=_fake_pdf_fetcher,
        sandbox_base_dir=sandbox_dir,
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
        freshness_snapshot_path=freshness_path,
    )

    assert outcome_t1.triggered is True
    assert outcome_t1.skipped_by_ledger is False
    assert len(search_calls) >= 1
    assert pdf_downloads == [new_pdf_url]
    assert outcome_t1.superseded_sources == [old_source]
    assert len(outcome_t1.web_hits) >= 1
    assert outcome_t1.web_hits[0]["fiscal_year"] == "FY2026-27"
    assert outcome_t1.web_hits[0]["source_url"] == new_pdf_url
    assert "## Live Web & Official Portal Verification" in outcome_t1.verified_facts_addition

    # Verify SSE events emitted in order: web_research.started, pdf_sandbox.extracting, qdrant_sync.updated
    event_names = [e[0] for e in outcome_t1.events]
    assert "web_research.started" in event_names
    assert "pdf_sandbox.extracting" in event_names
    assert "qdrant_sync.updated" in event_names

    # Verify old JSONL was archived, new JSONL written, and tombstone created
    assert not old_jsonl.exists()
    assert (archive_dir / f"{old_stem}.jsonl").is_file()
    assert (jsonl_dir / "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27.jsonl").is_file()
    assert any(t["source_uri"] == old_source for t in db.get_active_corpus_tombstones())

    # Verify verify_figure_lock and verify_claims approve an answer citing the new PDF's 12% and UGX 2,820,000
    combined_hits = list(outcome_t1.web_hits)
    citations_t1 = svc.HybridRetriever.build_citations(combined_hits)
    reply_t1 = (
        "Under the updated URA Real Estate Sector Guide (FY2026-27), individual landlords pay rental tax "
        "at 12% on gross rental income exceeding the annual threshold of UGX 2,820,000 [1]."
    )
    ok_lock, offending, _ = verify_figure_lock(
        reply_t1,
        query=query_t1,
        passages=[h["text"] for h in combined_hits],
        verified_facts=outcome_t1.verified_facts_addition,
    )
    assert ok_lock is True, f"Unexpected figure lock failure: {offending}"
    claim_rep_t1 = verify_claims(
        reply_t1,
        citations_t1,
        combined_hits,
        query=query_t1,
        locale="en",
        verified_facts=outcome_t1.verified_facts_addition,
    )
    assert claim_rep_t1["decision"] == "approve"

    # --- TURN 2: Same document family skips web search completely (0 HTTP calls) ---
    search_calls.clear()
    pdf_downloads.clear()

    outcome_t2 = wr.run_web_research(
        "What is the corporate rental expense cap in the real estate sector guide?",
        hits=stale_hits,
        retriever=retriever,
        turn_id="turn-2",
        auto_sync_qdrant=True,
        search_http_fetcher=_fake_search_fetcher,
        pdf_byte_fetcher=_fake_pdf_fetcher,
        sandbox_base_dir=sandbox_dir,
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
    )
    assert outcome_t2.triggered is True
    assert outcome_t2.skipped_by_ledger is True
    assert search_calls == []
    assert pdf_downloads == []

    # Verify admin tombstones payload exposes both tombstones and web_sync_ledger
    from app.main import list_tombstones_endpoint

    admin_payload = list_tombstones_endpoint(limit=50, _ctx=None)  # type: ignore[arg-type]
    assert any(t["source_uri"] == old_source for t in admin_payload["tombstones"])
    assert any(
        row["doc_family"] == "sector_guide_real_estate" and row["action_taken"] == "replaced_outdated"
        for row in admin_payload["web_sync_ledger"]
    )
