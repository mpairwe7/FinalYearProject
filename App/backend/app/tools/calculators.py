"""Deterministic tax calculators exposed as agent tools.

All calculators are:

- **Pure Python** — no network, no randomness, fully reproducible.
- **Unit-testable** — each ``execute()`` is a pure function of its args.
- **Version-stamped** — each result carries the FY rate table version
  so stale knowledge is detectable downstream.

Rate data is hard-coded for FY2025-26 as of this writing.  When URA
updates rates, bump the table in :data:`_FY2025_26_RATES` and add a
new table for the next year — the ``fiscal_year`` arg selects which.

References:
- Uganda Revenue Authority FY2025-26 rates
- Income Tax Act (Cap 340), Section 6
- VAT Act (Cap 349), Schedule 3
"""

from __future__ import annotations

import logging
from typing import Any

from . import Tool, ToolSchema, ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate tables (FY2025-26)
# ---------------------------------------------------------------------------
_FY2025_26_RATES = {
    "version": "FY2025-26",
    "vat_standard": 0.18,
    # PAYE (resident individual monthly) — progressive bands in UGX
    "paye_bands_resident": [
        # (lower_bound, upper_bound, rate, flat_amount_below_upper)
        (0,         235_000,    0.00, 0),
        (235_000,   335_000,    0.10, 0),
        (335_000,   410_000,    0.20, 10_000),
        (410_000, 10_000_000,   0.30, 25_000),
        (10_000_000, float("inf"), 0.40, 2_902_500),
    ],
    # Non-resident PAYE rates (simpler, flat brackets)
    "paye_bands_non_resident": [
        (0,         335_000,    0.10, 0),
        (335_000,   410_000,    0.20, 33_500),
        (410_000, 10_000_000,   0.30, 48_500),
        (10_000_000, float("inf"), 0.40, 2_925_500),
    ],
    "corporation_tax": 0.30,
    "capital_gains_corporate": 0.30,    # added to gross income
    "rental_tax_individual": 0.12,      # 12% on gross rental income above 2.82M p.a.
    "rental_tax_individual_threshold": 2_820_000,
    "rental_tax_company": 0.30,
    "withholding_services": 0.06,
    "withholding_goods": 0.06,
    "withholding_management_fees": 0.15,
    "customs_duty_common": 0.25,        # typical consumer goods import
    "withholding_dividend": 0.15,
}

_RATE_TABLES = {
    "FY2025-26": _FY2025_26_RATES,
}


def _get_rates(fiscal_year: str = "FY2025-26") -> dict[str, Any]:
    table = _RATE_TABLES.get(fiscal_year)
    if table is None:
        raise ValueError(
            f"Unknown fiscal year '{fiscal_year}'. "
            f"Known years: {sorted(_RATE_TABLES.keys())}"
        )
    return table


