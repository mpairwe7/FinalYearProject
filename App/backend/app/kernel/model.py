"""The kernel's schema, defined once (ADR-0033).

Three artefacts are generated from this module and must agree because they
share it:

* a Neo4j Ontology Graph Specification 1.1.0 document
  (:func:`app.kernel.schema_export.to_ogs`);
* W3C SHACL shapes over an RDF view of the graph
  (:func:`app.kernel.schema_export.to_shacl`);
* the runtime invariant checks (:mod:`app.kernel.validate`).

The node and edge vocabulary itself stays in :mod:`app.graph.schema`; this
module says what each kind must carry and how many of each relationship a
node may have.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..graph.schema import FACT_KINDS, EdgeKind, NodeKind

#: Version of this schema. Bump it when a node kind, property or cardinality
#: changes; a kernel release records it.
MODEL_VERSION = "1.0.0"

MODEL_ID = "ura-statutory-knowledge-kernel"
MODEL_NAME = "URA Statutory Knowledge Kernel"

#: ``FY2026-27``. Fiscal years run 1 July to 30 June.
FISCAL_YEAR_PATTERN = r"^FY\d{4}-\d{2}$"
ISO_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


@dataclass(frozen=True)
class PropertySpec:
    name: str
    #: A Neo4j Ontology Graph Specification type (``STRING``, ``BOOLEAN``, ``ANY``).
    type: str
    required: bool = False
    description: str = ""
    pattern: str = ""


@dataclass(frozen=True)
class NodeSpec:
    kind: NodeKind
    #: The Neo4j label, and the RDF class local name.
    label: str
    description: str
    properties: tuple[PropertySpec, ...]


@dataclass(frozen=True)
class RelationshipSpec:
    kind: EdgeKind
    src: NodeKind
    dst: NodeKind
    #: How many of this relationship each ``src`` node has. ``None`` is unbounded.
    min_count: int = 0
    max_count: int | None = None
    description: str = ""

    @property
    def id(self) -> str:
        return f"{self.src.value}_{self.kind.value}_{self.dst.value}"


_COMMON = (
    PropertySpec("id", "STRING", True, "Stable id derived from the content, never random."),
    PropertySpec("label", "STRING", True, "A human-readable label."),
    PropertySpec("source", "STRING", False, "The fiscal-year table the node came from."),
    PropertySpec("unverified", "BOOLEAN", True, "True when the figure is not reconciled against legislation."),
)

_FACT = (
    PropertySpec("key", "STRING", True, "The rate-table key."),
    PropertySpec("display_name", "STRING", True, "The name used in answers."),
    PropertySpec("fiscal_year", "STRING", True, "The fiscal year in force.", FISCAL_YEAR_PATTERN),
    PropertySpec("value", "ANY", False, "The figure: a number, a band list or a rule string."),
    PropertySpec("unit", "STRING", False, "ratio, UGX, USD, days, hours, years, currency_points, bands."),
    PropertySpec("currency", "STRING", False, "The table's currency."),
    PropertySpec("note", "STRING", False, "Explanatory note from the table."),
    PropertySpec("legal_basis", "STRING", False, "The citation the table records."),
    PropertySpec("uncited", "BOOLEAN", True, "True when no legal basis is recorded; the node is then unverified."),
)

_FACT_DESCRIPTIONS = {
    NodeKind.RATE: "A rate or band set, effective-dated.",
    NodeKind.THRESHOLD: "A monetary or numeric gate on whether a rule applies.",
    NodeKind.OBLIGATION: "A deadline or procedural time limit.",
    NodeKind.PENALTY: "A penal tax, penalty figure or interest rate charged for a failure.",
    NodeKind.FEE: "A fixed fee for a service.",
    NodeKind.PARAMETER: "A number a computation needs that is none of the other kinds.",
}

_LABELS = {
    NodeKind.TAX_TYPE: "TaxType",
    NodeKind.TAXPAYER_CLASS: "TaxpayerClass",
    NodeKind.RATE: "Rate",
    NodeKind.THRESHOLD: "Threshold",
    NodeKind.PROVISION: "Provision",
    NodeKind.FISCAL_YEAR: "FiscalYear",
    NodeKind.OBLIGATION: "Obligation",
    NodeKind.PENALTY: "Penalty",
    NodeKind.FEE: "Fee",
    NodeKind.PARAMETER: "Parameter",
}

#: Fact kinds in a fixed order, so generated documents are byte-stable.
ORDERED_FACT_KINDS: tuple[NodeKind, ...] = tuple(k for k in NodeKind if k in FACT_KINDS)


def _node_specs() -> tuple[NodeSpec, ...]:
    specs = [
        NodeSpec(NodeKind.TAX_TYPE, _LABELS[NodeKind.TAX_TYPE], "A tax or charge.",
                 (*_COMMON, PropertySpec("name", "STRING", True, "Stable tax name."))),
        NodeSpec(NodeKind.TAXPAYER_CLASS, _LABELS[NodeKind.TAXPAYER_CLASS], "Who a figure applies to.",
                 (*_COMMON, PropertySpec("name", "STRING", True, "Stable class name."))),
        NodeSpec(NodeKind.PROVISION, _LABELS[NodeKind.PROVISION], "An Act and section: the authority for a figure.",
                 (*_COMMON, PropertySpec("act", "STRING", True, "The Act."),
                  PropertySpec("section", "STRING", False, "The section, schedule or item."))),
        NodeSpec(NodeKind.FISCAL_YEAR, _LABELS[NodeKind.FISCAL_YEAR], "A fiscal year: 1 July to 30 June.",
                 (*_COMMON,
                  PropertySpec("effective_from", "STRING", True, "First day in force.", ISO_DATE_PATTERN),
                  PropertySpec("effective_to", "STRING", False, "Last day in force, if known."),
                  PropertySpec("status", "STRING", True, "confirmed or provisional."))),
    ]
    for kind in ORDERED_FACT_KINDS:
        specs.append(NodeSpec(kind, _LABELS[kind], _FACT_DESCRIPTIONS[kind], (*_COMMON, *_FACT)))
    return tuple(specs)


def _relationship_specs() -> tuple[RelationshipSpec, ...]:
    rels: list[RelationshipSpec] = []
    for kind in ORDERED_FACT_KINDS:
        rels += [
            RelationshipSpec(EdgeKind.RATED_FOR, kind, NodeKind.TAX_TYPE, 1, 1, "The tax this figure belongs to."),
            RelationshipSpec(EdgeKind.EFFECTIVE_IN, kind, NodeKind.FISCAL_YEAR, 1, 1, "When it was in force."),
            RelationshipSpec(EdgeKind.IMPOSED_BY, kind, NodeKind.PROVISION, 0, 1,
                             "The statutory authority. Required unless the figure is unverified."),
            RelationshipSpec(EdgeKind.APPLIES_TO, kind, NodeKind.TAXPAYER_CLASS, 0, 1, "Who it applies to."),
            RelationshipSpec(EdgeKind.SUPERSEDES, kind, kind, 0, 1, "The figure it replaced."),
        ]
    rels += [
        RelationshipSpec(EdgeKind.GATES, NodeKind.THRESHOLD, NodeKind.RATE, 0, None, "Below it the rate does not bite."),
        RelationshipSpec(EdgeKind.COMPUTED_ON, NodeKind.RATE, NodeKind.RATE, 0, None, "Charged on a base including the other."),
        RelationshipSpec(EdgeKind.CROSS_REFERENCES, NodeKind.PROVISION, NodeKind.PROVISION, 0, None,
                         "A schedule cross-references its charging section."),
    ]
    return tuple(rels)


NODE_SPECS: tuple[NodeSpec, ...] = _node_specs()
RELATIONSHIP_SPECS: tuple[RelationshipSpec, ...] = _relationship_specs()
NODE_SPEC_BY_KIND: dict[NodeKind, NodeSpec] = {spec.kind: spec for spec in NODE_SPECS}


def label_of(kind: NodeKind) -> str:
    return _LABELS[kind]


def relationships_from(kind: NodeKind) -> tuple[RelationshipSpec, ...]:
    return tuple(r for r in RELATIONSHIP_SPECS if r.src is kind)
