"""Deterministic tax calculators exposed as agent tools.

All calculators are:

- **Pure** — no network, no randomness, no clock beyond the fiscal year
  the caller asked for, so a result is reproducible from its arguments.
- **Decimal** — money arithmetic runs through :mod:`app.tax.money`, not
  binary floats.
- **Provenance-stamped** — every result carries the fiscal year it used,
  whether that table is confirmed or provisional, the statutory basis
  for each rate applied, and the sources the table was compiled from.

Rates themselves live in :mod:`app.tax.tables` as effective-dated JSON,
so a new fiscal year is a data file plus a test — nothing here changes.
Omitting ``fiscal_year`` resolves the table in force today rather than
defaulting to a year frozen in the source, which is what let this file
keep serving FY2025-26 figures after 1 July 2026.

References:
- Income Tax Act (Cap 340), Third Schedule
- VAT Act (Cap 349), Schedule 3
- Income Tax / VAT (Amendment) Acts 2026 (effective 1 July 2026)
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..tax.money import AmountError, apply_bands, marginal_band, to_decimal, to_float, to_rate
from ..tax.tables import (
    RateTable,
    RateTableError,
    fiscal_years_defining,
    get_table,
    list_fiscal_years,
)
from . import Tool, ToolRegistry, ToolSchema

logger = logging.getLogger(__name__)

#: MCP server that owns the calculators.
TAX_CALCULATOR_NAMESPACE = "tax_calculator"


class RateUnavailableError(RuntimeError):
    """Raised when a fiscal year's table has no figure for a needed rate."""


@dataclass
class CalcResult:
    """A calculator's own output plus the rate keys it consulted.

    ``rate_keys`` drives the provenance block, so a result cites only the
    statutory basis it actually relied on instead of the whole table's.
    """

    payload: dict[str, Any]
    rate_keys: tuple[str, ...] = ()


# Shared JSON-Schema fragments for the two arguments every calculator takes.
_PERIOD_PARAMS: dict[str, Any] = {
    "fiscal_year": {
        "type": "string",
        "description": (
            "URA fiscal year identifier, e.g. 'FY2026-27'. Omit to use the "
            "year in force today; pass it explicitly when the user asks "
            "about a past period or an amended return."
        ),
    },
    "as_of": {
        "type": "string",
        "format": "date",
        "description": (
            "ISO date (YYYY-MM-DD) the calculation applies to. Selects the "
            "fiscal year in force on that date. Ignored when fiscal_year is given."
        ),
    },
}


#: Every calculator returns the same envelope: the arithmetic, an
#: explanation, and the provenance of the rates it used.  Published as
#: the MCP ``outputSchema`` so a client can validate structuredContent
#: without each calculator hand-writing a schema for its own fields.
CALCULATOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "error": {"type": "string"},
        "explanation": {"type": "string"},
        "fiscal_year": {"type": "string"},
        "verification_warning": {"type": "string"},
        "rate_basis": {
            "type": "object",
            "properties": {
                "fiscal_year": {"type": "string"},
                "status": {"type": "string", "enum": ["confirmed", "provisional"]},
                "effective_from": {"type": "string", "format": "date"},
                "effective_to": {"type": ["string", "null"], "format": "date"},
                "legal_basis": {"type": "object", "additionalProperties": {"type": "string"}},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "publisher": {"type": "string"},
                            "url": {"type": "string"},
                        },
                        "required": ["id", "title"],
                    },
                },
            },
            "required": ["fiscal_year", "status"],
        },
    },
    "required": ["ok"],
    "additionalProperties": True,
}


