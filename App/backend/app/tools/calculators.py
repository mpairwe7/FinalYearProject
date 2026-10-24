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


def _require_rule(table: RateTable, key: str) -> Any:
    """Fetch a rule parameter (string or scalar) from the rate table, failing closed if absent."""
    value = table.get(key)
    if value is None:
        available = fiscal_years_defining(key)
        hint = f" It is defined for: {', '.join(available)}." if available else ""
        raise RateUnavailableError(
            f"'{key}' is not defined in the {table.fiscal_year} rate table.{hint}"
        )
    return value


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
            if result.rate_keys:
                payload.setdefault("fiscal_year", table.fiscal_year)
                payload.setdefault("rate_basis", table.provenance(*result.rate_keys))
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
                    "benefits": {
                        "type": "array",
                        "description": (
                            "Optional list of taxable benefits in kind to add to monthly gross "
                            "before applying PAYE bands (each item: {kind, monthly_value, verified?})."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string"},
                                "monthly_value": {"type": "number", "minimum": 0},
                                "verified": {"type": "boolean", "default": True},
                            },
                            "required": ["kind", "monthly_value"],
                        },
                    },
                    "compare_residency": {
                        "type": "boolean",
                        "description": (
                            "Also compute the other residency's PAYE (resident vs non_resident) "
                            "and the monthly difference."
                        ),
                        "default": False,
                    },
                    "is_secondary_employment": {
                        "type": "boolean",
                        "description": (
                            "Whether this salary is from a secondary employment (30% flat rate up to "
                            "UGX 10,000,000/month and 40% on the excess above UGX 10,000,000 under Third Schedule)."
                        ),
                        "default": False,
                    },
                    "include_lst": {
                        "type": "boolean",
                        "description": (
                            "Also deduct monthly Local Service Tax (LST) under the Local Governments Act "
                            "Fifth Schedule (graduated bands up to UGX 100,000/year)."
                        ),
                        "default": False,
                    },
                    "has_disability_exemption": {
                        "type": "boolean",
                        "description": (
                            "Whether the employee qualifies for the person-with-disability employment income "
                            "exemption threshold under Income Tax Act s.21(1)(v)."
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

    @staticmethod
    def _compute_monthly_lst(gross: Decimal) -> Decimal:
        """Compute monthly Local Service Tax (LST) under Local Governments Act Fifth Schedule (annual / 12)."""
        g = float(gross)
        if g < 100_000:
            annual = 0
        elif g < 200_000:
            annual = 5_000
        elif g < 300_000:
            annual = 10_000
        elif g < 400_000:
            annual = 20_000
        elif g < 500_000:
            annual = 30_000
        elif g < 600_000:
            annual = 40_000
        elif g < 700_000:
            annual = 60_000
        elif g < 800_000:
            annual = 70_000
        elif g < 900_000:
            annual = 80_000
        elif g <= 1_000_000:
            annual = 90_000
        else:
            annual = 100_000
        return Decimal(str(round(annual / 12.0, 2)))

    def compute(
        self,
        table: RateTable,
        monthly_gross: Any,
        residency: str = "resident",
        include_nssf: bool = False,
        benefits: list[dict[str, Any]] | None = None,
        compare_residency: bool = False,
        is_secondary_employment: bool = False,
        include_lst: bool = False,
        has_disability_exemption: bool = False,
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
        verified_bik = Decimal(0)
        unverified_bik = Decimal(0)
        unverified_kinds: list[str] = []
        for b in benefits or []:
            if not isinstance(b, dict):
                continue
            b_val = to_decimal(b.get("monthly_value", 0), field="monthly_value")
            if b.get("verified", True) is False:
                unverified_bik += b_val
                unverified_kinds.append(str(b.get("kind") or "benefit"))
            else:
                verified_bik += b_val

        chargeable_verified = gross + verified_bik
        chargeable_conditional = chargeable_verified + unverified_bik

        key = f"paye_bands_{residency}"
        bands = _require_bands(table, key)
        keys = [key]

        if has_disability_exemption and table.get("disability_income_tax_threshold_monthly") is not None:
            pwd_thresh = _require_scalar(table, "disability_income_tax_threshold_monthly")
            keys.append("disability_income_tax_threshold_monthly")
            chargeable_verified = max(Decimal(0), chargeable_verified - pwd_thresh)
            chargeable_conditional = max(Decimal(0), chargeable_conditional - pwd_thresh)

        if is_secondary_employment:
            sec_rate = _require_scalar(table, "paye_secondary_employment_rate")
            keys.append("paye_secondary_employment_rate")
            top_cap = Decimal("10000000")
            if chargeable_verified <= top_cap:
                paye = chargeable_verified * sec_rate
                lower, upper, marginal_rate = 0.0, 10_000_000.0, float(sec_rate)
                breakdown = [{"lower": 0.0, "upper": 10_000_000.0, "rate": float(sec_rate), "tax": to_float(paye)}]
            else:
                base_sec = top_cap * sec_rate
                excess_sec = (chargeable_verified - top_cap) * Decimal("0.40")
                paye = base_sec + excess_sec
                lower, upper, marginal_rate = 10_000_000.0, None, 0.40
                breakdown = [
                    {"lower": 0.0, "upper": 10_000_000.0, "rate": float(sec_rate), "tax": to_float(base_sec)},
                    {"lower": 10_000_000.0, "upper": None, "rate": 0.40, "tax": to_float(excess_sec)},
                ]
        else:
            paye, breakdown = apply_bands(chargeable_verified, bands)
            lower, upper, marginal_rate = marginal_band(chargeable_verified, bands)
        effective_rate = (paye / (gross + verified_bik)) if (gross + verified_bik) > 0 else Decimal(0)

        # NSSF comes out of pay *after* PAYE: the employee's own
        # contribution is not a deduction against employment income, so
        # it changes take-home without changing the tax.
        nssf_rate = Decimal(0)
        nssf = Decimal(0)
        nssf_employer = Decimal(0)
        if include_nssf:
            nssf_rate = _require_scalar(table, "nssf_employee_contribution")
            nssf = gross * nssf_rate
            nssf_employer = gross * Decimal("0.10")
            keys.append("nssf_employee_contribution")
        lst_monthly = self._compute_monthly_lst(gross) if include_lst else Decimal(0)
        net = gross - paye - nssf - lst_monthly

        deducted = f"PAYE {_ugx(paye)}"
        if include_nssf:
            deducted += f" and NSSF {_ugx(nssf)} ({nssf_rate * 100:.0f}%)"
        if include_lst and lst_monthly > 0:
            deducted += f" and LST {_ugx(lst_monthly)}"
        # Naming what was *not* deducted matters as much as the total:
        # a figure labelled "take-home" that silently omits a deduction
        # is read as final and reconciled against a payslip that differs.
        caveat = (
            "Local Service Tax and any voluntary deductions are not included."
            if include_nssf and not include_lst
            else (
                "Includes PAYE, 5% employee NSSF, and monthly Local Service Tax."
                if include_nssf and include_lst
                else (
                    "This is net of PAYE only — NSSF (5%), Local Service Tax and any "
                    "voluntary deductions still come off. Ask again with NSSF included "
                    "for a closer take-home figure."
                )
            )
        )

        payload: dict[str, Any] = {
            "monthly_gross": to_float(gross),
            "chargeable_monthly": to_float(chargeable_verified),
            "residency": residency,
            "is_secondary_employment": is_secondary_employment,
            "has_disability_exemption": has_disability_exemption,
            "paye": to_float(paye),
            "paye_verified": to_float(paye),
            "nssf_included": include_nssf,
            "nssf_employee": to_float(nssf),
            "nssf_employer": to_float(nssf_employer),
            "total_employer_cost": to_float(gross + nssf_employer),
            "lst_included": include_lst,
            "lst_monthly": to_float(lst_monthly),
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
                f"On a chargeable monthly income of {_ugx(chargeable_verified)} "
                f"(gross {_ugx(gross)} plus verified benefits {_ugx(verified_bik)}), "
                f"the applicable band is {marginal_rate * 100:.0f}% above {_ugx(lower)}. "
                f"After {deducted}, net pay = {_ugx(net)}. {caveat}"
                if verified_bik > 0
                else (
                    f"On a gross of {_ugx(gross)}, the applicable band is "
                    f"{marginal_rate * 100:.0f}% above {_ugx(lower)}. "
                    f"After {deducted}, net pay = {_ugx(net)}. {caveat}"
                )
            ),
        }

        if benefits is not None:
            payload["verified_benefits_monthly"] = to_float(verified_bik)
            payload["unverified_benefits_monthly"] = to_float(unverified_bik)
            if unverified_kinds:
                paye_cond, _ = apply_bands(chargeable_conditional, bands)
                payload["paye_conditional"] = to_float(paye_cond)
                payload["annual_paye_conditional"] = to_float(paye_cond * 12)
                payload["chargeable_monthly_conditional"] = to_float(chargeable_conditional)
                payload["unverified_benefits"] = unverified_kinds
                payload["conditional_label"] = "conditional on unverified rule"
            else:
                payload["paye_conditional"] = to_float(paye)

        if compare_residency:
            other_res = "non_resident" if residency == "resident" else "resident"
            other_key = f"paye_bands_{other_res}"
            other_bands = _require_bands(table, other_key)
            other_paye, _ = apply_bands(chargeable_verified, other_bands)
            if other_key not in keys:
                keys.append(other_key)
            diff = abs(other_paye - paye)
            non_res_minus_res = (other_paye - paye) if residency == "resident" else (paye - other_paye)
            payload["other_residency"] = other_res
            payload["other_residency_paye"] = to_float(other_paye)
            payload["residency_paye_difference"] = to_float(diff)
            payload["comparison"] = {
                "other_residency": other_res,
                "other_paye": to_float(other_paye),
                "monthly_difference": to_float(diff),
                "non_resident_minus_resident_monthly": to_float(non_res_minus_res),
            }

        return CalcResult(payload, rate_keys=tuple(keys))


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
                    },
                    "is_non_resident_branch": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Whether the entity is a Ugandan branch of a non-resident company "
                            "subject to 15% branch profit repatriation tax under Income Tax Act s.82."
                        ),
                    },
                    "repatriated_income": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Optional repatriated branch income in UGX (defaults to after-tax income "
                            "when is_non_resident_branch=true)."
                        ),
                    },
                },
                ["chargeable_income"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        chargeable_income: Any,
        is_non_resident_branch: bool = False,
        repatriated_income: Any = None,
        **_: Any,
    ) -> CalcResult:
        income = to_decimal(chargeable_income, field="chargeable_income")
        rate = _require_scalar(table, "corporation_tax")
        tax = income * rate
        after_tax = income - tax
        keys = ["corporation_tax"]
        repat_tax = Decimal(0)
        repat_rate = Decimal(0)
        if is_non_resident_branch:
            repat_rate = _require_scalar(table, "branch_repatriation_tax_rate")
            keys.append("branch_repatriation_tax_rate")
            repat_base = (
                to_decimal(repatriated_income, field="repatriated_income")
                if repatriated_income is not None
                else after_tax
            )
            repat_tax = repat_base * repat_rate
        total_tax = tax + repat_tax
        return CalcResult(
            {
                "chargeable_income": to_float(income),
                "rate": float(rate),
                "tax": to_float(tax),
                "branch_repatriation_tax_rate": float(repat_rate),
                "branch_repatriation_tax": to_float(repat_tax),
                "total_tax": to_float(total_tax),
                "after_tax": to_float(after_tax - repat_tax),
                "explanation": (
                    f"{rate * 100:.0f}% corporation tax on chargeable income of "
                    f"{_ugx(income)} = {_ugx(tax)}"
                    + (
                        f", plus {repat_rate * 100:.0f}% branch repatriation tax ({_ugx(repat_tax)}) = {_ugx(total_tax)} total."
                        if is_non_resident_branch
                        else "."
                    )
                ),
            },
            rate_keys=tuple(keys),
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
                    "rate": float(rate),
                    "tax": 0.0,
                    "net_proceeds": to_float(sale),
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
                        "enum": [
                            "general",
                            "raw_materials",
                            "intermediate",
                            "sensitive",
                            "used_clothing",
                            "used_vehicle_5_to_8_years",
                            "used_vehicle_over_8_years",
                        ],
                        "description": (
                            "Goods category under EAC CET & excise schedules: "
                            "'general' (25% finished goods), 'raw_materials' (0%), "
                            "'intermediate' (10%), 'sensitive' (35%), "
                            "'used_clothing' (30% environmental levy), "
                            "'used_vehicle_5_to_8_years' (35% environmental levy), "
                            "'used_vehicle_over_8_years' (50% environmental levy)."
                        ),
                        "default": "general",
                    },
                    "include_wht": {
                        "type": "boolean",
                        "description": "Whether to include 6% commercial import withholding tax (s.119). Default false.",
                        "default": False,
                    },
                    "include_infrastructure_levy": {
                        "type": "boolean",
                        "description": "Whether to include 1.5% infrastructure development levy on CIF. Default false.",
                        "default": False,
                    },
                    "vehicle_age_years": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Optional age of imported motor vehicle in years: 5-8 years applies 35% environmental levy, "
                            "9-15 years applies 50% environmental levy, >15 years is prohibited."
                        ),
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
        include_wht: bool = False,
        include_infrastructure_levy: bool = False,
        vehicle_age_years: int | None = None,
        goods_category: str = "general",
        **_: Any,
    ) -> CalcResult:
        valid_cats = (
            "general", "raw_materials", "intermediate", "sensitive",
            "used_clothing", "used_vehicle_5_to_8_years", "used_vehicle_over_8_years"
        )
        if goods_category not in valid_cats:
            return CalcResult(
                {
                    "ok": False,
                    "error": (
                        f"goods_category must be one of {valid_cats} "
                        f"(got {goods_category!r})"
                    ),
                }
            )
        import_prohibited = False
        if vehicle_age_years is not None:
            age_int = int(vehicle_age_years)
            if age_int > 15:
                import_prohibited = True
            elif age_int > 8 and goods_category == "general":
                goods_category = "used_vehicle_over_8_years"
            elif 5 <= age_int <= 8 and goods_category == "general":
                goods_category = "used_vehicle_5_to_8_years"

        cif = to_decimal(cif_value, field="cif_value")
        keys: list[str] = []

        # Determine duty rate
        if duty_rate is not None:
            rate = to_rate(duty_rate, field="duty_rate")
        elif goods_category == "raw_materials":
            rate = Decimal("0.0")
        elif goods_category == "intermediate":
            rate = Decimal("0.10")
        elif goods_category == "sensitive":
            rate = Decimal("0.35")
        else:
            rate = _require_scalar(table, "customs_duty_common")
            keys.append("customs_duty_common")

        duty = cif * rate

        # Environmental levy
        levy = Decimal(0)
        levy_rate = Decimal(0)
        if goods_category == "used_clothing":
            levy_rate = _require_scalar(table, "environmental_levy_used_clothing")
            levy = cif * levy_rate
            keys.append("environmental_levy_used_clothing")
        elif goods_category == "used_vehicle_5_to_8_years":
            levy_rate = (
                _require_scalar(table, "environmental_levy_used_vehicles_5_to_8_years")
                if table.get("environmental_levy_used_vehicles_5_to_8_years") is not None
                else Decimal("0.35")
            )
            levy = cif * levy_rate
            if table.get("environmental_levy_used_vehicles_5_to_8_years") is not None:
                keys.append("environmental_levy_used_vehicles_5_to_8_years")
        elif goods_category == "used_vehicle_over_8_years":
            levy_rate = (
                _require_scalar(table, "environmental_levy_used_vehicles_over_8_years")
                if table.get("environmental_levy_used_vehicles_over_8_years") is not None
                else Decimal("0.50")
            )
            levy = cif * levy_rate
            if table.get("environmental_levy_used_vehicles_over_8_years") is not None:
                keys.append("environmental_levy_used_vehicles_over_8_years")

        # Infrastructure Development Levy (1.5% on CIF)
        infra_rate = Decimal(0)
        infra_levy = Decimal(0)
        if include_infrastructure_levy:
            infra_rate = (
                _require_scalar(table, "customs_infrastructure_levy")
                if table.get("customs_infrastructure_levy") is not None
                else Decimal("0.015")
            )
            infra_levy = cif * infra_rate
            if table.get("customs_infrastructure_levy") is not None:
                keys.append("customs_infrastructure_levy")

        # VAT compounding: VAT = (CIF + Duty + Levy) * 18%
        vat_rate = Decimal(0)
        vat = Decimal(0)
        if include_vat:
            vat_rate = _require_scalar(table, "vat_standard")
            vat = (cif + duty + levy) * vat_rate
            keys.append("vat_standard")

        # Commercial Import WHT (6%)
        wht = Decimal(0)
        if include_wht:
            wht = cif * Decimal("0.06")
            if table.get("customs_import_wht") is not None:
                keys.append("customs_import_wht")

        total = cif + duty + levy + infra_levy + vat + wht
        parts = [f"CIF {_ugx(cif)} + duty {rate * 100:.0f}% ({_ugx(duty)})"]
        if levy > 0:
            parts.append(f" + environmental levy {levy_rate * 100:.0f}% ({_ugx(levy)})")
        if infra_levy > 0:
            parts.append(f" + infrastructure levy {infra_rate * 100:.1f}% ({_ugx(infra_levy)})")
        if include_vat:
            parts.append(f" + {vat_rate * 100:.0f}% VAT ({_ugx(vat)})")
        if include_wht:
            parts.append(f" + 6% import WHT ({_ugx(wht)})")

        return CalcResult(
            {
                "cif_value": to_float(cif),
                "duty_rate": float(rate),
                "duty": to_float(duty),
                "goods_category": goods_category,
                "vehicle_age_years": vehicle_age_years,
                "import_prohibited_over_15_years": import_prohibited,
                "environmental_levy_rate": float(levy_rate),
                "environmental_levy": to_float(levy),
                "infrastructure_levy_included": include_infrastructure_levy,
                "infrastructure_levy_rate": float(infra_rate),
                "infrastructure_levy": to_float(infra_levy),
                "vat_included": include_vat,
                "vat": to_float(vat),
                "wht_included": include_wht,
                "wht": to_float(wht),
                "landed_cost": to_float(total),
                "explanation": (
                    "".join(parts) + f" = {_ugx(total)} landed cost. "
                    "Note: binding duty rate is the EAC CET tariff line for the "
                    "goods' HS code. Vehicles over 15 years are prohibited under the Traffic Act."
                ),
            },
            rate_keys=tuple(keys) if keys else ("customs_duty_common",),
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
        "insurance_agent_commission": "withholding_insurance_agent_commission",
        "payment_service_provider_commission": "withholding_payment_service_provider_commission",
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


class ExciseDutyCalculator(CalculatorTool):
    """Compute statutory excise duty for mobile money, data, airtime, fuel, cement, sugar, cooking oil/fat, and motorcycles."""

    _RATE_KEYS: dict[str, str] = {
        "mobile_money_withdrawal": "excise_duty_mobile_money_withdrawal",
        "telecom_data": "excise_duty_telecom_data",
        "telecom_voice": "excise_duty_telecom_voice",
        "beer_malt": "excise_duty_beer_malt",
        "fuel_petrol": "excise_duty_fuel_petrol_per_litre",
        "fuel_diesel": "excise_duty_fuel_diesel_per_litre",
        "fuel_kerosene": "excise_duty_fuel_kerosene_per_litre",
        "cement_50kg": "excise_duty_cement_per_50kg",
        "sugar_kg": "excise_duty_sugar_per_kg",
        "cooking_oil_litre": "excise_duty_cooking_oil_per_litre",
        "cooking_fat_kg": "excise_duty_cooking_fat_per_kg",
        "motorcycle_first_registration": "excise_duty_motorcycle_first_registration_ugx",
    }

    _SPECIFIC_UNITS: dict[str, tuple[str, str]] = {
        "fuel_petrol": ("litres of Petrol", "litre"),
        "fuel_diesel": ("litres of Diesel", "litre"),
        "fuel_kerosene": ("litres of Kerosene", "litre"),
        "cement_50kg": ("50 kg bags of Cement", "50 kg bag"),
        "sugar_kg": ("kg of Sugar", "kg"),
        "cooking_oil_litre": ("litres of Cooking Oil", "litre"),
        "cooking_fat_kg": ("kg of Cooking Fat", "kg"),
        "motorcycle_first_registration": ("Motorcycle(s) at First Registration", "motorcycle"),
    }

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_excise_duty",
            description=(
                "Calculate Uganda statutory excise duty under the Excise Duty Act 2014 (as amended through FY2026-27): "
                "0.5% on mobile money cash withdrawals; 12% on internet data and voice calls; "
                "60% on malt beer; UGX 1,750/L on petrol; UGX 1,430/L on diesel; UGX 200/L on kerosene; "
                "UGX 750 per 50 kg bag on cement; UGX 200/kg on sugar; UGX 400/L on cooking oil; "
                "UGX 500/kg on cooking fat; UGX 500,000 per motorcycle at first registration."
            ),
            parameters=_schema_params(
                {
                    "excise_type": {
                        "type": "string",
                        "enum": sorted(self._RATE_KEYS),
                        "description": "Excisable product or transaction type.",
                    },
                    "amount": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Transaction value in UGX, or quantity in litres/kg/50kg-bags/units for specific-rate items.",
                    },
                },
                ["excise_type", "amount"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(self, table: RateTable, excise_type: str, amount: Any, **_: Any) -> CalcResult:
        rate_key = self._RATE_KEYS.get(excise_type)
        if rate_key is None:
            return CalcResult(
                {
                    "ok": False,
                    "error": (
                        f"Unknown excise_type '{excise_type}'. "
                        f"Choose one of: {', '.join(sorted(self._RATE_KEYS))}"
                    ),
                }
            )
        base = to_decimal(amount, field="amount")
        rate = _require_scalar(table, rate_key)

        is_specific = excise_type in self._SPECIFIC_UNITS
        if is_specific:
            excise_tax = base * rate
            unit_plural, unit_singular = self._SPECIFIC_UNITS[excise_type]
            explanation = (
                f"Excise duty on {base:,.0f} {unit_plural} at "
                f"UGX {rate:,.0f}/{unit_singular} is {_ugx(excise_tax)}."
            )
        else:
            excise_tax = base * rate
            label = excise_type.replace("_", " ")
            explanation = (
                f"Excise duty on {label} of {_ugx(base)} at {rate * 100:.1f}% is {_ugx(excise_tax)}."
            )
            if excise_type == "mobile_money_withdrawal":
                explanation += " Note: 0.5% applies to cash withdrawals only; deposits and transfers are exempt."

        return CalcResult(
            {
                "excise_type": excise_type,
                "amount": to_float(base),
                "rate": float(rate),
                "excise_duty": to_float(excise_tax),
                "is_specific_rate": is_specific,
                "explanation": explanation,
            },
            rate_keys=(rate_key,),
        )


# ---------------------------------------------------------------------------
# Phase F.4: Residency, Benefit in Kind, FX Conversion, and Per-Head Rule Tools
# ---------------------------------------------------------------------------
class ResidencyDeterminationTool(CalculatorTool):
    """Determine individual tax residency under the Income Tax Act (183-day, 122-day average, permanent home, government posting)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="determine_residency",
            description=(
                "Determine Ugandan individual tax residency status from physical presence days "
                "(183 days in any 12-month period commencing or ending in the year of income), "
                "3-year average presence (122 days), permanent home in Uganda, or Uganda "
                "Government employee posted abroad. Never treats contract_months alone as "
                "physical presence; contract_months only yields likely_resident=true."
            ),
            parameters=_schema_params(
                {
                    "days_in_uganda": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "Physical presence days in Uganda in the relevant 12-month window.",
                    },
                    "contract_months": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "Contract duration in months (not physical presence; only indicates likely_resident).",
                    },
                    "permanent_home": {
                        "type": ["boolean", "null"],
                        "description": "Whether the individual has a permanent home in Uganda.",
                    },
                    "avg_days_current_and_two_prior": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "description": "Average days present in Uganda across the current and two preceding years of income.",
                    },
                    "gov_employee_posted_abroad": {
                        "type": ["boolean", "null"],
                        "description": "Whether the individual is an employee or official of the Government of Uganda posted abroad.",
                    },
                    "period_start": {
                        "type": ["string", "null"],
                        "description": "Optional start date (YYYY-MM-DD) of presence in Uganda.",
                    },
                    "period_end": {
                        "type": ["string", "null"],
                        "description": "Optional end date (YYYY-MM-DD) of presence in Uganda.",
                    },
                },
                [],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        days_in_uganda: Any = None,
        contract_months: Any = None,
        permanent_home: bool | None = None,
        permanent_home_in_uganda: bool | None = None,
        avg_days_current_and_two_prior: Any = None,
        gov_employee_posted_abroad: bool | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        **_: Any,
    ) -> CalcResult:
        days_thresh = int(_require_scalar(table, "residency_days_threshold"))
        avg_thresh = int(_require_scalar(table, "residency_avg_days_threshold"))
        yoi_end = str(_require_rule(table, "year_of_income_end"))

        perm_home = permanent_home if permanent_home is not None else permanent_home_in_uganda
        days_val = int(to_decimal(days_in_uganda, field="days_in_uganda")) if days_in_uganda is not None else None
        contract_val = int(to_decimal(contract_months, field="contract_months")) if contract_months is not None else None
        avg_val = (
            float(to_decimal(avg_days_current_and_two_prior, field="avg_days_current_and_two_prior"))
            if avg_days_current_and_two_prior is not None
            else None
        )

        tests = {
            "permanent_home": {
                "result": "pass" if perm_home is True else ("fail" if perm_home is False else "unknown"),
                "threshold": True,
            },
            "presence_183_days": {
                "result": (
                    "pass"
                    if days_val is not None and days_val >= days_thresh
                    else ("fail" if days_val is not None else "unknown")
                ),
                "threshold": days_thresh,
                "actual_days": days_val,
            },
            "three_year_average_122_days": {
                "result": (
                    "pass"
                    if avg_val is not None and avg_val >= avg_thresh
                    else ("fail" if avg_val is not None else "unknown")
                ),
                "threshold": avg_thresh,
                "actual_avg_days": avg_val,
            },
            "gov_employee_posted_abroad": {
                "result": (
                    "pass"
                    if gov_employee_posted_abroad is True
                    else ("fail" if gov_employee_posted_abroad is False else "unknown")
                ),
                "threshold": True,
            },
        }

        missing_facts: list[str] = []
        likely_non_resident = False
        if any(t["result"] == "pass" for t in tests.values()):
            status = "resident"
        elif all(t["result"] == "fail" for t in tests.values()):
            status = "non_resident"
            likely_non_resident = True
        elif days_val is not None and days_val < days_thresh:
            status = "undetermined"
            likely_non_resident = True
            if perm_home is None:
                missing_facts.append("whether they have a permanent home in Uganda")
            if avg_val is None:
                missing_facts.append(f"3-year average presence days ({avg_thresh}-day test)")
            if gov_employee_posted_abroad is None:
                missing_facts.append("whether they are a Government of Uganda employee posted abroad")
        else:
            status = "undetermined"
            missing_facts.append("days physically present in Uganda")

        estimated_contract_days = (contract_val * 30) if contract_val is not None else None
        likely_resident = (
            status == "resident"
            or (
                status == "undetermined"
                and not likely_non_resident
                and estimated_contract_days is not None
                and estimated_contract_days >= days_thresh
            )
        )

        years_affected: list[str] = [table.fiscal_year]
        if period_start and period_end:
            try:
                ps = _dt.date.fromisoformat(str(period_start))
                pe = _dt.date.fromisoformat(str(period_end))
                end_month, end_day = (int(x) for x in yoi_end.split("-"))
                fy_start = (
                    f"FY{ps.year - 1}-{str(ps.year)[-2:]}"
                    if (ps.month, ps.day) <= (end_month, end_day)
                    else f"FY{ps.year}-{str(ps.year + 1)[-2:]}"
                )
                fy_end = (
                    f"FY{pe.year - 1}-{str(pe.year)[-2:]}"
                    if (pe.month, pe.day) <= (end_month, end_day)
                    else f"FY{pe.year}-{str(pe.year + 1)[-2:]}"
                )
                years_affected = [fy_start] if fy_start == fy_end else [fy_start, fy_end]
            except ValueError:
                pass

        if status == "resident":
            explanation = (
                f"Tax resident in Uganda: meets statutory residency criteria "
                f"({days_thresh}-day physical presence test in any 12-month period commencing "
                f"or ending in the year of income, {avg_thresh}-day 3-year average, permanent home, "
                f"or Government employee posted abroad)."
            )
        elif status == "non_resident":
            explanation = (
                f"Non-resident in Uganda: physical presence of {days_val} days is below the "
                f"{days_thresh}-day statutory threshold and all secondary residency tests failed."
            )
        elif likely_non_resident:
            explanation = (
                f"Physical presence of {days_val} days is below the {days_thresh}-day statutory "
                f"threshold (likely non-resident unless they have a permanent home in Uganda, "
                f"meet the {avg_thresh}-day 3-year average test, or are a Uganda Government employee posted abroad)."
            )
        elif likely_resident:
            explanation = (
                f"Residency is undetermined from contract length alone ({contract_val}-month contract "
                f"≈ {estimated_contract_days} days), but LIKELY RESIDENT if physically present in Uganda "
                f"for {days_thresh} days or more in any 12-month period commencing or ending in the year "
                f"of income (ending {yoi_end}). Confirm actual days physically present in Uganda."
            )
        else:
            explanation = (
                f"Residency is undetermined: confirm actual days physically present in Uganda "
                f"(threshold is {days_thresh} days in any 12-month period commencing or ending in the year of income)."
            )

        return CalcResult(
            {
                "status": status,
                "likely_resident": likely_resident,
                "likely_non_resident": likely_non_resident,
                "days_in_uganda": days_val,
                "contract_months": contract_val,
                "residency_days_threshold": days_thresh,
                "residency_avg_days_threshold": avg_thresh,
                "year_of_income_end": yoi_end,
                "tests": tests,
                "missing_facts": missing_facts,
                "years_of_income_affected": years_affected,
                "explanation": explanation,
            },
            rate_keys=("residency_days_threshold", "residency_avg_days_threshold", "year_of_income_end"),
        )


class BenefitInKindValuationTool(CalculatorTool):
    """Compute taxable value of employment benefits in kind per URA Taxation Handbook §12.2.3."""

    _VALID_KINDS = (
        "car",
        "housing",
        "domestic_staff",
        "meals",
        "utilities",
        "low_interest_loan",
        "loan",
        "debt_waiver",
        "property_or_services",
        "furniture",
        "other",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="value_benefit_in_kind",
            description=(
                "Calculate the taxable value of an employment benefit in kind (BIK) in Uganda "
                "(car/motor vehicle, housing/accommodation, domestic_staff, meals, utilities, "
                "low_interest_loan, debt_waiver, property_or_services/furniture, other). "
                "Benefits in kind are ADDED to employment income for PAYE, and non-cash benefits "
                "under UGX 10,000/month are excluded."
            ),
            parameters=_schema_params(
                {
                    "kind": {
                        "type": "string",
                        "enum": list(self._VALID_KINDS),
                        "description": "Category of benefit in kind.",
                    },
                    "market_value": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Market value in UGX (for car: market value when first provided; "
                            "for property/furniture/other: market value; for housing: monthly or annual market rent)."
                        ),
                    },
                    "market_rent": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Market rent of accommodation in UGX (monthly by default unless rent_period='annual').",
                    },
                    "rent_period": {
                        "type": "string",
                        "enum": ["monthly", "annual"],
                        "default": "monthly",
                        "description": "Whether market_rent / employee_payment for housing is monthly or annual.",
                    },
                    "employment_income_annual": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Annual employment income in UGX (before housing benefit) for the 15% housing formula.",
                    },
                    "monthly_gross": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Monthly employment income in UGX (multiplied by 12 if employment_income_annual is omitted).",
                    },
                    "monthly_cost": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Monthly cost to employer in UGX (for domestic_staff, meals, utilities, housing).",
                    },
                    "annual_cost": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Annual cost to employer in UGX.",
                    },
                    "years_since_first_provided": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Number of subsequent years after the car was first provided (35% reducing-balance depreciation per year).",
                    },
                    "days_private_use": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Days in the year of income the vehicle was available for private use (B).",
                    },
                    "days_in_year": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Total days in the year of income (C, default 365).",
                    },
                    "employee_payment": {
                        "type": "number",
                        "minimum": 0,
                        "default": 0,
                        "description": "Amount paid by the employee towards the benefit (D).",
                    },
                    "loan_principal": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Loan amount in UGX (for low_interest_loan).",
                    },
                    "interest_rate_paid": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "default": 0,
                        "description": "Annual interest rate paid by the employee as a decimal fraction (e.g. 0.03 for 3%).",
                    },
                },
                ["kind"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        kind: str,
        market_value: Any = None,
        market_rent: Any = None,
        rent_period: str = "monthly",
        employment_income_annual: Any = None,
        monthly_gross: Any = None,
        monthly_cost: Any = None,
        annual_cost: Any = None,
        years_since_first_provided: int = 0,
        days_private_use: int | None = None,
        days_in_year: int | None = None,
        employee_payment: Any = 0,
        loan_principal: Any = None,
        interest_rate_paid: Any = 0,
        **_: Any,
    ) -> CalcResult:
        if kind not in self._VALID_KINDS:
            return CalcResult(
                {"ok": False, "error": f"kind must be one of {self._VALID_KINDS} (got {kind!r})"}
            )
        de_minimis_monthly = _require_scalar(table, "bik_de_minimis_monthly_ugx")
        emp_pay = to_decimal(employee_payment or 0, field="employee_payment")
        keys: list[str] = ["bik_de_minimis_monthly_ugx"]
        verified = True
        formula_notes = ""

        if kind == "car":
            if market_value is None:
                return CalcResult(
                    {
                        "ok": False,
                        "needs": "market_value",
                        "error": "market_value (when first provided) is required to value a motor vehicle benefit.",
                    }
                )
            mv = to_decimal(market_value, field="market_value")
            inc_rate = _require_scalar(table, "bik_motor_vehicle_inclusion_rate")
            dep_rate = _require_scalar(table, "bik_motor_vehicle_depreciation_rate")
            std_days = int(_require_scalar(table, "bik_days_in_year_standard"))
            keys.extend(
                [
                    "bik_motor_vehicle_inclusion_rate",
                    "bik_motor_vehicle_depreciation_rate",
                    "bik_days_in_year_standard",
                ]
            )
            yrs = max(0, int(years_since_first_provided or 0))
            c_days = int(days_in_year) if days_in_year is not None else std_days
            b_days = int(days_private_use) if days_private_use is not None else c_days
            if c_days <= 0:
                return CalcResult({"ok": False, "error": "days_in_year must be positive"})
            depreciated_a = mv * ((Decimal(1) - dep_rate) ** yrs)
            raw_annual = max(
                Decimal(0),
                (inc_rate * depreciated_a * Decimal(b_days) / Decimal(c_days)) - emp_pay,
            )
            raw_monthly = raw_annual / Decimal(12)
            formula_notes = (
                f"(20% x A x B/C) - D where A={_ugx(depreciated_a)} "
                f"(initial {_ugx(mv)} depreciated at {dep_rate * 100:.0f}% p.a. for {yrs} yr(s)), "
                f"B={b_days}, C={c_days}, D={_ugx(emp_pay)}"
            )

        elif kind == "housing":
            rent_raw = market_rent if market_rent is not None else (market_value if market_value is not None else monthly_cost)
            if rent_raw is None and annual_cost is None:
                return CalcResult(
                    {
                        "ok": False,
                        "needs": "market_rent",
                        "error": "market_rent (or monthly_cost/annual_cost) is required to value a housing benefit.",
                    }
                )
            housing_rate = _require_scalar(table, "bik_housing_employment_income_rate")
            keys.append("bik_housing_employment_income_rate")
            verified = "bik_housing_employment_income_rate" not in table.unverified

            if annual_cost is not None and rent_raw is None:
                annual_rent = to_decimal(annual_cost, field="annual_cost")
                annual_emp_pay = emp_pay
            elif rent_period == "annual":
                annual_rent = to_decimal(rent_raw, field="market_rent")
                annual_emp_pay = emp_pay
            else:
                annual_rent = to_decimal(rent_raw, field="market_rent") * Decimal(12)
                annual_emp_pay = emp_pay * Decimal(12)

            part_a = max(Decimal(0), annual_rent - annual_emp_pay)
            if employment_income_annual is not None:
                emp_inc_ann = to_decimal(employment_income_annual, field="employment_income_annual")
            elif monthly_gross is not None:
                emp_inc_ann = to_decimal(monthly_gross, field="monthly_gross") * Decimal(12)
            else:
                emp_inc_ann = None

            if emp_inc_ann is not None:
                # Exact handbook wording: "15% of the employment income, including the amount
                # referred to in paragraph (a), paid by the employer to the employee for the year of income"
                part_b = housing_rate * (emp_inc_ann + part_a)
                raw_annual = min(part_a, part_b)
                formula_notes = (
                    f"lesser of (a) annual market rent less employee payment = {_ugx(part_a)} "
                    f"and (b) {housing_rate * 100:.0f}% of (employment income {_ugx(emp_inc_ann)} + (a) {_ugx(part_a)}) = {_ugx(part_b)}"
                )
            else:
                return CalcResult(
                    {
                        "ok": False,
                        "needs": "employment_income_annual",
                        "error": (
                            "employment_income_annual (or monthly_gross) is required: the housing "
                            "benefit is the lesser of market rent less payment and "
                            f"{housing_rate * 100:.0f}% of employment income."
                        ),
                    },
                    rate_keys=tuple(keys),
                )
            raw_monthly = raw_annual / Decimal(12)

        elif kind in ("low_interest_loan", "loan"):
            principal_raw = loan_principal if loan_principal is not None else market_value
            if principal_raw is None:
                return CalcResult(
                    {
                        "ok": False,
                        "needs": "loan_principal",
                        "error": "loan_principal is required to value a low-interest loan benefit.",
                    }
                )
            principal = to_decimal(principal_raw, field="loan_principal")
            loan_thresh = _require_scalar(table, "bik_loan_principal_threshold_ugx")
            keys.append("bik_loan_principal_threshold_ugx")
            if principal <= loan_thresh:
                raw_annual = Decimal(0)
                raw_monthly = Decimal(0)
                verified = True
                formula_notes = (
                    f"Loan principal {_ugx(principal)} does not exceed the {_ugx(loan_thresh)} "
                    "statutory threshold, so the taxable benefit is nil."
                )
            else:
                stat_rate = _require_scalar(table, "bik_loan_statutory_interest_rate")
                keys.append("bik_loan_statutory_interest_rate")
                verified = "bik_loan_statutory_interest_rate" not in table.unverified
                paid_rate = to_rate(interest_rate_paid or 0, field="interest_rate_paid")
                rate_diff = max(Decimal(0), stat_rate - paid_rate)
                raw_annual = principal * rate_diff
                raw_monthly = raw_annual / Decimal(12)
                formula_notes = (
                    f"Loan {_ugx(principal)} > {_ugx(loan_thresh)}: "
                    f"({stat_rate * 100:.1f}% statutory rate - {paid_rate * 100:.1f}% paid) x {_ugx(principal)}"
                )

        elif kind == "debt_waiver":
            waived_raw = (
                market_value
                if market_value is not None
                else (annual_cost if annual_cost is not None else loan_principal)
            )
            if waived_raw is None:
                return CalcResult(
                    {"ok": False, "needs": "market_value", "error": "Amount of debt waived is required."}
                )
            raw_annual = to_decimal(waived_raw, field="market_value")
            raw_monthly = raw_annual / Decimal(12)
            formula_notes = f"Debt waiver: full amount waived ({_ugx(raw_annual)}) is taxable."

        else:
            # domestic_staff, meals, utilities, property_or_services, furniture, other
            if monthly_cost is not None:
                m_cost = to_decimal(monthly_cost, field="monthly_cost")
                raw_monthly = max(Decimal(0), m_cost - emp_pay)
                raw_annual = raw_monthly * Decimal(12)
            elif annual_cost is not None:
                a_cost = to_decimal(annual_cost, field="annual_cost")
                raw_annual = max(Decimal(0), a_cost - emp_pay)
                raw_monthly = raw_annual / Decimal(12)
            elif market_value is not None:
                mv = to_decimal(market_value, field="market_value")
                raw_annual = max(Decimal(0), mv - emp_pay)
                raw_monthly = raw_annual / Decimal(12)
            else:
                return CalcResult(
                    {
                        "ok": False,
                        "needs": "monthly_cost",
                        "error": f"cost or market_value is required to value '{kind}' benefit.",
                    }
                )
            formula_notes = f"Cost/market value less employee payment = {_ugx(raw_monthly)}/month ({_ugx(raw_annual)}/year)."

        de_minimis_excluded = Decimal(0) < raw_monthly < de_minimis_monthly
        taxable_monthly = Decimal(0) if de_minimis_excluded else raw_monthly
        taxable_annual = Decimal(0) if de_minimis_excluded else raw_annual

        return CalcResult(
            {
                "kind": kind,
                "annual_value": to_float(taxable_annual),
                "monthly_value": to_float(taxable_monthly),
                "raw_annual_value": to_float(raw_annual),
                "raw_monthly_value": to_float(raw_monthly),
                "de_minimis_threshold_monthly": to_float(de_minimis_monthly),
                "de_minimis_excluded": de_minimis_excluded,
                "verified": verified,
                "direction": "added_to_employment_income",
                "explanation": (
                    f"Benefit in kind ({kind}): {formula_notes} -> {_ugx(taxable_monthly)}/month "
                    f"({_ugx(taxable_annual)}/year), ADDED to employment income for PAYE."
                    + (
                        f" Excluded under the {_ugx(de_minimis_monthly)}/month de-minimis relief."
                        if de_minimis_excluded
                        else ""
                    )
                    + (
                        " [Conditional: statutory rule/rate is marked unverified in the rate table.]"
                        if not verified
                        else ""
                    )
                ),
            },
            rate_keys=tuple(keys),
        )


class CurrencyConversionTool(CalculatorTool):
    """Convert foreign-currency amounts into UGX using user-supplied rate or BoU mid-rate snapshot."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="convert_currency",
            description=(
                "Convert a foreign currency amount (USD, EUR, GBP, KES) into UGX for Ugandan tax "
                "calculations. Uses a user-supplied exchange_rate when provided, or checks the "
                "Bank of Uganda mid-rate snapshot (BOU_mid_rates_2026.json). Never guesses an "
                "exchange rate: returns ok=false, needs='exchange_rate' if no rate is available."
            ),
            parameters=_schema_params(
                {
                    "amount": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Monetary amount in the source currency.",
                    },
                    "currency": {
                        "type": "string",
                        "description": "ISO currency code (e.g. 'USD', 'EUR', 'GBP', 'KES', 'UGX').",
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD or YYYY-MM) for the exchange rate lookup.",
                    },
                    "exchange_rate": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Optional user-supplied exchange rate (UGX per 1 unit of foreign currency). Always takes precedence.",
                    },
                    "use_live_fx": {
                        "type": "boolean",
                        "default": False,
                        "description": "When true (or when FLAG_LIVE_WEB_RESEARCH is enabled), fetch live mid-market rate from XE.com / Google Currency.",
                    },
                },
                ["amount", "currency"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        amount: Any,
        currency: str,
        date: str | None = None,
        exchange_rate: Any = None,
        use_live_fx: bool = False,
        **_: Any,
    ) -> CalcResult:
        """Convert foreign currency into UGX using user rate, live XE/Google FX, or BoU snapshot."""
        amt = to_decimal(amount, field="amount")
        curr = (currency or "UGX").strip().upper()
        _require_rule(table, "fx_conversion_rule")

        if curr == "UGX":
            return CalcResult(
                {
                    "amount": to_float(amt),
                    "currency": "UGX",
                    "ugx_amount": to_float(amt),
                    "amount_ugx": to_float(amt),
                    "exchange_rate": 1.0,
                    "rate_used": 1.0,
                    "rate_source": "identity",
                    "is_live_rate": False,
                    "needs_exchange_rate": False,
                    "verified": True,
                    "explanation": f"{_ugx(amt)} is already denominated in UGX.",
                },
                rate_keys=("fx_conversion_rule",),
            )

        if exchange_rate is not None:
            rate_dec = to_decimal(exchange_rate, field="exchange_rate")
            if rate_dec <= 0:
                return CalcResult({"ok": False, "error": "exchange_rate must be positive"})
            ugx_val = amt * rate_dec
            return CalcResult(
                {
                    "amount": to_float(amt),
                    "currency": curr,
                    "ugx_amount": to_float(ugx_val),
                    "amount_ugx": to_float(ugx_val),
                    "exchange_rate": to_float(rate_dec),
                    "rate_used": to_float(rate_dec),
                    "rate_source": "user_supplied",
                    "is_live_rate": False,
                    "needs_exchange_rate": False,
                    "verified": True,
                    "explanation": (
                        f"Converted {curr} {float(amt):,.2f} at user-supplied exchange rate "
                        f"UGX {float(rate_dec):,.2f}/{curr} = {_ugx(ugx_val)}."
                    ),
                },
                rate_keys=("fx_conversion_rule",),
            )

        from datetime import datetime, timezone
        from ..flags import flags

        today_month = datetime.now(timezone.utc).strftime("%Y-%m")
        date_is_current = not date or str(date).strip()[:7] == today_month
        if date_is_current and (use_live_fx or flags.is_enabled("live_web_research")):
            from ..web_fx import lookup_live_exchange_rate

            quote = lookup_live_exchange_rate(
                curr,
                date=date,
                allow_offline_fallback=False,
            )
            if quote is not None and quote.rate_ugx > 0:
                rate_dec = Decimal(str(quote.rate_ugx))
                ugx_val = amt * rate_dec
                fetched_disp = (quote.fetched_at_utc or "")[:16].replace("T", " ")
                return CalcResult(
                    {
                        "amount": to_float(amt),
                        "currency": curr,
                        "ugx_amount": to_float(ugx_val),
                        "amount_ugx": to_float(ugx_val),
                        "exchange_rate": to_float(rate_dec),
                        "rate_used": to_float(rate_dec),
                        "rate_source": quote.source,
                        "fetched_at_utc": quote.fetched_at_utc,
                        "is_live_rate": quote.is_live,
                        "needs_exchange_rate": False,
                        "verified": False,
                        "explanation": (
                            f"Converted {curr} {float(amt):,.2f} to {_ugx(ugx_val)} using live exchange rate "
                            f"1 {curr} = UGX {float(rate_dec):,.2f} (Source: {quote.source}, fetched {fetched_disp} UTC). "
                            "Note: for official URA return filing, apply the prevailing rate on the payment date."
                        ),
                    },
                    rate_keys=("fx_conversion_rule",),
                )

        # Check BOU_mid_rates_2026.json snapshot
        import json as _json
        from ..tax.tables import DATA_DIR

        fx_path = DATA_DIR / "fx" / "BOU_mid_rates_2026.json"
        snap_rate: Decimal | None = None
        if fx_path.exists():
            try:
                fx_data = _json.loads(fx_path.read_text(encoding="utf-8"))
                rates_map = (fx_data.get("rates") or {}).get(curr)
                if isinstance(rates_map, int | float):
                    snap_rate = Decimal(str(rates_map))
                elif isinstance(rates_map, dict) and rates_map:
                    month_key = (date or "")[:7]
                    val = rates_map.get(month_key) or rates_map.get("default")
                    if val is not None:
                        snap_rate = Decimal(str(val))
            except Exception:
                snap_rate = None

        if snap_rate is not None and snap_rate > 0:
            ugx_val = amt * snap_rate
            return CalcResult(
                {
                    "amount": to_float(amt),
                    "currency": curr,
                    "ugx_amount": to_float(ugx_val),
                    "amount_ugx": to_float(ugx_val),
                    "rate_used": to_float(snap_rate),
                    "rate_source": "bou_snapshot",
                    "verified": False,
                    "explanation": (
                        f"Converted {curr} {float(amt):,.2f} using BoU mid-rate snapshot "
                        f"UGX {float(snap_rate):,.2f}/{curr} = {_ugx(ugx_val)}."
                    ),
                },
                rate_keys=("fx_conversion_rule",),
            )

        return CalcResult(
            {
                "ok": False,
                "needs": "exchange_rate",
                "amount": to_float(amt),
                "currency": curr,
                "error": (
                    f"No official Bank of Uganda mid-rate is loaded for {curr}; "
                    f"please provide the exchange_rate (UGX per 1 {curr}) on the payment date."
                ),
                "explanation": (
                    f"Cannot convert {curr} {float(amt):,.2f} to UGX without an exchange rate: "
                    f"please state the Bank of Uganda mid-exchange rate (UGX per 1 {curr}) on the payment date."
                ),
            },
            rate_keys=("fx_conversion_rule",),
        )


