"""Fresh and in-force figures across fiscal years (ADR-0033 audit, phase B).

* One clock. The rate tables and the kernel both read the date in Kampala, so
  at 00:30 on 1 July in Kampala (21:30 UTC on 30 June) both are in the new
  fiscal year.
* No silent rollover. After the last published table ends, answers still use
  it, and say so, until the next Finance Act's table is added.
* Unconfirmed figures say so in the answer, not only in a tool payload.
* Edition-aware precedence. A passage that contradicts the figure in force is
  ranked below the others. If it comes from an older edition, it is
  *superseded*: expected, not sent to reviewers. If it is unlabelled or as new
  as the figure, it is a *conflict*, and goes to the discrepancy queue. USD
  amounts and UGX penalty amounts are compared as well as percentages and UGX
  rates.
* The kernel's state is visible from outside the process on ``/ready``.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from app.kernel import precedence
from app.tax import tables

KAMPALA = _dt.timezone(_dt.timedelta(hours=3))


# --------------------------------------------------------------------------- one clock


def test_the_tables_change_year_at_midnight_in_kampala() -> None:
    late_utc = _dt.datetime(2026, 6, 30, 21, 30, tzinfo=_dt.UTC)
    assert tables.kampala_today(late_utc) == _dt.date(2026, 7, 1)
    assert tables.resolve_fiscal_year(tables.kampala_today(late_utc)) == "FY2026-27"
    assert tables.kampala_today(_dt.datetime(2026, 6, 30, 20, 59, tzinfo=_dt.UTC)) == _dt.date(2026, 6, 30)


def test_the_default_day_is_kampalas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tables, "kampala_today", lambda now=None: _dt.date(2026, 7, 1))
    assert tables.resolve_fiscal_year() == "FY2026-27"
    monkeypatch.setattr(tables, "kampala_today", lambda now=None: _dt.date(2026, 6, 30))
    assert tables.resolve_fiscal_year() == "FY2025-26"


def test_the_kernel_pin_and_the_tables_agree_on_the_day() -> None:
    from app.kernel import pin

    instant = _dt.datetime(2026, 6, 30, 21, 30, tzinfo=_dt.UTC)
    assert pin.today(instant) == tables.kampala_today(instant)


def test_no_frozen_current_year_constant() -> None:
    from app import query

    assert not hasattr(query, "CURRENT_FISCAL_YEAR"), "computed once at import, it went stale on 1 July"


# --------------------------------------------------------------------------- no silent rollover


@pytest.mark.parametrize(("day", "coverage"), [(_dt.date(2026, 8, 15), "in_force"), (_dt.date(2025, 7, 1), "in_force"),
                                               (_dt.date(2027, 7, 1), "after_latest"),
                                               (_dt.date(2024, 12, 31), "before_first")])
def test_coverage_says_when_no_table_covers_the_day(day: _dt.date, coverage: str) -> None:
    assert tables.coverage(day) == coverage


def _rate_reply(key: str) -> str:
    from app import calculator_router as cr

    reply, _ = cr.format_rate_reply(cr.RatePlan(tax_type=key), tables.get_table())
    return reply


def test_an_answer_after_the_last_table_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tables, "kampala_today", lambda now=None: _dt.date(2027, 7, 2))
    reply = _rate_reply("vat_standard")
    assert "FY2026-27" in reply
    assert "No rate table has been published" in reply


def test_an_answer_inside_the_year_has_no_rollover_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tables, "kampala_today", lambda now=None: _dt.date(2026, 11, 8))
    assert "No rate table has been published" not in _rate_reply("vat_standard")


def test_a_calculator_footer_after_the_last_table_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import calculator_router as cr

    monkeypatch.setattr(tables, "kampala_today", lambda now=None: _dt.date(2027, 7, 2))
    lines = cr._rate_table_footer({"fiscal_year": "FY2026-27", "rate_basis": {"status": "confirmed"}})
    assert any("No rate table has been published" in line for line in lines)


def test_an_unconfirmed_figure_says_so_in_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tables, "kampala_today", lambda now=None: _dt.date(2026, 11, 8))
    assert "not been confirmed against a gazetted schedule" in _rate_reply("motor_vehicle_transfer_fee")
    assert "not been confirmed against a gazetted schedule" not in _rate_reply("vat_standard")


# --------------------------------------------------------------------------- edition-aware precedence

BAGGAGE = {"key": "passenger_baggage_allowance_usd", "display_name": "Passenger baggage allowance", "value": 2000,
           "unit": "USD", "provision": "EACCMA Fifth Schedule", "fiscal_year": "FY2026-27"}
VAT = {"key": "vat_registration_threshold_annual", "display_name": "VAT registration threshold", "value": 300000000,
       "unit": "UGX", "provision": "VAT Act s.7", "fiscal_year": "FY2026-27"}
EFRIS = {"key": "efris_penalty_failure_to_issue_invoice", "display_name": "EFRIS penalty failure issue invoice",
         "value": 200000, "unit": "UGX", "provision": "TPCA s.93(2)", "fiscal_year": "FY2026-27"}


def _hit(text: str, **meta) -> dict:
    return {"text": text, "doc_type": "pdf_chunk", "source": meta.pop("source", "guide.pdf"), **meta}


def test_usd_figures_are_compared() -> None:
    hits = [_hit("The passenger baggage allowance is US$500 for each traveller returning to Uganda.")]
    [conflict] = precedence.find_conflicts([BAGGAGE], hits)
    assert conflict.evidence_value == "500 USD"


def test_ugx_penalty_amounts_are_compared() -> None:
    from app.graph.build import unit_for_key

    assert unit_for_key("efris_penalty_failure_to_issue_invoice", 200000) == "UGX"
    hits = [_hit("The EFRIS penalty for failure to issue an invoice is UGX 6,000,000 or double the tax.")]
    assert [c.evidence_value for c in precedence.find_conflicts([EFRIS], hits)] == ["6000000 UGX"]


@pytest.mark.parametrize(("meta", "status"), [
    ({"fiscal_year": "FY2024-25"}, "superseded"),
    ({"source": "Taxation-handbook-FY2023-24.pdf"}, "superseded"),
    ({"fiscal_year": "FY2026-27"}, "conflict"),
    ({}, "conflict"),
])
def test_an_older_edition_is_superseded_and_anything_else_is_a_conflict(meta: dict, status: str) -> None:
    hits = [_hit("The passenger baggage allowance is US$500 for each traveller returning to Uganda.", **meta)]
    [conflict] = precedence.find_conflicts([BAGGAGE], hits)
    assert conflict.status == status


def test_the_previous_years_figure_in_its_own_edition_is_superseded() -> None:
    hits = [_hit("The VAT registration threshold is UGX 150,000,000 a year.", fiscal_year="FY2025-26")]
    [conflict] = precedence.find_conflicts([VAT], hits)
    assert (conflict.status, conflict.evidence_edition) == ("superseded", "FY2025-26")


def test_contradicting_passages_rank_below_the_rest_and_only_conflicts_are_reported(monkeypatch) -> None:
    recorded: list = []
    monkeypatch.setattr(precedence, "record_conflicts", lambda conflicts, **_: recorded.extend(conflicts) or len(conflicts))
    monkeypatch.setattr(precedence, "facts_from_claims", lambda claims: [BAGGAGE])
    old = _hit("The passenger baggage allowance is US$500 for each traveller.", fiscal_year="FY2024-25", id="old")
    other = _hit("Declare goods to customs on arrival at Entebbe.", id="other")
    unlabelled = _hit("Passenger baggage allowance: US$1,000 per traveller.", id="unlabelled")
    current = _hit("The passenger baggage allowance is US$2,000 per traveller.", id="current")
    graph_hit = {"doc_type": "graph", "graph_claims": [{"subject": "x"}]}
    ordered = precedence.apply_precedence(graph_hit, [old, other, unlabelled, current])
    assert [h["id"] for h in ordered] == ["other", "current", "old", "unlabelled"]
    assert old["kernel_precedence"]["status"] == "superseded"
    assert unlabelled["kernel_precedence"]["status"] == "conflict"
    assert [c.evidence_value for c in recorded] == ["1000 USD"], "a superseded edition is expected, not a review item"
    assert "kernel_precedence" not in other


def test_precedence_never_takes_retrieval_down(monkeypatch) -> None:
    def broken(_claims):
        raise RuntimeError("kernel unavailable")

    monkeypatch.setattr(precedence, "facts_from_claims", broken)
    hits = [_hit("a"), _hit("b")]
    assert precedence.apply_precedence({"graph_claims": [{}]}, hits) == hits

    def reorder_fails(_graph_hit, _hits):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(precedence, "_check", reorder_fails)
    assert precedence.apply_precedence({"graph_claims": [{}]}, hits) is hits


def test_both_fusion_paths_apply_precedence() -> None:
    from pathlib import Path

    root = Path(precedence.__file__).resolve().parents[1]
    for path in (root / "service.py", root / "agents/graphs/main_graph.py"):
        source = path.read_text(encoding="utf-8")
        assert "apply_precedence(" in source, path.name


# --------------------------------------------------------------------------- visible state


def test_the_kernel_state_is_reported_for_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.kernel.status import readiness_capabilities

    for name in ("GRAPH_STORE_BACKEND", "FLAG_SEMANTIC_LAYER"):
        monkeypatch.delenv(name, raising=False)
    caps = readiness_capabilities()
    assert caps["kernel"].startswith("1.2.")
    assert " released " in caps["kernel"]
    assert caps["graph_store"] == "memory"
    assert caps["semantic_layer"] == "off"
    assert caps["rate_tables"].startswith("FY2025-26..FY2026-27")
    assert all(isinstance(v, str) for v in caps.values())


def test_ready_includes_the_kernel_state() -> None:
    from pathlib import Path

    main = (Path(precedence.__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "readiness_capabilities()" in main
