"""Deterministic tax-calculator routing with parameter extraction.

Gives calculation questions an exact, instant answer when the message
already carries the figures ("VAT on 1.5m"), and starts the matching
guided calculator workflow when something is missing ("how much PAYE
will I pay?") so the assistant asks for exactly the absent details
instead of guessing. Pure regex + arithmetic — no LLM, no network —
so the path works identically on LLM-less deployments.

Production rules encoded here:
- never invent a figure: an ambiguous or absent amount becomes a
  question to the user, not a guess;
- every default applied (residency, VAT direction, landlord type,
  annual→monthly conversion) is surfaced as a visible assumption the
  user can correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Amount extraction
# ---------------------------------------------------------------------------
_CURRENCY = r"(?:ugx|ug\s?shs?|shs|shillings?)"
_AMOUNT_RE = re.compile(
    rf"""
    (?P<currency>{_CURRENCY}\.?\s*)?              # optional currency prefix
    (?P<number>\d{{1,3}}(?:[,\s]\d{{3}})+          # 1,000,000 / 1 000 000
       |\d+(?:\.\d+)?)                             # 1500000 / 1.5
    \s*
    (?P<suffix>k|m|bn|b|thousand|million|billion)?\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_MULTIPLIERS = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
}
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def extract_amounts(text: str) -> list[tuple[float, int, int]]:
    """All plausible UGX amounts in *text* as ``(value, start, end)``.

    Skips percentages, bare calendar years, and phone-shaped numbers.
    Bare numbers under 1,000 only count when marked by a currency
    prefix or a k/m/bn-style suffix — "2 houses" is not two shillings.
    """
    found: list[tuple[float, int, int]] = []
    percent_spans = [(m.start(1), m.end(1)) for m in _PERCENT_RE.finditer(text or "")]
    for m in _AMOUNT_RE.finditer(text or ""):
        raw = m.group("number")
        digits = raw.replace(",", "").replace(" ", "")
        if any(s <= m.start("number") < e or s < m.end("number") <= e for s, e in percent_spans):
            continue
        if _YEAR_RE.match(digits) and not (m.group("currency") or m.group("suffix")):
            continue
        if digits.startswith("0") and len(digits) >= 9:  # phone-shaped
            continue
        value = float(digits) * _MULTIPLIERS.get((m.group("suffix") or "").lower(), 1)
        if value < 1000 and not (m.group("currency") or m.group("suffix")):
            continue
        found.append((value, m.start(), m.end()))
    return found


def parse_ugx_amount(text: str) -> float | None:
    """Parse exactly ONE amount from a short answer ("1.5m", "1,200,000").

    Returns None when zero or several amounts are present — the caller
    should re-ask rather than pick one.
    """
    amounts = extract_amounts(text)
    return amounts[0][0] if len(amounts) == 1 else None


def _amount_near(
    amounts: list[tuple[float, int, int]], text: str, keywords: re.Pattern[str], window: int = 48
) -> float | None:
    """First amount preceded (within *window* chars) by one of *keywords*."""
    lowered = (text or "").lower()
    for value, start, _end in amounts:
        context = lowered[max(0, start - window) : start]
        if keywords.search(context):
            return value
    return None


# ---------------------------------------------------------------------------
# Intent + parameter planning
# ---------------------------------------------------------------------------
@dataclass
class CalcPlan:
    """A resolved calculation request.

    ``params`` holds every parameter already known (extracted or
    defaulted); ``missing`` lists, in ask-order, the workflow slots we
    still need from the user. Empty ``missing`` means compute now.
    """

    tool: str
    workflow_id: str
    params: dict[str, object] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


_CALC_VERB_RE = re.compile(
    r"\b(calculat\w*|comput\w*|work\s+out|how\s+much|estimate|what\s+will|figure\s+out)\b",
    re.IGNORECASE,
)
_INFO_ONLY_RE = re.compile(
    r"\bhow\s+(is|are|does)\b.*\b(calculated|computed|charged|determined)\b", re.IGNORECASE
)

