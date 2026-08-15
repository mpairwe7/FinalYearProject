"""Workflow registry and session runtime.

Provides :class:`WorkflowRegistry` (global singleton for loading workflow
definitions) and :class:`WorkflowSession` (per-conversation state machine
that advances through workflow steps, validates slots, and emits prompts).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loader import WorkflowDefinition, WorkflowStep, load_workflow
from .slots import SlotResolver, validate_slot

logger = logging.getLogger(__name__)


@dataclass
class WorkflowTurn:
    """The result of one ``advance()`` call."""

    question: str = ""
    is_complete: bool = False
    validation_error: str = ""
    tool_call: dict[str, Any] | None = None
    slot_name: str = ""
    slot_value: object = None


@dataclass
class WorkflowSession:
    """Per-conversation workflow state."""

    workflow_id: str
    current_step_idx: int = 0
    slots: dict[str, Any] = field(default_factory=dict)
    completed: bool = False


def _eval_condition(when: str, slots: dict[str, Any]) -> bool:
    """Evaluate a simple ``slot == value`` condition string."""
    if not when:
        return True
    m = re.match(r"^(\w+)\s*==\s*(.+)$", when.strip())
    if not m:
        logger.warning("Unparseable when condition: %s", when)
        return True
    slot_name, expected = m.group(1), m.group(2).strip()
    actual = str(slots.get(slot_name, "")).strip()
    return actual.lower() == expected.lower()


def _interpolate_args(template: dict[str, Any], slots: dict[str, Any]) -> dict[str, Any]:
    """Fill ``{slot}`` placeholders from *slots*, dropping unfilled ones.

    An optional slot the user never filled must fall through to the
    tool's own default.  Passing the literal ``"{residency}"`` on instead
    used to be read as "not resident" and quietly returned non-resident
    PAYE bands; it is now rejected by the tool's enum, which is safer but
    still not the behaviour the flow wants.
    """
    args: dict[str, Any] = {}
    for key, value in template.items():
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            filled = slots.get(value[1:-1])
            if filled is None or filled == "":
                continue
            args[key] = filled
        else:
            args[key] = value
    return args


class WorkflowRegistry:
    """Global workflow registry (module-level singleton)."""

    _workflows: dict[str, WorkflowDefinition] = {}

    @classmethod
    def register(cls, wf: WorkflowDefinition) -> None:
        cls._workflows[wf.id] = wf
        logger.info("Registered workflow: %s (%d steps)", wf.id, len(wf.steps))

    @classmethod
    def get(cls, workflow_id: str) -> WorkflowDefinition | None:
        return cls._workflows.get(workflow_id)

    @classmethod
    def list_all(cls) -> list[WorkflowDefinition]:
        return list(cls._workflows.values())

    @classmethod
    def match_trigger(cls, query: str) -> WorkflowDefinition | None:
        """Return the first workflow whose trigger phrases match *query*."""
        q = query.lower()
        for wf in cls._workflows.values():
            for phrase in wf.trigger_phrases:
                if phrase.lower() in q:
                    return wf
        return None

    @classmethod
    def create_session(cls, workflow_id: str) -> WorkflowSession | None:
        if workflow_id not in cls._workflows:
            return None
        return WorkflowSession(workflow_id=workflow_id)

    @classmethod
    def advance(
        cls,
        session: WorkflowSession,
        user_input: str,
        resolver: SlotResolver | None = None,
    ) -> WorkflowTurn:
        """Advance the workflow by one step.

        Validates *user_input* against the current step's slot validator,
        stores the value, and returns the next prompt or completion.

        *resolver* is threaded through to the slot validators as the last
        resort for a choice question the deterministic rules cannot place.
        Passed in rather than reached for, so this class stays free of the
        model and its tests stay free of a fixture for one.
        """
        wf = cls._workflows.get(session.workflow_id)
        if not wf or session.completed:
            return WorkflowTurn(is_complete=True)

        # Find the next applicable step (skip steps whose condition is false)
        step: WorkflowStep | None = None
        while session.current_step_idx < len(wf.steps):
            candidate = wf.steps[session.current_step_idx]
            if _eval_condition(candidate.when, session.slots):
                step = candidate
                break
            session.current_step_idx += 1

        if step is None:
            session.completed = True
            return WorkflowTurn(is_complete=True)

        # Prefilled slots from profile / consented memory should skip the
        # question entirely so guided flows feel stateful rather than repetitive.
        if step.slot and session.slots.get(step.slot) not in ("", None):
            session.current_step_idx += 1
            return cls.advance(session, user_input, resolver)

        # Informational terminal steps (question text but no slot/tool)
        # should be emitted once and then marked complete. This keeps
        # summary/final-instruction steps durable without requiring an
        # extra dummy user turn.
        if step.question and not step.slot and not step.tool:
            session.current_step_idx += 1
            session.completed = session.current_step_idx >= len(wf.steps)
            return WorkflowTurn(question=step.question, is_complete=session.completed)

        # First call for this step (no user input yet) — emit the question
        if not user_input and step.question and step.slot:
            return WorkflowTurn(question=step.question, slot_name=step.slot)

        # Tool step (no slot to fill, just dispatch)
        if step.tool and not step.slot:
            args = _interpolate_args(step.args, session.slots)
            session.current_step_idx += 1
            return WorkflowTurn(
                tool_call={"name": step.tool, "arguments": args},
                slot_name=step.id,
            )

        # Validate slot input
        if step.slot and step.validator:
            is_valid, normalised, error = validate_slot(user_input, step.validator, resolver)
            if not is_valid:
                return WorkflowTurn(
                    question=f"{error}\n\n{step.question}",
                    validation_error=error,
                    slot_name=step.slot,
                )
            session.slots[step.slot] = normalised
        elif step.slot:
            session.slots[step.slot] = user_input.strip()

        session.current_step_idx += 1

        # Find and emit the next applicable step's question
        while session.current_step_idx < len(wf.steps):
            nxt = wf.steps[session.current_step_idx]
            if not _eval_condition(nxt.when, session.slots):
                session.current_step_idx += 1
                continue

            if nxt.tool and not nxt.slot:
                args = _interpolate_args(nxt.args, session.slots)
                session.current_step_idx += 1
                return WorkflowTurn(
                    tool_call={"name": nxt.tool, "arguments": args},
                    slot_name=nxt.id,
                )

            if nxt.question and not nxt.slot and not nxt.tool:
                session.current_step_idx += 1
                session.completed = session.current_step_idx >= len(wf.steps)
                return WorkflowTurn(question=nxt.question, is_complete=session.completed)

            if nxt.question:
                return WorkflowTurn(question=nxt.question, slot_name=nxt.slot)
            break

        session.completed = True
        return WorkflowTurn(is_complete=True)


def auto_load_flows(flows_dir: Path) -> int:
    """Glob ``*.yaml`` in *flows_dir* and register each workflow."""
    count = 0
    for path in sorted(flows_dir.glob("*.yaml")):
        try:
            wf = load_workflow(path)
            WorkflowRegistry.register(wf)
            count += 1
        except Exception:
            logger.exception("Failed to load workflow from %s", path)
    return count
