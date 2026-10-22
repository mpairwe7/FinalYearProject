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

from .journey_copilot import (
    build_journey_gps,
    build_stall_scaffolding,
    evaluate_prerequisites,
    evaluate_step_up_auth,
    generate_resume_code,
)
from .loader import WorkflowDefinition, WorkflowStep, default_step_title, load_workflow
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
    step_id: str = ""
    step_title: str = ""
    step_index: int = 0
    total_steps: int = 0
    ui_widget: str = "text"
    options: list[str] = field(default_factory=list)
    portal_action: dict[str, Any] | None = None
    all_steps: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    is_tangent: bool = False
    tangent_query: str = ""
    parked_step_prompt: str = ""
    #: Consecutive answers to this step that failed validation (0 when accepted).
    failed_attempts: int = 0
    #: October 2026 Journey Copilot enrichments (Pillars 1, 3, 5, 6, 7).
    prerequisites: list[dict[str, Any]] = field(default_factory=list)
    journey_gps: dict[str, Any] | None = None
    scaffolding: dict[str, Any] | None = None
    step_up_auth: dict[str, Any] | None = None
    resume_code: str = ""


@dataclass
class WorkflowSession:
    """Per-conversation workflow state."""

    workflow_id: str
    current_step_idx: int = 0
    slots: dict[str, Any] = field(default_factory=dict)
    completed: bool = False
    #: Pushdown goal stack for nested prerequisite sub-workflows (Pillar 3).
    goal_stack: list[dict[str, Any]] = field(default_factory=list)


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


def compute_workflow_progress(
    wf: WorkflowDefinition,
    session: WorkflowSession,
    current_step_id: str = "",
) -> tuple[int, int, list[dict[str, str]]]:
    """Where the session stands: ``(1-based index, applicable steps, track)``.

    Only steps whose ``when`` holds for the slots so far are counted, so the
    total can grow once an answer opens a branch (choosing *individual* adds
    the NIN step). Without *current_step_id* the current step is the one the
    session is parked on. Track entries are ``{id, title, status}`` only — a
    step's options, links and portal button travel once, on the turn itself.
    """
    applicable = [s for s in wf.steps if _eval_condition(s.when, session.slots)]
    total = len(applicable)
    if total == 0:
        return 0, 0, []

    if not current_step_id and not session.completed:
        current_step_id = next(
            (s.id for s in wf.steps[session.current_step_idx :] if _eval_condition(s.when, session.slots)),
            "",
        )
    current = None if session.completed else next(
        (i for i, s in enumerate(applicable) if s.id == current_step_id), None
    )

    track: list[dict[str, str]] = []
    for i, s in enumerate(applicable):
        if current is None or i < current:
            status = "completed"
        elif i == current:
            status = "current"
        elif s.slot and session.slots.get(s.slot) not in ("", None):
            status = "completed"  # prefilled from the taxpayer's profile
        else:
            status = "pending"
        track.append({"id": s.id, "title": s.title or default_step_title(s.id), "status": status})

    return (total if current is None else current + 1), total, track


def _make_turn(
    wf: WorkflowDefinition,
    session: WorkflowSession,
    step: WorkflowStep | None = None,
    *,
    question: str = "",
    is_complete: bool = False,
    validation_error: str = "",
    tool_call: dict[str, Any] | None = None,
    slot_name: str = "",
    is_tangent: bool = False,
    tangent_query: str = "",
    parked_step_prompt: str = "",
    failed_attempts: int = 0,
) -> WorkflowTurn:
    """A :class:`WorkflowTurn` carrying *step*'s stepper metadata and the progress track."""
    if not session.goal_stack and isinstance(session.slots.get("_goal_stack"), list):
        session.goal_stack = list(session.slots["_goal_stack"])
    step_index, total_steps, all_steps = compute_workflow_progress(wf, session, step.id if step else "")
    effective_slot = slot_name or (step.slot if step else "")
    options_list = list(step.options) if step else []
    prereqs = evaluate_prerequisites(wf.id, session.slots)
    scaffolding = build_stall_scaffolding(
        wf.id,
        step.id if step else "",
        effective_slot,
        step.validator if step else "text",
        failed_attempts,
        options=options_list,
    )
    is_auth = bool(session.slots.get("_authenticated_user_id"))
    step_up = evaluate_step_up_auth(wf, step, session.slots, is_authenticated=is_auth)
    resume_code = generate_resume_code(wf.id, session.current_step_idx, session.slots)
    turn = WorkflowTurn(
        question=question,
        is_complete=is_complete,
        validation_error=validation_error,
        tool_call=tool_call,
        slot_name=effective_slot,
        step_id=step.id if step else "",
        step_title=(step.title or default_step_title(step.id)) if step else "",
        step_index=step_index,
        total_steps=total_steps,
        ui_widget=step.ui_widget if step else "text",
        options=options_list,
        portal_action=dict(step.portal_action) if step and step.portal_action else None,
        all_steps=all_steps,
        resources=list(step.resources) if step else [],
        is_tangent=is_tangent,
        tangent_query=tangent_query,
        parked_step_prompt=parked_step_prompt,
        failed_attempts=failed_attempts,
        prerequisites=prereqs,
        scaffolding=scaffolding,
        step_up_auth=step_up,
        resume_code=resume_code,
    )
    turn.journey_gps = build_journey_gps(
        wf,
        session,
        turn,
        step_index=step_index,
        total_steps=total_steps,
    )
    return turn


