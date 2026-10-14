"""``mcp_tax_calculator`` — the tax calculators as a standalone MCP server.

Speaks the **2026-07-28** specification, which is stateless: there is no
``initialize``/``initialized`` handshake and no session header, so every
request is self-describing and any request may land on any replica
behind a plain round-robin load balancer.

The tools themselves are the ones already registered in
:mod:`app.tools.calculators` — this module is transport, not arithmetic,
so an answer cannot differ between the in-process and remote paths.  The
wire rules it shares with every other server here are in :mod:`..wire`.
"""

from __future__ import annotations

import json
from typing import Any

from ....tools import ToolRegistry
from ..wire import (  # noqa: F401 - part of this module's public surface
    HEADER_MISMATCH,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    LIST_CACHE_SCOPE,
    LIST_TTL_MS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    UNSUPPORTED_PROTOCOL_VERSION,
    StatelessMCPServer,
)

SERVER_NAME = "mcp_tax_calculator"
SERVER_VERSION = "2.0.0"
NAMESPACE = "tax_calculator"

_RATES_URI = "ura://rates/current"
_DEADLINES_URI = "ura://calendar/deadlines"


def _resources() -> list[dict[str, Any]]:
    """Static/reference resources exposed by this server."""
    return [
        {
            "uri": _RATES_URI,
            "name": "URA Statutory Tax Rates",
            "description": "Official FY2026-27 statutory tax rate table (VAT, PAYE, WHT, Corporation Tax)",
            "mimeType": "application/json",
        },
        {
            "uri": _DEADLINES_URI,
            "name": "URA Tax Deadlines Calendar",
            "description": "Upcoming statutory filing, withholding, and return deadlines calendar",
            "mimeType": "application/json",
        },
    ]


def _read_resource(uri: str) -> dict[str, Any]:
    """Read contents of an exposed statutory tax resource."""
    if uri == _RATES_URI:
        from ....tax.tables import get_table

        table = get_table()
        payload: Any = {
            "fiscal_year": table.fiscal_year,
            "status": table.status,
            "rates": table.rates,
            "currency": "UGX",
        }
    elif uri == _DEADLINES_URI:
        payload = ToolRegistry.call("get_next_deadlines", {})
    else:
        raise LookupError(uri)
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(payload, default=str),
            }
        ]
    }


def _prompts() -> list[dict[str, Any]]:
    """Standard prompt templates provided by this server."""
    return [
        {
            "name": "vat_calculation_guide",
            "description": "Guidance template for calculating and verifying standard 18% Value Added Tax in Uganda.",
            "arguments": [
                {
                    "name": "amount",
                    "description": "Transaction amount in UGX",
                    "required": True,
                }
            ],
        },
        {
            "name": "paye_withholding_guide",
            "description": "Guidance on PAYE income tax brackets and employer withholding obligations.",
            "arguments": [
                {
                    "name": "monthly_salary",
                    "description": "Gross monthly salary in UGX",
                    "required": True,
                }
            ],
        },
    ]


def _get_prompt(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Render a named prompt template."""
    if name == "vat_calculation_guide":
        amount = arguments.get("amount", "1000000")
        description = "VAT calculation guidance"
        text = (
            f"Please calculate the VAT at the statutory 18% rate on UGX {amount} "
            "and explain whether any exemptions apply."
        )
    elif name == "paye_withholding_guide":
        salary = arguments.get("monthly_salary", "2000000")
        description = "PAYE calculation guidance"
        text = (
            f"Please calculate PAYE on a monthly gross salary of UGX {salary} "
            "under the current resident PAYE tax bands."
        )
    else:
        raise LookupError(name)
    return {
        "description": description,
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


server = StatelessMCPServer(
    SERVER_NAME,
    NAMESPACE,
    SERVER_VERSION,
    instructions="Uganda statutory tax calculators. Figures follow the published rate table for the fiscal year given.",
    resources=_resources,
    read_resource=_read_resource,
    prompts=_prompts,
    get_prompt=_get_prompt,
)
server_info = server.server_info
handle_request = server.handle_request
create_app = server.create_app
serve_stdio = server.serve_stdio