_ANNUAL_RE = re.compile(r"\b(a\s+year|per\s+year|annual(?:ly)?|p\.?a\.?|yearly)\b", re.IGNORECASE)
_MONTHLY_RE = re.compile(r"\b(a\s+month|per\s+month|monthly|each\s+month)\b", re.IGNORECASE)
_NON_RESIDENT_RE = re.compile(r"\bnon[-\s]?resident\b", re.IGNORECASE)
_VAT_EXTRACT_RE = re.compile(
    r"\b(incl(?:usive|uded|uding)?(?:\s+of)?\s+vat|vat[-\s]inclusive"
    r"|vat\s+(?:is\s+)?includ\w*|includes\s+vat|extract|back\s+out"
    r"|remove\s+vat|before\s+vat|net\s+of\s+vat|out\s+of|from\s+the\s+gross)\b",
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(r"\b(company|business|ltd|limited|sacco)\b", re.IGNORECASE)
_SALE_KW_RE = re.compile(r"\b(sold|sale|sell(?:ing)?|dispos\w*|proceeds)\b", re.IGNORECASE)
_COST_KW_RE = re.compile(r"\b(bought|purchas\w*|cost|acquir\w*|paid|basis)\b", re.IGNORECASE)
_EXPENSE_KW_RE = re.compile(r"\b(expense\w*|repair\w*|maintenance|costs)\b", re.IGNORECASE)
_NO_VAT_RE = re.compile(r"\b(without|excluding|no|minus)\s+vat\b", re.IGNORECASE)
_DUTY_KW_RE = re.compile(r"\bduty\b", re.IGNORECASE)

_INTENT_RES: list[tuple[str, re.Pattern[str]]] = [
    ("withholding", re.compile(r"\b(withholding|wht)\b", re.IGNORECASE)),
    (
        "paye",
        re.compile(
            r"\b(paye|take[-\s]?home|net\s+(pay|salary)|salary\s+tax"
            r"|pay\s+as\s+you\s+earn|tax\s+on\s+(my\s+)?salary)\b",
            re.IGNORECASE,
        ),
    ),
    ("rental", re.compile(r"\b(rent(?:al)?\s+(income|tax)|tax\s+on\s+(my\s+)?rent)\b", re.IGNORECASE)),
    ("capital_gains", re.compile(r"\b(capital\s+gains?|cgt)\b", re.IGNORECASE)),
    (
        "corporation",
        re.compile(r"\b(corporation|corporate|company)\s+(income\s+)?tax\b", re.IGNORECASE),
    ),
    ("customs", re.compile(r"\b(customs|import\s+(duty|tax)|cif)\b", re.IGNORECASE)),
    ("vat", re.compile(r"\bv\.?a\.?t\.?\b", re.IGNORECASE)),
]

_WHT_TYPE_RES: list[tuple[str, re.Pattern[str]]] = [
    ("management_fees", re.compile(r"\bmanagement\s+fees?\b", re.IGNORECASE)),
    ("dividend", re.compile(r"\bdividends?\b", re.IGNORECASE)),
    ("services", re.compile(r"\b(services?|consultan\w+|professional|contract\w*)\b", re.IGNORECASE)),
    ("goods", re.compile(r"\b(goods|supplies|supply)\b", re.IGNORECASE)),
]


def plan_calculation(message: str) -> CalcPlan | None:  # noqa: PLR0911, PLR0912
    """Map a chat message to a calculator plan, or None when not a calc ask.

    Conservative on purpose: fires only on an explicit calculation verb
    ("calculate", "how much", ...) and never on informational questions
    like "how is PAYE calculated" or "what is the VAT rate".
    """
    text = (message or "").strip()
    if not text or not _CALC_VERB_RE.search(text) or _INFO_ONLY_RE.search(text):
        return None

    intent = next((name for name, pat in _INTENT_RES if pat.search(text)), None)
    if intent is None:
        return None

    amounts = extract_amounts(text)
    single = amounts[0][0] if len(amounts) == 1 else None

    if intent == "paye":
        params: dict[str, object] = {
            "residency": "non_resident" if _NON_RESIDENT_RE.search(text) else "resident"
        }
        assumptions = []
        if params["residency"] == "resident" and not re.search(r"resident", text, re.IGNORECASE):
            assumptions.append("resident employee (say “non-resident” if not)")
        missing = []
        if single is not None:
            if _ANNUAL_RE.search(text):
                params["monthly_gross"] = round(single / 12, 2)
                assumptions.append(
                    f"annual salary of UGX {single:,.0f} → UGX {single / 12:,.0f} per month"
                )
            else:
                params["monthly_gross"] = single
        else:
            missing.append("monthly_gross")
        return CalcPlan("calculate_paye", "calc_paye", params, missing, assumptions)

    if intent == "vat":
        direction = "extract" if _VAT_EXTRACT_RE.search(text) else "add"
        assumptions = []
        if direction == "add" and not re.search(r"\badd\w*\b", text, re.IGNORECASE):
            assumptions.append(
                "adding 18% VAT to a net amount (say “VAT-inclusive” to extract instead)"
            )
        params = {"direction": direction}
        missing = []
        if single is not None:
            params["amount"] = single
        else:
            missing.append("amount")
        return CalcPlan("calculate_vat", "calc_vat", params, missing, assumptions)

    if intent == "corporation":
        params, missing = {}, []
        if single is not None:
            params["chargeable_income"] = single
        else:
            missing.append("chargeable_income")
        return CalcPlan("calculate_corporation_tax", "calc_corporation_tax", params, missing, [])

    if intent == "capital_gains":
        params, missing = {}, []
        sale = _amount_near(amounts, text, _SALE_KW_RE)
        cost = _amount_near(amounts, text, _COST_KW_RE)
        if sale is not None and cost is not None and sale != cost:
            params["sale_price"], params["cost_base"] = sale, cost
        elif len(amounts) == 2 and (sale is not None or cost is not None):
            other = next(v for v, *_ in amounts if v not in (sale, cost))
            params["sale_price"] = sale if sale is not None else other
            params["cost_base"] = cost if cost is not None else other
        else:
            if sale is not None:
                params["sale_price"] = sale
            if cost is not None:
                params["cost_base"] = cost
        for slot in ("sale_price", "cost_base"):
            if slot not in params:
                missing.append(slot)
        return CalcPlan("calculate_capital_gains", "calc_capital_gains", params, missing, [])

    if intent == "customs":
        params = {"include_vat": not _NO_VAT_RE.search(text)}
        assumptions = []
        duty_pcts = [
            float(m.group(1))
            for m in _PERCENT_RE.finditer(text)
            if _DUTY_KW_RE.search(text[max(0, m.start() - 32) : m.end() + 16])
        ]
        if duty_pcts:
            params["duty_rate"] = duty_pcts[0] / 100
        else:
            assumptions.append("common external tariff duty of 25% (tell me the exact rate to refine)")
        missing = []
        if single is not None:
            params["cif_value"] = single
        else:
            missing.append("cif_value")
        return CalcPlan("calculate_customs_duty", "calc_customs_duty", params, missing, assumptions)

    if intent == "rental":
        params = {"landlord_type": "company" if _COMPANY_RE.search(text) else "individual"}
        assumptions = []
        if params["landlord_type"] == "individual" and not re.search(
            r"\bindividual\b", text, re.IGNORECASE
        ):
            assumptions.append("individual landlord (say “company” if it is a company)")
        missing = []
        rent = single
        if rent is None and amounts and params["landlord_type"] == "company":
            expense = _amount_near(amounts, text, _EXPENSE_KW_RE)
            others = [v for v, *_ in amounts if v != expense]
            if expense is not None and len(others) == 1:
                params["allowable_expenses"] = expense
                rent = others[0]
        if rent is not None:
            if _MONTHLY_RE.search(text):
                params["annual_gross_rent"] = rent * 12
                assumptions.append(
                    f"monthly rent of UGX {rent:,.0f} → UGX {rent * 12:,.0f} per year"
                )
            else:
                params["annual_gross_rent"] = rent
        else:
            missing.append("annual_gross_rent")
        return CalcPlan("calculate_rental_tax", "calc_rental_tax", params, missing, assumptions)

    if intent == "withholding":
        params, missing = {}, []
        wht_type = next((name for name, pat in _WHT_TYPE_RES if pat.search(text)), None)
        if wht_type is not None:
            params["payment_type"] = wht_type
        else:
            missing.append("payment_type")
        if single is not None:
            params["amount"] = single
        else:
            missing.append("amount")
        return CalcPlan("calculate_withholding", "calc_withholding", params, missing, [])

    return None


# ---------------------------------------------------------------------------
# Reply formatting
# ---------------------------------------------------------------------------
def _ugx(value: object) -> str:
    return f"UGX {float(value):,.0f}"


def format_calc_reply(tool: str, result: dict[str, object], assumptions: list[str]) -> str:
    """Render a calculator result as a friendly Markdown breakdown."""
    fy = result.get("fiscal_year", "FY2025-26")
    lines: list[str]
    if tool == "calculate_paye":
        band = result.get("band") or {}
        upper = band.get("upper")
        band_span = f"{_ugx(band.get('lower', 0))}–{_ugx(upper)}" if upper else f"above {_ugx(band.get('lower', 0))}"
        lines = [
            f"**PAYE calculation ({fy})**",
            "",
            f"- Gross monthly salary: {_ugx(result['monthly_gross'])}",
            f"- PAYE due: **{_ugx(result['paye'])}** per month",
            f"- Net take-home: **{_ugx(result['net_take_home'])}**",
            f"- Band applied: {float(band.get('marginal_rate', 0)) * 100:.0f}% marginal ({band_span})",
        ]
    elif tool == "calculate_vat":
        lines = [
            f"**VAT calculation ({fy})**",
            "",
            f"- Net amount: {_ugx(result['net'])}",
            f"- VAT at {float(result['rate']) * 100:.0f}%: **{_ugx(result['vat'])}**",
            f"- Gross (VAT-inclusive): **{_ugx(result['gross'])}**",
        ]
    elif tool == "calculate_corporation_tax":
        lines = [
            f"**Corporation tax calculation ({fy})**",
            "",
            f"- Chargeable income: {_ugx(result['chargeable_income'])}",
            f"- Tax at {float(result['rate']) * 100:.0f}%: **{_ugx(result['tax'])}**",
            f"- After-tax income: {_ugx(result['after_tax'])}",
        ]
    elif tool == "calculate_capital_gains":
        lines = [
            f"**Capital gains calculation ({fy})**",
            "",
            f"- Sale price: {_ugx(result['sale_price'])}",
            f"- Cost base: {_ugx(result['cost_base'])}",
            f"- Gain: {_ugx(result['gain'])}",
            f"- Tax at {float(result['rate']) * 100:.0f}%: **{_ugx(result['tax'])}**",
        ]
    elif tool == "calculate_customs_duty":
        lines = [
            f"**Customs duty estimate ({fy})**",
            "",
            f"- CIF value: {_ugx(result['cif_value'])}",
            f"- Duty at {float(result['duty_rate']) * 100:.0f}%: **{_ugx(result['duty'])}**",
        ]
        if result.get("vat_included"):
            lines.append(f"- VAT at 18% on (CIF + duty): {_ugx(result['vat'])}")
        lines.append(f"- Estimated landed cost: **{_ugx(result['landed_cost'])}**")
    elif tool == "calculate_rental_tax":
        if result.get("landlord_type") == "company":
            lines = [
                f"**Rental income tax — company ({fy})**",
                "",
                f"- Gross annual rent: {_ugx(result['annual_gross_rent'])}",
                f"- Deductible expenses (≤50% of gross): {_ugx(result['allowable_expenses'])}",
                f"- Chargeable income: {_ugx(result['chargeable_income'])}",
                f"- Tax at {float(result['rate']) * 100:.0f}%: **{_ugx(result['tax'])}**",
            ]
        else:
            lines = [
                f"**Rental income tax — individual ({fy})**",
                "",
                f"- Gross annual rent: {_ugx(result['annual_gross_rent'])}",
                f"- Tax-free threshold: {_ugx(result['threshold'])} per year",
                f"- Taxable amount: {_ugx(result['taxable_amount'])}",
                f"- Tax at {float(result['rate']) * 100:.0f}%: **{_ugx(result['tax'])}**",
            ]
    elif tool == "calculate_withholding":
        label = str(result.get("payment_type", "")).replace("_", " ")
        lines = [
            f"**Withholding tax — {label} ({fy})**",
            "",
            f"- Gross payment: {_ugx(result['amount'])}",
            f"- WHT at {float(result['rate']) * 100:.0f}%: **{_ugx(result['withholding_tax'])}**",
            f"- Net payable to payee: {_ugx(result['net_payable'])}",
        ]
    else:
        lines = [str(result.get("explanation", ""))]

    if assumptions:
        lines += ["", "_Assumptions: " + "; ".join(assumptions) + "._"]
    lines += ["", f"Figures use the official URA {fy} rate table."]
    return "\n".join(lines)


NEXT_ACTIONS_BY_TOOL: dict[str, list[str]] = {
    "calculate_paye": ["Calculate PAYE for a different salary", "Ask how PAYE bands work"],
    "calculate_vat": ["Calculate VAT on another amount", "Ask who must register for VAT"],
    "calculate_corporation_tax": ["Try a different chargeable income", "Ask about filing company returns"],
    "calculate_capital_gains": ["Try different sale figures", "Ask what counts as a capital gain"],
    "calculate_customs_duty": ["Estimate duty for another import", "Ask about EAC tariff classification"],
    "calculate_rental_tax": ["Calculate for a different rent", "Ask how rental tax is declared"],
    "calculate_withholding": ["Calculate WHT on another payment", "Ask when WHT applies"],
}
