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
    required = (
        "return_filing",
        "tin_registration",
        "payment_assistance",
        "motor_vehicle_registration",
        "mda_ntr_resolution",
    )
    if any(WorkflowRegistry.get(wf_id) is None for wf_id in required):
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
    assert by_id["portal_credentials"]["remediation_workflow"] == "tin_registration"
    assert by_id["portal_credentials"]["remediation_action"] == "Guide me step by step through TIN Registration Guide"


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


def test_qa_audit_subworkflow_cancel_returns_to_parent_and_anon_to_auth_migration():
    """Verify QA-1 (anon->auth state key migration), QA-2 (sub-workflow cancel pop), QA-3 (cross-flow resume code), QA-4 (YAML slot alignment), and QA-6 (Node.js 24 CI pins)."""
    import uuid

    from app import database
    from app.service import _ACTIVE_CONVERSATION_STATE, _ACTIVE_TENANT_ID

    database.init_db()
    model = ChatModel()
    thread_id = f"conv-qa-{uuid.uuid4().hex[:10]}"

    # QA-1: Start return_filing under anonymous state key (user_id=None)
    anon_state = database.conversation_state_key(thread_id, user_id=None, tenant_id="default")
    tok_state = _ACTIVE_CONVERSATION_STATE.set(anon_state)
    tok_tenant = _ACTIVE_TENANT_ID.set("default")
    try:
        r1 = model._maybe_handle_workflow(
            message="Help me file my return",
            rewritten="Help me file my return",
            thread_id=thread_id,
            locale="en",
            user_id=None,
        )
        assert r1 is not None
        assert r1["workflow"]["id"] == "return_filing"

        r2 = model._maybe_handle_workflow(
            message="company",
            rewritten="company",
            thread_id=thread_id,
            locale="en",
            user_id=None,
        )
        assert r2 is not None
        assert r2["workflow"]["step_index"] == 2
    finally:
        _ACTIVE_CONVERSATION_STATE.reset(tok_state)
        _ACTIVE_TENANT_ID.reset(tok_tenant)

    # Now user authenticates (user_id="usr-qa-99") -> state_thread_id changes to auth_state!
    auth_state = database.conversation_state_key(thread_id, user_id="usr-qa-99", tenant_id="default")
    assert auth_state != anon_state
    tok_state2 = _ACTIVE_CONVERSATION_STATE.set(auth_state)
    tok_tenant2 = _ACTIVE_TENANT_ID.set("default")
    try:
        # Push prerequisite sub-workflow tin_registration while authenticated
        r3 = model._maybe_handle_workflow(
            message="Guide me step by step through TIN Registration Guide",
            rewritten="Guide me step by step through TIN Registration Guide",
            thread_id=thread_id,
            locale="en",
            user_id="usr-qa-99",
        )
        assert r3 is not None
        # Adopted anonymous session (where taxpayer_type='company' was already answered)
        # and pushed tin_registration onto goal_stack!
        assert r3["workflow"]["id"] == "tin_registration"
        assert r3["workflow"]["step_id"] == "collect_documents_ready"

        # QA-2: Cancel the sub-workflow -> must pop back to parent return_filing (Step 2) instead of cancelling parent!
        r4 = model._maybe_handle_workflow(
            message="cancel",
            rewritten="cancel",
            thread_id=thread_id,
            locale="en",
            user_id="usr-qa-99",
        )
        assert r4 is not None
        assert "Returning to **Return Filing**" in r4["reply"]
        assert r4["workflow"]["id"] == "return_filing"
        assert r4["workflow"]["status"] == "active"
        assert r4["workflow"]["step_id"] == "collect_tax_type"

        # QA-3: Pasting a resume code for a different workflow (URA-PAY-S1-1234) while return_filing is active
        # switches cleanly to payment_assistance instead of failing return_filing's enum validator!
        r5 = model._maybe_handle_workflow(
            message="URA-PAY-S1-1234",
            rewritten="URA-PAY-S1-1234",
            thread_id=thread_id,
            locale="en",
            user_id="usr-qa-99",
        )
        assert r5 is not None
        assert r5["workflow"]["id"] == "payment_assistance"
    finally:
        _ACTIVE_CONVERSATION_STATE.reset(tok_state2)
        _ACTIVE_TENANT_ID.reset(tok_tenant2)

    # QA-4: Verify motor_vehicle_registration and mda_ntr_resolution prerequisites bind to exact YAML slots
    mvr_prereqs = evaluate_prerequisites(
        "motor_vehicle_registration",
        {"registration_kind": "first registration", "has_tin": False},
    )
    mvr_by_id = {p["id"]: p for p in mvr_prereqs}
    assert mvr_by_id["vehicle_registration_kind"]["status"] == "satisfied"
    assert mvr_by_id["vehicle_owner_tin"]["status"] == "missing"

    mda_prereqs = evaluate_prerequisites(
        "mda_ntr_resolution",
        {"mda_service": "passport or work permit", "has_prn": True},
    )
    mda_by_id = {p["id"]: p for p in mda_prereqs}
    assert mda_by_id["mda_service_selected"]["status"] == "satisfied"
    assert mda_by_id["ntr_prn_available"]["status"] == "satisfied"

    # QA-6: Verify Node.js 24 upgrade across .github/workflows/*.yml
    workflows_dir = FLOWS_DIR.parents[4] / ".github" / "workflows"
    deprecated_node20_pins = (
        "setup-python@a26af69b",
        "download-artifact@d3f86a10",
        "build-push-action@4f58ea79",
        "checkout@34e11487",
        "cache@0057852b",
        "github-script@f28e40c7",
    )
    for wf_file in sorted(workflows_dir.glob("*.yml")):
        content = wf_file.read_text(encoding="utf-8")
        for old_pin in deprecated_node20_pins:
            assert old_pin not in content, f"{wf_file.name} still references Node 20 action {old_pin}"
        assert "NODE_VERSION: '20'" not in content
        assert 'NODE_VERSION: "20"' not in content
        assert "NODE_VERSION: 20" not in content


