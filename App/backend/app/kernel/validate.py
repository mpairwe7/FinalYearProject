"""Runtime consistency checks for a kernel build (ADR-0033).

The SHACL shapes are the normative statement of the schema and run in CI with
pySHACL. These checks run anywhere, with no RDF stack, and cover what SHACL
states awkwardly or not at all:

* every rate-table key is in the kernel, once per fiscal year, with the
  table's value (completeness and parity);
* fiscal years do not overlap;
* ``SUPERSEDES`` joins the same key across years and has no cycle;
* the model's cardinalities and required properties, mirrored from the same
  :mod:`app.kernel.model` the shapes come from.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..graph.schema import FACT_KINDS, EdgeKind, NodeKind, fact_id
from .model import NODE_SPEC_BY_KIND, relationships_from

if TYPE_CHECKING:
    from ..graph.store import InMemoryGraphStore


@dataclass(frozen=True)
class Violation:
    code: str
    node: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.node}: {self.message}"


def _required_props(store: InMemoryGraphStore) -> list[Violation]:
    found: list[Violation] = []
    for kind, spec in NODE_SPEC_BY_KIND.items():
        for node in store.by_kind(kind):
            flat: dict[str, Any] = {"id": node.id, "label": node.label, "source": node.source,
                                    "unverified": node.unverified, **node.props}
            for prop in spec.properties:
                value = flat.get(prop.name)
                if prop.required and (value is None or value == ""):
                    found.append(Violation("required", node.id, f"missing {prop.name}"))
                if prop.pattern and value not in (None, "") and not re.fullmatch(prop.pattern, str(value)):
                    found.append(Violation("pattern", node.id, f"{prop.name}={value!r} does not match {prop.pattern}"))
    return found


def _cardinalities(store: InMemoryGraphStore) -> list[Violation]:
    found: list[Violation] = []
    for kind in NODE_SPEC_BY_KIND:
        rels = relationships_from(kind)
        allowed = {(r.kind, r.dst) for r in rels}
        for node in store.by_kind(kind):
            out = store.neighbours(node.id, outgoing=True)
            for edge, other in out:
                if (edge.kind, other.kind) not in allowed:
                    found.append(Violation("edge_kind", node.id,
                                           f"{edge.kind.value} to a {other.kind.value} is not in the model"))
            for rel in rels:
                count = sum(1 for edge, other in out if edge.kind is rel.kind and other.kind is rel.dst)
                if count < rel.min_count or (rel.max_count is not None and count > rel.max_count):
                    bound = f"{rel.min_count}..{rel.max_count if rel.max_count is not None else '*'}"
                    found.append(Violation("cardinality", node.id, f"{rel.kind.value}: {count}, model allows {bound}"))
    return found


def _provenance(store: InMemoryGraphStore) -> list[Violation]:
    found: list[Violation] = []
    for kind in FACT_KINDS:
        for node in store.by_kind(kind):
            cited = bool(store.neighbours(node.id, (EdgeKind.IMPOSED_BY,)))
            if not cited and not node.unverified:
                found.append(Violation("provenance", node.id, "no provision and not marked unverified"))
            if bool(node.props.get("uncited")) == cited:
                found.append(Violation("uncited_flag", node.id, f"uncited={node.props.get('uncited')} but cited={cited}"))
    return found


def _fiscal_years(store: InMemoryGraphStore) -> list[Violation]:
    found: list[Violation] = []
    spans: list[tuple[_dt.date, _dt.date, str]] = []
    for node in store.by_kind(NodeKind.FISCAL_YEAR):
        try:
            start = _dt.date.fromisoformat(str(node.props.get("effective_from")))
            end_raw = str(node.props.get("effective_to") or "")
            end = _dt.date.fromisoformat(end_raw) if end_raw else _dt.date.max
        except ValueError:
            found.append(Violation("fiscal_year", node.id, "unreadable effective dates"))
            continue
        if end < start:
            found.append(Violation("fiscal_year", node.id, "ends before it starts"))
        spans.append((start, end, node.id))
    spans.sort()
    for (_s1, e1, a), (s2, _e2, b) in zip(spans, spans[1:], strict=False):
        if s2 <= e1:
            found.append(Violation("overlap", b, f"overlaps {a}"))
    return found


def _supersedes(store: InMemoryGraphStore) -> list[Violation]:
    found: list[Violation] = []
    for kind in FACT_KINDS:
        for node in store.by_kind(kind):
            seen = {node.id}
            current = node
            while True:
                nxt = [o for e, o in store.neighbours(current.id, (EdgeKind.SUPERSEDES,))]
                if not nxt:
                    break
                other = nxt[0]
                if other.props.get("key") != current.props.get("key"):
                    found.append(Violation("supersedes_key", current.id, f"supersedes a different key: {other.id}"))
                    break
                if other.id in seen:
                    found.append(Violation("supersedes_cycle", node.id, "SUPERSEDES has a cycle"))
                    break
                seen.add(other.id)
                current = other
    return found


def _table_parity(store: InMemoryGraphStore) -> list[Violation]:
    from ..graph.build import kind_for_key
    from ..graph.schema import fact_id
    from ..tax import tables

    found: list[Violation] = []
    for fy in tables.list_fiscal_years():
        table = tables.get_table(fy)
        for key, value in table.rates.items():
            node = store.get(fact_id(kind_for_key(key), key, fy))
            if node is None:
                found.append(Violation("completeness", f"{key}@{fy}", "rate-table key is not in the kernel"))
            elif node.props.get("value") != value:
                found.append(Violation("parity", node.id, "value differs from the rate table"))
    keyed: dict[tuple[str, str], list[str]] = {}
    for kind in FACT_KINDS:
        for node in store.by_kind(kind):
            keyed.setdefault((str(node.props.get("key")), str(node.props.get("fiscal_year"))), []).append(node.id)
    for (key, fy), ids in keyed.items():
        if len(ids) > 1:
            found.append(Violation("duplicate", f"{key}@{fy}", f"{len(ids)} nodes: {sorted(ids)}"))
    return found


#: Figures the law defines in terms of each other, per fiscal year:
#: ``(dependent key, source key, factor, provision)`` means dependent = source * factor.
#: A table that updates one and not the other fails here, not in an answer.
DERIVED: tuple[tuple[str, str, float, str], ...] = (
    ("vat_quarterly_registration_threshold", "vat_registration_threshold_annual", 0.25,
     "VAT Act s.7(1)(a): one quarter of the annual registration threshold"),
    ("efris_penalty_failure_to_issue_invoice", "efris_penal_tax_minimum_ugx", 1.0,
     "TPCA s.93(2): one floor for failing to issue an e-invoice"),
)


def _derived(store: InMemoryGraphStore) -> list[Violation]:
    from ..graph.build import kind_for_key

    found = []
    years = [str(n.props.get("name") or n.id.split(":", 1)[-1]) for n in store.by_kind(NodeKind.FISCAL_YEAR)]
    for dependent, source, factor, provision in DERIVED:
        for fy in years:
            dep = store.get(fact_id(kind_for_key(dependent), dependent, fy))
            src = store.get(fact_id(kind_for_key(source), source, fy))
            if dep is None or src is None:
                continue
            expected = float(src.props.get("value")) * factor
            if abs(float(dep.props.get("value")) - expected) > 1e-6:
                found.append(Violation("derived", dep.id, f"{dep.props.get('value')} but {provision} gives {expected:g}"))
    return found


def check(store: InMemoryGraphStore, *, against_tables: bool = True) -> list[Violation]:
    """Every invariant the kernel must hold. Empty means consistent."""
    violations = (
        _required_props(store) + _cardinalities(store) + _provenance(store)
        + _fiscal_years(store) + _supersedes(store) + _derived(store)
    )
    if against_tables:
        violations += _table_parity(store)
    return violations
