"""Unit tests for Phase F.2, F.3, F.5, and F.6: scenario planner, verified-facts block, and contradiction checks."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from App.backend.tests.test_calculator_router import ExplanationVsCalculationRoutingTests  # noqa: F401
from app import service as service_module
from app.case_facts import extract_case_facts
from app.premise_guard import check_fact_contradictions
from app.scenario_planner import build_verified_facts, plan_tools
from app.tools import ToolRegistry


def test_section1_expat_case_end_to_end_with_mocked_tools() -> None:
    """§1 turn 1 case: 8-month contract, $6,000/month, house + car, user assumes non-resident."""
    msg = (
        "We are hiring a foreign technical specialist on an 8-month contract in Entebbe. "
        "They will receive a gross monthly salary of $6,000 plus employer-paid housing and "
        "a company vehicle. How is PAYE calculated for a non-resident individual, and does "
        "the non-resident tax table differ from resident individuals?"
    )
    facts = extract_case_facts(msg)
    called_tools: list[str] = []

    def _mock_caller(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        called_tools.append(tool_name)
        return ToolRegistry.call(tool_name, params)

    block = build_verified_facts(msg, facts, tool_caller=_mock_caller)
    assert "## Verified Statutory Facts & Calculations" in block
    assert "determine_residency" in called_tools
    assert "convert_currency" in called_tools
    assert "LIKELY RESIDENT" in block
    assert "Your premise:" in block
    assert "183" in block
    assert "Car benefit:" in block
    assert "Housing benefit:" in block
    assert "[interpretation unverified]" in block
    assert "37,250" in block
    assert "Still needed for an exact figure:" in block
    assert "market value of the car" in block
    assert "market rent of the house" in block
    assert "exchange rate" in block


def test_paye_with_verified_car_and_unverified_housing_splits_verified_vs_conditional() -> None:
    """When car (verified) and housing (unverified) values are supplied, PAYE verified excludes housing and PAYE conditional includes it."""
    t1 = extract_case_facts(
        "He is physically present in Uganda for 240 days earning UGX 20,000,000 per month, "
        "with a company car worth UGX 60,000,000 when first provided and employer-paid housing "
        "with market rent of UGX 3,000,000 per month."
    )
    block = build_verified_facts(
        "How is his monthly PAYE calculated with the car and housing benefits?",
        t1,
    )
    assert "Residency: RESIDENT" in block
    assert "PAYE verified (resident, FY2026-27)" in block
    assert "PAYE conditional on unverified housing rule" in block
    assert "(conditional on unverified rule)" in block


@pytest.mark.parametrize(
    ("question", "expected_tool", "expected_snippet"),
    [
        (
            "Our consultant is staying physically in Kampala for 240 days this year, so we are treating her as a non-resident for PAYE. Is that right?",
            "determine_residency",
            "Your premise: the individual is a non-resident",
        ),
        (
            "As an individual landlord in Kampala, I collect UGX 40 million a year from residential flats and UGX 50 million a year from commercial shops (total UGX 90 million), and I spent UGX 25 million on roof repairs. How is my annual rental tax calculated, and can I deduct the UGX 25 million repair cost?",
            "calculate_rental_tax",
            "individual landlords CANNOT deduct any expenses",
        ),
        (
            "My hardware shop makes about UGX 240 million a year in taxable sales, so my accountant told me I am forced by law to register for VAT immediately in FY2026-27. Is he right?",
            "check_vat_registration",
            "Your premise: compulsory VAT registration is required",
        ),
        (
            "We are a VAT-registered distributor with annual turnover of UGX 600 million. Last month we purchased stock for UGX 40 million net of VAT from a supplier who gave us a manual handwritten paper invoice instead of an EFRIS e-invoice. Can we claim that input VAT?",
            "vat_input_claim_eligibility",
            "Your premise: claiming input VAT credit using a manual paper invoice",
        ),
        (
            "We are paying UGX 30,000,000 to a foreign consulting firm for a management advisory contract, and another UGX 10,000,000 salary to a full-time employee on an employment contract. Should we deduct 15% non-resident service WHT from both?",
            "wht_non_resident_service",
            "Your premise: 15% non-resident service withholding tax applies to an employment contract",
        ),
        (
            "I am a Ugandan citizen starting a new business with UGX 800 million in investment capital, and I made UGX 120 million in chargeable profit in year 1. Do I get the 3-year citizen start-up income tax exemption, or what corporation tax do I owe?",
            "calculate_presumptive_or_startup_tax",
            "calculate_corporation_tax",
        ),
        (
            "Our limited company sold a commercial warehouse for UGX 500 million that originally cost us UGX 320 million including improvements. How is the capital gain taxed?",
            "calculate_capital_gains",
            "54,000,000",
        ),
        (
            "I am importing a 10-year-old used station wagon with a CIF value of UGX 30,000,000. What customs duty, environmental levy, and VAT will apply?",
            "calculate_customs_duty",
            "61,950,000",
        ),
        (
            "If I withdraw UGX 4,000,000 cash from mobile money and also send UGX 4,000,000 as a wallet-to-wallet transfer, does the 0.5% excise duty apply to both transactions?",
            "calculate_excise_duty",
            "20,000",
        ),
        (
            "I am buying a plot of land valued at UGX 150,000,000 and also transferring a motor car into my name. How is stamp duty calculated on the land transfer and on the motor vehicle transfer in FY2026-27?",
            "calculate_stamp_duty",
            "1,500,000",
        ),
        (
            "URA just served us an administrative default assessment of UGX 50 million for withholding tax that we strongly dispute. How many days do we have to lodge a formal objection, and how does the 30% payment rule on disputed tax work?",
            "objection_requirements",
            "15,000,000",
        ),
        (
            "We filed our monthly return 1 month late, and the tax payable on that return was only UGX 3,000,000. Our clerk says the late filing penalty is just 2% of UGX 3,000,000. Is that right?",
            "penalty_late_filing",
            "200,000",
        ),
        (
            "A non-resident streaming and digital advertising platform with no physical office in Uganda earns UGX 400,000,000 in gross revenue from customers located in Uganda. What is the Digital Services Tax rate and how much tax is due?",
            "calculate_digital_services_tax",
            "20,000,000",
        ),
        (
            "Our licensed sports betting company collected UGX 500,000,000 in total stakes this month and paid out UGX 350,000,000 in winnings to players. What gaming and betting tax rate applies to the operator in FY2026-27?",
            "calculate_gaming_tax",
            "45,000,000",
        ),
        (
            "I own a 14-seater commercial taxi and a 10-tonne cargo lorry. How is the annual advance income tax calculated per seat for passenger vehicles and per tonne for goods vehicles in Uganda?",
            "calculate_advance_vehicle_tax",
            "280,000",
        ),
    ],
)
def test_one_case_per_tax_head_rule(
    question: str, expected_tool: str, expected_snippet: str
) -> None:
    """Every tax-head planner rule plans its tool and renders verified figures/contradictions."""
    facts = extract_case_facts(question)
    specs = plan_tools(question, facts)
    planned_names = [s.tool for s in specs]
    assert expected_tool in planned_names, f"{expected_tool} not in {planned_names}"
    block = build_verified_facts(question, facts)
    assert expected_snippet in block, f"{expected_snippet!r} not in:\n{block}"


def test_out_of_coverage_disclosure_when_numeric_question_has_no_planner_rule() -> None:
    """Numeric questions for uncovered tax topics emit the explicit out-of-coverage disclosure (§8.F.6)."""
    q = "Our mining exploration company paid UGX 75,000,000 in surface mineral royalties in Karamoja; how is the royalty shared?"
    facts = extract_case_facts(q)
    block = build_verified_facts(q, facts)
    assert "No verified calculator covers" in block
    assert "answer only from the passages" in block
    assert "recommend confirming with URA" in block


def test_no_duplicate_rental_pooling_logic_in_service_py() -> None:
    """Verify rental pooling logic was moved out of service._evaluate_calculation_context into scenario_planner.py."""
    src = inspect.getsource(service_module._evaluate_calculation_context)
    assert "pooled_gross" not in src
    assert "build_verified_facts" in src


def test_contradiction_rules_directly() -> None:
    """Direct unit test for check_fact_contradictions across residency, VAT threshold, rental expenses, and WHT employment."""
    f = extract_case_facts(
        "As an individual landlord I want to deduct UGX 10m repair expenses from my rent, "
        "and deduct 15% WHT from an employee on an employment contract."
    )
    contradictions = check_fact_contradictions(
        f,
        {
            "calculate_rental_tax": {"ok": True, "landlord_type": "individual"},
            "wht_non_resident_service": {"ok": True, "is_employment_contract": True},
        },
    )
    codes = {c.code for c in contradictions}
    assert "individual_rental_expense_deduction_disallowed" in codes
    assert "wht_service_excludes_employment_contract" in codes
