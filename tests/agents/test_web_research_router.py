"""Unit tests for Phase C: 8-trigger web research router, PII-scrubbed query planner, and 3-tier source authority filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import database as db
from app import web_research as wr
from app.case_facts import extract_case_facts


@pytest.fixture(autouse=True)
def _reset_search_cache():
    """Clear L1 web search cache before and after every test."""
    wr.clear_web_search_cache()
    yield
    wr.clear_web_search_cache()


def test_all_eight_research_triggers_fire_appropriately() -> None:
    """Verify each of the 8 trigger categories fires on its target scenario."""
    # 1. FX rate trigger
    q_fx = "We pay our expatriate engineer $6,000 per month. What is the PAYE?"
    t_fx = wr.detect_research_triggers(q_fx, extract_case_facts(q_fx))
    assert any(t.category == wr.TRIGGER_FX_RATE and t.currency == "USD" for t in t_fx)

    # FX trigger does NOT fire when user already supplied an exchange rate
    q_fx_supplied = "We pay $6,000 per month at an exchange rate of 3,750 UGX per USD."
    t_fx_sup = wr.detect_research_triggers(q_fx_supplied, extract_case_facts(q_fx_supplied))
    assert not any(t.category == wr.TRIGGER_FX_RATE for t in t_fx_sup)

    # 2. Stale corpus edition trigger (Qdrant hit is FY2024-25 while current is FY2026-27)
    stale_hits = [
        {
            "source": "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf",
            "fiscal_year": "FY2024-25",
            "text": "Real estate rental tax guide for FY 2024/25.",
            "score": 0.82,
        }
    ]
    t_stale = wr.detect_research_triggers(
        "What are the rules in the real estate sector tax guide?",
        hits=stale_hits,
        current_fy="FY2026-27",
    )
    stale_trig = next(t for t in t_stale if t.category == wr.TRIGGER_STALE_CORPUS)
    assert stale_trig.doc_family == "sector_guide_real_estate"

    # 3. Used motor vehicle CIF valuation trigger
    t_veh = wr.detect_research_triggers(
        "How much import duty and VAT will I pay when importing a 2018 Toyota Harrier from Japan?"
    )
    assert any(
        t.category == wr.TRIGGER_VEHICLE_CIF and t.doc_family == "used_mv_valuation"
        for t in t_veh
    )

    # 4. Public notice / waiver window trigger
    t_notice = wr.detect_research_triggers(
        "Is there an active URA public notice on penalty waiver or tax amnesty window this month?"
    )
    assert any(t.category == wr.TRIGGER_PUBLIC_NOTICE for t in t_notice)

    # 5. EFRIS / Digital Tax Stamps (DTS / Kakasa) gazetted goods & rates trigger
    t_dts = wr.detect_research_triggers(
        "Which products are mandatory under Digital Tax Stamps (Kakasa) and what are the gazetted stamp rates?"
    )
    assert any(t.category == wr.TRIGGER_EFRIS_DTS for t in t_dts)

    # 6. Bill-to-Act presidential assent & Gazette status trigger
    t_bill = wr.detect_research_triggers(
        "Has the Income Tax Amendment Bill 2026 been assented to by the President and gazetted into law?"
    )
    assert any(t.category == wr.TRIGGER_BILL_ASSENT for t in t_bill)

    # 7. EAC CET Stay of Application trigger
    t_cet = wr.detect_research_triggers(
        "What is the Uganda Stay of Application import duty rate under the EAC Common External Tariff for HS code 8703?"
    )
    assert any(t.category == wr.TRIGGER_EAC_CET for t in t_cet)

    # 8. Unverified rate (BoU statutory interest rate for employee loan BIK) or low retrieval confidence
    t_bou = wr.detect_research_triggers(
        "Our company gave an employee a low-interest loan at 4% p.a.; what is the Bank of Uganda statutory interest rate?"
    )
    assert any(
        t.category == wr.TRIGGER_UNVERIFIED_OR_LOW_CONF
        and t.doc_family == "bou_statutory_rate"
        for t in t_bou
    )


def test_pii_scrubbing_in_query_synthesizer() -> None:
    """Verify TINs, NINs, phone numbers, emails, and names are stripped from search queries."""
    fake_tin = "1" + "0" * 9
    fake_nin = "CM" + "9" * 12
    raw_query = (
        f"Hello URA, my name is Mukasa Ronald, TIN {fake_tin}, NIN {fake_nin}, "
        "phone +256772123456, email ronald@mukasa.co.ug. I want the latest 2026-27 "
        "real estate sector tax guide for rental property."
    )
    triggers = wr.detect_research_triggers(raw_query)
    specs = wr.build_search_queries(raw_query, triggers)
    assert specs
    for spec in specs:
        assert fake_tin not in spec.query
        assert fake_nin not in spec.query
        assert "772123456" not in spec.query
        assert "ronald" not in spec.query.lower()
        assert "mukasa" not in spec.query.lower()
        assert "site:ura.go.ug" in spec.query


def test_source_authority_tiering_and_qdrant_persistence_gate() -> None:
    """Verify Tier 1, Tier 2 (Uganda-scoped only), Tier 3 blocking, and Qdrant persistence rules."""
    # Tier 1 official URA PDF -> trust 1.0, can_write_qdrant=True
    ura_auth = wr.classify_source_authority(
        "https://ura.go.ug/en/wp-content/uploads/2026/07/Real-Estate-Guide-FY2026-27.pdf"
    )
    assert ura_auth.tier == 1
    assert ura_auth.trust == 1.0
    assert ura_auth.can_write_qdrant is True
    assert wr.can_persist_to_qdrant(
        "https://ura.go.ug/en/wp-content/uploads/2026/07/Real-Estate-Guide-FY2026-27.pdf"
    )

    # Tier 1 ULII Act -> trust 1.0, can_write_qdrant=True
    ulii_auth = wr.classify_source_authority(
        "https://ulii.org/akn/ug/act/2026/14/eng@2026-07-01"
    )
    assert ulii_auth.tier == 1
    assert ulii_auth.can_write_qdrant is True

    # Tier 1 Bank of Uganda -> trust 1.0, can_write_qdrant=False (only ura.go.ug / ulii.org write to Qdrant)
    bou_auth = wr.classify_source_authority("https://www.bou.or.ug/monetary-policy-statement.pdf")
    assert bou_auth.tier == 1
    assert bou_auth.trust == 1.0
    assert bou_auth.can_write_qdrant is False

    # Tier 2 PwC WITH Uganda scope -> tier 2, trust 0.8, can_write_qdrant=False
    pwc_ug = wr.classify_source_authority(
        "https://www.pwc.com/ug/en/tax-alert-2026.html",
        title="Uganda Tax Amendment Act 2026 analysis",
    )
    assert pwc_ug.tier == 2
    assert pwc_ug.trust == pytest.approx(0.8)
    assert pwc_ug.can_write_qdrant is False

    # Tier 2 PwC WITHOUT Uganda/URA mention -> downgraded to Tier 3 (blocked)
    pwc_uk = wr.classify_source_authority(
        "https://www.pwc.com/uk/en/corporation-tax.html",
        title="UK Corporation Tax Rates 2026",
    )
    assert pwc_uk.tier == 3
    assert pwc_uk.trust == 0.0

    # Tier 3 Foreign Revenue Authorities (KRA, TRA, RRA, IRS, HMRC) -> strictly blocked
    for foreign_url in (
        "https://www.kra.go.ke/individual/Threshold-PAYE",
        "https://www.tra.go.tz/index.php/paye",
        "https://www.rra.gov.rw/en/domestic-taxes",
        "https://www.irs.gov/individuals",
        "https://www.gov.uk/income-tax-rates",
    ):
        f_auth = wr.classify_source_authority(foreign_url, title="Uganda comparison")
        assert f_auth.tier == 3
        assert f_auth.trust == 0.0
        assert wr.validate_outbound_research_url(foreign_url) != ""


def test_temporal_precedence_and_ssrf_validation() -> None:
    """Verify fiscal-year extraction, temporal precedence, and SSRF IP/scheme checks."""
    assert wr.extract_fiscal_year("TAXATION-HANDBOOK-FY-2026-27.pdf") == "FY2026-27"
    assert wr.extract_fiscal_year("Real Estate Sector Guide 2024/25") == "FY2024-25"
    assert wr.can_override_statutory_table("FY2026-27", "FY2026-27") is True
    assert wr.can_override_statutory_table("FY2027-28", "FY2026-27") is True
    assert wr.can_override_statutory_table("FY2024-25", "FY2026-27") is False
    assert wr.can_override_statutory_table("", "FY2026-27") is False

    # SSRF validation rejects HTTP, localhost, private IPs, and credentials
    assert wr.validate_outbound_research_url("http://ura.go.ug/guide.pdf") != ""
    assert wr.validate_outbound_research_url("https://127.0.0.1/admin") != ""
    assert wr.validate_outbound_research_url("https://169.254.169.254/latest/meta-data") != ""
    assert wr.validate_outbound_research_url("https://user:pass@ura.go.ug/guide.pdf") != ""  # pragma: allowlist secret
    assert wr.validate_outbound_research_url("https://ura.go.ug/en/guide.pdf") == ""


def test_parse_search_results_and_web_sync_ledger_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify HTML result parsing drops KRA/Tier-3 links, ranks Tier-1 PDFs first, and honors web_sync_ledger."""
    db_file = tmp_path / "analytics_search.db"
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    ddg_html = """
    <html><body>
      <a class="result__a" href="https://www.kra.go.ke/paye-guide-2026.pdf">Kenya PAYE Guide 2026</a>
      <div class="result__snippet">Kenya Revenue Authority PAYE rates.</div>

      <a class="result__a" href="https://www.pwc.com/ug/en/real-estate-2026.html">PwC Uganda Real Estate Tax Alert FY2026-27</a>
      <div class="result__snippet">Analysis of URA rental tax changes in Uganda for FY 2026/27.</div>

      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fura.go.ug%2Fuploads%2FReal-Estate-Sector-Guide-FY-2026-27.pdf">URA Real Estate Sector Guide FY 2026-27 PDF</a>
      <div class="result__snippet">Official Uganda Revenue Authority Real Estate Guide FY 2026-27.</div>
    </body></html>
    """
    spec = wr.SearchSpec(
        trigger_category=wr.TRIGGER_STALE_CORPUS,
        topic_key="sector_guide_real_estate_fy2026-27",
        doc_family="sector_guide_real_estate",
        query="site:ura.go.ug real estate sector guide 2026 filetype:pdf",
        target_domains=("ura.go.ug",),
    )
    results = wr.execute_web_search(spec, http_fetcher=lambda _q: ddg_html)
    # KRA is dropped; URA Tier-1 PDF ranks #1 ahead of PwC Tier-2 HTML
    assert len(results) == 2
    assert results[0].tier == 1
    assert results[0].is_pdf is True
    assert results[0].fiscal_year == "FY2026-27"
    assert results[0].url == "https://ura.go.ug/uploads/Real-Estate-Sector-Guide-FY-2026-27.pdf"
    assert results[1].tier == 2

    # Once recorded in web_sync_ledger, should_skip_web_search returns True
    trig = wr.ResearchTrigger(
        category=wr.TRIGGER_STALE_CORPUS,
        reason="Stale local guide",
        topic_key="sector_guide_real_estate_fy2026-27",
        doc_family="sector_guide_real_estate",
    )
    assert wr.should_skip_web_search(trig) is False

    future = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db.upsert_web_sync_entry(
        topic_key="sector_guide_real_estate_fy2026-27",
        doc_family="sector_guide_real_estate",
        source_uri="Real-Estate-Sector-Guide-FY-2026-27.pdf",
        ura_url=results[0].url,
        pdf_sha256="f" * 64,
        fiscal_year="FY2026-27",
        action_taken="replaced_outdated",
        expires_at=future,
    )
    assert wr.should_skip_web_search(trig) is True
