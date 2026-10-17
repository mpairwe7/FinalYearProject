"""The MCP ``2026-07-28`` server, written once.

Each server here used to carry its own copy of the envelope handling,
and the copies had drifted from the specification in the same places:
no ``server/discover`` (which servers MUST implement), no ``resultType``
on results, a ``cacheScope`` value the specification does not define,
header disagreements reported as a generic invalid request, an unknown
*tool* reported as an unknown *method* — which over HTTP is a 404 that
tells a conforming client the server does not implement ``tools/call``
at all — and no authentication of the caller.

:class:`StatelessMCPServer` is that envelope in one place.  A server is
now a name, a namespace and, where it has them, its resources and
prompts.  The request handler stays a pure function of
``(body, headers)`` so the protocol is testable without a socket and the
same code backs HTTP and stdio.

No MCP SDK dependency: the wire format is JSON-RPC 2.0 over one HTTP
POST endpoint or newline-delimited stdio.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

from ...tools import ToolRegistry
from ..auth import OAUTH_CLIENT_CREDENTIALS_EXTENSION, ServerAuth
from ..policy import authorize_tool_call
from ..protocol import MCP_PROTOCOL_VERSION, META_PROTOCOL_VERSION, missing_required_meta
from ..validation import result_matches_schema, screen_tool_result, validate_arguments

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = MCP_PROTOCOL_VERSION
#: Every protocol version this server answers.  One entry: the handshake
#: revisions are not served, and an ``initialize`` is told so by name.
SUPPORTED_VERSIONS: tuple[str, ...] = (PROTOCOL_VERSION,)

# JSON-RPC 2.0
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Allocated by the MCP specification (-32020 … -32099).
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022

#: Tool lists are stable between deploys, so clients may cache them for
#: an hour.  ``public`` says the list does not vary by caller.
LIST_TTL_MS = 3_600_000
LIST_CACHE_SCOPE = "public"

META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
#: A request body larger than this is refused before it is parsed.
MAX_BODY_BYTES = 1_048_576

#: Methods whose target is mirrored into the ``Mcp-Name`` header, and the
#: ``params`` field it is mirrored from.
_NAME_SOURCES = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}
#: Everything this server answers.  Checked before the request metadata so
#: a probe for a method it lacks is told "no such method", not "bad params".
_METHODS = frozenset(
    {
        "server/discover",
        "server/info",
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
    }
)
_BASE64_PREFIX = "=?base64?"
_BASE64_SUFFIX = "?="

ResourceLister = Callable[[], list[dict[str, Any]]]
ResourceReader = Callable[[str], dict[str, Any]]
PromptLister = Callable[[], list[dict[str, Any]]]
PromptGetter = Callable[[str, dict[str, Any]], dict[str, Any]]


class ProtocolError(Exception):
    """A request the model cannot correct, reported as a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def error_response(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def ok_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def http_status_for(response: Any) -> int:
    """The HTTP status the Streamable HTTP transport assigns to a response.

    An unknown *method* is 404 and a request this server cannot read at
    all is 400.  Everything else — including a tool the server does not
    have — is 200 with the JSON-RPC error in the body, so a client can
    tell "this server has no such tool" from "this is not an MCP server".
    """
    if response is None:
        return 202
    code = (response.get("error") or {}).get("code") if isinstance(response, dict) else None
    if code == METHOD_NOT_FOUND:
        return 404
    if code in (
        PARSE_ERROR,
        INVALID_REQUEST,
        HEADER_MISMATCH,
        MISSING_REQUIRED_CLIENT_CAPABILITY,
        UNSUPPORTED_PROTOCOL_VERSION,
    ):
        return 400
    return 200


def decode_header_value(value: str) -> str:
    """Undo the Base64 sentinel a client uses for a value that is not plain ASCII."""
    if value.startswith(_BASE64_PREFIX) and value.endswith(_BASE64_SUFFIX):
        encoded = value[len(_BASE64_PREFIX) : -len(_BASE64_SUFFIX)]
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ProtocolError(
                HEADER_MISMATCH, "Header mismatch: malformed Base64 header value"
            ) from exc
    return value


def _identity(meta: dict[str, Any], *, trusted: bool) -> dict[str, str]:
    """Who the call is for.

    The vendor fields are the chat backend's statement about the taxpayer
    it is acting for.  On a request that did not authenticate there is no
    one to have made that statement, so the caller is ``public`` whatever
    the body says.
    """
    if not trusted:
        return {"tenant_id": "default", "user_id": "", "user_role": "public"}
    return {
        "tenant_id": str(meta.get("ug.go.ura.chatbot/tenantId", "default")),
        "user_id": str(meta.get("ug.go.ura.chatbot/userId", "")),
        "user_role": str(meta.get("ug.go.ura.chatbot/userRole", "public")),
    }


def call_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    """Wrap a tool payload as ``content`` + ``structuredContent``.

    The text block carries the tool's own explanation so a client that
    renders only ``content`` still shows a usable answer.
    """
    text = payload.get("explanation") or payload.get("error") or json.dumps(payload, default=str)
    return {
        "content": [{"type": "text", "text": str(text)}],
        "structuredContent": payload,
        "isError": is_error,
    }


class StatelessMCPServer:
    """One MCP server: a namespace of tools, and optionally resources and prompts."""

    def __init__(  # noqa: PLR0913 - a server's whole surface
        self,
        server_name: str,
        namespace: str,
        version: str = "2.0.0",
        *,
        instructions: str = "",
        resources: ResourceLister | None = None,
        read_resource: ResourceReader | None = None,
        prompts: PromptLister | None = None,
        get_prompt: PromptGetter | None = None,
    ) -> None:
        self.server_name = server_name
        self.namespace = namespace
        self.version = version
        self.instructions = instructions
        self._resources = resources
        self._read_resource = read_resource
        self._prompts = prompts
        self._get_prompt = get_prompt

    # -- identity ------------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {"tools": {"listChanged": False}}
        if self._resources is not None:
            capabilities["resources"] = {"listChanged": False}
        if self._prompts is not None:
            capabilities["prompts"] = {"listChanged": False}
        return capabilities

    def server_info(self) -> dict[str, Any]:
        """The pre-``server/discover`` identity call, kept for older callers."""
        return {
            "name": self.server_name,
            "version": self.version,
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": self.capabilities(),
        }

    def _complete(self, result: dict[str, Any]) -> dict[str, Any]:
        """Every result says it is complete and which server produced it."""
        completed = dict(result)
        completed.setdefault("resultType", "complete")
        meta = dict(completed.get("_meta") or {})
        meta.setdefault(META_SERVER_INFO, {"name": self.server_name, "version": self.version})
        completed["_meta"] = meta
        return completed

    @staticmethod
    def _cacheable(
        result: dict[str, Any], *, ttl_ms: int = LIST_TTL_MS, scope: str = LIST_CACHE_SCOPE
    ) -> dict[str, Any]:
        return {**result, "ttlMs": ttl_ms, "cacheScope": scope}

    # -- methods -------------------------------------------------------
    def handle_discover(self, *, oauth: bool = False) -> dict[str, Any]:
        capabilities = self.capabilities()
        if oauth:
            capabilities["extensions"] = {OAUTH_CLIENT_CREDENTIALS_EXTENSION: {}}
        result: dict[str, Any] = {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": capabilities,
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return self._cacheable(result)

    def handle_tools_list(self) -> dict[str, Any]:
        # ToolRegistry.mcp_tools is name-ordered, which is the stable
        # order the specification asks for so clients can cache the list.
        return self._cacheable({"tools": ToolRegistry.mcp_tools(namespace=self.namespace)})

    def handle_tools_call(
        self, params: dict[str, Any], *, trusted_identity: bool = True
    ) -> dict[str, Any]:
        """Execute one tool and return an MCP ``CallToolResult``.

        A tool that reports ``ok: false`` is a *tool* error, not a
        protocol error: it comes back as a normal result with ``isError``
        set, so the model can read the reason and retry.
        """
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ProtocolError(INVALID_PARAMS, "'arguments' must be an object")

        tool = ToolRegistry.get(name)
        if tool is None or tool.schema.namespace != self.namespace:
            raise ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")

        identity = _identity(params.get("_meta") or {}, trusted=trusted_identity)
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
            # Defence in depth: the calling client authorizes too, but a
            # server reachable on its own address must not rely on that.
            return call_result(
                {"ok": False, "error": "policy_denied", "policy": decision}, is_error=True
            )

        input_schema = tool.schema.parameters or {}
        declared_properties = set((input_schema.get("properties") or {}).keys())
        validation_args = dict(arguments)
        for control in ("submit", "idempotency_key"):
            if control not in declared_properties:
                validation_args.pop(control, None)
        arg_errors = validate_arguments(input_schema, validation_args)
        structural_errors = [
            err
            for err in arg_errors
            if "less than the minimum" not in err and "greater than the maximum" not in err
        ]
        if structural_errors:
            return call_result(
                {
                    "ok": False,
                    "error": f"Invalid arguments for {name}: " + "; ".join(structural_errors),
                    "validation_errors": structural_errors,
                    "expected": input_schema,
                },
                is_error=True,
            )

        result = ToolRegistry.call(
            name,
            validation_args,
            user_id=str(identity.get("user_id") or ""),
            tenant_id=str(identity.get("tenant_id") or "default"),
            channel="mcp",
            caller_confirmed=bool((params.get("_meta") or {}).get("confirmed", False)),
        )
        if arg_errors and result.get("ok", True):
            return call_result(
                {
                    "ok": False,
                    "error": f"Invalid arguments for {name}: " + "; ".join(arg_errors),
                    "validation_errors": arg_errors,
                    "expected": input_schema,
                },
                is_error=True,
            )
        drift = result_matches_schema(tool.schema.output_schema, result)
        if drift:
            result = {
                "ok": False,
                "error": "tool result did not match its output schema",
                "validation_errors": drift[:8],
            }
        else:
            output_violations = screen_tool_result(result)
            if output_violations:
                result = {
                    "ok": False,
                    "error": "untrusted_tool_output",
                    "violations": output_violations[:8],
                }
        return call_result(result, is_error=not result.get("ok", True))

    def handle_resources_list(self) -> dict[str, Any]:
        return self._cacheable({"resources": self._resources() if self._resources else []})

    def handle_resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = str(params.get("uri", ""))
        contents: dict[str, Any] | None = None
        if self._read_resource is not None:
            try:
                contents = self._read_resource(uri)
            except LookupError:
                contents = None
        if not contents:
            raise ProtocolError(INVALID_PARAMS, f"Resource not found: '{uri}'")
        # Live figures: a caller must not serve these to anyone else.
        return self._cacheable(contents, ttl_ms=0, scope="private")

    def handle_prompts_list(self) -> dict[str, Any]:
        return self._cacheable({"prompts": self._prompts() if self._prompts else []})

    def handle_prompts_get(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if self._get_prompt is None or not isinstance(arguments, dict):
            raise ProtocolError(INVALID_PARAMS, f"Prompt not found: '{name}'")
        try:
            return self._get_prompt(name, arguments)
        except LookupError as exc:
            raise ProtocolError(INVALID_PARAMS, f"Prompt not found: '{name}'") from exc

    # -- envelope ------------------------------------------------------
    @staticmethod
    def _check_headers(
        method: str, params: dict[str, Any], headers: dict[str, str], *, strict: bool
    ) -> None:
        """Refuse a request whose routing headers disagree with its body.

        A gateway routes and authorizes on the headers; the server acts on
        the body.  If the two can differ, a caller can be authorized for
        one operation and run another.
        """
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}

        header_method = normalized.get("mcp-method")
        if header_method is not None and header_method != method:
            raise ProtocolError(
                HEADER_MISMATCH,
                f"Header mismatch: Mcp-Method header value '{header_method}' does not match body value '{method}'",
            )
        if strict and header_method is None:
            raise ProtocolError(
                HEADER_MISMATCH, "Header mismatch: required Mcp-Method header is missing"
            )

        source = _NAME_SOURCES.get(method)
        if source is not None:
            header_name = normalized.get("mcp-name")
            body_name = str(params.get(source, ""))
            if header_name is not None and decode_header_value(header_name) != body_name:
                raise ProtocolError(
                    HEADER_MISMATCH,
                    f"Header mismatch: Mcp-Name header value '{header_name}' does not match body value '{body_name}'",
                )
            if strict and header_name is None:
                raise ProtocolError(
                    HEADER_MISMATCH, "Header mismatch: required Mcp-Name header is missing"
                )

        header_version = normalized.get("mcp-protocol-version")
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        body_version = meta.get(META_PROTOCOL_VERSION)
        if header_version is not None and body_version and header_version != body_version:
            raise ProtocolError(
                HEADER_MISMATCH,
                f"Header mismatch: MCP-Protocol-Version header value '{header_version}' "
                f"does not match body value '{body_version}'",
            )
        if strict and header_version is None:
            raise ProtocolError(
                HEADER_MISMATCH, "Header mismatch: required MCP-Protocol-Version header is missing"
            )

    def _dispatch(
        self, method: str, params: dict[str, Any], *, trusted_identity: bool, oauth: bool
    ) -> dict[str, Any]:
        if method == "server/discover":
            return self.handle_discover(oauth=oauth)
        if method == "tools/list":
            return self.handle_tools_list()
        if method == "tools/call":
            return self.handle_tools_call(params, trusted_identity=trusted_identity)
        if method == "resources/list":
            return self.handle_resources_list()
        if method == "resources/read":
            return self.handle_resources_read(params)
        if method == "prompts/list":
            return self.handle_prompts_list()
        if method == "prompts/get":
            return self.handle_prompts_get(params)
        if method == "server/info":
            return self.server_info()
        raise ProtocolError(METHOD_NOT_FOUND, f"unknown method '{method}'")

    def handle_request(
        self,
        body: Any,
        headers: dict[str, str] | None = None,
        *,
        strict_headers: bool = False,
        trusted_identity: bool = True,
        oauth: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Handle one JSON-RPC request; ``None`` for a notification.

        *headers* are checked against the body whenever they are given.
        *strict_headers* additionally requires the headers the Streamable
        HTTP transport makes mandatory, and is what the HTTP app passes.
        *trusted_identity* is False for a request that did not
        authenticate: stdio and direct in-process calls are the launching
        process, which is trusted.
        """
        if isinstance(body, list):
            # Batches are not part of Streamable HTTP in this revision and
            # the HTTP app refuses them; this is the stdio path only.
            if not body:
                return error_response(None, INVALID_REQUEST, "empty batch request")
            if not all(isinstance(item, dict) for item in body):
                return error_response(None, INVALID_REQUEST, "all batch items must be JSON objects")
            responses: list[dict[str, Any]] = []
            for item in body:
                response = self.handle_request(
                    item,
                    headers,
                    strict_headers=strict_headers,
                    trusted_identity=trusted_identity,
                    oauth=oauth,
                )
                if isinstance(response, dict):
                    responses.append(response)
            return responses or None

        if not isinstance(body, dict):
            return error_response(None, INVALID_REQUEST, "request must be a JSON object")

        request_id = body.get("id")
        method = str(body.get("method", ""))
        params = body.get("params") or {}
        if not isinstance(params, dict):
            return error_response(request_id, INVALID_PARAMS, "'params' must be an object")

        try:
            if headers is not None:
                self._check_headers(method, params, headers, strict=strict_headers)

            if request_id is None and method.startswith("notifications/"):
                return None
            if method in ("initialize", "initialized", "notifications/initialized"):
                raise ProtocolError(
                    METHOD_NOT_FOUND,
                    f"'{method}' was removed in MCP {PROTOCOL_VERSION}; this server is stateless and "
                    f"supports protocol versions: {', '.join(SUPPORTED_VERSIONS)}. Send the protocol "
                    "version and client info in params._meta on every request.",
                )
            if method == "ping":
                # Removed from the specification in this revision; still
                # answered so a caller one release behind is not cut off
                # mid-rollout.  New callers probe with server/discover.
                return ok_response(request_id, {})
            if method not in _METHODS:
                raise ProtocolError(METHOD_NOT_FOUND, f"unknown method '{method}'")

            meta = params.get("_meta")
            missing = missing_required_meta(meta if isinstance(meta, dict) else None)
            if missing:
                raise ProtocolError(
                    INVALID_PARAMS, "missing required _meta fields: " + ", ".join(missing)
                )
            requested = str(meta.get(META_PROTOCOL_VERSION, ""))
            if requested not in SUPPORTED_VERSIONS:
                raise ProtocolError(
                    UNSUPPORTED_PROTOCOL_VERSION,
                    "Unsupported protocol version",
                    {"supported": list(SUPPORTED_VERSIONS), "requested": requested},
                )

            result = self._dispatch(method, params, trusted_identity=trusted_identity, oauth=oauth)
            return ok_response(request_id, self._complete(result))
        except ProtocolError as exc:
            return error_response(request_id, exc.code, exc.message, exc.data)
        except Exception:  # noqa: BLE001 - a tool bug must not kill the server
            logger.exception("%s failed handling %s", self.server_name, method)
            return error_response(request_id, INTERNAL_ERROR, "internal server error")

    # -- transports ----------------------------------------------------
    def routes(
        self,
        auth: ServerAuth | None = None,
        allowed_origins: tuple[str, ...] | None = None,
        prefix: str = "",
    ) -> list[Any]:
        """This server's Streamable HTTP routes, optionally under *prefix*.

        A prefix lets several servers share one process and port, each at
        its own endpoint (``/efris``, ``/payment_system``, …) — each is
        still one MCP server with one endpoint of its own.
        """
        from starlette.concurrency import run_in_threadpool
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        server_auth = auth if auth is not None else ServerAuth.from_env(self.namespace)
        origins = (
            allowed_origins
            if allowed_origins is not None
            else tuple(
                origin.strip().rstrip("/")
                for origin in os.getenv("MCP_SERVER_ALLOWED_ORIGINS", "").split(",")
                if origin.strip()
            )
        )
        if server_auth.mode == "locked":
            logger.error("%s refuses every request: %s", self.server_name, server_auth.reason)
        elif server_auth.mode == "anonymous":
            logger.warning(
                "%s accepts unauthenticated requests (development only); every caller is treated as public",
                self.server_name,
            )

        async def read_body(request: Any) -> bytes | None:
            """The request body, or ``None`` once it passes the size limit."""
            chunks: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)

        async def rpc(request: Any) -> Response:
            # A browser sends Origin; a backend does not.  Refusing an
            # origin that was not listed is what stops a web page a user
            # has open from driving this server through their browser.
            origin = request.headers.get("origin")
            if origin is not None and origin.rstrip("/") not in origins:
                return JSONResponse(
                    error_response(None, INVALID_REQUEST, "origin not allowed"), status_code=403
                )

            outcome = server_auth.verify(request.headers.get("authorization"))
            if not outcome.ok:
                return JSONResponse(
                    error_response(
                        None, INVALID_REQUEST, outcome.description or "authentication required"
                    ),
                    status_code=outcome.status,
                    headers={
                        "WWW-Authenticate": server_auth.challenge(outcome),
                        "Cache-Control": "no-store",
                    },
                )

            raw = await read_body(request)
            if raw is None:
                return JSONResponse(
                    error_response(None, INVALID_REQUEST, "request body too large"), status_code=413
                )
            try:
                body = json.loads(raw)
            except ValueError:
                return JSONResponse(
                    error_response(None, PARSE_ERROR, "invalid JSON"), status_code=400
                )
            if not isinstance(body, dict):
                # One request per POST: JSON-RPC batches are not part of
                # Streamable HTTP in this revision.
                return JSONResponse(
                    error_response(
                        None, INVALID_REQUEST, "the body must be a single JSON-RPC request"
                    ),
                    status_code=400,
                )

            # Tools reach SQLite and the network; keep the event loop free.
            response = await run_in_threadpool(
                self.handle_request,
                body,
                dict(request.headers),
                strict_headers=True,
                trusted_identity=outcome.authenticated,
                oauth=server_auth.mode == "oauth",
            )
            if response is None:
                return Response(status_code=202)
            return JSONResponse(response, status_code=http_status_for(response))

        async def not_post(_request: Any) -> Response:
            # The GET stream and DELETE-to-end-a-session belong to earlier
            # revisions; this server has neither.
            return Response(status_code=405, headers={"Allow": "POST"})

        async def health(_request: Any) -> Response:
            return JSONResponse(
                {
                    "ok": True,
                    **self.server_info(),
                    "tools": len(ToolRegistry.mcp_tools(namespace=self.namespace)),
                    "auth": server_auth.mode,
                }
            )

        async def protected_resource(_request: Any) -> Response:
            document = server_auth.protected_resource_metadata()
            if document is None:
                return Response(status_code=404)
            return JSONResponse(document, headers={"Cache-Control": "public, max-age=3600"})

        endpoint = prefix or "/"
        return [
            Route(endpoint, rpc, methods=["POST"]),
            Route(endpoint, not_post, methods=["GET", "DELETE", "PUT", "PATCH"]),
            Route(f"{prefix}/health", health, methods=["GET"]),
            # RFC 9728 puts the well-known segment before the resource path.
            Route(
                f"/.well-known/oauth-protected-resource{prefix}",
                protected_resource,
                methods=["GET"],
            ),
        ]

    def create_app(
        self, auth: ServerAuth | None = None, allowed_origins: tuple[str, ...] | None = None
    ) -> Any:
        """A Starlette ASGI app serving this server over Streamable HTTP."""
        from starlette.applications import Starlette

        return Starlette(routes=self.routes(auth, allowed_origins))

    def serve_stdio(self) -> None:
        """Newline-delimited JSON-RPC on stdin/stdout.  Logs go to stderr."""
        logging.basicConfig(stream=sys.stderr, level=logging.INFO)
        logger.info(
            "%s %s listening on stdio (MCP %s)", self.server_name, self.version, PROTOCOL_VERSION
        )
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                body = json.loads(line)
            except json.JSONDecodeError:
                response: Any = error_response(None, PARSE_ERROR, "invalid JSON")
            else:
                response = self.handle_request(body)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