def _schema_params(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build a tool ``parameters`` schema with the shared period arguments."""
    return {
        "type": "object",
        "properties": {**properties, **_PERIOD_PARAMS},
        "required": required,
        "additionalProperties": False,
    }


def _require_scalar(table: RateTable, key: str) -> Decimal:
    """Fetch a scalar rate, failing closed when the fiscal year lacks it.

    A rate that a given year's law does not define (say a withholding
    category introduced in 2026, asked about for 2025) must produce an
    error naming the years that do define it — never a fallback value.
    """
    value = table.get(key)
    if value is None:
        available = fiscal_years_defining(key)
        hint = f" It is defined for: {', '.join(available)}." if available else ""
        raise RateUnavailableError(
            f"'{key}' is not defined in the {table.fiscal_year} rate table.{hint}"
        )
    if not isinstance(value, int | float):
        raise RateUnavailableError(f"'{key}' is not a scalar rate in {table.fiscal_year}")
    return Decimal(str(value))


def _require_bands(table: RateTable, key: str) -> list[tuple[float, float | None, float]]:
    bands = table.get(key)
    if not bands:
        raise RateUnavailableError(f"'{key}' is not defined in the {table.fiscal_year} rate table")
    return bands


def _ugx(amount: Decimal | float) -> str:
    return f"UGX {float(amount):,.2f}"


class CalculatorTool(Tool):
    """Base for the deterministic calculators.

    Handles what every calculator shares — resolving the fiscal year,
    turning argument and rate-availability failures into structured
    ``{"ok": false, ...}`` results the model can read, and stamping
    provenance onto the payload — so subclasses only implement the
    arithmetic in :meth:`compute`.
    """

    def compute(self, table: RateTable, **kwargs: Any) -> CalcResult:
        raise NotImplementedError

    def execute(
        self,
        fiscal_year: str | None = None,
        as_of: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            day = _dt.date.fromisoformat(as_of) if as_of else None
        except ValueError:
            return {"ok": False, "error": f"as_of must be an ISO date (YYYY-MM-DD), got {as_of!r}"}

        try:
            table = get_table(fiscal_year, as_of=day)
        except RateTableError as exc:
            return {"ok": False, "error": str(exc), "known_fiscal_years": list_fiscal_years()}

        try:
            result = self.compute(table, **kwargs)
        except AmountError as exc:
            return {"ok": False, "error": str(exc)}
        except RateUnavailableError as exc:
            return {"ok": False, "error": str(exc), "fiscal_year": table.fiscal_year}

        payload = result.payload
        if payload.get("ok") is False:
            return payload
        payload["ok"] = True
        payload["fiscal_year"] = table.fiscal_year
        basis = table.provenance(*result.rate_keys)
        payload["rate_basis"] = basis
        if not table.confirmed:
            payload["verification_warning"] = (
                f"{table.fiscal_year} figures are provisional — "
                f"{table.verification_note or 'confirm them with URA before relying on them.'}"
            )
        elif basis.get("unverified"):
            # The table is confirmed, but this particular calculation used a
            # figure that is not yet reconciled against the primary text.
            # Caveating the whole table instead would train users to ignore
            # the caveat on the figures that are settled.
            payload["verification_warning"] = (
                f"{table.fiscal_year} is confirmed, but "
                f"{', '.join(basis['unverified'])} is not yet reconciled against the "
                f"primary legislative text — confirm it with URA."
            )
        return payload


# ---------------------------------------------------------------------------
# VAT
# ---------------------------------------------------------------------------
class VATCalculator(CalculatorTool):
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
            parameters=_schema_params(
                {
                    "amount": {
                        "type": "number",
                        "minimum": 0,
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
                        "minimum": 0,
                        "maximum": 1,
                        "description": (
                            "Override the VAT rate as a decimal fraction (e.g. 0.18). "
                            "Only pass this for a non-standard supply."
                        ),
                    },
                },
                ["amount"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        amount: Any,
        direction: str = "add",
        rate: Any = None,
        **_: Any,
    ) -> CalcResult:
        if direction not in ("add", "extract"):
            return CalcResult(
                {"ok": False, "error": f"direction must be 'add' or 'extract' (got {direction!r})"}
            )
        net_or_gross = to_decimal(amount, field="amount")
        vat_rate = (
            to_rate(rate, field="rate") if rate is not None else _require_scalar(table, "vat_standard")
        )

        if direction == "add":
            net = net_or_gross
            vat = net * vat_rate
            gross = net + vat
        else:
            gross = net_or_gross
            net = gross / (Decimal(1) + vat_rate)
            vat = gross - net

        explanation = (
            f"VAT at {vat_rate * 100:.0f}% on a net of {_ugx(net)} is {_ugx(vat)}, "
            f"for a gross of {_ugx(gross)}."
            if direction == "add"
            else (
                f"Extracting {vat_rate * 100:.0f}% VAT from {_ugx(gross)} gives VAT "
                f"{_ugx(vat)} and net {_ugx(net)}."
            )
        )
        return CalcResult(
            {
                "direction": direction,
                "net": to_float(net),
                "rate": float(vat_rate),
                "vat": to_float(vat),
                "gross": to_float(gross),
                "explanation": explanation,
            },
            rate_keys=("vat_standard",),
        )


class VATRegistrationCheck(CalculatorTool):
    """Check whether a turnover crosses the VAT registration threshold."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="check_vat_registration",
            description=(
                "Check whether a business must register for VAT in Uganda, given its "
                "annual turnover. The compulsory registration threshold rose from "
                "UGX 150 million to UGX 300 million with effect from 1 July 2026, so "
                "always state which fiscal year the answer applies to. Use when the "
                "user asks 'do I need to register for VAT' or 'what is the VAT threshold'."
            ),
            parameters=_schema_params(
                {
                    "annual_turnover": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Total annual taxable turnover in UGX.",
                    }
                },
                ["annual_turnover"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(self, table: RateTable, annual_turnover: Any, **_: Any) -> CalcResult:
        turnover = to_decimal(annual_turnover, field="annual_turnover")
        threshold = _require_scalar(table, "vat_registration_threshold_annual")
        required = turnover >= threshold
        headroom = threshold - turnover
        explanation = (
            f"Turnover of {_ugx(turnover)} is at or above the {table.fiscal_year} VAT "
            f"registration threshold of {_ugx(threshold)}, so registration is compulsory."
            if required
            else (
                f"Turnover of {_ugx(turnover)} is below the {table.fiscal_year} VAT "
                f"registration threshold of {_ugx(threshold)} — {_ugx(headroom)} of "
                f"headroom. Registration is not compulsory, but voluntary registration "
                f"is available."
            )
        )
        return CalcResult(
            {
                "annual_turnover": to_float(turnover),
                "threshold": to_float(threshold),
                "registration_required": required,
                "headroom": to_float(headroom) if not required else 0.0,
                "explanation": explanation,
            },
            rate_keys=("vat_registration_threshold_annual",),
        )


# ---------------------------------------------------------------------------
# PAYE
# ---------------------------------------------------------------------------
class PAYECalculator(CalculatorTool):
    """Compute monthly PAYE on employment income."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_paye",
            description=(
                "Calculate monthly PAYE (Pay As You Earn) for a "
                "Ugandan employee from their gross monthly salary. "
                "Uses progressive bands per the Income Tax Act. "
                "Use this when the user asks 'how much PAYE will I pay'. "
                "For 'what's my take-home pay', pass include_nssf=true so the "
                "employee's 5% NSSF contribution is deducted too — PAYE alone "
                "overstates what actually reaches the employee's account."
            ),
            parameters=_schema_params(
                {
                    "monthly_gross": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Gross monthly salary in UGX, before any deductions.",
                    },
                    "residency": {
                        "type": "string",
                        "enum": ["resident", "non_resident"],
                        "description": "Tax residency status. Default 'resident'.",
                        "default": "resident",
                    },
                    "include_nssf": {
                        "type": "boolean",
                        "description": (
                            "Also deduct the employee's standard NSSF contribution "
                            "(5% of gross) to give actual take-home pay. NSSF is a "
                            "social-security contribution, not a URA tax, and is not "
                            "deductible before PAYE. Default false (PAYE only)."
                        ),
                        "default": False,
                    },
                },
                ["monthly_gross"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        monthly_gross: Any,
        residency: str = "resident",
        include_nssf: bool = False,
        **_: Any,
    ) -> CalcResult:
        if residency not in ("resident", "non_resident"):
            return CalcResult(
                {
                    "ok": False,
                    "error": f"residency must be 'resident' or 'non_resident' (got {residency!r})",
                }
            )
        gross = to_decimal(monthly_gross, field="monthly_gross")
        key = f"paye_bands_{residency}"
        bands = _require_bands(table, key)

        paye, breakdown = apply_bands(gross, bands)
        lower, upper, marginal_rate = marginal_band(gross, bands)
        effective_rate = (paye / gross) if gross > 0 else Decimal(0)

        # NSSF comes out of pay *after* PAYE: the employee's own
        # contribution is not a deduction against employment income, so
        # it changes take-home without changing the tax.
        keys = [key]
        nssf_rate = Decimal(0)
        nssf = Decimal(0)
        if include_nssf:
            nssf_rate = _require_scalar(table, "nssf_employee_contribution")
            nssf = gross * nssf_rate
            keys.append("nssf_employee_contribution")
        net = gross - paye - nssf

        deducted = f"PAYE {_ugx(paye)}"
        if include_nssf:
            deducted += f" and NSSF {_ugx(nssf)} ({nssf_rate * 100:.0f}%)"
        # Naming what was *not* deducted matters as much as the total:
        # a figure labelled "take-home" that silently omits a deduction
        # is read as final and reconciled against a payslip that differs.
        caveat = (
            "Local Service Tax and any voluntary deductions are not included."
            if include_nssf
            else (
                "This is net of PAYE only — NSSF (5%), Local Service Tax and any "
                "voluntary deductions still come off. Ask again with NSSF included "
                "for a closer take-home figure."
            )
        )

        return CalcResult(
            {
                "monthly_gross": to_float(gross),
                "residency": residency,
                "paye": to_float(paye),
                "nssf_included": include_nssf,
                "nssf_employee": to_float(nssf),
                "net_take_home": to_float(net),
                "annual_paye": to_float(paye * 12),
                "effective_rate": round(float(effective_rate), 4),
                "band": {
                    "lower": lower,
                    "upper": upper,
                    "marginal_rate": marginal_rate,
                },
                "bands_applied": breakdown,
                "deductions_note": caveat,
                "explanation": (
                    f"On a gross of {_ugx(gross)}, the applicable band is "
                    f"{marginal_rate * 100:.0f}% above {_ugx(lower)}. "
                    f"After {deducted}, net pay = {_ugx(net)}. {caveat}"
                ),
            },
            rate_keys=tuple(keys),
        )


# ---------------------------------------------------------------------------
# Corporation / Income tax
# ---------------------------------------------------------------------------
class CorporationTaxCalculator(CalculatorTool):
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
            parameters=_schema_params(
                {
                    "chargeable_income": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Annual chargeable income in UGX "
                            "(gross income minus allowable deductions)."
                        ),
                    }
                },
                ["chargeable_income"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(self, table: RateTable, chargeable_income: Any, **_: Any) -> CalcResult:
        income = to_decimal(chargeable_income, field="chargeable_income")
        rate = _require_scalar(table, "corporation_tax")
        tax = income * rate
        return CalcResult(
            {
                "chargeable_income": to_float(income),
                "rate": float(rate),
                "tax": to_float(tax),
                "after_tax": to_float(income - tax),
                "explanation": (
                    f"{rate * 100:.0f}% corporation tax on chargeable income of "
                    f"{_ugx(income)} = {_ugx(tax)}."
                ),
            },
            rate_keys=("corporation_tax",),
        )


# ---------------------------------------------------------------------------
# Capital gains (corporate)
# ---------------------------------------------------------------------------
class CapitalGainsCalculator(CalculatorTool):
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
            parameters=_schema_params(
                {
                    "sale_price": {
                        "type": "number",
                        "minimum": 0,
                        "description": "The price the asset was sold for (UGX).",
                    },
                    "cost_base": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Original acquisition cost plus allowable improvements (UGX)."
                        ),
                    },
                },
                ["sale_price", "cost_base"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(self, table: RateTable, sale_price: Any, cost_base: Any, **_: Any) -> CalcResult:
        sale = to_decimal(sale_price, field="sale_price")
        cost = to_decimal(cost_base, field="cost_base")
        rate = _require_scalar(table, "capital_gains_corporate")
        gain = sale - cost

        if gain <= 0:
            return CalcResult(
                {
                    "sale_price": to_float(sale),
                    "cost_base": to_float(cost),
                    "gain": to_float(gain),
                    "tax": 0.0,
                    "explanation": (
                        "No capital gain (or a loss) — no CGT is payable. "
                        "Losses may be offsettable against future gains; consult URA."
                    ),
                },
                rate_keys=("capital_gains_corporate",),
            )

        tax = gain * rate
        return CalcResult(
            {
                "sale_price": to_float(sale),
                "cost_base": to_float(cost),
                "gain": to_float(gain),
                "rate": float(rate),
                "tax": to_float(tax),
                "net_proceeds": to_float(sale - tax),
                "explanation": (
                    f"Gain of {_ugx(gain)} (sale {_ugx(sale)} minus cost {_ugx(cost)}) "
                    f"at {rate * 100:.0f}% = {_ugx(tax)} CGT."
                ),
            },
            rate_keys=("capital_gains_corporate",),
        )


# ---------------------------------------------------------------------------
# Customs duty (simplified)
# ---------------------------------------------------------------------------
class CustomsDutyCalculator(CalculatorTool):
    """Rough customs + VAT estimator for imported goods."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_customs_duty",
            description=(
                "Estimate the total import cost (customs duty + VAT, plus the "
                "environmental levy on used clothing) for goods being imported "
                "into Uganda. This is a rough estimate — the binding duty rate is "
                "the EAC CET tariff line for the goods' HS code. Use when the user "
                "asks 'how much will it cost to import' or wants a ballpark."
            ),
            parameters=_schema_params(
                {
                    "cif_value": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Cost, Insurance and Freight value in UGX (declared landed value)."
                        ),
                    },
                    "duty_rate": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": (
                            "Customs duty rate as a decimal fraction (0.0 raw materials, "
                            "0.1 intermediate, 0.25 finished goods). Defaults to the "
                            "finished-goods band."
                        ),
                    },
                    "include_vat": {
                        "type": "boolean",
                        "description": "Whether to add VAT on (CIF + duty). Default true.",
                        "default": True,
                    },
                    "goods_category": {
                        "type": "string",
                        "enum": ["general", "used_clothing"],
                        "description": (
                            "'used_clothing' additionally applies the environmental levy "
                            "on worn-clothing imports, charged on the CIF value."
                        ),
                        "default": "general",
                    },
                },
                ["cif_value"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        cif_value: Any,
        duty_rate: Any = None,
        include_vat: bool = True,
        goods_category: str = "general",
        **_: Any,
    ) -> CalcResult:
        if goods_category not in ("general", "used_clothing"):
            return CalcResult(
                {
                    "ok": False,
                    "error": (
                        f"goods_category must be 'general' or 'used_clothing' "
                        f"(got {goods_category!r})"
                    ),
                }
            )
        cif = to_decimal(cif_value, field="cif_value")
        keys: list[str] = ["customs_duty_common"]
        rate = (
            to_rate(duty_rate, field="duty_rate")
            if duty_rate is not None
            else _require_scalar(table, "customs_duty_common")
        )

        duty = cif * rate
        levy = Decimal(0)
        levy_rate = Decimal(0)
        if goods_category == "used_clothing":
            levy_rate = _require_scalar(table, "environmental_levy_used_clothing")
            levy = cif * levy_rate
            keys.append("environmental_levy_used_clothing")

        vat_rate = Decimal(0)
        vat = Decimal(0)
        if include_vat:
            vat_rate = _require_scalar(table, "vat_standard")
            vat = (cif + duty + levy) * vat_rate
            keys.append("vat_standard")

        total = cif + duty + levy + vat
        parts = [f"CIF {_ugx(cif)} + duty {rate * 100:.0f}% ({_ugx(duty)})"]
        if levy > 0:
            parts.append(f" + environmental levy {levy_rate * 100:.0f}% ({_ugx(levy)})")
        if include_vat:
            parts.append(f" + {vat_rate * 100:.0f}% VAT ({_ugx(vat)})")

        return CalcResult(
            {
                "cif_value": to_float(cif),
                "duty_rate": float(rate),
                "duty": to_float(duty),
                "goods_category": goods_category,
                "environmental_levy_rate": float(levy_rate),
                "environmental_levy": to_float(levy),
                "vat_included": include_vat,
                "vat": to_float(vat),
                "landed_cost": to_float(total),
                "explanation": (
                    "".join(parts) + f" = {_ugx(total)} landed cost. "
                    "Note: the binding duty rate is the EAC CET tariff line for the "
                    "goods' HS code — use URA EACCustoms for exact classification."
                ),
            },
            rate_keys=tuple(keys),
        )


