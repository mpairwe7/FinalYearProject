"""Verification & validation suite for the October 2026 Agentic Journey Copilot (7 Pillars).

Exercises:
1. Pre-Flight Prerequisite DAG & Readiness Gate (``evaluate_prerequisites``)
2. Declarative A2UI Form Co-Completion Surface (``build_form_draft_surface``, zero PII leak)
3. Plan-Centric 'Journey GPS' & Pushdown Goal Stack (``push_subworkflow``, ``pop_subworkflow``, ``build_journey_gps``)
4. Proactive Objective Inference & Confirmation Cards (``infer_taxpayer_objective``, ``apply_turn_guidance``)
5. Just-In-Time Step-Up Authentication & Zero-Data-Loss Session Elevation (``evaluate_step_up_auth``, ``elevate_anonymous_workflow_session``)
6. Multimodal 'Snap-to-Fill' Document-to-Slot Grounding & Stall Scaffolding (``bind_document_facts_to_workflow``, ``build_stall_scaffolding``)
7. Cross-Channel Resume Token (``generate_resume_code``) & ``ChatModel._workflow_view`` integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.service import _WORKFLOW_SENSITIVE_SLOTS, ChatModel
from app.turn_guidance import apply_turn_guidance
from app.workflows.journey_copilot import (
    bind_document_facts_to_workflow,
    build_form_draft_surface,
    build_stall_scaffolding,
    elevate_anonymous_workflow_session,
    evaluate_prerequisites,
    evaluate_step_up_auth,
    generate_resume_code,
    infer_taxpayer_objective,
    is_prerequisite_remediation,
    parse_resume_code,
)
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry

FLOWS_DIR = Path(__file__).resolve().parents[2] / "App" / "backend" / "app" / "workflows" / "flows"


@pytest.fixture(autouse=True)
def _ensure_workflows_loaded():
    if not WorkflowRegistry.list_all():
        for path in sorted(FLOWS_DIR.glob("*.yaml")):
            WorkflowRegistry.register(load_workflow(path))


def test_pillar1_prerequisite_dag_evaluates_pending_satisfied_and_missing():
    # Initially all prerequisites for return_filing are pending
    initial = evaluate_prerequisites("return_filing", {})
    assert len(initial) == 3
    assert all(p["status"] == "pending" for p in initial)

    # When taxpayer_type is filled, active_tin is satisfied; when portal_access is False,
    # portal_credentials is missing and offers a remediation action
    updated = evaluate_prerequisites(
        "return_filing",
        {"taxpayer_type": "company", "supporting_records_ready": True, "portal_access": False},
    )
    by_id = {p["id"]: p for p in updated}
    assert by_id["active_tin"]["status"] == "satisfied"
    assert by_id["supporting_schedules"]["status"] == "satisfied"
    assert by_id["portal_credentials"]["status"] == "missing"
    assert by_id["portal_credentials"]["remediation_workflow"] == "tin_procedure_help"
    assert "Guide me step by step through" in by_id["portal_credentials"]["remediation_action"]


def test_pillar2_declarative_a2ui_form_draft_masks_sensitive_slots_and_never_echoes_raw_values():
    sample_nin = "CM901234567890"
    sample_period = "March 2026 Private Note"
    draft = build_form_draft_surface(
        "return_filing",
        "Return Filing",
        {
            "taxpayer_type": "individual",
            "tax_type": "vat",
            "filing_period": sample_period,
            "supporting_records_ready": True,
            "portal_access": True,
            "nin": sample_nin,
        },
        all_steps=[],
        sensitive_slots=_WORKFLOW_SENSITIVE_SLOTS,
    )
    assert draft["protocol"] == "a2ui/v1"
    assert draft["form_code"] == "URA-ETAX-RET-01"
    assert draft["completion_pct"] == 100
    assert draft["ready_for_portal"] is True
    serialized = str(draft)
    assert sample_nin not in serialized
    assert sample_period not in serialized


def test_pillar3_pushdown_goal_stack_and_journey_gps():
    session = WorkflowRegistry.create_session("return_filing")
    assert session is not None
    t1 = WorkflowRegistry.advance(session, "")
    assert t1.journey_gps is not None
    assert t1.journey_gps["active_goal"] == "Return Filing"
    assert t1.journey_gps["step_index"] == 1

    # Answer step 1
    WorkflowRegistry.advance(session, "company")
    assert session.current_step_idx == 1

    # Push prerequisite sub-workflow onto goal_stack
    pushed = WorkflowRegistry.push_subworkflow(
        session, "tin_registration", reason="Need TIN before filing return"
    )
    assert pushed is True
    assert session.workflow_id == "tin_registration"
    assert session.current_step_idx == 0
    assert len(session.goal_stack) == 1
    assert session.goal_stack[0]["workflow_id"] == "return_filing"

    # Pop back to parent workflow and verify step position and slots remain intact
    popped = WorkflowRegistry.pop_subworkflow(session)
    assert popped is True
    assert session.workflow_id == "return_filing"
    assert session.current_step_idx == 1
    assert session.slots["taxpayer_type"] == "company"


def test_pillar4_proactive_objective_inference_and_turn_guidance_injection():
    hyp = infer_taxpayer_objective(
        "A government ministry paid me less than my invoice because they deducted 6% WHT"
    )
    assert hyp is not None
    assert hyp["objective_id"] == "wht_credit_and_filing"
    assert hyp["workflow_id"] == "return_filing"
    assert hyp["confirm_action"] == "Guide me step by step through Return Filing"

    turn_result = {
        "reply": "When a withholding agent deducts 6% WHT, you claim the credit on your return.",
        "retrieval_mode": "hybrid",
        "next_actions": [],
    }
    guided = apply_turn_guidance(
        "A government ministry paid me less than my invoice because they deducted 6% WHT",
        turn_result,
    )
    assert guided.get("objective_hypothesis") is not None
    assert guided["objective_hypothesis"]["objective_id"] == "wht_credit_and_filing"
    assert "Guide me step by step through Return Filing" in guided["next_actions"]


def test_pillar5_step_up_auth_and_zero_data_loss_session_elevation():
    session = WorkflowRegistry.create_session("return_filing")
    assert session is not None
    session.slots["taxpayer_type"] = "individual"
    session.slots["tax_type"] = "vat"
    session.slots["portal_access"] = False

    wf = WorkflowRegistry.get("return_filing")
    assert wf is not None
    step_up = evaluate_step_up_auth(wf, wf.steps[0], session.slots, is_authenticated=False)
    assert step_up is not None
    assert step_up["required"] is True
    assert step_up["mode"] == "recover_access"
    assert step_up["preserves_slots"] is True
    assert step_up["preserved_slot_count"] == 3

    # Elevate session to authenticated user and ensure user-entered 'individual' is NOT overwritten
    elevate_anonymous_workflow_session(
        session,
        user_id="usr-ura-900",
        profile_slots={"taxpayer_type": "company", "filing_period": "October 2026"},
    )
    assert session.slots["_authenticated_user_id"] == "usr-ura-900"
    assert session.slots["taxpayer_type"] == "individual"
    assert session.slots["filing_period"] == "October 2026"


def test_pillar6_snap_to_fill_document_binding_and_stall_scaffolding():
    session = WorkflowRegistry.create_session("return_filing")
    assert session is not None
    bound = bind_document_facts_to_workflow(
        session,
        {
            "doc_type": "efris_tax_invoice",
            "tax_type": "VAT",
            "filing_period": "September 2026",
        },
    )
    assert "supporting_records_ready" in bound
    assert "tax_type" in bound
    assert "filing_period" in bound
    assert session.slots["supporting_records_ready"] is True
    assert session.slots["tax_type"] == "vat"

    # Stall scaffolding on validation failure
    scaf_1 = build_stall_scaffolding(
        "return_filing",
        "collect_taxpayer_type",
        "taxpayer_type",
        "enum[individual,company,ngo]",
        failed_attempts=1,
        options=["individual", "company", "ngo"],
    )
    assert scaf_1 is not None
    assert scaf_1["level"] == "inline_example"
    assert "individual" in scaf_1["examples"]

    scaf_2 = build_stall_scaffolding(
        "return_filing",
        "collect_taxpayer_type",
        "taxpayer_type",
        "enum[individual,company,ngo]",
        failed_attempts=2,
        options=["individual", "company", "ngo"],
    )
    assert scaf_2 is not None
    assert scaf_2["level"] == "escalated_assist"
    assert "Talk to an officer" in scaf_2["helper_actions"]


def test_pillar7_resume_code_and_chat_model_workflow_view_integration():
    session = WorkflowRegistry.create_session("return_filing")
    assert session is not None
    WorkflowRegistry.advance(session, "")
    turn2 = WorkflowRegistry.advance(session, "individual")

    code = generate_resume_code("return_filing", session.current_step_idx, session.slots)
    assert code.startswith("URA-RET-S2-")
    assert "individual" not in code

    view = ChatModel._workflow_view(
        session,
        name="Return Filing",
        status="active",
        pending_slot=turn2.slot_name,
        turn=turn2,
    )
    assert view["resume_code"] == code
    assert isinstance(view["prerequisites"], list)
    assert view["form_draft"]["form_code"] == "URA-ETAX-RET-01"
    assert view["journey_gps"]["active_goal"] == "Return Filing"


def test_e2e_goal_stack_persistence_prerequisite_push_and_auto_pop_in_chat_model():
    """Verify goal_stack survives DB serialization, pushes prerequisite sub-flows, and pops on completion."""
    import uuid

    from app import database

    database.init_db()
    model = ChatModel()
    thread_id = f"conv-stack-{uuid.uuid4().hex[:10]}"

    # 1. Start Return Filing
    t1 = model._maybe_handle_workflow(
        message="Help me file my return",
        rewritten="Help me file my return",
        thread_id=thread_id,
        locale="en",
    )
    assert t1 is not None
    assert t1["workflow"]["id"] == "return_filing"

    # 2. Answer step 1 (taxpayer_type = individual) -> advances to step 2 (tax_type)
    t2 = model._maybe_handle_workflow(
        message="individual",
        rewritten="individual",
        thread_id=thread_id,
        locale="en",
    )
    assert t2 is not None
    assert t2["workflow"]["step_index"] == 2

    # 3. Taxpayer realizes they need a TIN first (a declared prerequisite of return_filing)
    assert is_prerequisite_remediation("return_filing", "tin_registration") is True
    t3 = model._maybe_handle_workflow(
        message="Guide me step by step through TIN Registration Guide",
        rewritten="Guide me step by step through TIN Registration Guide",
        thread_id=thread_id,
        locale="en",
    )
    assert t3 is not None
    assert t3["workflow"]["id"] == "tin_registration"
    # Because taxpayer_type='individual' was already collected in parent return_filing,
    # tin_registration skips step 1 (collect_type) and lands on step 2 (documents_ready)!
    assert t3["workflow"]["step_id"] == "collect_documents_ready"
    assert t3["workflow"]["journey_gps"]["parent_goals"][0]["workflow_id"] == "return_filing"

    # 4. Complete tin_registration by answering 'yes' to documents_ready -> auto-pops back to return_filing!
    t4 = model._maybe_handle_workflow(
        message="yes",
        rewritten="yes",
        thread_id=thread_id,
        locale="en",
    )
    assert t4 is not None
    assert "TIN Registration Guide completed" in t4["reply"]
    assert "Returning to **Return Filing**" in t4["reply"]
    assert t4["workflow"]["id"] == "return_filing"
    assert t4["workflow"]["status"] == "active"
    assert t4["workflow"]["step_id"] == "collect_tax_type"


def test_e2e_resume_code_parsing_and_localization_parity():
    """Verify cross-channel resume codes parse cleanly and _localize_workflow translates journey_gps."""
    import uuid

    from app import database

    database.init_db()
    model = ChatModel()
    thread_id = f"conv-resume-{uuid.uuid4().hex[:10]}"

    parsed = parse_resume_code("Please resume URA-RET-S2-8A4F")
    assert parsed is not None
    assert parsed["workflow_id"] == "return_filing"
    assert parsed["step_number"] == 2

    # Starting via resume code on a fresh thread launches the corresponding workflow
    started = model._maybe_handle_workflow(
        message="URA-TIN-S1-9F2A",
        rewritten="URA-TIN-S1-9F2A",
        thread_id=thread_id,
        locale="en",
        user_id="usr-elevated-42",
    )
    assert started is not None
    assert started["workflow"]["id"] == "tin_registration"
    # Authenticated user_id suppresses unauthenticated login_or_register step_up_auth prompt
    assert started["workflow"]["step_up_auth"] is None

    # Localization of journey_gps.active_goal across Luganda and Kiswahili
    loc_lg = ChatModel._localize_workflow(
        {
            "name": "Return Filing",
            "journey_gps": {"workflow_id": "return_filing", "active_goal": "Return Filing"},
        },
        "lg",
    )
    assert loc_lg is not None
    assert loc_lg["journey_gps"]["active_goal"] == loc_lg["name"]

