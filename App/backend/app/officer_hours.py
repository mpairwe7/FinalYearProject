"""When officers are on duty: the hours a person can take a call or answer a handoff.

The AI answers around the clock; URA's officers do not. Outside their hours a
transfer cannot reach anyone, and a chat handoff will not be read until they
return — so both must say so, and an SLA must not count the hours nobody was
there to meet it.

The desk's hours come from the environment (defaults: 07:00–23:00, every day,
``Africa/Kampala`` — the CXP desk closes at 11 PM):

* ``CXP_TIMEZONE`` — an IANA zone (East Africa Time has no daylight saving).
* ``CXP_OPEN_HOUR`` / ``CXP_CLOSE_HOUR`` — whole hours, 0–23 and 1–24. A close
  at or before the open runs past midnight (20 → 4 is a night shift); the
  same hour (or 0 → 24) is a desk that never closes.
* ``CXP_WORKING_DAYS`` — days a shift *starts* on (``mon,tue,…``; default all).
* ``CXP_CLOSED_DATES`` — ``YYYY-MM-DD`` dates with no shift (public holidays).

A value that cannot be read falls back to its default with a warning: a typo
must not close the desk or open it all night.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DEFAULT_ZONE = "Africa/Kampala"
#: Kampala's offset, for a host without zone data (it has no daylight saving).
_EAT = dt.timezone(dt.timedelta(hours=3), "EAT")


@dataclass(frozen=True)
class OfficerHours:
    """The desk's schedule, in its own time zone."""

    zone: dt.tzinfo
    open_hour: int
    close_hour: int
    days: frozenset[int]
    closed_dates: frozenset[dt.date]

    @property
    def shift_length(self) -> dt.timedelta:
        span = (self.close_hour - self.open_hour) % 24
        return dt.timedelta(hours=span or 24)

    def label(self, at: float | None = None) -> str:
        """``07:00–23:00 EAT`` — for screens and messages, never for the voice."""
        stamp = dt.datetime.fromtimestamp(time.time() if at is None else at, self.zone)
        return f"{self.open_hour:02d}:00–{self.close_hour:02d}:00 {stamp.strftime('%Z')}".strip()


def _hour(name: str, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if not low <= value <= high:
        logger.warning("%s=%r is not an hour from %d to %d; using %d", name, raw, low, high, default)
        return default
    return value


def _zone() -> dt.tzinfo:
    name = (os.getenv("CXP_TIMEZONE") or _DEFAULT_ZONE).strip()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name != _DEFAULT_ZONE:
            logger.warning("CXP_TIMEZONE=%r is not a known time zone; using %s", name, _DEFAULT_ZONE)
        try:
            return ZoneInfo(_DEFAULT_ZONE)
        except (ZoneInfoNotFoundError, ValueError):
            return _EAT


def _days() -> frozenset[int]:
    raw = (os.getenv("CXP_WORKING_DAYS") or "").strip().lower()
    if not raw:
        return frozenset(range(7))
    days = {_DAYS.index(d.strip()[:3]) for d in raw.split(",") if d.strip()[:3] in _DAYS}
    if not days:
        logger.warning("CXP_WORKING_DAYS=%r names no day; using every day", raw)
        return frozenset(range(7))
    return frozenset(days)


def _closed_dates() -> frozenset[dt.date]:
    dates: set[dt.date] = set()
    for part in (os.getenv("CXP_CLOSED_DATES") or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            dates.add(dt.date.fromisoformat(part))
        except ValueError:
            logger.warning("CXP_CLOSED_DATES entry %r is not a YYYY-MM-DD date; ignored", part)
    return frozenset(dates)


def officer_hours() -> OfficerHours:
    """The configured schedule (read on each call, so a changed setting applies at once)."""
    return OfficerHours(
        zone=_zone(),
        open_hour=_hour("CXP_OPEN_HOUR", 7, 0, 23),
        close_hour=_hour("CXP_CLOSE_HOUR", 23, 1, 24),
        days=_days(),
        closed_dates=_closed_dates(),
    )


def _shifts(hours: OfficerHours, first: dt.date, last: dt.date) -> Iterator[tuple[dt.datetime, dt.datetime]]:
    """Each shift that starts on a date from *first* to *last*, as aware datetimes."""
    day = first
    while day <= last:
        if day.weekday() in hours.days and day not in hours.closed_dates:
            start = dt.datetime(day.year, day.month, day.day, hours.open_hour, tzinfo=hours.zone)
            yield start, start + hours.shift_length
        day += dt.timedelta(days=1)


def on_duty(at: float | None = None, hours: OfficerHours | None = None) -> bool:
    """Whether a shift is running at *at* (now by default)."""
    hours = hours or officer_hours()
    local = dt.datetime.fromtimestamp(time.time() if at is None else at, hours.zone)
    return any(start <= local < end for start, end in _shifts(hours, local.date() - dt.timedelta(days=1), local.date()))


def next_on_duty(at: float | None = None, hours: OfficerHours | None = None) -> float | None:
    """When officers are next on duty: *at* itself during a shift, else the next shift's start.

    ``None`` when no shift starts within a month (every day closed).
    """
    hours = hours or officer_hours()
    now = time.time() if at is None else at
    local = dt.datetime.fromtimestamp(now, hours.zone)
    for start, end in _shifts(hours, local.date() - dt.timedelta(days=1), local.date() + dt.timedelta(days=31)):
        if start <= local < end:
            return now
        if start > local:
            return start.timestamp()
    return None


def working_seconds(start: float, end: float, hours: OfficerHours | None = None) -> float:
    """Seconds between *start* and *end* that fell inside a shift — the SLA clock.

    A handoff raised at 23:15 waits until 07:00 without a second counting
    against the officers; the clock runs again when the shift starts.
    """
    if end <= start:
        return 0.0
    hours = hours or officer_hours()
    lo = dt.datetime.fromtimestamp(start, hours.zone)
    hi = dt.datetime.fromtimestamp(end, hours.zone)
    total = 0.0
    for shift_start, shift_end in _shifts(hours, lo.date() - dt.timedelta(days=1), hi.date()):
        overlap = (min(hi, shift_end) - max(lo, shift_start)).total_seconds()
        if overlap > 0:
            total += overlap
    return total


def status(at: float | None = None, hours: OfficerHours | None = None) -> dict[str, object]:
    """Whether officers are on duty now, their hours, and when they are next on duty — for an API.

    ``next_on_duty`` is an ISO 8601 time with its offset (``None`` when no
    shift starts within a month). The chat shows the notice in the reader's
    language from these fields; nothing here is prose to translate.
    """
    hours = hours or officer_hours()
    now = time.time() if at is None else at
    nxt = next_on_duty(now, hours)
    return {
        "on_duty": on_duty(now, hours),
        "hours": hours.label(now),
        "next_on_duty": dt.datetime.fromtimestamp(nxt, hours.zone).isoformat() if nxt is not None else None,
    }
