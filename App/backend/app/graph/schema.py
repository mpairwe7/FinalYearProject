"""Node and edge vocabulary for the statutory knowledge graph.

Small and closed on purpose. Tax law is already structured — an Act, a
section, a rate, a threshold, a class of taxpayer, a date it takes
effect — so the graph does not need to discover a schema. It needs to
record the joins that flat retrieval cannot make, and nothing else.

A closed vocabulary is also what makes the graph auditable: a node kind
that is not here cannot be created, so nothing can quietly grow a
category nobody reviewed.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    """What a node is. Values are stable identifiers."""

    #: A tax: vat, paye, rental, corporation, customs, withholding…
    TAX_TYPE = "tax_type"
    #: Who it applies to: resident, non_resident, individual, company.
    TAXPAYER_CLASS = "taxpayer_class"
    #: A rate or band set, effective-dated.
    RATE = "rate"
    #: A monetary floor or ceiling that changes whether a rate applies.
    THRESHOLD = "threshold"
    #: An Act and section — the statutory authority for a figure.
    PROVISION = "provision"
    #: A fiscal year, so "as at" questions are a traversal.
    FISCAL_YEAR = "fiscal_year"


class EdgeKind(str, Enum):
    """How two nodes relate."""

    #: Rate → TaxType. Which tax this figure is a rate *of*.
    RATED_FOR = "rated_for"
    #: Rate → TaxpayerClass. The join flat retrieval keeps missing.
    APPLIES_TO = "applies_to"
    #: Rate → Provision. The statutory authority.
    IMPOSED_BY = "imposed_by"
    #: Rate → FiscalYear. When it was in force.
    EFFECTIVE_IN = "effective_in"
    #: Rate → Rate. This year's figure replaces last year's.
    #: Makes "what was the position then" a traversal rather than a
    #: retrieval gamble.
    SUPERSEDES = "supersedes"
    #: Threshold → Rate. Below the threshold the rate does not bite.
    GATES = "gates"
    #: Rate → Rate. This tax is charged on a base that already
    #: includes the other — VAT on the duty-inclusive value. Encoding
    #: it as an edge is the point: it currently lives as a sentence in
    #: a prompt the model may or may not honour.
    COMPUTED_ON = "computed_on"


@dataclass(frozen=True)
class Node:
    """One fact. ``id`` is stable and derived, never random."""

    id: str
    kind: NodeKind
    label: str
    #: Free-form, kind-specific. Rates carry ``value``/``bands``,
    #: provisions carry ``act``/``section``, fiscal years carry dates.
    props: dict[str, Any] = field(default_factory=dict)
    #: Where this came from. A node that cannot say why it is true is
    #: not allowed to reach an answer — see ``query.explain``.
    source: str = ""
    #: True when the underlying figure has not been reconciled against
    #: primary legislative text. Carried through to any answer built on
    #: it, because "the system is unsure" is itself an answer.
    unverified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "props": self.props,
            "source": self.source,
            "unverified": self.unverified,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Node:
        return Node(
            id=raw["id"],
            kind=NodeKind(raw["kind"]),
            label=raw["label"],
            props=raw.get("props", {}),
            source=raw.get("source", ""),
            unverified=bool(raw.get("unverified", False)),
        )


@dataclass(frozen=True)
class Edge:
    """A directed relation between two nodes."""

    src: str
    kind: EdgeKind
    dst: str
    props: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"src": self.src, "kind": self.kind.value, "dst": self.dst, "props": self.props}

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Edge:
        return Edge(
            src=raw["src"],
            kind=EdgeKind(raw["kind"]),
            dst=raw["dst"],
            props=raw.get("props", {}),
        )


# ---------------------------------------------------------------------------
# Stable id helpers
# ---------------------------------------------------------------------------
# Ids are derived from content rather than generated, so rebuilding the
# graph from the same tables produces byte-identical output. That is what
# lets a rebuild be diffed in review and shipped in a versioned bundle.


def tax_type_id(name: str) -> str:
    return f"tax:{name}"


def class_id(name: str) -> str:
    return f"class:{name}"


def rate_id(key: str, fiscal_year: str) -> str:
    return f"rate:{key}@{fiscal_year}"


def threshold_id(key: str, fiscal_year: str) -> str:
    return f"threshold:{key}@{fiscal_year}"


def provision_id(text: str) -> str:
    """Provisions are keyed on their citation text, normalised.

    Two rate keys citing the same section must land on the same node —
    that shared node is what makes "which rules come from this section"
    answerable at all.
    """
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"prov:{slug.strip('-')[:120]}"


def fiscal_year_id(fy: str) -> str:
    return f"fy:{fy}"


def parse_fiscal_year_start(fy: str) -> _dt.date | None:
    """``FY2026-27`` → 1 July 2026, for ordering supersession."""
    digits = "".join(ch for ch in fy.split("-")[0] if ch.isdigit())
    if len(digits) != 4:
        return None
    try:
        return _dt.date(int(digits), 7, 1)
    except ValueError:
        return None
