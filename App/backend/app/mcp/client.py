"""MCP client — the agent layer's only door to tools.

Responsibilities, in the order a call passes through them:

1. **Route.**  A tool's namespace picks its transport (in-process or a
   remote MCP server), so migrating a tool out of the process is a
   config change, not a caller change.
2. **Authorize.**  Deny-by-default, driven by what the tool declares
   about itself.  This is the security boundary — discovery filtering
   is UX, and a model that names an unoffered tool still lands here.
3. **Validate.**  Arguments are checked against the tool's JSON Schema
   before dispatch, so a malformed call becomes a precise error the
   model can correct rather than an exception from inside the tool.
4. **Replay.**  A confirmed critical call carrying an idempotency key
   returns its first result on retry instead of acting twice.
5. **Guard.**  A per-namespace circuit breaker stops a failing server
   from being hammered, and every call is timed against a deadline.
6. **Account.**  Every call yields an :class:`MCPCallResult` whose
   :meth:`~MCPCallResult.to_audit_dict` is the audit ledger's payload.

The agent layer never imports :mod:`app.tools` directly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ..resilience import CircuitBreaker
from ..tracing import trace_tool_call
from .mrtr import parse_input_required, unwrap_request_state
from .policy import authorize_tool_call
from .transport import (
    MCP_PROTOCOL_VERSION,
    InProcessTransport,
    ToolTransport,
    TransportError,
    build_transports,
    request_meta,
)
from .validation import (
    build_validation_repair_hints,
    coerce_arguments_to_schema,
    compute_descriptor_sha256,
    inspect_descriptor_integrity,
    result_matches_schema,
    screen_tool_result,
    scrub_prompt_overrides_in_result,
    validate_arguments,
    validate_resource_uri,
)

logger = logging.getLogger(__name__)

#: Soft deadline for a single tool call.
DEFAULT_TIMEOUT_S = 15.0
#: How many completed critical calls to remember for replay (in-process fallback).
_IDEMPOTENCY_CACHE_SIZE = 512
_IDEMPOTENCY_REDIS_PREFIX = "mcp:idem:"
_IDEMPOTENCY_REDIS_TTL_S = int(os.getenv("MCP_IDEMPOTENCY_TTL_S", "86400"))
#: TTL (seconds) for pinned tool descriptors before legitimate redeploy re-pins.
MCP_DESCRIPTOR_PIN_TTL_S = int(os.getenv("MCP_DESCRIPTOR_PIN_TTL_S", "3600"))
_redis = None
_redis_tried = False


def _shared_store():
    """Redis when ``REDIS_URL`` is up; otherwise ``None`` (in-process cache)."""
    global _redis, _redis_tried
    if _redis_tried:
        return _redis
    _redis_tried = True
    url = os.getenv("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis  # noqa: PLC0415 — optional

        client = redis.from_url(url, socket_timeout=2)
        client.ping()
        _redis = client
    except Exception:
        logger.info("MCP idempotency: Redis unavailable; using in-process cache")
        _redis = None
    return _redis


@dataclass
class MCPCallResult:
    """Structured outcome of a single MCP tool call.

    Used as the audit-ledger payload and the agent's observation
    channel.  Every field is JSON-serialisable so the ledger can
    store it verbatim.
    """

    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    ok: bool
    duration_ms: float
    iteration: int = 0
    tenant_id: str = "default"
    user_id: str = ""
    risk_tier: str = "low"
    auth_checked: bool = True
    # Provenance — for Phase 21 audit ledger
    call_id: str = ""
    ts: float = field(default_factory=time.time)
    namespace: str = "core"
    transport: str = "in_process"
    replayed: bool = False
    deadline_exceeded: bool = False

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialise to the shape the audit ledger expects.

        Arguments and results are hashed rather than stored: a call can
        carry a TIN or a salary, and the ledger's job is to prove what
        ran, not to become a second copy of the taxpayer's data.
        """
        import hashlib
        import json

        args_json = json.dumps(self.arguments, sort_keys=True, default=str)
        result_json = json.dumps(self.result, sort_keys=True, default=str)[:4000]
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "namespace": self.namespace,
            "transport": self.transport,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "risk_tier": self.risk_tier,
            "ok": self.ok,
            "replayed": self.replayed,
            "deadline_exceeded": self.deadline_exceeded,
            "duration_ms": self.duration_ms,
            "iteration": self.iteration,
            "arguments_sha256": hashlib.sha256(args_json.encode()).hexdigest(),
            "result_sha256": hashlib.sha256(result_json.encode()).hexdigest(),
            "ts": self.ts,
        }


@dataclass(frozen=True)
class RemoteNamespace:
    """What discovery learned about one deployment-bound remote server."""

    namespace: str
    reachable: bool
    tools: tuple[dict[str, Any], ...] = ()
    #: Self-reported by the server (name, version, instructions).  For
    #: display and logs only — never an input to an authorization decision.
    server: dict[str, Any] = field(default_factory=dict)
    #: How this client authenticates to it (``HttpTransport.credential_kind``):
    #: a kind for staff diagnostics, never the credential.
    credential: str = ""


def _meta_of(descriptor: dict[str, Any], key: str, default: Any) -> Any:
    """Read one of our ``_meta`` fields off an MCP tool descriptor."""
    meta = descriptor.get("_meta")
    if not isinstance(meta, dict):
        return default
    return meta.get(f"ug.go.ura.chatbot/{key}", default)


