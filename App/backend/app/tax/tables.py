"""Effective-dated URA rate tables loaded from versioned data files.

Rates are **data, not code**.  Each fiscal year is one JSON file under
``data/`` carrying the rates, the statutory basis for every key, the
sources they were compiled from, and a ``status`` of ``confirmed`` or
``provisional``.  A new fiscal year is a new file plus a test — no
calculator change.

Two properties matter for correctness here:

*Effective dating.*  A calculation is always for a period, not for
"now".  :func:`resolve_fiscal_year` maps a date onto the table that was
in force on it, so a July 2026 question gets FY2026-27 rates while an
amended-return question about March 2026 still gets FY2025-26 ones.
Hard-coding a single "current" table silently serves last year's
numbers the day the budget takes effect.

*Provenance.*  A table compiled from secondary summaries ahead of the
gazetted Act is marked ``provisional``.  Every consumer receives that
status alongside the numbers, so the answer can say so.  Setting
``TAX_RATES_REQUIRE_CONFIRMED=true`` makes provisional tables fail
closed instead — the deployment refuses to quote unverified rates
rather than quoting them quietly.  This mirrors :mod:`app.authority`.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"

#: Marginal PAYE band: ``(lower_bound, upper_bound_or_None, marginal_rate)``.
Band = tuple[float, float | None, float]

STATUS_CONFIRMED = "confirmed"
STATUS_PROVISIONAL = "provisional"


class RateTableError(RuntimeError):
    """Raised when no usable rate table can be produced for a request."""


@dataclass(frozen=True)
class SourceRef:
    """Where a table's figures were compiled from."""

    id: str
    title: str
    publisher: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "publisher": self.publisher, "url": self.url}


