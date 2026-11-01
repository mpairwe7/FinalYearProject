"""Unit and integration tests for the 5 Cross-MDA Interdependence & Tax-Base Expansion Pillars (October 2026)."""

from __future__ import annotations

from pathlib import Path

from app.mcp import get_client, reset_client
from app.mcp.tool_rag import ToolRAGSelector
from app.memory.resolution_ledger import _WORKFLOW_DOMAINS, workflow_domain
from app.scenario_planner import build_verified_facts, plan_tools
from app.tools import ToolRegistry
from app.tools.mda_interdependence import MDA_SERVICE_CATALOG
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry


def test_pillar1_resolve_mda_ntr_payment_across_all_10_mda_services() -> None:
    """Verify resolve_mda_ntr_payment covers all 10 MDA services, split NEMA PRNs, and Form DT-3008 refunds."""
    reset_client()
    client = get_client()

    # 1. MoWT Driving Permit (UDLS) paid but not reflecting
    res_udls = client.call_tool(
        "resolve_mda_ntr_payment",
        {"service_type": "mowt_driving_permit", "prn_status": "paid_not_reflecting_at_mda"},
        user_role="public",
    )
    assert res_udls.ok is True
    assert res_udls.result["mda_code"] == "MDA1_MoWT"
    assert res_udls.result["payable_ugx"] == 130_000.0
    assert any("status 'T'" in s for s in res_udls.result["reconciliation_steps"])

    # 2. DCIC Passport paid under wrong head -> Form DT-3008 manual NTR refund steps
    res_dcic = client.call_tool(
        "resolve_mda_ntr_payment",
        {"service_type": "dcic_passport", "prn_status": "paid_wrong_head"},
        user_role="public",
    )
    assert res_dcic.ok is True
    assert res_dcic.result["mda_code"] == "MDA2_DCIC"
    assert res_dcic.result["payable_ugx"] == 250_000.0
    assert any("DT-3008" in s for s in res_dcic.result["reconciliation_steps"])

    # 3. MLHUD Land Transfer Stamp Duty (1% per FY2026-27.json stamp_duty_property_transfer, matching calculate_stamp_duty)
    res_land = client.call_tool(
        "resolve_mda_ntr_payment",
        {
            "service_type": "mlhud_land_transfer",
            "transaction_value_ugx": 50_000_000.0,
            "prn_status": "unpaid_registered",
        },
        user_role="public",
    )
    assert res_land.ok is True
    assert res_land.result["mda_code"] == "MDA6_MLHUD"
    assert res_land.result["payable_ugx"] == 500_000.0

    # 4. NEMA EIA 30% Submission & 70% Approval split PRNs
    res_nema = client.call_tool(
        "resolve_mda_ntr_payment",
        {
            "service_type": "nema_eia_prn",
            "transaction_value_ugx": 10_000_000.0,
            "prn_status": "unpaid_registered",
        },
        user_role="public",
    )
    assert res_nema.ok is True
    assert res_nema.result["mda_code"] == "MDA10_NDA_NEMA_UESW"
    assert res_nema.result["split_prns"] == {
        "submission_prn_30_pct_ugx": 3_000_000.0,
        "approval_prn_70_pct_ugx": 7_000_000.0,
    }

    # Ensure every service in MDA_SERVICE_CATALOG executes cleanly
    for srv_key in MDA_SERVICE_CATALOG:
        out = client.call_tool(
            "resolve_mda_ntr_payment",
            {"service_type": srv_key, "transaction_value_ugx": 20_000_000.0},
            user_role="public",
        )
        assert out.ok is True, srv_key


