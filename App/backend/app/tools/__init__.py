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

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSchema:
    """Structural description of a tool, exported to the LLM.

    The ``parameters`` field must be a valid JSON Schema object —
    Qwen2.5's chat template uses it to render the tool definition
    into the prompt in OpenAI-function-calling format.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object", "properties": {}, "required": [],
    })
    # Risk tier (from the roadmap):  low = pure fn / read-only,
    # medium = user-scoped PII, high = URA data, critical = writes.
    risk: str = "low"
    # If True, calling this tool may require user confirmation in
    # the UI (writes, forms submission, etc).  The LLM must still
    # propose the call — the UI gates the actual execution.
    requires_confirmation: bool = False


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
            logger.info("Tool %s re-registered (was %s, now %s)",
                        name, type(cls._tools[name]).__name__, type(tool).__name__)
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

    # -- dispatch ------------------------------------------------------
    @classmethod
    def call(cls, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
                "available_tools": cls.names(),
            }

        try:
            result = tool.execute(**(arguments or {}))
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
            elif "ok" not in result:
                result = {"ok": True, **result}
        except TypeError as e:
            # Schema mismatch — the LLM passed wrong args.  Don't
            # log a stacktrace, just return a clean message.
            logger.info("Tool %s rejected args %s: %s", name, arguments, e)
            return {
                "ok": False,
                "error": f"Invalid arguments for {name}: {e}",
                "expected": tool.schema.parameters,
            }
        except Exception as e:  # noqa: BLE001 — tools are sandboxed
            logger.exception("Tool %s raised", name)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.debug("Tool %s executed in %.1fms", name, elapsed_ms)

        return result


# ---------------------------------------------------------------------------
# Auto-import tool modules so they self-register on package import.
# Adding a new tool module means adding one line here.
# ---------------------------------------------------------------------------
from . import calculators as _calculators  # noqa: E402, F401
from . import calendar as _calendar        # noqa: E402, F401
from . import rates as _rates              # noqa: E402, F401
from . import rag_tool as _rag_tool        # noqa: E402, F401


__all__ = ["Tool", "ToolSchema", "ToolRegistry"]
