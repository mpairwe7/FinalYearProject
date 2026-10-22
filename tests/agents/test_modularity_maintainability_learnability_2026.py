"""Unit & integration tests for the 3-Phase Modularity, Maintainability & Learnability Upgrade (ADR-0012, October 2026)."""

from __future__ import annotations

import json
from pathlib import Path

from app.calculator_router import plan_rate_lookup
from app.flags import _REGISTRY, flags
from app.memory.procedural import ProceduralMemory
from app.premise_guard import check_false_premise
from app.scenario_planner import should_defer_to_scenario_planner
from app.storage_protocol import StorageBackendProtocol, verify_storage_backend_parity
from app.tools.portal_navigator_data import (
    _WORKFLOWS,
    get_portal_knowledge_provenance,
    load_portal_knowledge_pack,
)
from app.turn_pipeline import (
    CANONICAL_BEHAVIORAL_MANIFEST,
    TurnPipelineOrchestrator,
    TurnStage,
)
from evals.export_preferences import build_bilevel_preference_bundle


def test_phase1_maintainability_storage_parity_and_flag_lifecycle() -> None:
    """Verify 103/103 public function parity between database.py and postgres.py and 4-tier flag taxonomy."""
    from app import database, postgres

    parity = verify_storage_backend_parity()
    assert parity["ok"] is True
    assert parity["missing_in_postgres"] == []
    assert parity["missing_in_sqlite"] == []
    assert parity["sqlite_function_count"] >= 103
    assert isinstance(database, StorageBackendProtocol)
    assert isinstance(postgres, StorageBackendProtocol)

    # Verify flag lifecycle taxonomy
    summary = flags.lifecycle_summary()
    assert sum(len(v) for v in summary.values()) == len(_REGISTRY)
    assert "auth_required" in flags.by_category("compliance_invariant")
    assert "hyde" in flags.by_category("experimental_rag")
    desc = flags.describe("ticket_queue")
    assert desc["category"] == "compliance_invariant"


def test_phase2_learnability_exprag_dynamic_trajectories_and_bilevel_preferences(tmp_path: Path) -> None:
    """Verify ExpRAG dynamic trajectory retrieval/persistence and BPO-RAG + DICE/LoRR preference bundle."""
    pm = ProceduralMemory()
    assert len(pm.list_exemplars()) >= 12

    # 1. Retrieve MDA NTR exemplar via ExpRAG IDF + tool-sequence scoring
    hits = pm.retrieve_exemplars(
        "I paid my driving permit PRN on Mobile Money but UDLS still shows unpaid",
        top_k=2,
        planned_tools=["resolve_mda_ntr_payment"],
    )
    assert hits
    assert hits[0].intent == "resolve_mda_ntr_payment"

    block = pm.format_experience_memory_block(
        "How do I waive penalties under Voluntary Disclosure on Form DT-10307?",
        top_k=2,
        planned_tools=["simulate_tax_relief_and_instalments"],
    )
    assert "Procedural Experience Trajectories (ExpRAG" in block
    assert "simulate_tax_relief_and_instalments" in block

    # 2. Record a new verified trajectory dynamically and round-trip via JSONL
    promoted = pm.record_verified_trajectory(
        intent="custom_b2g_deemed_vat",
        domain="customs",
        user_query="How is 18% Deemed VAT handled on a donor-funded Ministry road contract of UGX 200,000,000?",
        plan=["Identify aid-funded contract", "Run evaluate_b2g_and_uesw_clearance", "Explain VAT Act s.24(7)"],
        tool_sequence=["evaluate_b2g_and_uesw_clearance"],
        faithfulness_score=0.96,
        demonstration_turn={"legal_basis": "VAT Act s.24(7)"},
    )
    assert promoted is not None
    assert promoted.exemplar_id.startswith("ex-dyn-")

    # Reject low-faithfulness trajectory (< 0.85)
    rejected = pm.record_verified_trajectory(
        intent="hallucinated_turn",
        domain="domestic_tax",
        user_query="Some query",
        plan=["Step 1"],
        tool_sequence=["calculate_vat"],
        faithfulness_score=0.40,
    )
    assert rejected is None

    jsonl_file = tmp_path / "trajectories.jsonl"
    exported_count = pm.export_trajectories_jsonl(jsonl_file)
    assert exported_count >= 13

    pm2 = ProceduralMemory()
    loaded_count = pm2.load_trajectories_jsonl(jsonl_file)
    assert loaded_count == exported_count
    assert pm2.find_exemplar("custom_b2g_deemed_vat") is not None

    # 3. Verify Bi-Level Preference Optimization (BPO-RAG + DICE/LoRR length-debiased replay)
    raw_pairs = [
        {
            "prompt": "How do I waive penalties on UGX 10,000,000 omitted tax?",
            "chosen": "Submit Form DT-10307 under Section 66(1a) of the Tax Procedures Code Act (Cap. 343) before an audit notice for a 100% waiver on UGX 10,000,000.",
            "rejected": "You must pay all penalties at the URA office.",
            "source": "officer_copilot_edit",
        },
        {
            "prompt": "Length-exploited noisy pair",
            "chosen": "A" * 1000,
            "rejected": "Short reply.",
            "source": "officer_reply",
        },
    ]
    bundle = build_bilevel_preference_bundle(raw_pairs, replay_ratio=0.5, max_length_ratio=5.0)
    assert bundle["total_raw_pairs"] == 2
    assert bundle["length_filtered_count"] == 1
    assert len(bundle["retrieval_and_router_preferences"]) == 1
    signals = bundle["retrieval_and_router_preferences"][0]["missing_statutory_signals"]
    assert any("DT-10307" in s for s in signals)
    assert any("100%" in s for s in signals)
    # 1 valid on-policy pair + 1 golden statutory replay anchor (gamma=0.5)
    assert len(bundle["dpo_length_debiased_replay"]) >= 2
    assert any(p["source"] == "golden_statutory_replay_anchor" for p in bundle["dpo_length_debiased_replay"])