def test_pillar2_life_event_tax_base_onboarding_and_nira_nin_preflight() -> None:
    """Verify life-event tax onboarding (0% presumptive tax disclosure under UGX 10M) and NIRA NIN preflight."""
    reset_client()
    client = get_client()

    # 1. Informal trader applying for KCCA / IRAS Trading License with UGX 8,000,000 annual turnover -> 0 UGX tax!
    res_kcca_nil = client.call_tool(
        "assess_life_event_tax_onboarding",
        {"life_event": "kcca_iras_trading_license", "annual_turnover_ugx": 8_000_000.0},
        user_role="public",
    )
    assert res_kcca_nil.ok is True
    assert res_kcca_nil.result["tpc_s135_tin_mandatory"] is True
    assert res_kcca_nil.result["estimated_annual_tax_ugx"] == 0.0
    assert "0%" in res_kcca_nil.result["explanation"]

    # 2. Small trader with UGX 20,000,000 turnover -> 0.4% of excess over 10M = UGX 40,000/year with records (or UGX 80,000 without records)
    res_kcca_band = client.call_tool(
        "assess_life_event_tax_onboarding",
        {"life_event": "kcca_iras_trading_license", "annual_turnover_ugx": 20_000_000.0},
        user_role="public",
    )
    assert res_kcca_band.ok is True
    assert res_kcca_band.result["estimated_annual_tax_ugx"] == 40_000.0
    assert "80,000" in res_kcca_band.result["explanation"]

    # 2b. Trader with UGX 40,000,000 turnover -> UGX 80,000 + 0.5% of (40M - 30M) = UGX 130,000 with records (or UGX 200,000 without records)
    res_kcca_band2 = client.call_tool(
        "assess_life_event_tax_onboarding",
        {"life_event": "kcca_iras_trading_license", "annual_turnover_ugx": 40_000_000.0},
        user_role="public",
    )
    assert res_kcca_band2.ok is True
    assert res_kcca_band2.result["estimated_annual_tax_ugx"] == 130_000.0
    assert "200,000" in res_kcca_band2.result["explanation"]

    # 3. NIRA NIN-to-TIN pre-flight check detects name-order swap candidate
    res_nira = client.call_tool(
        "assess_life_event_tax_onboarding",
        {
            "life_event": "nira_nin_tin_mismatch",
            "nin": "CM900123456789",
            "stated_full_name": "Ronald Mukasa",
        },
        user_role="public",
    )
    assert res_nira.ok is True
    assert res_nira.result["nira_preflight"]["nin_format_valid"] is True
    assert "Surname='Mukasa'" in res_nira.result["nira_preflight"]["reORDERED_candidate"]


def test_pillar3_b2g_supplier_tcc_ifms_wht_and_uesw_customs_holds() -> None:
    """Verify PPDA e-GP TCC/WHT, Deemed VAT, and UNBS PVoC 15% surcharge calculations."""
    reset_client()
    client = get_client()

    # 1. PPDA e-GP / MoFPED IFMS contract of UGX 50,000,000 -> 6% WHT = UGX 3,000,000
    res_b2g = client.call_tool(
        "evaluate_b2g_and_uesw_clearance",
        {
            "clearance_domain": "ppda_egp_tcc_and_ifms_wht",
            "amount_ugx": 50_000_000.0,
            "has_valid_certificate": False,
        },
        user_role="public",
    )
    assert res_b2g.ok is True
    assert res_b2g.result["wht_or_surcharge_ugx"] == 3_000_000.0
    assert "EFRIS" in res_b2g.result["explanation"]

    # With WHT Exemption Certificate -> 0 UGX WHT
    res_b2g_exempt = client.call_tool(
        "evaluate_b2g_and_uesw_clearance",
        {
            "clearance_domain": "ppda_egp_tcc_and_ifms_wht",
            "amount_ugx": 50_000_000.0,
            "has_valid_certificate": True,
        },
        user_role="public",
    )
    assert res_b2g_exempt.ok is True
    assert res_b2g_exempt.result["wht_or_surcharge_ugx"] == 0.0

    # 2. Aid-funded project Deemed VAT on UGX 100,000,000 supply -> UGX 18,000,000 deemed VAT
    res_deemed = client.call_tool(
        "evaluate_b2g_and_uesw_clearance",
        {"clearance_domain": "aid_funded_deemed_vat", "amount_ugx": 100_000_000.0},
        user_role="public",
    )
    assert res_deemed.ok is True
    assert res_deemed.result["wht_or_surcharge_ugx"] == 18_000_000.0

    # 3. UNBS PVoC hold on CIF UGX 40,000,000 (FOB USD 5,000 > USD 2,000) without CoC -> 15% = UGX 6,000,000
    res_unbs = client.call_tool(
        "evaluate_b2g_and_uesw_clearance",
        {
            "clearance_domain": "unbs_pvoc_import_hold",
            "amount_ugx": 40_000_000.0,
            "fob_value_usd": 5_000.0,
            "has_valid_certificate": False,
        },
        user_role="public",
    )
    assert res_unbs.ok is True
    assert res_unbs.result["wht_or_surcharge_ugx"] == 6_000_000.0


