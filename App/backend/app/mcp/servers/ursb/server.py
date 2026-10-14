"""``mcp_ursb`` — Standalone FastMCP microservice for URSB Business Registration."""

from __future__ import annotations

from ..connector_server import FastMCPConnectorServer

server = FastMCPConnectorServer(server_name="mcp_ursb", namespace="ursb")
SERVER_NAME = server.server_name
create_app = server.create_app
serve_stdio = server.serve_stdio
handle_request = server.handle_request
