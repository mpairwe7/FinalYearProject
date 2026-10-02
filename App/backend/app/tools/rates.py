"""Rate lookup over the versioned, effective-dated URA rate tables.

This tool is explicitly **offline**: it returns whatever
:mod:`app.tax.tables` holds for the requested fiscal year, together with
the statutory basis and sources for that figure.  It never guesses, and
a rate a given year's law does not define is an error rather than a
fallback to an adjacent year.

The lookups are additionally gated on :mod:`app.authority`, so a
production deployment whose authority manifest has gone stale stops
quoting rates instead of serving figures nobody can vouch for.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..authority import authority_required, get_authority_status
from ..tax.tables import (
    RateTable,
    RateTableError,
    fiscal_years_defining,
    get_table,
    known_rate_keys,
    list_fiscal_years,
)
from . import Tool, ToolRegistry, ToolSchema

#: Human labels for the scalar rates.  Keys absent here are humanised
#: from the key itself, so adding a rate to a data file needs no edit
#: to this module.
_DISPLAY_NAMES: dict[str, str] = {
    "vat_standard": "VAT (standard rate)",
    "vat_registration_threshold_annual": "VAT registration threshold (annual turnover)",
    "nssf_employee_contribution": "NSSF employee contribution (social security, not a tax)",
    "corporation_tax": "Corporation tax",
    "capital_gains_corporate": "Capital gains tax (corporate)",
    "rental_tax_individual": "Rental tax (individual)",
    "rental_tax_individual_threshold": "Rental tax threshold (individual, annual)",
    "rental_tax_company": "Rental tax (company)",
    "rental_company_expense_cap": "Rental expense cap (company)",
    "withholding_services": "WHT on services",
    "withholding_goods": "WHT on goods",
    "withholding_management_fees": "WHT on management fees",
    "withholding_dividend": "WHT on dividends",
    "withholding_royalty": "WHT on royalties",
    "withholding_public_entertainer": "WHT on payments to public entertainers",
    "withholding_betting_winnings": "WHT on betting winnings",
    "withholding_foreign_interest": "WHT on debenture interest to non-resident lenders",
    "withholding_telecom_commission": "WHT on telecom / mobile-money commissions",
    "withholding_non_business_asset": "WHT on purchase of a non-business asset",
    "customs_duty_common": "Customs duty (common finished goods - 25%)",
    "customs_duty_raw_materials": "Customs duty (raw materials & capital goods - 0%)",
    "customs_duty_intermediate": "Customs duty (intermediate goods - 10%)",
    "customs_duty_sensitive_goods": "Customs duty (sensitive / protected goods - 35%)",
    "customs_import_wht": "Withholding tax on commercial imports (6%)",
    "customs_infrastructure_levy": "Infrastructure development levy on imports (1.5%)",
    "environmental_levy_used_clothing": "Environmental levy (used clothing imports - 30%)",
    "environmental_levy_used_vehicles_5_to_8_years": "Environmental levy (vehicles 5 to 8 years - 35%)",
    "environmental_levy_used_vehicles_over_8_years": "Environmental levy (vehicles 8 to 15 years - 50%)",
    "excise_duty_mobile_money_withdrawal": "Excise duty on mobile money cash withdrawals (0.5%)",
    "excise_duty_telecom_data": "Excise duty on internet data (12%)",
    "excise_duty_telecom_voice": "Excise duty on airtime and voice calls (12%)",
    "excise_duty_beer_malt": "Excise duty on malt beer (60%)",
    "excise_duty_fuel_petrol_per_litre": "Excise duty on petrol (UGX per litre)",
    "excise_duty_fuel_diesel_per_litre": "Excise duty on diesel (UGX per litre)",
    "excise_duty_fuel_kerosene_per_litre": "Excise duty on kerosene (UGX per litre)",
    "excise_due_date_monthly": "Excise duty monthly return due date (day of month)",
    "presumptive_tax_lower_threshold": "Presumptive tax threshold (lower turnover bound, annual)",
    "presumptive_tax_upper_threshold": "Presumptive tax threshold (upper turnover bound, annual)",
    "penal_tax_late_filing_monthly_rate": "Penal tax for late filing (monthly rate)",
    "penal_tax_late_filing_minimum_ugx": "Penal tax for late filing (minimum amount)",
    "stamp_duty_property_transfer": "Stamp duty on transfer of property",
    "objection_timeline_days": "Objection notice statutory timeline (days)",
    "paye_due_date_monthly": "PAYE monthly filing due date (day of month)",
    "passenger_baggage_allowance_usd": "Passenger baggage duty-free allowance (USD)",
}

#: Keys that are money thresholds rather than rates — reporting these as
#: a percentage would turn UGX 300,000,000 into "30,000,000,000%".
_AMOUNT_KEYS = frozenset(
    {
        "vat_registration_threshold_annual",
        "rental_tax_individual_threshold",
        "presumptive_tax_lower_threshold",
        "presumptive_tax_upper_threshold",
        "penal_tax_late_filing_minimum_ugx",
        "objection_timeline_days",
        "paye_due_date_monthly",
        "passenger_baggage_allowance_usd",
        "excise_duty_fuel_petrol_per_litre",
        "excise_duty_fuel_diesel_per_litre",
        "excise_duty_fuel_kerosene_per_litre",
        "excise_due_date_monthly",
    }
)

#: MCP server that owns the rate lookups.
RATES_NAMESPACE = "rates"

_PERIOD_PARAMS: dict[str, Any] = {
    "fiscal_year": {
        "type": "string",
        "description": "URA fiscal year identifier, e.g. 'FY2026-27'. Omit for the year in force today.",
    },
    "as_of": {
        "type": "string",
        "format": "date",
        "description": "ISO date the question applies to. Ignored when fiscal_year is given.",
    },
}


def display_name(key: str) -> str:
    return _DISPLAY_NAMES.get(key, key.replace("_", " ").capitalize())


def _authority_payload() -> tuple[bool, dict[str, Any]]:
    status = get_authority_status()
    if authority_required() and not status.get("ok"):
        return False, status
    return True, status


def _scalar_row(key: str, value: float) -> dict[str, Any]:
    """One rate/threshold as an output row, formatted for what it is."""
    val = float(value)
    is_amount = key in _AMOUNT_KEYS or val > 1.0
    row: dict[str, Any] = {
        "tax_type": key,
        "display_name": display_name(key),
        "value": val,
        "kind": "amount" if is_amount else "rate",
    }
    if is_amount:
        if key.endswith("_days") or key.endswith("_hours") or key.endswith("_hierarchy"):
            row["formatted"] = f"{val:,.0f}"
        else:
            row["formatted"] = f"UGX {val:,.0f}"
    else:
        row["rate"] = val
        row["rate_pct"] = round(val * 100, 2)
        row["formatted"] = f"{val * 100:g}%"
    return row


def _resolve(fiscal_year: str | None, as_of: str | None) -> RateTable:
    day = _dt.date.fromisoformat(as_of) if as_of else None
    return get_table(fiscal_year, as_of=day)


def _known_scalar_keys() -> list[str]:
    """Every scalar key defined by any loaded fiscal year.

    Reads the tables' structure rather than calling :func:`get_table`,
    because this runs while the tool's schema is being built at import
    time: gating it on confirmed rates made a deployment holding a
    provisional table fail to start instead of failing the one lookup.
    """
    return known_rate_keys()


class LookupRateTool(Tool):
    """Return the numeric rate for a specific Ugandan tax."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="lookup_rate",
            description=(
                "Return the current numeric rate or threshold for a specific "
                "Ugandan tax (VAT, corporation tax, WHT, registration thresholds, "
                "levies). Use this whenever the user asks 'what is the rate for X' "
                "— never guess rates, always call this tool. Rates are "
                "effective-dated, so pass fiscal_year or as_of when the question "
                "is about a past period."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tax_type": {
                        "type": "string",
                        "enum": _known_scalar_keys(),
                        "description": (
                            "Which figure to look up. 'vat_standard' for VAT, "
                            "'corporation_tax' for CIT, 'withholding_services' / "
                            "'withholding_goods' for WHT, etc."
                        ),
                    },
                    **_PERIOD_PARAMS,
                },
                "required": ["tax_type"],
                "additionalProperties": False,
            },
            risk="low",
            namespace=RATES_NAMESPACE,
        )

    def execute(
        self,
        tax_type: str,
        fiscal_year: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        authority_ok, authority = _authority_payload()
        if not authority_ok:
            return {
                "ok": False,
                "error": "fresh authority manifest required before returning tax rates",
                "authority": authority,
            }
        try:
            table = _resolve(fiscal_year, as_of)
        except (RateTableError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "known_fiscal_years": list_fiscal_years()}

        value = table.get(tax_type)
        if value is None:
            # Distinguish "no such tax" from "not defined in *this* year":
            # the second is a real answer (the rate exists, just not for the
            # period asked about) and should name the years that do define it.
            defined_in = fiscal_years_defining(tax_type)
            error = (
                f"'{tax_type}' is not defined for {table.fiscal_year}; "
                f"it applies to: {', '.join(defined_in)}"
                if defined_in
                else f"Unknown tax_type '{tax_type}'"
            )
            return {
                "ok": False,
                "error": error,
                "fiscal_year": table.fiscal_year,
                "defined_in": defined_in,
                "available": _known_scalar_keys(),
            }
        if not isinstance(value, int | float) or isinstance(value, bool):
            return {
                "ok": False,
                "error": (
                    f"'{tax_type}' is not a simple scalar rate — "
                    "use a more specific tool (e.g. calculate_paye)."
                ),
            }

        row = _scalar_row(tax_type, value)
        name = display_name(tax_type)
        fy_text = f" for fiscal year {table.fiscal_year}" if table.fiscal_year else ""
        explanation = f"The official {name} rate{fy_text} is {row['formatted']}."
        return {
            "ok": True,
            **row,
            "explanation": explanation,
            "fiscal_year": table.fiscal_year,
            "rate_basis": table.provenance(tax_type),
            "authority": authority,
        }


class ListAvailableRatesTool(Tool):
    """Return all known tax rates for one fiscal year in a single call."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="list_available_rates",
            description=(
                "Return every tax rate and threshold known for a fiscal year "
                "(VAT, CIT, WHT, rental, customs duty, levies). Useful when "
                "the user asks for a summary or overview."
            ),
            parameters={
                "type": "object",
                "properties": dict(_PERIOD_PARAMS),
                "required": [],
                "additionalProperties": False,
            },
            risk="low",
            namespace=RATES_NAMESPACE,
        )

    def execute(
        self,
        fiscal_year: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        authority_ok, authority = _authority_payload()
        if not authority_ok:
            return {
                "ok": False,
                "error": "fresh authority manifest required before returning tax rates",
                "authority": authority,
            }
        try:
            table = _resolve(fiscal_year, as_of)
        except (RateTableError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "known_fiscal_years": list_fiscal_years()}

        rows = [
            _scalar_row(key, value)
            for key, value in sorted(table.rates.items())
            if isinstance(value, int | float) and not isinstance(value, bool)
        ]
        top_rates_str = "\n".join(f"- **{r['display_name']}**: {r['formatted']}" for r in rows[:8])
        is_confirmed = table.confirmed() if callable(table.confirmed) else bool(table.confirmed)
        verification_statement = (
            "All rates are verified under current statutory instruments."
            if is_confirmed
            else "These rates are provisional; verify them with URA before use."
        )
        explanation = (
            f"Official URA statutory rate schedule for {table.fiscal_year} ({len(rows)} rates recorded):\n\n"
            f"{top_rates_str}\n\n"
            f"{verification_statement}"
        )
        return {
            "ok": True,
            "fiscal_year": table.fiscal_year,
            "count": len(rows),
            "rates": rows,
            "explanation": explanation,
            "rate_basis": table.provenance(*(r["tax_type"] for r in rows)),
            "authority": authority,
        }


class CompareFiscalYearsTool(Tool):
    """Diff two fiscal years' rate tables."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="compare_tax_years",
            description=(
                "Compare two URA fiscal years and report what changed: rates that "
                "moved, thresholds that were revised, and taxes newly introduced or "
                "withdrawn. Use when the user asks 'what changed this year', 'what's "
                "new in the budget', or 'is the rate different from last year'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "from_fiscal_year": {
                        "type": "string",
                        "enum": list_fiscal_years(),
                        "description": "The earlier fiscal year, e.g. 'FY2025-26'.",
                    },
                    "to_fiscal_year": {
                        "type": "string",
                        "enum": list_fiscal_years(),
                        "description": "The later fiscal year, e.g. 'FY2026-27'.",
                    },
                },
                "required": ["from_fiscal_year", "to_fiscal_year"],
                "additionalProperties": False,
            },
            risk="low",
            namespace=RATES_NAMESPACE,
        )

    def execute(self, from_fiscal_year: str, to_fiscal_year: str) -> dict[str, Any]:
        authority_ok, authority = _authority_payload()
        if not authority_ok:
            return {
                "ok": False,
                "error": "fresh authority manifest required before returning tax rates",
                "authority": authority,
            }
        try:
            older = get_table(from_fiscal_year)
            newer = get_table(to_fiscal_year)
        except RateTableError as exc:
            return {"ok": False, "error": str(exc), "known_fiscal_years": list_fiscal_years()}

        changed: list[dict[str, Any]] = []
        introduced: list[dict[str, Any]] = []
        withdrawn: list[dict[str, Any]] = []
        for key in sorted(set(older.rates) | set(newer.rates)):
            before, after = older.get(key), newer.get(key)
            if before == after:
                continue
            entry = {
                "tax_type": key,
                "display_name": display_name(key),
                "before": before,
                "after": after,
                "basis": newer.legal_basis.get(key, ""),
            }
            if before is None:
                introduced.append(entry)
            elif after is None:
                withdrawn.append(entry)
            else:
                changed.append(entry)

        lines = [f"Comparison of URA statutory rates from {older.fiscal_year} to {newer.fiscal_year}:"]
        if changed:
            lines.append(f"\n**Changed rates ({len(changed)}):**")
            for c in changed:
                lines.append(f"- **{c['display_name']}**: revised from {c['before']} to {c['after']}")
        if introduced:
            lines.append(f"\n**Newly introduced taxes ({len(introduced)}):**")
            for i in introduced:
                lines.append(f"- **{i['display_name']}**: {i['after']}")
        if withdrawn:
            lines.append(f"\n**Withdrawn taxes ({len(withdrawn)}):**")
            for w in withdrawn:
                lines.append(f"- **{w['display_name']}**")
        if not changed and not introduced and not withdrawn:
            lines.append("\nNo rate changes recorded between these fiscal years.")
        explanation = "\n".join(lines)

        return {
            "ok": True,
            "from_fiscal_year": older.fiscal_year,
            "to_fiscal_year": newer.fiscal_year,
            "changed": changed,
            "introduced": introduced,
            "withdrawn": withdrawn,
            "change_count": len(changed) + len(introduced) + len(withdrawn),
            "explanation": explanation,
            "rate_basis": newer.provenance(*(e["tax_type"] for e in changed + introduced)),
            "authority": authority,
        }


ToolRegistry.register(LookupRateTool())
ToolRegistry.register(ListAvailableRatesTool())
ToolRegistry.register(CompareFiscalYearsTool())