class MCPClient:
    """Routing, authorizing, validating MCP client."""

    def __init__(self, transports: dict[str, ToolTransport] | None = None) -> None:
        self._transports = transports if transports is not None else build_transports()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._idempotency: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # Scoped by (namespace, tool_name) -> (sha256_digest, expires_at_monotonic, server_version)
        self._pinned_descriptors: dict[tuple[str, str], tuple[str, float, str]] = {}
        self._pin_rejections_count: int = 0
        self._lock = threading.Lock()

    def clear_descriptor_pins(
        self, namespace: str | None = None, *, user_role: str = "ura_admin"
    ) -> int:
        """Staff/admin control to clear pinned tool descriptors after a verified redeploy."""
        if user_role not in {"ura_staff", "ura_admin"}:
            raise PermissionError("Only ura_staff or ura_admin may clear MCP descriptor pins")
        with self._lock:
            if namespace is None:
                cleared = len(self._pinned_descriptors)
                self._pinned_descriptors.clear()
                return cleared
            keys = [k for k in self._pinned_descriptors if k[0] == namespace]
            for k in keys:
                self._pinned_descriptors.pop(k, None)
            return len(keys)

    def close(self) -> None:
        """Close all bound remote HTTP transports on shutdown."""
        for transport in set(self._transports.values()):
            close_fn = getattr(transport, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    logger.debug("MCP transport close ignored", exc_info=True)

    @staticmethod
    def _check_cross_namespace_shadowing(name: str, namespace: str) -> str | None:
        """Block a remote namespace from shadowing a tool owned by another namespace (OWASP MCP09)."""
        if not name or not namespace:
            return None
        from ..tools import ToolRegistry

        registered = ToolRegistry.get(name)
        if registered is not None and registered.schema.namespace != namespace:
            return (
                f"cross_namespace_tool_shadowing:{name}:"
                f"canonical={registered.schema.namespace}!=transport={namespace}"
            )
        return None

    def _verify_and_pin_descriptor(
        self, descriptor: dict[str, Any], *, server_version: str = ""
    ) -> list[str]:
        """Verify tool descriptor integrity, namespace ownership, and TTL/version-scoped rug-pull pinning."""
        violations = inspect_descriptor_integrity(descriptor)
        if violations:
            with self._lock:
                self._pin_rejections_count += 1
            return violations
        name = str(descriptor.get("name", ""))
        if not name:
            return ["missing_tool_name"]
        namespace = str(_meta_of(descriptor, "namespace", "core"))
        shadow_err = self._check_cross_namespace_shadowing(name, namespace)
        if shadow_err:
            with self._lock:
                self._pin_rejections_count += 1
            return [shadow_err]
        digest = compute_descriptor_sha256(descriptor)
        version = str(
            server_version
            or (descriptor.get("_meta") or {}).get("io.modelcontextprotocol/serverVersion", "")
            or ""
        )
        now = time.monotonic()
        pin_key = (namespace, name)
        with self._lock:
            existing = self._pinned_descriptors.get(pin_key)
            if existing is not None:
                prev_digest, expires_at, prev_version = existing
                # Legitimate redeploy with a new server_version or expired TTL refreshes the pin
                if now < expires_at and (not version or version == prev_version) and prev_digest != digest:
                    self._pin_rejections_count += 1
                    logger.warning(
                        "MCP descriptor pin rejected namespace=%s tool=%s rejections=%d",
                        namespace,
                        name,
                        self._pin_rejections_count,
                    )
                    return [f"rug_pull_detected:{namespace}:{name}:expected={prev_digest[:12]}..got={digest[:12]}"]
            self._pinned_descriptors[pin_key] = (digest, now + MCP_DESCRIPTOR_PIN_TTL_S, version)
        return []

    # -- Routing -------------------------------------------------------
    def _unique_transports(self) -> list[ToolTransport]:
        """Each bound transport once, namespace-bound remotes first.

        A remote binding must win over a tool that also happens to still
        be registered in-process, otherwise setting the env var would
        silently keep calling the local copy.
        """
        remote = [t for t in self._transports.values() if not isinstance(t, InProcessTransport)]
        local = [t for t in self._transports.values() if isinstance(t, InProcessTransport)]
        return list(dict.fromkeys(remote + local))

    def _transport_for(self, namespace: str) -> ToolTransport:
        transport = self._transports.get(namespace)
        if transport is None:
            transport = InProcessTransport()
            self._transports[namespace] = transport
        return transport

    def _breaker_for(self, namespace: str) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(namespace)
            if breaker is None:
                breaker = CircuitBreaker(name=f"mcp:{namespace}", failure_threshold=3)
                self._breakers[namespace] = breaker
            return breaker

    def _descriptor(self, name: str) -> tuple[dict[str, Any] | None, ToolTransport | None]:
        """Find a tool's descriptor and the transport that owns it.

        The descriptor tells us the tool's namespace; the namespace tells
        us which transport is authoritative for it.  When those differ —
        a tool still registered locally whose namespace has since been
        bound to a remote server — the remote wins.
        """
        for transport in self._unique_transports():
            try:
                raw_descriptor = transport.describe(name)
            except TransportError:
                # A server that is down does not own this lookup; the tool
                # may belong to another transport.
                continue
            if raw_descriptor is None:
                continue
            descriptor = self._with_transport_namespace(
                raw_descriptor,
                str(getattr(transport, "namespace", "")),
            )
            bound = self._transports.get(str(_meta_of(descriptor, "namespace", "core")))
            if bound is not None and bound is not transport:
                # Not caught: if the server this namespace is bound to
                # cannot be asked, the call fails.  Falling back to the
                # copy still registered in this process would run the
                # tool somewhere the deployment said it must not run.
                remote_descriptor = bound.describe(name)
                if remote_descriptor is not None:
                    return (
                        self._with_transport_namespace(
                            remote_descriptor,
                            str(getattr(bound, "namespace", "")),
                        ),
                        bound,
                    )
                # The server answered and does not serve this tool (a release
                # that left it out, or a header annotation that made it
                # invalid). It is not ours to run here either.
                return None, None
            return descriptor, transport
        return None, None

    # -- Discovery -----------------------------------------------------
    def list_tools(self, allow_risk: list[str] | None = None) -> list[dict[str, Any]]:
        """Return OpenAI/Qwen-compatible tool schemas, optionally risk-filtered."""
        from ..tools import ToolRegistry

        return ToolRegistry.openai_specs(allow_risk=allow_risk)

    def list_mcp_tools(self) -> list[dict[str, Any]]:
        """Return MCP ``Tool`` descriptors across every bound transport."""
        seen: dict[str, dict[str, Any]] = {}
        for transport in self._unique_transports():
            namespace = str(getattr(transport, "namespace", ""))
            try:
                descriptors = transport.list_tools()
            except Exception as exc:  # noqa: BLE001 — one connector must not hide the others
                logger.warning(
                    "MCP tool discovery failed namespace=%s (%s)",
                    namespace or "local",
                    type(exc).__name__,
                )
                continue
            for raw_descriptor in descriptors:
                descriptor = self._with_transport_namespace(raw_descriptor, namespace)
                name = str(descriptor.get("name", ""))
                if not name:
                    continue
                integrity_errors = self._verify_and_pin_descriptor(descriptor)
                if integrity_errors:
                    logger.warning(
                        "MCP tool discovery rejected descriptor name=%s namespace=%s errors=%s",
                        name,
                        namespace or "local",
                        integrity_errors,
                    )
                    continue
                owner = self._transports.get(str(_meta_of(descriptor, "namespace", "core")))
                if name not in seen or owner is transport:
                    seen[name] = descriptor
        return [seen[name] for name in sorted(seen)]

    @staticmethod
    def _with_transport_namespace(
        descriptor: dict[str, Any], namespace: str,
    ) -> dict[str, Any]:
        """Bind a remote descriptor to the namespace configured by URA.

        The deployment binding is authoritative. This also lets ordinary MCP
        servers omit URA's private ``_meta`` namespace field while preserving
        the standard tool descriptor sent to the model.
        """
        normalized = dict(descriptor)
        if namespace:
            raw_meta = normalized.get("_meta")
            meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
            meta["ug.go.ura.chatbot/namespace"] = namespace
            normalized["_meta"] = meta
        return normalized

    def _probe_remote(self, namespace: str, transport: ToolTransport) -> RemoteNamespace:
        credential = str(getattr(transport, "credential_kind", "") or "")
        try:
            descriptors = transport.list_tools()
        except Exception as exc:  # noqa: BLE001 — fail closed per server
            logger.warning(
                "MCP tool discovery failed namespace=%s (%s)",
                namespace,
                type(exc).__name__,
            )
            return RemoteNamespace(namespace, reachable=False, credential=credential)
        # The tool list may come from a cache the server allowed for an hour;
        # whether the server is answering *now* is a separate, shorter-lived
        # question (HttpTransport.is_live).
        is_live = getattr(transport, "is_live", None)
        if callable(is_live) and not is_live():
            return RemoteNamespace(namespace, reachable=False, credential=credential)
        tools = tuple(
            self._with_transport_namespace(descriptor, namespace)
            for descriptor in descriptors
            if isinstance(descriptor, dict) and descriptor.get("name")
        )
        server: dict[str, Any] = {}
        discover = getattr(transport, "discover", None)
        if callable(discover):
            try:
                discovered = discover()
            except Exception:  # noqa: BLE001 — identity is optional; the tools are not
                discovered = {}
            info = (discovered.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo")
            if isinstance(info, dict):
                server = {"name": str(info.get("name", "")), "version": str(info.get("version", ""))}
            if isinstance(discovered.get("instructions"), str):
                server["instructions"] = discovered["instructions"]
        return RemoteNamespace(namespace, reachable=True, tools=tools, server=server, credential=credential)

    def remote_namespace_names(self) -> set[str]:
        """The namespaces bound to a remote server, without asking any of them."""
        return {
            namespace
            for namespace, transport in self._transports.items()
            if not isinstance(transport, InProcessTransport)
        }

    def remote_namespaces(self) -> list[RemoteNamespace]:
        """Every deployment-bound remote server, reachable or not.

        Servers are asked side by side: with several connectors bound, a
        catalog request must cost one slow server's deadline, not the
        sum of all of them.
        """
        remotes = [
            (namespace, transport)
            for namespace, transport in sorted(self._transports.items())
            if not isinstance(transport, InProcessTransport)
        ]
        if len(remotes) <= 1:
            return [self._probe_remote(namespace, transport) for namespace, transport in remotes]
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(len(remotes), 8), thread_name_prefix="mcp-discovery") as pool:
            return list(pool.map(lambda remote: self._probe_remote(*remote), remotes))

    def remote_tool_descriptors(self) -> list[tuple[str, dict[str, Any]]]:
        """List tools from deployment-bound remote MCP servers only.

        A failed server is omitted from discovery without preventing other
        configured connectors from appearing.
        """
        return [
            (remote.namespace, descriptor)
            for remote in self.remote_namespaces()
            for descriptor in remote.tools
        ]

    def describe_tool(self, name: str) -> dict[str, Any] | None:
        from ..tools import ToolRegistry

        tool = ToolRegistry.get(name)
        return tool.to_openai_spec() if tool is not None else None

    # -- Resources & Prompts (MCP 2026-07-28 Primitives) ---------------
    def list_resources(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """List all read-only data resources exposed across MCP transports."""
        resources: list[dict[str, Any]] = []
        seen_uris: set[str] = set()
        transports = (
            [self._transports[namespace]]
            if namespace and namespace in self._transports
            else list(self._transports.values())
        )
        for transport in transports:
            if hasattr(transport, "list_resources"):
                for r in transport.list_resources():
                    uri = r.get("uri")
                    if uri and uri not in seen_uris:
                        seen_uris.add(uri)
                        resources.append(r)
        return resources

    def read_resource(self, uri: str, *, tenant_id: str = "default", user_id: str = "") -> dict[str, Any]:
        """Read the contents of a URI-addressable MCP resource with scheme and output screening."""
        uri_err = validate_resource_uri(uri)
        if uri_err:
            if uri_err.startswith(("disallowed_resource_scheme", "missing_resource_scheme")):
                raise LookupError(f"No transport supports reading resource URI '{uri}'")
            raise ValueError(f"Invalid resource URI '{uri}': {uri_err}")
        scheme = uri.split("://")[0].lower() if "://" in uri else ""
        transport = self._transports.get(scheme, self._transports.get("core", next(iter(self._transports.values()))))
        if hasattr(transport, "read_resource"):
            res = transport.read_resource(uri)
            violations = screen_tool_result(res)
            if violations:
                hard = [
                    v
                    for v in violations
                    if not v.startswith("indirect_prompt_injection:prompt_override_directive@")
                ]
                if hard:
                    raise ValueError(f"Resource '{uri}' blocked by output safety screen: {hard[:3]}")
                res = scrub_prompt_overrides_in_result(res)
            return res
        raise LookupError(f"No transport supports reading resource URI '{uri}'")

    def list_prompts(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """List all prompt templates exposed across MCP transports."""
        prompts: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        transports = (
            [self._transports[namespace]]
            if namespace and namespace in self._transports
            else list(self._transports.values())
        )
        for transport in transports:
            if hasattr(transport, "list_prompts"):
                for p in transport.list_prompts():
                    p_name = p.get("name")
                    if p_name and p_name not in seen_names:
                        seen_names.add(p_name)
                        prompts.append(p)
        return prompts

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch and instantiate a prompt template by name."""
        for transport in self._transports.values():
            if hasattr(transport, "get_prompt"):
                try:
                    return transport.get_prompt(name, arguments)
                except LookupError:
                    continue
        raise LookupError(f"Prompt template '{name}' not found")

    def available_for(
        self,
        user_role: str = "public",
        granted_purposes: list[str] | None = None,
    ) -> list[str]:
        """Return the tool names this user is allowed to see.

        Runs the same policy the dispatcher runs, so discovery and
        enforcement cannot drift apart.  Confirmation and idempotency
        are treated as satisfied here — those are properties of a
        specific call, not of whether the tool should be offered at all.
        """
        purposes = list(granted_purposes or [])
        # A role beyond "public" implies an authenticated principal; the
        # real user id is checked again at dispatch.
        probe_user = "" if user_role == "public" else "discovery-probe"
        allowed: list[str] = []
        for descriptor in self.list_mcp_tools():
            name = str(descriptor.get("name", ""))
            if not name:
                continue
            decision = authorize_tool_call(
                name=name,
                risk=_meta_of(descriptor, "risk", "low"),
                user_role=user_role,
                granted_purposes=purposes,
                user_id=probe_user,
                confirmed=True,
                idempotency_key="discovery-probe",
                required_scopes=tuple(_meta_of(descriptor, "requiredScopes", ()) or ()),
                allowed_roles=tuple(_meta_of(descriptor, "allowedRoles", ()) or ()),
                scope_exempt_roles=tuple(_meta_of(descriptor, "scopeExemptRoles", ()) or ()),
                requires_confirmation=_meta_of(descriptor, "requiresConfirmation", None),
            )
            if decision["allowed"]:
                allowed.append(name)
        return allowed

    # -- Dispatch ------------------------------------------------------
    def call_tool(  # noqa: PLR0913 - a call's full security context
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tenant_id: str = "default",
        user_id: str = "",
        user_role: str = "public",
        granted_purposes: list[str] | None = None,
        confirmed: bool = False,
        idempotency_key: str = "",
        iteration: int = 0,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        input_responses: list[Any] | None = None,
        request_state: Any = None,
    ) -> MCPCallResult:
        """Call a tool and return a structured result.

        Never raises: every failure mode — unknown tool, denied policy,
        invalid arguments, open circuit, transport error — comes back as
        a result the model can read and react to. Every call is one
        ``execute_tool {name}`` span and one ``ura_tool_calls_total`` sample.
        """
        with trace_tool_call(name) as outcome:
            result = self._call_tool(
                name,
                arguments,
                tenant_id=tenant_id,
                user_id=user_id,
                user_role=user_role,
                granted_purposes=granted_purposes,
                confirmed=confirmed,
                idempotency_key=idempotency_key,
                iteration=iteration,
                timeout_s=timeout_s,
                input_responses=input_responses,
                request_state=request_state,
            )
            outcome["ok"] = bool(result.ok)
            return result

    def _call_tool(  # noqa: PLR0913 - a call's full security context
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
        user_role: str,
        granted_purposes: list[str] | None,
        confirmed: bool,
        idempotency_key: str,
        iteration: int,
        timeout_s: float,
        input_responses: list[Any] | None,
        request_state: Any,
    ) -> MCPCallResult:
        t0 = time.perf_counter()
        call_id = str(uuid.uuid4())
        args = dict(arguments or {})
        transport: ToolTransport | None = None

        def finish(
            result: dict[str, Any],
            *,
            ok: bool,
            risk: str = "low",
            namespace: str = "core",
            replayed: bool = False,
        ) -> MCPCallResult:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return MCPCallResult(
                tool_name=name,
                arguments=args,
                result=result,
                ok=ok,
                duration_ms=round(elapsed_ms, 2),
                iteration=iteration,
                tenant_id=tenant_id,
                user_id=user_id,
                risk_tier=risk,
                auth_checked=True,
                call_id=call_id,
                namespace=namespace,
                transport=transport.name if transport is not None else "none",
                replayed=replayed,
                deadline_exceeded=elapsed_ms > timeout_s * 1000,
            )

        try:
            descriptor, transport = self._descriptor(name)
        except TransportError:
            return finish(
                {
                    "ok": False,
                    "error": f"The service that provides '{name}' is temporarily unavailable.",
                    "retryable": True,
                },
                ok=False,
            )
        if descriptor is None or transport is None:
            return finish(
                {"ok": False, "error": f"Unknown tool: {name}"},
                ok=False,
            )

        risk = str(_meta_of(descriptor, "risk", "low"))
        namespace = str(_meta_of(descriptor, "namespace", "core"))

        integrity_errors = self._verify_and_pin_descriptor(descriptor)
        if integrity_errors:
            return finish(
                {
                    "ok": False,
                    "error": "descriptor_integrity_failed",
                    "violations": integrity_errors,
                },
                ok=False,
                risk=risk,
                namespace=namespace,
            )

        requires_confirmation = bool(
            _meta_of(descriptor, "requiresConfirmation", False)
        )
        # A write proposal must pass role and consent checks before it is
        # shown to the user, but the proposal itself is not a write. The
        # approval replay below is the only call that satisfies the
        # confirmation and idempotency requirements.
        policy = authorize_tool_call(
            name=name,
            risk=risk,
            user_role=user_role,
            granted_purposes=granted_purposes or [],
            user_id=user_id,
            tenant_id=tenant_id,
            confirmed=confirmed,
            idempotency_key=idempotency_key,
            required_scopes=tuple(_meta_of(descriptor, "requiredScopes", ()) or ()),
            allowed_roles=tuple(_meta_of(descriptor, "allowedRoles", ()) or ()),
            scope_exempt_roles=tuple(_meta_of(descriptor, "scopeExemptRoles", ()) or ()),
            requires_confirmation=requires_confirmation and confirmed,
            arguments=args,
        )
        if not policy["allowed"]:
            return finish(
                {"ok": False, "error": "policy_denied", "policy": policy},
                ok=False,
                risk=risk,
                namespace=namespace,
            )

        input_schema = descriptor.get("inputSchema") or {}
        declared_properties = set((input_schema.get("properties") or {}).keys())
        validation_args = dict(args)
        # These are MCP dispatch controls, not tool inputs. Older tools do
        # not declare them in their schemas; strip them only in that case so
        # strict additionalProperties validation remains useful.
        for control in ("submit", "idempotency_key"):
            if control not in declared_properties:
                validation_args.pop(control, None)
        validation_args = coerce_arguments_to_schema(input_schema, validation_args)
        errors = validate_arguments(input_schema, validation_args)
        if errors:
            return finish(
                {
                    "ok": False,
                    "error": f"Invalid arguments for {name}: " + "; ".join(errors),
                    "validation_errors": errors,
                    "repair_hints": build_validation_repair_hints(
                        input_schema, validation_args, errors
                    ),
                    "expected": input_schema,
                    "policy": policy,
                },
                ok=False,
                risk=risk,
                namespace=namespace,
            )

        if requires_confirmation and not confirmed:
            proposal = dict(validation_args)
            proposal["idempotency_key"] = idempotency_key or uuid.uuid4().hex
            proposal_policy = dict(policy)
            proposal_policy["requires_confirmation"] = True
            return finish(
                {
                    "ok": True,
                    "submitted": False,
                    "requires_confirmation": True,
                    "proposal": proposal,
                    "message": "No connector action has run. Review and confirm this proposal to continue.",
                    "policy": proposal_policy,
                },
                ok=True,
                risk=risk,
                namespace=namespace,
            )

        args_digest = hashlib.sha256(
            json.dumps(validation_args, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        replay_key = self._replay_key(name, tenant_id, idempotency_key, user_id=user_id)
        if replay_key is not None:
            cached = self._replay_lookup(replay_key)
            if cached is not None:
                cached_digest = cached.pop("_mcp_args_sha256", None)
                if cached_digest and cached_digest != args_digest:
                    return finish(
                        {
                            "ok": False,
                            "error": "idempotency_key_conflict",
                            "detail": f"Idempotency key for '{name}' was already used with different arguments.",
                            "policy": policy,
                        },
                        ok=False,
                        risk=risk,
                        namespace=namespace,
                    )
                logger.info("MCP replayed %s for idempotency key (no re-execution)", name)
                return finish(
                    cached, ok=bool(cached.get("ok", True)), risk=risk, namespace=namespace, replayed=True
                )

        meta = request_meta(
            tenant_id=tenant_id,
            user_id=user_id,
            user_role=user_role,
            call_id=call_id,
            confirmed=confirmed,
            idempotency_key=idempotency_key,
            granted_purposes=granted_purposes,
        )
        if request_state is not None:
            if (
                unwrap_request_state(
                    request_state,
                    expected_tool=name,
                    expected_namespace=namespace,
                    expected_tenant=tenant_id,
                    expected_user=user_id,
                )
                is None
            ):
                return finish(
                    {
                        "ok": False,
                        "error": "requestState integrity check failed",
                        "policy": policy,
                    },
                    ok=False,
                    risk=risk,
                    namespace=namespace,
                )

        breaker = self._breaker_for(namespace)
        if not breaker.allow_request():
            return finish(
                {
                    "ok": False,
                    "error": f"{namespace} tools are temporarily unavailable (circuit open)",
                    "retryable": True,
                    "policy": policy,
                },
                ok=False,
                risk=risk,
                namespace=namespace,
            )

        try:
            declared_args = set((input_schema.get("properties") or {}).keys())
            dispatch_args = dict(validation_args)
            if "submit" in declared_args and "submit" in args:
                dispatch_args["submit"] = args["submit"]
            if "idempotency_key" in declared_args and "idempotency_key" in args:
                dispatch_args["idempotency_key"] = args["idempotency_key"]
            raw = transport.call(
                name,
                dispatch_args,
                meta=meta,
                timeout_s=timeout_s,
                input_responses=input_responses,
                request_state=request_state,
            )
        except TransportError as exc:
            breaker.record_failure()
            return finish(
                {"ok": False, "error": str(exc), "retryable": True, "policy": policy},
                ok=False,
                risk=risk,
                namespace=namespace,
            )
        except Exception as exc:  # noqa: BLE001 - tools are sandboxed
            breaker.record_failure()
            logger.exception("MCP dispatch of %s raised", name)
            return finish(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "policy": policy},
                ok=False,
                risk=risk,
                namespace=namespace,
            )

        if not isinstance(raw, dict):
            raw = {"ok": True, "result": raw}
        required = parse_input_required(raw)
        if required is not None and not input_responses:
            raw = required
        elif required is not None and input_responses:
            # Already retried with responses; do not loop.
            raw = required
        raw.setdefault("policy", policy)
        # An elicitation is not a finished calculation, so it is not checked
        # against the tool's output schema.
        drift: list[str] = []
        if raw.get("resultType") != "input_required":
            drift = result_matches_schema(descriptor.get("outputSchema"), raw)
        if drift:
            logger.warning("MCP result for %s does not match its outputSchema: %s", name, drift[:3])
            raw = {
                "ok": False,
                "error": "tool result did not match its output schema",
                "validation_errors": drift[:8],
                "policy": policy,
            }
        else:
            output_violations = screen_tool_result(raw)
            if output_violations:
                hard_violations = [
                    v
                    for v in output_violations
                    if not v.startswith("indirect_prompt_injection:prompt_override_directive@")
                ]
                if hard_violations:
                    logger.warning(
                        "MCP result for %s blocked by output safety screen: %s",
                        name,
                        hard_violations[:3],
                    )
                    raw = {
                        "ok": False,
                        "error": "untrusted_tool_output",
                        "violations": hard_violations[:8],
                        "policy": policy,
                    }
                else:
                    raw = scrub_prompt_overrides_in_result(raw)
        ok = bool(raw.get("ok", True))
        # Transport completed without raising; finalize any HALF_OPEN probe and
        # keep the circuit closed (tool-level ok=false is not a transport fault).
        breaker.record_success()

        if replay_key is not None and ok:
            self._replay_store(replay_key, raw, args_sha256=args_digest)

        return finish(raw, ok=ok, risk=risk, namespace=namespace)

    # -- Idempotency ---------------------------------------------------
    @staticmethod
    def _replay_key(name: str, tenant_id: str, idempotency_key: str, *, user_id: str = "") -> str | None:
        """Replay is scoped by (tenant_id, user_id, tool_name, idempotency_key) so keys never leak across users."""
        if not idempotency_key:
            return None
        scoped_user = user_id or "anon"
        return f"{tenant_id}:{scoped_user}:{name}:{idempotency_key}"

    def _replay_lookup(self, key: str) -> dict[str, Any] | None:
        store = _shared_store()
        if store is not None:
            try:
                raw = store.get(_IDEMPOTENCY_REDIS_PREFIX + key)
                if raw:
                    data = json.loads(raw)
                    return dict(data) if isinstance(data, dict) else None
            except Exception:
                logger.debug("MCP idempotency redis get failed", exc_info=True)
        with self._lock:
            cached = self._idempotency.get(key)
            if cached is not None:
                self._idempotency.move_to_end(key)
            return dict(cached) if cached is not None else None

    def _replay_store(self, key: str, result: dict[str, Any], *, args_sha256: str = "") -> None:
        payload = dict(result)
        if args_sha256:
            payload["_mcp_args_sha256"] = args_sha256
        store = _shared_store()
        if store is not None:
            try:
                store.setex(
                    _IDEMPOTENCY_REDIS_PREFIX + key,
                    _IDEMPOTENCY_REDIS_TTL_S,
                    json.dumps(payload, default=str),
                )
            except Exception:
                logger.debug("MCP idempotency redis set failed", exc_info=True)
        with self._lock:
            self._idempotency[key] = payload
            self._idempotency.move_to_end(key)
            while len(self._idempotency) > _IDEMPOTENCY_CACHE_SIZE:
                self._idempotency.popitem(last=False)

    # -- Long-Horizon Multi-Server ToolAthlon Chain Executor -----------
    _CHAIN_SLOT_ALIASES: dict[str, tuple[str, ...]] = {
        "amount_ugx": (
            "amount_ugx",
            "ugx_amount",
            "converted_amount_ugx",
            "total_tax_ugx",
            "vat",
            "vat_amount",
            "vat_payable",
            "paye",
            "paye_tax",
            "net_tax",
            "tax_payable",
            "wht_amount",
            "presumptive_tax",
            "corporation_tax",
            "rental_tax",
            "total_duty_ugx",
            "stamp_duty_ugx",
            "amount",
        ),
        "amount": (
            "amount",
            "ugx_amount",
            "converted_amount_ugx",
            "amount_ugx",
            "vat",
            "vat_amount",
            "paye",
            "tax_payable",
        ),
        "gross_income": ("gross_income", "ugx_amount", "converted_amount_ugx", "amount_ugx", "monthly_gross"),
        "monthly_gross": ("monthly_gross", "ugx_amount", "converted_amount_ugx", "amount_ugx", "gross_income"),
        "tin": ("tin", "taxpayer_tin", "issued_tin", "verified_tin"),
        "taxpayer_tin": ("taxpayer_tin", "tin", "issued_tin", "verified_tin"),
        "brn": ("brn", "registration_number", "business_registration_number"),
        "prn": ("prn", "generated_prn", "payment_registration_number"),
        "taxpayer_name": ("taxpayer_name", "business_name", "entity_name", "full_name", "name"),
        "business_name": ("business_name", "entity_name", "taxpayer_name"),
    }

    def execute_tool_chain(  # noqa: PLR0913 - multi-step chain execution context
        self,
        steps: list[dict[str, Any]],
        *,
        tenant_id: str = "default",
        user_id: str = "",
        user_role: str = "public",
        granted_purposes: list[str] | None = None,
        initial_context: dict[str, Any] | None = None,
        max_steps: int = 10,
        timeout_per_step_s: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Execute a multi-step, cross-namespace ToolAthlon tool chain with automatic slot binding.

        Each item in *steps* is ``{"name": "<tool_name>", "arguments": {...},
        "bind_from": {"<arg_name>": "<prev_step_or_key>"}, "confirmed": bool,
        "idempotency_key": str}``.

        - Automatically resolves missing declared arguments from ``shared_context``
          (populated by prior steps' outputs and *initial_context*) via explicit
          ``bind_from`` mappings or :attr:`_CHAIN_SLOT_ALIASES`.
        - Stops fail-closed at the first step that fails (``ok=False``), requires
          confirmation (``requires_confirmation=True`` and ``submitted=False``),
          or requests elicitation (``input_required=True``).
        """
        t0 = time.perf_counter()
        if not steps:
            return {
                "ok": False,
                "status": "empty_chain",
                "completed_steps": [],
                "namespaces_traversed": [],
                "shared_context": dict(initial_context or {}),
                "audit_trail": [],
                "total_duration_ms": 0.0,
            }
        if len(steps) > max_steps:
            return {
                "ok": False,
                "status": "chain_too_long",
                "error": f"Tool chain length ({len(steps)}) exceeds max_steps ({max_steps})",
                "completed_steps": [],
                "namespaces_traversed": [],
                "shared_context": dict(initial_context or {}),
                "audit_trail": [],
                "total_duration_ms": 0.0,
            }

        shared_context: dict[str, Any] = dict(initial_context or {})
        step_outputs: dict[str, dict[str, Any]] = {}
        completed_steps: list[dict[str, Any]] = []
        namespaces_traversed: list[str] = []
        audit_trail: list[dict[str, Any]] = []

        for idx, step in enumerate(steps):
            tool_name = str(step.get("name") or "").strip()
            if not tool_name:
                return {
                    "ok": False,
                    "status": "invalid_step",
                    "halted_at_step": idx,
                    "error": f"Step {idx} is missing tool 'name'",
                    "completed_steps": completed_steps,
                    "namespaces_traversed": namespaces_traversed,
                    "shared_context": shared_context,
                    "audit_trail": audit_trail,
                    "total_duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            step_args = dict(step.get("arguments") or {})
            bind_from = step.get("bind_from")
            if isinstance(bind_from, dict):
                for target_arg, source_ref in bind_from.items():
                    if target_arg in step_args and step_args[target_arg] is not None:
                        continue
                    ref_str = str(source_ref or "")
                    if "." in ref_str:
                        src_step, src_field = ref_str.split(".", 1)
                        src_dict = step_outputs.get(src_step, {})
                        if src_field in src_dict and src_dict[src_field] is not None:
                            step_args[target_arg] = src_dict[src_field]
                    elif ref_str in shared_context and shared_context[ref_str] is not None:
                        step_args[target_arg] = shared_context[ref_str]

            descriptor, _ = self._descriptor(tool_name)
            if descriptor is not None:
                input_schema = descriptor.get("inputSchema") or {}
                props = (input_schema.get("properties") or {}) if isinstance(input_schema, dict) else {}
                for prop_name in props:
                    if prop_name in step_args and step_args[prop_name] is not None:
                        continue
                    candidates = self._CHAIN_SLOT_ALIASES.get(prop_name, (prop_name,))
                    for cand_key in candidates:
                        if cand_key in shared_context and shared_context[cand_key] is not None:
                            step_args[prop_name] = shared_context[cand_key]
                            break

            call_res = self.call_tool(
                tool_name,
                step_args,
                tenant_id=tenant_id,
                user_id=user_id,
                user_role=user_role,
                granted_purposes=granted_purposes,
                confirmed=bool(step.get("confirmed", False)),
                idempotency_key=str(step.get("idempotency_key") or ""),
                iteration=idx,
                timeout_s=timeout_per_step_s,
                input_responses=step.get("input_responses"),
                request_state=step.get("request_state"),
            )
            audit_trail.append(call_res.to_audit_dict())
            if call_res.namespace not in namespaces_traversed:
                namespaces_traversed.append(call_res.namespace)

            res_payload = call_res.result
            step_record = {
                "step_index": idx,
                "tool_name": tool_name,
                "namespace": call_res.namespace,
                "arguments": step_args,
                "result": res_payload,
                "ok": call_res.ok,
                "replayed": call_res.replayed,
                "duration_ms": call_res.duration_ms,
            }

            if res_payload.get("requires_confirmation") is True and res_payload.get("submitted") is False:
                return {
                    "ok": True,
                    "status": "awaiting_confirmation",
                    "halted_at_step": idx,
                    "pending_step": step_record,
                    "pending_proposal": res_payload.get("proposal"),
                    "completed_steps": completed_steps,
                    "namespaces_traversed": namespaces_traversed,
                    "shared_context": shared_context,
                    "audit_trail": audit_trail,
                    "total_duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            if res_payload.get("input_required") is True:
                return {
                    "ok": False,
                    "status": "input_required",
                    "halted_at_step": idx,
                    "pending_step": step_record,
                    "input_requests": res_payload.get("inputRequests"),
                    "request_state": res_payload.get("requestState"),
                    "completed_steps": completed_steps,
                    "namespaces_traversed": namespaces_traversed,
                    "shared_context": shared_context,
                    "audit_trail": audit_trail,
                    "total_duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            if not call_res.ok:
                return {
                    "ok": False,
                    "status": "step_failed",
                    "halted_at_step": idx,
                    "failed_step": step_record,
                    "error": res_payload.get("error") or f"Step {idx} ({tool_name}) failed",
                    "completed_steps": completed_steps,
                    "namespaces_traversed": namespaces_traversed,
                    "shared_context": shared_context,
                    "audit_trail": audit_trail,
                    "total_duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                }

            completed_steps.append(step_record)
            step_key = str(step.get("id") or tool_name)
            flattened: dict[str, Any] = {}
            for k, v in res_payload.items():
                if k in ("ok", "policy", "mode", "live", "simulation_notice") or v is None:
                    continue
                if isinstance(v, dict):
                    for sub_k, sub_v in v.items():
                        if sub_v is not None and not isinstance(sub_v, dict | list):
                            flattened[sub_k] = sub_v
                            shared_context[sub_k] = sub_v
                else:
                    flattened[k] = v
                    shared_context[k] = v
            step_outputs[step_key] = flattened
            step_outputs[tool_name] = flattened

        return {
            "ok": True,
            "status": "completed",
            "completed_steps": completed_steps,
            "namespaces_traversed": namespaces_traversed,
            "shared_context": shared_context,
            "audit_trail": audit_trail,
            "total_duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    # -- Health --------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Per-namespace transport binding and breaker state, for /health."""
        return {
            "protocol_version": MCP_PROTOCOL_VERSION,
            "namespaces": {
                namespace: {
                    "transport": transport.name,
                    "circuit": self._breakers[namespace].state.value
                    if namespace in self._breakers
                    else "closed",
                }
                for namespace, transport in sorted(self._transports.items())
            },
        }

    def ping(self, namespace: str = "tax_calculator", timeout_s: float = 5.0) -> bool:
        """Diagnostic reachability probe for an MCP namespace.

        A remote server is probed with ``server/discover``; the protocol's
        own ``ping`` was removed in 2026-07-28.
        """
        transport = self._transport_for(namespace)
        if isinstance(transport, InProcessTransport):
            return True
        probe = getattr(transport, "ping", None)
        if not callable(probe):
            return False
        try:
            return bool(probe(timeout_s=timeout_s))
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Module-level singleton so the agent layer always sees the same client.
# ---------------------------------------------------------------------------
_client: MCPClient | None = None
_client_lock = threading.Lock()


def get_client() -> MCPClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MCPClient()
    return _client


def reset_client() -> None:
    """Testing hook — forces re-creation."""
    global _client
    with _client_lock:
        _client = None
