"""Fiscal calendar + deadline tool.

Grounds the model in **real-time temporal context** — without this
tool, Qwen has no way to know today's date or which URA deadlines
are upcoming.  Returns deterministic data pulled from a static
deadline table keyed on the Ugandan fiscal year (1 July → 30 June).

Every response carries the ``today`` date and a ``fiscal_year``
string so the LLM can cite them in its answer without having to
guess.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from . import Tool, ToolRegistry, ToolSchema


def _fiscal_year_for(date: _dt.date) -> str:
    """Return the Ugandan fiscal year string for a given date.

    The URA FY runs 1 July → 30 June.  So 2026-04-12 is in
    FY2025-26 (which runs 2025-07-01 → 2026-06-30).
    """
    if date.month >= 7:
        start = date.year
        end = date.year + 1
    else:
        start = date.year - 1
        end = date.year
    return f"FY{start}-{str(end)[-2:]}"


# ---------------------------------------------------------------------------
# Recurring deadline schedule (simplified)
# ---------------------------------------------------------------------------
# Each entry: (month, day, name, description, scope)
# "Scope" names the taxpayer type: all | individual | company | vat | paye | customs
_RECURRING_DEADLINES: list[tuple[int, int, str, str, str]] = [
    (
        1,
        15,
        "PAYE / WHT / VAT monthly",
        "PAYE, WHT, VAT and excise returns for the prior month are due",
        "all",
    ),
    (
        2,
        15,
        "PAYE / WHT / VAT monthly",
        "PAYE, WHT, VAT and excise returns for the prior month are due",
        "all",
    ),
    (3, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Feb are due", "all"),
    (4, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Mar are due", "all"),
    (5, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Apr are due", "all"),
    (6, 15, "PAYE / WHT / VAT monthly", "Monthly returns for May are due", "all"),
    (7, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Jun are due", "all"),
    (8, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Jul are due", "all"),
    (9, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Aug are due", "all"),
    (10, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Sep are due", "all"),
    (11, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Oct are due", "all"),
    (12, 15, "PAYE / WHT / VAT monthly", "Monthly returns for Nov are due", "all"),
    # Annual returns
    (
        9,
        30,
        "Individual provisional income tax",
        "Third-month provisional return for individuals (3 months after start of FY)",
        "individual",
    ),
    (
        12,
        31,
        "Company provisional income tax",
        "Sixth-month provisional return for companies (6 months after start of FY)",
        "company",
    ),
    (
        12,
        31,
        "Final income tax return",
        "Final income tax return due within 6 months after FY end",
        "all",
    ),
]


class CurrentDateTool(Tool):
    """Returns today's date and the current Ugandan fiscal year."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="get_current_date",
            description=(
                "Return today's date, day of week, and the current "
                "Ugandan fiscal year (URA FY runs 1 July to 30 June). "
                "Call this tool whenever the user asks about 'now', "
                "'today', 'this year', 'next month', or any date-relative "
                "concept — the model does not know the real date."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "today": {"type": "string"},
                    "fiscal_year": {"type": "string"},
                    "error": {"type": "string"},
                },
            },
            risk="low",
            namespace="calendar",
        )

    def execute(self) -> dict[str, Any]:
        today = _dt.date.today()
        fy = _fiscal_year_for(today)
        fy_start = _dt.date(int(fy[2:6]), 7, 1)
        fy_end = _dt.date(int(fy[2:6]) + 1, 6, 30)
        days_into_fy = (today - fy_start).days
        days_remaining_fy = (fy_end - today).days
        return {
            "ok": True,
            "today": today.isoformat(),
            "day_of_week": today.strftime("%A"),
            "fiscal_year": fy,
            "fiscal_year_start": fy_start.isoformat(),
            "fiscal_year_end": fy_end.isoformat(),
            "days_into_fy": days_into_fy,
            "days_remaining_in_fy": days_remaining_fy,
        }


class NextDeadlineTool(Tool):
    """Returns the next upcoming URA deadline(s)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="get_next_deadlines",
            description=(
                "Return the next N upcoming URA deadlines from today. "
                "Useful for proactive reminders: 'when is my next "
                "filing due', 'what's due this month', etc. Defaults "
                "to the next 3 deadlines within the next 90 days."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of deadlines to return (1-10).",
                        "default": 3,
                    },
                    "within_days": {
                        "type": "integer",
                        "description": "Only return deadlines falling within this many days from today.",
                        "default": 90,
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["all", "individual", "company"],
                        "description": "Filter to 'individual', 'company', or 'all' (default).",
                        "default": "all",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "today": {"type": "string"},
                    "count": {"type": "integer"},
                    "deadlines": {"type": "array"},
                    "error": {"type": "string"},
                },
            },
            risk="low",
            namespace="calendar",
        )

    def execute(
        self,
        limit: int = 3,
        within_days: int = 90,
        scope: str = "all",
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 10))
        within_days = max(1, min(int(within_days), 365))
        today = _dt.date.today()
        unique = upcoming_deadlines(within_days=within_days, scope=scope, today=today)
        return {
            "ok": True,
            "today": today.isoformat(),
            "horizon": (today + _dt.timedelta(days=within_days)).isoformat(),
            "scope": scope,
            "count": len(unique[:limit]),
            "deadlines": unique[:limit],
        }


def upcoming_deadlines(
    within_days: int = 90,
    scope: str = "all",
    today: _dt.date | None = None,
) -> list[dict[str, Any]]:
    """Deadlines falling within *within_days* of *today*, soonest first.

    Extracted from :class:`NextDeadlineTool` so the reminder selector
    can reuse it rather than grow a second copy of the recurrence rules,
    and so *today* can be injected — the tool hardcoded
    ``date.today()``, which made every date-dependent assertion about it
    depend on the day the suite happened to run.
    """
    today = today or _dt.date.today()
    within_days = max(1, min(int(within_days), 365))
    horizon = today + _dt.timedelta(days=within_days)

    candidates: list[dict[str, Any]] = []
    # Roll forward into next year so a December run still sees January.
    for year in sorted({today.year, today.year + 1}):
        for month, day, name, desc, sc in _RECURRING_DEADLINES:
            try:
                due = _dt.date(year, month, day)
            except ValueError:
                continue
            if due < today or due > horizon:
                continue
            if scope not in ("all", sc) and sc != "all":
                continue
            candidates.append(
                {
                    "date": due.isoformat(),
                    "days_away": (due - today).days,
                    "days_until": (due - today).days,
                    "name": name,
                    "description": desc,
                    "scope": sc,
                }
            )

    candidates.sort(key=lambda c: c["date"])
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (candidate["date"], candidate["name"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


ToolRegistry.register(CurrentDateTool())
ToolRegistry.register(NextDeadlineTool())
