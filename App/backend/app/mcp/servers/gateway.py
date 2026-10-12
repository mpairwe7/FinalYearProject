"""Every system connector's MCP server in one process.

Each connector can run as its own service (``python -m
app.mcp.servers.efris --http``), which is the shape for a deployment
that isolates each system behind its own network policy.  A prototype
or a single-host stack does not need six containers for six simulators,
so this serves the same servers side by side, one endpoint each::

    POST /efris              POST /payment_system     POST /ursb
    POST /digital_tax_stamps POST /tin_registration   POST /bwims

They remain separate MCP servers — separate tool lists, separate
credentials (``MCP_SERVER_TOKEN_<NAMESPACE>``), separate protected
resource metadata — that happen to share a port.

    python -m app.mcp.servers.gateway --port 8930
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, Any

from ..auth import ServerAuth
from .bwims.server import server as _bwims
from .dts.server import server as _dts
from .efris.server import server as _efris
from .payment.server import server as _payment
from .tin.server import server as _tin
from .ursb.server import server as _ursb

if TYPE_CHECKING:
    from .wire import StatelessMCPServer

logger = logging.getLogger(__name__)

SERVER_NAME = "mcp_connectors"

#: Keyed by namespace, which is also the endpoint path and the suffix of
#: the client's ``MCP_SERVER_URL_<NAMESPACE>`` binding.
CONNECTOR_SERVERS: dict[str, StatelessMCPServer] = {
    server.namespace: server for server in (_bwims, _dts, _efris, _payment, _tin, _ursb)
}


def create_app(namespaces: list[str] | None = None) -> Any:
    """A Starlette app serving the chosen connectors, each under ``/<namespace>``."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    from ...tools import ToolRegistry

    chosen = sorted(namespaces) if namespaces else sorted(CONNECTOR_SERVERS)
    unknown = [namespace for namespace in chosen if namespace not in CONNECTOR_SERVERS]
    if unknown:
        raise ValueError(f"unknown connector namespace(s): {', '.join(unknown)}")

    routes: list[Any] = []
    modes: dict[str, str] = {}
    for namespace in chosen:
        auth = ServerAuth.from_env(namespace)
        modes[namespace] = auth.mode
        routes.extend(CONNECTOR_SERVERS[namespace].routes(auth, prefix=f"/{namespace}"))

    async def health(_request: Any) -> Response:
        return JSONResponse(
            {
                "ok": True,
                "name": SERVER_NAME,
                "servers": {
                    namespace: {
                        "tools": len(ToolRegistry.mcp_tools(namespace=namespace)),
                        "auth": modes[namespace],
                    }
                    for namespace in chosen
                },
            }
        )

    routes.append(Route("/health", health, methods=["GET"]))
    return Starlette(routes=routes)


def main() -> None:
    parser = argparse.ArgumentParser(prog=SERVER_NAME)
    parser.add_argument("--host", default="0.0.0.0")  # nosec B104 # noqa: S104 -- container-local bind
    parser.add_argument("--port", type=int, default=8930)
    parser.add_argument(
        "--namespaces",
        default="",
        help="comma-separated connector namespaces to serve (default: all)",
    )
    args = parser.parse_args()

    import uvicorn

    namespaces = [item.strip() for item in args.namespaces.split(",") if item.strip()]
    uvicorn.run(create_app(namespaces or None), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
