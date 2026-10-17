"""Unit tests for the deterministic complex-scenario scorer (Phase A.4)."""

from __future__ import annotations

from typing import Any

from evals.complex_scenarios.scoring import score_scenario, score_turn


def _paye_expect(*, include_net_claim: bool = False) -> dict[str, Any]:
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
            "Suppose aresident company has a balance of UGX 7,688,250 in its account. "
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
    # Resident PAYE on 22M is 7,688,250 and net_take_home is 14,311,750.
    # Here the reply swaps them!
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
    # Force convert_currency to be unavailable so we test the fx_unavailable skip path
    from evals.complex_scenarios.scoring import default_tool_runner

    def _no_fx_runner(name: str, params: dict[str, Any]) -> dict[str, Any]:
        if name == "convert_currency":
            return {"ok": False, "error": "tool_missing", "tool_missing": True}
        return default_tool_runner(name, params)

    ts = score_turn(expect, response, tool_runner=_no_fx_runner)
    assert ts.figure_details[0].status == "fx_unavailable"
    assert ts.figure_details[1].status == "tool_missing"
    # Only the second figure spec is active (since the first has required=False and fx_unavailable)
    assert ts.figures == 0.0


def test_score_scenario_aggregates_turns() -> None:
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
