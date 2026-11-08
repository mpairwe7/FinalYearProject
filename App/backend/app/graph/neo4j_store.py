"""A Neo4j backend behind :class:`~app.graph.store.GraphStore` (ADR-0033, phase 3).

Off by default. ``GRAPH_STORE_BACKEND=neo4j`` selects it for the graph the
tools and fusion read (:func:`app.graph.get_graph`). The kernel's own store,
whose content hash is the release, stays in memory and is the source the Neo4j
copy is synced from, so a release still means the same bytes everywhere.

It is worth running only past the thresholds in ADR-0033: more than 50,000
nodes, traversal p99 above 5 ms, or a staff need for ad-hoc Cypher.
:func:`advise` measures the in-memory store against them.

Safety:

* Every statement is one of the constants in :data:`QUERIES`. Ids, kinds and
  properties travel as parameters, never as text in a statement. Labels and
  relationship types, which Cypher cannot parameterise, come only from the
  ``NodeKind`` and ``EdgeKind`` enums.
* Walks are bounded the same way as in memory: ``MAX_HOPS`` and ``MAX_NODES``.
* Properties are stored as JSON, so they round-trip as JSON does (a tuple of
  bands comes back as a list); a value JSON cannot hold fails the sync.
* Production refuses a connection without verified TLS (``neo4j+s://`` or
  ``bolt+s://``) or without credentials (:func:`production_errors`).
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .schema import Edge, EdgeKind, Node, NodeKind
from .store import MAX_HOPS, MAX_NODES

if TYPE_CHECKING:
    from .store import InMemoryGraphStore

NODE_THRESHOLD = 50_000
P99_THRESHOLD_MS = 5.0
TLS_SCHEMES = ("neo4j+s://", "bolt+s://")

#: Labels and relationship types, from the enums only.
NODE_LABELS = {kind: "".join(part.title() for part in kind.value.split("_")) for kind in NodeKind}
REL_TYPES = {kind: kind.value.upper() for kind in EdgeKind}

Q_CONSTRAINT = "CREATE CONSTRAINT kernel_node_id IF NOT EXISTS FOR (n:Kernel) REQUIRE n.id IS UNIQUE"
Q_GET = "MATCH (n:Kernel {id: $id}) RETURN n"
Q_BY_KIND = "MATCH (n:Kernel) WHERE n.kind = $kind RETURN n ORDER BY n.id"
Q_OUT = (
    "MATCH (a:Kernel {id: $id})-[r]->(b:Kernel) WHERE size($kinds) = 0 OR r.kind IN $kinds "
    "RETURN r, a.id AS src, b.id AS dst, b ORDER BY r.kind, b.id"
)
Q_IN = (
    "MATCH (a:Kernel {id: $id})<-[r]-(b:Kernel) WHERE size($kinds) = 0 OR r.kind IN $kinds "
    "RETURN r, b.id AS src, a.id AS dst, b ORDER BY r.kind, b.id"
)
Q_COUNT_NODES = "MATCH (n:Kernel) RETURN n.kind AS kind, count(n) AS n"
Q_COUNT_EDGES = "MATCH (:Kernel)-[r]->(:Kernel) RETURN count(r) AS n"
Q_CLEAR = "MATCH (n:Kernel) DETACH DELETE n"
#: One MERGE per node label and relationship type: the label or type is
#: fixed text from the enums, everything else a parameter.
Q_MERGE_NODE = {
    kind: (
        f"UNWIND $rows AS row MERGE (n:Kernel {{id: row.id}}) SET n:{label}, n.kind = row.kind, "
        "n.label = row.label, n.props_json = row.props_json, n.source = row.source, n.unverified = row.unverified"
    )
    for kind, label in NODE_LABELS.items()
}
Q_MERGE_EDGE = {
    kind: (
        "UNWIND $rows AS row MATCH (a:Kernel {id: row.src}), (b:Kernel {id: row.dst}) "
        f"MERGE (a)-[r:{rel} {{kind: row.kind}}]->(b) SET r.props_json = row.props_json"
    )
    for kind, rel in REL_TYPES.items()
}
QUERIES = frozenset(
    {Q_CONSTRAINT, Q_GET, Q_BY_KIND, Q_OUT, Q_IN, Q_COUNT_NODES, Q_COUNT_EDGES, Q_CLEAR}
    | set(Q_MERGE_NODE.values())
    | set(Q_MERGE_EDGE.values())
)


class CypherNotAllowed(RuntimeError):
    """A statement outside :data:`QUERIES` was about to run."""


def _node(record: Any) -> Node:
    data = dict(record)
    return Node(
        id=data["id"],
        kind=NodeKind(data["kind"]),
        label=data.get("label", ""),
        props=json.loads(data.get("props_json") or "{}"),
        source=data.get("source", ""),
        unverified=bool(data.get("unverified", False)),
    )


class Neo4jGraphStore:
    """``GraphStore`` over a Neo4j driver (``neo4j.Driver`` or anything with ``execute_query``)."""

    def __init__(self, driver: Any, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    @classmethod
    def from_env(cls) -> Neo4jGraphStore:
        import neo4j  # optional dependency: only this backend needs it

        uri = os.environ["NEO4J_URI"]
        auth = (os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
        return cls(neo4j.GraphDatabase.driver(uri, auth=auth), database=os.getenv("NEO4J_DATABASE") or None)

    def _run(self, query: str, **params: Any) -> list[Any]:
        if query not in QUERIES:
            raise CypherNotAllowed("only the module's fixed, parameterised statements run")
        kwargs = {"database_": self._database} if self._database else {}
        records, _summary, _keys = self._driver.execute_query(query, params, **kwargs)
        return list(records)

    # -- writes ----------------------------------------------------------

    def add_node(self, node: Node) -> None:
        self._run(Q_MERGE_NODE[node.kind], rows=[self._node_row(node)])

    def add_edge(self, edge: Edge) -> None:
        self._run(Q_MERGE_EDGE[edge.kind], rows=[self._edge_row(edge)])

    @staticmethod
    def _node_row(node: Node) -> dict[str, Any]:
        return {"id": node.id, "kind": node.kind.value, "label": node.label,
                "props_json": json.dumps(node.props, sort_keys=True), "source": node.source,
                "unverified": node.unverified}

    @staticmethod
    def _edge_row(edge: Edge) -> dict[str, Any]:
        return {"src": edge.src, "dst": edge.dst, "kind": edge.kind.value,
                "props_json": json.dumps(edge.props, sort_keys=True)}

    def sync_from(self, store: InMemoryGraphStore, *, batch: int = 500) -> dict[str, int]:
        """Replace the Neo4j copy with *store*, in batches per label and type."""
        raw = store.to_dict()
        self._run(Q_CONSTRAINT)
        self._run(Q_CLEAR)
        for kind in NodeKind:
            rows = [self._node_row(Node.from_dict(n)) for n in raw["nodes"] if n["kind"] == kind.value]
            for start in range(0, len(rows), batch):
                self._run(Q_MERGE_NODE[kind], rows=rows[start:start + batch])
        for kind in EdgeKind:
            rows = [self._edge_row(Edge.from_dict(e)) for e in raw["edges"] if e["kind"] == kind.value]
            for start in range(0, len(rows), batch):
                self._run(Q_MERGE_EDGE[kind], rows=rows[start:start + batch])
        return {"nodes": len(raw["nodes"]), "edges": len(raw["edges"])}

    # -- reads -----------------------------------------------------------

    def get(self, node_id: str) -> Node | None:
        records = self._run(Q_GET, id=node_id)
        return _node(records[0]["n"]) if records else None

    def by_kind(self, kind: NodeKind) -> list[Node]:
        return [_node(r["n"]) for r in self._run(Q_BY_KIND, kind=kind.value)]

    def neighbours(
        self, node_id: str, kinds: tuple[EdgeKind, ...] = (), outgoing: bool = True
    ) -> list[tuple[Edge, Node]]:
        query = Q_OUT if outgoing else Q_IN
        out = []
        for record in self._run(query, id=node_id, kinds=[k.value for k in kinds]):
            rel = dict(record["r"])
            edge = Edge(src=record["src"], kind=EdgeKind(rel["kind"]), dst=record["dst"],
                        props=json.loads(rel.get("props_json") or "{}"))
            out.append((edge, _node(record["b"])))
        return out

    def walk(self, seeds: list[str], *, hops: int = 2, kinds: tuple[EdgeKind, ...] = (),
             limit: int = MAX_NODES) -> list[Node]:
        """The in-memory store's breadth-first walk, with the same two bounds."""
        hops = max(0, min(hops, MAX_HOPS))
        limit = max(1, min(limit, MAX_NODES))
        seen: set[str] = set()
        found: list[Node] = []
        queue: deque[tuple[str, int]] = deque()
        for seed in seeds:
            node = self.get(seed) if seed not in seen else None
            if node is not None:
                seen.add(seed)
                queue.append((seed, 0))
                found.append(node)
        while queue and len(found) < limit:
            node_id, depth = queue.popleft()
            if depth >= hops:
                continue
            for outgoing in (True, False):
                for _edge, other in self.neighbours(node_id, kinds, outgoing=outgoing):
                    if other.id in seen:
                        continue
                    seen.add(other.id)
                    found.append(other)
                    queue.append((other.id, depth + 1))
                    if len(found) >= limit:
                        return found
        return found

    def stats(self) -> dict[str, int]:
        counts = {f"nodes_{r['kind']}": int(r["n"]) for r in self._run(Q_COUNT_NODES)}
        counts["nodes"] = sum(counts.values())
        edges = self._run(Q_COUNT_EDGES)
        counts["edges"] = int(edges[0]["n"]) if edges else 0
        return counts


