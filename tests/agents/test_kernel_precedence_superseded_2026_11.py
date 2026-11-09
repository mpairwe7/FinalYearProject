"""Superseded figures stay out of answers (G534, ADR-0033 amendment).

On 2026-11-09 the dense leg (ADR-0034) put an EACCMA Fifth Schedule passage
stating the replaced USD 500 baggage allowance in front of the model, and the
live Space answered USD 500 instead of the verified USD 2,000. Precedence
matched passages to a fact by the fact's name, which the paraphrase never
used, and it only moved a contradicting passage down, so the model could
still quote it.
"""

from __future__ import annotations

import pytest
from app.kernel import precedence

#: The passage the live Space served, verbatim.
LIVE_PASSAGE = (
    "Under the Fifth Schedule of the East African Community Customs Management Act (EACCMA), "
    "bona fide international travelers arriving in Uganda are entitled to import personal effects "
    "and unused accompanied general goods not exceeding USD 500 in customs value."
)
BAGGAGE = {
    "key": "passenger_baggage_allowance_usd",
    "display_name": "Passenger baggage duty-free allowance (USD)",
    "value": 2000, "unit": "USD", "fiscal_year": "FY2026-27",
    "provision": "EACCMA Fifth Schedule Part B item 5",
    "legal_basis": ("East African Community Customs Management Act 2004, Fifth Schedule Part B item 5, as amended: "
                    "goods up to USD 2,000 per traveller outside Uganda for more than 24 hours, in accompanied "
                    "baggage and declared. Raised from USD 500 with effect from 1 July 2024."),
}


def _hit(text: str, **extra: str) -> dict:
    return {"text": text, "doc_type": "pdf_chunk", "source": "eaccma.pdf", **extra}


def test_the_live_paraphrase_is_a_conflict() -> None:
    [conflict] = precedence.find_conflicts([BAGGAGE], [_hit(LIVE_PASSAGE)])
    assert (conflict.evidence_value, conflict.kernel_value, conflict.reason) == ("500 USD", "2000 USD", "former_value")


@pytest.mark.parametrize("text", [
    "Goods up to the value of United States Dollars two thousand (USD 2,000) imported by a bona fide traveler.",
    "The allowance was raised from USD 500 to USD 2,000 on 1 July 2024.",  # states the current figure
    "Cash above USD 10,000 must be declared at the border.",               # another figure, not a former one
    # States the current figure, so it is current; its other USD 500 is about something else.
    "The passenger baggage duty-free allowance is USD 2,000; items under USD 500 need no receipt.",
])
def test_passages_that_do_not_restate_the_old_figure_are_kept(text: str) -> None:
    assert precedence.find_conflicts([BAGGAGE], [_hit(text)]) == []


def test_a_past_tense_mention_of_only_the_old_figure_is_left_out() -> None:
    """It offers nothing current, and the kernel holds the current figure."""
    found = precedence.find_conflicts([BAGGAGE], [_hit("Previously travellers could bring USD 500 of goods.")])
    assert [c.evidence_value for c in found] == ["500 USD"]


def test_a_date_is_not_a_former_value() -> None:
    assert precedence._former_values("with effect from 1 July 2024") == {}
    assert precedence._former_values("Raised from USD 500; threshold moved from UGX 150,000,000; rate from 18%") == {
        "USD": {500.0}, "UGX": {150_000_000.0}, "percent": {18.0}}


def test_a_former_value_only_counts_in_the_fact_s_unit() -> None:
    vat = {"key": "vat_standard", "display_name": "VAT (standard rate)", "value": 0.18, "unit": "ratio",
           "fiscal_year": "FY2026-27", "provision": "VATA s.24", "legal_basis": "Raised from USD 500 (sic)."}
    assert precedence.find_conflicts([vat], [_hit("Travellers may import goods worth USD 500.")]) == []


def test_another_year_s_fact_does_not_flag_its_own_figure() -> None:
    """FY2025-26's threshold is UGX 150,000,000; its basis records no replaced value."""
    fy26 = {"key": "vat_registration_threshold_annual", "display_name": "VAT registration threshold",
            "value": 150_000_000, "unit": "UGX", "fiscal_year": "FY2025-26",
            "provision": "VATA s.7", "legal_basis": "Value Added Tax Act s.7(1)."}
    assert precedence.find_conflicts([fy26], [_hit("Register once turnover passes UGX 150,000,000.")]) == []


def test_the_contradicting_passage_never_reaches_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(precedence, "record_conflicts", lambda conflicts, **_: len(conflicts))
    monkeypatch.setattr(precedence, "facts_from_claims", lambda _claims: [BAGGAGE])
    stale = _hit(LIVE_PASSAGE, id="stale")
    current = _hit("Goods up to USD 2,000 per traveller are duty-free.", id="current")
    graph = {"doc_type": "graph", "graph_claims": [{"subject": "Passenger baggage duty-free allowance (USD)"}], "id": "g"}
    served = precedence.apply_precedence(graph, [stale, graph, current])
    assert [h["id"] for h in served] == ["g", "current"]
    [dropped] = graph["kernel_precedence_dropped"]
    assert (dropped["stated_value"], dropped["reason"], dropped["source"]) == ("500 USD", "former_value", "eaccma.pdf")


def test_facts_carry_the_full_legal_basis() -> None:
    [fact] = precedence.facts_from_claims([{
        "subject": "Passenger baggage duty-free allowance (USD)",
        "fiscal_year": "FY2026-27", "provision": "EACCMA Fifth Schedule"}])
    assert "from USD 500" in fact["legal_basis"], "the short citation alone loses the replaced value"
