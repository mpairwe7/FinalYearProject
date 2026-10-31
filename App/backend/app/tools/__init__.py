"""Tool framework for agentic AI workflows (Phase 14-A).

This package is the foundation for the supervisor-specialist pattern
described in ``docs/GAPS_AND_AGENTIC_ROADMAP.md``.  Each tool is:

- **Deterministic** (or returns ``{"ok": false, "error": ...}`` on
  failure) so the LLM sees a bounded, inspectable result.
- **JSON-schema described** so Qwen2.5's native function-calling
  (``tokenizer.apply_chat_template(..., tools=[...])``) can invoke
  it without freeform parsing.
- **Isolated from network I/O by default** — each tool that needs
  live data must declare it explicitly so the circuit breaker +
  rate limit layers can guard it.
- **Self-registering on import** so simply adding a new
  ``backend/app/tools/foo.py`` module (with ``from . import foo``
  in this file) makes it available.

A tool is invoked by the tool-calling loop in :mod:`llm` only when
``FLAG_TOOL_USE`` is enabled.  Without that flag the pipeline is
unchanged from Phase 1-13.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_SIMULATED_CONNECTOR_NAMESPACES = frozenset(
    {"efris", "payment_system", "bwims", "tin_registration", "digital_tax_stamps", "ursb"}
)
_SIMULATION_NOTICE = (
    "Local simulator result only. No live URA, NIRA, URSB, EFRIS, BWIMS, bank, or payment service was contacted or updated."
)
#: Gate outcomes that stop a tool from running.
_HELD_OUTCOMES = frozenset({"hard_blocked", "proposed_awaiting_officer", "failed_closed"})


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------
def readable_title(name: str, namespace: str = "") -> str:
    """A function name as a person reads it, without its service's prefix.

    ``bwims_consignment_status`` in the ``bwims`` namespace is "Consignment
    status": the connector picker shows it under the service's own name, and
    "Bwims consignment status" read as a typo. A name that is nothing but the
    prefix is kept whole. ``plugins.base.readable_title`` is the same rule for
    the plugin package's own schema type (a test keeps them in step).
    """
    words = [word for word in name.split("_") if word]
    prefixes = {word for word in namespace.split("_") if word}
    if namespace:
        prefixes.add(namespace)
        prefixes.add("".join(word[0] for word in namespace.split("_") if word))
    if len(words) > 1 and words[0] in prefixes:
        words = words[1:]
    return " ".join(words).capitalize()


@dataclass(frozen=True)
class ToolSchema:
    """Structural description of a tool, exported to the LLM and to MCP.

    ``parameters`` must be a valid JSON Schema object — Qwen's chat
    template renders it into the prompt in OpenAI-function-calling
    format, and :meth:`Tool.to_mcp_tool` publishes the same object as
    the MCP ``inputSchema``.

    The authorization fields are **declarations, not conventions**.
    Access used to be inferred from the tool's name (anything starting
    ``ura_`` was treated as account access), which silently grants
    everything to a tool that is named differently and cannot express
    "this tool needs consent X but not consent Y".  ``required_scopes``
    and ``allowed_roles`` state the requirement on the tool itself, and
    the dispatch policy denies by default when a tool declares nothing
    and its risk tier is above ``low``.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )
    # Risk tier (from the roadmap):  low = pure fn / read-only,
    # medium = user-scoped PII, high = URA data, critical = writes.
    risk: str = "low"
    # If True, calling this tool may require user confirmation in
    # the UI (writes, forms submission, etc).  The LLM must still
    # propose the call — the UI gates the actual execution.
    requires_confirmation: bool = False

    # -- MCP 2026-07-28 surface ----------------------------------------
    #: JSON Schema for the tool's ``structuredContent``.  Optional; when
    #: present the client validates results against it before handing
    #: them to the model, so a server that starts returning a different
    #: shape is caught at the boundary rather than in a rendered reply.
    output_schema: dict[str, Any] | None = None
    #: Which MCP server owns this tool.  Routes the call to a transport.
    namespace: str = "core"
    #: Consent purposes the caller must hold.  Empty means none needed.
    required_scopes: tuple[str, ...] = ()
    #: Roles allowed to call this tool.  Empty means any role.
    allowed_roles: tuple[str, ...] = ()
    #: Roles that satisfy ``required_scopes`` by virtue of the role
    #: itself — URA staff opening a ticket are acting under their own
    #: mandate, not under an end-user consent grant.
    scope_exempt_roles: tuple[str, ...] = ()
    #: Tool annotations — behavioural hints clients use to build
    #: guardrails.  Defaults describe a pure read-only function.
    title: str = ""
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False

    def __post_init__(self) -> None:
        if self.output_schema and isinstance(self.output_schema, dict):
            if "properties" in self.output_schema and "ok" in self.output_schema["properties"]:
                req = list(self.output_schema.get("required", []))
                if "ok" not in req:
                    req.append("ok")
                    schema_copy = dict(self.output_schema)
                    schema_copy["required"] = req
                    object.__setattr__(self, "output_schema", schema_copy)

    def annotations(self) -> dict[str, Any]:
        """MCP ``annotations`` object for this tool."""
        return {
            "title": self.title or readable_title(self.name, self.namespace),
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


# ---------------------------------------------------------------------------
# Tool base class
# ---------------------------------------------------------------------------
class Tool(ABC):
    """Abstract base for all agent tools."""

    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """Return the structural description of this tool."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Run the tool and return a JSON-serialisable result.

        Implementations should return a dict with an ``ok`` bool
        and either the payload (on success) or an ``error`` field
        (on failure).  Raising is fine — the registry wraps every
        call in a try/except and converts exceptions into structured
        errors the LLM can read.
        """

    # -- convenience ---------------------------------------------------
    def to_openai_spec(self) -> dict[str, Any]:
        """Convert to the OpenAI/Qwen2.5 function-calling schema.

        Qwen's :py:meth:`tokenizer.apply_chat_template` expects the
        ``{"type": "function", "function": {...}}`` envelope.
        """
        s = self.schema
        return {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
            },
        }

    def to_mcp_tool(self) -> dict[str, Any]:
        """Convert to an MCP ``Tool`` descriptor (spec 2026-07-28).

        Input and output schemas are plain JSON Schema 2020-12 objects.
        The risk tier and consent scopes travel in ``_meta`` under this
        project's reverse-DNS prefix, which is where the spec puts
        implementation-defined metadata — gateways can authorize on it
        without having to understand our tool names.
        """
        s = self.schema
        descriptor: dict[str, Any] = {
            "name": s.name,
            "description": s.description,
            "inputSchema": s.parameters,
            "annotations": s.annotations(),
            "_meta": {
                "ug.go.ura.chatbot/risk": s.risk,
                "ug.go.ura.chatbot/namespace": s.namespace,
                "ug.go.ura.chatbot/requiredScopes": list(s.required_scopes),
                "ug.go.ura.chatbot/allowedRoles": list(s.allowed_roles),
                "ug.go.ura.chatbot/scopeExemptRoles": list(s.scope_exempt_roles),
                "ug.go.ura.chatbot/requiresConfirmation": s.requires_confirmation,
            },
        }
        if s.output_schema is not None:
            descriptor["outputSchema"] = s.output_schema
        from ..mcp.protocol import META_DESCRIPTOR_SHA256
        from ..mcp.validation import compute_descriptor_sha256

        descriptor["_meta"][META_DESCRIPTOR_SHA256] = compute_descriptor_sha256(descriptor)
        return descriptor


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ToolRegistry:
    """Global registry of available tools.

    Tools call :py:meth:`register` at import time.  The registry is a
    module-level singleton so the LLM loop and the supervisor agent
    see the exact same set of tools regardless of where in the
    codebase they're created.
    """

    _tools: dict[str, Tool] = {}

    # -- mutation ------------------------------------------------------
    @classmethod
    def register(cls, tool: Tool) -> Tool:
        """Register *tool* under its schema name.

        Returns the tool instance so this can be used as a decorator
        or inline inside a module.  Re-registration of the same name
        replaces the previous entry (useful for hot-reload in dev).
        """
        name = tool.schema.name
        if name in cls._tools:
            logger.info(
                "Tool %s re-registered (was %s, now %s)",
                name,
                type(cls._tools[name]).__name__,
                type(tool).__name__,
            )
        cls._tools[name] = tool
        logger.debug("Registered tool %s (risk=%s)", name, tool.schema.risk)
        return tool

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._tools.pop(name, None)

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()

    # -- access --------------------------------------------------------
    @classmethod
    def get(cls, name: str) -> Tool | None:
        return cls._tools.get(name)

    @classmethod
    def all(cls) -> list[Tool]:
        return list(cls._tools.values())

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._tools.keys())

    @classmethod
    def openai_specs(cls, allow_risk: list[str] | None = None) -> list[dict[str, Any]]:
        """Return the OpenAI/Qwen function-calling spec list.

        If *allow_risk* is provided, tools outside that set are
        filtered out — this is how the supervisor agent enforces
        per-specialist tool scopes (e.g. customs specialist gets only
        low/medium risk tools, never critical).
        """
        tools: list[Tool] = list(cls._tools.values())
        if allow_risk is not None:
            tools = [t for t in tools if t.schema.risk in allow_risk]
        return [t.to_openai_spec() for t in tools]

    @classmethod
    def mcp_tools(cls, namespace: str | None = None) -> list[dict[str, Any]]:
        """Return MCP ``Tool`` descriptors, optionally for one namespace.

        This is what an MCP server's ``tools/list`` serves.
        """
        tools = sorted(cls._tools.values(), key=lambda t: t.schema.name)
        if namespace is not None:
            tools = [t for t in tools if t.schema.namespace == namespace]
        return [t.to_mcp_tool() for t in tools]

    @classmethod
    def namespaces(cls) -> list[str]:
        return sorted({t.schema.namespace for t in cls._tools.values()})

    # -- dispatch ------------------------------------------------------
    @classmethod
    def call(
        cls,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: str = "",
        tenant_id: str = "default",
        channel: str = "rest",
        caller_confirmed: bool = False,
        officer_approved: bool = False,
        approving_officer_id: str = "",
        ticket_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a tool call by name, returning a JSON-safe dict.

        All exceptions are captured — the LLM should never see a
        Python stack trace.  Timing is included so the caller can
        log tool latency into OTel spans.
        """
        t0 = time.perf_counter()
        tool = cls.get(name)
        if tool is None:
            logger.warning("Tool not found: %s (available: %s)", name, cls.names())
            return {
                "ok": False,
                "error": f"Unknown tool: {name}",
            }

        if (
            tool.schema.namespace in _SIMULATED_CONNECTOR_NAMESPACES
            and os.getenv("APP_ENV", "development").lower() == "production"
        ):
            return {
                "ok": False,
                "error": "Local simulator connector tools are never enabled in production.",
                "mode": "simulation",
                "live": False,
            }

        arg_dict = dict(arguments or {})
        resolved_user_id = str(user_id or arg_dict.get("user_id") or "")
        resolved_tenant_id = str(tenant_id or arg_dict.get("tenant_id") or "default")
        resolved_session_id = str(arg_dict.get("conversation_id") or "tool_dispatch")
        key_only_params = {"argument_keys": sorted(str(k) for k in arg_dict)}
        audit_context: dict[str, Any] = {
            "session_id": resolved_session_id,
            "tool_or_action_name": name,
            "intent_detected": name,
            "intent_confidence": 1.0,
            "parameters_redacted": key_only_params,
            "parameters_digest_source": arg_dict,
            "channel": channel,
            "user_id": resolved_user_id,
            "tenant_id": resolved_tenant_id,
            "caller_confirmed": caller_confirmed,
            "officer_approved": officer_approved,
            "approving_officer_id": approving_officer_id,
            "ticket_id": ticket_id,
            "declared_risk": tool.schema.risk,
        }

        # Preventive Autonomy Gate (Phase 34): decided before tool.execute.
        gate_outcome, refusal = cls._autonomy_gate(tool, audit_context, t0)
        if refusal is not None:
            return refusal

        raised = False
        try:
            exec_kwargs = dict(arg_dict)
            try:
                sig = inspect.signature(tool.execute)
                params = sig.parameters
                declared_props = set((tool.schema.parameters.get("properties") or {}).keys())
                if "user_id" in params and "user_id" not in declared_props:
                    exec_kwargs.pop("user_id", None)
                    if user_id:
                        exec_kwargs["user_id"] = str(user_id)
                elif "user_id" in params and user_id:
                    exec_kwargs["user_id"] = str(user_id)
                if "tenant_id" in params and "tenant_id" not in declared_props:
                    exec_kwargs.pop("tenant_id", None)
                    if tenant_id:
                        exec_kwargs["tenant_id"] = str(tenant_id)
                elif "tenant_id" in params and tenant_id and tenant_id != "default":
                    exec_kwargs["tenant_id"] = str(tenant_id)
            except Exception:
                logger.debug("Signature inspection skipped for %s", name, exc_info=True)
            result = tool.execute(**exec_kwargs)
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
            elif "ok" not in result:
                result = {"ok": True, **result}
        except TypeError as e:
            # Schema mismatch — the LLM passed wrong args.  Don't
            # log a stacktrace, just return a clean message.
            logger.info(
                "Tool %s rejected arguments (argument_keys=%s, error_type=%s)",
                name,
                sorted(arg_dict.keys()),
                type(e).__name__,
            )
            raised = True
            result = {
                "ok": False,
                "error": f"Invalid arguments for {name}: {e}",
                "expected": tool.schema.parameters,
            }
        except Exception as e:  # noqa: BLE001 — tools are sandboxed
            logger.exception("Tool %s raised", name)
            raised = True
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.debug("Tool %s executed in %.1fms", name, elapsed_ms)

        if not raised and tool.schema.namespace in _SIMULATED_CONNECTOR_NAMESPACES:
            result.setdefault("mode", "simulation")
            result.setdefault("live", False)
            result.setdefault("simulation_notice", _SIMULATION_NOTICE)

        # A call that failed is evidence too: the return paths above used to skip
        # this, so the actions an auditor most wants to see left no record.
        try:
            from ..flags import flags

            if flags.is_enabled("action_level_audit_v3", subject=resolved_user_id or None) or flags.is_enabled(
                "verifiable_decision_receipts", subject=resolved_user_id or None
            ):
                from ..audit.governance_v3 import append_action_audit_v3

                rec, receipt = append_action_audit_v3(
                    **audit_context,
                    response_summary_redacted={"ok": bool(result.get("ok", True))},
                    execution_status="success" if result.get("ok", True) else "error",
                    execution_latency_ms=elapsed_ms,
                    decided_outcome=gate_outcome or "autonomous_executed",
                )
                if flags.is_enabled("verifiable_decision_receipts", subject=resolved_user_id or None):
                    result["decision_receipt"] = receipt.model_dump()
                elif rec.audit_event_id:
                    result["audit_event_id"] = rec.audit_event_id
        except Exception:
            logger.warning("Tool audit record not written for %s", name, exc_info=True)

        return result

    @classmethod
    def _autonomy_gate(
        cls, tool: Tool, audit_context: dict[str, Any], started: float
    ) -> tuple[str, dict[str, Any] | None]:
        """Decide whether *tool* may run: ``(outcome, refusal)``, ``refusal`` None to proceed.

        The decision and its record are kept apart on purpose. They shared one
        ``try`` that logged at debug level, so an audit write that failed while
        refusing a critical action fell through and **ran the action**. A gate
        that cannot decide has not said yes (ASI02), and a block does not depend
        on being able to write it down. A pure read has nothing to stop, so it
        alone survives a gate fault: a calculator should outlive the policy store.
        """
        name = str(audit_context["tool_or_action_name"])
        subject = str(audit_context["user_id"]) or None
        schema = tool.schema
        try:
            from ..flags import flags

            if not flags.is_enabled("risk_tiered_autonomy_gates", subject=subject):
                return "", None
            from ..audit import governance_v3

            outcome, decision = governance_v3.evaluate_autonomy_gate(
                name,
                intent_confidence=1.0,
                caller_confirmed=bool(audit_context["caller_confirmed"]),
                officer_approved=bool(audit_context["officer_approved"]),
                approving_officer_id=str(audit_context["approving_officer_id"]),
                ticket_id=audit_context["ticket_id"],
                subject=subject,
                declared_risk=schema.risk,
            )
            # Kept now: the audit row is written after the action this allowed.
            audit_context["approval_timestamp_utc"] = decision.approval_timestamp_utc
        except Exception:
            logger.exception("Autonomy gate could not decide for %s", name)
            cls._gate_fault("evaluate")
            if schema.read_only and schema.risk == "low":
                return "", None
            outcome = "failed_closed"

        if outcome not in _HELD_OUTCOMES:
            return outcome, None

        refusal: dict[str, Any] = {
            "ok": False,
            "autonomy_outcome": outcome,
            "error": f"Action '{name}' requires approval or is blocked ({outcome}).",
        }
        context = dict(audit_context)
        if outcome == "proposed_awaiting_officer":
            # "Awaiting an officer" is only true once an officer has it.
            from .escalate import refer_to_officer

            referred = refer_to_officer(f"Officer approval needed before running {name}")
            if referred:
                refusal["ticket_id"] = referred
                refusal["message"] = (
                    "This needs a URA officer's approval, so it has not been done. "
                    "It has been passed to an officer."
                )
                context["ticket_id"] = referred
        try:
            from ..audit import governance_v3
            from ..flags import flags

            record, receipt = governance_v3.append_action_audit_v3(
                **context,
                response_summary_redacted={"ok": False, "autonomy_outcome": outcome},
                execution_status="blocked" if outcome != "proposed_awaiting_officer" else "proposed",
                execution_latency_ms=(time.perf_counter() - started) * 1000,
                decided_outcome=outcome,
            )
            if flags.is_enabled("verifiable_decision_receipts", subject=subject):
                refusal["decision_receipt"] = receipt.model_dump()
            elif record.audit_event_id:
                refusal["audit_event_id"] = record.audit_event_id
        except Exception:
            logger.exception("Refused action %s could not be recorded", name)
            cls._gate_fault("audit")
        return outcome, refusal

    @staticmethod
    def _gate_fault(stage: str) -> None:
        try:
            from ..analytics import metrics

            metrics.inc("autonomy_gate_faults_total", labels={"stage": stage})
        except Exception:
            logger.debug("gate fault not counted", exc_info=True)


# ---------------------------------------------------------------------------
# Auto-import tool modules so they self-register on package import.
# Adding a new tool module means adding one line here.
# ---------------------------------------------------------------------------
from . import calculators as _calculators  # noqa: E402, F401
from . import calendar as _calendar  # noqa: E402, F401
from . import document_audit as _document_audit  # noqa: E402, F401
from . import education as _education  # noqa: E402, F401
from . import empathy as _empathy  # noqa: E402, F401
from . import escalate as _escalate  # noqa: E402, F401
from . import graph_tools as _graph_tools  # noqa: E402, F401
from . import rag_tool as _rag_tool  # noqa: E402, F401
from . import tasks as _tasks  # noqa: E402, F401
from . import rates as _rates  # noqa: E402, F401
from . import portal_navigator as _portal_navigator  # noqa: E402, F401
from . import ura_account as _ura_account  # noqa: E402, F401
from . import ura_actions as _ura_actions  # noqa: E402, F401
from . import plugins as _plugins  # noqa: E402, F401
from . import memory_tools as _memory_tools  # noqa: E402, F401
from . import mda_interdependence as _mda_interdependence  # noqa: E402, F401

__all__ = ["Tool", "ToolSchema", "ToolRegistry"]
