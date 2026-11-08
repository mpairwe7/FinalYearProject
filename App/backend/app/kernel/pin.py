"""Pin a turn to one kernel release (ADR-0033).

A turn's figures, its audit row and any approval it suspends must all name the
same knowledge. The pin is taken once, at turn start: the kernel version, the
ruleset version, the content hash and the date the answer must be true for.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass
from typing import Any

#: Uganda is UTC+3 all year; "today" for a taxpayer is Kampala's date.
KAMPALA = _dt.timezone(_dt.timedelta(hours=3), "EAT")


@dataclass(frozen=True)
class KernelPin:
    kernel_version: str
    ruleset_version: str
    content_sha256: str
    released: bool
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def today() -> _dt.date:
    return _dt.datetime.now(KAMPALA).date()


def pin_for_turn(as_of: _dt.date | None = None) -> KernelPin:
    from .release import current

    version = current()
    return KernelPin(
        kernel_version=version.version,
        ruleset_version=version.ruleset_version,
        content_sha256=version.content_sha256,
        released=version.released,
        as_of=(as_of or today()).isoformat(),
    )


def active_pin() -> dict[str, Any] | None:
    """The pin of the running turn, or ``None`` outside a turn."""
    from ..turn_context import ACTIVE_KERNEL_PIN

    return ACTIVE_KERNEL_PIN.get()
