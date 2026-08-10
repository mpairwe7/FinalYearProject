"""Decimal money helpers and progressive-band arithmetic.

Tax figures are quoted to the shilling and are checked by hand against
payslips, so the arithmetic uses :class:`~decimal.Decimal` end to end.
Binary floats make ``0.1 + 0.2`` visible in a rounded total often enough
to cost trust in an assistant whose whole value is being exactly right.

Bands are integrated from the table rather than read off pre-computed
"tax at the bottom of this band" constants.  Those constants have to be
recomputed by hand every time a band boundary moves, and a wrong one is
invisible until someone in that band checks their payslip — this
codebase already shipped one that was off by UGX 500.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tables import Band

#: Shilling-and-cents precision for every published figure.
CENTS = Decimal("0.01")


class AmountError(ValueError):
    """Raised for an amount that cannot be used in a calculation."""


def to_decimal(value: Any, *, field: str, allow_negative: bool = False) -> Decimal:
    """Coerce *value* to a finite :class:`Decimal`, or raise :class:`AmountError`.

    Floats round-trip through :func:`repr` so a JSON ``1.15`` becomes
    ``Decimal("1.15")`` rather than the binary expansion underneath it.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a money amount
        raise AmountError(f"{field} must be a number")
    try:
        amount = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise AmountError(f"{field} must be a number (got {value!r})") from exc
    if not amount.is_finite():
        raise AmountError(f"{field} must be a finite number")
    if amount < 0 and not allow_negative:
        raise AmountError(f"{field} must be non-negative")
    return amount


def to_rate(value: Any, *, field: str) -> Decimal:
    """Coerce *value* to a rate in ``[0, 1]``.

    Percent-shaped input (``18`` for 18%) is rejected rather than
    silently divided by 100: a caller that means 1,800% and a caller
    that means 18% are indistinguishable, and guessing wrong produces a
    confident, wrong tax bill.
    """
    rate = to_decimal(value, field=field)
    if rate > 1:
        raise AmountError(f"{field} must be a decimal fraction between 0 and 1 (e.g. 0.18 for 18%)")
    return rate


def quantize(amount: Decimal) -> Decimal:
    """Round to the cent, half-up — the convention on URA assessments."""
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def to_float(amount: Decimal) -> float:
    """Quantized amount as a JSON-safe float for tool output."""
    return float(quantize(amount))


def apply_bands(amount: Decimal, bands: list[Band]) -> tuple[Decimal, list[dict[str, Any]]]:
    """Integrate *amount* through progressive *bands*.

    Returns the total tax and a per-band breakdown, so a reply can show
    the working rather than assert a total.  Bands are validated as
    contiguous at load time (see :func:`app.tax.tables._parse_bands`),
    so the walk here cannot skip a slice of income.
    """
    total = Decimal(0)
    breakdown: list[dict[str, Any]] = []
    for lower, upper, rate in bands:
        low = Decimal(str(lower))
        if amount <= low:
            break
        high = amount if upper is None else min(amount, Decimal(str(upper)))
        taxable = high - low
        if taxable <= 0:
            continue
        band_rate = Decimal(str(rate))
        band_tax = taxable * band_rate
        total += band_tax
        breakdown.append(
            {
                "lower": float(low),
                "upper": None if upper is None else float(upper),
                "rate": float(band_rate),
                "taxable_in_band": to_float(taxable),
                "tax_in_band": to_float(band_tax),
            }
        )
    return total, breakdown


def marginal_band(amount: Decimal, bands: list[Band]) -> Band:
    """The band *amount* falls in — the taxpayer's marginal rate."""
    for band in bands:
        lower, upper, _rate = band
        if Decimal(str(lower)) <= amount and (upper is None or amount < Decimal(str(upper))):
            return band
    return bands[-1]
