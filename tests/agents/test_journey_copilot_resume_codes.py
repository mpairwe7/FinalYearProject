"""Cross-channel resume codes are unique per workflow and verified on parse (post-#619 audit).

Before: the prefix was the first three letters of the workflow id, so
``tin_procedure_help`` codes opened ``tin_registration`` and the nine
calculators all shared ``CAL``, which nothing mapped back. The trailing check
was never verified, so any code-shaped string — a typo, or ``URA-PAY-S1-1234``
— could pivot the taxpayer out of, and cancel, the flow they were in.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from app import database
from app.service import _ACTIVE_CONVERSATION_STATE, _ACTIVE_TENANT_ID, ChatModel
from app.workflows.journey_copilot import (
    RESUME_CODE_PREFIXES,
    generate_resume_code,
    looks_like_resume_code,
    parse_resume_code,
)
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry

FLOWS_DIR = Path(__file__).resolve().parents[2] / "App" / "backend" / "app" / "workflows" / "flows"
SHIPPED_WORKFLOWS = sorted(load_workflow(p).id for p in FLOWS_DIR.glob("*.yaml"))


@pytest.fixture(autouse=True)
def _workflows_loaded() -> None:
    if any(WorkflowRegistry.get(wf_id) is None for wf_id in SHIPPED_WORKFLOWS):
        for path in sorted(FLOWS_DIR.glob("*.yaml")):
            WorkflowRegistry.register(load_workflow(path))
    database.init_db()


def _turn(model: ChatModel, thread_id: str, message: str) -> dict | None:
    state = database.conversation_state_key(thread_id, session_id="sess-rc", tenant_id="default")
    tok_state = _ACTIVE_CONVERSATION_STATE.set(state)
    tok_tenant = _ACTIVE_TENANT_ID.set("default")
    try:
        return model._maybe_handle_workflow(
            message=message,
            rewritten=message,
            thread_id=thread_id,
            locale="en",
            session_id="sess-rc",
        )
    finally:
        _ACTIVE_CONVERSATION_STATE.reset(tok_state)
        _ACTIVE_TENANT_ID.reset(tok_tenant)


def test_every_shipped_workflow_has_its_own_prefix():
    assert set(RESUME_CODE_PREFIXES) == set(SHIPPED_WORKFLOWS), (
        "add a prefix for every new workflow YAML (and drop retired ones)"
    )
    prefixes = list(RESUME_CODE_PREFIXES.values())
    assert len(prefixes) == len(set(prefixes)), "resume-code prefixes must be unique"


@pytest.mark.parametrize("workflow_id", SHIPPED_WORKFLOWS)
@pytest.mark.parametrize("step_idx", [0, 4])
def test_issued_codes_parse_back_to_the_same_workflow_and_step(workflow_id, step_idx):
    code = generate_resume_code(workflow_id, step_idx)
    parsed = parse_resume_code(f"please resume {code.lower()} thanks")
    assert parsed is not None
    assert parsed["workflow_id"] == workflow_id
    assert parsed["step_idx"] == step_idx
    assert parsed["code"] == code


def test_unregistered_workflow_gets_no_code():
    assert generate_resume_code("not_a_workflow", 0) == ""


@pytest.mark.parametrize(
    "text",
    [
        "URA-PAY-S1-1234",  # made-up check
        "URA-XYZ-S1-0000",  # unknown prefix
        "URA-CAL-S2-ABCD",  # the old shared calculator prefix
        "URA-RET-S0-0000",  # step numbers start at 1
        "",
    ],
)
def test_forged_or_mistyped_codes_are_rejected(text):
    assert parse_resume_code(text) is None
    assert looks_like_resume_code(text) is bool(text)


def test_mistyped_check_is_rejected():
    code = generate_resume_code("payment_assistance", 0)
    flipped = code[:-1] + ("0" if code[-1] != "0" else "1")
    assert parse_resume_code(flipped) is None


def test_a_valid_code_after_a_forged_one_is_still_found():
    good = generate_resume_code("tax_clearance", 1)
    parsed = parse_resume_code(f"URA-PAY-S1-1234 or maybe {good}")
    assert parsed is not None and parsed["workflow_id"] == "tax_clearance"


@pytest.mark.parametrize("workflow_id", ["tin_procedure_help", "calc_vat", "tin_registration"])
def test_cold_start_code_opens_its_own_workflow(workflow_id):
    started = _turn(ChatModel(), f"conv-rc-{uuid.uuid4().hex[:10]}", generate_resume_code(workflow_id, 0))
    assert started is not None
    assert started["workflow"]["id"] == workflow_id


def test_forged_code_cannot_pivot_or_cancel_the_active_flow():
    model = ChatModel()
    thread_id = f"conv-rc-{uuid.uuid4().hex[:10]}"
    started = _turn(model, thread_id, "Help me file my return")
    assert started is not None and started["workflow"]["id"] == "return_filing"

    assert _turn(model, thread_id, "company") is not None
    at_period = _turn(model, thread_id, "vat")
    assert at_period is not None and at_period["workflow"]["step_id"] == "collect_period"

    # collect_period is free text: before, a made-up code was either taken as a
    # switch to Payment Assistance or stored as the filing period.
    forged = _turn(model, thread_id, "URA-PAY-S1-1234")
    assert forged is not None
    assert forged["workflow"]["id"] == "return_filing"
    assert forged["workflow"]["step_id"] == "collect_period"
    assert "recognise that resume code" in forged["reply"]
    state = database.conversation_state_key(thread_id, session_id="sess-rc", tenant_id="default")
    row = database.get_workflow_session(state, tenant_id="default")
    assert row is not None
    assert row["workflow_id"] == "return_filing"
    assert row["status"] == "active"
    assert "filing_period" not in row["slots"]

    switched = _turn(model, thread_id, generate_resume_code("payment_assistance", 0))
    assert switched is not None and switched["workflow"]["id"] == "payment_assistance"