# --------------------------------------------------------------------------- selection and gates


def backend() -> str:
    return (os.getenv("GRAPH_STORE_BACKEND") or "memory").strip().lower()


def production_errors() -> list[str]:
    """Startup errors for the graph backend in production (empty when it is in memory)."""
    name = backend()
    if name == "memory":
        return []
    if name != "neo4j":
        return [f"G506: GRAPH_STORE_BACKEND={name!r} is not a known backend (memory or neo4j)."]
    errors = []
    uri = os.getenv("NEO4J_URI", "")
    if not uri.startswith(TLS_SCHEMES):
        errors.append("G506: GRAPH_STORE_BACKEND=neo4j requires NEO4J_URI with verified TLS (neo4j+s:// or bolt+s://).")
    for name in ("NEO4J_USER", "NEO4J_PASSWORD"):
        if not os.getenv(name):
            errors.append(f"G506: GRAPH_STORE_BACKEND=neo4j requires {name}.")
    return errors


def select(built: InMemoryGraphStore) -> Any:
    """The store to serve: *built* in memory, or a Neo4j copy synced from it.

    A Neo4j failure falls back to memory, logged: the in-memory graph holds
    the same release, so an unreachable database must not take answers down.
    """
    if backend() != "neo4j":
        return built
    import logging

    try:
        store = Neo4jGraphStore.from_env()
        store.sync_from(built)
        return store
    except Exception:
        logging.getLogger(__name__).warning("Neo4j graph backend unavailable; serving in memory", exc_info=True)
        return built


