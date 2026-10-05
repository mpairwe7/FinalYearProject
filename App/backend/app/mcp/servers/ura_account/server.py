"""``mcp_ura_account`` — URA DMZ Account Access standalone MCP server.

Speaks the **2026-07-28** stateless MCP specification over HTTP/stdio.
Carries caller identity and consent scopes in ``params._meta``.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from ....tools import ToolRegistry
from ...policy import authorize_tool_call
from ...protocol import missing_required_meta

logger = logging.getLogger(__name__)

SERVER_NAME = "mcp_ura_account"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2026-07-28"
NAMESPACE = "ura_account"

LIST_TTL_MS = 3_600_000
LIST_CACHE_SCOPE = "server"

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _identity(meta: dict[str, Any]) -> dict[str, str]:
    return {
        "tenant_id": str(meta.get("ug.go.ura.chatbot/tenantId", "default")),
        "user_id": str(meta.get("ug.go.ura.chatbot/userId", "")),
        "user_role": str(meta.get("ug.go.ura.chatbot/userRole", "public")),
    }


def server_info() -> dict[str, Any]:
    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
        },
    }


def handle_tools_list() -> dict[str, Any]:
    return {
        "tools": ToolRegistry.mcp_tools(namespace=NAMESPACE),
        "ttlMs": LIST_TTL_MS,
        "cacheScope": LIST_CACHE_SCOPE,
    }


def handle_resources_list() -> dict[str, Any]:
    return {
        "resources": [
            {
                "uri": "ura://account/profile-spec",
                "name": "URA Taxpayer Account Profile Specification",
                "description": "Read-only schema for official URA taxpayer portal account profiles.",
                "mimeType": "application/json",
            }
        ]
    }


def handle_resources_read(params: dict[str, Any]) -> dict[str, Any]:
    uri = str(params.get("uri", ""))
    if uri == "ura://account/profile-spec":
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps({
                        "specification": "URA-API-ACCOUNT-2026",
                        "endpoints": ["GET /v1/taxpayer/profile", "GET /v1/taxpayer/returns"],
                        "auth": "OAuth 2.1 / DPoP Bearer",
                    }),
                }
            ]
        }
    raise LookupError(f"Resource not found: '{uri}'")


def handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name", ""))
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("'arguments' must be an object")

    tool = ToolRegistry.get(name)
    if tool is None or tool.schema.namespace != NAMESPACE:
        raise LookupError(f"{SERVER_NAME} does not serve a tool named '{name}'")

    identity = _identity(params.get("_meta") or {})
    decision = authorize_tool_call(
        name=name,
        risk=tool.schema.risk,
        user_role=identity["user_role"],
        user_id=identity["user_id"],
        tenant_id=identity["tenant_id"],
        required_scopes=tool.schema.required_scopes,
        allowed_roles=tool.schema.allowed_roles,
        scope_exempt_roles=tool.schema.scope_exempt_roles,
        requires_confirmation=tool.schema.requires_confirmation,
        arguments=arguments,
    )
    if not decision["allowed"]:
        payload = {"ok": False, "error": "policy_denied", "policy": decision}
        return _call_result(payload, is_error=True)

    result = ToolRegistry.call(name, arguments)
    return _call_result(result, is_error=not result.get("ok", True))


def _call_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    text = payload.get("explanation") or payload.get("error") or json.dumps(payload, default=str)
    return {
        "content": [{"type": "text", "text": str(text)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def handle_request(body: Any, headers: dict[str, str] | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
    if isinstance(body, list):
        if not body:
            return _error(None, INVALID_REQUEST, "empty batch request")
        if not all(isinstance(item, dict) for item in body):
            return _error(None, INVALID_REQUEST, "all batch items must be JSON objects")
        batch_responses = []
        for item in body:
            res = handle_request(item, headers)
            if res is not None and isinstance(res, dict):
                batch_responses.append(res)
        return batch_responses if batch_responses else None

    if not isinstance(body, dict):
        return _error(None, INVALID_REQUEST, "request must be a JSON object")

    request_id = body.get("id")
    method = str(body.get("method", ""))
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, INVALID_PARAMS, "'params' must be an object")

    normalized = {str(k).lower(): v for k, v in (headers or {}).items()}
    header_method = normalized.get("mcp-method")
    if header_method and header_method != method:
        return _error(request_id, INVALID_REQUEST, f"Mcp-Method header '{header_method}' does not match body method '{method}'")

    header_name = normalized.get("mcp-name")
    body_name = str(params.get("name", "")) if method == "tools/call" else ""
    if header_name and method == "tools/call" and header_name != body_name:
        return _error(request_id, INVALID_REQUEST, f"Mcp-Name header '{header_name}' does not match body tool '{body_name}'")

    if method in ("tools/list", "tools/call", "server/info", "resources/list", "resources/read"):
        missing = missing_required_meta(params.get("_meta") if isinstance(params, dict) else None)
        if missing:
            return _error(request_id, INVALID_PARAMS, "missing required _meta fields: " + ", ".join(missing))

    if request_id is None and method.startswith("notifications/"):
        return None

    try:
        if method == "ping":
            return _ok(request_id, {})
        if method == "tools/list":
            return _ok(request_id, handle_tools_list())
        if method == "tools/call":
            return _ok(request_id, handle_tools_call(params))
        if method == "resources/list":
            return _ok(request_id, handle_resources_list())
        if method == "resources/read":
            return _ok(request_id, handle_resources_read(params))
        if method == "server/info":
            return _ok(request_id, server_info())
        if method in ("initialize", "initialized"):
            return _error(
                request_id,
                METHOD_NOT_FOUND,
                f"'{method}' was removed in MCP {PROTOCOL_VERSION}; this server is stateless.",
            )
        return _error(request_id, METHOD_NOT_FOUND, f"unknown method '{method}'")
    except LookupError as exc:
        return _error(request_id, METHOD_NOT_FOUND, str(exc))
    except ValueError as exc:
        return _error(request_id, INVALID_PARAMS, str(exc))
    except Exception:  # noqa: BLE001
        logger.exception("%s failed handling %s", SERVER_NAME, method)
        return _error(request_id, INTERNAL_ERROR, "internal server error")


def create_app() -> Any:
    """Starlette ASGI app serving mcp_ura_account over HTTP."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def rpc(request: Any) -> Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(_error(None, PARSE_ERROR, "invalid JSON"), status_code=400)
        response = handle_request(body, dict(request.headers))
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    async def health(_request: Any) -> Response:
        return JSONResponse(
            {"ok": True, **server_info(), "tools": len(ToolRegistry.mcp_tools(namespace=NAMESPACE))}
        )

    return Starlette(routes=[Route("/", rpc, methods=["POST"]), Route("/health", health)])


def serve_stdio() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    logger.info("%s %s listening on stdio", SERVER_NAME, SERVER_VERSION)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, PARSE_ERROR, "invalid JSON")
        else:
            response = handle_request(body)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