class ObjectionRequirementsTool(CalculatorTool):
    """Statutory objection deadline (45 days), objection decision window (90 days), TAT appeal window (30 days), and 30% disputed tax deposit."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="objection_requirements",
            description=(
                "Compute statutory tax objection and Tax Appeals Tribunal (TAT) requirements in Uganda: "
                "45-day objection deadline from service of assessment, 90-day Commissioner General decision "
                "timeline, 30-day TAT appeal window, and mandatory 30% deposit of disputed tax (or undisputed "
                "tax if higher) under Section 15 of the Tax Appeals Tribunals Act."
            ),
            parameters=_schema_params(
                {
                    "disputed_tax_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "default": 0,
                        "description": "Total tax assessed / in dispute in UGX.",
                    },
                    "undisputed_tax_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "default": 0,
                        "description": "Portion of assessed tax not in dispute in UGX.",
                    },
                    "days_since_assessment": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional days elapsed since the notice of assessment was served.",
                    },
                },
                [],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        disputed_tax_ugx: Any = 0,
        undisputed_tax_ugx: Any = 0,
        days_since_assessment: int | None = None,
        **_: Any,
    ) -> CalcResult:
        disputed = to_decimal(disputed_tax_ugx or 0, field="disputed_tax_ugx")
        undisputed = to_decimal(undisputed_tax_ugx or 0, field="undisputed_tax_ugx")
        obj_days = int(_require_scalar(table, "objection_timeline_days"))
        dec_days = int(_require_scalar(table, "objection_decision_timeline_days"))
        tat_days = int(_require_scalar(table, "tax_appeals_tribunal_deadline_days"))
        dep_rate = _require_scalar(table, "tax_appeals_tribunal_deposit_rate")

        thirty_pct = disputed * dep_rate
        deposit = max(thirty_pct, undisputed)
        within_window = (
            (int(days_since_assessment) <= obj_days)
            if days_since_assessment is not None
            else None
        )

        return CalcResult(
            {
                "disputed_tax_ugx": to_float(disputed),
                "undisputed_tax_ugx": to_float(undisputed),
                "deposit_rate": float(dep_rate),
                "deposit_amount": to_float(deposit),
                "objection_deadline_days": obj_days,
                "objection_decision_days": dec_days,
                "tat_deadline_days": tat_days,
                "within_objection_window": within_window,
                "explanation": (
                    f"An objection must be lodged with the Commissioner General within {obj_days} days "
                    f"of service of the assessment notice (decision due within {dec_days} days). "
                    f"An appeal to the Tax Appeals Tribunal (TAT) must be filed within {tat_days} days "
                    f"and requires a mandatory deposit of {dep_rate * 100:.0f}% of the tax assessed "
                    f"({_ugx(thirty_pct)}) or the undisputed tax ({_ugx(undisputed)}), whichever is greater "
                    f"= {_ugx(deposit)}."
                ),
            },
            rate_keys=(
                "objection_timeline_days",
                "objection_decision_timeline_days",
                "tax_appeals_tribunal_deadline_days",
                "tax_appeals_tribunal_deposit_rate",
            ),
        )


class LateFilingPenaltyTool(CalculatorTool):
    """Compute statutory penal tax for late filing of a return under TPC Act s.49(1) and late payment interest under s.39."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="penalty_late_filing",
            description=(
                "Calculate Ugandan penal tax for failing to file a tax return by the due date "
                "(Tax Procedures Code Act s.49(1)): greater of UGX 200,000 or 2% of the tax payable "
                "per month (or part of a month) the return remains unfiled, plus 2% per month "
                "late payment interest (s.39)."
            ),
            parameters=_schema_params(
                {
                    "tax_payable_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Net tax payable on the return in UGX.",
                    },
                    "months_late": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                        "description": "Number of months (or part-months) the return is filed late.",
                    },
                },
                ["tax_payable_ugx"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        tax_payable_ugx: Any,
        months_late: int = 1,
        **_: Any,
    ) -> CalcResult:
        tax_payable = to_decimal(tax_payable_ugx, field="tax_payable_ugx")
        months = max(1, int(months_late or 1))
        monthly_rate = _require_scalar(table, "penal_tax_late_filing_monthly_rate")
        min_ugx = _require_scalar(table, "penal_tax_late_filing_minimum_ugx")
        interest_rate = _require_scalar(table, "late_payment_interest_monthly_rate")

        pct_penalty = tax_payable * monthly_rate * Decimal(months)
        penal_tax = max(min_ugx, pct_penalty)
        monthly_interest = tax_payable * interest_rate * Decimal(months)
        in_duplum_cap = tax_payable + penal_tax
        capped_interest = min(monthly_interest, in_duplum_cap)
        is_capped = bool(monthly_interest > in_duplum_cap and tax_payable > 0)

        return CalcResult(
            {
                "tax_payable_ugx": to_float(tax_payable),
                "months_late": months,
                "monthly_penalty_rate": float(monthly_rate),
                "minimum_penalty_ugx": to_float(min_ugx),
                "percentage_penalty_ugx": to_float(pct_penalty),
                "penal_tax": to_float(penal_tax),
                "late_payment_interest_monthly_rate": float(interest_rate),
                "late_payment_interest_ugx": to_float(monthly_interest),
                "capped_interest_ugx": to_float(capped_interest),
                "interest_capped_under_s39_5": is_capped,
                "total_liability_ugx": to_float(tax_payable + penal_tax + capped_interest),
                "explanation": (
                    f"Late filing penal tax under TPC Act s.49(1) is the greater of {_ugx(min_ugx)} "
                    f"or {monthly_rate * 100:.0f}% of tax payable per month ({_ugx(pct_penalty)} for "
                    f"{months} month(s)) = {_ugx(penal_tax)}. Late payment interest under s.39 runs at "
                    f"{interest_rate * 100:.0f}% per month (capped under s.39(5) at principal + penal tax = {_ugx(capped_interest)})."
                ),
            },
            rate_keys=(
                "penal_tax_late_filing_monthly_rate",
                "penal_tax_late_filing_minimum_ugx",
                "late_payment_interest_monthly_rate",
            ),
        )


