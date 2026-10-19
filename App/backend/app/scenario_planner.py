"""Declarative scenario planner and verified-facts block builder (Phase F.6).

Maps conversation-scoped :class:`app.case_facts.CaseFacts` and the taxpayer's
question to deterministic calculator and statutory-rule tools, executes them
through the MCP client (`get_client().call_tool(...)`), checks for statutory
premise contradictions via :func:`app.premise_guard.check_fact_contradictions`,
and renders a single ``## Verified Statutory Facts & Calculations`` block
injected before retrieved passages in both REST and streaming paths.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .calculator_router import (
    _COMPANY_RE,
    _EXPENSE_KW_RE,
    _SALE_KW_RE,
    _COST_KW_RE,
    extract_amounts,
    format_calc_reply,
    has_money_amount,
    plan_calculation,
)
from .case_facts import CaseFacts, extract_case_facts
from .premise_guard import check_fact_contradictions

logger = logging.getLogger(__name__)

ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class ToolCallSpec:
    """Specification for a planned deterministic tool call or missing-input note."""

    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    label: str = ""


def _default_mcp_caller(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute *tool_name* through the in-process MCP client (`get_client().call_tool`)."""
    from .mcp import get_client

    call = get_client().call_tool(tool_name, dict(params), user_role="public")
    if call and isinstance(call.result, dict):
        return call.result
    return {"ok": False, "error": "Tool call returned no structured result"}


def _ugx_amounts_in_text(text: str) -> list[tuple[float, int, int]]:
    return extract_amounts(text or "")


def _amount_near_pattern(
    amounts: list[tuple[float, int, int]],
    text: str,
    pattern: re.Pattern[str],
    before: int = 38,
    after: int = 28,
) -> float | None:
    for val, s, e in amounts:
        ctx = text[max(0, s - before) : min(len(text), e + after)]
        if pattern.search(ctx):
            return val
    return None