def test_post_619_phased_audit_remediations_session_binding_goal_stack_snap_to_fill_and_sha_pins():
    """Verify Phase 1-4 post-#619 QA audit remediations end-to-end."""
    import re
    import uuid
    from types import SimpleNamespace

    from app import database
    from app.service import _ACTIVE_CONVERSATION_STATE, _ACTIVE_TENANT_ID
    from app.turn_guidance import apply_turn_guidance
    from app.workflows.journey_copilot import (
        _OBJECTIVE_PATTERNS,
        WORKFLOW_FORM_SCHEMAS,
        WORKFLOW_PREREQUISITE_DAG,
    )

    database.init_db()
    model = ChatModel()

    # 1. Every remediation_action / confirm_action across DAG, Objectives, and Step-Up Auth resolves via match_trigger
    for wf_id, specs in WORKFLOW_PREREQUISITE_DAG.items():
        wf_def = WorkflowRegistry.get(wf_id)
        assert wf_def is not None, f"Unknown workflow {wf_id} in WORKFLOW_PREREQUISITE_DAG"
        valid_slots = {s.slot for s in wf_def.steps if s.slot}
        for spec in specs:
            assert spec.slot in valid_slots, f"{wf_id}.{spec.id} references non-existent slot {spec.slot}"
            if spec.remediation_workflow:
                rem_action = f"Guide me step by step through {spec.remediation_workflow_name}"
                matched = WorkflowRegistry.match_trigger(rem_action)
                assert matched is not None
                assert matched.id == spec.remediation_workflow

    for wf_id, (_code, _title, field_specs) in WORKFLOW_FORM_SCHEMAS.items():
        wf_def = WorkflowRegistry.get(wf_id)
        assert wf_def is not None
        valid_slots = {s.slot for s in wf_def.steps if s.slot}
        for fspec in field_specs:
            assert fspec.slot in valid_slots, f"Form schema {wf_id} box {fspec.box} references unknown slot {fspec.slot}"

    for _pat, meta in _OBJECTIVE_PATTERNS:
        matched = WorkflowRegistry.match_trigger(f"Guide me step by step through {meta['workflow_name']}")
        assert matched is not None
        assert matched.id == meta["workflow_id"]

    rf_wf = WorkflowRegistry.get("return_filing")
    tin_wf = WorkflowRegistry.get("tin_registration")
    assert rf_wf is not None
    assert tin_wf is not None
    su_recover = evaluate_step_up_auth(rf_wf, rf_wf.steps[0], {"portal_access": False})
    assert su_recover is not None
    matched_rec = WorkflowRegistry.match_trigger(su_recover["remediation_action"])
    assert matched_rec is not None
    assert matched_rec.id == su_recover["remediation_workflow_id"] == "tin_registration"

    su_docs = evaluate_step_up_auth(tin_wf, tin_wf.steps[0], {"documents_ready": False})
    assert su_docs is not None
    matched_docs = WorkflowRegistry.match_trigger(su_docs["remediation_action"])
    assert matched_docs is not None
    assert matched_docs.id == su_docs["remediation_workflow_id"] == "mda_ntr_resolution"

    # 2. Out-of-jurisdiction / blocked turns must never attach an objective_hypothesis
    ooj_res = {"reply": "This service covers Uganda.", "retrieval_mode": "out_of_jurisdiction", "next_actions": []}
    apply_turn_guidance("My client deducted 6% WHT in Kenya", ooj_res)
    assert "objective_hypothesis" not in ooj_res

    # 3. Session-bound anonymous -> authenticated migration with session_id present + side-question & parent resume pop
    thread_id = f"conv-p1-{uuid.uuid4().hex[:10]}"
    sess_id = f"sess-{uuid.uuid4().hex[:8]}"
    anon_session_state = database.conversation_state_key(
        thread_id, session_id=sess_id, user_id=None, tenant_id="default"
    )
    tok_s = _ACTIVE_CONVERSATION_STATE.set(anon_session_state)
    tok_t = _ACTIVE_TENANT_ID.set("default")
    try:
        # Start return_filing with a DocumentRecord attachment (Pillar 6 Snap-to-Fill end-to-end!)
        mock_attachment = SimpleNamespace(
            doc_type="invoice",
            fields={"tax_heads": ["vat"], "efris_invoice_numbers": ["FDN1234567890123456"]},
        )
        t1 = model._maybe_handle_workflow(
            message="Help me file my return",
            rewritten="Help me file my return",
            thread_id=thread_id,
            locale="en",
            user_id=None,
            session_id=sess_id,
            attachments=[mock_attachment],
        )
        assert t1 is not None
        assert t1["workflow"]["id"] == "return_filing"
        # Answer step 1 (taxpayer_type = company); step 2 (tax_type='vat') was pre-filled by Snap-to-Fill attachment!
        t2 = model._maybe_handle_workflow(
            message="company",
            rewritten="company",
            thread_id=thread_id,
            locale="en",
            user_id=None,
            session_id=sess_id,
        )
        assert t2 is not None
        # Because tax_type='vat' was pre-filled from the invoice attachment, it skipped collect_tax_type
        # and advanced straight to collect_period!
        assert t2["workflow"]["step_id"] == "collect_period"
    finally:
        _ACTIVE_CONVERSATION_STATE.reset(tok_s)
        _ACTIVE_TENANT_ID.reset(tok_t)

    anon_created_at = database.get_workflow_session(anon_session_state, tenant_id="default")["created_at"]

    # Now user logs in (user_id="usr-p1-auth", same session_id=sess_id) and asks an off-topic side question first!
    auth_state = database.conversation_state_key(
        thread_id, session_id=sess_id, user_id="usr-p1-auth", tenant_id="default"
    )
    tok_s2 = _ACTIVE_CONVERSATION_STATE.set(auth_state)
    tok_t2 = _ACTIVE_TENANT_ID.set("default")
    try:
        side_q = model._maybe_handle_workflow(
            message="What is the penalty if I pay on the 20th instead of the 15th?",
            rewritten="What is the penalty if I pay on the 20th instead of the 15th?",
            thread_id=thread_id,
            locale="en",
            user_id="usr-p1-auth",
            session_id=sess_id,
        )
        # Side question yields None so RAG answers it, AND the migrated session is safely persisted under auth_state!
        assert side_q is None
        # Re-keyed in place: no anonymous copy of the answers is left behind, and
        # the journey keeps its start time instead of counting as cancelled + new.
        assert database.get_workflow_session(anon_session_state, tenant_id="default") is None
        migrated_row = database.get_workflow_session(auth_state, tenant_id="default")
        assert migrated_row is not None
        assert migrated_row["status"] == "active"
        assert migrated_row["user_id"] == "usr-p1-auth"
        assert migrated_row["created_at"] == anon_created_at
        assert migrated_row["slots"]["taxpayer_type"] == "company"
        assert migrated_row["slots"]["tax_type"] == "vat"

        # Suspended workflow name lookup resolves under auth_state even without contextvar
        assert (
            model._get_suspended_workflow_name(
                thread_id, session_id=sess_id, user_id="usr-p1-auth", tenant_id="default"
            )
            == "Return Filing"
        )

        # Push sub-workflow tin_registration, then return to parent return_filing via parent resume code!
        sub_turn = model._maybe_handle_workflow(
            message="Guide me step by step through TIN Registration Guide",
            rewritten="Guide me step by step through TIN Registration Guide",
            thread_id=thread_id,
            locale="en",
            user_id="usr-p1-auth",
            session_id=sess_id,
        )
        assert sub_turn is not None
        assert sub_turn["workflow"]["id"] == "tin_registration"

        pop_turn = model._maybe_handle_workflow(
            message="URA-RET-S3-ABCD",
            rewritten="URA-RET-S3-ABCD",
            thread_id=thread_id,
            locale="en",
            user_id="usr-p1-auth",
            session_id=sess_id,
        )
        assert pop_turn is not None
        assert pop_turn["workflow"]["id"] == "return_filing"
        # Preserved parent slots (company + vat) and parked step (collect_period) instead of resetting to Step 1!
        assert pop_turn["workflow"]["step_id"] == "collect_period"
        assert "taxpayer_type" in pop_turn["workflow"]["filled_slots"]
        assert "tax_type" in pop_turn["workflow"]["filled_slots"]
    finally:
        _ACTIVE_CONVERSATION_STATE.reset(tok_s2)
        _ACTIVE_TENANT_ID.reset(tok_t2)

    # 4. Supply-chain gate: 100% of workflow uses: lines are 40-char SHA pinned and Crane Cloud Dockerfile is Node 24
    repo_root = FLOWS_DIR.parents[4]
    workflows_dir = repo_root / ".github" / "workflows"
    uses_re = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.M)
    sha_pin_re = re.compile(r"^[^@]+@[0-9a-f]{40}$")
    forbidden_pins = (
        "codecov-action@b9fd7d16",
        "fetch-metadata@d7267f60",
        "action-baseline@2b5fc7f9",
        "scorecard-action@2d114668",
        "cache@27d5ce7f",
    )
    for wf_file in sorted(workflows_dir.glob("*.yml")):
        content = wf_file.read_text(encoding="utf-8")
        for match in uses_re.finditer(content):
            action_ref = match.group(1)
            assert sha_pin_re.match(action_ref), f"Unpinned action in {wf_file.name}: {action_ref}"
        for bad in forbidden_pins:
            assert bad not in content, f"{wf_file.name} still contains outdated action pin {bad}"

    crane_df = (repo_root / "App" / "Dockerfile.cranecloud").read_text(encoding="utf-8")
    assert "FROM node:24-bookworm-slim AS frontend-builder" in crane_df
    assert "COPY --from=oven/bun:1.4.3-slim /usr/local/bin/bun /usr/local/bin/bun" in crane_df
    assert "COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node" in crane_df
    assert "App/frontend/bun.lock*" in crane_df
    assert "node:20" not in crane_df
    assert "setup_20.x" not in crane_df
    assert "setup_24.x" not in crane_df



