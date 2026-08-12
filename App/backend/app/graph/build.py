"""Build the statutory graph from the effective-dated rate tables.

The proposal's first increment was LLM extraction over the daily crawl.
Starting there would have been backwards: this repository already holds
a **curated, effective-dated, provenance-carrying** dataset in
``app/tax/data/FY*.json``, and it is the same dataset the calculators
answer from and the multi-hop golden set is pinned to.

So the graph is built as a *projection* of those tables. Every node
traces to a rate key, its ``legal_basis`` citation, and its fiscal
year — no model, no extraction, no hallucination surface. What the
graph adds is the **joins** the flat table cannot express:

- ``paye_bands_resident`` and ``paye_bands_non_resident`` become two
  rates on one tax, separated by an ``APPLIES_TO`` edge to a taxpayer
  class. Retrieval keeps answering the non-resident question with the
  resident bands; the edge is what distinguishes them.
- The same key across two years becomes a ``SUPERSEDES`` chain, so
  "was I required to register last year" is a walk rather than a guess.
- ``vat_standard`` gains a ``COMPUTED_ON`` edge to ``customs_duty_common``,
  which is currently a sentence in a prompt the model may or may not
  honour.

Prose provisions from the crawl come later, behind human review, and
land in the same store through the same interface.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .schema import (
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    class_id,
    fiscal_year_id,
    parse_fiscal_year_start,
    provision_id,
    rate_id,
    tax_type_id,
    threshold_id,
)
from .store import InMemoryGraphStore

logger = logging.getLogger(__name__)

#: Rate-key prefix → the tax it belongs to. Longest prefix wins, so
#: ``capital_gains_corporate`` resolves to capital_gains rather than to
#: a shorter accidental match.
_TAX_PREFIXES: tuple[tuple[str, str], ...] = (
    ("vat_registration_threshold", "vat"),
    ("vat", "vat"),
    ("paye_bands", "paye"),
    ("corporation_tax", "corporation_tax"),
    ("capital_gains", "capital_gains"),
    ("rental_company_expense_cap", "rental"),
    ("rental_tax", "rental"),
    ("withholding", "withholding"),
    ("customs_duty", "customs"),
    ("environmental_levy", "customs"),
    ("nssf", "nssf"),
)

#: Key suffix → taxpayer class. This is the join the golden set is
#: mostly about, and it is derivable from the key name alone because
#: the tables were named consistently.
_CLASS_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_non_resident", "non_resident"),
    ("_resident", "resident"),
    ("_individual", "individual"),
    ("_individual_threshold", "individual"),
    ("_company", "company"),
    ("_corporate", "company"),
)

#: Keys that are a monetary gate rather than a rate.
_THRESHOLD_MARKERS = ("threshold", "_cap")

#: Human labels for the tax types, for answers and for the tool output.
_TAX_LABELS = {
    "vat": "Value Added Tax",
    "paye": "Pay As You Earn",
    "corporation_tax": "Corporation tax",
    "capital_gains": "Capital gains tax",
    "rental": "Rental income tax",
    "withholding": "Withholding tax",
    "customs": "Customs and import charges",
    "nssf": "NSSF contribution",
}

_CLASS_LABELS = {
    "resident": "Resident taxpayer",
    "non_resident": "Non-resident taxpayer",
    "individual": "Individual",
    "company": "Company",
}

#: Rates charged on a base that already includes another charge.
#: ``(dependent_key, base_key)``. This is the customs interaction the
#: specialist prompt describes in prose.
_COMPUTED_ON: tuple[tuple[str, str], ...] = (
    ("vat_standard", "customs_duty_common"),
    ("vat_standard", "environmental_levy_used_clothing"),
)

_ACT_RE = re.compile(r"^(?P<act>[^,]+?)(?:,\s*(?P<section>s\.?\s*[\w().]+.*))?$")

#: Labels the rate tool already curates, plus the few this graph adds.
#: Reused rather than re-typed so a rate cannot be called one thing in a
#: calculator result and another in a graph claim.
_EXTRA_LABELS = {
    "nssf_employee_contribution": "NSSF employee contribution on employment income",
    "capital_gains_corporate": "Capital gains tax on the gain",
}


def display_name(key: str) -> str:
    """Human label for a rate key.

    Emitting the raw key to a model is a defect in its own right: it
    reads ``environmental_levy_used_clothing`` as one token-salad noun
    and cannot say "environmental levy" back to a taxpayer. The rate
    tool already maintains this mapping; importing it keeps one name per
    figure across the calculators, the rate lookups and the graph.
    """
    if key in _EXTRA_LABELS:
        return _EXTRA_LABELS[key]
    try:
        from ..tools.rates import _DISPLAY_NAMES

        if key in _DISPLAY_NAMES:
            return _DISPLAY_NAMES[key]
    except Exception as exc:  # pragma: no cover - label lookup is best-effort
        # Falling back to the humanised key is fine; doing it silently
        # is not — a label that quietly regresses to snake_case is the
        # kind of thing nobody notices until it is in front of a
        # taxpayer.
        logger.debug("graph: label lookup unavailable for %s (%s)", key, exc)
    return key.replace("_", " ")


def tax_for_key(key: str) -> str:
    """Which tax a rate key belongs to, or ``""``."""
    for prefix, tax in _TAX_PREFIXES:
        if key.startswith(prefix):
            return tax
    return ""


def class_for_key(key: str) -> str:
    """Which taxpayer class a rate key is scoped to, or ``""``.

    Longest suffix first: ``paye_bands_non_resident`` must not match
    ``_resident`` and be filed as a resident rate — that single
    misclassification would reproduce the exact bug the graph exists
    to fix.
    """
    for suffix, klass in sorted(_CLASS_SUFFIXES, key=lambda p: -len(p[0])):
        if key.endswith(suffix):
            return klass
    return ""


def is_threshold(key: str) -> bool:
    return any(marker in key for marker in _THRESHOLD_MARKERS)


def _split_citation(text: str) -> dict[str, str]:
    """Split a ``legal_basis`` string into act and section."""
    match = _ACT_RE.match(text.strip())
    if not match:
        return {"act": text.strip(), "section": ""}
    return {
        "act": (match.group("act") or "").strip(),
        "section": (match.group("section") or "").strip(),
    }


def build_graph(fiscal_years: list[str] | None = None) -> InMemoryGraphStore:
    """Project the rate tables into a graph.

    Deterministic: the same tables always produce the same store, so a
    rebuild can be diffed in review rather than trusted.
    """
    from ..tax import tables as rate_tables

    store = InMemoryGraphStore()
    years = fiscal_years or rate_tables.list_fiscal_years()

    # Rate nodes are indexed by key so SUPERSEDES can be wired after
    # every year is loaded — a chain needs both ends to exist first.
    by_key: dict[str, list[tuple[str, str]]] = {}

    for fy in years:
        try:
            table = rate_tables.get_table(fy)
        except Exception as exc:
            logger.warning("graph: skipping %s (%s)", fy, exc)
            continue

        fy_node = Node(
            id=fiscal_year_id(fy),
            kind=NodeKind.FISCAL_YEAR,
            label=fy,
            props={
                "effective_from": table.effective_from.isoformat(),
                "effective_to": table.effective_to.isoformat() if table.effective_to else "",
                "status": table.status,
            },
            source=fy,
        )
        store.add_node(fy_node)

        for key, value in table.rates.items():
            tax = tax_for_key(key)
            if not tax:
                continue

            tax_node = Node(
                id=tax_type_id(tax),
                kind=NodeKind.TAX_TYPE,
                label=_TAX_LABELS.get(tax, tax.replace("_", " ").title()),
                props={"name": tax},
            )
            store.add_node(tax_node)

            threshold = is_threshold(key)
            node_id = (threshold_id if threshold else rate_id)(key, fy)
            citation = table.legal_basis.get(key, "")
            node = Node(
                id=node_id,
                kind=NodeKind.THRESHOLD if threshold else NodeKind.RATE,
                label=f"{key} ({fy})",
                props={
                    "key": key,
                    "display_name": display_name(key),
                    "fiscal_year": fy,
                    "value": value,
                    "currency": table.currency,
                    "note": table.notes.get(key, ""),
                    "legal_basis": citation,
                },
                source=fy,
                unverified=key in table.unverified,
            )
            store.add_node(node)
            store.add_edge(Edge(node.id, EdgeKind.RATED_FOR, tax_node.id))
            store.add_edge(Edge(node.id, EdgeKind.EFFECTIVE_IN, fy_node.id))

            klass = class_for_key(key)
            if klass:
                class_node = Node(
                    id=class_id(klass),
                    kind=NodeKind.TAXPAYER_CLASS,
                    label=_CLASS_LABELS.get(klass, klass.replace("_", " ").title()),
                    props={"name": klass},
                )
                store.add_node(class_node)
                store.add_edge(Edge(node.id, EdgeKind.APPLIES_TO, class_node.id))

            if citation:
                parts = _split_citation(citation)
                prov = Node(
                    id=provision_id(citation),
                    kind=NodeKind.PROVISION,
                    label=citation,
                    props=parts,
                    source=fy,
                )
                store.add_node(prov)
                store.add_edge(Edge(node.id, EdgeKind.IMPOSED_BY, prov.id))

            by_key.setdefault(key, []).append((fy, node.id))

    _wire_supersedes(store, by_key)
    _wire_gates(store, by_key)
    _wire_computed_on(store, by_key)
    logger.info("graph: built %s", store.stats())
    return store


def _wire_supersedes(store: InMemoryGraphStore, by_key: dict[str, list[tuple[str, str]]]) -> None:
    """Chain each key's figures newest → oldest.

    The direction is deliberate: ``SUPERSEDES`` points from the newer
    figure at the one it replaced, so walking it answers "and before
    that?" — which is the shape of the question a taxpayer asks about a
    past period.
    """
    for entries in by_key.values():
        ordered = sorted(
            entries,
            key=lambda e: (parse_fiscal_year_start(e[0]) or __import__("datetime").date.min),
        )
        for older, newer in zip(ordered, ordered[1:], strict=False):
            store.add_edge(
                Edge(
                    newer[1],
                    EdgeKind.SUPERSEDES,
                    older[1],
                    props={"from": newer[0], "replaced": older[0]},
                )
            )


def _wire_gates(store: InMemoryGraphStore, by_key: dict[str, list[tuple[str, str]]]) -> None:
    """Point each threshold at the rate it gates, within its own year.

    ``rental_tax_individual_threshold`` gates ``rental_tax_individual``:
    below it the rate does not bite, which is a different answer from
    the rate itself and the one an answer most often omits.
    """
    for key, entries in by_key.items():
        if not is_threshold(key):
            continue
        for marker in _THRESHOLD_MARKERS:
            base_key = key.replace(marker, "").rstrip("_")
            if base_key == key or base_key not in by_key:
                continue
            years = dict(by_key[base_key])
            for fy, node_id in entries:
                target = years.get(fy)
                if target:
                    store.add_edge(Edge(node_id, EdgeKind.GATES, target))
            break


def _wire_computed_on(store: InMemoryGraphStore, by_key: dict[str, list[tuple[str, str]]]) -> None:
    """Record which charges stack on a base that includes another.

    VAT on an import is charged on the duty-inclusive value, so duty
    and VAT cannot be worked out independently and added. As an edge
    the system can apply and cite it; as prose in a prompt it is a
    suggestion.
    """
    for dependent, base in _COMPUTED_ON:
        dep_years = dict(by_key.get(dependent, []))
        base_years = dict(by_key.get(base, []))
        for fy, dep_id in dep_years.items():
            base_id = base_years.get(fy)
            if base_id:
                store.add_edge(
                    Edge(dep_id, EdgeKind.COMPUTED_ON, base_id, props={"fiscal_year": fy})
                )


def build_summary() -> dict[str, Any]:
    """Counts plus the joins that exist, for the tool and for tests."""
    store = build_graph()
    stats = store.stats()
    edges = store.to_dict()["edges"]
    by_edge: dict[str, int] = {}
    for edge in edges:
        by_edge[edge["kind"]] = by_edge.get(edge["kind"], 0) + 1
    return {"nodes": stats["nodes"], "edges": stats["edges"], "by_edge_kind": by_edge}