@dataclass(frozen=True)
class RateTable:
    """One fiscal year's rates plus the provenance needed to defend them."""

    fiscal_year: str
    effective_from: _dt.date
    effective_to: _dt.date | None
    status: str
    rates: dict[str, Any]
    currency: str = "UGX"
    legal_basis: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    carried_forward: tuple[str, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    verification_note: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status == STATUS_CONFIRMED

    def covers(self, day: _dt.date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to

    def get(self, key: str, default: Any = None) -> Any:
        return self.rates.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.rates

    def __getitem__(self, key: str) -> Any:
        return self.rates[key]

    def provenance(self, *keys: str) -> dict[str, Any]:
        """Provenance block to stamp onto a tool result.

        *keys* names the rate keys the caller actually used, so the
        result carries only the statutory basis that applies to it
        instead of the whole table's.
        """
        payload: dict[str, Any] = {
            "fiscal_year": self.fiscal_year,
            "status": self.status,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "sources": [s.to_dict() for s in self.sources],
        }
        if self.verification_note:
            payload["verification_note"] = self.verification_note
        basis = {k: self.legal_basis[k] for k in keys if k in self.legal_basis}
        if basis:
            payload["legal_basis"] = basis
        notes = {k: self.notes[k] for k in keys if k in self.notes}
        if notes:
            payload["notes"] = notes
        carried = [k for k in keys if k in self.carried_forward]
        if carried:
            payload["carried_forward"] = carried
            payload["carried_forward_note"] = (
                "These figures were carried forward from the previous fiscal year because the "
                f"{self.fiscal_year} amendment did not publish a replacement; confirm with URA."
            )
        return payload


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _parse_date(value: Any, *, field_name: str, source: Path) -> _dt.date:
    if not isinstance(value, str):
        raise RateTableError(f"{source.name}: '{field_name}' must be an ISO date string")
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise RateTableError(f"{source.name}: '{field_name}' is not an ISO date: {value!r}") from exc


def _parse_bands(raw: Any, *, key: str, source: Path) -> list[Band]:
    """Validate a marginal band list, converting JSON ``null`` to an open top.

    Bands must be contiguous and ascending: a gap or an overlap would
    silently mis-tax an entire income range, so it is rejected at load
    time rather than at the first taxpayer who lands in the hole.
    """
    if not isinstance(raw, list) or not raw:
        raise RateTableError(f"{source.name}: '{key}' must be a non-empty list of bands")
    bands: list[Band] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, list | tuple) or len(entry) != 3:
            raise RateTableError(f"{source.name}: '{key}' band {idx} must be [lower, upper, rate]")
        lower, upper, rate = entry
        if not isinstance(lower, int | float) or not isinstance(rate, int | float):
            raise RateTableError(f"{source.name}: '{key}' band {idx} has a non-numeric bound/rate")
        if upper is not None and not isinstance(upper, int | float):
            raise RateTableError(f"{source.name}: '{key}' band {idx} upper bound must be a number or null")
        if upper is not None and upper <= lower:
            raise RateTableError(f"{source.name}: '{key}' band {idx} is empty or inverted")
        if not 0.0 <= float(rate) <= 1.0:
            raise RateTableError(f"{source.name}: '{key}' band {idx} rate {rate} is out of range")
        bands.append((float(lower), None if upper is None else float(upper), float(rate)))

    if bands[0][0] != 0:
        raise RateTableError(f"{source.name}: '{key}' must start at 0")
    for idx in range(1, len(bands)):
        previous_upper = bands[idx - 1][1]
        if previous_upper is None:
            raise RateTableError(f"{source.name}: '{key}' has bands after an open-ended one")
        if previous_upper != bands[idx][0]:
            raise RateTableError(
                f"{source.name}: '{key}' is not contiguous at band {idx} "
                f"({previous_upper} -> {bands[idx][0]})"
            )
    if bands[-1][1] is not None:
        raise RateTableError(f"{source.name}: '{key}' must end with an open-ended (null) top band")
    return bands


def _load_table(path: Path) -> RateTable:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RateTableError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RateTableError(f"{path.name}: rate table must be a JSON object")

    fiscal_year = str(raw.get("fiscal_year", "")).strip()
    if not fiscal_year:
        raise RateTableError(f"{path.name}: 'fiscal_year' is required")
    if fiscal_year != path.stem:
        raise RateTableError(f"{path.name}: declares fiscal_year {fiscal_year!r}; filename must match")

    status = str(raw.get("status", STATUS_PROVISIONAL))
    if status not in (STATUS_CONFIRMED, STATUS_PROVISIONAL):
        raise RateTableError(f"{path.name}: unknown status {status!r}")

    rates = raw.get("rates")
    if not isinstance(rates, dict) or not rates:
        raise RateTableError(f"{path.name}: 'rates' must be a non-empty object")
    parsed_rates: dict[str, Any] = {}
    for key, value in rates.items():
        if key.startswith("paye_bands"):
            parsed_rates[key] = _parse_bands(value, key=key, source=path)
        else:
            parsed_rates[key] = value

    effective_to_raw = raw.get("effective_to")
    sources = tuple(
        SourceRef(
            id=str(s.get("id", "")),
            title=str(s.get("title", "")),
            publisher=str(s.get("publisher", "")),
            url=str(s.get("url", "")),
        )
        for s in raw.get("sources", [])
        if isinstance(s, dict)
    )
    if not sources:
        raise RateTableError(f"{path.name}: at least one source is required")

    return RateTable(
        fiscal_year=fiscal_year,
        effective_from=_parse_date(raw.get("effective_from"), field_name="effective_from", source=path),
        effective_to=(
            None if effective_to_raw is None else _parse_date(effective_to_raw, field_name="effective_to", source=path)
        ),
        status=status,
        rates=parsed_rates,
        currency=str(raw.get("currency", "UGX")),
        legal_basis={str(k): str(v) for k, v in (raw.get("legal_basis") or {}).items()},
        notes={str(k): str(v) for k, v in (raw.get("notes") or {}).items()},
        carried_forward=tuple(str(k) for k in (raw.get("carried_forward") or [])),
        sources=sources,
        verification_note=str(raw.get("verification_note", "")),
    )


_lock = threading.Lock()
_tables: dict[str, RateTable] | None = None


def _all_tables() -> dict[str, RateTable]:
    """Load every table once, ordered oldest first."""
    global _tables
    if _tables is not None:
        return _tables
    with _lock:
        if _tables is not None:  # pragma: no cover - lost race
            return _tables
        loaded: list[RateTable] = []
        for path in sorted(DATA_DIR.glob("*.json")):
            loaded.append(_load_table(path))
        if not loaded:
            raise RateTableError(f"no rate tables found in {DATA_DIR}")
        loaded.sort(key=lambda t: t.effective_from)
        for earlier, later in zip(loaded, loaded[1:], strict=False):
            if earlier.effective_to is None:
                raise RateTableError(
                    f"{earlier.fiscal_year} is open-ended but {later.fiscal_year} follows it"
                )
            if earlier.effective_to >= later.effective_from:
                raise RateTableError(
                    f"{earlier.fiscal_year} overlaps {later.fiscal_year}"
                )
        _tables = {t.fiscal_year: t for t in loaded}
        logger.info(
            "Loaded %d URA rate tables: %s",
            len(_tables),
            ", ".join(f"{t.fiscal_year}({t.status})" for t in loaded),
        )
        return _tables


def reload_tables() -> None:
    """Drop the cache so the next lookup re-reads ``data/`` (tests, hot reload)."""
    global _tables
    with _lock:
        _tables = None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def require_confirmed() -> bool:
    """Whether provisional tables should fail closed rather than be quoted."""
    default = "true" if os.getenv("APP_ENV", "development").lower() == "production" else "false"
    return os.getenv("TAX_RATES_REQUIRE_CONFIRMED", default).lower() in ("1", "true", "yes", "on")


def list_fiscal_years() -> list[str]:
    """Known fiscal years, oldest first."""
    return sorted(_all_tables(), key=lambda fy: _all_tables()[fy].effective_from)


def latest_fiscal_year() -> str:
    return list_fiscal_years()[-1]


def resolve_fiscal_year(as_of: _dt.date | None = None) -> str:
    """The fiscal year in force on *as_of* (default: today, UTC).

    Dates before the earliest table resolve to that earliest table and
    dates after the latest resolve to the latest — a question about a
    period we have no table for is answered with the nearest one we do
    have, and the caller sees the mismatch via ``effective_from``.
    """
    # timezone.utc rather than datetime.UTC: the dev container still
    # runs 3.10, and this is the only clock read in the module.
    day = as_of or _dt.datetime.now(_dt.timezone.utc).date()  # noqa: UP017
    tables = _all_tables()
    ordered = [tables[fy] for fy in list_fiscal_years()]
    for table in ordered:
        if table.covers(day):
            return table.fiscal_year
    return ordered[0].fiscal_year if day < ordered[0].effective_from else ordered[-1].fiscal_year


def get_table(fiscal_year: str | None = None, *, as_of: _dt.date | None = None) -> RateTable:
    """Return the table for *fiscal_year*, or the one in force on *as_of*.

    Raises :class:`RateTableError` for an unknown fiscal year, and — when
    ``TAX_RATES_REQUIRE_CONFIRMED`` is on — for a provisional one.
    """
    tables = _all_tables()
    fy = fiscal_year or resolve_fiscal_year(as_of)
    table = tables.get(fy)
    if table is None:
        raise RateTableError(
            f"Unknown fiscal year '{fy}'. Known years: {', '.join(list_fiscal_years())}"
        )
    if not table.confirmed and require_confirmed():
        raise RateTableError(
            f"{fy} rates are provisional and this deployment requires confirmed rates. "
            f"{table.verification_note or 'Confirm the figures with URA before use.'}"
        )
    return table


def compare(key: str, *, older: str, newer: str) -> dict[str, Any]:
    """Old vs new value for one rate key across two fiscal years.

    Used to answer "what changed this year" without the caller having to
    load both tables itself.
    """
    old_table, new_table = get_table(older), get_table(newer)
    return {
        "key": key,
        "older": {"fiscal_year": older, "value": old_table.get(key)},
        "newer": {"fiscal_year": newer, "value": new_table.get(key)},
        "changed": old_table.get(key) != new_table.get(key),
    }