def test_pillar4_scenario_planner_and_5_locale_tool_rag_integration() -> None:
    """Verify scenario_planner plans and renders MDA interdependence tools and ToolRAG matches 5-locale terms."""
    # 1. Driving permit PRN not reflecting at UDLS
    q_udls = "I paid for my driving permit PRN on Mobile Money, but UDLS says PRN not paid."
    specs_udls = plan_tools(q_udls)
    assert any(s.tool == "resolve_mda_ntr_payment" for s in specs_udls)
    block_udls = build_verified_facts(q_udls)
    assert "MDA1_MoWT" in block_udls
    assert "130,000" in block_udls

    # 2. KCCA / IRAS trading license for small shop with UGX 7,500,000 turnover
    q_kcca = "I want to renew my KCCA e-Citie trading license for my shop with annual turnover of UGX 7,500,000."
    block_kcca = build_verified_facts(q_kcca)
    assert "assess_life_event_tax_onboarding" in block_kcca
    assert "0%" in block_kcca

    # 3. PPDA e-GP government tender of UGX 25,000,000
    q_ppda = "We won a PPDA e-GP government contract worth UGX 25,000,000; what TCC, EFRIS, and WHT rules apply on IFMS?"
    block_ppda = build_verified_facts(q_ppda)
    assert "evaluate_b2g_and_uesw_clearance" in block_ppda
    assert "1,500,000" in block_ppda

    # 4. 5-locale ToolRAGSelector matches vernacular NTR/MDA words
    selector = ToolRAGSelector()
    eligible_names = ToolRegistry.names()
    for query in (
        "okusasula pamiti ne paasipooti ku URA",  # Luganda
        "kulipa leseni ya biashara na kitambulisho",  # Swahili
        "okushashura ekyapa n'endagamuntu",  # Runyankole
        "culu waraga me biacara",  # Acholi
    ):
        sel = selector.select(query, eligible_names, k=10)
        assert any(
            t in sel.tool_names
            for t in (
                "resolve_mda_ntr_payment",
                "assess_life_event_tax_onboarding",
                "evaluate_b2g_and_uesw_clearance",
            )
        ), (query, sel.tool_names)


def test_pillar5_guided_mda_ntr_resolution_workflow_and_ledger_domain() -> None:
    """Verify mda_ntr_resolution.yaml loads, advances cleanly, and maps to the payment resolution domain."""
    flow_path = Path("App/backend/app/workflows/flows/mda_ntr_resolution.yaml")
    assert flow_path.is_file()
    wf = load_workflow(flow_path)
    WorkflowRegistry.register(wf)

    assert "mda_ntr_resolution" in _WORKFLOW_DOMAINS
    assert workflow_domain("mda_ntr_resolution") == "payment"

    session = WorkflowRegistry.create_session("mda_ntr_resolution")
    t1 = WorkflowRegistry.advance(session, "")
    assert "driving permit" in t1.question.lower()
    t2 = WorkflowRegistry.advance(session, "driving permit")
    assert "paid but not reflecting" in t2.question.lower()
    t3 = WorkflowRegistry.advance(session, "paid but not reflecting")
    assert "13-digit" in t3.question.lower()
    t4 = WorkflowRegistry.advance(session, "yes")
    assert t4.is_complete is True
    assert "DT-3008" in t4.question


