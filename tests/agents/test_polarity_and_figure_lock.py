"""Unit tests for Phase G.1 & G.2: polarity-aware faithfulness, tool-backed figure lock, and same-tier revision."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from app.agents.evaluator import (
    evaluate,
    strip_unverified_figure_sentences,
    verify_figure_lock,
)
from app.claim_polarity import DIRECTION_LEXICON, polarity_conflicts
from app.claim_verifier import verify_claims
from app.providers.routing import ENDPOINTS, ModelTier
from app.retriever import HybridRetriever
from app.service import ChatModel


# ---------------------------------------------------------------------------
# Phase G.1: Polarity-aware faithfulness & claim verification (>= 20 pairs)
# ---------------------------------------------------------------------------
def test_direction_lexicon_has_at_least_20_pairs() -> None:
    assert len(DIRECTION_LEXICON) >= 20


def test_d6_housing_subtracted_vs_handbook_passage_reports_conflict() -> None:
    """D6 bug: 'housing benefit is subtracted from gross salary' vs handbook 'added to / forms part of'."""
    passage = (
        "Housing benefits provided by an employer form part of taxable employment income "
        "and are added to the employee's gross salary for PAYE."
    )
    bad_reply = (
        "The taxable value of the employer-provided housing benefit is subtracted from "
        "the employee's gross salary when calculating PAYE. [1]"
    )
    good_paraphrase = (
        "The taxable value of the employer-provided housing benefit is included in "
        "taxable employment income and added to gross salary for PAYE. [1]"
    )

    conflicts = polarity_conflicts(bad_reply, [passage])
    assert len(conflicts) >= 1
    assert conflicts[0].pair_name == "inclusion_vs_subtraction"

    assert polarity_conflicts(good_paraphrase, [passage]) == []

    # compute_faithfulness sets conflicting sentence to ungrounded (0.0)
    assert HybridRetriever.compute_faithfulness(bad_reply, [passage]) == 0.0
    assert HybridRetriever.compute_faithfulness(good_paraphrase, [passage]) == 1.0

    # verify_claims adds a contradicted_claims entry and sets decision="revise"
    hits = [{"text": passage}]
    citations = [{"ref": "[1]", "passage": passage}]
    bad_report = verify_claims(bad_reply, citations, hits)
    assert bad_report["decision"] == "revise"
    assert len(bad_report["contradicted_claims"]) == 1
    assert bad_report["contradicted_claims"][0]["kind"] == "polarity_conflict"

    good_report = verify_claims(good_paraphrase, citations, hits)
    assert good_report["decision"] == "approve"
    assert good_report["contradicted_claims"] == []


POLARITY_PAIR_CASES: list[tuple[str, str, str]] = [
    (
        "inclusion_vs_subtraction",
        "Motor vehicle benefits are added to employment income for PAYE.",
        "Motor vehicle benefits are subtracted from employment income for PAYE.",
    ),
    (
        "taxable_vs_exempt",
        "Cash allowances paid to an employee are taxable under PAYE.",
        "Cash allowances paid to an employee are exempt from PAYE.",
    ),
    (
        "resident_vs_non_resident",
        "An individual present in Uganda for 240 days is a tax resident for income tax.",
        "An individual present in Uganda for 240 days is a non-resident for income tax.",
    ),
    (
        "allowed_vs_disallowed_deduction",
        "Individual landlords cannot deduct repair expenses from rental income.",
        "Individual landlords can deduct repair expenses from rental income.",
    ),
    (
        "claimable_vs_not_claimable",
        "Input VAT on a manual paper invoice is not claimable by the buyer.",
        "Input VAT on a manual paper invoice is claimable by the buyer.",
    ),
    (
        "lesser_vs_greater",
        "The housing benefit is the lesser of market rent and 15% of employment income.",
        "The housing benefit is the greater of market rent and 15% of employment income.",
    ),
    (
        "before_vs_after_tax",
        "Employee NSSF contributions are deducted after PAYE is calculated.",
        "Employee NSSF contributions are deducted before PAYE is calculated.",
    ),
    (
        "increase_vs_decrease",
        "Taxable benefits in kind increases taxable employment income for the month.",
        "Taxable benefits in kind decreases taxable employment income for the month.",
    ),
    (
        "compulsory_vs_voluntary_registration",
        "Below the threshold, VAT registration is not compulsory for the trader.",
        "Below the threshold, VAT registration is compulsory for the trader.",
    ),
    (
        "within_vs_after_45_days",
        "A tax objection must be lodged within 45 days of service of the assessment.",
        "A tax objection must be lodged after 45 days of service of the assessment.",
    ),
    (
        "within_vs_after_30_days",
        "An appeal to the Tax Appeals Tribunal is filed within 30 days of the decision.",
        "An appeal to the Tax Appeals Tribunal is filed after 30 days of the decision.",
    ),
    (
        "within_vs_after_15_days",
        "Monthly PAYE returns must be filed within 15 days of the following month.",
        "Monthly PAYE returns must be filed after 15 days of the following month.",
    ),
    (
        "inclusive_vs_exclusive_vat",
        "The quoted contract price is VAT-inclusive at the standard rate.",
        "The quoted contract price is VAT-exclusive at the standard rate.",
    ),
    (
        "gross_vs_net_rental_base",
        "Individual rental income tax is charged at 12% of gross annual rent above the threshold.",
        "Individual rental income tax is charged at 12% of net annual rent above the threshold.",
    ),
    (
        "final_vs_creditable_wht",
        "Withholding tax on treasury bills and dividends is a final tax under the Act.",
        "Withholding tax on treasury bills and dividends is not a final tax under the Act.",
    ),
    (
        "zero_rated_vs_exempt_vat",
        "Exports of goods from Uganda are zero-rated for Value Added Tax.",
        "Exports of goods from Uganda are an exempt supply for Value Added Tax.",
    ),
    (
        "prohibited_vs_permitted_import",
        "Motor vehicles over 15 years old are prohibited from importation into Uganda.",
        "Motor vehicles over 15 years old are permitted for importation into Uganda.",
    ),
    (
        "worldwide_vs_uganda_sourced",
        "Resident individuals are taxed on worldwide income under the Income Tax Act.",
        "Resident individuals are taxed on only Uganda-sourced income under the Income Tax Act.",
    ),
    (
        "service_vs_employment_contract_wht",
        "A Ugandan-sourced service contract does not include an employment contract.",
        "A Ugandan-sourced service contract includes an employment contract.",
    ),
    (
        "reducing_balance_vs_straight_line",
        "The vehicle market value is depreciated on a reducing balance basis at 35% per annum.",
        "The vehicle market value is depreciated on a straight-line basis at 35% per annum.",
    ),
    (
        "progressive_vs_flat_rate_paye",
        "Resident PAYE is calculated using progressive bands in the Third Schedule.",
        "Resident PAYE is calculated using a simplified flat rate in the Third Schedule.",
    ),
    (
        "free_vs_fee_tin",
        "TIN registration on the URA portal is free of charge for all applicants.",
        "TIN registration on the URA portal charges a fee for all applicants.",
    ),
]


@pytest.mark.parametrize(("expected_pair", "source_sent", "inverted_sent"), POLARITY_PAIR_CASES)
def test_22_polarity_pairs_detect_inversion_and_accept_paraphrase(
    expected_pair: str, source_sent: str, inverted_sent: str
) -> None:
    conflicts = polarity_conflicts(inverted_sent, [source_sent])
    assert conflicts, f"Expected conflict for {expected_pair}"
    assert any(c.pair_name == expected_pair for c in conflicts)
    # Identical / true-polarity statement has no conflict
    assert polarity_conflicts(source_sent, [source_sent]) == []


# ---------------------------------------------------------------------------
# Phase G.2: Tool-backed figure lock, conditional figures, and sentence stripping
# ---------------------------------------------------------------------------
def test_figure_lock_accepts_verified_and_rounded_figures_and_rejects_invented_ones() -> None:
    verified_block = (
        "## Verified Statutory Facts & Calculations\n"
        "- PAYE verified (resident, FY2026-27): UGX 7,725,500.00 / month on chargeable UGX 22,000,000.00\n"
        "- PAYE conditional on unverified housing rule: UGX 8,625,500.00 / month (conditional on unverified rule)\n"
        "- Residency threshold: 183 days in Uganda."
    )
    tool_outputs = [{"rate": 0.3, "paye": 7725500.0, "unverified_benefits": ["housing"], "paye_conditional": 8625500.0}]

    # 1. Exact and ±0.5% rounded (7.73m for 7,725,500) + 30% from 0.3 + 183 days -> passes
    good_reply = (
        "For FY2026-27, after 183 days in Uganda, the marginal rate is 30% and "
        "monthly PAYE is UGX 7,725,500 (approximately UGX 7.73m). "
        "If the conditional housing benefit is included, conditional PAYE is UGX 8,625,500."
    )
    ok, offending, _ = verify_figure_lock(
        good_reply,
        query="What is PAYE on UGX 22,000,000?",
        verified_facts=verified_block,
        tool_outputs=tool_outputs,
    )
    assert ok is True, f"Unexpected offending figures: {offending}"

    # 2. Presenting the conditional figure WITHOUT labelling it conditional -> rejected
    unlabelled_cond_reply = "His verified monthly PAYE is UGX 8,625,500."
    ok_cond, offending_cond, _ = verify_figure_lock(
        unlabelled_cond_reply,
        query="What is PAYE on UGX 22,000,000?",
        verified_facts=verified_block,
        tool_outputs=tool_outputs,
    )
    assert ok_cond is False
    assert "UGX 8,625,500" in offending_cond

    # 3. Invented figure (UGX 6,150,000 and 24% and 90 days) -> rejected and stripped on fallback
    bad_reply = (
        "For FY2026-27, monthly PAYE is UGX 7,725,500. "
        "However, under a special rule after 90 days, the tax drops to UGX 6,150,000 at 24%."
    )
    verdict = evaluate(
        "What is PAYE on UGX 22,000,000?",
        bad_reply,
        effective_year="FY2026-27",
        faithfulness=0.9,
        verified_facts=verified_block,
        tool_outputs=tool_outputs,
    )
    assert verdict.numerically_consistent is False
    assert "UGX 6,150,000" in verdict.revision_note

    stripped = strip_unverified_figure_sentences(bad_reply, verdict.detail["offending_figures"])
    assert "UGX 7,725,500" in stripped
    assert "UGX 6,150,000" not in stripped
    assert "Some figures could not be verified; please confirm with URA." in stripped


def test_reflect_llm_uses_same_tier_endpoint_and_60s_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """_reflect_llm forwards endpoint= and timeout=60.0 to llm._vllm_generate (§9.G.2)."""
    from app import llm as llm_module

    captured: dict[str, Any] = {}

    def _fake_vllm_generate(messages: list[dict[str, str]], **kwargs: Any) -> str:
        captured["messages"] = messages
        captured.update(kwargs)
        return "Revised grounded answer with verified figures [1]."

    monkeypatch.setattr(llm_module, "LLM_BACKEND", "vllm")
    monkeypatch.setattr(llm_module, "_vllm_generate", _fake_vllm_generate)

    t3_ep = ENDPOINTS[ModelTier.T3]
    out = ChatModel._reflect_llm(
        query="What is PAYE on UGX 22,000,000?",
        draft_reply="PAYE is UGX 6,150,000.",
        unsupported_claims=[{"text": "PAYE is UGX 6,150,000."}],
        passages=[{"text": "Schedule 4 Part I sets the resident PAYE bands."}],
        endpoint=t3_ep,
        verified_facts="PAYE verified: UGX 7,725,500",
        offending_figures=["UGX 6,150,000"],
    )
    assert "Revised grounded answer" in out
    assert captured["endpoint"] is t3_ep
    assert captured["timeout"] == 60.0
    assert "UGX 6,150,000" in captured["messages"][1]["content"]
    assert "PAYE verified: UGX 7,725,500" in captured["messages"][1]["content"]


def test_mixed_passage_does_not_falsely_flag_unrelated_opposing_rule_and_retains_tie() -> None:
    """Mixed passage with individual rental taxable + NGO rental exempt must not flag a correct individual claim."""
    mixed_passage = (
        "Individual rental income is taxable at 12% of gross annual rent above the threshold. "
        "Rental income of an exempt charity or NGO is exempt from tax under Section 21."
    )
    correct_individual_claim = (
        "Individual rental income is taxable at 12% of gross annual rent above the threshold."
    )
    assert polarity_conflicts(correct_individual_claim, [mixed_passage]) == []

    # A genuine tied contradiction (where both same-pole and opposite-pole sentences have equal subject overlap)
    # is still surfaced rather than suppressed.
    tied_passage = (
        "Cash allowances paid to an employee are taxable under PAYE. "
        "Cash allowances paid to an employee are exempt from PAYE."
    )
    claim = "Cash allowances paid to an employee are exempt from PAYE."
    conflicts = polarity_conflicts(claim, [tied_passage])
    assert len(conflicts) == 1
    assert conflicts[0].pair_name == "taxable_vs_exempt"


def test_calendar_and_fiscal_years_excluded_while_year_like_amounts_preserved() -> None:
    """Dates like '30 June 2025' or 'FY2026-27' are not treated as UGX 2,025, while UGX 2025 / milioni 2025 are."""
    ok, offending, _ = verify_figure_lock(
        "For FY2025-26 ending 30 June 2026, the monthly PAYE is UGX 7,725,500.",
        query="What is my monthly PAYE?",
        verified_facts="Monthly PAYE: UGX 7,725,500",
    )
    assert ok is True, f"Dated sentence should not fail figure lock: {offending}"

    # Year-like numbers that ARE genuine currency/multiplier amounts must still be checked
    ok_amt, offending_amt, _ = verify_figure_lock(
        "The penalty is UGX 2025 and the turnover is milioni 2025.",
        query="What is my monthly PAYE?",
        verified_facts="Monthly PAYE: UGX 7,725,500",
    )
    assert ok_amt is False
    assert "UGX 2,025" in offending_amt
    assert "UGX 2,025,000,000" in offending_amt

