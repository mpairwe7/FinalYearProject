"""MCP servers for the system connectors (EFRIS, payments, stamps, …).

Each connector is one :class:`~.wire.StatelessMCPServer` over the tools
its plugin registers, plus the plugin's read-only resources.  The wire
rules — MCP ``2026-07-28`` over Streamable HTTP or stdio, and the
authentication of the caller — live in :mod:`.wire`.
"""

from __future__ import annotations

import logging
from typing import Any

from .wire import (  # noqa: F401 - re-exported for callers that imported them from here
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    LIST_CACHE_SCOPE,
    LIST_TTL_MS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    StatelessMCPServer,
)

logger = logging.getLogger(__name__)


def _plugin_description(namespace: str) -> str:
    """What the plugin says it is — its simulator notice included."""
    try:
        from plugins import get_orchestrator

        plugin = get_orchestrator().get_plugin(namespace)
        return plugin.metadata.description if plugin else ""
    except Exception:  # noqa: BLE001 - the description is display text, not a dependency
        logger.debug("no plugin description for %s", namespace, exc_info=True)
        return ""


class FastMCPConnectorServer(StatelessMCPServer):
    """The MCP server for one system connector's namespace."""

    def __init__(self, server_name: str, namespace: str, version: str = "2.0.0") -> None:
        super().__init__(
            server_name,
            namespace,
            version,
            instructions=_plugin_description(namespace),
            resources=self._list_resources,
            read_resource=self._read_resource_from_plugin,
        )

    def _list_resources(self) -> list[dict[str, Any]]:
        from plugins import get_orchestrator

        plugin = get_orchestrator().get_plugin(self.namespace)
        return (
            [resource.to_mcp_dict() for resource in plugin.connector.get_resources()]
            if plugin
            else []
        )

    def _read_resource_from_plugin(self, uri: str) -> dict[str, Any]:
        from plugins import get_orchestrator

        plugin = get_orchestrator().get_plugin(self.namespace)
        # Only this connector's own resources: a server must not read
        # another system's data because the caller knew its URI.
        if plugin is None or uri not in {
            resource.uri for resource in plugin.connector.get_resources()
        }:
            raise LookupError(uri)
        return plugin.connector.read_resource(uri)
