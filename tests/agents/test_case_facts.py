"""Unit tests for Phase F.1: CaseFacts extraction, merge, provenance, and persistence."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any

import pytest
from app.case_facts import (
    CaseFacts,
    Money,
    clear_case_facts,
    export_user_case_facts,
    extract_all_money,
    extract_case_facts,
    forget_user_case_facts,
    recall_case_facts,
    remember_case_facts,
)


# ---------------------------------------------------------------------------
# 1. Amount & currency parsing table (>= 40 phrasings across EN + LG + SW)
# ---------------------------------------------------------------------------
AMOUNT_PHRASING_CASES: list[tuple[str, Decimal, str]] = [
    # USD phrasings
    ("We pay him $6,000 per month", Decimal("6000"), "USD"),
    ("Monthly salary of $6,000.50", Decimal("6000.50"), "USD"),
    ("Retainer is USD 6k monthly", Decimal("6000"), "USD"),
    ("Paid 6,000 dollars a month", Decimal("6000"), "USD"),
    ("Salary of US$ 8,500 per month", Decimal("8500"), "USD"),
    ("Contract fee of $1.2m", Decimal("1200000"), "USD"),
    ("Annual package of 120,000 US dollars", Decimal("120000"), "USD"),
    ("Invoice for USD 45,000", Decimal("45000"), "USD"),
    ("Shipment FOB value $1,800", Decimal("1800"), "USD"),
    ("Consultation fee of 2.5k dollars", Decimal("2500"), "USD"),
    # EUR / GBP / KES phrasings
    ("Grant of EUR 4,500 per month", Decimal("4500"), "EUR"),
    ("Paid €12,000 for software", Decimal("12000"), "EUR"),
    ("Fee of 3,200 euros", Decimal("3200"), "EUR"),
    ("Stipend of GBP 3,000 monthly", Decimal("3000"), "GBP"),
    ("Invoice for £7,500", Decimal("7500"), "GBP"),
    ("Paid 5,000 pounds sterling", Decimal("5000"), "GBP"),
    ("Cross-border freight of KES 500,000", Decimal("500000"), "KES"),
    ("KSh 250k transport invoice", Decimal("250000"), "KES"),
    ("Paid 800,000 Kenyan shillings", Decimal("800000"), "KES"),
    # UGX standard & shorthand phrasings
    ("Gross pay is UGX 1.5m per month", Decimal("1500000"), "UGX"),
    ("He earns 1.5 million shillings a month", Decimal("1500000"), "UGX"),
    ("Housing allowance of Shs 900,000", Decimal("900000"), "UGX"),
    ("Monthly gross salary UGX 4,500,000", Decimal("4500000"), "UGX"),
    ("Annual turnover of UGX 350m", Decimal("350000000"), "UGX"),
    ("Turnover is 180 million shillings per year", Decimal("180000000"), "UGX"),
    ("Rent collected is Shs. 24,000,000 per year", Decimal("24000000"), "UGX"),
    ("Car market value of UGX 80 million", Decimal("80000000"), "UGX"),
    ("Loan of UGX 15,000,000 from employer", Decimal("15000000"), "UGX"),
    ("Payment of Ush 750k to supplier", Decimal("750000"), "UGX"),
    ("Sale price of 1.2 billion shillings", Decimal("1200000000"), "UGX"),
    ("Capital of UGX 500bn", Decimal("500000000000"), "UGX"),
    (" domestic staff paid 600,000/= a month", Decimal("600000"), "UGX"),
    ("Utility bill of 350,000/- per month", Decimal("350000"), "UGX"),
    # Luganda scale + number words
    ("Omusaala gwa obukadde 5 buli mwezi", Decimal("5000000"), "UGX"),
    ("Afuna obukadde bitaano buli mwezi", Decimal("5000000"), "UGX"),
    ("Ssente obukadde bubiri", Decimal("2000000"), "UGX"),
    ("Asasula emitwalo 50", Decimal("500000"), "UGX"),
    ("Emitwalo amakumi abiri", Decimal("200000"), "UGX"),
    ("Enkumi ataano", Decimal("50000"), "UGX"),
    ("Akakadde kamu", Decimal("1000000"), "UGX"),
    # Swahili scale + number words
    ("Mshahara wa milioni 4 kwa mwezi", Decimal("4000000"), "UGX"),
    ("Mapato ya milioni tatu kwa mwezi", Decimal("3000000"), "UGX"),
    ("Kodi ya laki 8", Decimal("800000"), "UGX"),
    ("Malipo ya laki mbili", Decimal("200000"), "UGX"),
    ("Elfu hamsini kwa mwezi", Decimal("50000"), "UGX"),
]


@pytest.mark.parametrize(("text", "expected_amount", "expected_currency"), AMOUNT_PHRASING_CASES)
def test_extract_all_money_phrasings(
    text: str, expected_amount: Decimal, expected_currency: str
) -> None:
    """Verify >= 40 money phrasings across USD/EUR/GBP/KES/UGX and Luganda/Swahili number words."""
    found = extract_all_money(text)
    assert found, f"No money extracted from: {text!r}"
    mon = found[0][0]
    assert mon.amount == expected_amount, f"{text!r} -> {mon.amount} != {expected_amount}"
    assert mon.currency == expected_currency, f"{text!r} -> {mon.currency} != {expected_currency}"


# ---------------------------------------------------------------------------
# 2. Contract length vs physical presence in Uganda
# ---------------------------------------------------------------------------
def test_contract_months_does_not_fabricate_days_in_uganda() -> None:
    """'8-month contract' sets contract_months=8 and leaves days_in_uganda=None (§8.F.1)."""
    msg = (
        "We are hiring an expat on an 8-month contract and paying him $6,000 a month "
        "plus a company car and a rent-free house. Since he's a non-resident, how much PAYE do we deduct?"
    )
    facts = extract_case_facts(msg)
    assert facts.contract_months == 8
    assert facts.days_in_uganda is None
    assert facts.residency_claim == "non_resident"
    assert facts.monthly_salary is not None
    assert facts.monthly_salary.amount == Decimal("6000")
    assert facts.monthly_salary.currency == "USD"
    benefit_kinds = {b.kind for b in facts.benefits}
    assert {"car", "housing"} <= benefit_kinds


def test_explicit_presence_days_and_date_spans_populate_days_in_uganda() -> None:
    """Explicit day counts or Uganda presence date spans populate days_in_uganda."""
    f1 = extract_case_facts("He will be physically present in Uganda for 240 days.")
    assert f1.days_in_uganda == 240

    f2 = extract_case_facts("She is staying in Uganda for 8 months on a project.")
    assert f2.days_in_uganda == 240

    f3 = extract_case_facts("The consultant is in Uganda from March to October 2026.")
    assert f3.period_start == _dt.date(2026, 3, 1)
    assert f3.period_end == _dt.date(2026, 10, 31)
    assert f3.days_in_uganda == 245

    # Procedural deadlines ("within 45 days to object") must NOT become days_in_uganda
    f4 = extract_case_facts("Can I object within 45 days of receiving the assessment notice?")
    assert f4.days_in_uganda is None


# ---------------------------------------------------------------------------
# 3. Cross-turn merge, explicit override, and provenance tracking
# ---------------------------------------------------------------------------
def test_cross_turn_merge_override_and_provenance() -> None:
    """Later turns merge with earlier facts, override changed fields, and record turn provenance."""
    t1 = extract_case_facts(
        "We have a non-resident consultant on an 8-month contract earning $6,000 per month.",
        prior=None,
    )
    assert t1.turn_index == 1
    assert t1.residency_claim == "non_resident"
    assert t1.contract_months == 8
    assert t1.days_in_uganda is None
    assert t1.provenance["residency_claim"] == 1
    assert t1.provenance["monthly_salary"] == 1

    t2 = extract_case_facts(
        "Actually he will be in Uganda for 210 days and has a company car worth UGX 60m when first provided.",
        prior=t1,
    )
    assert t2.turn_index == 2
    # Prior salary and contract_months are preserved from turn 1
    assert t2.monthly_salary is not None
    assert t2.monthly_salary.amount == Decimal("6000")
    assert t2.monthly_salary.currency == "USD"
    assert t2.provenance["monthly_salary"] == 1
    # New facts from turn 2
    assert t2.days_in_uganda == 210
    assert t2.provenance["days_in_uganda"] == 2
    assert any(b.kind == "car" and b.market_value and b.market_value.amount == Decimal("60000000") for b in t2.benefits)

    t3 = extract_case_facts(
        "Correction: he is a tax resident and his salary is UGX 18,000,000 per month.",
        prior=t2,
    )
    assert t3.turn_index == 3
    assert t3.residency_claim == "resident"
    assert t3.provenance["residency_claim"] == 3
    assert t3.monthly_salary is not None
    assert t3.monthly_salary.amount == Decimal("18000000")
    assert t3.monthly_salary.currency == "UGX"
    assert t3.provenance["monthly_salary"] == 3


# ---------------------------------------------------------------------------
# 4. Persistence, PII redaction, and /v1/me export + erasure
# ---------------------------------------------------------------------------
def test_persistence_redaction_and_me_export_erasure() -> None:
    """CaseFacts persist per conversation, redact TINs in phrases, and support /v1/me export & erasure."""
    conv_id = "conv-test-case-facts-01"
    user_id = "user-case-facts-01"
    clear_case_facts(conv_id, user_id=user_id, tenant_id="default")

    facts = extract_case_facts(
        "My TIN is 1000123456 and my monthly salary is UGX 5,000,000 with a housing allowance of UGX 900,000."
    )
    # Raw TIN must be redacted in stored phrase
    for mon in facts.amounts:
        assert "1000123456" not in mon.phrase

    remember_case_facts(conv_id, facts, user_id=user_id, tenant_id="default")
    loaded = recall_case_facts(conv_id, user_id=user_id, tenant_id="default")
    assert loaded is not None
    assert loaded.monthly_salary is not None
    assert loaded.monthly_salary.amount == Decimal("5000000")

    exported = export_user_case_facts(user_id, tenant_id="default")
    assert len(exported) >= 1
    assert exported[0]["facts"]["monthly_salary"]["amount"] == "5000000"

    erased_count = forget_user_case_facts(user_id, tenant_id="default")
    assert erased_count >= 1
    assert recall_case_facts(conv_id, user_id=user_id, tenant_id="default") is None
    assert export_user_case_facts(user_id, tenant_id="default") == []
