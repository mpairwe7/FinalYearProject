"""Per-conversation structured case facts extraction and persistence (Phase F.1).

Extracts, merges, and persists structured taxpayer scenario facts across turns:
- Multi-currency monetary amounts (``UGX``, ``USD``, ``EUR``, ``GBP``, ``KES``)
  reusing :func:`app.calculator_router.extract_amounts` for UGX and adding
  foreign-currency + Luganda/Swahili number-word normalization beside it.
- Physical presence in Uganda (``days_in_uganda``, ``period_start``, ``period_end``)
  strictly separated from contract duration (``contract_months``).
- Residency claims, permanent home, government posting abroad, and 3-year average
  presence days.
- Employment salary (monthly/annual) and benefits in kind (car, housing,
  furniture, domestic_staff, utilities, meals, loan, debt_waiver, other).
- Entity type, annual turnover, active tax heads, and per-field turn provenance.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .calculator_router import _MULTIPLIERS, _redis_store, extract_amounts
from .context_manager import extract_conversation_entities
from .guardrails import redact_pii_text
from .number_words import _LG_AGREEING, _LG_TENS, _LG_UNITS, _SW_TENS, _SW_UNITS

logger = logging.getLogger(__name__)

ResidencyLiteral = Literal["resident", "non_resident"]
EntityLiteral = Literal["individual", "company", "partnership", "trust"]
BenefitKindLiteral = Literal[
    "car",
    "housing",
    "furniture",
    "domestic_staff",
    "utilities",
    "meals",
    "loan",
    "debt_waiver",
    "other",
]

_CASE_FACTS_TTL_S = 6 * 60 * 60
_CASE_FACTS_KEY_PREFIX = "case_facts:"
_CASE_FACTS_lock = threading.Lock()
_case_facts_memory: dict[str, tuple[float, dict[str, Any]]] = {}
_user_case_keys: dict[tuple[str, str], set[str]] = {}


@dataclass
class Money:
    """A monetary figure with ISO currency code and redacted source phrase."""

    amount: Decimal
    currency: str = "UGX"
    phrase: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            self.amount = Decimal(str(self.amount))
        self.currency = (self.currency or "UGX").strip().upper()
        if self.phrase:
            self.phrase = redact_pii_text(self.phrase.strip())[:160]

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": str(self.amount),
            "currency": self.currency,
            "phrase": self.phrase,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Money | None:
        if not raw or not isinstance(raw, dict) or raw.get("amount") is None:
            return None
        try:
            return cls(
                amount=Decimal(str(raw["amount"])),
                currency=str(raw.get("currency") or "UGX"),
                phrase=str(raw.get("phrase") or ""),
                label=str(raw.get("label") or ""),
            )
        except (InvalidOperation, ValueError, TypeError):
            return None


@dataclass
class BenefitFact:
    """Structured parameters for an employment benefit in kind."""

    kind: BenefitKindLiteral
    market_value: Money | None = None
    monthly_cost: Money | None = None
    annual_cost: Money | None = None
    employee_payment: Money | None = None
    days_private_use: int | None = None
    days_in_year: int | None = None
    years_since_first_provided: int = 0
    loan_principal: Money | None = None
    interest_rate_paid: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "market_value": self.market_value.to_dict() if self.market_value else None,
            "monthly_cost": self.monthly_cost.to_dict() if self.monthly_cost else None,
            "annual_cost": self.annual_cost.to_dict() if self.annual_cost else None,
            "employee_payment": self.employee_payment.to_dict() if self.employee_payment else None,
            "days_private_use": self.days_private_use,
            "days_in_year": self.days_in_year,
            "years_since_first_provided": self.years_since_first_provided,
            "loan_principal": self.loan_principal.to_dict() if self.loan_principal else None,
            "interest_rate_paid": (
                str(self.interest_rate_paid) if self.interest_rate_paid is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BenefitFact | None:
        if not raw or not isinstance(raw, dict) or not raw.get("kind"):
            return None
        ir = raw.get("interest_rate_paid")
        return cls(
            kind=raw["kind"],
            market_value=Money.from_dict(raw.get("market_value")),
            monthly_cost=Money.from_dict(raw.get("monthly_cost")),
            annual_cost=Money.from_dict(raw.get("annual_cost")),
            employee_payment=Money.from_dict(raw.get("employee_payment")),
            days_private_use=int(raw["days_private_use"]) if raw.get("days_private_use") is not None else None,
            days_in_year=int(raw["days_in_year"]) if raw.get("days_in_year") is not None else None,
            years_since_first_provided=int(raw.get("years_since_first_provided") or 0),
            loan_principal=Money.from_dict(raw.get("loan_principal")),
            interest_rate_paid=Decimal(str(ir)) if ir is not None else None,
        )


@dataclass
class CaseFacts:
    """Conversation-scoped structured facts extracted across turns."""

    residency_claim: ResidencyLiteral | None = None
    days_in_uganda: int | None = None
    contract_months: int | None = None
    period_start: _dt.date | None = None
    period_end: _dt.date | None = None
    permanent_home_in_uganda: bool | None = None
    gov_employee_posted_abroad: bool | None = None
    avg_days_current_and_two_prior: int | None = None
    monthly_salary: Money | None = None
    annual_salary: Money | None = None
    benefits: list[BenefitFact] = field(default_factory=list)
    entity_type: EntityLiteral | None = None
    annual_turnover: Money | None = None
    vat_registered: bool | None = None
    claims_vat_compulsory: bool | None = None
    has_efris_einvoice: bool | None = None
    service_contract_not_employment: bool | None = None
    employment_contract: bool | None = None
    claims_rental_expense_deduction: bool | None = None
    exchange_rate_ugx: Decimal | None = None
    tax_heads: list[str] = field(default_factory=list)
    amounts: list[Money] = field(default_factory=list)
    provenance: dict[str, int] = field(default_factory=dict)
    turn_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "residency_claim": self.residency_claim,
            "days_in_uganda": self.days_in_uganda,
            "contract_months": self.contract_months,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "permanent_home_in_uganda": self.permanent_home_in_uganda,
            "gov_employee_posted_abroad": self.gov_employee_posted_abroad,
            "avg_days_current_and_two_prior": self.avg_days_current_and_two_prior,
            "monthly_salary": self.monthly_salary.to_dict() if self.monthly_salary else None,
            "annual_salary": self.annual_salary.to_dict() if self.annual_salary else None,
            "benefits": [b.to_dict() for b in self.benefits],
            "entity_type": self.entity_type,
            "annual_turnover": self.annual_turnover.to_dict() if self.annual_turnover else None,
            "vat_registered": self.vat_registered,
            "claims_vat_compulsory": self.claims_vat_compulsory,
            "has_efris_einvoice": self.has_efris_einvoice,
            "service_contract_not_employment": self.service_contract_not_employment,
            "employment_contract": self.employment_contract,
            "claims_rental_expense_deduction": self.claims_rental_expense_deduction,
            "exchange_rate_ugx": (
                str(self.exchange_rate_ugx) if self.exchange_rate_ugx is not None else None
            ),
            "tax_heads": list(self.tax_heads),
            "amounts": [a.to_dict() for a in self.amounts],
            "provenance": dict(self.provenance),
            "turn_index": self.turn_index,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> CaseFacts:
        if not raw or not isinstance(raw, dict):
            return cls()
        p_start = raw.get("period_start")
        p_end = raw.get("period_end")
        fx = raw.get("exchange_rate_ugx")
        benefits_raw = raw.get("benefits") or []
        amounts_raw = raw.get("amounts") or []
        return cls(
            residency_claim=raw.get("residency_claim"),
            days_in_uganda=int(raw["days_in_uganda"]) if raw.get("days_in_uganda") is not None else None,
            contract_months=int(raw["contract_months"]) if raw.get("contract_months") is not None else None,
            period_start=_dt.date.fromisoformat(p_start) if isinstance(p_start, str) and p_start else None,
            period_end=_dt.date.fromisoformat(p_end) if isinstance(p_end, str) and p_end else None,
            permanent_home_in_uganda=raw.get("permanent_home_in_uganda"),
            gov_employee_posted_abroad=raw.get("gov_employee_posted_abroad"),
            avg_days_current_and_two_prior=(
                int(raw["avg_days_current_and_two_prior"])
                if raw.get("avg_days_current_and_two_prior") is not None
                else None
            ),
            monthly_salary=Money.from_dict(raw.get("monthly_salary")),
            annual_salary=Money.from_dict(raw.get("annual_salary")),
            benefits=[b for b in (BenefitFact.from_dict(x) for x in benefits_raw) if b is not None],
            entity_type=raw.get("entity_type"),
            annual_turnover=Money.from_dict(raw.get("annual_turnover")),
            vat_registered=raw.get("vat_registered"),
            claims_vat_compulsory=raw.get("claims_vat_compulsory"),
            has_efris_einvoice=raw.get("has_efris_einvoice"),
            service_contract_not_employment=raw.get("service_contract_not_employment"),
            employment_contract=raw.get("employment_contract"),
            claims_rental_expense_deduction=raw.get("claims_rental_expense_deduction"),
            exchange_rate_ugx=Decimal(str(fx)) if fx is not None else None,
            tax_heads=[str(k) for k in (raw.get("tax_heads") or [])],
            amounts=[m for m in (Money.from_dict(x) for x in amounts_raw) if m is not None],
            provenance={str(k): int(v) for k, v in (raw.get("provenance") or {}).items()},
            turn_index=int(raw.get("turn_index") or 0),
        )


# ---------------------------------------------------------------------------
# Luganda & Swahili number-word normalizer (bridges number_words.py -> extract_amounts)
# ---------------------------------------------------------------------------
def _build_vernacular_word_map() -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, w in enumerate(_LG_UNITS):
        if w:
            mapping[w] = idx
    for agree_list in _LG_AGREEING.values():
        for idx, w in enumerate(agree_list):
            if w:
                mapping[w] = idx
    for idx, w in enumerate(_LG_TENS):
        if w:
            mapping[w] = idx * 10
            parts = w.split()
            if len(parts) == 2 and parts[0] in ("amakumi", "makumi"):
                mapping[parts[1]] = idx * 10
    mapping["kikumi"] = 100
    for idx, w in enumerate(_SW_UNITS):
        if w:
            mapping[w] = idx
    for idx, w in enumerate(_SW_TENS):
        if w:
            mapping[w] = idx * 10
    return mapping


_VERNACULAR_WORD_TO_INT = _build_vernacular_word_map()
_VERNACULAR_SCALE_PREFIX_RE = re.compile(
    r"\b(?P<scale>milioni|bilioni|elfu|laki|o?bukadde|a?kakadde|o?bukaikuru|a?kakaikuru|"
    r"o?buwumbi|a?kawumbi|e?mitwalo|o?mutwalo|e?mitwaro|o?mutwaro|e?nkumi|o?lukumi|o?rukumi)"
    r"(?:\s+(?P<w1>[a-zA-Z]+)(?:\s+(?:(?:na|mu)\s+)?(?P<w2>[a-zA-Z]+))?)?\b",
    re.IGNORECASE,
)


def _normalize_vernacular_amount_words(text: str) -> str:
    """Convert Luganda/Swahili number words after scale nouns into digits for ``extract_amounts``."""
    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        scale = match.group("scale")
        w1 = (match.group("w1") or "").lower()
        w2 = (match.group("w2") or "").lower()
        trailing = text[match.end() :]
        if not w1:
            if (
                scale.lower() in {"kakadde", "akakadde", "mutwalo", "omutwalo", "lukumi", "olukumi", "laki"}
                and not re.match(r"^\s*\d", trailing)
            ):
                return f"{scale} 1"
            return match.group(0)
        phrase_two = f"{w1} {w2}".strip()
        if phrase_two in _VERNACULAR_WORD_TO_INT:
            return f"{scale} {_VERNACULAR_WORD_TO_INT[phrase_two]}"
        if w1 in _VERNACULAR_WORD_TO_INT:
            total = _VERNACULAR_WORD_TO_INT[w1]
            if w2 and w2 in _VERNACULAR_WORD_TO_INT:
                total += _VERNACULAR_WORD_TO_INT[w2]
                return f"{scale} {total}"
            # Keep w2 untouched if it was an ordinary word like "kwa" or "buli"
            if match.group("w2"):
                return f"{scale} {total} {match.group('w2')}"
            return f"{scale} {total}"
        return match.group(0)

    return _VERNACULAR_SCALE_PREFIX_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Foreign-currency amount extraction (beside calculator_router.extract_amounts)
# ---------------------------------------------------------------------------
_FX_PREFIX_MAP = {
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "eur": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "£": "GBP",
    "kes": "KES",
    "ksh": "KES",
    "kshs": "KES",
}
_FX_SUFFIX_MAP = {
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "us dollars": "USD",
    "us dollar": "USD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "pound sterling": "GBP",
    "pounds sterling": "GBP",
    "sterling": "GBP",
    "kes": "KES",
    "ksh": "KES",
    "kshs": "KES",
    "kenyan shillings": "KES",
    "kenyan shilling": "KES",
}

_FX_AMOUNT_RE = re.compile(
    r"""
    (?:
        (?P<fx_prefix>US\$|\$|€|£|\b(?:USD|EUR|GBP|KES|KShs?)\b)\s*
        (?P<num1>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
        (?:\s*(?P<suf1>k|m|bn|b|thousand|million|billion)\b)?
        (?:\s*(?:\b(?:us\s+dollars?|dollars?|euros?|pounds?\s+sterling|pounds?)\b))?
    )
    |
    (?:
        (?<!\w)
        (?P<num2>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
        (?:\s*(?P<suf2>k|m|bn|b|thousand|million|billion)\b)?
        \s*(?P<fx_suffix>\b(?:USD|EUR|GBP|KES|KShs?|us\s+dollars?|dollars?|euros?|pounds?\s+sterling|pounds?|kenyan\s+shillings?)\b)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_EXCHANGE_RATE_RE = re.compile(
    r"(?:\b(?:exchange\s+rate|fx\s+rate|rate\s+of\s+exchange|bou\s+(?:mid[-\s]?)?rate|mid[-\s]?rate|converted\s+at|at\s+a\s+rate\s+of)\b[^0-9]{0,25}?"
    r"(?:ugx|shs\.?)?\s*(?P<rate1>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{3,5}(?:\.\d+)?))"
    r"|(?:\b1\s*(?:usd|eur|gbp|\$|dollar|euro|pound)\s*(?:=|is|at|equals)\s*(?:ugx|shs\.?)?\s*(?P<rate2>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{3,5}(?:\.\d+)?))"
    r"|(?:(?:ugx|shs\.?)\s*(?P<rate3>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{3,5}(?:\.\d+)?)\s*(?:per|/)\s*(?:usd|eur|gbp|dollar|euro|pound))",
    re.IGNORECASE,
)


def extract_all_money(text: str) -> list[tuple[Money, int, int]]:
    """Extract all monetary figures (both foreign currency and UGX) with spans."""
    if not text:
        return []

    normalized = _normalize_vernacular_amount_words(text)
    results: list[tuple[Money, int, int]] = []
    occupied_spans: list[tuple[int, int]] = []

    # Exclude explicit exchange-rate quotes from being treated as standalone money amounts
    for fx_m in _EXCHANGE_RATE_RE.finditer(normalized):
        occupied_spans.append((fx_m.start(), fx_m.end()))

    # 1. Foreign currency amounts
    for m in _FX_AMOUNT_RE.finditer(normalized):
        s, e = m.start(), m.end()
        if any(os_ <= s and e <= oe for os_, oe in occupied_spans):
            continue
        if m.group("fx_prefix"):
            raw_curr = m.group("fx_prefix").strip().lower()
            currency = _FX_PREFIX_MAP.get(raw_curr, "USD")
            raw_num = m.group("num1")
            raw_suf = (m.group("suf1") or "").strip().lower()
        else:
            raw_curr = re.sub(r"\s+", " ", m.group("fx_suffix").strip().lower())
            currency = _FX_SUFFIX_MAP.get(raw_curr, "USD")
            raw_num = m.group("num2")
            raw_suf = (m.group("suf2") or "").strip().lower()

        mult = Decimal(str(int(_MULTIPLIERS.get(raw_suf, 1)))) if raw_suf in _MULTIPLIERS else Decimal(1)
        try:
            amt = (Decimal(raw_num.replace(",", "")) * mult).quantize(Decimal("0.01"))
            if amt == amt.to_integral():
                amt = amt.quantize(Decimal("1"))
        except InvalidOperation:
            continue
        phrase = normalized[max(0, s - 24) : min(len(normalized), e + 24)].strip()
        results.append((Money(amount=amt, currency=currency, phrase=phrase), s, e))
        occupied_spans.append((s, e))

    # 2. UGX amounts via calculator_router.extract_amounts
    for val, s, e in extract_amounts(normalized):
        if any(not (e <= os_ or s >= oe) for os_, oe in occupied_spans):
            continue
        amt = Decimal(str(round(val, 2)))
        if amt == amt.to_integral():
            amt = amt.quantize(Decimal("1"))
        phrase = normalized[max(0, s - 24) : min(len(normalized), e + 24)].strip()
        results.append((Money(amount=amt, currency="UGX", phrase=phrase), s, e))
        occupied_spans.append((s, e))

    results.sort(key=lambda item: item[1])
    return results


# ---------------------------------------------------------------------------
# Presence days, contract months, and date-span extraction
# ---------------------------------------------------------------------------
_MONTH_MAP = {name.lower(): idx for idx, name in enumerate(calendar.month_name) if idx}
_MONTH_MAP.update({name.lower(): idx for idx, name in enumerate(calendar.month_abbr) if idx})

_CONTRACT_MONTHS_RE = re.compile(
    r"\b(?:(?P<m1>\d{1,2})[-\s]*months?\s+(?:contract|assignment|secondment|consultancy|engagement|project|term|stint)"
    r"|(?:contract|assignment|secondment|engagement)\s+(?:of|for)\s+(?P<m2>\d{1,2})\s+months?)\b",
    re.IGNORECASE,
)

_PRESENCE_MONTHS_IN_UGANDA_RE = re.compile(
    r"\b(?:(?:physically\s+)?(?:present\s+)?in\s+uganda\s+(?:for\s+)?(?P<m1>\d{1,2})\s+months?"
    r"|(?:stay(?:ing|ed)?|liv(?:ing|ed)|work(?:ing|ed)|bas(?:ed)|residing|spent)\s+in\s+uganda\s+(?:for\s+)?(?P<m2>\d{1,2})\s+months?"
    r"|(?P<m3>\d{1,2})\s+months?\s+(?:physically\s+)?(?:present\s+)?in\s+uganda)\b",
    re.IGNORECASE,
)

_AVG_DAYS_RE = re.compile(
    r"\b(?:averag(?:e|ing)\s+(?:of\s+)?(?P<d1>\d{1,3})\s+days?"
    r"|(?P<d2>\d{1,3})\s+days?\s+on\s+average)\b",
    re.IGNORECASE,
)

_DAYS_IN_UGANDA_RE = re.compile(
    r"\b(?!(?:within|after|before|in)\s+\d{1,3}\s+days?\s+(?:of|from|to\s+object|to\s+file|to\s+pay|deadline|notice))"
    r"(?P<days>\d{1,3})\s+days?\b"
    r"(?:\s+(?:physically\s+)?(?:present\s+)?(?:in\s+uganda|here|in\s+the\s+country|during\s+the\s+year|per\s+year))?",
    re.IGNORECASE,
)

_PROCEDURAL_DAYS_CONTEXT_RE = re.compile(
    r"\b(objection|appeal|tribunal|tat|notice|assessment|deadline|within|file\s+a\s+return|invoice|efris|refund|private\s+use|available\s+for\s+private)\b",
    re.IGNORECASE,
)

_DATE_SPAN_MONTHS_RE = re.compile(
    r"\bfrom\s+(?:(?P<d1>\d{1,2})\s+)?(?P<m1>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(?P<y1>20\d{2}))?\s+(?:to|through|until|up\s+to|-)\s+"
    r"(?:(?P<d2>\d{1,2})\s+)?(?P<m2>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(?P<y2>20\d{2}))?\b",
    re.IGNORECASE,
)


def _extract_date_span(text: str) -> tuple[_dt.date | None, _dt.date | None, int | None]:
    """Parse 'from March to October 2026' into (period_start, period_end, days_if_in_uganda)."""
    m = _DATE_SPAN_MONTHS_RE.search(text or "")
    if not m:
        return None, None, None
    m1 = _MONTH_MAP.get(m.group("m1").lower()[:3]) or _MONTH_MAP.get(m.group("m1").lower())
    m2 = _MONTH_MAP.get(m.group("m2").lower()[:3]) or _MONTH_MAP.get(m.group("m2").lower())
    if not m1 or not m2:
        return None, None, None
    y2 = int(m.group("y2") or m.group("y1") or 2026)
    y1 = int(m.group("y1") or (y2 - 1 if m1 > m2 else y2))
    d1 = int(m.group("d1") or 1)
    last_day_m2 = calendar.monthrange(y2, m2)[1]
    d2 = int(m.group("d2") or last_day_m2)
    try:
        start = _dt.date(y1, m1, min(d1, calendar.monthrange(y1, m1)[1]))
        end = _dt.date(y2, m2, min(d2, last_day_m2))
    except ValueError:
        return None, None, None
    if end < start:
        return None, None, None
    # Only count as physical presence days if Uganda / presence is mentioned
    has_uganda_presence = bool(
        re.search(
            r"\b(in\s+uganda|physically\s+present|stay(?:ing|ed)?|work(?:ing|ed)\s+in\s+uganda|here\s+in\s+uganda|kampala|entebbe)\b",
            text,
            re.IGNORECASE,
        )
    )
    days = (end - start).days + 1 if has_uganda_presence else None
    return start, end, days


# ---------------------------------------------------------------------------
# Benefit-in-kind extraction
# ---------------------------------------------------------------------------
_BENEFIT_PATTERNS: list[tuple[BenefitKindLiteral, re.Pattern[str]]] = [
    ("car", re.compile(r"\b(company\s+car|motor\s+vehicle\s+benefit|employer[-\s]provided\s+(?:car|vehicle)|vehicle\s+benefit|free\s+car)\b", re.IGNORECASE)),
    ("housing", re.compile(r"\b(housing\s+(?:benefit|allowance)|accommodation|rent[-\s]?free\s+house|furnished\s+(?:house|apartment)|company\s+(?:house|apartment|flat)|employer[-\s]provided\s+housing|market\s+rent)\b", re.IGNORECASE)),
    ("furniture", re.compile(r"\b(furniture|furnishings)\b", re.IGNORECASE)),
    ("domestic_staff", re.compile(r"\b(domestic\s+(?:staff|servants?|workers?|assistants?)|housekeeper|gardener|security\s+guard|maid|driver\s+paid\s+by\s+employer)\b", re.IGNORECASE)),
    ("utilities", re.compile(r"\b(utilities|electricity\s+and\s+water|water\s+and\s+electricity|utility\s+bills?)\b", re.IGNORECASE)),
    ("meals", re.compile(r"\b(meals?\s+benefit|free\s+(?:meals?|lunch)|refreshments?\s+provided|canteen\s+meals?)\b", re.IGNORECASE)),
    ("loan", re.compile(r"\b(low[-\s]?interest\s+loan|interest[-\s]?free\s+loan|staff\s+loan|employer\s+loan|concessional\s+loan)\b", re.IGNORECASE)),
    ("debt_waiver", re.compile(r"\b(debt\s+waiver|waived\s+(?:loan|debt)|forgiven\s+(?:loan|debt))\b", re.IGNORECASE)),
]


_BENEFIT_NEGATION_PRE_RE = re.compile(
    r"\b(?:"
    r"no|not|without|neither|never|remove|removed|scratch|cancel|cancelled|dropped|stopped|exclude|excluding|instead\s+of"
    r"|no\s+longer(?:\s+(?:have|has|had|get|gets|getting|receive|receives|receiving|use|using|given))?"
    r"|(?:do|does|did)\s+not\s+(?:have|get|receive|use)"
    r"|(?:don['’]?t|doesn['’]?t|didn['’]?t)\s+(?:have|get|receive|use)"
    r")\s+"
    r"(?:any\s+|a\s+|an\s+|the\s+|my\s+|our\s+|his\s+|her\s+)?$",
    re.IGNORECASE,
)


def _extract_benefits(text: str, money_spans: list[tuple[Money, int, int]]) -> tuple[list[BenefitFact], set[BenefitKindLiteral]]:
    found: list[BenefitFact] = []
    retracted: set[BenefitKindLiteral] = set()
    lowered = (text or "").lower()
    for kind, pat in _BENEFIT_PATTERNS:
        m = pat.search(text or "")
        if not m:
            continue
        prefix_window = (text or "")[max(0, m.start() - 40) : m.start()]
        if _BENEFIT_NEGATION_PRE_RE.search(prefix_window):
            retracted.add(kind)
            continue
        bf = BenefitFact(kind=kind)
        window_start = max(0, m.start() - 60)
        window_end = min(len(text), m.end() + 140)
        window_text = lowered[window_start:window_end]
        nearby_money = [
            mon
            for mon, ms, me in sorted(
                money_spans,
                key=lambda item: min(abs(item[1] - m.start()), abs(item[2] - m.start())),
            )
            if window_start <= ms <= window_end
        ]
        if kind == "car":
            for mon in nearby_money:
                p_low = mon.phrase.lower()
                if any(k in p_low for k in ("contribut", "employee pays", "deducted from", "pays")):
                    bf.employee_payment = mon
                elif bf.market_value is None:
                    bf.market_value = mon
            m_priv = re.search(r"\b(\d{1,3})\s+days?\s+(?:for\s+)?private\s+use\b", window_text)
            if m_priv:
                bf.days_private_use = int(m_priv.group(1))
            m_yr = re.search(r"\b(\d)\s+years?\s+(?:old|since\s+first\s+provided|ago)\b", window_text)
            if m_yr:
                bf.years_since_first_provided = int(m_yr.group(1))
        elif kind == "housing":
            for mon in nearby_money:
                p_low = mon.phrase.lower()
                if any(k in p_low for k in ("employee pays", "contribut", "deducted")):
                    bf.employee_payment = mon
                elif bf.monthly_cost is None:
                    bf.monthly_cost = mon
        elif kind == "loan":
            if nearby_money:
                bf.loan_principal = nearby_money[0]
            m_rate = re.search(r"\b(\d+(?:\.\d+)?)\s*%\s*(?:interest|p\.?a\.?)?", window_text)
            if m_rate:
                bf.interest_rate_paid = Decimal(m_rate.group(1)) / Decimal(100)
        else:
            if nearby_money:
                bf.monthly_cost = nearby_money[0]
        found.append(bf)
    return found, retracted


# ---------------------------------------------------------------------------
# Main extraction & cross-turn merge
# ---------------------------------------------------------------------------
_SALARY_KW_RE = re.compile(
    r"\b(salar(?:y|ies)|earn(?:s|ing)?|paid|pay(?:ing)?|gross\s+pay|monthly\s+gross|remuneration|stipend|retainer|wage|compensation|omusaala|mshahara)\b",
    re.IGNORECASE,
)
_STRONG_SALARY_KW_RE = re.compile(
    r"\b(salar(?:y|ies)|earn(?:s|ing)?|gross\s+pay|monthly\s+gross|remuneration|stipend|retainer|wage|compensation|omusaala|mshahara)\b",
    re.IGNORECASE,
)
_TURNOVER_KW_RE = re.compile(
    r"\b(turnover|annual\s+sales|gross\s+sales|taxable\s+sales|in\s+sales|annual\s+revenue|gross\s+turnover|mauzo)\b",
    re.IGNORECASE,
)
_MONTHLY_CADENCE_RE = re.compile(
    r"\b(per\s+month|a\s+month|monthly|each\s+month|/month|/mo\b|kwa\s+mwezi|buli\s+mwezi)\b",
    re.IGNORECASE,
)
_ANNUAL_CADENCE_RE = re.compile(
    r"\b(per\s+year|a\s+year|annual(?:ly)?|p\.?a\.?|yearly|kwa\s+mwaka|buli\s+mwaka)\b",
    re.IGNORECASE,
)


def extract_case_facts(
    message: str,
    prior: CaseFacts | None = None,
    *,
    turn_index: int | None = None,
) -> CaseFacts:
    """Extract structured :class:`CaseFacts` from *message* and merge with *prior*.

    Later explicit statements override earlier ones, and ``provenance``
    records the turn index each populated field came from.
    """
    base = CaseFacts.from_dict(prior.to_dict()) if prior is not None else CaseFacts()
    eff_turn = (base.turn_index + 1) if turn_index is None else int(turn_index)
    base.turn_index = eff_turn
    text = (message or "").strip()
    if not text:
        return base

    def _set(field_name: str, value: Any) -> None:
        setattr(base, field_name, value)
        base.provenance[field_name] = eff_turn

    # 1. Residency claim (explicit user claim; non-resident checked before resident)
    if re.search(
        r"\b(non[-\s]?resident|not\s+a\s+(?:tax\s+)?resident|foreign\s+(?:national|consultant|employee|expat\w*)|expat(?:riate)?)\b",
        text,
        re.IGNORECASE,
    ) and not re.search(r"\bbecomes?\s+(?:a\s+)?resident\b", text, re.IGNORECASE):
        _set("residency_claim", "non_resident")
    if re.search(
        r"\b(?<!non-)(?<!non\s)(?:i\s+am\s+a\s+|he\s+is\s+a\s+|she\s+is\s+a\s+|they\s+are\s+|treat(?:ed)?\s+as\s+|is\s+a\s+|status\s+is\s+|as\s+a\s+)?(?:ugandan\s+)?(?:tax\s+)?resident\b",
        text,
        re.IGNORECASE,
    ) and not re.search(r"\bnon[-\s]?resident\b", text, re.IGNORECASE):
        _set("residency_claim", "resident")

    # 2. Contract months (NOT physical presence)
    m_contract = _CONTRACT_MONTHS_RE.search(text)
    if m_contract:
        months_val = int(m_contract.group("m1") or m_contract.group("m2"))
        _set("contract_months", months_val)

    # 3. Physical presence days or date span in Uganda
    p_start, p_end, span_days = _extract_date_span(text)
    if p_start is not None and p_end is not None:
        _set("period_start", p_start)
        _set("period_end", p_end)
        if span_days is not None:
            _set("days_in_uganda", span_days)

    m_pres_months = _PRESENCE_MONTHS_IN_UGANDA_RE.search(text)
    if m_pres_months:
        pres_m = int(
            m_pres_months.group("m1")
            or m_pres_months.group("m2")
            or m_pres_months.group("m3")
        )
        _set("days_in_uganda", pres_m * 30)

    m_avg = _AVG_DAYS_RE.search(text)
    if m_avg:
        avg_d = int(m_avg.group("d1") or m_avg.group("d2"))
        _set("avg_days_current_and_two_prior", avg_d)

    for m_days in _DAYS_IN_UGANDA_RE.finditer(text):
        span_ctx = text[max(0, m_days.start() - 35) : min(len(text), m_days.end() + 35)]
        if m_avg and m_avg.start() <= m_days.start() <= m_avg.end():
            continue
        if _PROCEDURAL_DAYS_CONTEXT_RE.search(span_ctx):
            continue
        d_val = int(m_days.group("days"))
        if 0 <= d_val <= 366:
            _set("days_in_uganda", d_val)
            break

    # 4. Permanent home & government posting abroad
    if re.search(r"\b(?:no|without\s+a|does\s+not\s+have\s+a)\s+permanent\s+home\b", text, re.IGNORECASE):
        _set("permanent_home_in_uganda", False)
    elif re.search(r"\bpermanent\s+home\s+in\s+uganda\b|\bhas\s+a\s+permanent\s+home\b", text, re.IGNORECASE):
        _set("permanent_home_in_uganda", True)

    if re.search(
        r"\b(government\s+employee\s+posted\s+abroad|ugandan\s+diplomat\s+posted\s+abroad|employee\s+of\s+the\s+government\s+of\s+uganda\s+posted\s+abroad)\b",
        text,
        re.IGNORECASE,
    ):
        _set("gov_employee_posted_abroad", True)

    # 5. Entity type
    if re.search(r"\b(partnership|partnerships|firm\s+of\s+partners)\b", text, re.IGNORECASE):
        _set("entity_type", "partnership")
    elif re.search(r"\b(trust|trustees|settlor)\b", text, re.IGNORECASE):
        _set("entity_type", "trust")
    elif re.search(
        r"\b(company|corporation|limited|ltd\b|inc\b|corporate\s+landlord|our\s+business|our\s+firm)\b",
        text,
        re.IGNORECASE,
    ) and not re.search(r"\bcompany\s+(?:car|house|housing|apartment)\b", text, re.IGNORECASE):
        _set("entity_type", "company")
    elif re.search(
        r"\b(individual|sole\s+(?:trader|proprietor)|individual\s+landlord|personal\s+name|employee|consultant)\b",
        text,
        re.IGNORECASE,
    ):
        _set("entity_type", "individual")

    # 6. Contract nature & VAT/rental flags for contradiction checks
    if re.search(r"\b(employment\s+contract|contract\s+of\s+employment|salaried\s+employee|full[-\s]?time\s+employee)\b", text, re.IGNORECASE):
        _set("employment_contract", True)
    if re.search(r"\b(independent\s+consultant|service\s+contract|contract\s+for\s+services|consultancy\s+fee)\b", text, re.IGNORECASE):
        _set("service_contract_not_employment", True)
    if re.search(r"\b(deduct|claim)\b[^.?!]{0,40}\b(expenses?|repairs?|maintenance|mortgage\s+interest)\b", text, re.IGNORECASE) and re.search(r"\b(rent|rental|landlord)\b", text, re.IGNORECASE):
        _set("claims_rental_expense_deduction", True)
    if re.search(
        r"\b(?:forced\s+(?:by\s+law\s+)?to\s+register\s+for\s+vat|compulsory\s+to\s+register\s+for\s+vat|"
        r"accountant\s+told\s+me\s+i\s+(?:am\s+forced|must|have)\s+to\s+register\s+for\s+vat)\b",
        text,
        re.IGNORECASE,
    ):
        _set("claims_vat_compulsory", True)
    if re.search(
        r"\b(?:no\s+efris|without\s+(?:an?\s+)?efris|non[-\s]?fiscalised|"
        r"manual\s+(?:handwritten\s+)?(?:paper\s+)?(?:invoice|receipt)|"
        r"paper\s+(?:invoice|receipt)|instead\s+of\s+(?:an?\s+)?efris)\b",
        text,
        re.IGNORECASE,
    ):
        _set("has_efris_einvoice", False)
    elif re.search(r"\befris\s+e[-\s]?(?:invoice|receipt)\b|\bfiscalised\s+invoice\b", text, re.IGNORECASE):
        _set("has_efris_einvoice", True)

    # 7. Exchange rate
    m_fx = _EXCHANGE_RATE_RE.search(text)
    if m_fx:
        raw_fx = m_fx.group("rate1") or m_fx.group("rate2") or m_fx.group("rate3")
        if raw_fx:
            with contextlib.suppress(InvalidOperation):
                _set("exchange_rate_ugx", Decimal(raw_fx.replace(",", "")))

    # 8. Monetary amounts, salary, turnover, and benefits
    money_spans = extract_all_money(text)
    if money_spans:
        new_amounts: list[Money] = []
        saw_salary_in_turn = False
        for mon, ms, me in money_spans:
            ctx_window = text[max(0, ms - 50) : min(len(text), me + 50)]
            pre_clause = re.split(r"(?:[,;]|\b(?:plus|and|with)\b)", text[max(0, ms - 35) : ms], flags=re.I)[-1]
            post_clause = re.split(r"(?:[,;]|\b(?:plus|and|with)\b)", text[me : min(len(text), me + 35)], flags=re.I)[0]
            same_clause_window = f"{pre_clause} {text[ms:me]} {post_clause}"
            is_benefit_span = any(pat.search(same_clause_window) for _, pat in _BENEFIT_PATTERNS)
            is_rental_span = bool(
                re.search(
                    r"\b(rentals?|rental\s+(?:income|units?|apartments?|properties|tax)|tenants?|landlords?)\b",
                    text,
                    re.IGNORECASE,
                )
                and not re.search(r"\b(paye|employ\w+|hiring|expat\w*|salar\w+|wages?)\b", text, re.IGNORECASE)
            )
            has_explicit_salary_kw = bool(_STRONG_SALARY_KW_RE.search(same_clause_window))
            if _TURNOVER_KW_RE.search(ctx_window):
                mon.label = "annual_turnover"
                _set("annual_turnover", mon)
            elif (
                (has_explicit_salary_kw or (not saw_salary_in_turn and not is_rental_span and (_SALARY_KW_RE.search(ctx_window) or _MONTHLY_CADENCE_RE.search(ctx_window))))
                and (not is_benefit_span or has_explicit_salary_kw)
            ):
                saw_salary_in_turn = True
                if _ANNUAL_CADENCE_RE.search(ctx_window) and not _MONTHLY_CADENCE_RE.search(ctx_window):
                    mon.label = "annual_salary"
                    _set("annual_salary", mon)
                    _set(
                        "monthly_salary",
                        Money(
                            amount=(mon.amount / Decimal(12)).quantize(Decimal("0.01")),
                            currency=mon.currency,
                            phrase=mon.phrase,
                            label="monthly_salary",
                        ),
                    )
                else:
                    mon.label = "monthly_salary"
                    _set("monthly_salary", mon)
                    _set(
                        "annual_salary",
                        Money(
                            amount=(mon.amount * Decimal(12)).quantize(Decimal("0.01")),
                            currency=mon.currency,
                            phrase=mon.phrase,
                            label="annual_salary",
                        ),
                    )
            new_amounts.append(mon)
        base.amounts.extend(new_amounts)
        if len(base.amounts) > 20:
            base.amounts = base.amounts[-20:]
        base.provenance["amounts"] = eff_turn

    # 9. Benefits in kind (with multi-turn retraction support)
    extracted_benefits, retracted_kinds = _extract_benefits(text, money_spans)
    if extracted_benefits or retracted_kinds:
        by_kind = {b.kind: b for b in base.benefits if b.kind not in retracted_kinds}
        for bf in extracted_benefits:
            existing = by_kind.get(bf.kind)
            if existing is None:
                by_kind[bf.kind] = bf
            else:
                if bf.market_value is not None:
                    existing.market_value = bf.market_value
                if bf.monthly_cost is not None:
                    existing.monthly_cost = bf.monthly_cost
                if bf.annual_cost is not None:
                    existing.annual_cost = bf.annual_cost
                if bf.employee_payment is not None:
                    existing.employee_payment = bf.employee_payment
                if bf.days_private_use is not None:
                    existing.days_private_use = bf.days_private_use
                if bf.loan_principal is not None:
                    existing.loan_principal = bf.loan_principal
                if bf.interest_rate_paid is not None:
                    existing.interest_rate_paid = bf.interest_rate_paid
        base.benefits = list(by_kind.values())
        base.provenance["benefits"] = eff_turn

    # 10. Tax heads via context_manager.extract_conversation_entities
    entities = extract_conversation_entities([{"user_message": text, "bot_reply": ""}])
    for key in entities.tax_topic_keys:
        if key and key != "general_tax" and key not in base.tax_heads:
            base.tax_heads.append(key)
            base.provenance["tax_heads"] = eff_turn

    return base


# ---------------------------------------------------------------------------
# Conversation-scoped persistence (Redis + in-process fallback) & /v1/me support
# ---------------------------------------------------------------------------
import contextlib


def _scoped_case_key(
    conversation_id: str,
    *,
    user_id: str = "",
    tenant_id: str = "default",
) -> str:
    if not conversation_id:
        return ""
    try:
        from .database import conversation_state_key

        scoped = conversation_state_key(
            conversation_id,
            user_id=user_id or None,
            tenant_id=tenant_id or "default",
        )
    except Exception:
        scoped = f"{tenant_id or 'default'}:{user_id or 'anon'}:{conversation_id}"
    return f"{_CASE_FACTS_KEY_PREFIX}{scoped}"


def _user_index_key(tenant_id: str, user_id: str) -> str:
    return f"case_facts_user_index:{tenant_id or 'default'}:{user_id}"


def remember_case_facts(
    conversation_id: str,
    facts: CaseFacts,
    *,
    user_id: str = "",
    tenant_id: str = "default",
) -> None:
    """Persist *facts* for *conversation_id* in Redis (with TTL + user index) and in-process fallback."""
    key = _scoped_case_key(conversation_id, user_id=user_id, tenant_id=tenant_id)
    if not key:
        return
    payload = facts.to_dict()
    with _CASE_FACTS_lock:
        _case_facts_memory[key] = (time.monotonic() + _CASE_FACTS_TTL_S, payload)
        if user_id:
            _user_case_keys.setdefault((tenant_id or "default", user_id), set()).add(key)

    store = _redis_store()
    if store is None:
        return
    try:
        store.setex(key, _CASE_FACTS_TTL_S, json.dumps(payload))
        if user_id:
            idx_key = _user_index_key(tenant_id, user_id)
            store.sadd(idx_key, key)
            store.expire(idx_key, _CASE_FACTS_TTL_S)
    except Exception:
        logger.debug("case_facts redis set failed", exc_info=True)


def recall_case_facts(
    conversation_id: str,
    *,
    user_id: str = "",
    tenant_id: str = "default",
) -> CaseFacts | None:
    """Recall persisted :class:`CaseFacts` for *conversation_id*, or ``None`` if absent/expired."""
    key = _scoped_case_key(conversation_id, user_id=user_id, tenant_id=tenant_id)
    if not key:
        return None
    store = _redis_store()
    if store is not None:
        try:
            raw = store.get(key)
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return CaseFacts.from_dict(data)
        except Exception:
            logger.debug("case_facts redis get failed", exc_info=True)

    with _CASE_FACTS_lock:
        cached = _case_facts_memory.get(key)
        if cached is None:
            return None
        expires, payload = cached
        if time.monotonic() >= expires:
            _case_facts_memory.pop(key, None)
            return None
        return CaseFacts.from_dict(payload)


def clear_case_facts(
    conversation_id: str,
    *,
    user_id: str = "",
    tenant_id: str = "default",
) -> None:
    """Delete persisted :class:`CaseFacts` for *conversation_id*."""
    if not conversation_id:
        return
    keys_to_clear = {
        _scoped_case_key(conversation_id, user_id=user_id, tenant_id=tenant_id),
        f"{_CASE_FACTS_KEY_PREFIX}{conversation_id}",
    }
    with _CASE_FACTS_lock:
        for k in list(_case_facts_memory):
            if k in keys_to_clear or k.endswith(f":{conversation_id}"):
                _case_facts_memory.pop(k, None)
    store = _redis_store()
    if store is not None:
        for k in keys_to_clear:
            if k:
                with contextlib.suppress(Exception):
                    store.delete(k)


def export_user_case_facts(
    user_id: str,
    *,
    tenant_id: str = "default",
) -> list[dict[str, Any]]:
    """Return all active conversation CaseFacts for *user_id* (for ``GET /v1/me/export``)."""
    if not user_id:
        return []
    now = time.monotonic()
    by_key: dict[str, dict[str, Any]] = {}
    with _CASE_FACTS_lock:
        keys = set(_user_case_keys.get((tenant_id or "default", user_id), set()))
        for k, (exp, payload) in list(_case_facts_memory.items()):
            if exp > now and (k in keys or f":{user_id}:" in k):
                by_key[k] = {"key": k, "facts": dict(payload)}

    store = _redis_store()
    if store is not None:
        try:
            idx_key = _user_index_key(tenant_id, user_id)
            raw_members = store.smembers(idx_key) or set()
            redis_keys = {
                m.decode("utf-8") if isinstance(m, bytes) else str(m)
                for m in raw_members
                if m
            }
            for k in sorted(redis_keys):
                if k in by_key:
                    continue
                raw_val = store.get(k)
                if raw_val:
                    parsed = json.loads(raw_val)
                    if isinstance(parsed, dict):
                        by_key[k] = {"key": k, "facts": parsed}
        except Exception:
            logger.debug("case_facts redis export failed", exc_info=True)

    return list(by_key.values())


def forget_user_case_facts(
    user_id: str,
    *,
    tenant_id: str = "default",
) -> int:
    """Erase all conversation CaseFacts for *user_id* (for ``DELETE /v1/me``)."""
    if not user_id:
        return 0
    removed = 0
    keys_to_delete: set[str] = set()
    with _CASE_FACTS_lock:
        tracked = _user_case_keys.pop((tenant_id or "default", user_id), set())
        keys_to_delete.update(tracked)
        for k in list(_case_facts_memory):
            if k in keys_to_delete or f":{user_id}:" in k:
                _case_facts_memory.pop(k, None)
                keys_to_delete.add(k)
                removed += 1
    store = _redis_store()
    if store is not None:
        try:
            idx_key = _user_index_key(tenant_id, user_id)
            raw_members = store.smembers(idx_key) or set()
            for m in raw_members:
                rk = m.decode("utf-8") if isinstance(m, bytes) else str(m)
                if rk:
                    keys_to_delete.add(rk)
            store.delete(idx_key)
        except Exception:
            logger.debug("case_facts redis index lookup failed", exc_info=True)
        for k in keys_to_delete:
            with contextlib.suppress(Exception):
                if store.delete(k) and removed == 0:
                    removed += 1
    return removed