def _record_invalid_answer(session: WorkflowSession, step_id: str) -> int:
    """Count consecutive invalid answers to *step_id*; another step starts again at one.

    Engine state, so ``_``-prefixed like ``_tangent_count``: never a slot the
    taxpayer gave (the slot bank and the officer hand-off skip such keys).
    """
    if session.slots.get("_invalid_step") != step_id:
        session.slots["_invalid_step"] = step_id
        session.slots["_invalid_count"] = 0
    session.slots["_invalid_count"] = int(session.slots.get("_invalid_count") or 0) + 1
    return int(session.slots["_invalid_count"])


def _clear_invalid_answers(session: WorkflowSession) -> None:
    session.slots.pop("_invalid_step", None)
    session.slots.pop("_invalid_count", None)


#: The "-ing" forms people use for the task a trigger names. Triggers are
#: written with the base verb ("file a return"), so "filing my VAT return"
#: never matched, and an explicit "walk me through filing my VAT return" fell
#: through to a VAT explainer instead of the Return Filing flow (measured on
#: the local stack, 2026-09-29).
#:
#: Past tense is deliberately NOT folded. "I filed my return yesterday but the
#: portal shows an error" and "I registered for a TIN and lost the
#: certificate" report something already done; folding them to "file my
#: return" / "register for a tin" started a fresh guided flow instead of
#: answering the problem (code review, 2026-09-29).
_VERB_FORMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bfiling\b"), "file"),
    (re.compile(r"\bregistering\b"), "register"),
    (re.compile(r"\bsubmitting\b"), "submit"),
    (re.compile(r"\bpaying\b"), "pay"),
    (re.compile(r"\bgenerating\b"), "generate"),
    (re.compile(r"\bclearing\b"), "clear"),
    (re.compile(r"\bobjecting\b"), "object"),
    (re.compile(r"\bgetting\b"), "get"),
    (re.compile(r"\bapplying\b"), "apply"),
)

#: A tax-type word between the determiner and "return" — "my VAT return" is
#: still "my return" for the purpose of finding the filing flow. "nil" is not
#: folded: "nil return" is itself a trigger phrase, and folding it away left
#: "submit the nil return" matching nothing.
_RETURN_QUALIFIER_RE = re.compile(
    r"\b(my|a|the|our)\s+(?:vat|paye|income\s+tax|corporation\s+tax|withholding\s+tax"
    r"|rental(?:\s+income)?\s+tax|annual|monthly|provisional)\s+return\b"
)


def _normalise_for_triggers(text: str) -> str:
    """Lower-case, collapse spaces, base verbs, drop the tax type before "return"."""
    q = " ".join((text or "").lower().split())
    for pattern, base in _VERB_FORMS:
        q = pattern.sub(base, q)
    return _RETURN_QUALIFIER_RE.sub(r"\1 return", q)


_TAX_LIABILITY_SLOTS: frozenset[str] = frozenset({
    "tax_amount",
    "payment_amount",
    "amount_due",
    "vat_amount",
    "paye_amount",
    "duty_amount",
})

_BASE_AMOUNT_KEYS: tuple[str, ...] = (
    "net",
    "monthly_gross",
    "cif_value",
    "chargeable_income",
    "annual_gross_rent",
    "amount",
    "gross",
)