# ---------------------------------------------------------------------------
# VAT calculator
# ---------------------------------------------------------------------------
class VATCalculator(Tool):
    """Compute Ugandan VAT at the standard rate (18%)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_vat",
            description=(
                "Calculate Value Added Tax (VAT) in Uganda. "
                "Use this when the user asks 'how much VAT' or "
                "'total after VAT' or needs to convert a gross "
                "price to a VAT-exclusive price. Standard rate is "
                "18% for most goods and services."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "The base amount in UGX.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["add", "extract"],
                        "description": (
                            "'add' treats `amount` as VAT-exclusive and returns "
                            "amount + VAT. 'extract' treats `amount` as VAT-inclusive "
                            "(gross) and returns the VAT portion and the net amount."
                        ),
                        "default": "add",
                    },
                    "rate": {
                        "type": "number",
                        "description": "VAT rate as decimal (default 0.18).",
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": "FY identifier, e.g. 'FY2025-26'.",
                    },
                },
                "required": ["amount"],
            },
            risk="low",
        )

    def execute(
        self,
        amount: float,
        direction: str = "add",
        rate: float | None = None,
        fiscal_year: str = "FY2025-26",
    ) -> dict[str, Any]:
        rates = _get_rates(fiscal_year)
        if rate is None:
            rate = float(rates["vat_standard"])
        amount = float(amount)
        if amount < 0:
            return {"ok": False, "error": "amount must be non-negative"}

        if direction == "add":
            vat = amount * rate
            total = amount + vat
            return {
                "ok": True,
                "direction": "add",
                "net": round(amount, 2),
                "rate": rate,
                "vat": round(vat, 2),
                "gross": round(total, 2),
                "fiscal_year": fiscal_year,
                "explanation": (
                    f"VAT at {rate * 100:.0f}% on a net of "
                    f"UGX {amount:,.2f} is UGX {vat:,.2f}, for a "
                    f"gross of UGX {total:,.2f}."
                ),
            }
        if direction == "extract":
            net = amount / (1 + rate)
            vat = amount - net
            return {
                "ok": True,
                "direction": "extract",
                "net": round(net, 2),
                "rate": rate,
                "vat": round(vat, 2),
                "gross": round(amount, 2),
                "fiscal_year": fiscal_year,
                "explanation": (
                    f"Extracting {rate * 100:.0f}% VAT from UGX "
                    f"{amount:,.2f} gives VAT UGX {vat:,.2f} and "
                    f"net UGX {net:,.2f}."
                ),
            }
        return {"ok": False, "error": f"direction must be 'add' or 'extract' (got {direction!r})"}


# ---------------------------------------------------------------------------
# PAYE calculator
# ---------------------------------------------------------------------------
class PAYECalculator(Tool):
    """Compute monthly PAYE on employment income."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_paye",
            description=(
                "Calculate monthly PAYE (Pay As You Earn) for a "
                "Ugandan employee from their gross monthly salary. "
                "Uses progressive bands per the Income Tax Act. "
                "Use this when the user asks 'how much PAYE will I "
                "pay' or 'what's my take-home pay'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "monthly_gross": {
                        "type": "number",
                        "description": "Gross monthly salary in UGX, before any deductions.",
                    },
                    "residency": {
                        "type": "string",
                        "enum": ["resident", "non_resident"],
                        "description": "Tax residency status. Default 'resident'.",
                        "default": "resident",
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": "FY identifier, e.g. 'FY2025-26'.",
                    },
                },
                "required": ["monthly_gross"],
            },
            risk="low",
        )

    def execute(
        self,
        monthly_gross: float,
        residency: str = "resident",
        fiscal_year: str = "FY2025-26",
    ) -> dict[str, Any]:
        rates = _get_rates(fiscal_year)
        bands = (
            rates["paye_bands_resident"]
            if residency == "resident"
            else rates["paye_bands_non_resident"]
        )
        gross = float(monthly_gross)
        if gross < 0:
            return {"ok": False, "error": "monthly_gross must be non-negative"}

        paye = 0.0
        applied_band: tuple[float, float, float, float] | None = None
        for lo, hi, rate, flat in bands:
            if lo <= gross < hi:
                marginal = (gross - lo) * rate
                paye = flat + marginal
                applied_band = (lo, hi, rate, flat)
                break
        # Edge case: gross >= final upper bound (the `hi=inf` band)
        if applied_band is None:
            lo, hi, rate, flat = bands[-1]
            marginal = (gross - lo) * rate
            paye = flat + marginal
            applied_band = (lo, hi, rate, flat)

        net = gross - paye
        lo, hi, rate, flat = applied_band
        return {
            "ok": True,
            "monthly_gross": round(gross, 2),
            "residency": residency,
            "paye": round(paye, 2),
            "net_take_home": round(net, 2),
            "fiscal_year": fiscal_year,
            "band": {
                "lower": lo,
                "upper": None if hi == float("inf") else hi,
                "marginal_rate": rate,
                "flat_portion": flat,
            },
            "explanation": (
                f"On a gross of UGX {gross:,.2f}, the applicable "
                f"band is {rate * 100:.0f}% above UGX {lo:,.0f}. "
                f"PAYE = UGX {paye:,.2f}, net pay = UGX {net:,.2f}."
            ),
        }


