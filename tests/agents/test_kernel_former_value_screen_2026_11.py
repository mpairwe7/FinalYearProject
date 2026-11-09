"""A replaced figure stays out of answers on every path (G535, ADR-0033 amendment).

On 2026-11-09, with the dense leg off, "Under the EACCMA Fifth Schedule, what
value of accompanied goods is exempt for a traveller?" was still answered USD
500. A FAQ row stated the replaced allowance, the graph leg found no fact for a
question that never names it, and precedence needs a graph hit. The screen
runs on every retrieval result and every blended FAQ row, graph hit or not.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from app.kernel import precedence

ROOT = Path(__file__).resolve().parents[2]

#: The FAQ row the live Space served, as it read before the fix.
STALE_BAGGAGE_ROW = (
    "What is the duty-free passenger baggage allowance for travelers at Entebbe Airport? Under the Fifth Schedule "
    "of the East African Community Customs Management Act (EACCMA), bona fide international travelers arriving in "
    "Uganda are entitled to import personal effects and unused accompanied general goods not exceeding USD 500 in "
    "customs value free of import duty and VAT."
)


def _faq(text: str, source: str = "faq.csv") -> dict:
    return {"text": text, "doc_type": "csv", "source": source}


@pytest.mark.parametrize("text", [
    STALE_BAGGAGE_ROW,
    "EFRIS is not mandatory for small businesses with annual turnover below the VAT registration threshold of "
    "UGX 150,000,000.",
    "Event owners with annual turnover exceeding UGX 150 million (or UGX 37.5M in 3 months) must register for VAT.",
    "If an arriving passenger's purchases exceed the USD 500 duty-free personal allowance, duties must be paid.",
])
def test_a_replaced_figure_is_screened_without_a_graph_hit(text: str) -> None:
    kept = _faq("Declare goods at the Red Channel.")
    assert precedence.screen_former_values([_faq(text), kept], fiscal_year="FY2026-27") == [kept]


@pytest.mark.parametrize("text", [
    # The presumptive-tax ceiling in force is the same number as the replaced VAT threshold.
    "Sole proprietors with annual gross sales between UGX 10,000,000 and UGX 150,000,000 pay presumptive tax.",
    "What is the presumptive regime? Resident businesses with annual turnover above UGX 10m and not exceeding "
    "UGX 150m pay presumptive tax.",
    # Another rule with the replaced refund threshold's number.
    "Every expense transaction exceeding UGX 5,000,000 must include the TIN of the seller on the e-invoice.",
    # The current figures, and the old ones framed as history.
    "Goods up to USD 2,000 per traveller are duty-free; the allowance was raised from USD 500 on 1 July 2024.",
    "Register for VAT once turnover exceeds UGX 75m in 3 months (UGX 300m a year, raised from UGX 150m).",
    # States the current figure by name; its other USD 500 is about something else.
    "The passenger baggage duty-free allowance is USD 2,000; items under USD 500 need no receipt.",
])
def test_current_figures_and_other_subjects_are_kept(text: str) -> None:
    assert precedence.screen_former_values([_faq(text)], fiscal_year="FY2026-27") == [_faq(text)]


def test_every_fact_with_a_replaced_value_has_subject_cues() -> None:
    """A number alone cannot say which figure it is; a new replaced value needs its cues."""
    from app.tax import tables

    for fiscal_year in ("FY2025-26", "FY2026-27"):
        table = tables.get_table(fiscal_year)
        for key, basis in table.legal_basis.items():
            if precedence._former_values(str(basis)):
                assert key in precedence._SUBJECT_CUES, f"{fiscal_year} {key} records a replaced value"


def test_promotion_screens_even_without_a_graph_hit() -> None:
    stale, fine = _faq(STALE_BAGGAGE_ROW, "starter.csv"), _faq("Declare goods at the Red Channel.", "x.csv")
    assert precedence.reorder_after_promotion([stale, fine]) == [fine]


def test_a_graph_hit_lists_what_the_screen_left_out() -> None:
    graph = {"doc_type": "graph", "graph_claims": [], "id": "g"}
    kept = precedence.screen_former_values([graph, _faq(STALE_BAGGAGE_ROW, "starter.csv")], fiscal_year="FY2026-27")
    assert kept == [graph]
    [entry] = graph["kernel_precedence_dropped"]
    assert (entry["key"], entry["stated_value"], entry["source"]) == ("passenger_baggage_allowance_usd", "500 USD",
                                                                     "starter.csv")


def test_every_retrieval_caller_gets_the_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.retriever import HybridRetriever

    retriever = HybridRetriever.__new__(HybridRetriever)
    fine = _faq("Declare goods at the Red Channel.")
    monkeypatch.setattr(HybridRetriever, "_search_planned", lambda self, *a, **k: [_faq(STALE_BAGGAGE_ROW), fine])
    assert retriever.search_planned("what is exempt for a traveller") == [fine]


def test_the_screen_never_takes_retrieval_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_fiscal_year):
        raise RuntimeError("kernel unavailable")

    monkeypatch.setattr(precedence, "_facts_with_former_values", broken)
    hits = [_faq(STALE_BAGGAGE_ROW)]
    assert precedence.screen_former_values(hits) is hits


def test_no_faq_row_states_a_replaced_figure_as_current() -> None:
    """The corpus itself: 11 rows stated the replaced allowance or VAT threshold until 2026-11-09."""
    rows = []
    for path in sorted((ROOT / "Data/dataset").glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            rows += [_faq(f"{row[0]} {row[1]}", f"{path.name}:{n}") for n, row in enumerate(reader, 2) if len(row) >= 2]
    kept = precedence.screen_former_values(rows, fiscal_year="FY2026-27")
    stale = [h["source"] for h in rows if h not in kept]
    assert stale == [], f"rows stating a replaced figure: {stale}"


# --------------------------------------------------------------------------- review fixes (PR 710)


@pytest.mark.parametrize("text", [
    # The current figure in words, with the replaced number used for something else.
    "The VAT registration threshold is UGX 300 million a year; a shop with UGX 150 million turnover need not register.",
    # Another per-passenger charge with the replaced allowance's number.
    "Passengers chartering a private aircraft pay a USD 500 landing permit fee to the aviation authority.",
])
def test_review_cases_are_kept(text: str) -> None:
    assert precedence.screen_former_values([_faq(text)], fiscal_year="FY2026-27") == [_faq(text)]


def test_cached_facts_follow_the_store_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reloaded table is a new store, even if Python reuses the old one's id()."""
    from app.kernel import functions

    class _Node:
        def __init__(self, value: float) -> None:
            self.props = {"key": "passenger_baggage_allowance_usd", "display_name": "Passenger baggage allowance",
                          "value": value, "unit": "USD", "fiscal_year": "FY2026-27",
                          "legal_basis": f"Goods up to USD {value:,.0f}. Raised from USD 500."}

    class _Store:
        def __init__(self, value: float) -> None:
            self._node = _Node(value)

        def by_kind(self, kind) -> list:
            from app.graph.schema import NodeKind

            return [self._node] if kind == NodeKind.THRESHOLD else []

    first, second = _Store(2000), _Store(3000)
    monkeypatch.setattr(precedence, "_FORMER_FACTS_CACHE", {})
    monkeypatch.setattr(functions, "kernel_store", lambda: first)
    assert [f["value"] for f in precedence._facts_with_former_values("FY2026-27")] == [2000]
    monkeypatch.setattr(functions, "kernel_store", lambda: second)
    assert [f["value"] for f in precedence._facts_with_former_values("FY2026-27")] == [3000]


def test_no_portal_navigator_text_states_a_replaced_figure_as_current() -> None:
    """The navigator's guides gave the replaced allowance and VAT threshold too (G535)."""
    import ast

    source = (ROOT / "App/backend/app/tools/portal_navigator_data.py").read_text(encoding="utf-8")
    strings = [node.value for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 30]
    hits = [{"text": s, "doc_type": "portal", "source": "portal_navigator_data.py"} for s in strings]
    kept = precedence.screen_former_values(hits, fiscal_year="FY2026-27")
    assert [h["text"][:120] for h in hits if h not in kept] == []
    # The navigator's "USD 2,000 TIN rule" shares the allowance's number, so a string stating
    # it looks current to the screen; the replaced allowance may appear only as history.
    import re

    stale = [s[:120] for s in strings for m in re.finditer(r"USD\s?500(?![\d,])", s)
             if not s[max(0, m.start() - 5):m.start()].lower().endswith("from ")]
    assert stale == []
