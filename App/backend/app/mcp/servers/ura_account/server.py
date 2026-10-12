"""``mcp_ura_account`` — URA DMZ Account Access standalone MCP server.

Caller identity and consent scopes ride in ``params._meta`` and are
honoured only on an authenticated request; the wire rules are in
:mod:`..wire`.
"""

from __future__ import annotations

import json
from typing import Any

from ..wire import (  # noqa: F401 - part of this module's public surface
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

SERVER_NAME = "mcp_ura_account"
SERVER_VERSION = "2.0.0"
NAMESPACE = "ura_account"

_PROFILE_SPEC_URI = "ura://account/profile-spec"


def _resources() -> list[dict[str, Any]]:
    return [
        {
            "uri": _PROFILE_SPEC_URI,
            "name": "URA Taxpayer Account Profile Specification",
            "description": "Read-only schema for official URA taxpayer portal account profiles.",
            "mimeType": "application/json",
        }
    ]


def _read_resource(uri: str) -> dict[str, Any]:
    if uri != _PROFILE_SPEC_URI:
        raise LookupError(uri)
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(
                    {
                        "specification": "URA-API-ACCOUNT-2026",
                        "endpoints": ["GET /v1/taxpayer/profile", "GET /v1/taxpayer/returns"],
                        "auth": "OAuth 2.1 / DPoP Bearer",
                    }
                ),
            }
        ]
    }


server = StatelessMCPServer(
    SERVER_NAME,
    NAMESPACE,
    SERVER_VERSION,
    resources=_resources,
    read_resource=_read_resource,
)
server_info = server.server_info
handle_request = server.handle_request
create_app = server.create_app
serve_stdio = server.serve_stdio