def test_phase3_modularity_turn_pipeline_portal_pack_and_g224_fixes() -> None:
    """Verify Composable Turn Pipeline (AgentSLA + OWASP ASI10), declarative portal pack, and G224 fixes."""
    # 1. Composable Turn Pipeline & ASI10 behavioral attestation (including short-circuit guard enforcement)
    orch = TurnPipelineOrchestrator()
    orch.register_stage(TurnStage.STAGE_0_CONTEXT_AND_LOCALE, lambda s: {"locale": "en"})
    orch.register_stage(TurnStage.STAGE_1_INPUT_GUARD, lambda s: {"input_safe": True})
    orch.register_stage(
        TurnStage.STAGE_2_WORKFLOW_AND_FAST_PATH,
        lambda s: {"route": "fast_path_calculator", "_short_circuit": True, "reply": "UGX 180,000"},
    )
    orch.register_stage(TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION, lambda s: {"reply": "Should be skipped"})
    orch.register_stage(TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT, lambda s: {"audited": True})

    final_state, trace = orch.run({"query": "VAT on 1,000,000"}, transport="rest")
    assert final_state["reply"] == "UGX 180,000"
    assert final_state["audited"] is True
    verdict = trace.verify_behavioral_integrity()
    assert verdict["ok"] is True
    assert verdict["monotonic_stage_order"] is True
    assert verdict["missing_mandatory_guards"] == []
    assert verdict["manifest_sha256"] == CANONICAL_BEHAVIORAL_MANIFEST.digest_sha256()

    # 1b. Fail-closed stage exception containment: Stage 6 (OutputGuard + Audit) still executes on mid-pipeline failure
    orch_err = TurnPipelineOrchestrator()
    orch_err.register_stage(TurnStage.STAGE_1_INPUT_GUARD, lambda s: {"input_safe": True})

    def _failing_stage(s: dict) -> dict:
        raise RuntimeError("transient retriever timeout")

    orch_err.register_stage(TurnStage.STAGE_4_HYBRID_RETRIEVAL_AND_WEB, _failing_stage)
    orch_err.register_stage(TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT, lambda s: {"fail_closed_audited": True})
    err_state, err_trace = orch_err.run({"query": "test"}, transport="sse", raise_on_error=False)
    assert err_state["fail_closed_audited"] is True
    assert "transient retriever timeout" in err_state["_stage_error"]
    assert err_trace.verify_behavioral_integrity()["ok"] is True

    # 2. Declarative Portal Knowledge Pack (MOD-GAP-03) & NavigateExternalPortalTool integration
    prov = get_portal_knowledge_provenance()
    assert prov["loaded"] is True
    assert len(prov["sha256"]) == 64
    assert "mda_ntr_prn_reconciliation" in _WORKFLOWS
    assert "voluntary_disclosure_and_instalments" in _WORKFLOWS
    pack_meta = load_portal_knowledge_pack()
    assert pack_meta["workflow_count"] >= 2
    assert pack_meta["portal_count"] >= 5

    from app.tools import ToolRegistry

    nav_res = ToolRegistry.call(
        "navigate_external_portal",
        {
            "extracted_text": "Paid PRN on Mobile Money but UDLS portal still shows unpaid",
            "portal_name": "prn_payments",
            "workflow_intent": "mda_ntr_prn_reconciliation",
        },
    )
    assert nav_res["ok"] is True
    assert nav_res["screen_diagnostic"]["detected_state"] == "mda_ntr_prn_reconciliation"
    assert any("DT-3008" in step for step in nav_res["navigation_guidance"]["steps"])

    # 2b. MemoryService integration & architecture_integrity_status in production_readiness
    from app.memory.service import MemoryService
    from app.production_readiness import architecture_integrity_status, readiness_report

    memsvc = MemoryService()
    exp_block = memsvc.retrieve_procedural_trajectories(
        "How do I pay my driving permit PRN on UDLS?",
        top_k=2,
        planned_tools=["resolve_mda_ntr_payment"],
    )
    assert "resolve_mda_ntr_payment" in exp_block

    arch = architecture_integrity_status()
    assert arch["ok"] is True
    assert arch["storage_backend_parity"]["signature_mismatches"] == []
    assert "architecture_integrity" in readiness_report()

    # 3. Unified router -> scenario_planner deferral (MOD-GAP-02)
    assert should_defer_to_scenario_planner(
        "I paid for my driving permit PRN on Mobile Money, but UDLS says unpaid."
    ) is True
    assert should_defer_to_scenario_planner(
        "Under the 2026 VAT amendments in Uganda, how does the 5% tax refund for final consumers who demand EFRIS e-receipts work?"
    ) is True

    # 4. G224 held-out false-premise & rate-lookup verification across all 105 complex scenarios
    for split in ("dev.jsonl", "heldout.jsonl"):
        path = Path("evals/complex_scenarios") / split
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sid = row["id"]
            if "jurisdiction" in sid:
                continue
            for turn in row["turns"]:
                fp = check_false_premise(turn["message"], hits=[])
                assert fp.is_false_premise is False, f"False positive on {sid}: {fp}"

    assert plan_rate_lookup(
        "Under the 2026 VAT amendments in Uganda, how does the 5% tax refund for final consumers who demand EFRIS e-receipts work?"
    ) is None
    assert plan_rate_lookup(
        "And under what circumstances can a taxpayer apply for a waiver of interest and penalties?"
    ) is None
