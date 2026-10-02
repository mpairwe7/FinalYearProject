"""``mcp_tax_calculator`` — the tax calculators as a standalone MCP server.

Speaks the **2026-07-28** specification, which is stateless: there is no
``initialize``/``initialized`` handshake and no session header, so every
request is self-describing and any request may land on any replica
behind a plain round-robin load balancer.

The request handler is a pure function of ``(body, headers)``.  That is
deliberate — it means the protocol is testable without a socket, and the
same handler backs both the HTTP app and the stdio loop.

No MCP SDK dependency.  The wire format is JSON-RPC 2.0 over HTTP or
newline-delimited stdio, which is small enough to implement exactly and
keeps this server deployable into the same slim image as the API.  The
tools themselves are the ones already registered in
:mod:`app.tools.calculators` — this module is transport, not arithmetic,
so an answer cannot differ between the in-process and remote paths.
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

SERVER_NAME = "mcp_tax_calculator"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2026-07-28"
NAMESPACE = "tax_calculator"

#: Tool lists are stable between deploys, so clients may cache them for
#: an hour.  ``cacheScope: "server"`` says the list does not vary by
#: caller — these calculators are public and identical for everyone.
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
    """Caller identity, carried per-request now that sessions are gone."""
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
    """List static/reference resources exposed by this server."""
    return {
        "resources": [
            {
                "uri": "ura://rates/current",
                "name": "URA Statutory Tax Rates",
                "description": "Official FY2026-27 statutory tax rate table (VAT, PAYE, WHT, Corporation Tax)",
                "mimeType": "application/json",
            },
            {
                "uri": "ura://calendar/deadlines",
                "name": "URA Tax Deadlines Calendar",
                "description": "Upcoming statutory filing, withholding, and return deadlines calendar",
                "mimeType": "application/json",
            },
        ]
    }


def handle_resources_read(params: dict[str, Any]) -> dict[str, Any]:
    """Read contents of an exposed statutory tax resource."""
    uri = str(params.get("uri", ""))
    if uri == "ura://rates/current":
        from ....tax.tables import get_table

        table = get_table()
        payload = {
            "fiscal_year": table.fiscal_year,
            "status": table.status,
            "rates": table.rates,
            "currency": "UGX",
        }
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(payload, default=str),
                }
            ]
        }
    if uri == "ura://calendar/deadlines":
        deadlines = ToolRegistry.call("get_next_deadlines", {})
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(deadlines, default=str),
                }
            ]
        }
    raise LookupError(f"Resource not found: '{uri}'")


def handle_prompts_list() -> dict[str, Any]:
    """List standard prompt templates provided by this server."""
    return {
        "prompts": [
            {
                "name": "vat_calculation_guide",
                "description": "Guidance template for calculating and verifying standard 18% Value Added Tax in Uganda.",
                "arguments": [
                    {
                        "name": "amount",
                        "description": "Transaction amount in UGX",
                        "required": True,
                    }
                ],
            },
            {
                "name": "paye_withholding_guide",
                "description": "Guidance on PAYE income tax brackets and employer withholding obligations.",
                "arguments": [
                    {
                        "name": "monthly_salary",
                        "description": "Gross monthly salary in UGX",
                        "required": True,
                    }
                ],
            },
        ]
    }


def handle_prompts_get(params: dict[str, Any]) -> dict[str, Any]:
    """Render a named prompt template."""
    name = str(params.get("name", ""))
    args = params.get("arguments") or {}
    if name == "vat_calculation_guide":
        amt = args.get("amount", "1000000")
        return {
            "description": "VAT calculation guidance",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"Please calculate the VAT at the statutory 18% rate on UGX {amt} and explain whether any exemptions apply.",
                    },
                }
            ],
        }
    if name == "paye_withholding_guide":
        salary = args.get("monthly_salary", "2000000")
        return {
            "description": "PAYE calculation guidance",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"Please calculate PAYE on a monthly gross salary of UGX {salary} under the current resident PAYE tax bands.",
                    },
                }
            ],
        }
    raise LookupError(f"Prompt template not found: '{name}'")


def handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    """Execute one tool and return an MCP ``CallToolResult``.

    A tool that reports ``ok: false`` is a *tool* error, not a protocol
    error: it comes back as a normal result with ``isError`` set, so the
    model can read the reason and retry with corrected arguments rather
    than seeing a transport failure it cannot act on.
    """
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
    )
    if not decision["allowed"]:
        # Defence in depth: the calling client authorizes too, but a
        # server reachable on its own address must not rely on that.
        payload = {"ok": False, "error": "policy_denied", "policy": decision}
        return _call_result(payload, is_error=True)

    result = ToolRegistry.call(name, arguments)
    return _call_result(result, is_error=not result.get("ok", True))


def _call_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    """Wrap a tool payload as ``content`` + ``structuredContent``.

    The text block carries the calculator's own explanation so a client
    that renders only ``content`` still shows a usable answer.
    """
    text = payload.get("explanation") or payload.get("error") or json.dumps(payload, default=str)
    return {
        "content": [{"type": "text", "text": str(text)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def handle_request(body: Any, headers: dict[str, str] | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Handle one JSON-RPC request or batch; ``None`` for a notification.

    *headers* are checked for consistency when present: a router that
    set ``Mcp-Method`` must agree with the body, otherwise a gateway
    could be authorizing one method while the server runs another.
    """
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
        return _error(
            request_id,
            INVALID_REQUEST,
            f"Mcp-Method header '{header_method}' does not match body method '{method}'",
        )
    header_name = normalized.get("mcp-name")
    body_name = str(params.get("name", "")) if method == "tools/call" else ""
    if header_name and method == "tools/call" and header_name != body_name:
        return _error(
            request_id,
            INVALID_REQUEST,
            f"Mcp-Name header '{header_name}' does not match body tool '{body_name}'",
        )

    if method in ("tools/list", "tools/call", "server/info", "resources/list", "resources/read", "prompts/list", "prompts/get"):
        missing = missing_required_meta(params.get("_meta") if isinstance(params, dict) else None)
        if missing:
            return _error(
                request_id,
                INVALID_PARAMS,
                "missing required _meta fields: " + ", ".join(missing),
            )

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
        if method == "prompts/list":
            return _ok(request_id, handle_prompts_list())
        if method == "prompts/get":
            return _ok(request_id, handle_prompts_get(params))
        if method == "server/info":
            return _ok(request_id, server_info())
        if method in ("initialize", "initialized"):
            return _error(
                request_id,
                METHOD_NOT_FOUND,
                (
                    f"'{method}' was removed in MCP {PROTOCOL_VERSION}; this server is "
                    "stateless — send protocol version and client info in params._meta"
                ),
            )
        return _error(request_id, METHOD_NOT_FOUND, f"unknown method '{method}'")
    except LookupError as exc:
        return _error(request_id, METHOD_NOT_FOUND, str(exc))
    except ValueError as exc:
        return _error(request_id, INVALID_PARAMS, str(exc))
    except Exception:  # noqa: BLE001 - a tool bug must not kill the server
        logger.exception("%s failed handling %s", SERVER_NAME, method)
        return _error(request_id, INTERNAL_ERROR, "internal server error")


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
def create_app() -> Any:
    """A Starlette ASGI app serving this server over streamable HTTP."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def rpc(request: Any) -> Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - malformed body is a protocol error
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
    """Serve over newline-delimited JSON on stdin/stdout.

    The transport local MCP clients use when they launch the server as a
    subprocess.  Logging goes to stderr so it cannot corrupt the stream.
    """
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
