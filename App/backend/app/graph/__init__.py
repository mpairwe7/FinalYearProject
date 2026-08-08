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

_GRAPH: InMemoryGraphStore | None = None


def get_graph() -> InMemoryGraphStore:
    """Process-wide graph, built on first use.

    Built rather than loaded: the projection is deterministic and takes
    milliseconds over two rate tables, so a build at startup cannot go
    stale against the tables the calculators answer from. A file-backed
    load is what the offline bundle uses, where the tables are not
    present.
    """
    global _GRAPH
    if _GRAPH is None:
        from .build import build_graph

        _GRAPH = build_graph()
    return _GRAPH


def reset_graph() -> None:
    """Drop the cached graph — for tests and for a rate-table reload."""
    global _GRAPH
    _GRAPH = None


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