# ---------------------------------------------------------------------------
# Corporation / Income tax
# ---------------------------------------------------------------------------
class CorporationTaxCalculator(Tool):
    """Compute 30% corporation tax on chargeable income."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_corporation_tax",
            description=(
                "Calculate Ugandan corporation (company) income tax "
                "on a given chargeable income. Resident and non-resident "
                "companies both pay 30%. Use when the user asks 'how "
                "much corporation tax' or wants to estimate a company's "
                "annual tax bill."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chargeable_income": {
                        "type": "number",
                        "description": "Annual chargeable income in UGX (gross income minus allowable deductions).",
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": "FY identifier.",
                    },
                },
                "required": ["chargeable_income"],
            },
            risk="low",
        )

    def execute(
        self,
        chargeable_income: float,
        fiscal_year: str = "FY2025-26",
    ) -> dict[str, Any]:
        rates = _get_rates(fiscal_year)
        rate = float(rates["corporation_tax"])
        income = float(chargeable_income)
        if income < 0:
            return {"ok": False, "error": "chargeable_income must be non-negative"}
        tax = income * rate
        return {
            "ok": True,
            "chargeable_income": round(income, 2),
            "rate": rate,
            "tax": round(tax, 2),
            "after_tax": round(income - tax, 2),
            "fiscal_year": fiscal_year,
            "explanation": (
                f"30% corporation tax on chargeable income of "
                f"UGX {income:,.2f} = UGX {tax:,.2f}."
            ),
        }


# ---------------------------------------------------------------------------
# Capital gains (corporate)
# ---------------------------------------------------------------------------
class CapitalGainsCalculator(Tool):
    """Compute CGT for a corporate entity."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_capital_gains",
            description=(
                "Calculate Ugandan capital gains tax for a corporate "
                "entity. Gains on business assets, shares, or "
                "commercial buildings are added to gross income and "
                "taxed at the standard corporate rate (30%). Use when "
                "the user asks 'how much CGT' or 'tax on selling "
                "shares'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sale_price": {
                        "type": "number",
                        "description": "The price the asset was sold for (UGX).",
                    },
                    "cost_base": {
                        "type": "number",
                        "description": "Original acquisition cost plus allowable improvements (UGX).",
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": "FY identifier.",
                    },
                },
                "required": ["sale_price", "cost_base"],
            },
            risk="low",
        )

    def execute(
        self,
        sale_price: float,
        cost_base: float,
        fiscal_year: str = "FY2025-26",
    ) -> dict[str, Any]:
        rates = _get_rates(fiscal_year)
        rate = float(rates["capital_gains_corporate"])
        sale = float(sale_price)
        cost = float(cost_base)
        if sale < 0 or cost < 0:
            return {"ok": False, "error": "sale_price and cost_base must be non-negative"}
        gain = sale - cost
        if gain <= 0:
            return {
                "ok": True,
                "sale_price": round(sale, 2),
                "cost_base": round(cost, 2),
                "gain": round(gain, 2),
                "tax": 0.0,
                "fiscal_year": fiscal_year,
                "explanation": (
                    "No capital gain (or a loss) — no CGT is payable. "
                    "Losses may be offsettable against future gains; "
                    "consult URA."
                ),
            }
        tax = gain * rate
        return {
            "ok": True,
            "sale_price": round(sale, 2),
            "cost_base": round(cost, 2),
            "gain": round(gain, 2),
            "rate": rate,
            "tax": round(tax, 2),
            "net_proceeds": round(sale - tax, 2),
            "fiscal_year": fiscal_year,
            "explanation": (
                f"Gain of UGX {gain:,.2f} (sale UGX {sale:,.2f} minus "
                f"cost UGX {cost:,.2f}) at 30% = UGX {tax:,.2f} CGT."
            ),
        }


# ---------------------------------------------------------------------------
# Customs duty (simplified)
# ---------------------------------------------------------------------------
class CustomsDutyCalculator(Tool):
    """Rough customs + VAT estimator for imported goods."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_customs_duty",
            description=(
                "Estimate the total import cost (customs duty + VAT) "
                "for goods being imported into Uganda. This is a "
                "rough estimate — the actual rate depends on the "
                "EAC CET tariff code. Use when the user asks 'how "
                "much will it cost to import' or wants a ballpark."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cif_value": {
                        "type": "number",
                        "description": "Cost, Insurance and Freight value in UGX (declared landed value).",
                    },
                    "duty_rate": {
                        "type": "number",
                        "description": "Customs duty rate (e.g. 0.0 for raw materials, 0.1 for intermediate, 0.25 for finished goods). Default 0.25.",
                    },
                    "include_vat": {
                        "type": "boolean",
                        "description": "Whether to add 18% VAT on (CIF + duty). Default true.",
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": "FY identifier.",
                    },
                },
                "required": ["cif_value"],
            },
            risk="low",
        )

    def execute(
        self,
        cif_value: float,
        duty_rate: float | None = None,
        include_vat: bool = True,
        fiscal_year: str = "FY2025-26",
    ) -> dict[str, Any]:
        rates = _get_rates(fiscal_year)
        if duty_rate is None:
            duty_rate = float(rates["customs_duty_common"])
        cif = float(cif_value)
        if cif < 0:
            return {"ok": False, "error": "cif_value must be non-negative"}

        duty = cif * duty_rate
        vat_base = cif + duty
        vat = vat_base * float(rates["vat_standard"]) if include_vat else 0.0
        total = cif + duty + vat

        return {
            "ok": True,
            "cif_value": round(cif, 2),
            "duty_rate": duty_rate,
            "duty": round(duty, 2),
            "vat_included": include_vat,
            "vat": round(vat, 2),
            "landed_cost": round(total, 2),
            "fiscal_year": fiscal_year,
            "explanation": (
                f"CIF UGX {cif:,.2f} + duty {duty_rate * 100:.0f}% "
                f"(UGX {duty:,.2f})"
                + (f" + 18% VAT (UGX {vat:,.2f})" if include_vat else "")
                + f" = UGX {total:,.2f} landed cost. "
                f"Note: actual duty depends on EAC CET tariff code — "
                f"use URA EACCustoms for exact classification."
            ),
        }


# ---------------------------------------------------------------------------
# Register everything on import
# ---------------------------------------------------------------------------
ToolRegistry.register(VATCalculator())
ToolRegistry.register(PAYECalculator())
ToolRegistry.register(CorporationTaxCalculator())
ToolRegistry.register(CapitalGainsCalculator())
ToolRegistry.register(CustomsDutyCalculator())