class VATInputClaimEligibilityTool(CalculatorTool):
    """Evaluate VAT input tax credit eligibility (EFRIS e-invoice requirement, imported services reverse charge, mixed supplies apportionment)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="vat_input_claim_eligibility",
            description=(
                "Check whether a VAT-registered taxpayer in Uganda can claim an input VAT credit "
                "and compute the claimable amount: requires a valid EFRIS e-invoice/e-receipt for "
                "domestic purchases, disallows input credit on reverse-charge imported services, "
                "and apportions input VAT for mixed taxable/exempt supplies."
            ),
            parameters=_schema_params(
                {
                    "amount_net_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "default": 0,
                        "description": "Purchase amount net of VAT in UGX.",
                    },
                    "has_efris_einvoice": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether the purchase is supported by a valid EFRIS e-invoice/e-receipt.",
                    },
                    "is_imported_service": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether the VAT arises under the reverse charge on imported services.",
                    },
                    "taxable_supplies_ratio": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "default": 1.0,
                        "description": "Ratio of taxable supplies to total supplies (0..1) for mixed-supply apportionment.",
                    },
                },
                [],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        amount_net_ugx: Any = 0,
        has_efris_einvoice: bool = True,
        is_imported_service: bool = False,
        taxable_supplies_ratio: Any = 1.0,
        **_: Any,
    ) -> CalcResult:
        net = to_decimal(amount_net_ugx or 0, field="amount_net_ugx")
        vat_rate = _require_scalar(table, "vat_standard")
        efris_req = int(_require_scalar(table, "vat_input_efris_einvoice_required"))
        rev_allowed = int(_require_scalar(table, "vat_imported_services_reverse_charge_input_credit_allowed"))
        ratio = to_rate(taxable_supplies_ratio if taxable_supplies_ratio is not None else 1.0, field="taxable_supplies_ratio")

        vat_amt = net * vat_rate
        keys = [
            "vat_standard",
            "vat_input_efris_einvoice_required",
            "vat_imported_services_reverse_charge_input_credit_allowed",
        ]

        if is_imported_service and rev_allowed == 0:
            eligible = False
            claimable = Decimal(0)
            reason = (
                f"Reverse-charge VAT of {_ugx(vat_amt)} ({vat_rate * 100:.0f}% on {_ugx(net)}) "
                "must be accounted for by the recipient on imported services, but is NOT claimable "
                "as an input tax credit under the VAT Act."
            )
        elif efris_req == 1 and not has_efris_einvoice:
            eligible = False
            claimable = Decimal(0)
            reason = (
                f"Input VAT of {_ugx(vat_amt)} ({vat_rate * 100:.0f}% on {_ugx(net)}) is NOT claimable "
                "because a manual paper invoice does not qualify — a valid EFRIS e-invoice/e-receipt "
                "is mandatory to claim input VAT credit."
            )
        else:
            eligible = ratio > 0
            claimable = vat_amt * ratio
            reason = (
                f"Input VAT of {_ugx(vat_amt)} apportioned by taxable supplies ratio ({ratio * 100:.1f}%) "
                f"= {_ugx(claimable)} claimable."
            )

        return CalcResult(
            {
                "amount_net_ugx": to_float(net),
                "vat_rate": float(vat_rate),
                "vat_amount": to_float(vat_amt),
                "eligible": eligible,
                "claimable_input_vat": to_float(claimable),
                "has_efris_einvoice": has_efris_einvoice,
                "is_imported_service": is_imported_service,
                "taxable_supplies_ratio": float(ratio),
                "explanation": reason,
            },
            rate_keys=tuple(keys),
        )


class WHTNonResidentServiceTool(CalculatorTool):
    """Compute 15% WHT on non-resident Ugandan-sourced service contracts and enforce the employment-contract exclusion."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="wht_non_resident_service",
            description=(
                "Calculate 15% withholding tax on payments to a non-resident for a Ugandan-sourced "
                "service contract (Income Tax Act s.85), while excluding employment contracts "
                "('A Ugandan-sourced service contract does not include an employment contract', "
                "which is taxed under PAYE)."
            ),
            parameters=_schema_params(
                {
                    "amount_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Gross payment amount in UGX.",
                    },
                    "is_employment_contract": {
                        "type": "boolean",
                        "default": False,
                        "description": "True if the payment is salary/remuneration under an employment contract.",
                    },
                },
                ["amount_ugx"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        amount_ugx: Any,
        is_employment_contract: bool = False,
        **_: Any,
    ) -> CalcResult:
        amt = to_decimal(amount_ugx, field="amount_ugx")
        rate = _require_scalar(table, "withholding_non_resident_service_contract")
        if is_employment_contract:
            return CalcResult(
                {
                    "amount_ugx": to_float(amt),
                    "is_employment_contract": True,
                    "wht_applicable": False,
                    "rate": 0.0,
                    "withholding_tax": 0.0,
                    "net_payable": to_float(amt),
                    "tax_head_applicable": "PAYE",
                    "explanation": (
                        "A Ugandan-sourced service contract does NOT include an employment contract "
                        "(Withholding-Tax-FY-2024-25-1.pdf). Employment remuneration is taxed under "
                        "PAYE, not 15% non-resident service WHT."
                    ),
                },
                rate_keys=("withholding_non_resident_service_contract",),
            )
        wht = amt * rate
        return CalcResult(
            {
                "amount_ugx": to_float(amt),
                "is_employment_contract": False,
                "wht_applicable": True,
                "rate": float(rate),
                "withholding_tax": to_float(wht),
                "net_payable": to_float(amt - wht),
                "tax_head_applicable": "WHT",
                "explanation": (
                    f"Non-resident Ugandan-sourced service contract WHT at {rate * 100:.0f}% on "
                    f"{_ugx(amt)} = {_ugx(wht)} (net payable {_ugx(amt - wht)}). Note: employment "
                    "contracts are excluded from service WHT and taxed under PAYE."
                ),
            },
            rate_keys=("withholding_non_resident_service_contract",),
        )


