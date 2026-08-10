"""The ``tax_graph`` MCP namespace — statutory joins as tools.

Three tools, each answering a question the flat rate lookup cannot:

``graph_resolve_rate``
    A tax *and* a taxpayer class *and* a fiscal year, resolved
    together. ``lookup_rate`` answers "what is the PAYE rate"; this
    answers "what does a **non-resident** pay", which is a different
    figure reached through a different Schedule.

``graph_rate_history``
    A figure's supersession chain. "Has the VAT registration threshold
    changed, and from when?" needs both years at once.

``graph_effective_on``
    The figure in force on a given date. Rates change on 1 July, so a
    taxpayer asking about a past period needs that period's figure —
    answering with today's is a compliance failure the system caused.

Every result carries the provision it came from, and an ``unverified``
figure keeps that mark. A claim that cannot cite its provision is not
emitted at all.

Gated by ``FLAG_TAX_GRAPH``: with the flag closed the tools return a
structured "disabled" result rather than disappearing, so a caller that
has them in a whitelist gets an explanation instead of an unknown-tool
error.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..flags import flags
from . import Tool, ToolRegistry, ToolSchema

GRAPH_NAMESPACE = "tax_graph"

_DISABLED = {
    "ok": False,
    "error": "The statutory graph is not enabled on this deployment.",
    "hint": "Set FLAG_TAX_GRAPH=true to enable graph-backed answers.",
}


def _enabled() -> bool:
    return flags.is_enabled("tax_graph")


class GraphResolveRateTool(Tool):
    """Resolve a rate against a taxpayer class and a fiscal year."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="graph_resolve_rate",
            description=(
                "Resolve tax rates for a specific taxpayer situation by "
                "traversing the statutory graph. Use this INSTEAD of "
                "lookup_rate when the question depends on WHO the taxpayer "
                "is (resident vs non-resident, individual vs company) or on "
                "WHICH YEAR applies, because those change which figure is "
                "correct. Returns each rate with the Act and section it "
                "comes from, any threshold that gates it, and any charge it "
                "is computed on top of."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The taxpayer's question, in their own words. The "
                            "tool extracts the tax, the taxpayer class and the "
                            "fiscal year from it."
                        ),
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": (
                            "Fiscal year to assume when the question does not "
                            "name one, e.g. 'FY2026-27'. Optional."
                        ),
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
            risk="low",
            namespace=GRAPH_NAMESPACE,
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, question: str, fiscal_year: str = "") -> dict[str, Any]:
        if not _enabled():
            return dict(_DISABLED)
        from ..graph.query import resolve

        answer = resolve(question, default_fiscal_year=fiscal_year)
        return {"ok": True, **answer.to_dict()}


class GraphRateHistoryTool(Tool):
    """Walk a figure's supersession chain."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="graph_rate_history",
            description=(
                "Show how a single rate or threshold has changed across "
                "fiscal years, newest first, with the Act behind each "
                "version. Use this when the taxpayer asks whether something "
                "changed, when it changed, or what it used to be."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "rate_key": {
                        "type": "string",
                        "description": (
                            "Rate table key, e.g. "
                            "'vat_registration_threshold_annual' or "
                            "'corporation_tax'."
                        ),
                    }
                },
                "required": ["rate_key"],
                "additionalProperties": False,
            },
            risk="low",
            namespace=GRAPH_NAMESPACE,
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, rate_key: str) -> dict[str, Any]:
        if not _enabled():
            return dict(_DISABLED)
        from ..graph.query import history

        return {"ok": True, **history(rate_key).to_dict()}


class GraphEffectiveOnTool(Tool):
    """The figure in force on a given date."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="graph_effective_on",
            description=(
                "Return the rate or threshold that was in force on a "
                "specific date. Ugandan rates change on 1 July, so ALWAYS "
                "use this rather than the current rate when the taxpayer "
                "asks about a past period, a past transaction, or a return "
                "for an earlier year."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "rate_key": {
                        "type": "string",
                        "description": "Rate table key.",
                    },
                    "date": {
                        "type": "string",
                        "description": "ISO date, e.g. '2026-03-01'.",
                    },
                },
                "required": ["rate_key", "date"],
                "additionalProperties": False,
            },
            risk="low",
            namespace=GRAPH_NAMESPACE,
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, rate_key: str, date: str) -> dict[str, Any]:
        if not _enabled():
            return dict(_DISABLED)
        from ..graph.query import effective_on

        try:
            day = _dt.date.fromisoformat(date.strip())
        except (ValueError, AttributeError):
            return {
                "ok": False,
                "error": f"date must be an ISO date like 2026-03-01, got {date!r}",
            }
        return {"ok": True, **effective_on(rate_key, day).to_dict()}


ToolRegistry.register(GraphResolveRateTool())
ToolRegistry.register(GraphRateHistoryTool())
ToolRegistry.register(GraphEffectiveOnTool())
