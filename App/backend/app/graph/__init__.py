"""Statutory knowledge graph — the joins flat retrieval cannot make.

See :mod:`app.graph.store` for why the default backend has no
dependencies, and :mod:`app.graph.build` for why it is projected from
the rate tables rather than extracted from prose.

Gated by ``FLAG_TAX_GRAPH`` (load and expose the tools) and
``FLAG_GRAPH_FUSION`` (let it reach an answer). Both default off.
"""

from __future__ import annotations

import logging

from .schema import Edge, EdgeKind, Node, NodeKind
from .store import GraphStore, InMemoryGraphStore

logger = logging.getLogger(__name__)

_GRAPH: GraphStore | None = None


def get_graph() -> GraphStore:
    """Process-wide graph, built on first use.

    Built rather than loaded: the projection is deterministic and takes
    milliseconds over two rate tables, so a build at startup cannot go
    stale against the tables the calculators answer from. A file-backed
    load is what the offline bundle uses, where the tables are not
    present.

    ``GRAPH_STORE_BACKEND=neo4j`` serves a Neo4j copy synced from the build
    (:mod:`app.graph.neo4j_store`, ADR-0033); the build stays the source.
    """
    global _GRAPH
    if _GRAPH is None:
        from .build import build_graph
        from .neo4j_store import select

        _GRAPH = select(build_graph())
    return _GRAPH


def reset_graph() -> None:
    """Drop the cached graph — for tests and for a rate-table reload.

    A Neo4j-backed store holds a driver connection pool, so it is closed.
    """
    global _GRAPH
    previous, _GRAPH = _GRAPH, None
    close = getattr(previous, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - a failed close must not stop the reset
            logger.warning("graph store close failed", exc_info=True)


def active_store_name() -> str:
    """The store serving now, for readiness: ``not loaded``, ``memory``, ``neo4j`` or a fallback."""
    if _GRAPH is None:
        return "not loaded"
    name = getattr(_GRAPH, "active_name", None)
    return name() if callable(name) else "memory"


__all__ = [
    "Edge",
    "EdgeKind",
    "GraphStore",
    "InMemoryGraphStore",
    "Node",
    "NodeKind",
    "get_graph",
    "reset_graph",
]