_TAX_OUTPUT_KEYS: tuple[str, ...] = (
    "tax",
    "vat",
    "paye",
    "withholding_tax",
    "total_taxes",
    "gross",
    "amount",
)


_SIDE_QUESTION_RE: re.Pattern[str] = re.compile(
    r"(?:\?\s*$|\b(?:what\s+is|what\s+are|what\s+does|how\s+do\s+i|how\s+does|how\s+is|why\s+is|why\s+do|"
    r"which\s+supplies|is\s+it\s+exempt|who\s+qualifies|when\s+is\s+the\s+deadline|explain|meaning\s+of|"
    r"kye\s+ki|kitegeeza\s+ki|gubalibwa\s+gutya|ebikwata\s+ku|ni\s+nini|maana\s+ya|inamaanisha\s+nini|"
    r"niki|ekikora\s+kita|amakuru\s+ga|nigubaririrwa\s+guta|shangahi|bingahi|"
    r"obedo\s+ngo|tiyo\s+nining|te\s+lok|kikwano\s+nining|tye\s+adi)\b)",
    re.IGNORECASE,
)


def is_side_question(user_input: str) -> bool:
    """Return True if *user_input* reads as a policy/clarification question mid-workflow."""
    text = (user_input or "").strip()
    if len(text.split()) < 3:
        return False
    return bool(_SIDE_QUESTION_RE.search(text))


def resolve_workflow_side_question(user_input: str) -> str:
    """Resolve a mid-workflow policy or rate side-question deterministically without losing slot state."""
    q = (user_input or "").strip()
    if not q:
        return ""
    try:
        from ..calculator_router import plan_rate_lookup
        from ..mcp import get_client

        rate_plan = plan_rate_lookup(q)
        if rate_plan and rate_plan.tax_type:
            call = get_client().call_tool("lookup_rate", {"tax_type": rate_plan.tax_type}, user_role="public")
            res = call.result if call and isinstance(call.result, dict) else {}
            if res.get("ok") and res.get("formatted"):
                name = res.get("display_name") or res.get("tax_type")
                fy = f" ({res['fiscal_year']})" if res.get("fiscal_year") else ""
                return f"Regarding your question: the official {name} rate{fy} is {res['formatted']}."
    except Exception:
        logger.debug("workflow side-question rate lookup skipped", exc_info=True)

    try:
        from ..tools import ToolRegistry

        if any(w in q.lower() for w in ("deadline", "due date", "when is", "filing date")):
            dl_tool = ToolRegistry.get("get_filing_deadlines")
            if dl_tool is not None:
                dl_out = dl_tool.execute()
                if isinstance(dl_out, dict) and dl_out.get("ok") and isinstance(dl_out.get("deadlines"), list):
                    top_dl = dl_out["deadlines"][:2]
                    dl_summary = "; ".join(
                        f"{d.get('name', 'Deadline')}: {d.get('date', '')}" for d in top_dl if isinstance(d, dict)
                    )
                    if dl_summary:
                        return f"Regarding your question: upcoming statutory deadlines are {dl_summary}."

        edu = ToolRegistry.get("explain_tax_concept")
        if edu is not None:
            for concept in ("vat", "paye", "tin", "withholding", "efris", "presumptive", "rental", "customs", "chargeable income"):
                if concept in q.lower():
                    out = edu.execute(concept=concept)
                    if isinstance(out, dict) and out.get("ok") and out.get("explanation"):
                        return f"Regarding your question: {str(out['explanation']).strip()}"
    except Exception:
        logger.debug("workflow side-question education lookup skipped", exc_info=True)

    return (
        "Regarding your question: I could not verify that specific rule during this guided step; "
        "you can ask me right after we finish this workflow, or contact URA on 0800 117 000."
    )


