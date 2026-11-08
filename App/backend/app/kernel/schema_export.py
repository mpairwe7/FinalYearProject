"""Generate the kernel's schema artefacts from :mod:`app.kernel.model` (ADR-0033).

* :func:`to_ogs`: a Neo4j Ontology Graph Specification 1.1.0 document,
  checked by :func:`validate_ogs` against the vendored published JSON Schema.
* :func:`to_shacl`: W3C SHACL shapes in Turtle.
* :func:`to_rdf`: the graph's instances in Turtle, the data the shapes test.
* :func:`shacl_validate`: runs pySHACL when it is installed (CI installs it;
  the production image does not need it, because only validated releases ship).

Run ``python -m app.kernel.schema_export --out DIR`` to write all three.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..graph.schema import FACT_KINDS, NodeKind
from .model import (
    MODEL_ID,
    MODEL_NAME,
    MODEL_VERSION,
    NODE_SPECS,
    RELATIONSHIP_SPECS,
    label_of,
    relationships_from,
)

if TYPE_CHECKING:
    from ..graph.store import InMemoryGraphStore

OGS_SCHEMA_URI = "https://neo4j.com/docs/ontology-graph-spec/1.1.0/schema.json"
_VENDORED_SCHEMA = Path(__file__).parent / "vendor" / "ontology-graph-spec-1.1.0.schema.json"

#: RDF namespaces. URNs, because the kernel names no web resource it controls.
VOCAB = "urn:ura-kernel:vocab#"
NODE_NS = "urn:ura-kernel:node:"

_XSD = {"STRING": "xsd:string", "BOOLEAN": "xsd:boolean", "INTEGER": "xsd:integer", "FLOAT": "xsd:decimal"}


# --------------------------------------------------------------------------- OGS


def to_ogs(version: str = MODEL_VERSION) -> dict[str, Any]:
    """The kernel schema as a Neo4j Ontology Graph Specification document."""
    nodes: dict[str, Any] = {}
    for spec in NODE_SPECS:
        properties: dict[str, Any] = {}
        for prop in spec.properties:
            entry: dict[str, Any] = {"type": prop.type, "description": prop.description}
            if prop.required:
                entry["must_exist"] = True
            if prop.name == "id":
                entry["key"] = True
            if prop.pattern:
                entry["pattern"] = prop.pattern
            properties[prop.name] = entry
        nodes[spec.kind.value] = {"label": spec.label, "description": spec.description, "properties": properties}
    relationships: dict[str, Any] = {}
    for rel in RELATIONSHIP_SPECS:
        relationships[rel.id] = {
            "type": rel.kind.value.upper(),
            "description": rel.description,
            "from": {"node": rel.src.value, "min_count": rel.min_count, "max_count": rel.max_count},
            "to": {"node": rel.dst.value},
        }
    return {
        "schema": OGS_SCHEMA_URI,
        "version": version,
        "id": MODEL_ID,
        "name": MODEL_NAME,
        "description": (
            "Effective-dated Ugandan tax rates, thresholds, deadlines, penalties, fees and their statutory "
            "provisions, projected from URA's rate tables (ADR-0033)."
        ),
        "nodes": nodes,
        "relationships": relationships,
    }


@lru_cache(maxsize=1)
def _ogs_validator() -> Any:
    from jsonschema import Draft202012Validator

    schema = json.loads(_VENDORED_SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def validate_ogs(document: dict[str, Any]) -> list[str]:
    """Errors against the published Ontology Graph Specification schema; empty when valid."""
    return sorted(
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in _ogs_validator().iter_errors(document)
    )


# --------------------------------------------------------------------------- SHACL


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f'"{value}"^^xsd:integer'
    if isinstance(value, float):
        return f'"{value!r}"^^xsd:decimal'
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True)
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _prefixes() -> str:
    return (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
        f"@prefix k: <{VOCAB}> .\n\n"
    )


def to_shacl() -> str:
    """SHACL shapes, one closed node shape per kind (ADR-0033)."""
    out = [_prefixes()]
    for spec in NODE_SPECS:
        lines = [f"k:{spec.label}Shape a sh:NodeShape ;", f"  sh:targetClass k:{spec.label} ;"]
        allowed = ["rdf:type"]
        for prop in spec.properties:
            parts = [f"sh:path k:{prop.name}"]
            if prop.required:
                parts.append("sh:minCount 1")
            parts.append("sh:maxCount 1")
            if prop.type in _XSD:
                parts.append(f"sh:datatype {_XSD[prop.type]}")
            if prop.pattern:
                parts.append(f"sh:pattern {_literal(prop.pattern)}")
            lines.append(f"  sh:property [ {' ; '.join(parts)} ] ;")
            allowed.append(f"k:{prop.name}")
        for rel in relationships_from(spec.kind):
            parts = [f"sh:path k:{rel.kind.value.upper()}", f"sh:class k:{label_of(rel.dst)}"]
            if rel.min_count:
                parts.append(f"sh:minCount {rel.min_count}")
            if rel.max_count is not None:
                parts.append(f"sh:maxCount {rel.max_count}")
            lines.append(f"  sh:property [ {' ; '.join(parts)} ] ;")
            allowed.append(f"k:{rel.kind.value.upper()}")
        if spec.kind in FACT_KINDS:
            # Provenance: a figure either names its provision or says it is unverified.
            lines.append(
                "  sh:or ( [ sh:property [ sh:path k:IMPOSED_BY ; sh:minCount 1 ] ] "
                "[ sh:property [ sh:path k:unverified ; sh:hasValue true ] ] ) ;"
            )
        lines.append("  sh:closed true ;")
        lines.append(f"  sh:ignoredProperties ( {' '.join(sorted(set(allowed)))} ) .")
        out.append("\n".join(lines) + "\n\n")
    return "".join(out)


def _node_iri(node_id: str) -> str:
    return f"<{NODE_NS}{quote(node_id, safe='')}>"


def to_rdf(store: InMemoryGraphStore) -> str:
    """The graph as RDF instance data in Turtle, sorted so it diffs cleanly."""
    raw = store.to_dict()
    out = [_prefixes()]
    for node in raw["nodes"]:
        kind = NodeKind(node["kind"])
        triples = [f"a k:{label_of(kind)}", f"k:id {_literal(node['id'])}", f"k:label {_literal(node['label'])}",
                   f"k:unverified {_literal(bool(node.get('unverified')))}"]
        if node.get("source"):
            triples.append(f"k:source {_literal(node['source'])}")
        for name, value in sorted((node.get("props") or {}).items()):
            if value is None or value == "":
                continue
            triples.append(f"k:{name} {_literal(value)}")
        out.append(f"{_node_iri(node['id'])} " + " ;\n  ".join(triples) + " .\n")
    for edge in raw["edges"]:
        out.append(f"{_node_iri(edge['src'])} k:{edge['kind'].upper()} {_node_iri(edge['dst'])} .\n")
    return "".join(out)


def shacl_validate(store: InMemoryGraphStore) -> tuple[bool, str]:
    """``(conforms, report)`` from pySHACL. Raises ImportError when pySHACL is absent."""
    import pyshacl
    import rdflib

    data = rdflib.Graph().parse(data=to_rdf(store), format="turtle")
    shapes = rdflib.Graph().parse(data=to_shacl(), format="turtle")
    conforms, _graph, text = pyshacl.validate(data, shacl_graph=shapes, inference="none", advanced=True)
    return bool(conforms), str(text)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    from .functions import kernel_store

    parser = argparse.ArgumentParser(description="Write the kernel schema artefacts.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    document = to_ogs()
    errors = validate_ogs(document)
    (args.out / "kernel.ontology-graph-spec.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    (args.out / "kernel.shapes.ttl").write_text(to_shacl(), encoding="utf-8")
    (args.out / "kernel.data.ttl").write_text(to_rdf(kernel_store()), encoding="utf-8")
    for error in errors:
        print(f"OGS: {error}")  # noqa: T201 - CLI output
    return 1 if errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
