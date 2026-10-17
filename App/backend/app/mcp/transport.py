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

import base64
import ipaddress
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from typing import Any, Protocol

from .auth import (
    MIN_SHARED_TOKEN_CHARS,
    OAUTH_CLIENT_CREDENTIALS_EXTENSION,
    ClientCredentialsToken,
    TokenError,
    client_credentials_from_env,
)
from .protocol import (  # noqa: F401 — re-exported for existing imports
    CLIENT_NAME,
    MCP_PROTOCOL_VERSION,
    META_CLIENT_CAPABILITIES,
    request_meta,
)

logger = logging.getLogger(__name__)

#: ``MCP_SERVER_URL_<NAMESPACE>`` binds a namespace to a remote server.
URL_ENV_PREFIX = "MCP_SERVER_URL_"
_TOKEN_ENV_PREFIX = "MCP_SERVER_TOKEN_"  # noqa: S105 - an env-var name prefix, not a secret

#: Discovery (``server/discover``, ``tools/list``) answers from memory on a
#: healthy server, so it gets a short deadline: a connector that is down
#: must not hold the catalog — and the picker a taxpayer is looking at —
#: for the full tool-call timeout.
DISCOVERY_TIMEOUT_S = 3.0
#: After a failed discovery, do not try that server again for this long.
DISCOVERY_RETRY_S = 15.0
#: How long "this server is answering" is believed. The tool *list* may be
#: kept as long as the server says (the bundled servers say an hour), which
#: is right for the list and wrong for liveness: a server that went down
#: was shown as available — and could be chosen — for up to that hour.
LIVENESS_TTL_S = 30.0
#: Deadline for the reachability probe itself (``server/discover``).
LIVENESS_TIMEOUT_S = 2.0
#: Maximum cache TTL a remote MCP server may advertise (24h).
MAX_CACHE_TTL_S = 86_400.0
#: Maximum response payload bytes accepted from a remote MCP server (2 MiB).
MAX_MCP_RESPONSE_BYTES = int(os.getenv("MCP_MAX_RESPONSE_BYTES", str(2 * 1024 * 1024)))
#: Gateway answers that mean "nothing is behind this address right now".
_UNREACHABLE_STATUSES = frozenset({502, 503, 504})

_MCP_UNSUPPORTED_PROTOCOL_VERSION = -32022
_BASE64_PREFIX = "=?base64?"
_BASE64_SUFFIX = "?="
#: RFC 9110 ``token``: what an HTTP field name may be made of.
_HEADER_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
#: Integers a JSON number can carry without losing precision.
_SAFE_INTEGER = 2**53 - 1


class TransportError(RuntimeError):
    """A transport-level failure — unreachable server, bad envelope, timeout.

    ``code`` and ``data`` carry a JSON-RPC error when the server sent
    one, and ``status`` the HTTP status.  The message never includes a
    credential: tokens travel in a header and are not part of any URL.
    """

    def __init__(self, message: str, *, code: int | None = None, data: Any = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
        self.status = status


def encode_header_value(value: str) -> str:
    """A value as it may appear in an ``Mcp-Name`` / ``Mcp-Param-*`` header.

    Plain visible ASCII travels as-is.  Anything else — or anything that
    would be mistaken for the encoding itself — is Base64 inside the
    sentinel the specification defines, so a newline in an argument
    cannot become a second header.
    """
    plain = (
        bool(value)
        and value == value.strip()
        and all(0x20 <= ord(char) <= 0x7E for char in value)
        and not (value.startswith(_BASE64_PREFIX) and value.endswith(_BASE64_SUFFIX))
    )
    if plain:
        return value
    return _BASE64_PREFIX + base64.b64encode(value.encode("utf-8")).decode("ascii") + _BASE64_SUFFIX


def header_parameters(input_schema: Any) -> dict[str, tuple[str, ...]]:
    """Map each ``x-mcp-header`` name in a tool's schema to its argument path.

    Raises ``ValueError`` when the schema uses the annotation in a way
    the specification forbids; a client must then leave the tool out of
    its list rather than call it with headers a gateway could misread.
    """
    found: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()

    def walk(schema: Any, path: tuple[str, ...], reachable: bool) -> None:
        if isinstance(schema, list):
            for item in schema:
                walk(item, path, False)
            return
        if not isinstance(schema, dict):
            return
        if "x-mcp-header" in schema:
            name = schema["x-mcp-header"]
            if not reachable or not path:
                raise ValueError("x-mcp-header is only allowed on a property reachable through 'properties'")
            if not isinstance(name, str) or not _HEADER_TOKEN_RE.fullmatch(name):
                raise ValueError("x-mcp-header must be a non-empty HTTP field-name token")
            if schema.get("type") not in ("string", "integer", "boolean"):
                raise ValueError("x-mcp-header requires a string, integer or boolean parameter")
            if name.lower() in seen:
                raise ValueError(f"x-mcp-header '{name}' is used more than once")
            seen.add(name.lower())
            found[name] = path
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                for prop, child in value.items():
                    walk(child, (*path, str(prop)), reachable)
            elif key != "x-mcp-header":
                # Array items, composition and conditionals are not
                # statically reachable: an annotation below them is invalid.
                walk(value, path, False)

    walk(input_schema, (), True)
    return found


def _parameter_headers(input_schema: Any, arguments: dict[str, Any]) -> dict[str, str]:
    """The ``Mcp-Param-*`` headers a call to this tool must carry."""
    headers: dict[str, str] = {}
    for name, path in header_parameters(input_schema).items():
        value: Any = arguments
        for step in path:
            value = value.get(step) if isinstance(value, dict) else None
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, int):
            if abs(value) > _SAFE_INTEGER:
                raise TransportError(f"argument for header Mcp-Param-{name} is outside the safe integer range")
            text = str(value)
        elif isinstance(value, str):
            text = value
        else:
            continue
        headers[f"Mcp-Param-{name}"] = encode_header_value(text)
    return headers