def delegate_workflow_slot_calculation(
    session: WorkflowSession,
    user_input: str,
    *,
    slot_name: str = "",
) -> tuple[str | None, dict[str, Any] | None]:
    """Delegate a mid-workflow calculation expression to the MCP calculator specialist bridge.

    Gated by ``FLAG_WORKFLOW_SPECIALIST_BRIDGE``. Maps calculator output keys to the
    semantic meaning of *slot_name* (tax/payment liability vs base/turnover amount).
    """
    from ..flags import flags

    if not flags.is_enabled("workflow_specialist_bridge"):
        return None, None
    try:
        from ..calculator_router import extract_amounts, plan_calculation
        from ..mcp import get_client

        target_slot = (slot_name or "").strip().lower()
        wants_tax_output = (
            not target_slot
            or target_slot in _TAX_LIABILITY_SLOTS
            or session.workflow_id == "payment_assistance"
        )
        key_order = _TAX_OUTPUT_KEYS if wants_tax_output else _BASE_AMOUNT_KEYS

        plan = plan_calculation(user_input)
        if plan and not plan.missing:
            call = get_client().call_tool(plan.tool, dict(plan.params), user_role="public")
            res = call.result if call and isinstance(call.result, dict) else {}
            if res.get("ok"):
                for key in key_order:
                    if res.get(key) is not None:
                        val = float(res[key])
                        session.slots[f"_bridge_{plan.tool}"] = val
                        return str(int(val) if val == int(val) else val), res
        amts = extract_amounts(user_input)
        if len(amts) == 1:
            val = amts[0][0]
            return str(int(val) if val == int(val) else val), {"ok": True, "extracted_amount": val}
    except Exception:
        logger.debug("workflow_specialist_bridge delegation skipped", exc_info=True)
    return None, None


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
        """Return the first workflow whose trigger phrases match *query*.

        Both sides are normalised (:func:`_normalise_for_triggers`), and a
        flow's own name counts as a trigger — "guide me through Return
        Filing" is the most explicit way to ask for it, and it is the text
        the guided-mode offer puts on its button. Flows with no trigger
        phrases (the calculators) are started programmatically only, and a
        name match does not change that.
        """
        q = _normalise_for_triggers(query)
        for wf in cls._workflows.values():
            phrases = list(wf.trigger_phrases)
            if phrases and wf.name:
                phrases.append(wf.name)
            for phrase in phrases:
                if _normalise_for_triggers(phrase) in q:
                    return wf
        return None

    @classmethod
    def create_session(cls, workflow_id: str) -> WorkflowSession | None:
        if workflow_id not in cls._workflows:
            return None
        return WorkflowSession(workflow_id=workflow_id)

    @classmethod
    def push_subworkflow(
        cls,
        session: WorkflowSession,
        sub_workflow_id: str,
        *,
        reason: str = "",
    ) -> bool:
        """Push the current workflow onto ``goal_stack`` and switch to *sub_workflow_id* (Pillar 3)."""
        if sub_workflow_id not in cls._workflows or sub_workflow_id == session.workflow_id:
            return False
        if not session.goal_stack and isinstance(session.slots.get("_goal_stack"), list):
            session.goal_stack = list(session.slots["_goal_stack"])
        session.goal_stack.append(
            {
                "workflow_id": session.workflow_id,
                "step_idx": session.current_step_idx,
                "reason": reason,
            }
        )
        session.slots["_goal_stack"] = list(session.goal_stack)
        session.workflow_id = sub_workflow_id
        session.current_step_idx = 0
        session.completed = False
        return True

    @classmethod
    def pop_subworkflow(cls, session: WorkflowSession) -> bool:
        """Pop the parent workflow from ``goal_stack`` and restore its step position (Pillar 3)."""
        if not session.goal_stack and isinstance(session.slots.get("_goal_stack"), list):
            session.goal_stack = list(session.slots["_goal_stack"])
        if not session.goal_stack:
            return False
        parent = session.goal_stack.pop()
        parent_wf_id = str(parent.get("workflow_id") or "")
        if parent_wf_id not in cls._workflows:
            return False
        if session.goal_stack:
            session.slots["_goal_stack"] = list(session.goal_stack)
        else:
            session.slots.pop("_goal_stack", None)
        session.workflow_id = parent_wf_id
        session.current_step_idx = int(parent.get("step_idx") or 0)
        session.completed = False
        return True

    @classmethod
    def pending_step(cls, session: WorkflowSession) -> WorkflowStep | None:
        """The step this session is waiting on, without advancing it.

        Mirrors the condition-skipping :meth:`advance` does when it looks for
        the current step, but leaves ``session`` untouched, so a caller can ask
        what the flow expects next before deciding whether to hand it the turn.
        """
        wf = cls._workflows.get(session.workflow_id)
        if not wf or session.completed:
            return None
        idx = session.current_step_idx
        while idx < len(wf.steps):
            candidate = wf.steps[idx]
            if _eval_condition(candidate.when, session.slots):
                return candidate
            idx += 1
        return None

    @classmethod
    def current_turn(cls, session: WorkflowSession) -> WorkflowTurn | None:
        """Stepper metadata for the step *session* is parked on, without advancing it."""
        wf = cls._workflows.get(session.workflow_id)
        if wf is None:
            return None
        return _make_turn(wf, session, cls.pending_step(session), is_complete=session.completed)

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
            return _make_turn(wf, session, is_complete=True) if wf else WorkflowTurn(is_complete=True)

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
            return _make_turn(wf, session, is_complete=True)

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
            session.completed = step.ends_flow or session.current_step_idx >= len(wf.steps)
            return _make_turn(wf, session, step, question=step.question, is_complete=session.completed)

        # First call for this step (no user input yet) — emit the question
        if not user_input and step.question and step.slot:
            return _make_turn(wf, session, step, question=step.question, slot_name=step.slot)

        # Tool step (no slot to fill, just dispatch)
        if step.tool and not step.slot:
            args = _interpolate_args(step.args, session.slots)
            session.current_step_idx += 1
            return _make_turn(
                wf,
                session,
                step,
                tool_call={"name": step.tool, "arguments": args},
                slot_name=step.id,
            )

        # Validate slot input (checking free-text steps for mid-workflow side-questions first)
        if step.slot:
            try:
                import os

                from ..flags import flags

                max_hops = max(1, int(os.getenv("WORKFLOW_MAX_TANGENT_HOPS", "2")))
                tangent_enabled = flags.is_enabled("workflow_tangent_resume")
            except ValueError:
                max_hops = 2
                tangent_enabled = False
            tangent_hops = int(session.slots.get("_tangent_count") or 0)

            def _emit_tangent_turn() -> WorkflowTurn:
                session.slots["_tangent_count"] = tangent_hops + 1
                side_answer = resolve_workflow_side_question(user_input)
                step_idx, total_s, _ = compute_workflow_progress(wf, session, step.id)
                s_title = step.title or default_step_title(step.id)
                try:
                    from ..analytics import metrics

                    metrics.inc(
                        "workflow_tangent_resumed_total",
                        labels={"workflow_id": session.workflow_id},
                    )
                except Exception:
                    logger.debug("workflow_tangent_resumed_total metric skipped", exc_info=True)
                resume_text = (
                    f"{side_answer}\n\n"
                    f"Now, returning to Step {step_idx} of {total_s} ({s_title}): {step.question}"
                ).strip()
                return _make_turn(
                    wf,
                    session,
                    step,
                    question=resume_text,
                    validation_error="",
                    slot_name=step.slot,
                    is_tangent=True,
                    tangent_query=user_input.strip(),
                    parked_step_prompt=step.question,
                )

            if (
                tangent_enabled
                and tangent_hops < max_hops
                and (not step.validator or step.validator == "text")
                and is_side_question(user_input)
            ):
                return _emit_tangent_turn()

        if step.slot and step.validator:
            is_valid, normalised, error = validate_slot(user_input, step.validator, resolver)
            if not is_valid and step.validator in ("currency", "amount", "number"):
                bridged_val, _receipt = delegate_workflow_slot_calculation(
                    session, user_input, slot_name=step.slot
                )
                if bridged_val is not None:
                    is_valid, normalised, error = validate_slot(bridged_val, step.validator, resolver)
            if not is_valid:
                if (
                    tangent_enabled
                    and tangent_hops < max_hops
                    and is_side_question(user_input)
                ):
                    return _emit_tangent_turn()

                return _make_turn(
                    wf,
                    session,
                    step,
                    question=f"{error}\n\n{step.question}",
                    validation_error=error,
                    slot_name=step.slot,
                    failed_attempts=_record_invalid_answer(session, step.id),
                )
            session.slots[step.slot] = normalised
            _clear_invalid_answers(session)
        elif step.slot:
            session.slots[step.slot] = user_input.strip()
            _clear_invalid_answers(session)

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
                return _make_turn(
                    wf,
                    session,
                    nxt,
                    tool_call={"name": nxt.tool, "arguments": args},
                    slot_name=nxt.id,
                )

            if nxt.question and not nxt.slot and not nxt.tool:
                session.current_step_idx += 1
                session.completed = nxt.ends_flow or session.current_step_idx >= len(wf.steps)
                return _make_turn(wf, session, nxt, question=nxt.question, is_complete=session.completed)

            if nxt.question:
                return _make_turn(wf, session, nxt, question=nxt.question, slot_name=nxt.slot)
            break

        session.completed = True
        return _make_turn(wf, session, is_complete=True)


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