class PresumptiveOrStartupTaxTool(CalculatorTool):
    """Check small-business presumptive tax threshold eligibility and 3-year citizen start-up exemption eligibility."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_presumptive_or_startup_tax",
            description=(
                "Evaluate eligibility for the Ugandan small-business presumptive tax regime "
                "(annual turnover between UGX 10,000,000 and UGX 150,000,000) and the 3-year "
                "citizen start-up income tax exemption (investment capital up to UGX 500,000,000)."
            ),
            parameters=_schema_params(
                {
                    "annual_turnover_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Annual gross business turnover in UGX.",
                    },
                    "investment_capital_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Start-up investment capital in UGX.",
                    },
                    "chargeable_income_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Optional chargeable income in UGX if standard 30% corporate tax applies.",
                    },
                    "is_citizen_startup": {
                        "type": "boolean",
                        "description": "Whether the business is a Ugandan citizen start-up meeting s.21(1)(za) conditions.",
                    },
                    "years_in_operation": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Years since the start-up commenced operations.",
                    },
                    "has_records": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether the small business maintains sales records under Second Schedule (default true).",
                    },
                },
                [],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    @staticmethod
    def _compute_second_schedule_presumptive(turnover: Decimal) -> tuple[Decimal, Decimal, str]:
        """Return (with_records_tax, without_records_tax, band_label) under Income Tax Act Second Schedule."""
        if turnover <= Decimal("10000000"):
            return Decimal(0), Decimal(0), "Up to UGX 10,000,000 (0% — Exempt)"
        if turnover <= Decimal("30000000"):
            with_rec = (turnover - Decimal("10000000")) * Decimal("0.004")
            return with_rec, Decimal("80000"), "UGX 10,000,001 to 30,000,000 (0.4% above 10M with records; UGX 80,000 without records)"
        if turnover <= Decimal("50000000"):
            with_rec = Decimal("80000") + (turnover - Decimal("30000000")) * Decimal("0.005")
            return with_rec, Decimal("200000"), "UGX 30,000,001 to 50,000,000 (UGX 80,000 + 0.5% above 30M with records; UGX 200,000 without records)"
        if turnover <= Decimal("80000000"):
            with_rec = Decimal("180000") + (turnover - Decimal("50000000")) * Decimal("0.006")
            return with_rec, Decimal("400000"), "UGX 50,000,001 to 80,000,000 (UGX 180,000 + 0.6% above 50M with records; UGX 400,000 without records)"
        with_rec = Decimal("360000") + (turnover - Decimal("80000000")) * Decimal("0.007")
        return with_rec, Decimal("900000"), "UGX 80,000,001 to 150,000,000 (UGX 360,000 + 0.7% above 80M with records; UGX 900,000 without records)"

    def compute(
        self,
        table: RateTable,
        annual_turnover_ugx: Any = None,
        investment_capital_ugx: Any = None,
        chargeable_income_ugx: Any = None,
        is_citizen_startup: bool | None = None,
        years_in_operation: int | None = None,
        has_records: bool = True,
        **_: Any,
    ) -> CalcResult:
        lower = _require_scalar(table, "presumptive_tax_lower_threshold")
        upper = _require_scalar(table, "presumptive_tax_upper_threshold")
        cap = _require_scalar(table, "startup_citizen_exemption_capital_cap_ugx")
        years = int(_require_scalar(table, "startup_citizen_exemption_years"))
        corp_rate = _require_scalar(table, "corporation_tax")

        turnover = to_decimal(annual_turnover_ugx, field="annual_turnover_ugx") if annual_turnover_ugx is not None else None
        capital = to_decimal(investment_capital_ugx, field="investment_capital_ugx") if investment_capital_ugx is not None else None
        chargeable = to_decimal(chargeable_income_ugx, field="chargeable_income_ugx") if chargeable_income_ugx is not None else None

        qualifies_presumptive = (
            (lower <= turnover <= upper) if turnover is not None else None
        )
        pres_with_rec: Decimal | None = None
        pres_without_rec: Decimal | None = None
        pres_selected: Decimal | None = None
        pres_band = ""
        if turnover is not None and turnover <= upper:
            pres_with_rec, pres_without_rec, pres_band = self._compute_second_schedule_presumptive(turnover)
            pres_selected = pres_with_rec if has_records else pres_without_rec

        capital_within_cap = (capital <= cap) if capital is not None else None
        if (
            capital_within_cap is False
            or is_citizen_startup is False
            or (years_in_operation is not None and int(years_in_operation) >= years)
        ):
            qualifies_startup: bool | None = False
        elif (
            capital_within_cap is True
            and is_citizen_startup is True
            and years_in_operation is not None
            and int(years_in_operation) < years
        ):
            qualifies_startup = True
        else:
            qualifies_startup = None

        if qualifies_presumptive is True or chargeable is None:
            corp_tax: Decimal | None = None
        elif qualifies_startup is True:
            corp_tax = Decimal(0)
        else:
            corp_tax = chargeable * corp_rate

        pres_note = (
            f" Second Schedule presumptive tax on {_ugx(turnover)} ({pres_band}): "
            f"{_ugx(pres_with_rec)} with records, {_ugx(pres_without_rec)} without records."
            if pres_with_rec is not None and pres_without_rec is not None
            else ""
        )

        return CalcResult(
            {
                "presumptive_lower_threshold": to_float(lower),
                "presumptive_upper_threshold": to_float(upper),
                "qualifies_for_presumptive": qualifies_presumptive,
                "presumptive_band": pres_band,
                "presumptive_tax_with_records": to_float(pres_with_rec) if pres_with_rec is not None else None,
                "presumptive_tax_without_records": to_float(pres_without_rec) if pres_without_rec is not None else None,
                "presumptive_tax": to_float(pres_selected) if pres_selected is not None else None,
                "startup_capital_cap": to_float(cap),
                "startup_exemption_years": years,
                "capital_within_startup_cap": capital_within_cap,
                "qualifies_for_startup_exemption": qualifies_startup,
                "corporation_tax_rate": float(corp_rate),
                "tax": to_float(corp_tax) if corp_tax is not None else None,
                "explanation": (
                    f"Presumptive tax applies to annual turnover between {_ugx(lower)} and {_ugx(upper)}."
                    f"{pres_note} "
                    f"The {years}-year citizen startup exemption applies to investment capital up to {_ugx(cap)}."
                ),
            },
            rate_keys=(
                "presumptive_tax_lower_threshold",
                "presumptive_tax_upper_threshold",
                "startup_citizen_exemption_capital_cap_ugx",
                "startup_citizen_exemption_years",
                "corporation_tax",
            ),
        )


class StampDutyCalculator(CalculatorTool):
    """Compute Ugandan stamp duty on property/land transfers, motor vehicle transfers, and motorcycle transfers."""

    _INSTRUMENTS = ("property_transfer", "motor_vehicle_transfer", "motorcycle_transfer")

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_stamp_duty",
            description=(
                "Calculate Ugandan stamp duty under Schedule 2 of the Stamp Duty Act: "
                "1% ad valorem on property/land transfers (on the Chief Government Valuer market value), "
                "and fixed statutory stamp duty on motor vehicle and motorcycle transfers."
            ),
            parameters=_schema_params(
                {
                    "instrument_type": {
                        "type": "string",
                        "enum": list(self._INSTRUMENTS),
                        "description": "Type of instrument being stamped.",
                    },
                    "value_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Market value of the property/land in UGX (required for property_transfer).",
                    },
                },
                ["instrument_type"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        instrument_type: str,
        value_ugx: Any = None,
        **_: Any,
    ) -> CalcResult:
        if instrument_type not in self._INSTRUMENTS:
            return CalcResult(
                {"ok": False, "error": f"instrument_type must be one of {self._INSTRUMENTS}"}
            )
        if instrument_type == "property_transfer":
            if value_ugx is None:
                return CalcResult(
                    {
                        "ok": False,
                        "needs": "value_ugx",
                        "error": "value_ugx (market value) is required for property_transfer.",
                    }
                )
            val = to_decimal(value_ugx, field="value_ugx")
            rate = _require_scalar(table, "stamp_duty_property_transfer")
            duty = val * rate
            return CalcResult(
                {
                    "instrument_type": instrument_type,
                    "value_ugx": to_float(val),
                    "rate": float(rate),
                    "stamp_duty": to_float(duty),
                    "tax": to_float(duty),
                    "explanation": (
                        f"Stamp duty on property/land transfer at {rate * 100:.1f}% of {_ugx(val)} "
                        f"= {_ugx(duty)}."
                    ),
                },
                rate_keys=("stamp_duty_property_transfer",),
            )
        if instrument_type == "motor_vehicle_transfer":
            fee = _require_scalar(table, "stamp_duty_motor_vehicle_transfer_ugx")
            return CalcResult(
                {
                    "instrument_type": instrument_type,
                    "stamp_duty": to_float(fee),
                    "tax": to_float(fee),
                    "explanation": f"Fixed stamp duty on motor vehicle transfer in {table.fiscal_year} is {_ugx(fee)}.",
                },
                rate_keys=("stamp_duty_motor_vehicle_transfer_ugx",),
            )
        fee = _require_scalar(table, "stamp_duty_motorcycle_transfer_ugx")
        return CalcResult(
            {
                "instrument_type": instrument_type,
                "stamp_duty": to_float(fee),
                "tax": to_float(fee),
                "explanation": f"Fixed stamp duty on motorcycle/tricycle transfer in {table.fiscal_year} is {_ugx(fee)}.",
            },
            rate_keys=("stamp_duty_motorcycle_transfer_ugx",),
        )


class DigitalServicesTaxCalculator(CalculatorTool):
    """Compute 5% Digital Services Tax (DST) on non-resident digital service providers."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_digital_services_tax",
            description=(
                "Calculate Ugandan 5% Digital Services Tax (DST) under Income Tax Act s.86A "
                "on gross revenue derived by a non-resident digital service provider from users in Uganda."
            ),
            parameters=_schema_params(
                {
                    "gross_uganda_revenue_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Gross revenue derived from Ugandan users in UGX.",
                    },
                },
                ["gross_uganda_revenue_ugx"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        gross_uganda_revenue_ugx: Any,
        **_: Any,
    ) -> CalcResult:
        rev = to_decimal(gross_uganda_revenue_ugx, field="gross_uganda_revenue_ugx")
        rate = _require_scalar(table, "digital_services_tax_non_resident")
        dst = rev * rate
        return CalcResult(
            {
                "gross_uganda_revenue_ugx": to_float(rev),
                "rate": float(rate),
                "dst_tax": to_float(dst),
                "tax": to_float(dst),
                "explanation": (
                    f"Non-resident Digital Services Tax at {rate * 100:.0f}% on gross Uganda revenue "
                    f"of {_ugx(rev)} = {_ugx(dst)}."
                ),
            },
            rate_keys=("digital_services_tax_non_resident",),
        )


