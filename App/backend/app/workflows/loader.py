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
    title: str = ""
    ui_widget: str = "text"
    options: list[str] = field(default_factory=list)
    portal_action: dict[str, Any] = field(default_factory=dict)
    resources: list[dict[str, Any]] = field(default_factory=list)


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
        step_id = str(s.get("id", ""))
        validator = str(s.get("validator", "text"))
        title = str(s.get("title", "")) or step_id.replace("_", " ").title()

        # Derive interactive ui_widget and options if not explicitly specified
        raw_options = s.get("options")
        if isinstance(raw_options, list):
            options = [str(opt).strip() for opt in raw_options if opt]
        else:
            options = []

        ui_widget = str(s.get("ui_widget", ""))
        if not ui_widget:
            if validator.startswith("enum[") and validator.endswith("]"):
                ui_widget = "options"
                if not options:
                    options = [opt.strip() for opt in validator[5:-1].split(",") if opt.strip()]
            elif validator == "boolean":
                ui_widget = "boolean"
                if not options:
                    options = ["yes", "no"]
            elif "portal" in step_id or s.get("portal_action"):
                ui_widget = "portal_action"
            else:
                ui_widget = "text"

        portal_action = s.get("portal_action") or {}
        if not portal_action and ("portal" in step_id or "summary" in step_id):
            # Check if step references URA portal
            q = str(s.get("question", "")).lower()
            if "efris.ura.go.ug" in q:
                portal_action = {"label": "Open URA EFRIS Portal ↗", "url": "https://efris.ura.go.ug"}
            elif "portal.ura.go.ug/payment" in q or "prn" in q:
                portal_action = {"label": "Open URA PRN Payments ↗", "url": "https://portal.ura.go.ug/payment"}
            elif "ura.go.ug" in q or "portal" in q:
                portal_action = {"label": "Open URA e-Services Portal ↗", "url": "https://portal.ura.go.ug"}

        raw_resources = s.get("resources")
        if isinstance(raw_resources, list):
            resources = [dict(r) for r in raw_resources if isinstance(r, dict)]
        else:
            resources = []

        steps.append(
            WorkflowStep(
                id=step_id,
                question=str(s.get("question", "")),
                slot=str(s.get("slot", "")),
                validator=validator,
                when=str(s.get("when", "")),
                tool=str(s.get("tool", "")),
                args=s.get("args") or {},
                confirmation_required=bool(s.get("confirmation_required", False)),
                title=title,
                ui_widget=ui_widget,
                options=options,
                portal_action=portal_action,
                resources=resources,
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
