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
    ):
        assert name in all_flags
        assert all_flags[name] is False

    variants = flags.logged_variants(subject="taxpayer-101")
    assert "hybrid_speech_s2s" in variants
    assert "multi_agent_dag" in variants
    assert "spoken_figure_guard" in variants


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
        assert obs_res.next_node == "retrieve"
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
