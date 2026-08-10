"""YAML → WorkflowDefinition parser.

Loads workflow YAML files into typed dataclasses consumable by the
workflow runtime in :mod:`registry`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStep:
    """A single step in a multi-turn workflow."""

    id: str
    question: str = ""
    slot: str = ""
    validator: str = "text"
    when: str = ""  # e.g. "taxpayer_type == individual"
    tool: str = ""  # tool name to invoke (action step)
    args: dict[str, Any] = field(default_factory=dict)
    confirmation_required: bool = False


@dataclass
class WorkflowDefinition:
    """Parsed representation of a workflow YAML file."""

    id: str
    name: str
    version: str = "1"
    description: str = ""
    requires_auth: bool = False
    trigger_phrases: list[str] = field(default_factory=list)
    steps: list[WorkflowStep] = field(default_factory=list)


def load_workflow(path: Path) -> WorkflowDefinition:
    """Parse a YAML file into a :class:`WorkflowDefinition`."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Workflow YAML at {path} is not a mapping")

    steps: list[WorkflowStep] = []
    for s in raw.get("steps", []):
        steps.append(
            WorkflowStep(
                id=str(s.get("id", "")),
                question=str(s.get("question", "")),
                slot=str(s.get("slot", "")),
                validator=str(s.get("validator", "text")),
                when=str(s.get("when", "")),
                tool=str(s.get("tool", "")),
                args=s.get("args") or {},
                confirmation_required=bool(s.get("confirmation_required", False)),
            )
        )

    return WorkflowDefinition(
        id=str(raw.get("id", path.stem)),
        name=str(raw.get("name", path.stem)),
        version=str(raw.get("version", "1")),
        description=str(raw.get("description", "")),
        requires_auth=bool(raw.get("requires_auth", False)),
        trigger_phrases=[str(p) for p in (raw.get("trigger_phrases") or [])],
        steps=steps,
    )