def test_calculator_router_bypass_and_full_10_mda_planner_coverage_and_latency() -> None:
    """Verify calculator_router does not hijack B2G/MDA queries and all 10 MDA services route in scenario_planner."""
    import os
    import time

    from app.calculator_router import plan_calculation
    from app.corpus_sync import _NON_REPLACING_FAMILIES
    from app.web_research import infer_doc_family

    # 1. calculator_router must return None on B2G / Deemed VAT / UNBS / NEMA queries carrying amounts
    for bypass_q in (
        "How much WHT applies on a PPDA e-GP government contract of UGX 25,000,000 on IFMS?",
        "What is the VAT on a UGX 100,000,000 aid-funded government project under Deemed VAT?",
        "How much customs duty and UNBS PVoC surcharge applies on CIF UGX 40,000,000?",
        "How much NEMA EIA fee PRN do I generate on UGX 10,000,000?",
    ):
        assert plan_calculation(bypass_q) is None, bypass_q

    # 2. UNBS PVoC USD FOB extraction: USD 1,500 (<= USD 2,000 exempt -> UGX 0) vs USD 5,000 (> USD 2,000 -> 15% surcharge)
    block_exempt = build_verified_facts(
        "My import shipment has FOB USD 1,500 (CIF UGX 6,000,000); is it subject to UNBS PVoC surcharge?"
    )
    assert "Computed WHT / Surcharge / Deemed Tax = UGX 0" in block_exempt

    block_surcharge = build_verified_facts(
        "My import shipment has FOB USD 5,000 (CIF UGX 40,000,000) without a CoC; what UNBS PVoC surcharge applies?"
    )
    assert "6,000,000" in block_surcharge

    # 3. Verify web_research doc_family classification and non-replacing family safeguard for MDA / B2G / UESW
    from app.web_research import can_persist_to_qdrant, classify_source_authority

    assert infer_doc_family("UDLS driving permit and DCIC e-passport NTR fees") == "mda_ntr_schedule"
    assert infer_doc_family("NITA-U PDPO data protection and privacy registration fee") == "mda_ntr_schedule"
    assert infer_doc_family("PPDA e-GP and IFMS deemed VAT compliance") == "b2g_ppda_ifms_compliance"
    assert infer_doc_family("UNBS PVoC and NDAMIS single window UESW clearance") == "unbs_nda_uesw_clearance"
    assert {"mda_ntr_schedule", "b2g_ppda_ifms_compliance", "unbs_nda_uesw_clearance"}.issubset(_NON_REPLACING_FAMILIES)
    for nita_url in (
        "https://nita.go.ug/publications/it-certification-guidelines.pdf",
        "https://www.pdpo.go.ug/fees-schedule.pdf",
        "https://egpuganda.go.ug/supplier-guide.pdf",
    ):
        auth = classify_source_authority(nita_url)
        assert auth.tier == 1 and auth.trust == 1.0
        assert can_persist_to_qdrant(nita_url) is True
    block_nita_ntr = build_verified_facts(
        "I paid the UGX 100,000 PDPO data protection registration fee PRN, how does it reflect?"
    )
    assert "MDA11_NITA_U" in block_nita_ntr
    assert "s.29" in block_nita_ntr
    assert "25,000" in block_nita_ntr

    from app.web_research import build_search_queries, detect_research_triggers

    nita_trigs = detect_research_triggers(
        "What is the latest NITA-U PDPO data protection registration fee schedule?",
        hits=[],
    )
    nita_specs = build_search_queries(
        "What is the latest NITA-U PDPO data protection registration fee schedule?",
        nita_trigs,
    )
    assert any("pdpo.go.ug" in s.target_domains and "nita.go.ug" in s.target_domains for s in nita_specs)

    # 4. Verify planner sets missing_inputs when amount-dependent MDA/relief prompts omit figures
    specs_missing_relief = plan_tools("How do I apply for Voluntary Disclosure on Form DT-10307?")
    assert any(
        s.tool == "simulate_tax_relief_and_instalments" and s.missing_inputs
        for s in specs_missing_relief
    )
    block_missing_relief = build_verified_facts("How do I apply for Voluntary Disclosure on Form DT-10307?")
    assert "unpaid or undeclared principal tax amount (UGX)" in block_missing_relief

    # 5. Functional & latency verification across all 10 MDA prompts
    prompts = [
        "I paid for my driving permit PRN on Mobile Money, but UDLS says unpaid.",
        "I am transferring ownership of my motor vehicle; what URA stamp duty and EPS rules apply?",
        "I paid UGX 250,000 for my DCIC e-passport under the wrong tax head and need a refund.",
        "How much is a DCIC work permit NTR PRN on visas.immigration.go.ug?",
        "How much is the NIRA National ID replacement fee PRN?",
        "My Instant TIN application was rejected due to a NIRA NIN surname mismatch for CM900123456789.",
        "What stamp duty PRN applies on a MLHUD UgNLIS land transfer valued at UGX 50,000,000 by the CGV?",
        "I want to renew my KCCA e-Citie trading license on IRAS with annual turnover of UGX 8,500,000.",
        "How do I pay a Uganda Police Force EPS traffic ticket PRN before the 28-day surcharge?",
        "How do I generate the 30% EIA submission and 70% approval PRNs for NEMA on UGX 10,000,000?",
    ]
    latencies_ms: list[float] = []
    for _ in range(20):
        for p in prompts:
            t0 = time.perf_counter()
            out = build_verified_facts(p)
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            assert "Verified Statutory Facts & Calculations" in out, p

    latencies_ms.sort()
    p95_ms = latencies_ms[int(0.95 * len(latencies_ms))]
    max_p95_ms = 5.0 if os.environ.get("RUN_PERF_BENCHMARK") == "1" else 250.0
    assert p95_ms < max_p95_ms, f"Expected p95 < {max_p95_ms} ms, got {p95_ms:.3f} ms"

    # 5. False-premise guard must never reject legitimate MDA/B2G/UESW statutory terms when hits=[]
    from app.premise_guard import check_false_premise

    for valid_q in (
        "What is the UNBS PVoC surcharge in Uganda?",
        "How does the PVoC evasion surcharge work?",
        "How is Deemed VAT handled on aid-funded government contracts?",
        "What is the NEMA environmental levy on used motor vehicles?",
    ):
        fp = check_false_premise(valid_q, hits=[])
        assert fp.is_false_premise is False, (valid_q, fp)


