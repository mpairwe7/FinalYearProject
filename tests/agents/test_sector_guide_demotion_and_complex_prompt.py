"""Unit tests for Phase G.3 & G.4: sector-guide demotion, topic-section boosts, and complex-answer prompt contract."""

from __future__ import annotations

from app.llm import COMPLEX_ANSWER_CONTRACT, _build_messages
from app.providers.routing import ENDPOINTS, ModelTier
from app.query import decompose_query, extract_retrieval_preferences
from app.retriever import (
    SECTOR_GUIDE_DEMOTION,
    SECTOR_GUIDE_SOURCE_PATTERNS,
    TOPIC_SECTION_BOOST,
    TOPIC_SECTION_HINTS,
    apply_preference_boost,
    is_sector_guide_source,
)


def test_sector_guide_source_patterns_match_sector_pdfs_and_spare_handbook() -> None:
    assert len(SECTOR_GUIDE_SOURCE_PATTERNS) >= 8
    assert is_sector_guide_source("A-guide-to-taxation-of-the-Hotel-and-Accomodation-Sector-2025-26.pdf")
    assert is_sector_guide_source("ura.go.ug-A-GUIDE-TO-TAXATION-OF-THE-AGRICULTURAL-SECTOR-VOL-1-ISSUE-4-FY-2025-2026.pdf")
    assert is_sector_guide_source("A-GUIDE-TO-TAXATION-OF-THE-MANUFACTURING-SECTOR-FINAL.pdf")
    assert is_sector_guide_source("Aguide-To-Taxation-Of-The-Wholesale-And-Retail-Sector22-23-1.pdf")
    assert is_sector_guide_source("CONSTRUCTION-SECTOR-2022-23-1.pdf")
    assert is_sector_guide_source("GUIDE-TO-TAXATION-OF-GOVERNMENT-AGENCIES-2023-24.pdf")
    assert not is_sector_guide_source("808977692-Taxation-Handbook-FY-2024-25-1.pdf")
    assert not is_sector_guide_source("ura_employment_income_faqs.csv")
    assert not is_sector_guide_source("Income-Tax-Amendment-Act-2025-FY2025-26.pdf")


def test_extract_retrieval_preferences_demotes_sector_guides_unless_sector_named() -> None:
    # Non-sector employment / BIK query (D7 scenario) -> demote_sector_guides=True + employment hints
    pref_non_sector = extract_retrieval_preferences(
        "An expatriate employee receives a furnished house and motor vehicle benefit. How is PAYE calculated?"
    )
    assert pref_non_sector.get("demote_sector_guides") is True
    assert "employment income" in pref_non_sector.get("preferred_sections", [])
    assert "benefits in kind" in pref_non_sector.get("preferred_sections", [])
    assert "ura_employment_income_faqs.csv" in pref_non_sector.get("preferred_sections", [])

    # Explicit hotel/accommodation query -> demote_sector_guides is NOT set
    pref_hotel = extract_retrieval_preferences(
        "What are the VAT and income tax rules for a hotel and tourism business in Kampala?"
    )
    assert not pref_hotel.get("demote_sector_guides")

    # Explicit agriculture query -> demote_sector_guides is NOT set
    pref_agri = extract_retrieval_preferences("Are agricultural inputs exempt from VAT for farmers?")
    assert not pref_agri.get("demote_sector_guides")


def test_apply_preference_boost_demotes_hotel_guide_below_handbook_on_bik_query() -> None:
    """Fixes D7: Hotel sector guide (-0.06) is demoted and Employment Income handbook (+0.05) promoted."""
    hits = [
        {
            "source": "A-guide-to-taxation-of-the-Hotel-and-Accomodation-Sector-2025-26.pdf",
            "section": "Hotel PAYE and Benefits",
            "text": "Employees in the hotel sector are subject to PAYE on benefits.",
            "score_norm": 0.72,
            "score_rrf": 0.25,
        },
        {
            "source": "808977692-Taxation-Handbook-FY-2024-25-1.pdf",
            "section": "Employment Income and Benefits in Kind",
            "text": "Housing benefit is the lesser of market rent and 15% of employment income.",
            "score_norm": 0.65,
            "score_rrf": 0.20,
        },
    ]
    prefer = extract_retrieval_preferences(
        "How is an employer-provided housing benefit valued for PAYE?"
    )
    ranked = apply_preference_boost(hits, prefer)
    assert ranked[0]["source"] == "808977692-Taxation-Handbook-FY-2024-25-1.pdf"
    assert abs(ranked[0]["score_norm"] - (0.65 + TOPIC_SECTION_BOOST)) < 1e-6
    assert abs(ranked[1]["score_norm"] - (0.72 + SECTOR_GUIDE_DEMOTION)) < 1e-6


def test_topic_section_hints_cover_major_tax_heads() -> None:
    for topic in ("employment", "vat", "wht", "rental", "cit", "cgt", "efris", "objections"):
        assert topic in TOPIC_SECTION_HINTS
        assert len(TOPIC_SECTION_HINTS[topic]) >= 2


def test_decompose_query_splits_multi_issue_numbered_and_auxiliary_questions() -> None:
    q = (
        "(1) Is he a Ugandan tax resident for FY2025/26? "
        "(2) How is the housing benefit valued? "
        "(3) What is his monthly PAYE?"
    )
    parts = decompose_query(q)
    assert len(parts) == 3


def test_complex_answer_contract_injected_only_when_verified_facts_or_t3() -> None:
    # 1. Simple T1 turn without verified facts -> no COMPLEX_ANSWER_CONTRACT
    simple_msgs = _build_messages("What is the standard VAT rate?", passages=[])
    assert "## Complex-Answer Contract" not in simple_msgs[0]["content"]

    # 2. Turn with scenario_planner's "## Verified Statutory Facts & Calculations" -> contract included
    verified_ctx = (
        "## Verified Statutory Facts & Calculations (tool ground truth — use these figures verbatim)\n"
        "- Premise Check (`wht_5pct_rate_does_not_exist`): Uganda has no 5% WHT rate.\n"
        "- PAYE verified (resident, FY2026-27): UGX 7,725,500.00 / month.\n"
        "- Still needed for an exact figure: full-year gross employment income."
    )
    complex_msgs = _build_messages(
        "Calculate his monthly PAYE.",
        passages=[{"source": "ura_employment_income_faqs.csv", "text": "PAYE bands under Third Schedule."}],
        personalization_context=verified_ctx,
    )
    sys_prompt = complex_msgs[0]["content"]
    user_prompt = complex_msgs[-1]["content"]
    assert "## Complex-Answer Contract" in sys_prompt
    assert COMPLEX_ANSWER_CONTRACT in sys_prompt
    # Verified facts block must be routed to calc_ground_truth in user prompt, NOT "Do not treat it as live URA account data"
    assert "## Consent-granted personalization context" not in sys_prompt
    assert "## Verified Scenario Ground Truth" in user_prompt
    assert "do NOT tell the taxpayer to \"consult URA for exact figures\"" in sys_prompt

    # 3. Turn on T3 endpoint even without verified facts -> contract included
    t3_msgs = _build_messages(
        "Explain the interaction between VAT apportionment and EFRIS e-invoicing.",
        passages=[],
        endpoint=ENDPOINTS[ModelTier.T3],
    )
    assert "## Complex-Answer Contract" in t3_msgs[0]["content"]
