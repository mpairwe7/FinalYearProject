"""Static rate lookup — VAT, CIT, WHT, thresholds.

This tool is explicitly **offline**: it returns whatever the FY rate
table holds, nothing more.  A future MCP server (``mcp_rates``) will
pull from Bank of Uganda CBR + URA live endpoints; until then, this
gives the LLM a deterministic source of truth that's versioned by
fiscal year and can be audited when rates change.
"""

from __future__ import annotations

from typing import Any

from ..authority import authority_required, get_authority_status
from . import Tool, ToolRegistry, ToolSchema
from .calculators import _RATE_TABLES, _get_rates  # noqa: F401

_DISPLAY_NAMES: dict[str, str] = {
    "vat_standard": "VAT (standard rate)",
    "corporation_tax": "Corporation tax",
    "capital_gains_corporate": "Capital gains tax (corporate)",
    "rental_tax_individual": "Rental tax (individual)",
    "rental_tax_company": "Rental tax (company)",
    "withholding_services": "WHT on services",
    "withholding_goods": "WHT on goods",
    "withholding_management_fees": "WHT on management fees",
    "withholding_dividend": "WHT on dividends",
    "customs_duty_common": "Customs duty (common finished goods)",
}


def _authority_payload() -> tuple[bool, dict[str, Any]]:
    status = get_authority_status()
    if authority_required() and not status.get("ok"):
        return False, status
    return True, status


class LookupRateTool(Tool):
    """Return the numeric rate for a specific Ugandan tax."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="lookup_rate",
            description=(
                "Return the current numeric rate for a specific "
                "Ugandan tax (VAT, corporation tax, WHT, etc). "
                "Use this whenever the user asks 'what is the rate "
                "for X' — never guess rates, always call this tool."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tax_type": {
                        "type": "string",
                        "enum": sorted(_DISPLAY_NAMES.keys()),
                        "description": (
                            "Which tax to look up. 'vat_standard' for "
                            "VAT, 'corporation_tax' for CIT, "
                            "'withholding_services' / "
                            "'withholding_goods' for WHT, etc."
                        ),
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": "FY identifier, e.g. 'FY2025-26'.",
                    },
                },
                "required": ["tax_type"],
            },
            risk="low",
        )

    def execute(
        self,
        tax_type: str,
        fiscal_year: str = "FY2025-26",
    ) -> dict[str, Any]:
        authority_ok, authority = _authority_payload()
        if not authority_ok:
            return {
                "ok": False,
                "error": "fresh authority manifest required before returning tax rates",
                "authority": authority,
            }
        rates = _get_rates(fiscal_year)
        if tax_type not in rates:
            return {
                "ok": False,
                "error": f"Unknown tax_type '{tax_type}'",
                "available": sorted(_DISPLAY_NAMES.keys()),
            }
        rate = rates[tax_type]
        # Guard against non-numeric entries (e.g. PAYE bands)
        if not isinstance(rate, int | float):
            return {
                "ok": False,
                "error": f"'{tax_type}' is not a simple scalar rate — "
                "use a more specific tool (e.g. calculate_paye).",
            }
        return {
            "ok": True,
            "tax_type": tax_type,
            "display_name": _DISPLAY_NAMES.get(tax_type, tax_type),
            "rate": float(rate),
            "rate_pct": round(float(rate) * 100, 2),
            "fiscal_year": fiscal_year,
            "authority": authority,
        }


class ListAvailableRatesTool(Tool):
    """Return all known tax rates in one call."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="list_available_rates",
            description=(
                "Return every tax rate currently known to the bot "
                "(VAT, CIT, WHT, rental, customs duty). Useful when "
                "the user asks for a summary or overview."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fiscal_year": {
                        "type": "string",
                        "description": "FY identifier.",
                    },
                },
                "required": [],
            },
            risk="low",
        )

    def execute(self, fiscal_year: str = "FY2025-26") -> dict[str, Any]:
        authority_ok, authority = _authority_payload()
        if not authority_ok:
            return {
                "ok": False,
                "error": "fresh authority manifest required before returning tax rates",
                "authority": authority,
            }
        rates = _get_rates(fiscal_year)
        rows: list[dict[str, Any]] = []
        for key, display in _DISPLAY_NAMES.items():
            val = rates.get(key)
            if isinstance(val, int | float):
                rows.append(
                    {
                        "tax_type": key,
                        "display_name": display,
                        "rate": float(val),
                        "rate_pct": round(float(val) * 100, 2),
                    }
                )
        return {
            "ok": True,
            "fiscal_year": fiscal_year,
            "count": len(rows),
            "rates": rows,
            "authority": authority,
        }


ToolRegistry.register(LookupRateTool())
ToolRegistry.register(ListAvailableRatesTool())
