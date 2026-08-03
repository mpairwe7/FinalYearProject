"""Model Context Protocol (MCP) — 2026-07-28 standard interface.

This package is the abstraction layer between the agent runtime and the
underlying tool implementations.  The agent layer never imports
:mod:`app.tools` directly; it goes through :class:`MCPClient`, so a tool
can move out of the process without any caller changing.

Key abstractions:

- :class:`MCPClient` — the handle the agent layer uses.  Routes each
  call to the transport bound to the tool's namespace, authorizes it
  against what the tool declares about itself, validates arguments
  against its JSON Schema, and guards the dispatch with a per-namespace
  circuit breaker and an idempotency replay cache.
- :mod:`.transport` — ``InProcessTransport`` (the default) and
  ``HttpTransport`` for a namespace bound to a deployed MCP server via
  ``MCP_SERVER_URL_<NAMESPACE>``.
- :mod:`.servers` — the out-of-process servers themselves.
  ``tax_calculator`` is live; see its README.
- :class:`ToolRAGSelector` — retrieves the top-k relevant tools per
  query from an index over tool descriptions, replacing the "paste every
  tool schema into every prompt" anti-pattern.

Design invariants:

- **Zero new runtime dependencies** — the wire protocol is JSON-RPC 2.0
  over HTTP or stdio, implemented directly; Tool RAG uses token overlap
  until a dense embedder is injected.
- **Declaration-driven authorization** — a tool states its required
  consent scopes and roles; the policy denies by default rather than
  inferring permissions from the tool's name.
- **Audit-ready** — every call yields an :class:`MCPCallResult` whose
  ``to_audit_dict()`` hashes arguments and results for the audit ledger.

Feature flag: ``FLAG_TOOL_RAG`` default false.
"""

from __future__ import annotations

from .client import MCPCallResult, MCPClient, get_client, reset_client
from .policy import authorize_tool_call
from .tool_rag import ToolRAGSelector, ToolSelection
from .transport import (
    MCP_PROTOCOL_VERSION,
    HttpTransport,
    InProcessTransport,
    ToolTransport,
    TransportError,
)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "HttpTransport",
    "InProcessTransport",
    "MCPCallResult",
    "MCPClient",
    "ToolRAGSelector",
    "ToolSelection",
    "ToolTransport",
    "TransportError",
    "authorize_tool_call",
    "get_client",
    "reset_client",
]
