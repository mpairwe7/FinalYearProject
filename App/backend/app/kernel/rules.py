"""Rules as code for penalties and interest (ADR-0033, phase 2).

Every parameter is read from the kernel for the as-of date, and every step of
a computation names the provision it applies. A rule's arithmetic is the
statute's, as recorded in the rate tables' citations and notes:

* **Late-filing penal tax** (Tax Procedures Code Act (Cap 343), s.49(1)): the
  greater of the minimum (UGX 200,000) or 2% of the tax payable per month *or
  part of a month* the return remains unfiled.
* **Late-payment interest** (Tax Procedures Code Act (Cap 343), s.39; wording
  corroborated by the Income Tax Act s.136 in the Domestic Tax Laws of Uganda,
  July 2021): 2% per month on the amount unpaid, as **simple** interest
  (s.136(5)), with any interest above the principal tax plus penal tax waived
  (s.136(7)). The available text does not say how a part-month counts, so the
  number of months is an explicit input and no rounding convention is invented.
* **EFRIS penal tax** (Tax Procedures Code (Amendment) Act 2026, s.4,
  substituting s.93(1)-(2)): the greater of double the tax due or the minimum.
* **Unstamped-goods penal tax** (Tax Procedures Code (Amendment) Act 2026, s.2,
  amending s.21(3)): the greater of double the tax due or the minimum.

Every rule is ``pending_tax_team_review`` until URA's tax-policy reviewer
approves it in a kernel release, and its result is marked unverified until
then. Changing this file changes the ruleset hash, so it needs a release.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .functions import KernelFact, NotInKernel, fact

if TYPE_CHECKING:
    import datetime as _dt

RULESET_VERSION = "1.0.0"
REVIEW_PENDING = "pending_tax_team_review"
REVIEW_APPROVED = "approved"
#: Marked approved here, but the running kernel release names no reviewer yet.
REVIEW_AWAITING_RELEASE = "awaiting_approved_release"

#: Review status per rule. Marking a rule approved here is not enough: its
#: results are verified only when the running kernel release is itself
#: approved, which needs a named reviewer (``release record --reviewer``).
REVIEW_STATUS: dict[str, str] = {
    "late_filing_penal_tax": REVIEW_PENDING,
    "late_payment_interest": REVIEW_PENDING,
    "efris_penal_tax": REVIEW_PENDING,
    "dts_unstamped_penal_tax": REVIEW_PENDING,
}

_INTEREST_CAP_SOURCE = (
    "Income Tax Act s.136(5) and (7), Domestic Tax Laws of Uganda (July 2021), p.108: simple interest; interest "
    "above the principal tax and penal tax is waived. Pending confirmation against the revised Cap 343 text."
)


@dataclass(frozen=True)
class RuleStep:
    description: str
    value: str
    provision: str


@dataclass(frozen=True)
class RuleResult:
    rule: str
    amount: float
    currency: str
    steps: list[RuleStep]
    inputs: dict[str, Any]
    fiscal_year: str
    as_of: str
    kernel_version: str
    ruleset_version: str
    review_status: str
    unverified: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["ok"] = True
        return out


class RuleInputError(ValueError):
    """An input the rule cannot accept (negative amount, fractional months)."""


def _money(value: Any, name: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise RuleInputError(f"{name} must be a number") from exc
    if amount < 0 or not amount.is_finite():
        raise RuleInputError(f"{name} must be zero or more")
    return amount


def _months(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuleInputError("months must be a whole number of months, zero or more")
    return value


def _fmt(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.01"))
    return f"UGX {q:,.0f}" if q == q.to_integral_value() else f"UGX {q:,.2f}"


def _params(as_of: _dt.date, *keys: str) -> dict[str, KernelFact] | NotInKernel:
    found: dict[str, KernelFact] = {}
    for key in keys:
        item = fact(key, as_of)
        if isinstance(item, NotInKernel):
            return item
        found[key] = item
    return found


def review_status(rule: str) -> str:
    status = REVIEW_STATUS[rule]
    if status != REVIEW_APPROVED:
        return status
    from .release import REVIEW_APPROVED as RELEASE_APPROVED
    from .release import current

    return REVIEW_APPROVED if current().review_status == RELEASE_APPROVED else REVIEW_AWAITING_RELEASE


def _result(rule: str, amount: Decimal, steps: list[RuleStep], inputs: dict[str, Any], params: dict[str, KernelFact],
            as_of: _dt.date, notes: list[str] | None = None) -> RuleResult:
    first = next(iter(params.values()))
    review = review_status(rule)
    return RuleResult(
        rule=rule,
        amount=float(amount.quantize(Decimal("0.01"))),
        currency="UGX",
        steps=steps,
        inputs=inputs,
        fiscal_year=first.fiscal_year,
        as_of=as_of.isoformat(),
        kernel_version=first.kernel_version,
        ruleset_version=RULESET_VERSION,
        review_status=review,
        unverified=review != REVIEW_APPROVED or any(p.unverified for p in params.values()),
        notes=list(notes or []),
    )


def late_filing_months(due: _dt.date, filed: _dt.date) -> int:
    """Months *or part of a month* a return was late, as s.49(1) counts them."""
    if filed <= due:
        return 0
    months = (filed.year - due.year) * 12 + (filed.month - due.month)
    # A month is complete on the same day of the month; any further day starts a part-month.
    if filed.day > due.day:
        months += 1
    return max(months, 1)


def late_filing_penal_tax(tax_payable: Any, months_late: Any, *, as_of: _dt.date) -> RuleResult | NotInKernel:
    payable = _money(tax_payable, "tax_payable")
    months = _months(months_late)
    params = _params(as_of, "penal_tax_late_filing_minimum_ugx", "penal_tax_late_filing_monthly_rate")
    if isinstance(params, NotInKernel):
        return params
    minimum = Decimal(str(params["penal_tax_late_filing_minimum_ugx"].value))
    rate = Decimal(str(params["penal_tax_late_filing_monthly_rate"].value))
    prov = params["penal_tax_late_filing_monthly_rate"].provision
    inputs = {"tax_payable": float(payable), "months_late": months}
    if months == 0:
        return _result("late_filing_penal_tax", Decimal(0),
                       [RuleStep("The return was not late", _fmt(Decimal(0)), prov)], inputs, params, as_of)
    by_rate = payable * rate * months
    amount = max(minimum, by_rate)
    steps = [
        RuleStep(f"{rate * 100:g}% of the tax payable for each of {months} month(s) or part-month", _fmt(by_rate), prov),
        RuleStep("Minimum penal tax", _fmt(minimum), params["penal_tax_late_filing_minimum_ugx"].provision),
        RuleStep("The greater of the two applies", _fmt(amount), prov),
    ]
    return _result("late_filing_penal_tax", amount, steps, inputs, params, as_of)


def late_payment_interest(unpaid: Any, months: Any, *, penal_tax: Any = 0, as_of: _dt.date) -> RuleResult | NotInKernel:
    principal = _money(unpaid, "unpaid")
    count = _months(months)
    penal = _money(penal_tax, "penal_tax")
    params = _params(as_of, "late_payment_interest_monthly_rate")
    if isinstance(params, NotInKernel):
        return params
    rate = Decimal(str(params["late_payment_interest_monthly_rate"].value))
    prov = params["late_payment_interest_monthly_rate"].provision
    simple = principal * rate * count
    cap = principal + penal
    amount = min(simple, cap)
    steps = [
        RuleStep(f"Simple interest: {rate * 100:g}% of {_fmt(principal)} for {count} month(s)", _fmt(simple), prov),
        RuleStep("Interest above the principal tax and penal tax is waived", f"cap {_fmt(cap)}", _INTEREST_CAP_SOURCE),
        RuleStep("Interest payable", _fmt(amount), prov),
    ]
    notes = ["How a part-month counts is not stated in the available text; months is supplied by the caller."]
    return _result("late_payment_interest", amount, steps,
                   {"unpaid": float(principal), "months": count, "penal_tax": float(penal)}, params, as_of, notes)


def _double_or_minimum(rule: str, minimum_key: str, tax_due: Any, as_of: _dt.date, what: str) -> RuleResult | NotInKernel:
    due = _money(tax_due, "tax_due")
    params = _params(as_of, minimum_key)
    if isinstance(params, NotInKernel):
        return params
    minimum = Decimal(str(params[minimum_key].value))
    prov = params[minimum_key].provision
    doubled = due * 2
    amount = max(doubled, minimum)
    steps = [
        RuleStep(f"Double the tax due on the {what}", _fmt(doubled), prov),
        RuleStep("Minimum penal tax", _fmt(minimum), prov),
        RuleStep("The greater of the two applies", _fmt(amount), prov),
    ]
    return _result(rule, amount, steps, {"tax_due": float(due)}, params, as_of)


def efris_penal_tax(tax_due: Any, *, as_of: _dt.date) -> RuleResult | NotInKernel:
    return _double_or_minimum("efris_penal_tax", "efris_penal_tax_minimum_ugx", tax_due, as_of,
                              "goods or services not e-invoiced")


def dts_unstamped_penal_tax(tax_due: Any, *, as_of: _dt.date) -> RuleResult | NotInKernel:
    return _double_or_minimum("dts_unstamped_penal_tax", "dts_unstamped_goods_penal_tax_minimum_ugx", tax_due, as_of,
                              "unstamped goods")


RULES: dict[str, Any] = {
    "late_filing_penal_tax": late_filing_penal_tax,
    "late_payment_interest": late_payment_interest,
    "efris_penal_tax": efris_penal_tax,
    "dts_unstamped_penal_tax": dts_unstamped_penal_tax,
}
