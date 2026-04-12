"""MCP client abstraction.

Today this is a thin wrapper over the in-process
:class:`app.tools.ToolRegistry`.  Tomorrow it becomes a FastMCP
client that speaks stdio (local servers) or HTTP/SSE (DMZ
servers for ``mcp_ura_account`` etc.).

The agent layer never imports :mod:`app.tools` directly — it goes
through this client so Phase 17 (DMZ MCP) can migrate tools one
at a time without touching agents.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialise to the shape the audit ledger expects."""
        import hashlib
        import json

        args_json = json.dumps(self.arguments, sort_keys=True, default=str)
        result_json = json.dumps(self.result, sort_keys=True, default=str)[:4000]
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "risk_tier": self.risk_tier,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "iteration": self.iteration,
            "arguments_sha256": hashlib.sha256(args_json.encode()).hexdigest(),
            "result_sha256": hashlib.sha256(result_json.encode()).hexdigest(),
            "ts": self.ts,
        }


class MCPClient:
    """In-process MCP client (Phase 15 Lite).

    Exposes three operations the agent layer needs:

    - :py:meth:`list_tools` — discovery (fed into Tool RAG)
    - :py:meth:`call_tool` — dispatch + structured result
    - :py:meth:`available_for` — security-trimmed discovery

    Phase 15 full replaces this implementation with a FastMCP
    client that fans out to multiple remote servers.  The public
    API above is the stable contract.
    """

    def __init__(self) -> None:
        from ..tools import ToolRegistry
        self._registry = ToolRegistry

    # -- Discovery -----------------------------------------------------
    def list_tools(self, allow_risk: list[str] | None = None) -> list[dict[str, Any]]:
        """Return OpenAI/Qwen-compatible tool schemas, optionally risk-filtered."""
        return self._registry.openai_specs(allow_risk=allow_risk)

    def describe_tool(self, name: str) -> dict[str, Any] | None:
        tool = self._registry.get(name)
        if tool is None:
            return None
        return tool.to_openai_spec()

    def available_for(
        self,
        user_role: str = "public",
        granted_purposes: list[str] | None = None,
    ) -> list[str]:
        """Return the tool names this user is allowed to see.

        Security trimming:
        - ``ura_account_access`` consent is required for any tool
          starting with ``ura_`` (Phase 17 tools).
        - ``ura_actions`` (writes) are additionally gated on
          ``ura_staff`` or ``verified_taxpayer`` role.
        - Escalation tools are gated on ``ticket_escalation``
          consent for non-staff users.
        - All other Phase A-D tools are universally available.
        """
        granted_purposes = granted_purposes or []
        allowed: list[str] = []
        for name in self._registry.names():
            tool = self._registry.get(name)
            if tool is None:
                continue
            risk = tool.schema.risk
            # High / critical risk → role gate
            if risk in ("high", "critical"):
                if user_role not in ("verified_taxpayer", "ura_staff", "ura_admin"):
                    continue
                if name.startswith("ura_") and "ura_account_access" not in granted_purposes:
                    continue
            # Ticket escalation needs consent from anonymous/public users
            if name == "escalate_to_human" and user_role == "public":
                if "ticket_escalation" not in granted_purposes:
                    continue
            allowed.append(name)
        return allowed

    # -- Dispatch ------------------------------------------------------
    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tenant_id: str = "default",
        user_id: str = "",
        iteration: int = 0,
    ) -> MCPCallResult:
        """Call a tool and return a structured result.

        Does NOT enforce authz — callers must pre-check via
        :py:meth:`available_for`.  This split keeps policy
        decisions explicit and loggable.
        """
        import uuid

        t0 = time.perf_counter()
        call_id = str(uuid.uuid4())
        tool = self._registry.get(name)
        risk = tool.schema.risk if tool is not None else "low"

        raw = self._registry.call(name, arguments)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        return MCPCallResult(
            tool_name=name,
            arguments=dict(arguments or {}),
            result=raw,
            ok=bool(raw.get("ok", True)),
            duration_ms=round(elapsed_ms, 2),
            iteration=iteration,
            tenant_id=tenant_id,
            user_id=user_id,
            risk_tier=risk,
            call_id=call_id,
        )


# ---------------------------------------------------------------------------
# Module-level singleton so the agent layer always sees the same client.
# ---------------------------------------------------------------------------
_client: MCPClient | None = None


def get_client() -> MCPClient:
    global _client
    if _client is None:
        _client = MCPClient()
    return _client


def reset_client() -> None:
    """Testing hook — forces re-creation."""
    global _client
    _client = None
