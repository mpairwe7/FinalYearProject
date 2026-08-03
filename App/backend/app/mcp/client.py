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

import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ..resilience import CircuitBreaker
from .policy import authorize_tool_call
from .transport import (
    MCP_PROTOCOL_VERSION,
    InProcessTransport,
    ToolTransport,
    TransportError,
    build_transports,
    request_meta,
)
from .validation import result_matches_schema, validate_arguments

logger = logging.getLogger(__name__)

#: Soft deadline for a single tool call.
DEFAULT_TIMEOUT_S = 15.0
#: How many completed critical calls to remember for replay.
_IDEMPOTENCY_CACHE_SIZE = 512


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


def _meta_of(descriptor: dict[str, Any], key: str, default: Any) -> Any:
    """Read one of our ``_meta`` fields off an MCP tool descriptor."""
    meta = descriptor.get("_meta") or {}
    return meta.get(f"ug.go.ura.chatbot/{key}", default)


class MCPClient:
    """Routing, authorizing, validating MCP client."""

    def __init__(self, transports: dict[str, ToolTransport] | None = None) -> None:
        self._transports = transports if transports is not None else build_transports()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._idempotency: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

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
            descriptor = transport.describe(name)
            if descriptor is None:
                continue
            bound = self._transports.get(str(_meta_of(descriptor, "namespace", "core")))
            if bound is not None and bound is not transport:
                remote_descriptor = bound.describe(name)
                if remote_descriptor is not None:
                    return remote_descriptor, bound
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
            for descriptor in transport.list_tools():
                name = str(descriptor.get("name", ""))
                if not name:
                    continue
                owner = self._transports.get(str(_meta_of(descriptor, "namespace", "core")))
                if name not in seen or owner is transport:
                    seen[name] = descriptor
        return [seen[name] for name in sorted(seen)]

    def describe_tool(self, name: str) -> dict[str, Any] | None:
        from ..tools import ToolRegistry

        tool = ToolRegistry.get(name)
        return tool.to_openai_spec() if tool is not None else None

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
    ) -> MCPCallResult:
        """Call a tool and return a structured result.

        Never raises: every failure mode — unknown tool, denied policy,
        invalid arguments, open circuit, transport error — comes back as
        a result the model can read and react to.
        """
        t0 = time.perf_counter()
        call_id = str(uuid.uuid4())
        args = dict(arguments or {})
        descriptor, transport = self._descriptor(name)

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

        if descriptor is None or transport is None:
            return finish(
                {
                    "ok": False,
                    "error": f"Unknown tool: {name}",
                    "available_tools": [t["name"] for t in self.list_mcp_tools()],
                },
                ok=False,
            )

        risk = str(_meta_of(descriptor, "risk", "low"))
        namespace = str(_meta_of(descriptor, "namespace", "core"))

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
            requires_confirmation=_meta_of(descriptor, "requiresConfirmation", None),
        )
        if not policy["allowed"]:
            return finish(
                {"ok": False, "error": "policy_denied", "policy": policy},
                ok=False,
                risk=risk,
                namespace=namespace,
            )

        errors = validate_arguments(descriptor.get("inputSchema"), args)
        if errors:
            return finish(
                {
                    "ok": False,
                    "error": f"Invalid arguments for {name}: " + "; ".join(errors),
                    "validation_errors": errors,
                    "expected": descriptor.get("inputSchema"),
                    "policy": policy,
                },
                ok=False,
                risk=risk,
                namespace=namespace,
            )

        replay_key = self._replay_key(name, tenant_id, idempotency_key)
        if replay_key is not None:
            cached = self._replay_lookup(replay_key)
            if cached is not None:
                logger.info("MCP replayed %s for idempotency key (no re-execution)", name)
                return finish(
                    cached, ok=bool(cached.get("ok", True)), risk=risk, namespace=namespace, replayed=True
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

        meta = request_meta(
            tenant_id=tenant_id, user_id=user_id, user_role=user_role, call_id=call_id
        )
        try:
            raw = transport.call(name, args, meta=meta, timeout_s=timeout_s)
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
        raw.setdefault("policy", policy)
        ok = bool(raw.get("ok", True))
        if ok:
            breaker.record_success()
        else:
            # A tool returning ok=false for bad user input is not a sick
            # dependency, but the breaker only trips after a run of them,
            # so a genuinely failing server is still caught.
            breaker.record_failure()

        drift = result_matches_schema(descriptor.get("outputSchema"), raw)
        if drift:
            logger.warning("MCP result for %s does not match its outputSchema: %s", name, drift[:3])

        if replay_key is not None and ok:
            self._replay_store(replay_key, raw)

        return finish(raw, ok=ok, risk=risk, namespace=namespace)

    # -- Idempotency ---------------------------------------------------
    @staticmethod
    def _replay_key(name: str, tenant_id: str, idempotency_key: str) -> str | None:
        """Replay is scoped by tenant so keys cannot collide across them."""
        if not idempotency_key:
            return None
        return f"{tenant_id}:{name}:{idempotency_key}"

    def _replay_lookup(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            cached = self._idempotency.get(key)
            if cached is not None:
                self._idempotency.move_to_end(key)
            return dict(cached) if cached is not None else None

    def _replay_store(self, key: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._idempotency[key] = dict(result)
            self._idempotency.move_to_end(key)
            while len(self._idempotency) > _IDEMPOTENCY_CACHE_SIZE:
                self._idempotency.popitem(last=False)

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
