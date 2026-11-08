"""Certified metrics for the semantic layer (ADR-0033, phase 3).

A metric is served only when it is ``certified``: a named data owner has
reviewed its SQL, its sources and its minimum group size, and the entry
records who and when. ``metrics.json`` ships with drafts only, because no URA
analytical database is connected yet; a data owner certifies an entry by
filling ``certified_by`` and ``certified_at`` and setting ``status``.

Every metric is aggregate. Its template must return a ``group_size`` column,
and a row whose group is smaller than ``min_group_size`` is suppressed, so no
individual taxpayer's figures leave the database.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .guard import check

CATALOGUE = Path(__file__).with_name("metrics.json")
CERTIFIED = "certified"
MIN_GROUP_FLOOR = 10

_PARAM_TYPES: dict[str, re.Pattern[str]] = {
    "period": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "date": re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    "code": re.compile(r"^[a-z][a-z0-9_]{0,39}$"),
}


class MetricError(ValueError):
    """An unknown metric, an uncertified one, or a bad parameter."""


@dataclass(frozen=True)
class Metric:
    id: str
    title: str
    description: str
    owner: str
    sql: str
    sources: frozenset[str]
    parameters: dict[str, dict[str, Any]]
    min_group_size: int
    status: str = "draft"
    certified_by: str = ""
    certified_at: str = ""
    allowed_values: dict[str, list[str]] = field(default_factory=dict)

    @property
    def certified(self) -> bool:
        return self.status == CERTIFIED and bool(self.certified_by.strip()) and bool(self.certified_at.strip())

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "description": self.description, "owner": self.owner,
                "parameters": self.parameters, "certified_by": self.certified_by, "certified_at": self.certified_at}

    def bind(self, values: dict[str, Any]) -> dict[str, Any]:
        """Validated parameters for this metric; refuses unknown, missing or malformed ones."""
        values = dict(values or {})
        unknown = sorted(set(values) - set(self.parameters))
        if unknown:
            raise MetricError(f"unknown parameters for {self.id}: {', '.join(unknown)}")
        bound: dict[str, Any] = {}
        for name, spec in self.parameters.items():
            if name not in values:
                raise MetricError(f"{self.id} needs {name}")
            value = values[name]
            kind = spec.get("type", "")
            if kind == "int":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise MetricError(f"{name} must be a whole number")
                low, high = int(spec.get("min", 0)), int(spec.get("max", 10_000))
                if not low <= value <= high:
                    raise MetricError(f"{name} must be between {low} and {high}")
            elif kind in _PARAM_TYPES:
                if not isinstance(value, str) or not _PARAM_TYPES[kind].match(value):
                    raise MetricError(f"{name} must be a {kind}")
                if kind == "date":
                    try:
                        _dt.date.fromisoformat(value)
                    except ValueError as exc:
                        raise MetricError(f"{name} is not a calendar date") from exc
                allowed = self.allowed_values.get(name)
                if allowed is not None and value not in allowed:
                    raise MetricError(f"{name} must be one of {', '.join(allowed)}")
            else:
                raise MetricError(f"{self.id}: parameter {name} has no known type")
            bound[name] = value
        return bound


def _text(raw: dict[str, Any], field: str, default: str = "") -> str:
    """A text field; ``null`` or a non-string is refused, never turned into ``"None"``."""
    value = raw.get(field, default)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MetricError(f"{raw.get('id', '?')}: {field} must be text")
    return value


def _metric(raw: dict[str, Any]) -> Metric:
    metric = Metric(
        id=str(raw["id"]),
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
        owner=str(raw.get("owner", "")),
        sql=str(raw["sql"]),
        sources=frozenset(raw.get("sources") or ()),
        parameters=dict(raw.get("parameters") or {}),
        min_group_size=int(raw.get("min_group_size", MIN_GROUP_FLOOR)),
        status=_text(raw, "status", "draft"),
        certified_by=_text(raw, "certified_by"),
        certified_at=_text(raw, "certified_at"),
        allowed_values={k: list(v) for k, v in (raw.get("allowed_values") or {}).items()},
    )
    bound = check(metric.sql, sources=metric.sources)
    if bound != set(metric.parameters):
        raise MetricError(f"{metric.id}: template binds {sorted(bound)} but declares {sorted(metric.parameters)}")
    if metric.min_group_size < MIN_GROUP_FLOOR:
        raise MetricError(f"{metric.id}: min_group_size must be at least {MIN_GROUP_FLOOR}")
    if not re.search(r"\bgroup_size\b", metric.sql, re.I):
        raise MetricError(f"{metric.id}: an aggregate metric must return group_size")
    return metric


def load(path: Path | None = None) -> dict[str, Metric]:
    """Every metric in the catalogue, each validated; a bad entry fails the load."""
    source = path or Path(os.getenv("SEMANTIC_LAYER_CATALOGUE") or CATALOGUE)
    raw = json.loads(source.read_text(encoding="utf-8"))
    metrics = [_metric(item) for item in raw.get("metrics") or []]
    return {m.id: m for m in metrics}


def certified(path: Path | None = None) -> dict[str, Metric]:
    return {k: m for k, m in load(path).items() if m.certified}