# ---------------------------------------------------------------------------
# Declarative per-head planning rules: when(facts, question) -> list[ToolCallSpec]
# ---------------------------------------------------------------------------
def _rule_residency(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    has_residency_signal = (
        facts.days_in_uganda is not None
        or facts.contract_months is not None
        or facts.permanent_home_in_uganda is not None
        or facts.gov_employee_posted_abroad is not None
        or facts.avg_days_current_and_two_prior is not None
        or facts.residency_claim is not None
        or bool(
            re.search(
                r"\b(residen(?:t|cy|ts)|non[-\s]?resident\w*|183[-\s]?days?|122[-\s]?days?|expat(?:riate)?s?|foreign\s+(?:specialist|engineer|consultant|employee|national))\b",
                question,
                re.IGNORECASE,
            )
        )
    )
    if not has_residency_signal:
        return []

    params: dict[str, Any] = {}
    if facts.days_in_uganda is not None:
        params["days_in_uganda"] = facts.days_in_uganda
    if facts.contract_months is not None:
        params["contract_months"] = facts.contract_months
    if facts.permanent_home_in_uganda is not None:
        params["permanent_home"] = facts.permanent_home_in_uganda
    if facts.avg_days_current_and_two_prior is not None:
        params["avg_days_current_and_two_prior"] = facts.avg_days_current_and_two_prior
    if facts.gov_employee_posted_abroad is not None:
        params["gov_employee_posted_abroad"] = facts.gov_employee_posted_abroad
    if facts.period_start is not None:
        params["period_start"] = facts.period_start.isoformat()
    if facts.period_end is not None:
        params["period_end"] = facts.period_end.isoformat()

    missing: list[str] = []
    if (
        facts.days_in_uganda is None
        and facts.permanent_home_in_uganda is None
        and facts.gov_employee_posted_abroad is None
        and facts.avg_days_current_and_two_prior is None
    ):
        missing.append("days physically present in Uganda")

    return [ToolCallSpec("determine_residency", params=params, missing_inputs=missing)]


def _rule_paye_and_bik(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    specs: list[ToolCallSpec] = []
    q_low = question.lower()
    mentions_paye = (
        "paye" in facts.tax_heads
        or bool(
            re.search(
                r"\b(paye|pay\s+as\s+you\s+earn|monthly\s+tax|employment\s+income|salar(?:y|ies)|take[-\s]?home)\b",
                question,
                re.IGNORECASE,
            )
        )
    )
    mentions_bik = bool(facts.benefits) or bool(
        re.search(
            r"\b(benefits?\s+in\s+kind|\bbik\b|company\s+(?:car|vehicle|suv)|motor\s+vehicle\s+benefit|"
            r"furnished\s+(?:house|apartment)|rent[-\s]?free\s+house|employer[-\s]paid\s+housing|"
            r"free\s+company\s+accommodation|housing\s+(?:benefit|allowance)|low[-\s]?interest\s+loan|debt\s+waiver)\b",
            question,
            re.IGNORECASE,
        )
    )
    if not mentions_paye and not mentions_bik:
        return []

    # 1. Foreign currency conversion for salary if not UGX
    sal = facts.monthly_salary
    if sal is not None and sal.currency != "UGX":
        fx_params: dict[str, Any] = {
            "amount": float(sal.amount),
            "currency": sal.currency,
        }
        fx_missing: list[str] = []
        if facts.exchange_rate_ugx is not None:
            fx_params["exchange_rate"] = float(facts.exchange_rate_ugx)
        else:
            fx_missing.append(f"exchange rate ({sal.currency} to UGX) on payment date")
        specs.append(ToolCallSpec("convert_currency", params=fx_params, missing_inputs=fx_missing))

    # 2. Benefit-in-kind valuation specs
    seen_bik_kinds: set[str] = set()
    for bf in facts.benefits:
        seen_bik_kinds.add(bf.kind)
        if bf.kind == "car":
            if bf.market_value is not None and bf.market_value.currency == "UGX":
                yrs = bf.years_since_first_provided
                if yrs == 0 and re.search(r"\b(second\s+year|year\s+2|a\s+year\s+ago|1\s+year\s+ago)\b", q_low):
                    yrs = 1
                specs.append(
                    ToolCallSpec(
                        "value_benefit_in_kind",
                        params={
                            "kind": "car",
                            "market_value": float(bf.market_value.amount),
                            "years_since_first_provided": yrs,
                            "days_private_use": bf.days_private_use or 365,
                            "days_in_year": bf.days_in_year or 365,
                            "employee_payment": float(bf.employee_payment.amount) if bf.employee_payment else 0.0,
                        },
                        label="car",
                    )
                )
            else:
                specs.append(
                    ToolCallSpec(
                        "value_benefit_in_kind",
                        params={"kind": "car"},
                        missing_inputs=["market value of the car when first provided"],
                        label="car",
                    )
                )
        elif bf.kind == "housing":
            rent_mon = bf.monthly_cost or bf.market_value
            if (
                rent_mon is not None
                and rent_mon.currency == "UGX"
                and sal is not None
                and sal.currency == "UGX"
            ):
                specs.append(
                    ToolCallSpec(
                        "value_benefit_in_kind",
                        params={
                            "kind": "housing",
                            "market_rent": float(rent_mon.amount),
                            "monthly_gross": float(sal.amount),
                            "employee_payment": float(bf.employee_payment.amount) if bf.employee_payment else 0.0,
                        },
                        label="housing",
                    )
                )
            else:
                missing_h = ["market rent of the house"]
                if sal is None:
                    missing_h.append("gross monthly salary")
                specs.append(
                    ToolCallSpec(
                        "value_benefit_in_kind",
                        params={"kind": "housing"},
                        missing_inputs=missing_h,
                        label="housing",
                    )
                )
        elif bf.kind == "loan":
            if bf.loan_principal is not None and bf.loan_principal.currency == "UGX":
                specs.append(
                    ToolCallSpec(
                        "value_benefit_in_kind",
                        params={
                            "kind": "low_interest_loan",
                            "loan_principal": float(bf.loan_principal.amount),
                            "interest_rate_paid": float(bf.interest_rate_paid or 0),
                        },
                        label="low_interest_loan",
                    )
                )
            else:
                specs.append(
                    ToolCallSpec(
                        "value_benefit_in_kind",
                        params={"kind": "low_interest_loan"},
                        missing_inputs=["loan principal amount and interest rate paid"],
                        label="low_interest_loan",
                    )
                )
        elif bf.monthly_cost is not None and bf.monthly_cost.currency == "UGX":
            specs.append(
                ToolCallSpec(
                    "value_benefit_in_kind",
                    params={
                        "kind": bf.kind,
                        "monthly_cost": float(bf.monthly_cost.amount),
                        "employee_payment": float(bf.employee_payment.amount) if bf.employee_payment else 0.0,
                    },
                    label=bf.kind,
                )
            )

    # Check if car or housing was mentioned in question without being in facts.benefits
    if "car" not in seen_bik_kinds and re.search(
        r"\b(company\s+(?:car|vehicle|suv)|motor\s+vehicle\s+benefit)\b", question, re.IGNORECASE
    ):
        ugx_amts = _ugx_amounts_in_text(question)
        if len(ugx_amts) == 1 and re.search(r"\b(bought\s+at|market\s+value|worth)\b", q_low):
            yrs = 1 if re.search(r"\b(second\s+year|year\s+2|a\s+year\s+ago|1\s+year\s+ago)\b", q_low) else 0
            specs.append(
                ToolCallSpec(
                    "value_benefit_in_kind",
                    params={
                        "kind": "car",
                        "market_value": ugx_amts[0][0],
                        "years_since_first_provided": yrs,
                        "days_private_use": 365,
                        "days_in_year": 365,
                        "employee_payment": 0.0,
                    },
                    label="car",
                )
            )
        else:
            specs.append(
                ToolCallSpec(
                    "value_benefit_in_kind",
                    params={"kind": "car"},
                    missing_inputs=["market value of the car when first provided"],
                    label="car",
                )
            )
    if "housing" not in seen_bik_kinds and re.search(
        r"\b(furnished\s+(?:house|apartment)|rent[-\s]?free\s+house|employer[-\s]paid\s+housing|free\s+company\s+accommodation|housing\s+benefit)\b",
        question,
        re.IGNORECASE,
    ):
        specs.append(
            ToolCallSpec(
                "value_benefit_in_kind",
                params={"kind": "housing"},
                missing_inputs=["market rent of the house"],
                label="housing",
            )
        )

    # 3. PAYE calculation spec
    compare_res = bool(
        re.search(
            r"\b(residen(?:t|cy)|non[-\s]?resident|compared\s+with|differ\w*|vs\.?)\b",
            question,
            re.IGNORECASE,
        )
        or facts.residency_claim is not None
        or facts.days_in_uganda is not None
        or facts.contract_months is not None
    )
    res_mode = facts.residency_claim or "resident"
    if sal is not None and sal.currency == "UGX":
        specs.append(
            ToolCallSpec(
                "calculate_paye",
                params={
                    "monthly_gross": float(sal.amount),
                    "residency": res_mode,
                    "compare_residency": compare_res,
                },
            )
        )
    elif sal is not None and sal.currency != "UGX":
        specs.append(
            ToolCallSpec(
                "calculate_paye",
                params={
                    "residency": res_mode,
                    "compare_residency": True,
                },
                missing_inputs=(
                    [f"exchange rate ({sal.currency} to UGX) on payment date"]
                    if facts.exchange_rate_ugx is None
                    else []
                ),
            )
        )
    return specs


def _rule_rental(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(r"\b(rent|rental|landlord)\b", question, re.IGNORECASE) and "rental_tax" not in facts.tax_heads:
        return []
    amounts = _ugx_amounts_in_text(question)
    if not amounts:
        return []

    # Exclude statutory threshold (2,820,000) when mentioned next to "threshold"
    cleaned_spans = [
        (val, s, e)
        for val, s, e in amounts
        if not (
            abs(val - 2_820_000.0) < 1.0
            and re.search(
                r"\bthreshold\b",
                question[max(0, s - 25) : min(len(question), e + 25)],
                re.IGNORECASE,
            )
        )
    ]
    if not cleaned_spans:
        return []

    is_company = (
        facts.entity_type == "company"
        or bool(_COMPANY_RE.search(question))
    ) and not re.search(r"\bindividual\s+landlord\b", question, re.IGNORECASE)

    expense_val = _amount_near_pattern(cleaned_spans, question, _EXPENSE_KW_RE)
    rent_vals = [
        val
        for val, s, e in cleaned_spans
        if not _EXPENSE_KW_RE.search(question[max(0, s - 35) : min(len(question), e + 35)])
    ]
    if not rent_vals:
        rent_vals = [val for val, _, _ in cleaned_spans]

    max_v = max(rent_vals)
    others = [v for v in rent_vals if v != max_v]
    if others and abs(max_v - sum(others)) < 1.0:
        pooled_gross = max_v
    elif len(rent_vals) >= 2 and re.search(
        r"\b(residential|commercial|apartments?|flats?|shops?|units?|floors?)\b",
        question,
        re.IGNORECASE,
    ):
        pooled_gross = sum(rent_vals)
    else:
        pooled_gross = rent_vals[0]

    params: dict[str, Any] = {
        "annual_gross_rent": pooled_gross,
        "landlord_type": "company" if is_company else "individual",
    }
    if is_company and expense_val is not None:
        params["allowable_expenses"] = expense_val

    return [ToolCallSpec("calculate_rental_tax", params=params)]


def _rule_vat(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    specs: list[ToolCallSpec] = []
    if re.search(r"\b(customs\s+duty|cif\s+value|station\s+wagon|used\s+car|used\s+vehicle)\b", question, re.IGNORECASE):
        return []
    has_vat = "vat" in facts.tax_heads or bool(
        re.search(r"\b(vat|value\s+added\s+tax|efris|e[-\s]?invoice|imported\s+service|reverse[-\s]?charge)\b", question, re.IGNORECASE)
    )
    if not has_vat:
        return []

    amounts = _ugx_amounts_in_text(question)
    turnover_val = (
        float(facts.annual_turnover.amount)
        if facts.annual_turnover is not None and facts.annual_turnover.currency == "UGX"
        else (
            amounts[0][0]
            if len(amounts) == 1 and re.search(r"\b(register\w*|registration|threshold|compulsory|forced|sales|turnover)\b", question, re.IGNORECASE)
            else None
        )
    )
    if turnover_val is not None:
        if re.search(r"\b(register\w*|registration|threshold|compulsory|forced)\b", question, re.IGNORECASE):
            if re.search(r"\b(forced|must|required|compulsory|right)\b", question, re.IGNORECASE):
                facts.vat_registered = True
            specs.append(
                ToolCallSpec(
                    "check_vat_registration",
                    params={"annual_turnover": turnover_val},
                )
            )

    purchase_amt = _amount_near_pattern(
        amounts,
        question,
        re.compile(r"\b(purchas\w*|stock|bought|pays?|paid|invoice|subscription|saas)\b", re.IGNORECASE),
    )
    if purchase_amt is None and len(amounts) == 1 and (
        facts.annual_turnover is None or abs(amounts[0][0] - float(facts.annual_turnover.amount)) > 1.0
    ):
        purchase_amt = amounts[0][0]

    is_imported_srv = bool(
        re.search(
            r"\b(imported\s+service\w*|reverse[-\s]?charge|foreign\s+.*(?:software|saas|cloud|vendor|provider)|irish\s+cloud)\b",
            question,
            re.IGNORECASE,
        )
    )
    if facts.has_efris_einvoice is False or is_imported_srv or re.search(r"\b(apportion\w*|mixed\s+suppl\w*|exempt\s+.*inputs?)\b", question, re.IGNORECASE):
        specs.append(
            ToolCallSpec(
                "vat_input_claim_eligibility",
                params={
                    "amount_net_ugx": purchase_amt or 0.0,
                    "has_efris_einvoice": facts.has_efris_einvoice is not False,
                    "is_imported_service": is_imported_srv,
                },
            )
        )
    if purchase_amt is not None:
        specs.append(
            ToolCallSpec(
                "calculate_vat",
                params={"amount": purchase_amt, "direction": "add"},
            )
        )
    return specs


def _rule_withholding(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(
        r"\b(withholding|wht|consulting\s+firm|management\s+(?:advisory|fee\w*)|betting\s+winning\w*)\b",
        question,
        re.IGNORECASE,
    ):
        return []
    specs: list[ToolCallSpec] = []
    amounts = _ugx_amounts_in_text(question)
    if re.search(r"\b(management\s+(?:advisory|fee\w*)|consulting\s+firm|service\s+contract)\b", question, re.IGNORECASE) and amounts:
        srv_amt = _amount_near_pattern(
            amounts,
            question,
            re.compile(r"\b(consulting|management|advisory|service\s+contract|foreign)\b", re.IGNORECASE),
        ) or amounts[0][0]
        specs.append(
            ToolCallSpec(
                "calculate_withholding",
                params={"payment_type": "management_fees", "amount": srv_amt},
            )
        )
        specs.append(
            ToolCallSpec(
                "wht_non_resident_service",
                params={"amount_ugx": srv_amt, "is_employment_contract": False},
            )
        )
        if facts.employment_contract or re.search(r"\bemployment\s+contract\b", question, re.IGNORECASE):
            emp_amt = _amount_near_pattern(
                amounts,
                question,
                re.compile(r"\b(salar(?:y|ies)|employee|employment\s+contract)\b", re.IGNORECASE),
            ) or (amounts[1][0] if len(amounts) > 1 else srv_amt)
            specs.append(
                ToolCallSpec(
                    "wht_non_resident_service",
                    params={"amount_ugx": emp_amt, "is_employment_contract": True},
                    label="employment_contract_exclusion",
                )
            )
    return specs


def _rule_corporation_presumptive_startup(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(
        r"\b(presumptive|start[-\s]?up|investment\s+capital|corporation\s+tax|corporate\s+tax|chargeable\s+(?:profit|income))\b",
        question,
        re.IGNORECASE,
    ):
        return []
    specs: list[ToolCallSpec] = []
    amounts = _ugx_amounts_in_text(question)
    cap_amt = _amount_near_pattern(
        amounts, question, re.compile(r"\b(investment\s+capital|capital)\b", re.IGNORECASE)
    )
    profit_amt = _amount_near_pattern(
        amounts, question, re.compile(r"\b(chargeable\s+(?:profit|income)|profit)\b", re.IGNORECASE)
    )
    turnover_amt = (
        float(facts.annual_turnover.amount)
        if facts.annual_turnover and facts.annual_turnover.currency == "UGX"
        else _amount_near_pattern(amounts, question, re.compile(r"\b(turnover|sales)\b", re.IGNORECASE))
    )

    if turnover_amt is not None or cap_amt is not None or re.search(r"\b(presumptive|start[-\s]?up)\b", question, re.IGNORECASE):
        params: dict[str, Any] = {}
        if turnover_amt is not None:
            params["annual_turnover_ugx"] = turnover_amt
        if cap_amt is not None:
            params["investment_capital_ugx"] = cap_amt
        if profit_amt is not None:
            params["chargeable_income_ugx"] = profit_amt
        specs.append(ToolCallSpec("calculate_presumptive_or_startup_tax", params=params))

    if profit_amt is not None:
        specs.append(
            ToolCallSpec("calculate_corporation_tax", params={"chargeable_income": profit_amt})
        )
    return specs


def _rule_capital_gains(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(r"\b(capital\s+gains?|cgt|warehouse|commercial\s+building|cost\s+base)\b", question, re.IGNORECASE):
        return []
    amounts = _ugx_amounts_in_text(question)
    if len(amounts) < 2:
        return []
    sale = _amount_near_pattern(amounts, question, _SALE_KW_RE, before=38, after=12) or amounts[0][0]
    cost = _amount_near_pattern(amounts, question, _COST_KW_RE, before=38, after=12) or amounts[1][0]
    return [
        ToolCallSpec(
            "calculate_capital_gains",
            params={"sale_price": sale, "cost_base": cost},
        )
    ]


def _rule_customs(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(r"\b(customs|cif|environmental\s+levy|station\s+wagon|used\s+car|used\s+vehicle|used\s+cloth\w*)\b", question, re.IGNORECASE):
        return []
    amounts = _ugx_amounts_in_text(question)
    if not amounts:
        return []
    cif = amounts[0][0]
    cat = "general"
    if re.search(r"\b(?:9|1[0-5]|nine|ten|eleven|twelve|thirteen|fourteen|fifteen)[-\s]*years?[-\s]*old\b", question, re.IGNORECASE):
        cat = "used_vehicle_over_8_years"
    elif re.search(r"\b(?:[5-8]|five|six|seven|eight)[-\s]*years?[-\s]*old\b", question, re.IGNORECASE):
        cat = "used_vehicle_5_to_8_years"
    elif re.search(r"\b(used\s+cloth\w*|worn\s+cloth\w*)\b", question, re.IGNORECASE):
        cat = "used_clothing"
    elif re.search(r"\b(raw\s+materials?|capital\s+goods?)\b", question, re.IGNORECASE):
        cat = "raw_materials"
    return [
        ToolCallSpec(
            "calculate_customs_duty",
            params={"cif_value": cif, "goods_category": cat, "include_vat": True},
        )
    ]


def _rule_excise(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(r"\b(excise|mobile\s+money|withdraw\w*|petrol|diesel)\b", question, re.IGNORECASE):
        return []
    amounts = _ugx_amounts_in_text(question)
    if not amounts:
        return []
    if re.search(r"\b(mobile\s+money|withdraw\w*)\b", question, re.IGNORECASE):
        return [
            ToolCallSpec(
                "calculate_excise_duty",
                params={"excise_type": "mobile_money_withdrawal", "amount": amounts[0][0]},
            )
        ]
    return []


def _rule_stamp_duty(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(r"\b(stamp\s+duty)\b", question, re.IGNORECASE):
        return []
    specs: list[ToolCallSpec] = []
    amounts = _ugx_amounts_in_text(question)
    if re.search(r"\b(land|plot|property)\b", question, re.IGNORECASE) and amounts:
        specs.append(
            ToolCallSpec(
                "calculate_stamp_duty",
                params={"instrument_type": "property_transfer", "value_ugx": amounts[0][0]},
                label="property_transfer",
            )
        )
    if re.search(r"\b(motor\s+(?:car|vehicle)|car\s+transfer|transferring\s+a\s+motor)\b", question, re.IGNORECASE):
        specs.append(
            ToolCallSpec(
                "calculate_stamp_duty",
                params={"instrument_type": "motor_vehicle_transfer"},
                label="motor_vehicle_transfer",
            )
        )
    if re.search(r"\b(motorcycle|tricycle|quadricycle|boda)\b", question, re.IGNORECASE):
        specs.append(
            ToolCallSpec(
                "calculate_stamp_duty",
                params={"instrument_type": "motorcycle_transfer"},
                label="motorcycle_transfer",
            )
        )
    return specs


def _rule_objections(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(
        r"\b(objection|dispute\w*|tax\s+appeals\s+tribunal|\btat\b|default\s+assessment)\b",
        question,
        re.IGNORECASE,
    ):
        return []
    amounts = _ugx_amounts_in_text(question)
    disputed = amounts[0][0] if amounts else 0.0
    return [
        ToolCallSpec(
            "objection_requirements",
            params={"disputed_tax_ugx": disputed},
        )
    ]


def _rule_penalties(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(
        r"\b(late\s+filing|filed\s+[^?.!]{0,30}\s+late|penal\s+tax|late\s+payment\s+interest)\b",
        question,
        re.IGNORECASE,
    ):
        return []
    amounts = _ugx_amounts_in_text(question)
    if not amounts:
        return []
    tax_payable = _amount_near_pattern(
        amounts, question, re.compile(r"\b(tax\s+payable|return\s+was)\b", re.IGNORECASE)
    ) or amounts[0][0]
    m_months = re.search(r"\b(\d{1,2})\s+months?\s+late\b", question, re.IGNORECASE)
    months_late = int(m_months.group(1)) if m_months else 1
    return [
        ToolCallSpec(
            "penalty_late_filing",
            params={"tax_payable_ugx": tax_payable, "months_late": months_late},
        )
    ]


def _rule_digital_services(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(
        r"\b(digital\s+services\s+tax|\bdst\b|non[-\s]?resident\s+(?:streaming|digital))\b",
        question,
        re.IGNORECASE,
    ):
        return []
    amounts = _ugx_amounts_in_text(question)
    if not amounts:
        return []
    return [
        ToolCallSpec(
            "calculate_digital_services_tax",
            params={"gross_uganda_revenue_ugx": amounts[0][0]},
        )
    ]


def _rule_gaming(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(r"\b(gaming|sports\s+betting|total\s+stakes|casino)\b", question, re.IGNORECASE):
        return []
    amounts = _ugx_amounts_in_text(question)
    if not amounts:
        return []
    stakes = _amount_near_pattern(amounts, question, re.compile(r"\bstakes?\b", re.IGNORECASE)) or amounts[0][0]
    payouts = _amount_near_pattern(amounts, question, re.compile(r"\b(payouts?|winnings?)\b", re.IGNORECASE)) or (
        amounts[1][0] if len(amounts) > 1 else 0.0
    )
    return [
        ToolCallSpec(
            "calculate_gaming_tax",
            params={"total_stakes_ugx": stakes, "total_payouts_ugx": payouts},
        )
    ]


def _rule_advance_vehicle_tax(facts: CaseFacts, question: str) -> list[ToolCallSpec]:
    if not re.search(r"\b(advance\s+(?:income\s+)?tax|\d+[-\s]?seater|\d+[-\s]?tonne)\b", question, re.IGNORECASE):
        return []
    specs: list[ToolCallSpec] = []
    m_seats = re.search(r"\b(\d{1,3})[-\s]*(?:seater|passenger\s+seats?|seats?)\b", question, re.IGNORECASE)
    if m_seats:
        specs.append(
            ToolCallSpec(
                "calculate_advance_vehicle_tax",
                params={"vehicle_type": "passenger", "seats": int(m_seats.group(1))},
                label="passenger",
            )
        )
    m_tonnes = re.search(r"\b(\d+(?:\.\d+)?)[-\s]*tonnes?\b", question, re.IGNORECASE)
    if m_tonnes:
        specs.append(
            ToolCallSpec(
                "calculate_advance_vehicle_tax",
                params={"vehicle_type": "goods", "tonnes": float(m_tonnes.group(1))},
                label="goods",
            )
        )
    return specs


_PLANNER_RULES: tuple[Callable[[CaseFacts, str], list[ToolCallSpec]], ...] = (
    _rule_residency,
    _rule_paye_and_bik,
    _rule_rental,
    _rule_vat,
    _rule_withholding,
    _rule_corporation_presumptive_startup,
    _rule_capital_gains,
    _rule_customs,
    _rule_excise,
    _rule_stamp_duty,
    _rule_objections,
    _rule_penalties,
    _rule_digital_services,
    _rule_gaming,
    _rule_advance_vehicle_tax,
)


def plan_tools(question: str, facts: CaseFacts | None = None) -> list[ToolCallSpec]:
    """Map *question* and *facts* to an ordered list of :class:`ToolCallSpec` items."""
    eff_facts = facts if facts is not None else extract_case_facts(question or "")
    specs: list[ToolCallSpec] = []
    seen: set[tuple[str, str]] = set()

    for rule_fn in _PLANNER_RULES:
        for spec in rule_fn(eff_facts, question or ""):
            sig = (spec.tool, repr(sorted(spec.params.items())) + spec.label)
            if sig not in seen:
                seen.add(sig)
                specs.append(spec)

    if not specs:
        # Fallback to single-calculator plan if applicable
        calc_plan = plan_calculation(question or "")
        if calc_plan and not calc_plan.missing:
            specs.append(ToolCallSpec(calc_plan.tool, params=dict(calc_plan.params)))

    return specs


def build_verified_facts(
    question: str,
    facts: CaseFacts | None = None,
    *,
    tool_caller: ToolCaller | None = None,
) -> str:
    """Execute planned tools via MCP and render the ``## Verified Statutory Facts & Calculations`` block."""
    text = (question or "").strip()
    if not text:
        return ""

    eff_facts = facts if facts is not None else extract_case_facts(text)
    caller = tool_caller or _default_mcp_caller
    specs = plan_tools(text, eff_facts)

    if not specs:
        # Out-of-coverage disclosure when the question has numeric figures or an uncovered tax head
        if has_money_amount(text) or eff_facts.amounts:
            topic_label = ", ".join(eff_facts.tax_heads) if eff_facts.tax_heads else "this scenario"
            return (
                "\n\n## Verified Statutory Facts & Calculations (tool ground truth — use these figures verbatim)\n"
                f"- No verified calculator covers {topic_label}; answer only from the passages, "
                "state any figure's source, and recommend confirming with URA."
            )
        return ""

    tool_results: dict[str, Any] = {}
    bik_results: list[dict[str, Any]] = []
    bullet_lines: list[str] = []
    still_needed: list[str] = []

    def _add_needed(item: str) -> None:
        if item and item not in still_needed:
            still_needed.append(item)

    for spec in specs:
        for m_in in spec.missing_inputs:
            _add_needed(m_in)

        # Skip calling tools whose required inputs are already known to be missing
        if spec.tool == "value_benefit_in_kind" and spec.missing_inputs:
            kind = spec.params.get("kind", "benefit")
            if kind == "car":
                bullet_lines.append(
                    "- Car benefit: formula is (20% × A × B/C) − D where A is market value when first provided "
                    "(depreciated at 35% p.a. reducing balance for subsequent years), B is private-use days, "
                    "C is days in the year of income (365), and D is employee payment; ADDED to employment income "
                    "(basis: TAXATION-HANDBOOK-FY-2025-26-1, s.12.2.3)."
                )
            elif kind == "housing":
                bullet_lines.append(
                    "- Housing benefit: lesser of (a) market rent less employee payment and (b) 15% of employment "
                    "income (including paragraph (a)) for the year of income; ADDED to employment income "
                    "[interpretation unverified] (basis: TAXATION-HANDBOOK-FY-2025-26-1, s.12.2.3)."
                )
            continue

        if spec.tool == "calculate_paye":
            # Chain residency from determine_residency and FX salary / BIK values if available
            paye_params = dict(spec.params)
            res_out = tool_results.get("determine_residency")
            if isinstance(res_out, dict) and res_out.get("ok"):
                if res_out.get("status") == "resident" or res_out.get("likely_resident"):
                    paye_params["residency"] = "resident"
                elif res_out.get("status") == "non_resident" or res_out.get("likely_non_resident"):
                    paye_params["residency"] = "non_resident"
            fx_out = tool_results.get("convert_currency")
            if "monthly_gross" not in paye_params and isinstance(fx_out, dict) and fx_out.get("ok"):
                paye_params["monthly_gross"] = fx_out.get("ugx_amount")
            if bik_results:
                paye_params["benefits"] = [
                    {
                        "kind": b.get("kind", "benefit"),
                        "monthly_value": b.get("monthly_value", 0.0),
                        "verified": b.get("verified", True),
                    }
                    for b in bik_results
                    if b.get("ok")
                ]
            if "monthly_gross" not in paye_params or paye_params["monthly_gross"] is None:
                bullet_lines.append(
                    "- PAYE Bands Comparison (FY2026-27): Resident monthly bands have a 0% tax-free threshold "
                    "up to UGX 335,000 (then 20% to 410,000; 25% to 485,000; 30% to 10,000,000; 40% above). "
                    "Non-resident monthly bands have NO tax-free threshold and charge 10% on the first UGX 335,000 "
                    "(then 20% to 410,000; 30% to 10,000,000; 40% above) — above UGX 485,000/month a non-resident "
                    "pays exactly UGX 37,250/month more than a resident."
                )
                continue
            spec = ToolCallSpec(spec.tool, params=paye_params, missing_inputs=spec.missing_inputs)

        try:
            res = caller(spec.tool, spec.params)
        except Exception:
            logger.debug("scenario_planner tool call failed for %s", spec.tool, exc_info=True)
            continue

        if not isinstance(res, dict):
            continue

        if not res.get("ok"):
            need = res.get("needs")
            if need:
                _add_needed(str(need).replace("_", " "))
            continue

        tool_results[spec.tool] = res
        if spec.tool == "value_benefit_in_kind":
            bik_results.append(res)

        # Render verified bullet line per tool
        if spec.tool == "determine_residency":
            st = res.get("status", "undetermined")
            if st == "resident":
                bullet_lines.append(f"- Residency: RESIDENT — {res.get('explanation', '')}")
            elif st == "non_resident" or res.get("likely_non_resident"):
                bullet_lines.append(f"- Residency: NON-RESIDENT — {res.get('explanation', '')}")
            elif res.get("likely_resident"):
                bullet_lines.append(
                    f"- Residency: LIKELY RESIDENT — {res.get('explanation', '')} "
                    f"User premise \"{eff_facts.residency_claim or 'non-resident'}\" may be wrong."
                )
            else:
                bullet_lines.append(f"- Residency: UNDETERMINED — {res.get('explanation', '')}")
            for mf in res.get("missing_facts") or []:
                _add_needed(mf)

        elif spec.tool == "value_benefit_in_kind":
            kind = res.get("kind", "benefit")
            unv_tag = " [interpretation unverified]" if not res.get("verified", True) else ""
            bullet_lines.append(
                f"- {kind.capitalize()} benefit: UGX {res.get('monthly_value', 0):,.2f} / month "
                f"(UGX {res.get('annual_value', 0):,.2f} / year) — {res.get('explanation', '')}{unv_tag}"
            )

        elif spec.tool == "calculate_paye":
            fy = res.get("fiscal_year", "FY2026-27")
            r_mode = res.get("residency", "resident")
            line = (
                f"- PAYE verified ({r_mode}, {fy}): UGX {res.get('paye_verified', res.get('paye', 0)):,.2f} / month "
                f"(annual UGX {res.get('annual_paye', 0):,.2f}) on chargeable UGX {res.get('chargeable_monthly', res.get('monthly_gross', 0)):,.2f}"
            )
            comp = res.get("comparison")
            if isinstance(comp, dict):
                other_r = comp.get("other_residency", "non_resident")
                line += (
                    f"; {other_r} PAYE would be UGX {comp.get('other_paye', 0):,.2f} / month "
                    f"(difference UGX {comp.get('monthly_difference', 0):,.2f} / month)"
                )
            bullet_lines.append(line)
            if res.get("unverified_benefits"):
                bullet_lines.append(
                    f"- PAYE conditional on unverified {', '.join(res['unverified_benefits'])} rule: "
                    f"UGX {res.get('paye_conditional', 0):,.2f} / month (conditional on unverified rule)"
                )

        elif spec.tool == "calculate_rental_tax":
            breakdown = format_calc_reply("calculate_rental_tax", res, ["pooled gross rental income across all properties"])
            bullet_lines.append(f"- Rental Tax: {res.get('explanation', '')}\n{breakdown}")
            if res.get("landlord_type") == "individual":
                bullet_lines.append(
                    "- Rental Pooling & Expenses Rule: All individual rental income from residential and commercial "
                    "properties is pooled into one total annual gross rent; the UGX 2,820,000 threshold applies once, "
                    "and individual landlords CANNOT deduct repair, maintenance, or interest expenses."
                )

        else:
            expl = res.get("explanation") or format_calc_reply(spec.tool, res, [])
            bullet_lines.append(f"- {spec.tool}: {expl}")

    # Run premise/fact contradiction checks and prepend them so the model addresses them first
    contradictions = check_fact_contradictions(eff_facts, tool_results)
    contradiction_lines = [f"- {c.format_bullet()}" for c in contradictions]

    if still_needed:
        bullet_lines.append(f"- Still needed for an exact figure: {'; '.join(still_needed)}.")

    all_lines = contradiction_lines + bullet_lines
    if not all_lines:
        return ""

    return (
        "\n\n## Verified Statutory Facts & Calculations (tool ground truth — use these figures verbatim)\n"
        + "\n".join(all_lines)
        + "\nPresent these exact computed figures and statutory rules clearly."
    )