# --------------------------------------------------------------------------- threshold advisor


@dataclass(frozen=True)
class Advice:
    recommended: bool
    nodes: int
    p99_ms: float
    reasons: list[str]


def advise(store: Any, *, samples: int = 200, adhoc_cypher_needed: bool = False) -> Advice:
    """Whether the ADR-0033 thresholds for a Neo4j backend are met, measured on *store*."""
    stats = store.stats()
    nodes = int(stats.get("nodes", 0))
    seeds = [n.id for n in store.by_kind(NodeKind.TAX_TYPE)] or [n.id for n in store.by_kind(NodeKind.RATE)]
    timings = []
    for index in range(samples):
        seed = seeds[index % len(seeds)] if seeds else ""
        started = time.perf_counter()
        store.walk([seed], hops=2)
        timings.append((time.perf_counter() - started) * 1000)
    p99 = statistics.quantiles(timings, n=100)[98] if len(timings) >= 2 else (timings[0] if timings else 0.0)
    reasons = []
    if nodes > NODE_THRESHOLD:
        reasons.append(f"{nodes} nodes exceeds {NODE_THRESHOLD}")
    if p99 > P99_THRESHOLD_MS:
        reasons.append(f"traversal p99 {p99:.2f} ms exceeds {P99_THRESHOLD_MS} ms")
    if adhoc_cypher_needed:
        reasons.append("staff need ad-hoc Cypher")
    return Advice(bool(reasons), nodes, round(p99, 3), reasons)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse
    from dataclasses import asdict

    from .build import build_graph

    parser = argparse.ArgumentParser(description="Neo4j backend threshold advisor (ADR-0033).")
    parser.add_argument("--adhoc-cypher", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(asdict(advise(build_graph(), adhoc_cypher_needed=args.adhoc_cypher)), indent=2))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
