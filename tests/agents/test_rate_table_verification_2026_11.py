"""Rate-table figures verified against original documents (ADR-0033 audit, 2026-11-08).

The 14 figures that had no legal basis were checked against URA's published
law and guidance in ``Data/pdfs``. Seven were wrong or stale and are corrected;
one could not be confirmed and is served as unverified. The evidence ledger
``app/tax/evidence/rate_evidence.json`` quotes each page, and these tests
re-read the page, so the verification can be repeated rather than trusted.

Also checked: figures the law defines in terms of each other (the quarterly
VAT test is a quarter of the annual threshold), and that no answer template
states a figure the table does not.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from app.graph.store import InMemoryGraphStore
from app.kernel import functions
from app.kernel.validate import check
from app.tax import tables

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = json.loads((ROOT / "App/backend/app/tax/evidence/rate_evidence.json").read_text(encoding="utf-8"))
TABLES = {fy: json.loads((ROOT / f"App/backend/app/tax/data/{fy}.json").read_text(encoding="utf-8"))
          for fy in ("FY2025-26", "FY2026-27")}
OPEN_KEYS = {item["key"] for item in EVIDENCE["open"]}


def _page_text(document: str, page: int) -> str:
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(ROOT / document) as doc:
        return " ".join(doc[page - 1].get_text("text").split())


@pytest.mark.parametrize("fy", sorted(TABLES))
def test_every_figure_is_cited_or_declared_open(fy: str) -> None:
    table = TABLES[fy]
    uncited = {k for k in table["rates"] if not table["legal_basis"].get(k)}
    assert uncited <= OPEN_KEYS, f"uncited figures not in the evidence ledger's open list: {sorted(uncited - OPEN_KEYS)}"
    for key in uncited:
        assert key in table["unverified"], key
        assert table["notes"].get(key), f"{key} is open and must say why"


@pytest.mark.parametrize("entry", EVIDENCE["documents"], ids=lambda e: f"{e['key']}-p{e['page']}")
def test_document_evidence_is_on_its_page_and_matches_the_table(entry: dict) -> None:
    text = _page_text(entry["document"], entry["page"])
    assert entry["quote"] in text, f"quote not found on {entry['document']} p.{entry['page']}"
    assert entry["needle"] in entry["quote"]
    for fy in entry["fiscal_years"]:
        assert TABLES[fy]["rates"][entry["key"]] == entry["value"], (fy, entry["key"])
        assert entry["key"] not in TABLES[fy]["unverified"] or entry["key"] in {w["key"] for w in EVIDENCE["web"]}


@pytest.mark.parametrize("entry", EVIDENCE["web"], ids=lambda e: e["key"])
def test_web_evidence_matches_and_stays_unverified(entry: dict) -> None:
    for fy in entry["fiscal_years"]:
        assert TABLES[fy]["rates"][entry["key"]] == entry["value"]
        assert entry["key"] in TABLES[fy]["unverified"], "an FAQ page is not a gazetted schedule"
        assert TABLES[fy]["legal_basis"][entry["key"]]


@pytest.mark.parametrize("entry", [e for e in EVIDENCE["documents"] + EVIDENCE["web"] if "previous_value" in e],
                         ids=lambda e: e["key"])
def test_a_corrected_figure_does_not_come_back(entry: dict) -> None:
    for fy in entry["fiscal_years"]:
        assert TABLES[fy]["rates"][entry["key"]] != entry["previous_value"]


def test_the_open_item_is_contradicted_by_the_last_schedule_found() -> None:
    for item in EVIDENCE["open"]:
        q = item["contradicting_quote"]
        assert q["needle"] in _page_text(q["document"], q["page"])
        for fy in item["fiscal_years"]:
            assert item["key"] in TABLES[fy]["unverified"]


def test_a_stale_dependent_figure_fails_the_kernel() -> None:
    raw = functions.kernel_store().to_dict()
    node = next(n for n in raw["nodes"] if n["id"].endswith("vat_quarterly_registration_threshold@FY2026-27"))
    node["props"]["value"] = 37_500_000
    violations = check(InMemoryGraphStore.from_dict(raw), against_tables=False)
    assert [v.code for v in violations] == ["derived"]
    assert check(functions.kernel_store()) == []


def test_two_floors_for_one_efris_penalty_fail_the_kernel() -> None:
    raw = functions.kernel_store().to_dict()
    node = next(n for n in raw["nodes"] if n["id"].endswith("efris_penalty_failure_to_issue_invoice@FY2026-27"))
    node["props"]["value"] = 6_000_000
    assert [v.code for v in check(InMemoryGraphStore.from_dict(raw), against_tables=False)] == ["derived"]


# --------------------------------------------------------------------------- answer templates


def _render(key: str, fy: str) -> str:
    from app import calculator_router as cr

    reply, _ = cr.format_rate_reply(cr.RatePlan(tax_type=key), tables.get_table(fy))
    return reply


@pytest.mark.parametrize(
    ("key", "fy", "present", "absent"),
    [
        ("passenger_baggage_allowance", "FY2026-27", ["USD 2,000", "24 hours"], ["USD 500"]),
        ("passenger_baggage_allowance", "FY2025-26", ["USD 2,000"], ["USD 500"]),
        ("vat_quarterly_registration_threshold", "FY2026-27", ["UGX 75,000,000", "UGX 300,000,000"], ["37,500,000"]),
        ("vat_registration_threshold_annual", "FY2026-27", ["UGX 300,000,000"], ["150,000,000"]),
        ("efris_penalty_failure_to_issue_invoice", "FY2026-27", ["double the tax due or UGX 200,000"], ["6,000,000", "19B"]),
        ("efris_penalty_failure_to_issue_invoice", "FY2025-26", ["the tax due or UGX 6,000,000"], ["double", "19B"]),
        ("efris_invoicing_mandate", "FY2026-27", ["UGX 200,000"], ["6,000,000", "19B"]),
        ("efris_tampering_penalty", "FY2026-27", ["UGX 200,000"], ["10,000,000", "5 years"]),
        ("efris_offline_sync_window_hours", "FY2026-27", ["5 days"], ["24 hours", "24-hour"]),
        ("motor_vehicle_transfer_fee", "FY2026-27", ["UGX 84,000", "UGX 104,000", "UGX 74,000", "UGX 200,000"],
         ["100,000", "UGX 15,000"]),
        ("motor_vehicle_duplicate_logbook_fee", "FY2026-27", ["not confirmed", "Ministry of Works"], []),
        ("excise_duty_fuel_summary", "FY2026-27", ["UGX 1,750", "UGX 1,430"], ["1,450", "1,130"]),
        ("paye_secondary_employment_rate", "FY2026-27", ["UGX 335,000"], ["235,000"]),
    ],
)
def test_templates_state_the_years_figures(key: str, fy: str, present: list[str], absent: list[str]) -> None:
    reply = _render(key, fy)
    for text in present:
        assert text in reply, (key, fy, text)
    for text in absent:
        assert text not in reply, (key, fy, text)


def test_no_template_for_a_table_figure_writes_its_own_amount() -> None:
    src = (ROOT / "App/backend/app/calculator_router.py").read_text(encoding="utf-8")
    keys = set(TABLES["FY2025-26"]["rates"]) | set(TABLES["FY2026-27"]["rates"])
    found = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "descriptions" for t in node.targets):
            for k, v in zip(node.value.keys, node.value.values, strict=True):
                if isinstance(k, ast.Constant) and k.value in keys and isinstance(v, ast.Constant):
                    literal = re.findall(r"(?:UGX|USD|US\$)\s?[\d,]{3,}", re.sub(r"\{[^}]*\}", "", v.value))
                    if literal:
                        found.append((k.value, literal))
    assert found == [], "render these from the table (calculator_router._table_figures)"


def test_land_transfer_stamp_duty_reaches_the_mda_resolver() -> None:
    from app.tools import mda_interdependence

    src = Path(mda_interdependence.__file__).read_text(encoding="utf-8")
    assert "or 0.01" not in src
    assert '"stamp_duty_rate"' not in src
    assert tables.get_table("FY2026-27").get("stamp_duty_property_transfer") == 0.015