class GamingAndBettingTaxCalculator(CalculatorTool):
    """Compute operator gaming and betting tax on total stakes less payouts."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_gaming_tax",
            description=(
                "Calculate Ugandan gaming and betting tax on licensed operators "
                "(30% in FY2026-27 on total money staked less payouts under the Lotteries and Gaming Act)."
            ),
            parameters=_schema_params(
                {
                    "total_stakes_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Total money staked in UGX.",
                    },
                    "total_payouts_ugx": {
                        "type": "number",
                        "minimum": 0,
                        "default": 0,
                        "description": "Total winnings paid out to punters in UGX.",
                    },
                },
                ["total_stakes_ugx"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        total_stakes_ugx: Any,
        total_payouts_ugx: Any = 0,
        **_: Any,
    ) -> CalcResult:
        stakes = to_decimal(total_stakes_ugx, field="total_stakes_ugx")
        payouts = to_decimal(total_payouts_ugx or 0, field="total_payouts_ugx")
        rate = _require_scalar(table, "gaming_and_betting_tax_rate")
        net_stakes = max(Decimal(0), stakes - payouts)
        tax = net_stakes * rate
        return CalcResult(
            {
                "total_stakes_ugx": to_float(stakes),
                "total_payouts_ugx": to_float(payouts),
                "net_stakes_ugx": to_float(net_stakes),
                "rate": float(rate),
                "operator_gaming_tax": to_float(tax),
                "tax": to_float(tax),
                "explanation": (
                    f"Operator gaming and betting tax at {rate * 100:.0f}% on net stakes of "
                    f"{_ugx(net_stakes)} (stakes {_ugx(stakes)} minus payouts {_ugx(payouts)}) = {_ugx(tax)}."
                ),
            },
            rate_keys=("gaming_and_betting_tax_rate",),
        )


class AdvanceVehicleTaxCalculator(CalculatorTool):
    """Compute annual commercial motor vehicle advance income tax per seat or per tonne."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calculate_advance_vehicle_tax",
            description=(
                "Calculate annual advance income tax on commercial transport vehicles in Uganda: "
                "UGX 20,000 per passenger seat per year for PSVs, or UGX 50,000 per tonne per year "
                "for goods vehicles."
            ),
            parameters=_schema_params(
                {
                    "vehicle_type": {
                        "type": "string",
                        "enum": ["passenger", "goods"],
                        "description": "Commercial vehicle category ('passenger' or 'goods').",
                    },
                    "seats": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Number of passenger seats (for passenger vehicles).",
                    },
                    "tonnes": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Loading capacity in tonnes (for goods vehicles).",
                    },
                },
                ["vehicle_type"],
            ),
            risk="low",
            namespace=TAX_CALCULATOR_NAMESPACE,
            output_schema=CALCULATOR_OUTPUT_SCHEMA,
        )

    def compute(
        self,
        table: RateTable,
        vehicle_type: str,
        seats: Any = None,
        tonnes: Any = None,
        **_: Any,
    ) -> CalcResult:
        if vehicle_type not in ("passenger", "goods"):
            return CalcResult(
                {"ok": False, "error": f"vehicle_type must be 'passenger' or 'goods' (got {vehicle_type!r})"}
            )
        if vehicle_type == "passenger":
            if seats is None:
                return CalcResult(
                    {
                        "ok": False,
                        "needs": "seats",
                        "error": "seats is required for passenger vehicles.",
                    }
                )
            rate = _require_scalar(table, "motor_vehicle_advance_tax_passenger_per_seat")
            units = to_decimal(seats, field="seats")
            tax = units * rate
            return CalcResult(
                {
                    "vehicle_type": "passenger",
                    "seats": to_float(units),
                    "rate_per_seat": to_float(rate),
                    "annual_advance_tax": to_float(tax),
                    "tax": to_float(tax),
                    "explanation": (
                        f"Annual advance tax for a {float(units):,.0f}-seat passenger vehicle at "
                        f"{_ugx(rate)}/seat = {_ugx(tax)}."
                    ),
                },
                rate_keys=("motor_vehicle_advance_tax_passenger_per_seat",),
            )
        if tonnes is None:
            return CalcResult(
                {
                    "ok": False,
                    "needs": "tonnes",
                    "error": "tonnes is required for goods vehicles.",
                }
            )
        rate = _require_scalar(table, "motor_vehicle_advance_tax_goods_per_tonne")
        units = to_decimal(tonnes, field="tonnes")
        tax = units * rate
        return CalcResult(
            {
                "vehicle_type": "goods",
                "tonnes": to_float(units),
                "rate_per_tonne": to_float(rate),
                "annual_advance_tax": to_float(tax),
                "tax": to_float(tax),
                "explanation": (
                    f"Annual advance tax for a {float(units):,.1f}-tonne goods vehicle at "
                    f"{_ugx(rate)}/tonne = {_ugx(tax)}."
                ),
            },
            rate_keys=("motor_vehicle_advance_tax_goods_per_tonne",),
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
    ExciseDutyCalculator(),
    ResidencyDeterminationTool(),
    BenefitInKindValuationTool(),
    CurrencyConversionTool(),
    ObjectionRequirementsTool(),
    LateFilingPenaltyTool(),
    VATInputClaimEligibilityTool(),
    WHTNonResidentServiceTool(),
    PresumptiveOrStartupTaxTool(),
    StampDutyCalculator(),
    DigitalServicesTaxCalculator(),
    GamingAndBettingTaxCalculator(),
    AdvanceVehicleTaxCalculator(),
)

for _tool in CALCULATOR_TOOLS:
    ToolRegistry.register(_tool)
