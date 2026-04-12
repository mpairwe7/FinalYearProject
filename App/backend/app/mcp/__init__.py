"""Model Context Protocol (MCP) — 2026 standard interface.

This package is the abstraction layer between the agent runtime
and the underlying tool implementations.  Today every tool lives
in the in-process :mod:`app.tools` registry; Phase 15 full lands
FastMCP 0.4+ servers that run out-of-process and speak MCP stdio
or HTTP/SSE.  The interface below lets us migrate tools one at a
time without touching the agent code.

Key abstractions:

- :class:`MCPClient` — the handle the agent layer uses.  Hides
  transport (in-process today, stdio/HTTP later).
- :class:`ToolRAGSelector` — retrieves the top-k relevant tools
  per query from an embedding index over tool descriptions,
  replacing the "paste every tool schema into every prompt" anti-
  pattern (see docs/URA_Chatbot_Roadmap_2026_Enhanced.md §4).

Design invariants:

- **Zero new runtime dependencies** — Tool RAG uses a lightweight
  hash-based similarity today; it upgrades cleanly to a real
  sentence-transformers embedder when the embedding model is
  available.
- **Security trimming** — every selector call takes an
  ``AuthContext`` and filters tools by risk tier × user role ×
  granted consent purposes before retrieval.
- **Audit-ready** — every MCP call yields a structured record
  that Phase 21's audit ledger consumes.

Feature flag: ``FLAG_TOOL_RAG`` default false.
"""

from __future__ import annotations

from .client import MCPCallResult, MCPClient, get_client
from .tool_rag import ToolRAGSelector, ToolSelection

__all__ = [
    "MCPCallResult",
    "MCPClient",
    "ToolRAGSelector",
    "ToolSelection",
    "get_client",
]