# ---------------------------------------------------------------------------
# Rental income tax
# ---------------------------------------------------------------------------
class RentalIncomeTaxCalculator(CalculatorTool):
    """Compute annual rental income tax for individuals or companies."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_rental_tax",
            description=(
                "Calculate annual rental income tax in Uganda. Individuals "
                "pay 12% of gross rental income above the UGX 2,820,000 "
                "annual threshold; companies pay 30% of chargeable rental "
                "income after deductible expenses (capped at 50% of gross "
                "rent). Use when the user asks about tax on rent they "
                "collect from tenants."
            ),
            parameters=_schema_params(
                {
                    "landlord_type": {
                        "type": "string",
                        "enum": ["individual", "company"],
                        "description": "Whether the landlord is an individual or a company.",
                        "default": "individual",
                    },
                    "annual_gross_rent": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Total gross rental income for the year in UGX.",
                    },
                    "allowable_expenses": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Company landlords only: deductible expenses in UGX "
                            "(capped at 50% of gross rent). Ignored for individuals."
                        ),
                        "default": 0,
                    },
                },
                ["annual_gross_rent"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        annual_gross_rent: Any,
        landlord_type: str = "individual",
        allowable_expenses: Any = 0,
        **_: Any,
    ) -> CalcResult:
        if landlord_type not in ("individual", "company"):
            return CalcResult(
                {
                    "ok": False,
                    "error": (
                        f"landlord_type must be 'individual' or 'company' "
                        f"(got {landlord_type!r})"
                    ),
                }
            )
        rent = to_decimal(annual_gross_rent, field="annual_gross_rent")

        if landlord_type == "company":
            cap = _require_scalar(table, "rental_company_expense_cap")
            rate = _require_scalar(table, "rental_tax_company")
            expenses = min(to_decimal(allowable_expenses, field="allowable_expenses"), rent * cap)
            chargeable = rent - expenses
            tax = chargeable * rate
            return CalcResult(
                {
                    "landlord_type": "company",
                    "annual_gross_rent": to_float(rent),
                    "allowable_expenses": to_float(expenses),
                    "chargeable_income": to_float(chargeable),
                    "rate": float(rate),
                    "tax": to_float(tax),
                    "explanation": (
                        f"Company rental tax at {rate * 100:.0f}% on chargeable income of "
                        f"{_ugx(chargeable)} (gross rent {_ugx(rent)} minus deductible "
                        f"expenses {_ugx(expenses)}, capped at {cap * 100:.0f}% of gross) "
                        f"= {_ugx(tax)}."
                    ),
                },
                rate_keys=("rental_tax_company", "rental_company_expense_cap"),
            )

        threshold = _require_scalar(table, "rental_tax_individual_threshold")
        rate = _require_scalar(table, "rental_tax_individual")
        taxable = max(rent - threshold, Decimal(0))
        tax = taxable * rate
        return CalcResult(
            {
                "landlord_type": "individual",
                "annual_gross_rent": to_float(rent),
                "threshold": to_float(threshold),
                "taxable_amount": to_float(taxable),
                "rate": float(rate),
                "tax": to_float(tax),
                "explanation": (
                    f"Individual rental tax is {rate * 100:.0f}% of gross rent above the "
                    f"{_ugx(threshold)} annual threshold: {rate * 100:.0f}% x "
                    f"{_ugx(taxable)} = {_ugx(tax)}."
                    if taxable > 0
                    else (
                        f"Gross rent of {_ugx(rent)} is within the {_ugx(threshold)} annual "
                        f"threshold, so no rental tax is due."
                    )
                ),
            },
            rate_keys=("rental_tax_individual", "rental_tax_individual_threshold"),
        )


# ---------------------------------------------------------------------------
# Withholding tax
# ---------------------------------------------------------------------------
class WithholdingTaxCalculator(CalculatorTool):
    """Compute withholding tax on common payment types."""

    #: Payment type → rate key.  A type whose key is absent from the
    #: resolved fiscal year's table fails closed via :func:`_require_rate`,
    #: which is how pre-2026 years reject the categories the 2026
    #: amendments introduced.
    _RATE_KEYS: dict[str, str] = {
        "services": "withholding_services",
        "goods": "withholding_goods",
        "management_fees": "withholding_management_fees",
        "dividend": "withholding_dividend",
        "royalty": "withholding_royalty",
        "public_entertainer": "withholding_public_entertainer",
        "betting_winnings": "withholding_betting_winnings",
        "foreign_interest": "withholding_foreign_interest",
        "telecom_commission": "withholding_telecom_commission",
        "non_business_asset": "withholding_non_business_asset",
    }

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_withholding",
            description=(
                "Calculate withholding tax (WHT) deducted at source on a payment in "
                "Uganda: 6% on services, goods and payments to public entertainers; "
                "15% on management fees, dividends, royalties and betting winnings; "
                "10% on telecommunications and mobile-money commissions; "
                "5% on debenture interest paid to non-resident lenders. Public "
                "entertainer, betting-winnings, telecom-commission, non-business-asset "
                "and debenture-interest withholding apply from FY2026-27. Use when the "
                "user asks how much tax is withheld from a payment, invoice, dividend, "
                "commission or winnings."
            ),
            parameters=_schema_params(
                {
                    "payment_type": {
                        "type": "string",
                        "enum": sorted(self._RATE_KEYS),
                        "description": "The kind of payment the WHT applies to.",
                    },
                    "amount": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Gross payment amount in UGX before withholding.",
                    },
                },
                ["payment_type", "amount"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(self, table: RateTable, payment_type: str, amount: Any, **_: Any) -> CalcResult:
        rate_key = self._RATE_KEYS.get(payment_type)
        if rate_key is None:
            return CalcResult(
                {
                    "ok": False,
                    "error": (
                        f"Unknown payment_type '{payment_type}'. "
                        f"Choose one of: {', '.join(sorted(self._RATE_KEYS))}"
                    ),
                }
            )
        gross = to_decimal(amount, field="amount")
        rate = _require_scalar(table, rate_key)
        wht = gross * rate
        net = gross - wht
        label = payment_type.replace("_", " ")
        return CalcResult(
            {
                "payment_type": payment_type,
                "amount": to_float(gross),
                "rate": float(rate),
                "withholding_tax": to_float(wht),
                "net_payable": to_float(net),
                "explanation": (
                    f"Withholding tax on {label} at {rate * 100:.0f}% of {_ugx(gross)} is "
                    f"{_ugx(wht)}; the payee receives {_ugx(net)} net."
                ),
            },
            rate_keys=(rate_key,),
        )


# ---------------------------------------------------------------------------
# Register everything on import
# ---------------------------------------------------------------------------
CALCULATOR_TOOLS: tuple[CalculatorTool, ...] = (
    VATCalculator(),
    VATRegistrationCheck(),
    PAYECalculator(),
    CorporationTaxCalculator(),
    CapitalGainsCalculator(),
    CustomsDutyCalculator(),
    RentalIncomeTaxCalculator(),
    WithholdingTaxCalculator(),
)

for _tool in CALCULATOR_TOOLS:
    ToolRegistry.register(_tool)
