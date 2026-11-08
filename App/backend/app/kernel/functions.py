"""Kernel functions: the typed operations the agent may call (ADR-0033).

Each returns a :class:`KernelFact` (or a list of them) or a
:class:`NotInKernel`. Nothing here guesses: a concept the kernel does not
declare, or a date no fiscal year covers, is reported as such, and the
caller falls through to evidence with a disclosure or to escalation.

This is the contract the agent works inside. It cannot name a concept the
kernel does not declare, because no function accepts one (the OaK principle,
arXiv 2608.22974).
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ..graph.schema import FACT_KINDS, EdgeKind, Node, NodeKind, fact_id, fiscal_year_id

if TYPE_CHECKING:
    from ..graph.store import InMemoryGraphStore

_lock = threading.Lock()
_store: InMemoryGraphStore | None = None


@dataclass(frozen=True)
class KernelFact:
    key: str
    kind: str
    tax: str
    display_name: str
    value: Any
    unit: str
    fiscal_year: str
    effective_from: str
    effective_to: str
    provision: str
    provision_id: str
    unverified: bool
    uncited: bool
    note: str
    kernel_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NotInKernel:
    concept: str
    #: ``undeclared`` (no such concept), ``no_fiscal_year`` (no release covers
    #: the date) or ``not_in_year`` (declared, but not defined for that year).
    reason: str
    detail: str
    kernel_version: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "not_in_kernel": True, **asdict(self)}


def kernel_store() -> InMemoryGraphStore:
    """The kernel graph, built once per process from the rate tables."""
    global _store
    with _lock:
        if _store is None:
            from ..graph.build import build_graph

            _store = build_graph()
        return _store


def refresh() -> None:
    """Drop the cached build, after ``tables.reload_tables()``."""
    global _store
    with _lock:
        _store = None
    from .release import clear_cache

    clear_cache()


def _version() -> str:
    from .release import current

    return current().version


def declared_keys() -> frozenset[str]:
    """Every rate-table key the kernel declares."""
    store = kernel_store()
    return frozenset(str(n.props.get("key")) for kind in FACT_KINDS for n in store.by_kind(kind))


def declared_taxes() -> frozenset[str]:
    return frozenset(str(n.props.get("name")) for n in kernel_store().by_kind(NodeKind.TAX_TYPE))


def fiscal_year_on(as_of: _dt.date) -> str:
    """The fiscal year whose effective range contains *as_of*, or ``""``."""
    for node in kernel_store().by_kind(NodeKind.FISCAL_YEAR):
        start = _dt.date.fromisoformat(str(node.props["effective_from"]))
        end_raw = str(node.props.get("effective_to") or "")
        end = _dt.date.fromisoformat(end_raw) if end_raw else _dt.date.max
        if start <= as_of <= end:
            return node.label
    return ""


def _fact(store: InMemoryGraphStore, node: Node, version: str) -> KernelFact:
    tax = next((o.props.get("name", "") for _e, o in store.neighbours(node.id, (EdgeKind.RATED_FOR,))), "")
    prov = next((o for _e, o in store.neighbours(node.id, (EdgeKind.IMPOSED_BY,))), None)
    fy_node = store.get(fiscal_year_id(str(node.props.get("fiscal_year"))))
    return KernelFact(
        key=str(node.props.get("key")),
        kind=node.kind.value,
        tax=str(tax),
        display_name=str(node.props.get("display_name") or ""),
        value=node.props.get("value"),
        unit=str(node.props.get("unit") or ""),
        fiscal_year=str(node.props.get("fiscal_year")),
        effective_from=str(fy_node.props.get("effective_from")) if fy_node else "",
        effective_to=str(fy_node.props.get("effective_to") or "") if fy_node else "",
        provision=prov.label if prov else "",
        provision_id=prov.id if prov else "",
        unverified=node.unverified,
        uncited=bool(node.props.get("uncited")),
        note=str(node.props.get("note") or ""),
        kernel_version=version,
    )


def fact(key: str, as_of: _dt.date) -> KernelFact | NotInKernel:
    """The figure for *key* in force on *as_of*."""
    from ..graph.build import kind_for_key

    version = _version()
    if key not in declared_keys():
        return NotInKernel(key, "undeclared", "the kernel declares no such figure", version)
    fy = fiscal_year_on(as_of)
    if not fy:
        return NotInKernel(key, "no_fiscal_year", f"no kernel release covers {as_of.isoformat()}", version)
    store = kernel_store()
    node = store.get(fact_id(kind_for_key(key), key, fy))
    if node is None:
        return NotInKernel(key, "not_in_year", f"{key} is not defined for {fy}", version)
    return _fact(store, node, version)


def facts(*, as_of: _dt.date, kind: NodeKind | None = None, tax: str = "") -> list[KernelFact] | NotInKernel:
    """Every figure of *kind* (and *tax*) in force on *as_of*, sorted by key."""
    version = _version()
    if tax and tax not in declared_taxes():
        return NotInKernel(tax, "undeclared", "the kernel declares no such tax", version)
    fy = fiscal_year_on(as_of)
    if not fy:
        return NotInKernel(tax or (kind.value if kind else "facts"), "no_fiscal_year",
                           f"no kernel release covers {as_of.isoformat()}", version)
    store = kernel_store()
    kinds = (kind,) if kind else tuple(FACT_KINDS)
    out = []
    for k in kinds:
        for node in store.by_kind(k):
            if node.props.get("fiscal_year") != fy:
                continue
            item = _fact(store, node, version)
            if tax and item.tax != tax:
                continue
            out.append(item)
    return sorted(out, key=lambda f: f.key)


def obligations(*, as_of: _dt.date, tax: str = "") -> list[KernelFact] | NotInKernel:
    """Deadlines and procedural time limits in force on *as_of*."""
    return facts(as_of=as_of, kind=NodeKind.OBLIGATION, tax=tax)


def penalties(*, as_of: _dt.date, tax: str = "") -> list[KernelFact] | NotInKernel:
    """Penal taxes, penalties and interest rates in force on *as_of*."""
    return facts(as_of=as_of, kind=NodeKind.PENALTY, tax=tax)


def explain(provision: str) -> list[KernelFact] | NotInKernel:
    """Every figure a provision imposes. *provision* is a provision id or citation."""
    from ..graph.schema import provision_id as _pid

    version = _version()
    store = kernel_store()
    node = store.get(provision) or store.get(_pid(provision))
    if node is None or node.kind is not NodeKind.PROVISION:
        return NotInKernel(provision, "undeclared", "the kernel declares no such provision", version)
    found = [_fact(store, other, version) for _e, other in store.neighbours(node.id, (EdgeKind.IMPOSED_BY,), outgoing=False)]
    return sorted(found, key=lambda f: (f.fiscal_year, f.key))
