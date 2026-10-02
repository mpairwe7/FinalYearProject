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

from ..verified_resources import portal_action_for, resolve_resource_ids
from .slots import enum_options

logger = logging.getLogger(__name__)

#: Words a derived step title keeps upper-case.
_TITLE_ACRONYMS = frozenset(
    {"cgt", "cit", "dts", "efris", "ngo", "nin", "paye", "prn", "tcc", "tin", "ursb", "vat", "wht"}
)
#: Verb prefixes that name what the step does, not what it is about.
_TITLE_VERBS = frozenset({"ask", "collect", "get"})


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
    #: Short label for the progress stepper; derived from ``id`` when omitted.
    title: str = ""
    #: How the client offers an answer: ``options`` (enum), ``boolean``,
    #: ``portal_action`` or free ``text``.
    ui_widget: str = "text"
    #: The values the stepper offers as buttons — each one a valid slot answer.
    options: list[str] = field(default_factory=list)
    #: ``{label, url}`` button, resolved from an official-resource id.
    portal_action: dict[str, str] = field(default_factory=dict)
    #: Official-resource payloads, resolved from the ids the YAML names.
    resources: list[dict[str, Any]] = field(default_factory=list)
    #: An informational step after which the flow stops: the rest does not
    #: apply to this taxpayer (e.g. no TIN yet). Only valid on a step with a
    #: question and neither a slot nor a tool.
    ends_flow: bool = False


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


def default_step_title(step_id: str) -> str:
    """``collect_nin`` → ``NIN``; ``collect_taxpayer_type`` → ``Taxpayer type``."""
    words = [w for w in step_id.split("_") if w]
    if len(words) > 1 and words[0] in _TITLE_VERBS:
        words = words[1:]
    title = " ".join(w.upper() if w in _TITLE_ACRONYMS else w for w in words)
    return title[:1].upper() + title[1:]


def _step_widget(validator: str, has_portal_action: bool) -> tuple[str, list[str]]:
    """The answer widget a validator implies, with the options it accepts."""
    options = enum_options(validator)
    if options:
        return "options", options
    if validator == "boolean":
        return "boolean", ["yes", "no"]
    if has_portal_action:
        return "portal_action", []
    return "text", []


def load_workflow(path: Path) -> WorkflowDefinition:
    """Parse a YAML file into a :class:`WorkflowDefinition`.

    ``resources`` and ``portal_action`` name entries in
    :mod:`app.verified_resources` by id rather than carrying URLs, so every
    link a guided flow shows is one that was checked live; an unknown id is
    logged and dropped.
    """
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Workflow YAML at {path} is not a mapping")

    steps: list[WorkflowStep] = []
    for s in raw.get("steps", []):
        step_id = str(s.get("id", ""))
        validator = str(s.get("validator", "text"))
        context = f"{path.name}:{step_id}"

        ends_flow_value = s.get("ends_flow", False)
        if not isinstance(ends_flow_value, bool):
            raise ValueError(f"{context}: ends_flow must be a boolean")
        ends_flow = ends_flow_value
        if ends_flow and (s.get("slot") or s.get("tool") or not s.get("question")):
            raise ValueError(f"{context}: ends_flow is only valid on an informational step")

        portal_action_id = str(s.get("portal_action") or "")
        portal_action = portal_action_for(portal_action_id, context=context) if portal_action_id else {}
        ui_widget, options = _step_widget(validator, bool(portal_action))

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
                title=str(s.get("title", "")) or default_step_title(step_id),
                ui_widget=ui_widget,
                options=options,
                portal_action=portal_action,
                resources=resolve_resource_ids(s.get("resources") or [], context=context),
                ends_flow=ends_flow,
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
