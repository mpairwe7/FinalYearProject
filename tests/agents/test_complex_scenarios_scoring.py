"""Unit tests for the deterministic complex-scenario scorer (Phase A.4)."""

from __future__ import annotations

from typing import Any

from evals.complex_scenarios.scoring import score_scenario, score_turn


def _paye_expect(*, include_net_claim: bool = False) -> dict[str, Any]:
    """Build a representative PAYE turn expectation dictionary."""
    figures = [
        {
            "tool": "calculate_paye",
            "params": {"monthly_gross": 22_000_000, "residency": "resident"},
            "field": "paye",
            "claim": {"label_cues": {"any": ["PAYE", "tax payable", "monthly tax"]}},
            "required": True,
        }
    ]
    if include_net_claim:
        figures.append(
            {
                "tool": "calculate_paye",
                "params": {"monthly_gross": 22_000_000, "residency": "resident"},
                "field": "net_take_home",
                "claim": {"label_cues": {"any": ["net pay", "take-home pay", "net take-home"]}},
                "required": True,
            }
        )
    return {
        "route": "rag",
        "must_include": [{"any": ["resident", "30%"]}],
        "must_not_include": [{"text": "flat rate", "severity": "critical"}],
        "figures": figures,
    }


def test_correct_claim_figure_hits_against_live_rate_table() -> None:
    """Verify that a matching full-digit or rounded figure in a claim clause scores a hit."""
    expect = _paye_expect()
    response = {
        "reply": (
            "On a gross salary of UGX 22,000,000 for a resident individual, "
            "your monthly PAYE is UGX 7,688,250."
        ),
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts = score_turn(expect, response)
    assert ts.figures == 1.0
    assert ts.figure_details[0].status == "hit"
    assert ts.route_ok is True
    assert ts.facts == 1.0
    assert ts.sources_relevant == 1.0
    assert ts.complete is True
    assert ts.score == 1.0


def test_non_resident_paye_does_not_match_resident_paye_in_full_digits() -> None:
    """Verify full-digit amounts use ±1 UGX tolerance so non-resident PAYE (7,725,500) misses resident PAYE (7,688,250)."""
    expect = _paye_expect()
    response = {
        "reply": (
            "For a resident employee earning UGX 22,000,000, "
            "your monthly PAYE is UGX 7,725,500."
        ),
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts = score_turn(expect, response)
    assert ts.figures == 0.0
    assert ts.figure_details[0].status == "miss"

    # Rounded shorthand ("UGX 7.69 million") still uses ±0.5% tolerance and hits
    resp_rounded = {
        "reply": (
            "For a resident employee earning UGX 22,000,000, "
            "your monthly PAYE is about UGX 7.69 million."
        ),
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts_rounded = score_turn(expect, resp_rounded)
    assert ts_rounded.figures == 1.0
    assert ts_rounded.figure_details[0].status == "hit"


def test_parenthesised_working_math_and_vat_headroom_do_not_cause_false_conflict() -> None:
    """Verify parenthesised arithmetic working values and headroom notes are ignored when scoring direct claim assertions."""
    cgt_expect = {
        "route": "rag",
        "figures": [
            {
                "tool": "calculate_capital_gains",
                "params": {"sale_price": 500_000_000, "cost_base": 320_000_000},
                "field": "tax",
                "claim": {"label_cues": {"any": ["CGT", "capital gains tax", "tax"]}},
                "required": True,
            }
        ],
    }
    cgt_resp = {
        "reply": (
            "- **Cost Base:** UGX 320 million (including improvements).\n"
            "- **Sale Price:** UGX 500 million.\n"
            "- **Taxable Gain:** UGX 180 million (500m - 320m).\n"
            "- **Tax Rate:** 30%.\n"
            "- **Capital Gains Tax:** UGX 54 million (180m × 30%)."
        ),
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    cgt_ts = score_turn(cgt_expect, cgt_resp)
    assert cgt_ts.figures == 1.0
    assert cgt_ts.figure_details[0].status == "hit"

    vat_expect = {
        "route": "rag",
        "figures": [
            {
                "tool": "check_vat_registration",
                "params": {"annual_turnover": 240_000_000},
                "field": "threshold",
                "claim": {"label_cues": {"any": ["threshold", "registration"]}},
                "required": True,
            }
        ],
    }
    vat_resp = {
        "reply": (
            "- Annual turnover: UGX 240,000,000\n"
            "- Registration threshold: UGX 300,000,000 per year\n"
            "- **Registration is not compulsory** — you are below the threshold (headroom UGX 60,000,000)."
        ),
        "retrieval_mode": "calculator",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    vat_ts = score_turn(vat_expect, vat_resp)
    assert vat_ts.figures == 1.0
    assert vat_ts.figure_details[0].status == "hit"


def test_contradictory_same_claim_assertions_across_clauses_miss() -> None:
    """Verify contradictory direct assertions for the same claim across separate clauses score as a conflict."""
    cgt_expect = {
        "route": "rag",
        "figures": [
            {
                "tool": "calculate_capital_gains",
                "params": {"sale_price": 500_000_000, "cost_base": 320_000_000},
                "field": "tax",
                "claim": {"label_cues": {"any": ["CGT", "capital gains tax"]}},
                "required": True,
            }
        ],
    }
    resp = {
        "reply": "Capital Gains Tax is UGX 54 million. Capital Gains Tax is UGX 60 million.",
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts = score_turn(cgt_expect, resp)
    assert ts.figures == 0.0
    assert ts.figure_details[0].status == "miss"
    assert "Conflicting" in ts.figure_details[0].reason


def test_figure_appearing_only_in_negation_misses() -> None:
    """Fixture 1: the correct value appearing only in a negation."""
    expect = _paye_expect()
    for phrase in (
        "For a resident employee, your monthly PAYE is not UGX 7,688,250.",
        "The monthly tax payable applies instead of UGX 7,688,250.",
        "Your PAYE is calculated rather than UGX 7,688,250.",
    ):
        response = {
            "reply": phrase,
            "retrieval_mode": "hybrid",
            "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
        }
        ts = score_turn(expect, response)
        assert ts.figures == 0.0, f"Expected miss for negated phrase: {phrase}"
        assert ts.figure_details[0].status == "miss"


def test_figure_in_unrelated_sentence_while_claim_states_different_amount_misses() -> None:
    """Fixture 2: correct value in unrelated sentence while PAYE line states wrong amount."""
    expect = _paye_expect()
    response = {
        "reply": (
            "Suppose a resident company has a balance of UGX 7,688,250 in its account. "
            "However, your monthly PAYE is UGX 5,200,000 under the 30% bracket."
        ),
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts = score_turn(expect, response)
    assert ts.figures == 0.0
    assert ts.figure_details[0].status == "miss"
    assert "Conflicting" in ts.figure_details[0].reason or "No matching" in ts.figure_details[0].reason


def test_two_claims_with_swapped_amounts_both_miss() -> None:
    """Fixture 3: two claims with swapped amounts."""
    expect = _paye_expect(include_net_claim=True)
    response = {
        "reply": (
            "For a resident earning UGX 22,000,000, your monthly PAYE is UGX 14,311,750, "
            "and your net take-home pay is UGX 7,688,250."
        ),
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts = score_turn(expect, response)
    assert ts.figures == 0.0
    assert [d.status for d in ts.figure_details] == ["miss", "miss"]


def test_d6_housing_benefit_subtracted_critical_forbidden_fails_turn() -> None:
    """D6 regression: housing benefit described as 'subtracted from' gross salary zeroes score."""
    expect = {
        "route": "rag",
        "must_include": [
            {"any": ["20%", "20 per cent"]},
            {"any": ["15%"]},
        ],
        "must_not_include": [
            {"text": "subtracted from", "severity": "critical"},
            {"text": "What is your gross monthly salary", "severity": "critical"},
        ],
    }
    response = {
        "reply": (
            "The motor vehicle benefit uses 20% and housing uses 15%. "
            "This value is subtracted from the employee's gross salary."
        ),
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts = score_turn(expect, response)
    assert ts.critical_fail is True
    assert ts.score == 0.0
    assert any(h["text"] == "subtracted from" for h in ts.forbidden_hits)


def test_empty_sources_on_rag_scores_zero_but_calculator_route_is_none() -> None:
    """Empty source list: 0.0 on rag route, None (weight redistributed) on calculator/workflow/declined."""
    rag_expect = {
        "route": "rag",
        "must_include": [{"any": ["183 days"]}],
    }
    rag_resp = {
        "reply": "An individual present for 183 days or more is resident.",
        "retrieval_mode": "hybrid",
        "sources": [],
    }
    rag_ts = score_turn(rag_expect, rag_resp)
    assert rag_ts.sources_relevant == 0.0
    assert rag_ts.score < 1.0

    for non_rag_route, mode in (
        ("calculator", "calculator"),
        ("workflow", "workflow"),
        ("declined", "out_of_jurisdiction"),
    ):
        non_rag_expect = {
            "route": non_rag_route,
            "must_include": [{"any": ["Uganda", "PAYE"]}],
        }
        non_rag_resp = {
            "reply": "In Uganda PAYE is calculated progressively.",
            "retrieval_mode": mode,
            "sources": [],
        }
        ts = score_turn(non_rag_expect, non_rag_resp)
        assert ts.sources_relevant is None
        assert ts.score == 1.0


def test_sector_guide_penalized_only_on_non_sector_questions() -> None:
    """Verify sector guides reduce sources_relevant only when the scenario does not mention that sector."""
    expect = {
        "route": "rag",
        "must_include": [{"any": ["PAYE"]}],
    }
    response = {
        "reply": "PAYE applies to employment income.",
        "retrieval_mode": "hybrid",
        "sources": [
            {"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"},
            {"source": "A-GUIDE-TO-TAXATION-OF-AGRICULTURAL-SECTOR.pdf"},
            {"source": "A-guide-to-taxation-of-the-Hotel-and-Accomodation-Sector-2025-26.pdf"},
        ],
    }
    ts_non_sector = score_turn(
        expect,
        response,
        scenario_context="Foreign software engineer hired on an 8-month contract.",
    )
    assert ts_non_sector.sources_relevant == round(1 / 3, 6)
    assert len(ts_non_sector.sector_guide_hits) == 2

    ts_sector = score_turn(
        expect,
        response,
        scenario_context="We run an agricultural farm and a tourist hotel lodge.",
    )
    assert ts_sector.sources_relevant == 1.0
    assert ts_sector.sector_guide_hits == []


def test_truncation_via_finish_reason_or_mid_word_halves_score() -> None:
    """Verify finish_reason='length' or mid-word truncation marks complete=False and halves the turn score."""
    expect = {
        "route": "rag",
        "must_include": [{"any": ["183 days"]}],
    }
    resp_len = {
        "reply": "A person present for 183 days is a resident individual.",
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
        "debug": {"finish_reason": "length"},
    }
    ts_len = score_turn(expect, resp_len)
    assert ts_len.complete is False
    assert ts_len.truncation_reason == "finish_reason=length"
    assert ts_len.score == 0.5

    resp_cut = {
        "reply": "A person present for 183 days in the current fiscal",
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    ts_cut = score_turn(expect, resp_cut)
    assert ts_cut.complete is False
    assert ts_cut.score == 0.5


def test_missing_tool_and_optional_fx_placeholder() -> None:
    """Verify optional FX placeholders are skipped when convert_currency is absent while missing tools count as misses."""
    expect = {
        "route": "rag",
        "must_include": [{"any": ["benefit in kind"]}],
        "figures": [
            {
                "tool": "calculate_paye",
                "params": {"monthly_gross": "${FX:USD:6000}", "residency": "resident"},
                "field": "paye",
                "claim": {"label_cues": {"any": ["PAYE"]}},
                "required": False,
            },
            {
                "tool": "nonexistent_future_tool_xyz",
                "params": {"amount": 1000},
                "field": "tax",
                "claim": {"label_cues": {"any": ["tax"]}},
                "required": True,
            },
        ],
    }
    response = {
        "reply": "The benefit in kind is included in employment income.",
        "retrieval_mode": "hybrid",
        "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
    }
    from evals.complex_scenarios.scoring import default_tool_runner

    def _no_fx_runner(name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Stub tool runner that reports convert_currency as missing."""
        if name == "convert_currency":
            return {"ok": False, "error": "tool_missing", "tool_missing": True}
        return default_tool_runner(name, params)

    ts = score_turn(expect, response, tool_runner=_no_fx_runner)
    assert ts.figure_details[0].status == "fx_unavailable"
    assert ts.figure_details[1].status == "tool_missing"
    assert ts.figures == 0.0


def test_score_scenario_aggregates_turns() -> None:
    """Verify score_scenario averages turn scores and tracks critical_fail and truncated_turns."""
    scenario = {
        "id": "demo_scen",
        "tax_head": "paye",
        "difficulty": "hard",
        "description": "Expat PAYE scenario",
        "turns": [
            {
                "message": "Turn 1",
                "expect": {
                    "route": "rag",
                    "premise_flag": True,
                    "premise_cues": [{"any": ["183"]}, {"any": ["days"]}],
                    "must_include": [{"any": ["resident"]}],
                },
            },
            {
                "message": "Turn 2",
                "expect": {
                    "route": "rag",
                    "must_include": [{"any": ["20%"]}],
                },
            },
        ],
    }
    responses = [
        {
            "reply": "If they stay 183 days or more, they are a resident for tax purposes.",
            "retrieval_mode": "hybrid",
            "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
        },
        {
            "reply": "The motor vehicle formula uses 20% of the market value.",
            "retrieval_mode": "hybrid",
            "sources": [{"source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf"}],
        },
    ]
    summary = score_scenario(scenario, responses)
    assert summary["score"] == 1.0
    assert summary["score_100"] == 100.0
    assert summary["critical_fail"] is False
    assert summary["truncated_turns"] == 0