def test_novel_queue_reduction_and_new_taxpayer_onboarding_tools() -> None:
    """Verify build_new_taxpayer_compliance_shield, simulate_tax_relief_and_instalments, and resolve_efris_and_account_recovery."""
    reset_client()
    client = get_client()

    # 1. New company with zero sales for 3 unfiled months -> Nil-Return Shield warns of UGX 600,000 exposure
    res_nil = client.call_tool(
        "build_new_taxpayer_compliance_shield",
        {"taxpayer_profile": "new_company_zero_sales", "unfiled_months": 3},
        user_role="public",
    )
    assert res_nil.ok is True
    assert res_nil.result["monthly_return_required"] is True
    assert res_nil.result["nil_return_required_when_zero_sales"] is True
    assert res_nil.result["non_filing_penalty_exposure_ugx"] == 600_000.0
    assert "Total late-filing penalty exposure = UGX 600,000 (UGX 200,000 per unfiled return)" in res_nil.result["explanation"]
    assert res_nil.result["vat_registration_threshold_ugx"] == 300_000_000.0

    # 2. Salaried-only employee -> no monthly return required
    res_sal = client.call_tool(
        "build_new_taxpayer_compliance_shield",
        {"taxpayer_profile": "salaried_employee_only"},
        user_role="public",
    )
    assert res_sal.ok is True
    assert res_sal.result["monthly_return_required"] is False
    assert res_sal.result["non_filing_penalty_exposure_ugx"] == 0.0

    # 3. VAT-registered business at UGX 200,000,000 turnover -> qualifies for FY2026-27 VAT deregistration (< 300M)
    res_vat_dg = client.call_tool(
        "build_new_taxpayer_compliance_shield",
        {"taxpayer_profile": "vat_registered_150m_to_300m", "annual_turnover_ugx": 200_000_000.0},
        user_role="public",
    )
    assert res_vat_dg.ok is True
    assert "300,000,000" in res_vat_dg.result["explanation"]

    # 4. Voluntary Disclosure (TPC Act s.66(1a)) on UGX 10,000,000 principal 4 months late -> saves 100% penalty + interest
    res_vd = client.call_tool(
        "simulate_tax_relief_and_instalments",
        {
            "principal_tax_ugx": 10_000_000.0,
            "months_late": 4,
            "voluntary_before_audit_notice": True,
            "instalment_months": 6,
        },
        user_role="public",
    )
    assert res_vd.ok is True
    assert res_vd.result["standard_penal_tax_ugx"] == 800_000.0
    assert res_vd.result["standard_interest_ugx"] == 800_000.0
    assert res_vd.result["total_without_relief_ugx"] == 11_600_000.0
    assert res_vd.result["voluntary_disclosure_payable_ugx"] == 10_000_000.0
    assert res_vd.result["penalty_and_interest_saved_ugx"] == 1_600_000.0
    assert res_vd.result["instalment_plan"]["initial_down_payment_25pct_ugx"] == 2_500_000.0
    assert res_vd.result["instalment_plan"]["monthly_instalment_ugx"] == 1_250_000.0
    assert res_vd.result["instalment_plan"]["unlocks_tcc_upon_down_payment"] is True

    # 4b. Post-audit notice (voluntary_before_audit_notice=False) -> no waiver claimed in explanation
    res_vd_after = client.call_tool(
        "simulate_tax_relief_and_instalments",
        {
            "principal_tax_ugx": 10_000_000.0,
            "months_late": 4,
            "voluntary_before_audit_notice": False,
            "instalment_months": 6,
        },
        user_role="public",
    )
    assert res_vd_after.ok is True
    assert res_vd_after.result["penalty_and_interest_saved_ugx"] == 0.0
    assert "not available after a URA audit notice" in res_vd_after.result["explanation"]
    assert "100% of penal tax and interest is WAIVED" not in res_vd_after.result["explanation"]

    # 4c. Life-event land transfer uses 1.0% stamp duty from FY2026-27 table
    res_land = client.call_tool(
        "assess_life_event_tax_onboarding",
        {"life_event": "land_or_property_transfer"},
        user_role="public",
    )
    assert res_land.ok is True
    assert "1.0%" in res_land.result["recommended_tax_head"]
    assert "1.5%" not in res_land.result["explanation"]

    # 5. EFRIS 5% Consumer E-Receipt Refund on UGX 2,360,000 VAT-inclusive purchases (>= UGX 2,000,000 FY2026-27 threshold)
    # Embedded VAT = 2,360,000 * (18/118) = 360,000; 5% refund = 18,000 UGX
    res_efris = client.call_tool(
        "resolve_efris_and_account_recovery",
        {
            "scenario": "efris_free_channel_and_5pct_refund",
            "thirty_day_ereceipt_purchases_ugx": 2_360_000.0,
        },
        user_role="public",
    )
    assert res_efris.ok is True
    assert res_efris.result["qualifies_for_5pct_ereceipt_refund"] is True
    assert res_efris.result["ereceipt_qualifying_threshold_ugx"] == 2_000_000.0
    assert res_efris.result["estimated_vat_refund_ugx"] == 18_000.0
    assert res_efris.result["efris_min_penalty_ugx"] == 200_000.0

    # 6. Scenario planner end-to-end verification for Nil Return, Voluntary Disclosure, E-Receipt Refund & Lost SIM
    assert "build_new_taxpayer_compliance_shield" in build_verified_facts(
        "I registered a new company 2 months ago and had zero sales; do I need to file a nil return?"
    )
    assert "1,600,000" in build_verified_facts(
        "We have UGX 10,000,000 in undeclared tax from 4 months ago; how much do we save under voluntary disclosure and can we pay in instalments?"
    )
    assert "18,000" in build_verified_facts(
        "I collected UGX 2,360,000 in EFRIS e-receipts this month; how much is my 5% VAT e-receipt refund?"
    )
    assert "Lost SIM/Email Recovery" in build_verified_facts(
        "I lost my SIM card and phone number linked to my TIN OTP; how do I update my TIN phone number?"
    )

    # 7. Verify new_taxpayer_and_relief workflow loads and completes cleanly
    flow_path = Path("App/backend/app/workflows/flows/new_taxpayer_and_relief.yaml")
    wf = load_workflow(flow_path)
    WorkflowRegistry.register(wf)
    session = WorkflowRegistry.create_session("new_taxpayer_and_relief")
    s1 = WorkflowRegistry.advance(session, "")
    assert "voluntary disclosure" in s1.question.lower()
    s2 = WorkflowRegistry.advance(session, "voluntary disclosure or instalments")
    assert "audit notice" in s2.question.lower()
    s3 = WorkflowRegistry.advance(session, "no")
    assert s3.is_complete is True
    # 8. Form numbers (Form DT-10307, Form DT-3008, HS code 8703) must NOT be misparsed as UGX amounts,
    #    and custom instalment_months ("10 monthly instalments") must be respected.
    from app.calculator_router import extract_amounts

    assert extract_amounts("Under Form DT-10307, I have UGX 10,000,000 in undeclared tax")[0][0] == 10_000_000.0
    assert extract_amounts("Using Form DT-3008 for my UGX 250,000 passport PRN refund")[0][0] == 250_000.0
    assert extract_amounts("Importing under HS code 8703 with CIF UGX 40,000,000")[0][0] == 40_000_000.0

    block_dt10307 = build_verified_facts(
        "Under Form DT-10307, we have UGX 10,000,000 in undeclared tax from 4 months ago; can we pay in 10 monthly instalments?"
    )
    assert "1,600,000" in block_dt10307
    assert "750,000/month" in block_dt10307

    # 9. Threshold-vs-actual amount disambiguation, negative-amount MCP wire rejection, and conditional workflow skip
    block_thresh_vs_actual = build_verified_facts(
        "Since the EFRIS e-receipt threshold was lowered to UGX 2,000,000, if I collected UGX 4,720,000 in EFRIS e-receipts this month, what is my 5% VAT refund?"
    )
    assert "36,000" in block_thresh_vs_actual

    # Negative principal_tax_ugx rejected by JSON Schema minimum=0 at MCP boundary
    neg_call = client.call_tool(
        "simulate_tax_relief_and_instalments",
        {"principal_tax_ugx": -10_000.0},
        user_role="public",
    )
    assert neg_call.ok is False

    # Conditional `when:` step in new_taxpayer_and_relief skips audit-notice question for "lost sim or email recovery"
    session_sim = WorkflowRegistry.create_session("new_taxpayer_and_relief")
    WorkflowRegistry.advance(session_sim, "")
    done_turn = WorkflowRegistry.advance(session_sim, "lost sim or email recovery")
    assert done_turn.is_complete is True


