"""``mcp_tax_calculator`` MCP server package.

Run it with ``python -m app.mcp.servers.tax_calculator`` (stdio) or
``--http`` (streamable HTTP).  See :mod:`.server` for the protocol
handler and ``README.md`` for deployment.
"""

from __future__ import annotations

from .server import (
    NAMESPACE,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    create_app,
    handle_request,
    serve_stdio,
    server_info,
)

__all__ = [
    "NAMESPACE",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "create_app",
    "handle_request",
    "serve_stdio",
    "server_info",
]
