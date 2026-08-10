"""Graph storage behind a seam, with a zero-dependency default.

## Why not Kùzu

``docs/NEXTGEN_ARCHITECTURE_PROPOSAL_2026.md`` proposed an embedded
Kùzu database. Building it made the trade look worse than it read.

The statutory graph is **curated, not derived**: it is a projection of
the effective-dated rate tables plus, later, reviewed extractions. That
puts it in the hundreds of nodes, not the millions. Kùzu would add a
native wheel to a slim Crane Cloud image and to a mobile bundle already
capped at 800 MB, in exchange for query planning over a dataset that
fits in a few hundred kilobytes of JSON and traverses fully in
microseconds.

So the default backend is a plain adjacency index over a versioned JSON
document — no dependency, no native code, byte-identical rebuilds, and
it drops into the offline bundle as one small file.

The **seam** is the part that matters. :class:`GraphStore` is the
interface every caller uses, matching the pattern the audit ledger and
the Postgres mirror already follow here. When the graph outgrows this —
millions of extracted provisions, or a query the adjacency index cannot
answer in bounded time — a Kùzu or Neo4j backend implements the same
interface and no caller changes.

The size at which that becomes true is a measurement, not a guess:
:meth:`InMemoryGraphStore.stats` reports node and edge counts, and
``test_graph_store`` asserts traversal stays inside its budget.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Protocol

from .schema import Edge, EdgeKind, Node, NodeKind

logger = logging.getLogger(__name__)

#: Hard ceiling on how far a traversal may walk, whatever a caller asks.
#: Statutory joins are shallow — rate → class → provision → year is
#: three — and an unbounded walk over a densely linked hub node is how a
#: graph query becomes a latency incident on the request path.
MAX_HOPS = 3

#: Ceiling on nodes returned by one traversal, for the same reason.
MAX_NODES = 200


class GraphStore(Protocol):
    """What every graph backend must provide.

    Deliberately narrow: add, look up, walk, persist. Anything richer
    belongs in :mod:`app.graph.query`, where it is written once against
    this interface rather than reimplemented per backend.
    """

    def add_node(self, node: Node) -> None: ...

    def add_edge(self, edge: Edge) -> None: ...

    def get(self, node_id: str) -> Node | None: ...

    def by_kind(self, kind: NodeKind) -> list[Node]: ...

    def neighbours(
        self, node_id: str, kinds: tuple[EdgeKind, ...] = (), outgoing: bool = True
    ) -> list[tuple[Edge, Node]]: ...

    def stats(self) -> dict[str, int]: ...


class InMemoryGraphStore:
    """Adjacency-indexed store over a JSON document.

    Nodes are unique by id and idempotent to re-add, so a rebuild is
    safe to run over an existing store. Edges are deduplicated on
    ``(src, kind, dst)`` for the same reason — an ingestion that runs
    twice must not double the graph.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        self._by_kind: dict[NodeKind, list[str]] = defaultdict(list)
        self._edge_keys: set[tuple[str, str, str]] = set()

    # -- writes --------------------------------------------------------
    def add_node(self, node: Node) -> None:
        if node.id in self._nodes:
            return
        self._nodes[node.id] = node
        self._by_kind[node.kind].append(node.id)

    def add_edge(self, edge: Edge) -> None:
        key = (edge.src, edge.kind.value, edge.dst)
        if key in self._edge_keys:
            return
        # An edge to a node that does not exist would make traversal
        # results depend on insertion order. Refuse it loudly instead.
        if edge.src not in self._nodes or edge.dst not in self._nodes:
            logger.warning(
                "graph: dropping edge %s -%s-> %s (endpoint missing)",
                edge.src, edge.kind.value, edge.dst,
            )
            return
        self._edge_keys.add(key)
        self._out[edge.src].append(edge)
        self._in[edge.dst].append(edge)

    # -- reads ---------------------------------------------------------
    def get(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def by_kind(self, kind: NodeKind) -> list[Node]:
        return [self._nodes[i] for i in self._by_kind.get(kind, [])]

    def neighbours(
        self, node_id: str, kinds: tuple[EdgeKind, ...] = (), outgoing: bool = True
    ) -> list[tuple[Edge, Node]]:
        edges = (self._out if outgoing else self._in).get(node_id, [])
        out: list[tuple[Edge, Node]] = []
        for edge in edges:
            if kinds and edge.kind not in kinds:
                continue
            other = self._nodes.get(edge.dst if outgoing else edge.src)
            if other is not None:
                out.append((edge, other))
        return out

    def walk(
        self,
        seeds: list[str],
        *,
        hops: int = 2,
        kinds: tuple[EdgeKind, ...] = (),
        limit: int = MAX_NODES,
    ) -> list[Node]:
        """Breadth-first walk from *seeds*, bounded twice over.

        Both bounds are enforced here rather than trusted from the
        caller: a traversal runs on the request path, and the caller
        that gets it wrong is the one under load.
        """
        hops = max(0, min(hops, MAX_HOPS))
        limit = max(1, min(limit, MAX_NODES))

        seen: set[str] = set()
        found: list[Node] = []
        queue: deque[tuple[str, int]] = deque()
        for seed in seeds:
            if seed in self._nodes and seed not in seen:
                seen.add(seed)
                queue.append((seed, 0))
                found.append(self._nodes[seed])

        while queue and len(found) < limit:
            node_id, depth = queue.popleft()
            if depth >= hops:
                continue
            # Both directions: "which rates cite this provision" is an
            # incoming walk, and it is a question taxpayers ask.
            for outgoing in (True, False):
                for _edge, other in self.neighbours(node_id, kinds, outgoing=outgoing):
                    if other.id in seen:
                        continue
                    seen.add(other.id)
                    found.append(other)
                    queue.append((other.id, depth + 1))
                    if len(found) >= limit:
                        break
        return found

    def stats(self) -> dict[str, int]:
        counts = {f"nodes_{k.value}": len(v) for k, v in self._by_kind.items()}
        counts["nodes"] = len(self._nodes)
        counts["edges"] = len(self._edge_keys)
        return counts

    # -- persistence ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialised form. Sorted, so a rebuild diffs cleanly."""
        return {
            "version": 1,
            "nodes": [self._nodes[i].to_dict() for i in sorted(self._nodes)],
            "edges": sorted(
                (e.to_dict() for edges in self._out.values() for e in edges),
                key=lambda e: (e["src"], e["kind"], e["dst"]),
            ),
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1, sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> InMemoryGraphStore:
        store = cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        for node in raw.get("nodes", []):
            store.add_node(Node.from_dict(node))
        for edge in raw.get("edges", []):
            store.add_edge(Edge.from_dict(edge))
        return store

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> InMemoryGraphStore:
        store = cls()
        for node in raw.get("nodes", []):
            store.add_node(Node.from_dict(node))
        for edge in raw.get("edges", []):
            store.add_edge(Edge.from_dict(edge))
        return store