def test_government_mda_pronunciation_normalization_and_asr_repair() -> None:
    """Verify speech pronunciation rules for all 10-MDA/government terms and TIN/NIN natural word preservation."""
    from app.query import expand_abbreviations
    from app.receptionist.lexicon import repair_asr_entities
    from app.speech_normalization import clean_text_for_speech, phoneticize_bantu_acronyms

    raw = (
        "Apply via kcca/KCCA, ursb/URSB, nema/NEMA, unbs/UNBS, nira/NIRA, dcic/DCIC, "
        "udls/UDLS, mlhud/MLHUD, upf/UPF, nda/NDA, ppda/PPDA, ifms/IFMS, obrs/OBRS, "
        "uesw/UESW, iras/IRAS, bwims/BWIMS, ntr/NTR, ontr/ONTR, mowt/MoWT, mofped/MoFPED, "
        "pvoc/PVoC, e-GP, and e-Citie using your TIN/tin and NIN/nin (or T-I-N and N-I-N)."
    )
    spoken = clean_text_for_speech(raw, locale="en")
    assert "K C C A/K C C A" in spoken
    assert "U R S B/U R S B" in spoken
    assert "Nema/Nema" in spoken
    assert "U N B S/U N B S" in spoken
    assert "Nira/Nira" in spoken
    assert "D C I C/D C I C" in spoken
    assert "U D L S/U D L S" in spoken
    assert "M L H U D/M L H U D" in spoken
    assert "U P F/U P F" in spoken
    assert "N D A/N D A" in spoken
    assert "P P D A/P P D A" in spoken
    assert "I F M S/I F M S" in spoken
    assert "O B R S/O B R S" in spoken
    assert "U E S W/U E S W" in spoken
    assert "Iras/Iras" in spoken
    assert "Bwims/Bwims" in spoken
    assert "N T R/N T R" in spoken
    assert "O N T R/O N T R" in spoken
    assert "M O W T/M O W T" in spoken
    assert "M O F P E D/M O F P E D" in spoken
    assert "P V O C/P V O C" in spoken
    assert "E G P" in spoken
    assert "E-City" in spoken
    assert "TIN/tin" in spoken
    assert "NIN/nin" in spoken
    assert "T-I-N" not in spoken
    assert "N-I-N" not in spoken

    # Bantu acronym phonetics keeps TIN and NIN as natural words (never T-I-N or N-I-N) and spells PRN as P R N
    for loc in ("lg", "sw", "nyn", "ach"):
        bantu_out = phoneticize_bantu_acronyms("TIN ne NIN ne T-I-N ne N-I-N ne PRN", locale=loc)
        assert "T-I-N" not in bantu_out
        assert "N-I-N" not in bantu_out
        assert "P-R-N" not in bantu_out
        assert "TIN" in bantu_out
        assert "NIN" in bantu_out
        assert "P R N" in bantu_out

    # Vernacular "ekitongore" / "dul" maps to "ngo", whereas generic English "organisation"
    # remains ambiguous when both "company" and "ngo" are offered.
    from app.workflows.slots import _match_enum

    assert _match_enum("ekitongore", ["individual", "company", "ngo"]) == (True, "ngo", "")
    assert _match_enum("organisation", ["individual", "company", "ngo"])[0] is False
    assert _match_enum("organisation", ["individual", "organisation"]) == (True, "organisation", "")

    assert "from URSB" in repair_asr_entities("I got a certificate from u r s b")
    assert "from URA s b" not in repair_asr_entities("I got a certificate from u r s b")

    repaired = repair_asr_entities(
        "I paid k c c a and u r s b and u n b s and n e m a and n i r a and d c i c and u d l s"
    )
    for canonical in ("KCCA", "URSB", "UNBS", "NEMA", "NIRA", "DCIC", "UDLS"):
        assert canonical in repaired

    # Query abbreviation expansion includes the government bodies while keeping the acronym token
    expanded = expand_abbreviations("kcca ursb nema unbs nira")
    assert "Kampala Capital City Authority (KCCA)" in expanded
    assert "Uganda Registration Services Bureau (URSB)" in expanded
    assert "National Environment Management Authority (NEMA)" in expanded
    assert "Uganda National Bureau of Standards (UNBS)" in expanded
    assert "National Identification and Registration Authority (NIRA)" in expanded






