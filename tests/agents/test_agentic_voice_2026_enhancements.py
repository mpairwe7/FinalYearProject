"""Comprehensive verification suite for the October 2026 Agentic Voice CX & Speech Enhancements (E1–E9).

Covers:
1. Feature flags registration, default-off safety, and variant logging (`flags.py`).
2. Hybrid S2S + Regulated Cascaded Speech Router & Semantic Endpointing (`receptionist/hybrid_s2s.py`).
3. Acoustic Prosody & Emotion Conditioning (`receptionist/prosody.py`) + Orpheus first-clause streaming (`orpheus_tts.py`).
4. Spoken-Figure Round-Trip Verification & Dictation Pacing (`receptionist/spoken_figure_guard.py`).
5. Intra-Utterance Uganda Code-Switching Normalizer (`receptionist/lexicon.py`).
6. Multi-Agent Specialist DAG Chaining (`agents/supervisor.py`, `agents/graphs/main_graph.py`).
7. Enriched `HandoffEnvelope` v2 (`memory/handoff.py`, `receptionist/transfer.py`).
8. Continuous Relationship `ResolutionCase` Ledger & Bidirectional Carryover (`memory/resolution_ledger.py`, `receptionist/carryover.py`).
9. Spoken Confirmation Token Binding & Officer Voice Co-Pilot (`receptionist/brain.py`, `receptionist/taps.py`).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
from app.agents.graphs.main_graph import node_observe, node_route
from app.agents.graphs.state import AgentGraphState
from app.agents.state import AgentRoute
from app.agents.supervisor import supervisor
from app.flags import flags
from app.memory import (
    EvidenceCard,
    HandoffEnvelope,
    ResolutionCase,
    ToolExecutionReceipt,
    build_resumption_prompt,
    get_memory_service,
)
from app.orpheus_tts import split_for_voice
from app.receptionist.brain import UraReceptionistBrain
from app.receptionist.carryover import call_to_web_carryover
from app.receptionist.hybrid_s2s import (
    classify_hybrid_speech_mode,
    compute_semantic_endpoint_silence_ms,
)
from app.receptionist.lexicon import normalize_codeswitched_query
from app.receptionist.prosody import analyze_utterance_prosody, empathetic_opening_for
from app.receptionist.spoken_figure_guard import (
    inject_dictation_pauses,
    verify_spoken_figures,
)
from app.receptionist.state import CallRoom, CallState
from app.receptionist.taps import CallerAudioTap


def test_2026_flags_registered_and_logged_variants():
    all_flags = flags.all()
    for name in (
        "hybrid_speech_s2s",
        "voice_agentic_workflows",
        "multi_agent_dag",
        "continuous_relationship_memory",
        "emotion_aware_prosody",
        "spoken_figure_guard",
        "officer_voice_copilot",
        "eq_adaptive_retrieval",
        "zero_effort_slot_prefill",
        "workflow_specialist_bridge",
        "proactive_resolution_continuation",
        "officer_ai_co_execution",
        "low_bandwidth_voice_codec",
        "call_drop_resume",
        "offline_action_outbox",
        "action_level_audit_v3",
        "continuous_quality_scorecard",
        "risk_tiered_autonomy_gates",
        "quality_circuit_breaker",
        "verifiable_decision_receipts",
    ):
        assert name in all_flags
        assert all_flags[name] is False

    variants = flags.logged_variants(subject="taxpayer-101")
    assert "hybrid_speech_s2s" in variants
    assert "multi_agent_dag" in variants
    assert "spoken_figure_guard" in variants
    assert "eq_adaptive_retrieval" in variants
    assert "workflow_specialist_bridge" in variants
    assert "low_bandwidth_voice_codec" in variants
    assert "call_drop_resume" in variants
    assert "action_level_audit_v3" in variants
    assert "continuous_quality_scorecard" in variants
    assert "risk_tiered_autonomy_gates" in variants
    assert "quality_circuit_breaker" in variants
    assert "verifiable_decision_receipts" in variants


def test_hybrid_speech_s2s_router_and_semantic_endpointing():
    # Flag off -> always cascaded_guarded
    dec_off = classify_hybrid_speech_mode("Hello, good morning", locale="en")
    assert dec_off.mode == "cascaded_guarded"
    assert dec_off.reason == "flag_disabled"

    flags.set("hybrid_speech_s2s", True)
    try:
        # Conversational greeting -> s2s_fast
        dec_fast = classify_hybrid_speech_mode("Oli otya, gyebale ko", locale="lg")
        assert dec_fast.mode == "s2s_fast"
        assert dec_fast.contains_numeric_or_statutory is False

        # Statutory / numeric / money / TIN turn -> strictly forced to cascaded_guarded
        for risky_query in (
            "What is the VAT rate on UGX 1,500,000?",
            "How do I register for a TIN?",
            "Omusolo gwa PAYE ku bukadde bubiri",
            "Kodi ya ongezeko la thamani ni asilimia ngapi?",
        ):
            dec_guarded = classify_hybrid_speech_mode(risky_query, locale="en")
            assert dec_guarded.mode == "cascaded_guarded", risky_query
            assert dec_guarded.contains_numeric_or_statutory is True

        # Active numeric workflow slot forces cascaded_guarded even for short replies
        dec_slot = classify_hybrid_speech_mode("yes", locale="en", active_workflow_slot_type="tin")
        assert dec_slot.mode == "cascaded_guarded"
    finally:
        flags.clear("hybrid_speech_s2s")

    # Semantic endpointing extends silence when mid-number or mid-conjunction
    assert compute_semantic_endpoint_silence_ms("My TIN number is 100023", locale="en") >= 1000
    assert compute_semantic_endpoint_silence_ms("Nze njagala okumanya ku VAT era", locale="lg") >= 1000
    assert compute_semantic_endpoint_silence_ms("What is the current VAT rate in Uganda?", locale="en") <= 400


def test_acoustic_prosody_and_emotion_conditioning():
    # Generate high-energy bursty 16kHz PCM16 audio (1 second)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    envelope = np.where((t * 10).astype(int) % 2 == 0, 0.85, 0.05)
    samples = (np.sin(2 * np.pi * 240 * t) * envelope * 32767).astype(np.int16)
    pcm = samples.tobytes()

    prof_frustrated = analyze_utterance_prosody(
        pcm,
        sample_rate=16000,
        text="This system is not working and my penalty is unfair!",
        locale="en",
    )
    assert prof_frustrated.caller_affect in ("frustrated", "urgent_distress")
    assert prof_frustrated.recommended_speech_rate < 1.0
    assert empathetic_opening_for(prof_frustrated.caller_affect, "lg").startswith("Ntegedde")
    assert empathetic_opening_for(prof_frustrated.caller_affect, "sw").startswith("Naelewa")

    prof_confused = analyze_utterance_prosody(
        b"\x00\x00" * 8000,
        sample_rate=16000,
        text="Sielewi jinsi ya kuanza",
        locale="sw",
        clarifications_asked=2,
    )
    assert prof_confused.caller_affect == "confused"
    assert empathetic_opening_for("confused", "sw").startswith("Usijali")


def test_spoken_figure_guard_and_dictation_pauses():
    flags.set("spoken_figure_guard", True)
    try:
        # Preserved figure passes
        ok_res = verify_spoken_figures(
            "The standard VAT rate is 18% on UGX 1,500,000.",
            "The standard VAT rate is 18 percent on 1,500,000 Uganda shillings.",
            locale="en",
        )
        assert ok_res.ok is True
        assert ok_res.status == "verified"

        # Mutated figure is caught
        bad_res = verify_spoken_figures(
            "The standard VAT rate is 18%.",
            "The standard VAT rate is 25 percent.",
            locale="en",
        )
        assert bad_res.ok is False
        assert bad_res.status == "fallback_text_only"

        # Vernacular spoken-word realization from clean_text_for_speech passes
        from app.speech_normalization import clean_text_for_speech

        lg_source = "VAT ku UGX 2,000,000 guli 18% (UGX 360,000)."
        lg_spoken = clean_text_for_speech(lg_source, locale="lg")
        lg_res = verify_spoken_figures(lg_source, lg_spoken, locale="lg")
        assert lg_res.ok is True
    finally:
        flags.clear("spoken_figure_guard")

    # Dictation pause injection chunks 10-digit spelled-out TINs
    spelled_tin = "Your TIN is one zero zero zero two three four five six seven"
    chunked = inject_dictation_pauses(spelled_tin)
    assert "," in chunked
    assert "one zero zero" in chunked


def test_codeswitched_query_normalizer_and_orpheus_first_clause():
    # Intra-utterance Enganda code-switching
    norm = normalize_codeswitched_query("Hello, how do I pay omusolo gwa rental on my house?", language="en")
    assert "Rental Income Tax" in norm

    # Orpheus first-clause tighter cap for lower TTFB
    long_sentence = (
        "Thank you for calling Uganda Revenue Authority, I have checked the VAT rate "
        "and it is eighteen percent for standard taxable supplies across Uganda."
    )
    pieces = split_for_voice(long_sentence, limit=120, first_clause_limit=55)
    assert len(pieces) >= 2
    assert len(pieces[0]) <= 55


def test_multi_agent_dag_supervisor_and_graph_handoff():
    query = "I imported industrial machinery worth 50,000,000 — calculate customs duty and VAT deferment rate"

    # Flag off -> single route
    dec_single = supervisor.plan_multi_agent_dag(query, locale="en")
    assert dec_single.requires_multi_agent is False

    flags.set("multi_agent_dag", True)
    flags.set("tool_use", True)
    try:
        dec_dag = supervisor.plan_multi_agent_dag(query, locale="en")
        assert dec_dag.requires_multi_agent is True
        assert AgentRoute.CUSTOMS_SPECIALIST in dec_dag.sub_routes
        assert AgentRoute.TAX_SPECIALIST in dec_dag.sub_routes

        state = AgentGraphState(
            query=query,
            rewritten_query=query,
            locale="en",
            observations=[{"ok": True, "explanation": "Customs duty is UGX 12,500,000."}],
            tool_calls=[{"call_id": "c1", "name": "calculate_customs_duty", "ok": True, "duration_ms": 4.2}],
        )
        route_res = node_route(state)
        assert route_res.next_node == "tool_rag_select"
        assert len(state.sub_routes) >= 2

        obs_res = node_observe(state)
        assert obs_res.next_node in ("tool_rag_select", "retrieve")
        assert state.agent_role == "tax_specialist"
        assert len(state.specialist_handoffs) == 1
        assert state.specialist_handoffs[0]["source_agent"] == "customs_specialist"
        assert state.specialist_handoffs[0]["target_channel"] == "tax_specialist"
        assert len(state.specialist_handoffs[0]["tool_receipts"]) == 1
    finally:
        flags.clear("multi_agent_dag")
        flags.clear("tool_use")


def test_handoff_envelope_v2_and_resolution_ledger(tmp_db):
    memsvc = get_memory_service()
    tenant_id = "default"
    u = tmp_db.upsert_user(external_id="1000555666", tenant_id=tenant_id)
    user_id = u["id"]
    tmp_db.grant_consent(user_id, "personalization", "2026-04")

    env = memsvc.create_handoff_envelope(
        thread_id="th-2026",
        user_id="1000555666",
        objective="Customs & VAT Deferment Objection",
        target_resolution="Verify commercial invoice & approve deferment",
        tenant_id=tenant_id,
        evidence=[EvidenceCard(source="VAT Act Sec 24", fact="Deferment applies to plant & machinery")],
        locale="lg",
        source_channel="voice_call",
        caller_affect="frustrated",
        priority="high",
        tool_receipts=[
            ToolExecutionReceipt(
                tool_name="calculate_customs_duty",
                inputs_summary="CIF 50m",
                deterministic_output={"duty_ugx": 6250000},
            )
        ],
        officer_macros=[{"id": "approve_def", "label": "Verify Deferment", "action": "check_deferment"}],
    )
    assert env.completeness_ratio() == 1.0
    assert env.sla.target_response_seconds == 60
    roundtrip = HandoffEnvelope.from_dict(env.to_dict())
    assert roundtrip.caller_affect == "frustrated"
    assert roundtrip.tool_receipts[0].tool_name == "calculate_customs_duty"
    assert roundtrip.officer_macros[0]["id"] == "approve_def"

    # Low ASR confidence (< 0.60) must NOT write to ResolutionLedger
    case = ResolutionCase(
        user_id=user_id,
        tenant_id=tenant_id,
        thread_id="th-2026",
        source_channel="voice_call",
        locale="lg",
        primary_objective="Customs & VAT Deferment",
        status="awaiting_user_action",
        pending_steps=["Upload commercial invoice"],
        handoff_envelope=env.to_dict(),
    )
    assert memsvc.checkpoint_resolution_case(user_id, case, tenant_id=tenant_id, asr_confidence=0.42) is False
    assert memsvc.get_active_resolution_case(user_id, tenant_id=tenant_id) is None

    # High ASR confidence persists and supports background task continuation + cross-channel carryover
    assert memsvc.checkpoint_resolution_case(user_id, case, tenant_id=tenant_id, asr_confidence=0.91) is True
    updated = memsvc.resolution_ledger.attach_background_task(
        user_id,
        "task-ocr-99",
        tenant_id=tenant_id,
        completed_step="Upload commercial invoice",
        evidence_note="Invoice verified: CIF USD 14,000",
    )
    assert updated is not None
    assert "Upload commercial invoice" in updated.completed_steps

    flags.set("continuous_relationship_memory", True)
    flags.set("memory_enabled", True)
    try:
        prompt_lg = call_to_web_carryover(user_id, tenant_id=tenant_id, target_locale="lg")
        assert "Customs & VAT Deferment" in prompt_lg
        assert build_resumption_prompt(updated, "sw").startswith("Katika kipindi")

        from app.service import ChatModel

        model = ChatModel.__new__(ChatModel)
        p_state = model._load_personalization_state(user_id, tenant_id=tenant_id)
        assert p_state is not None
        assert "Customs & VAT Deferment" in p_state["prompt_context"]

        assert memsvc.resolution_ledger.mark_resolved(user_id, tenant_id=tenant_id, channel="web") is True
        assert memsvc.get_active_resolution_case(user_id, tenant_id=tenant_id) is None
    finally:
        flags.clear("continuous_relationship_memory")
        flags.clear("memory_enabled")


def test_spoken_confirmation_and_officer_voice_copilot(tmp_db):
    state = CallState(call_id="call-2026", conversation_id="conv-2026", user_id="1000777888", locale="lg")
    sample_challenge_id = "chal-2026-1"
    state.pending_confirm_challenge = sample_challenge_id
    state.pending_confirm_action = {"description": "TIN registration submission"}
    room = CallRoom(call_id="call-2026", state=state)
    chat_model = MagicMock()
    brain = UraReceptionistBrain(room=room, chat_model=chat_model)
    spoken_lines: list[tuple[str, str]] = []

    async def fake_say(text: str, kind: str = "answer", **kwargs):
        spoken_lines.append((kind, text))

    brain._say_and_record = fake_say  # type: ignore[method-assign]

    flags.set("voice_agentic_workflows", True)
    try:
        # 1. Low ASR confidence (< 0.75) reprompts instead of confirming
        asyncio.run(brain.handle_external_question("Yee", words=[{"word": "Yee", "prob": 0.55}]))
        assert state.pending_confirm_challenge == sample_challenge_id
        assert spoken_lines[-1][0] == "confirm"

        # 2. High ASR confidence (>= 0.75) completes the confirmed action
        asyncio.run(brain.handle_external_question("Yee, kakasa", words=[{"word": "Yee", "prob": 0.92}]))
        assert state.pending_confirm_challenge == ""
        assert spoken_lines[-1][0] == "answer"
        assert "kikakasiddwa" in spoken_lines[-1][1]
    finally:
        flags.clear("voice_agentic_workflows")

    # 3. Officer Voice Co-Pilot transcribes caller in bridged mode without invoking brain
    flags.set("officer_voice_copilot", True)
    try:
        state.mode = "bridged"
        speech_mock = MagicMock()
        speech_mock.transcribe.return_value = SimpleNamespace(text="Omusolo gwa rental", latency_s=0.1)
        tap = CallerAudioTap(room=room, speech_model=speech_mock)
        asyncio.run(tap._transcribe_caller_copilot(b"\x00\x00" * 1600))
        speech_mock.transcribe.assert_called_once()
    finally:
        flags.clear("officer_voice_copilot")


def test_phase32_eq_adaptive_retrieval_and_sla_reprioritization():
    from app.query import plan_retrieval
    from app.tools.empathy import assess

    flags.set("eq_adaptive_retrieval", True)
    try:
        msg = "I cannot afford this penalty and my shop is about to close down, what can I do?"
        eq = assess(msg)
        assert eq["kind"] == "hardship"
        assert eq["priority_boost"] == "high"
        assert any("Section 39" in s for s in eq["recommended_relief_statutes"])
        assert "instalment" in eq["retrieval_preference_terms"]

        plan = plan_retrieval(msg)
        assert plan["prefer"].get("eq_affect") == "hardship"
        assert any("section 39" in sq.lower() for sq in plan["subqueries"])

        memsvc = get_memory_service()
        env = memsvc.create_handoff_envelope(
            thread_id="th-eq-32",
            user_id="1000888111",
            objective="Penalty remission & instalment plan",
            target_resolution="Review hardship application under TPCA s.39",
            caller_affect="hardship",
            priority="normal",
            eq_guidance={"avoid": eq["avoid"], "relief": eq["recommended_relief_statutes"]},
        )
        assert env.sla.priority == "high"
        assert env.sla.target_response_seconds == 45
        roundtrip = HandoffEnvelope.from_dict(env.to_dict())
        assert "Section 39" in roundtrip.eq_guidance["relief"][0]
    finally:
        flags.clear("eq_adaptive_retrieval")


def test_phase32_zero_effort_slot_prefill_and_workflow_specialist_bridge(tmp_db):
    from pathlib import Path

    from app.service import ChatModel
    from app.workflows.registry import (
        WorkflowSession,
        auto_load_flows,
        delegate_workflow_slot_calculation,
    )

    auto_load_flows(Path("App/backend/app/workflows/flows"))
    memsvc = get_memory_service()
    u = tmp_db.upsert_user(external_id="1000444333", tenant_id="default")
    user_id = u["id"]
    tmp_db.grant_consent(user_id, "personalization", "2026-04")

    assert memsvc.resolution_ledger.store_verified_slots(
        user_id,
        {"tin": "1000444333", "annual_turnover": 180000000},
        tenant_id="default",
    ) is True
    slots = memsvc.resolution_ledger.get_verified_slots(user_id, tenant_id="default")
    assert slots["tin"] == "1000444333"

    flags.set("zero_effort_slot_prefill", True)
    flags.set("memory_enabled", True)
    flags.set("workflow_specialist_bridge", True)
    try:
        model = ChatModel.__new__(ChatModel)
        p_state = model._load_personalization_state(user_id, tenant_id="default")
        assert p_state is not None
        assert p_state["prefill_slots"]["tin"] == "1000444333"
        assert p_state["prefill_slots"]["annual_turnover"] == 180000000

        session = WorkflowSession(workflow_id="payment_assistance")
        val_str, receipt = delegate_workflow_slot_calculation(
            session,
            "Calculate VAT on UGX 5,000,000",
        )
        assert val_str == "900000"
        assert receipt is not None
        assert receipt.get("ok") is True
    finally:
        flags.clear("zero_effort_slot_prefill")
        flags.clear("memory_enabled")
        flags.clear("workflow_specialist_bridge")


def test_phase32_proactive_continuation_and_officer_ai_co_execution(tmp_db):
    from pathlib import Path

    from app import officer_chat
    from app.workflows.registry import auto_load_flows

    auto_load_flows(Path("App/backend/app/workflows/flows"))
    memsvc = get_memory_service()
    u = tmp_db.upsert_user(external_id="1000222111", tenant_id="default")
    user_id = u["id"]
    tmp_db.grant_consent(user_id, "personalization", "2026-04")

    case = ResolutionCase(
        user_id=user_id,
        tenant_id="default",
        thread_id="conv-p32",
        source_channel="voice_call",
        locale="en",
        primary_objective="EFRIS Invoice Audit",
        status="in_progress_background",
        pending_steps=["Verify EFRIS fiscal receipt"],
    )
    assert memsvc.checkpoint_resolution_case(user_id, case, asr_confidence=0.95) is True

    flags.set("proactive_resolution_continuation", True)
    flags.set("officer_ai_co_execution", True)
    try:
        memsvc.resolution_ledger.attach_background_task(
            user_id,
            "task-efris-32",
            completed_step="Verify EFRIS fiscal receipt",
            evidence_note="Fiscal invoice FDN-8891 verified valid",
        )
        reminders = tmp_db.list_reminder_inbox(user_id)
        assert any("EFRIS Invoice Audit" in r.get("deadline_name", "") for r in reminders)

        ticket = tmp_db.create_ticket(
            reason="dispute_resolved_followup",
            conversation_id="conv-p32",
            user_id=user_id,
            locale="en",
        )
        draft = officer_chat.build_officer_copilot_draft(
            ticket,
            "Your VAT liability is 18% on UGX 2,000,000 (UGX 360,000).",
        )
        assert draft["figures_verified"] is True
        assert "18%" in draft["draft_localized"]

        delegated = officer_chat.delegate_ticket_to_ai(
            ticket,
            officer_id="staff-007",
            officer_name="Officer Namuli",
            workflow_id="tin_procedure_help",
            prefill_slots={"taxpayer_kind": "individual"},
        )
        assert delegated["ok"] is True
        assert delegated["delegated_workflow_id"] == "tin_procedure_help"
        assert delegated["prefilled_slots"]["taxpayer_kind"] == "individual"
    finally:
        flags.clear("proactive_resolution_continuation")
        flags.clear("officer_ai_co_execution")


def test_phase33_dynamic_turn_stop_and_bantu_acronym_phonetics():
    from types import SimpleNamespace

    from app.receptionist.turns import apply_dynamic_turn_timeout, effective_turn_timeout_s
    from app.speech_normalization import phoneticize_bantu_acronyms

    state = CallState(call_id="c-p33", conversation_id="conv-p33", locale="lg")
    room = CallRoom(call_id="c-p33", state=state)
    dummy_strategy = SimpleNamespace(_user_speech_timeout=1.0)

    state.semantic_endpoint_silence_ms = 320
    assert effective_turn_timeout_s(room) == 0.32
    assert apply_dynamic_turn_timeout(dummy_strategy, room) == 0.32
    assert dummy_strategy._user_speech_timeout == 0.32

    state.semantic_endpoint_silence_ms = 1100
    assert effective_turn_timeout_s(room) == 1.1
    assert apply_dynamic_turn_timeout(dummy_strategy, room) == 1.1
    assert dummy_strategy._user_speech_timeout == 1.1

    phon = phoneticize_bantu_acronyms("Nsaba okumanya ku TIN ne PRN yange.", locale="lg")
    assert "T-I-N" in phon
    assert "P-R-N" in phon


def test_phase33_low_bandwidth_mulaw_codec_and_call_drop_resume(monkeypatch):
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.receptionist.low_bandwidth import (
        mint_call_resume_ticket,
        mulaw_8k_to_pcm16_16k,
        negotiate_low_bandwidth_mode,
        pcm16_16k_to_mulaw_8k,
        verify_call_resume_ticket,
    )

    # 1. 16kHz PCM16 <-> 8kHz G.711 mu-law (4x compression)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    sine_pcm16 = (np.sin(2 * np.pi * 440 * t) * 20000).astype(np.int16).tobytes()
    assert len(sine_pcm16) == 32000
    mulaw = pcm16_16k_to_mulaw_8k(sine_pcm16)
    assert len(mulaw) == 8000
    restored = mulaw_8k_to_pcm16_16k(mulaw)
    assert len(restored) == 32000
    orig_arr = np.frombuffer(sine_pcm16, dtype=np.int16).astype(np.float32)
    rest_arr = np.frombuffer(restored, dtype=np.int16).astype(np.float32)
    corr = float(np.corrcoef(orig_arr, rest_arr)[0, 1])
    assert corr > 0.95

    flags.set("low_bandwidth_voice_codec", True)
    flags.set("call_drop_resume", True)
    try:
        prof_2g = negotiate_low_bandwidth_mode({"effective_type": "2g", "save_data": True})
        assert prof_2g.enabled is True
        assert prof_2g.codec == "mulaw_8k"
        assert prof_2g.sample_rate == 8000
        assert prof_2g.caption_only is False

        prof_slow2g = negotiate_low_bandwidth_mode({"effective_type": "slow-2g", "rtt_ms": 1800})
        assert prof_slow2g.enabled is True
        assert prof_slow2g.caption_only is True

        # Fail closed when signing material is absent
        monkeypatch.delenv("RECEPTIONIST_RESUME_SECRET", raising=False)
        monkeypatch.delenv("AUTH_DEV_SECRET", raising=False)
        assert mint_call_resume_ticket("call-p33-0", "conv-p33-0", user_id="1000333222") == ""

        monkeypatch.setenv("RECEPTIONIST_RESUME_SECRET", os.urandom(16).hex())
        ticket_str = mint_call_resume_ticket(
            "call-p33-1",
            "conv-p33-1",
            user_id="1000333222",
            tenant_id="default",
        )
        assert ticket_str

        # Malformed / non-ASCII input returns None without raising
        assert verify_call_resume_ticket("non-ascii-body-é.sig-é") is None

        # Concurrent redemption race: exactly one of two synchronized callers succeeds
        barrier = threading.Barrier(2)

        def _redeem():
            barrier.wait(timeout=5.0)
            return verify_call_resume_ticket(
                ticket_str,
                expected_call_id="call-p33-1",
                expected_user_id="1000333222",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_redeem)
            f2 = pool.submit(_redeem)
            outcomes = [f1.result(timeout=5.0), f2.result(timeout=5.0)]

        succeeded = [o for o in outcomes if o is not None]
        assert len(succeeded) == 1
        assert succeeded[0]["cid"] == "call-p33-1"
        # Sequential replay must also be rejected
        assert verify_call_resume_ticket(ticket_str, expected_call_id="call-p33-1") is None
    finally:
        flags.clear("low_bandwidth_voice_codec")
        flags.clear("call_drop_resume")


def test_phase33_acoustic_risk_escalation_and_offline_action_outbox_sync(tmp_db):
    from app.receptionist.risk import evaluate_call_risk
    from app.receptionist.state import registry
    from app.receptionist.store import create_call

    create_call(call_id="call-risk-33", conversation_id="conv-risk-33", locale="lg")
    state = CallState(call_id="call-risk-33", conversation_id="conv-risk-33", locale="lg")
    state.caller_affect = "frustrated"
    room = CallRoom(call_id="call-risk-33", state=state)
    registry._rooms["call-risk-33"] = room

    flags.set("emotion_aware_prosody", True)
    flags.set("offline_action_outbox", True)
    try:
        risk_res = evaluate_call_risk("call-risk-33")
        assert risk_res["level"] == "at_risk"
        assert "distress" in risk_res["signals"]

        memsvc = get_memory_service()
        u = tmp_db.upsert_user(external_id="1000666777", tenant_id="default")
        uid = u["id"]
        tmp_db.grant_consent(uid, "personalization", "2026-04")

        batch = [
            {
                "tool": "calculate_vat",
                "summary": "Computed 18% VAT on UGX 4,000,000 (UGX 720,000)",
                "slots": {"tin": "1000666777", "annual_turnover": 160000000},
            },
            {
                "slots": {"filing_period": "2026-09"},
            },
        ]
        sync_out = memsvc.resolution_ledger.sync_offline_actions(uid, batch, tenant_id="default")
        assert sync_out["ok"] is True
        assert sync_out["synced_count"] == 2
        assert sync_out["slots_merged"]["tin"] == "1000666777"
        assert sync_out["slots_merged"]["filing_period"] == "2026-09"
        active_case = memsvc.get_active_resolution_case(uid, tenant_id="default")
        assert active_case is not None
        assert active_case.source_channel == "offline_pwa"

        # Retrying the same already-applied batch should report 0 newly applied actions
        retry_out = memsvc.resolution_ledger.sync_offline_actions(uid, batch, tenant_id="default")
        assert retry_out["ok"] is True
        assert retry_out["synced_count"] == 0
    finally:
        registry._rooms.pop("call-risk-33", None)
        flags.clear("emotion_aware_prosody")
        flags.clear("offline_action_outbox")


def test_phase34_enterprise_governance_audit_v3_and_quality_circuit_breaker(tmp_db):
    from app.agents.prompts import governance_prompt
    from app.audit.governance_v3 import (
        append_action_audit_v3,
        classify_action_risk,
        evaluate_autonomy_gate,
        evaluate_interaction_scorecard,
        quality_circuit_breaker,
    )

    quality_circuit_breaker.reset()

    # 1. Risk-Tiered Autonomy Classification & Gate Enforcement
    assert classify_action_risk("calculate_paye") == "low"
    assert classify_action_risk("verify_tin") == "medium"
    assert classify_action_risk("tin_registration_submit") == "high"
    assert classify_action_risk("mutate_tax_liability_ledger") == "critical"

    out_low, _ = evaluate_autonomy_gate("calculate_paye", intent_confidence=0.95)
    assert out_low == "autonomous_executed"

    out_med_ok, _ = evaluate_autonomy_gate("verify_tin", intent_confidence=0.88)
    assert out_med_ok == "soft_confirmed_executed"

    out_med_low_conf, _ = evaluate_autonomy_gate("verify_tin", intent_confidence=0.60)
    assert out_med_low_conf == "proposed_awaiting_officer"

    out_high_unapproved, gate_high = evaluate_autonomy_gate(
        "tin_registration_submit",
        intent_confidence=0.99,
        caller_confirmed=True,
        officer_approved=False,
    )
    assert out_high_unapproved == "proposed_awaiting_officer"
    assert gate_high.approval_required is True

    out_high_approved, gate_approved = evaluate_autonomy_gate(
        "tin_registration_submit",
        intent_confidence=0.99,
        caller_confirmed=True,
        officer_approved=True,
        approving_officer_id="officer-ura-042",
        ticket_id="TKT-901",
    )
    assert out_high_approved == "officer_approved_executed"
    assert gate_approved.officer_approval_obtained is True
    assert len(gate_approved.approving_officer_id_hash) == 32

    out_crit, _ = evaluate_autonomy_gate(
        "mutate_tax_liability_ledger",
        intent_confidence=1.0,
        caller_confirmed=True,
        officer_approved=True,
        approving_officer_id="officer-ura-042",
    )
    assert out_crit == "hard_blocked"

    # 2. Tamper-evident ActionAuditRecord v3 & DecisionReceipt
    flags.set("audit_ledger", True)
    flags.set("action_level_audit_v3", True)
    flags.set("risk_tiered_autonomy_gates", True)
    flags.set("continuous_quality_scorecard", True)
    flags.set("quality_circuit_breaker", True)
    flags.set("verifiable_decision_receipts", True)
    try:
        assert "Enterprise Safety" in governance_prompt()

        rec, receipt = append_action_audit_v3(
            session_id="conv-p34-001",
            tool_or_action_name="tin_registration_submit",
            intent_detected="tin_registration",
            intent_confidence=0.96,
            agent_role="tax_specialist",
            decision_trace=["router:tin_registration", "gate:high_risk_officer_approved"],
            parameters_redacted={"taxpayer_type": "individual", "tin": "1000***999"},
            response_summary_redacted={"status": "submitted"},
            user_id="1000555999",
            caller_confirmed=True,
            officer_approved=True,
            approving_officer_id="officer-ura-042",
            ticket_id="TKT-901",
            pre_state={"draft_step": 2},
            statutory_basis=["Tax Procedures Code Act Sec. 4 & Sec. 23 (FY2026-27)"],
        )
        assert rec.audit_event_id
        assert len(rec.row_hash) == 64
        assert len(rec.taxpayer_tin_hmac) == 64
        assert rec.autonomy_outcome == "officer_approved_executed"
        assert rec.reversibility.reversible is True
        assert "TPC Sec. 23" in receipt.reversibility_note
        assert receipt.audit_event_id == rec.audit_event_id

        # 3. 100% Continuous Quality Scorecard & Self-Healing Circuit Breaker
        sc_good = evaluate_interaction_scorecard(
            session_id="conv-p34-001",
            locale="lg",
            channel="voice_cascaded",
            reply_text="Omusolo gwa PAYE ku UGX 2,800,000 guba UGX 728,250.",
            sources=["Income Tax Act Cap 340"],
            figure_fidelity_passed=True,
            policy_adherence_passed=True,
            distress_detected=True,
            relief_cited=True,
        )
        assert sc_good.composite_quality_score == 1.0
        assert sc_good.flagged_for_human_review is False
        assert quality_circuit_breaker.is_throttled() is False

        # Trigger figure-drift anomaly -> circuit breaker trips and throttles Medium-risk autonomy
        sc_drift = evaluate_interaction_scorecard(
            session_id="conv-p34-002",
            locale="lg",
            channel="voice_cascaded",
            reply_text="Corrupted figure output",
            figure_fidelity_passed=False,
        )
        assert "NUMERIC_DRIFT_DETECTED" in sc_drift.anomaly_flags
        assert sc_drift.flagged_for_human_review is True
        assert sc_drift.autonomy_throttle_triggered is True
        assert quality_circuit_breaker.is_throttled() is True

        # Medium-risk tool is now automatically downgraded to officer approval while throttled
        out_med_throttled, _ = evaluate_autonomy_gate("verify_tin", intent_confidence=0.95)
        assert out_med_throttled == "proposed_awaiting_officer"

        # 4. ToolRegistry.call decision_receipt & HandoffEnvelope v3 governance roundtrip
        from app.tools import ToolRegistry

        vat_res = ToolRegistry.call("calculate_vat", {"amount": 10_000_000})
        assert vat_res.get("ok") is True
        assert "decision_receipt" in vat_res
        assert vat_res["decision_receipt"]["risk_tier"] == "low"

        env = HandoffEnvelope(
            thread_id="conv-p34-001",
            audit_event_ids=[rec.audit_event_id],
            decision_trace=["router:tin_registration", "gate:officer_approved"],
            reversibility_summary=receipt.reversibility_note,
            quality_scorecard_summary={"composite": sc_good.composite_quality_score},
        )
        restored_env = HandoffEnvelope.from_dict(env.to_dict())
        assert restored_env.audit_event_ids == [rec.audit_event_id]
        assert restored_env.decision_trace == ["router:tin_registration", "gate:officer_approved"]
        assert "TPC Sec. 23" in restored_env.reversibility_summary
        assert restored_env.quality_scorecard_summary["composite"] == 1.0
    finally:
        quality_circuit_breaker.reset()
        for f in (
            "audit_ledger",
            "action_level_audit_v3",
            "risk_tiered_autonomy_gates",
            "continuous_quality_scorecard",
            "quality_circuit_breaker",
            "verifiable_decision_receipts",
        ):
            flags.clear(f)


def test_phase34_sunbird_v2_model_defaults(monkeypatch):
    from app import llm, orpheus_tts, spark_tts_salt
    from app import speech_service as ss

    assert llm.LLM_MODEL == "Sunbird/Sunflower-Qwen3.5-9B"
    assert ss.WHISPER_SALT_MODEL == "Sunbird/SunflowerASR-51-african-languages"
    assert ss.SALT_LANGUAGE_TOKEN_IDS["lg"] == 50332
    assert ss.SALT_LANGUAGE_TOKEN_IDS["nyn"] == 50322
    assert ss.SALT_LANGUAGE_TOKEN_IDS["ach"] == 50357
    assert spark_tts_salt.SPARK_TTS_ENABLED is False
    monkeypatch.setenv("ORPHEUS_TTS_URL", "http://orpheus-tts:8100")
    assert orpheus_tts.speaker_for("lg") == "salt_lug_0001"
    assert orpheus_tts.speaker_for("sw") == "waxal_swa_0006"
    assert orpheus_tts.speaker_for("en") == "salt_eng_0001"
