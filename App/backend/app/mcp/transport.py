"""Transports the MCP client dispatches through.

A tool's :attr:`~app.tools.ToolSchema.namespace` names the MCP server
that owns it.  Each namespace binds to a transport: in-process today,
streamable HTTP once a server is deployed out-of-process.  Because the
binding is per namespace, a tool moves from in-process to a remote
server by setting one environment variable — no caller changes, which
is the whole point of the abstraction.

Remote calls follow the **2026-07-28** spec: there is no
``initialize``/``initialized`` handshake and no session header.  Every
request carries its own protocol version and client identity in
``_meta`` and repeats the method and tool name in the ``Mcp-Method`` /
``Mcp-Name`` headers, so a plain round-robin load balancer can route
and a gateway can authorize without parsing the body.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Protocol

from .protocol import (  # noqa: F401 — re-exported for existing imports
    CLIENT_NAME,
    MCP_PROTOCOL_VERSION,
    request_meta,
)

logger = logging.getLogger(__name__)

#: ``MCP_SERVER_URL_<NAMESPACE>`` binds a namespace to a remote server.
_URL_ENV_PREFIX = "MCP_SERVER_URL_"
_TOKEN_ENV_PREFIX = "MCP_SERVER_TOKEN_"  # noqa: S105 - an env-var name prefix, not a secret


class TransportError(RuntimeError):
    """A transport-level failure — unreachable server, bad envelope, timeout."""


class ToolTransport(Protocol):
    """What the client needs from any tool backend."""

    name: str

    def list_tools(self) -> list[dict[str, Any]]:
        """MCP ``Tool`` descriptors this transport serves."""

    def describe(self, tool_name: str) -> dict[str, Any] | None:
        """One descriptor, or ``None`` when the transport does not serve it."""

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any],
        timeout_s: float,
        input_responses: list[Any] | None = None,
        request_state: Any = None,
    ) -> dict[str, Any]:
        """Execute the tool and return its JSON-serialisable result."""


class InProcessTransport:
    """Dispatch into the in-process :class:`~app.tools.ToolRegistry`.

    The default for every namespace.  Calls are pure Python, so there is
    no wire timeout to enforce — *timeout_s* is recorded by the client as
    a soft deadline for observability rather than a hard cancellation.
    """

    name = "in_process"

    def list_tools(self) -> list[dict[str, Any]]:
        from ..tools import ToolRegistry

        return ToolRegistry.mcp_tools()

    def describe(self, tool_name: str) -> dict[str, Any] | None:
        from ..tools import ToolRegistry

        tool = ToolRegistry.get(tool_name)
        return tool.to_mcp_tool() if tool is not None else None

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any],
        timeout_s: float,
        input_responses: list[Any] | None = None,
        request_state: Any = None,
    ) -> dict[str, Any]:
        from ..tools import ToolRegistry

        return ToolRegistry.call(tool_name, arguments)


class HttpTransport:
    """Streamable-HTTP transport to a remote MCP server.

    Stateless by construction: nothing is cached between calls except the
    tool list, so any request may land on any replica.
    """

    def __init__(self, namespace: str, base_url: str, token: str = "") -> None:
        self.name = f"http:{namespace}"
        self.namespace = namespace
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._tools: list[dict[str, Any]] | None = None
        self._tools_expires_at: float = 0.0

    # -- wire ----------------------------------------------------------
    def _headers(self, method: str, tool_name: str = "") -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        if tool_name:
            headers["Mcp-Name"] = tool_name
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        tool_name: str = "",
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - deployment shape
            raise TransportError("httpx is required for remote MCP servers") from exc

        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        try:
            response = httpx.post(
                self.base_url,
                json=body,
                headers=self._headers(method, tool_name),
                timeout=timeout_s,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - surfaced as a structured error
            # The URL is safe to name; the token lives in a header and is
            # never part of the message.
            raise TransportError(f"{method} to {self.base_url} failed: {type(exc).__name__}") from exc

        if "error" in payload:
            error = payload["error"] or {}
            raise TransportError(f"{method} returned error {error.get('code')}: {error.get('message')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TransportError(f"{method} returned a malformed result envelope")
        return result

    # -- ToolTransport -------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._tools is None or now >= self._tools_expires_at:
            result = self._request("tools/list", {"_meta": request_meta()})
            tools = result.get("tools", [])
            self._tools = [t for t in tools if isinstance(t, dict)]
            ttl_ms = result.get("ttlMs")
            try:
                ttl_s = max(1.0, float(ttl_ms) / 1000.0) if ttl_ms is not None else 3600.0
            except (TypeError, ValueError):
                ttl_s = 3600.0
            self._tools_expires_at = now + ttl_s
        return list(self._tools)

    def describe(self, tool_name: str) -> dict[str, Any] | None:
        return next((t for t in self.list_tools() if t.get("name") == tool_name), None)

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any],
        timeout_s: float,
        input_responses: list[Any] | None = None,
        request_state: Any = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": tool_name,
            "arguments": arguments,
            "_meta": meta,
        }
        if input_responses:
            params["inputResponses"] = input_responses
        if request_state is not None:
            params["requestState"] = request_state
        result = self._request(
            "tools/call",
            params,
            tool_name=tool_name,
            timeout_s=timeout_s,
        )
        from .mrtr import parse_input_required

        required = parse_input_required(result)
        if required is not None:
            return required
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            structured.setdefault("ok", not result.get("isError", False))
            return structured
        return {
            "ok": not result.get("isError", False),
            "content": result.get("content", []),
        }


def build_transports() -> dict[str, ToolTransport]:
    """Bind namespaces to transports from the environment.

    Any namespace without an ``MCP_SERVER_URL_*`` entry stays in-process,
    so an unconfigured deployment behaves exactly as before.
    """
    from ..tools import ToolRegistry

    in_process = InProcessTransport()
    transports: dict[str, ToolTransport] = {ns: in_process for ns in ToolRegistry.namespaces()}
    for key, value in os.environ.items():
        if not key.startswith(_URL_ENV_PREFIX) or not value.strip():
            continue
        namespace = key[len(_URL_ENV_PREFIX) :].lower()
        token = os.getenv(f"{_TOKEN_ENV_PREFIX}{namespace.upper()}", "")
        transports[namespace] = HttpTransport(namespace, value.strip(), token)
        logger.info("MCP namespace %s bound to remote server", namespace)
    return transports
