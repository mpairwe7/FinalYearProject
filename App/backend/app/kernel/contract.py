"""The agent's action contract (ADR-0033).

The agent may only call tools the registry declares and only name concepts
the kernel declares. Planning an undeclared step is reported as exactly that,
rather than skipped as "arguments unavailable", which hid an invented tool
behind an ordinary miss.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The reason recorded when a plan names a tool nothing declares.
UNDECLARED_TOOL = "not a declared tool (ADR-0033 action contract)"


@dataclass(frozen=True)
class ContractViolation:
    step: str
    reason: str


def undeclared_tools(plan: list[str]) -> list[ContractViolation]:
    """Steps of *plan* that name no registered tool."""
    from ..tools import ToolRegistry

    return [ContractViolation(step, UNDECLARED_TOOL) for step in plan if ToolRegistry.get(step) is None]


def undeclared_concepts(keys: list[str]) -> list[ContractViolation]:
    """Rate-table keys the kernel does not declare."""
    from .functions import declared_keys

    declared = declared_keys()
    return [ContractViolation(key, "not a declared kernel concept") for key in keys if key not in declared]