def _event_stream_message(text: str, request_id: str) -> Any:
    """The JSON-RPC response carried by a Server-Sent Events body.

    A server may answer a request with a stream: progress notifications
    first, then the response.  Only the message answering *request_id*
    is the result; everything else on the stream is commentary.
    """
    for block in re.split(r"\r?\n\r?\n", text):
        data_lines = [line[5:].lstrip(" ") for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        try:
            message = json.loads("\n".join(data_lines))
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id and ("result" in message or "error" in message):
            return message
    return None


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

        meta_dict = meta if isinstance(meta, dict) else {}
        return ToolRegistry.call(
            tool_name,
            arguments,
            user_id=str(meta_dict.get("ug.go.ura.chatbot/userId") or ""),
            tenant_id=str(meta_dict.get("ug.go.ura.chatbot/tenantId") or "default"),
            channel="mcp",
            caller_confirmed=bool(meta_dict.get("confirmed", False)),
        )

    def list_resources(self) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = [
            {
                "uri": "ura://rates/current",
                "name": "URA Statutory Tax Rates",
                "description": "Official statutory tax rate table (VAT, PAYE, WHT, Corporation Tax)",
                "mimeType": "application/json",
            },
            {
                "uri": "ura://calendar/deadlines",
                "name": "URA Tax Deadlines Calendar",
                "description": "Upcoming statutory filing, withholding, and return deadlines calendar",
                "mimeType": "application/json",
            },
        ]
        try:
            from plugins.orchestrator import get_orchestrator
            resources.extend(get_orchestrator().get_all_resources())
        except Exception:
            pass
        return resources

    def read_resource(self, uri: str) -> dict[str, Any]:
        import json
        if uri == "ura://rates/current":
            from ..tax.tables import get_table
            table = get_table()
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps({"fiscal_year": table.fiscal_year, "status": table.status, "rates": table.rates, "currency": "UGX"}, default=str),
                    }
                ]
            }
        if uri == "ura://calendar/deadlines":
            from ..tools import ToolRegistry
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
        try:
            from plugins.orchestrator import get_orchestrator
            res = get_orchestrator().read_connector_resource(uri)
            if res:
                return {"contents": [res]}
        except Exception:
            pass
        raise LookupError(f"Resource not found: '{uri}'")

    def list_prompts(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "vat_calculation_guide",
                "description": "Guidance template for calculating and verifying standard 18% Value Added Tax in Uganda.",
                "arguments": [{"name": "amount", "description": "Transaction amount in UGX", "required": True}],
            },
            {
                "name": "tcc_tender_checklist",
                "description": "Compliance checklist template for Tax Clearance Certificate applications.",
                "arguments": [{"name": "entity_type", "description": "individual or company", "required": False}],
            },
        ]

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name == "vat_calculation_guide":
            amt = args.get("amount", "your specified amount")
            return {
                "description": f"VAT Calculation Guide for {amt}",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": f"Guide me through calculating 18% VAT on {amt} UGX under Uganda VAT Act."}}
                ],
            }
        if name == "tcc_tender_checklist":
            ent = args.get("entity_type", "business")
            return {
                "description": f"Tax Clearance Certificate Checklist for {ent}",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": f"Provide the official URA compliance checklist for a {ent} TCC application."}}
                ],
            }
        raise LookupError(f"Prompt template not found: '{name}'")


class HttpTransport:
    """Streamable-HTTP transport to a remote MCP server.

    Stateless by construction: nothing is cached between calls except
    what the server said may be cached — its tool list and its answer to
    ``server/discover`` — so any request may land on any replica.
    """

    def __init__(
        self,
        namespace: str,
        base_url: str,
        token: str = "",
        cert: tuple[str, str] | str | None = None,
        token_source: ClientCredentialsToken | None = None,
        client: Any = None,
    ) -> None:
        """*client* is an ``httpx.Client`` to use instead of creating one —
        for a caller that manages its own pool, and for tests."""
        self.name = f"http:{namespace}"
        self.namespace = namespace
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._token_source = token_source
        cert_file = os.getenv(f"MCP_CLIENT_CERT_{namespace.upper()}")
        key_file = os.getenv(f"MCP_CLIENT_KEY_{namespace.upper()}")
        self._cert = (cert_file, key_file) if cert_file and key_file else (cert or None)
        self._tools: list[dict[str, Any]] | None = None
        self._tools_expires_at: float = 0.0
        self._discovery: dict[str, Any] | None = None
        self._discovery_expires_at: float = 0.0
        #: Set by a failed discovery; until it passes, the server is
        #: reported unavailable without another network round trip.
        self._retry_after: float = 0.0
        self._last_error: str = ""
        #: The last reachability answer and when it was learned (monotonic).
        self._live: bool = True
        self._live_checked_at: float = 0.0
        self._client: Any = client
        self._lock = threading.Lock()

    @property
    def credential_kind(self) -> str:
        """How this client authenticates to the server — a kind, never the credential.

        ``shared_token_too_short`` is a token the server will treat as absent
        (it would answer 401 and the link would read as down).
        """
        if self._token_source is not None:
            return "oauth_client_credentials"
        if self._token:
            return "shared_token" if len(self._token) >= MIN_SHARED_TOKEN_CHARS else "shared_token_too_short"
        return "none"

    def _record_liveness(self, live: bool) -> None:
        self._live = live
        self._live_checked_at = time.monotonic()

    def is_live(self) -> bool:
        """Whether the server answered a reachability probe recently.

        Answers from memory for :data:`LIVENESS_TTL_S`; otherwise asks with
        ``server/discover`` under a short deadline. A failed call on any
        path (:meth:`_post`) records the server as down at once, so a turn
        that just failed is not followed by a picker that says "available".
        """
        now = time.monotonic()
        if now < self._retry_after:
            return False
        if self._live_checked_at and now - self._live_checked_at < LIVENESS_TTL_S:
            return self._live
        live = self.ping(timeout_s=LIVENESS_TIMEOUT_S)
        if not live:
            self._retry_after = now + DISCOVERY_RETRY_S
        self._record_liveness(live)
        return live

    def _get_client(self) -> Any:
        """The one pooled client for this server.

        The deadline is passed per request, never baked into the client:
        discovery and tool calls use different deadlines and run on
        different threads, and rebuilding the client whenever the
        deadline changed closed it underneath whichever call was still
        using it.
        """
        import httpx

        with self._lock:
            if self._client is None or getattr(self._client, "is_closed", False):
                # Redirects are not followed: a redirect would carry the
                # bearer token to wherever the response pointed.
                self._client = httpx.Client(cert=self._cert, follow_redirects=False)
            return self._client

    # -- wire ----------------------------------------------------------
    def _bearer(self) -> str:
        if self._token_source is not None:
            try:
                return self._token_source.get()
            except TokenError as exc:
                raise TransportError(f"could not obtain an access token for {self.namespace}: {exc}") from exc
        return self._token

    def _headers(self, method: str, tool_name: str = "") -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # A server may answer with one JSON object or with a stream
            # that ends in one; a client has to accept both.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        if tool_name:
            headers["Mcp-Name"] = encode_header_value(tool_name)
        bearer = self._bearer()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        return headers

    def _post(self, body: dict[str, Any], headers: dict[str, str], timeout_s: float) -> Any:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:  # pragma: no cover - deployment shape
            raise TransportError("httpx is required for remote MCP servers") from exc
        method = str(body.get("method", ""))
        try:
            response = self._get_client().post(self.base_url, json=body, headers=headers, timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001 - surfaced as a structured error
            self._record_liveness(False)
            # The URL is safe to name; the token lives in a header and is
            # never part of the message.
            raise TransportError(f"{method} to {self.base_url} failed: {type(exc).__name__}") from exc
        status = getattr(response, "status_code", 0)
        if status in _UNREACHABLE_STATUSES:
            self._record_liveness(False)
        elif isinstance(status, int) and 200 <= status < 300:
            self._record_liveness(True)
        return response

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        tool_name: str = "",
        timeout_s: float = 10.0,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        if self._token_source is not None and isinstance(params.get("_meta"), dict):
            params = {**params, "_meta": _with_extension(params["_meta"], OAUTH_CLIENT_CREDENTIALS_EXTENSION)}
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}

        def send() -> Any:
            headers = self._headers(method, tool_name)
            headers.update(extra_headers or {})
            return self._post(body, headers, timeout_s)

        response = send()
        if response.status_code == 401 and self._token_source is not None:
            # The cached token may have been revoked or rotated; one fresh
            # token is worth trying before reporting the link as broken.
            self._token_source.invalidate()
            response = send()
        if response.status_code in (401, 403):
            raise TransportError(
                f"{method} to {self.base_url} was refused: the server did not accept this client's credentials "
                f"(HTTP {response.status_code})",
                status=response.status_code,
            )

        content_type = str(response.headers.get("content-type", "")).lower()
        raw_bytes = getattr(response, "content", None)
        if isinstance(raw_bytes, bytes | bytearray) and len(raw_bytes) > MAX_MCP_RESPONSE_BYTES:
            raise TransportError(
                f"{method} to {self.base_url} exceeded maximum response size ({len(raw_bytes)} > {MAX_MCP_RESPONSE_BYTES} bytes)",
                status=response.status_code,
            )
        try:
            if content_type.startswith("text/event-stream"):
                payload = _event_stream_message(response.text, request_id)
            else:
                payload = response.json()
        except Exception:  # noqa: BLE001 - an unreadable body is reported by status below
            payload = None

        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
            code = error.get("code")
            detail = f"{method} returned error {code}: {error.get('message')}"
            if code == _MCP_UNSUPPORTED_PROTOCOL_VERSION:
                supported = (error.get("data") or {}).get("supported") if isinstance(error.get("data"), dict) else None
                detail = (
                    f"{method} to {self.base_url} failed: the server does not speak MCP {MCP_PROTOCOL_VERSION} "
                    f"(it supports {supported or 'no version this client knows'})"
                )
            raise TransportError(detail, code=code, data=error.get("data"), status=response.status_code)
        if response.status_code >= 400:
            raise TransportError(
                f"{method} to {self.base_url} failed: HTTP {response.status_code}", status=response.status_code
            )
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise TransportError(f"{method} returned a malformed result envelope")
        return result

    # -- discovery -----------------------------------------------------
    def _discover_guarded(self, fetch: Any) -> Any:
        """Run one discovery call, remembering a failure for a short while."""
        now = time.monotonic()
        if now < self._retry_after:
            raise TransportError(self._last_error or f"{self.namespace} is temporarily unavailable")
        try:
            value = fetch()
        except TransportError as exc:
            self._retry_after = now + DISCOVERY_RETRY_S
            self._last_error = str(exc)
            raise
        self._retry_after = 0.0
        self._last_error = ""
        return value

    def discover(self, timeout_s: float = DISCOVERY_TIMEOUT_S) -> dict[str, Any]:
        """The server's answer to ``server/discover``: versions, capabilities, identity."""
        now = time.monotonic()
        if self._discovery is None or now >= self._discovery_expires_at:
            result = self._discover_guarded(
                lambda: self._request("server/discover", {"_meta": request_meta()}, timeout_s=timeout_s)
            )
            self._discovery = result
            self._discovery_expires_at = now + _cache_seconds(result.get("ttlMs"))
        return dict(self._discovery)

    def ping(self, timeout_s: float = 5.0) -> bool:
        """Whether the server answers.

        ``ping`` was removed from the protocol in 2026-07-28;
        ``server/discover`` is the call every server must implement, so
        it is the reachability probe.  Always a live request, never the
        cached answer.
        """
        try:
            result = self._request("server/discover", {"_meta": request_meta()}, timeout_s=timeout_s)
        except Exception:  # noqa: BLE001 - a probe reports, it does not raise
            return False
        self._retry_after = 0.0
        return isinstance(result, dict)

    def close(self) -> None:
        """Close the underlying persistent HTTP client pool."""
        if self._client is not None and not getattr(self._client, "is_closed", True):
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # -- ToolTransport -------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._tools is None or now >= self._tools_expires_at:
            result = self._discover_guarded(
                lambda: self._request("tools/list", {"_meta": request_meta()}, timeout_s=DISCOVERY_TIMEOUT_S)
            )
            tools: list[dict[str, Any]] = []
            for tool in result.get("tools", []):
                if not isinstance(tool, dict):
                    continue
                try:
                    header_parameters(tool.get("inputSchema"))
                except ValueError as exc:
                    # One malformed definition must not hide the others.
                    logger.warning(
                        "MCP tool %s from %s ignored: %s", tool.get("name"), self.namespace, exc
                    )
                    continue
                tools.append(tool)
            self._tools = tools
            self._tools_expires_at = now + _cache_seconds(result.get("ttlMs"))
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
        descriptor = next((t for t in self._tools or [] if t.get("name") == tool_name), None)
        result = self._request(
            "tools/call",
            params,
            tool_name=tool_name,
            timeout_s=timeout_s,
            extra_headers=_parameter_headers(descriptor.get("inputSchema"), arguments) if descriptor else None,
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

    def list_resources(self) -> list[dict[str, Any]]:
        result = self._request("resources/list", {"_meta": request_meta()})
        return [r for r in result.get("resources", []) if isinstance(r, dict)]

    def read_resource(self, uri: str) -> dict[str, Any]:
        return self._request("resources/read", {"uri": uri, "_meta": request_meta()}, tool_name=uri)

    def list_prompts(self) -> list[dict[str, Any]]:
        result = self._request("prompts/list", {"_meta": request_meta()})
        return [p for p in result.get("prompts", []) if isinstance(p, dict)]

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(
            "prompts/get",
            {"name": name, "arguments": arguments or {}, "_meta": request_meta()},
            tool_name=name,
        )


def _with_extension(meta: dict[str, Any], extension: str) -> dict[str, Any]:
    """A copy of *meta* whose client capabilities declare *extension*."""
    capabilities = dict(meta.get(META_CLIENT_CAPABILITIES) or {})
    capabilities["extensions"] = {**(capabilities.get("extensions") or {}), extension: {}}
    return {**meta, META_CLIENT_CAPABILITIES: capabilities}


def _cache_seconds(ttl_ms: Any) -> float:
    """How long a server said a cacheable result may be kept, clamped to [1s, 24h]."""
    try:
        raw = max(1.0, float(ttl_ms) / 1000.0) if ttl_ms is not None else 3600.0
        return min(raw, MAX_CACHE_TTL_S)
    except (TypeError, ValueError):
        return 3600.0


def remote_url_problem(url: str) -> str:
    """Why *url* may not be bound, or ``""`` when it may.

    A bearer token is sent to this address on every request, so outside
    development it has to be an address that TLS can vouch for.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return "it is not an http(s) URL"
    if parts.username or parts.password:
        return "credentials belong in MCP_SERVER_TOKEN_<NAMESPACE>, not in the URL"
    is_prod = os.getenv("APP_ENV", "development").lower() == "production"
    if parts.scheme != "https" and is_prod:
        return "production requires https"
    host = (parts.hostname or "").strip().lower()
    if is_prod and host:
        if host in {"localhost", "0.0.0.0"}:
            return "production forbids loopback or unspecified MCP server hosts"
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                return "production forbids loopback or link-local metadata MCP server IPs"
        except ValueError:
            pass
    return ""


def build_transports() -> dict[str, ToolTransport]:
    """Bind namespaces to transports from the environment.

    Any namespace without an ``MCP_SERVER_URL_*`` entry stays in-process,
    so an unconfigured deployment behaves exactly as before.
    """
    from ..tools import ToolRegistry

    in_process = InProcessTransport()
    transports: dict[str, ToolTransport] = {ns: in_process for ns in ToolRegistry.namespaces()}
    for key, value in os.environ.items():
        if not key.startswith(URL_ENV_PREFIX) or not value.strip():
            continue
        namespace = key[len(URL_ENV_PREFIX) :].lower()
        url = value.strip()
        problem = remote_url_problem(url)
        if problem:
            logger.error("MCP namespace %s is not bound to its remote server: %s", namespace, problem)
            continue
        token = os.getenv(f"{_TOKEN_ENV_PREFIX}{namespace.upper()}", "")
        transports[namespace] = HttpTransport(
            namespace,
            url,
            token,
            token_source=client_credentials_from_env(namespace, url.rstrip("/")),
        )
        logger.info("MCP namespace %s bound to remote server", namespace)
    return transports
