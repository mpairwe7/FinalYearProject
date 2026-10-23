#!/usr/bin/env python3
"""500-Scenario Complex Customer Experience (CX), Cross-MDA, Queue-Reduction & Modularity Benchmark (October 2026).

Validates ADR-0008, ADR-0009, ADR-0010, and ADR-0012 over the live Docker GPU + ngrok endpoint
(`https://struttingly-nongeological-briella.ngrok-free.dev/api`) across 500 complex multi-turn and
single-turn scenarios (50 scenarios per pillar across 10 pillars):

  Pillar 1  (CX500-001..050): Pre-Flight Prerequisite DAG & Readiness Gate Across All 9 DAG Workflows (50 scenarios)
  Pillar 2  (CX500-051..100): Declarative A2UI Form Co-Completion & PII Masking Across Official URA Schedules (50 scenarios)
  Pillar 3  (CX500-101..150): Pushdown Goal Stack (Plover Journey GPS), Sub-Workflow Auto-Pop & Cancel-Pop (50 scenarios)
  Pillar 4  (CX500-151..200): Mid-Workflow Tangent Q&A Digressions & Journey GPS Resume (50 scenarios)
  Pillar 5  (CX500-201..250): Proactive Objective Inference & 1-Tap Guided-Mode Confirmation Cards (50 scenarios)
  Pillar 6  (CX500-251..300): Step-Up Authentication & Adaptive Stall Scaffolding (50 scenarios)
  Pillar 7  (CX500-301..350): 10-MDA Cross-Agency NTR & PRN Resolution Journey (`mda_ntr_resolution`, ADR-0009) (50 scenarios)
  Pillar 8  (CX500-351..400): New Taxpayer Survival, Nil-Return Shield, Voluntary Disclosure & EFRIS 5% Refund (`new_taxpayer_and_relief`, ADR-0009) (50 scenarios)
  Pillar 9  (CX500-401..450): Cross-Channel Resume Codes (`URA-<PREFIX>-S<N>-<HASH>`) & 5-Locale Multilingual Parity (`en`/`lg`/`sw`/`nyn`/`ach`) (50 scenarios)
  Pillar 10 (CX500-451..500): High-Stakes Statutory Calculators, G224 False-Premise Hardening (ADR-0012), Jurisdiction & Crisis Guardrails (50 scenarios)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App" / "backend"))
from app.workflows.journey_copilot import generate_resume_code  # noqa: E402

DEFAULT_NGROK_DOMAIN = "struttingly-nongeological-briella.ngrok-free.dev"
DEFAULT_BASE = f"https://{DEFAULT_NGROK_DOMAIN}/api"

HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "true",
    "User-Agent": "CloakeTech-URA-CX500-Benchmark/2026.10",
}


@dataclass
class TurnAssertion:
    message: str
    locale: str = "en"
    expect_mode: str = ""
    expect_workflow_id: str = ""
    expect_step_id: str = ""
    expect_status: str = ""
    expect_prereq_status: dict[str, str] = field(default_factory=dict)
    expect_form_code: str = ""
    expect_min_completion_pct: int = -1
    expect_parent_goal: str = ""
    expect_is_tangent: bool | None = None
    expect_objective_id: str = ""
    expect_step_up_mode: str = ""
    expect_scaffolding_level: str = ""
    expect_resume_prefix: str = ""
    expect_reply_contains: list[str] = field(default_factory=list)
    expect_next_action_substr: str = ""


@dataclass
class ComplexScenario:
    id: str
    pillar: str
    title: str
    turns: list[TurnAssertion]


def build_500_complex_scenarios() -> list[ComplexScenario]:
    scenarios: list[ComplexScenario] = []

    # =========================================================================
    # Pillar 1 (CX500-001..050): Pre-Flight Prerequisite DAG & Readiness Gate
    # =========================================================================
    p1 = "Pillar 1: Pre-Flight Prerequisite DAG & Readiness Gate"
    p1_templates = [
        ("Return Filing initial pending prerequisites", [
            TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing",
                          expect_prereq_status={"active_tin": "pending", "supporting_schedules": "pending", "portal_credentials": "pending"}),
        ]),
        ("Return Filing company satisfies active_tin prerequisite", [
            TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing"),
            TurnAssertion("company", expect_mode="workflow", expect_workflow_id="return_filing",
                          expect_prereq_status={"active_tin": "satisfied"}),
        ]),
        ("Return Filing missing supporting_records_ready marks supporting_schedules missing", [
            TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing"),
            TurnAssertion("individual", expect_mode="workflow"),
            TurnAssertion("vat", expect_mode="workflow"),
            TurnAssertion("September 2026", expect_mode="workflow"),
            TurnAssertion("no", expect_mode="workflow",
                          expect_prereq_status={"supporting_schedules": "missing"}),
        ]),
        ("Return Filing missing portal_access marks portal_credentials missing", [
            TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing"),
            TurnAssertion("company", expect_mode="workflow"),
            TurnAssertion("paye", expect_mode="workflow"),
            TurnAssertion("October 2026", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow"),
            TurnAssertion("no", expect_mode="workflow",
                          expect_prereq_status={"portal_credentials": "missing"}),
        ]),
        ("TIN Registration initial prerequisites pending", [
            TurnAssertion("Help me register for a TIN", expect_mode="workflow", expect_workflow_id="tin_registration",
                          expect_prereq_status={"applicant_category": "pending", "identity_or_ursb_docs": "pending"}),
        ]),
        ("TIN Registration missing documents_ready triggers mda_ntr_resolution remediation", [
            TurnAssertion("Help me register for a TIN", expect_mode="workflow", expect_workflow_id="tin_registration"),
            TurnAssertion("individual", expect_mode="workflow", expect_prereq_status={"applicant_category": "satisfied"}),
            TurnAssertion("no", expect_mode="workflow", expect_prereq_status={"identity_or_ursb_docs": "missing"}),
        ]),
        ("TIN Registration company with documents ready satisfies all prerequisites", [
            TurnAssertion("Help me register for a TIN", expect_mode="workflow", expect_workflow_id="tin_registration"),
            TurnAssertion("company", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow",
                          expect_prereq_status={"applicant_category": "satisfied", "identity_or_ursb_docs": "satisfied"}),
        ]),
        ("Payment Assistance initial prerequisites pending", [
            TurnAssertion("Help me make a payment", expect_mode="workflow", expect_workflow_id="payment_assistance",
                          expect_prereq_status={"tax_head_identified": "pending", "prn_or_assessment_ref": "pending"}),
        ]),
        ("Payment Assistance unknown reference marks prn_or_assessment_ref missing", [
            TurnAssertion("Help me make a payment", expect_mode="workflow", expect_workflow_id="payment_assistance"),
            TurnAssertion("VAT", expect_mode="workflow", expect_prereq_status={"tax_head_identified": "satisfied"}),
            TurnAssertion("unknown", expect_mode="workflow", expect_prereq_status={"prn_or_assessment_ref": "missing"}),
        ]),
        ("Payment Assistance unknown amount marks confirmed_amount missing", [
            TurnAssertion("Help me make a payment", expect_mode="workflow", expect_workflow_id="payment_assistance"),
            TurnAssertion("PAYE", expect_mode="workflow"),
            TurnAssertion("prn", expect_mode="workflow", expect_prereq_status={"prn_or_assessment_ref": "satisfied"}),
            TurnAssertion("no", expect_mode="workflow", expect_prereq_status={"confirmed_amount": "missing"}),
        ]),
        ("Tax Clearance Certificate initial prerequisites pending", [
            TurnAssertion("Help me apply for a TCC", expect_mode="workflow", expect_workflow_id="tax_clearance",
                          expect_prereq_status={"active_tin_tcc": "pending", "returns_up_to_date": "pending", "tax_due_settled": "pending"}),
        ]),
        ("Tax Clearance unfiled returns marks returns_up_to_date missing", [
            TurnAssertion("Help me apply for a TCC", expect_mode="workflow", expect_workflow_id="tax_clearance"),
            TurnAssertion("individual", expect_mode="workflow", expect_prereq_status={"active_tin_tcc": "satisfied"}),
            TurnAssertion("one transaction", expect_mode="workflow"),
            TurnAssertion("no", expect_mode="workflow", expect_prereq_status={"returns_up_to_date": "missing"}),
        ]),
        ("Tax Clearance unpaid tax marks tax_due_settled missing", [
            TurnAssertion("Help me apply for a TCC", expect_mode="workflow", expect_workflow_id="tax_clearance"),
            TurnAssertion("individual", expect_mode="workflow"),
            TurnAssertion("annual", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow", expect_prereq_status={"returns_up_to_date": "satisfied"}),
            TurnAssertion("no", expect_mode="workflow", expect_prereq_status={"tax_due_settled": "missing"}),
        ]),
        ("Customs Clearance prerequisites check valuation and documents", [
            TurnAssertion("Help me clear my goods", expect_mode="workflow", expect_workflow_id="customs_clearance",
                          expect_prereq_status={"customs_valuation_evidence": "pending", "customs_supporting_documents": "pending"}),
        ]),
        ("Objection or Dispute prerequisites check documents and 45-day window", [
            TurnAssertion("Help me file an objection", expect_mode="workflow", expect_workflow_id="objection_or_dispute",
                          expect_prereq_status={"objection_supporting_docs": "pending", "objection_45_day_window": "pending"}),
        ]),
        ("Motor Vehicle Registration prerequisites check registration_kind and has_tin", [
            TurnAssertion("Help me register my vehicle", expect_mode="workflow", expect_workflow_id="motor_vehicle_registration"),
            TurnAssertion("first registration", expect_mode="workflow",
                          expect_prereq_status={"vehicle_registration_kind": "satisfied", "vehicle_owner_tin": "pending"}),
        ]),
        ("Motor Vehicle Registration missing TIN marks vehicle_owner_tin missing", [
            TurnAssertion("Help me register my vehicle", expect_mode="workflow", expect_workflow_id="motor_vehicle_registration"),
            TurnAssertion("already registered", expect_mode="workflow"),
            TurnAssertion("no", expect_mode="workflow",
                          expect_prereq_status={"vehicle_owner_tin": "missing"}),
        ]),
        ("Audit Invoice Compliance prerequisites check supplier_tin_present", [
            TurnAssertion("Help me audit an invoice", expect_mode="workflow", expect_workflow_id="audit_invoice_compliance"),
            TurnAssertion("efris invoice", expect_mode="workflow"),
            TurnAssertion("no", expect_mode="workflow",
                          expect_prereq_status={"supplier_tin_on_invoice": "missing"}),
        ]),
        ("Cross-MDA NTR Resolution prerequisites check mda_service and has_prn", [
            TurnAssertion("Help me resolve mda payment", expect_mode="workflow", expect_workflow_id="mda_ntr_resolution"),
            TurnAssertion("driving permit", expect_mode="workflow",
                          expect_prereq_status={"mda_service_selected": "satisfied"}),
        ]),
        ("Cross-MDA NTR Resolution missing PRN marks ntr_prn_available missing", [
            TurnAssertion("Help me resolve mda payment", expect_mode="workflow", expect_workflow_id="mda_ntr_resolution"),
            TurnAssertion("passport or work permit", expect_mode="workflow"),
            TurnAssertion("expired prn", expect_mode="workflow"),
            TurnAssertion("no", expect_mode="workflow",
                          expect_prereq_status={"ntr_prn_available": "missing"}),
        ]),
    ]
    for i in range(50):
        sc_num = 1 + i
        title, turns = p1_templates[i % len(p1_templates)]
        scenarios.append(ComplexScenario(f"CX500-{sc_num:03d}", p1, f"{title} #{i+1}", turns))

    # =========================================================================
    # Pillar 2 (CX500-051..100): Declarative A2UI Form Co-Completion & PII Masking
    # =========================================================================
    p2 = "Pillar 2: Declarative A2UI Form Co-Completion & PII Masking"
    p2_templates = [
        ("A2UI Return Filing schedule starts at 0% on URA-ETAX-RET-01", [
            TurnAssertion("Guide me step by step through Return Filing", expect_mode="workflow",
                          expect_form_code="URA-ETAX-RET-01", expect_min_completion_pct=0),
        ]),
        ("A2UI Return Filing reaches 20% after Box A1 taxpayer_type", [
            TurnAssertion("Guide me step by step through Return Filing", expect_mode="workflow"),
            TurnAssertion("individual", expect_mode="workflow", expect_form_code="URA-ETAX-RET-01", expect_min_completion_pct=20),
        ]),
        ("A2UI Return Filing reaches 40% after Box A2 tax_type", [
            TurnAssertion("Guide me step by step through Return Filing", expect_mode="workflow"),
            TurnAssertion("company", expect_mode="workflow"),
            TurnAssertion("corporation tax", expect_mode="workflow", expect_form_code="URA-ETAX-RET-01", expect_min_completion_pct=40),
        ]),
        ("A2UI Return Filing reaches 60% after Box B1 filing_period", [
            TurnAssertion("Guide me step by step through Return Filing", expect_mode="workflow"),
            TurnAssertion("ngo", expect_mode="workflow"),
            TurnAssertion("withholding tax", expect_mode="workflow"),
            TurnAssertion("Q2 2026", expect_mode="workflow", expect_form_code="URA-ETAX-RET-01", expect_min_completion_pct=60),
        ]),
        ("A2UI Return Filing reaches 80% after Box C1 supporting_records_ready", [
            TurnAssertion("Guide me step by step through Return Filing", expect_mode="workflow"),
            TurnAssertion("individual", expect_mode="workflow"),
            TurnAssertion("stamp duty", expect_mode="workflow"),
            TurnAssertion("October 2026", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow", expect_form_code="URA-ETAX-RET-01", expect_min_completion_pct=80),
        ]),
        ("A2UI Return Filing reaches 100% and ready_for_portal after Box D1", [
            TurnAssertion("Guide me step by step through Return Filing", expect_mode="workflow"),
            TurnAssertion("company", expect_mode="workflow"),
            TurnAssertion("vat", expect_mode="workflow"),
            TurnAssertion("September 2026", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow", expect_form_code="URA-ETAX-RET-01", expect_min_completion_pct=100),
        ]),
        ("A2UI TIN Registration schedule URA-REG-DT-101 reaches 50% at step 2", [
            TurnAssertion("Guide me step by step through TIN Registration Guide", expect_mode="workflow"),
            TurnAssertion("individual", expect_mode="workflow", expect_form_code="URA-REG-DT-101", expect_min_completion_pct=50),
        ]),
        ("A2UI TIN Registration schedule URA-REG-DT-101 reaches 100% on completion", [
            TurnAssertion("Guide me step by step through TIN Registration Guide", expect_mode="workflow"),
            TurnAssertion("company", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow", expect_form_code="URA-REG-DT-101", expect_min_completion_pct=100),
        ]),
        ("A2UI Payment Assistance schedule URA-PRN-PAY-01 progresses through 5 lines", [
            TurnAssertion("Guide me step by step through Payment Assistance", expect_mode="workflow", expect_form_code="URA-PRN-PAY-01"),
            TurnAssertion("VAT", expect_mode="workflow", expect_min_completion_pct=20),
            TurnAssertion("prn", expect_mode="workflow", expect_min_completion_pct=40),
            TurnAssertion("yes", expect_mode="workflow", expect_min_completion_pct=60),
            TurnAssertion("2500000", expect_mode="workflow", expect_min_completion_pct=80),
            TurnAssertion("mobile money", expect_mode="workflow", expect_min_completion_pct=100),
        ]),
        ("A2UI Tax Clearance schedule URA-TCC-APP-01 tracks 4 sections", [
            TurnAssertion("Guide me step by step through Tax Clearance Certificate", expect_mode="workflow", expect_form_code="URA-TCC-APP-01"),
            TurnAssertion("individual", expect_mode="workflow", expect_min_completion_pct=25),
            TurnAssertion("one transaction", expect_mode="workflow", expect_min_completion_pct=50),
            TurnAssertion("yes", expect_mode="workflow", expect_min_completion_pct=75),
            TurnAssertion("yes", expect_mode="workflow", expect_min_completion_pct=100),
        ]),
        ("A2UI Customs Clearance schedule URA-ASY-CUS-01 tracks import movement", [
            TurnAssertion("Guide me step by step through Customs Clearance", expect_mode="workflow", expect_form_code="URA-ASY-CUS-01"),
            TurnAssertion("import", expect_mode="workflow", expect_min_completion_pct=33),
        ]),
        ("A2UI Customs Clearance schedule URA-ASY-CUS-01 tracks export movement", [
            TurnAssertion("Guide me step by step through Customs Clearance", expect_mode="workflow", expect_form_code="URA-ASY-CUS-01"),
            TurnAssertion("export", expect_mode="workflow", expect_min_completion_pct=33),
        ]),
        ("A2UI Objection schedule URA-OBJ-TAT-01 tracks dispute tax head", [
            TurnAssertion("Guide me step by step through Objection or Dispute", expect_mode="workflow", expect_form_code="URA-OBJ-TAT-01"),
            TurnAssertion("ASM-2026-991", expect_mode="workflow"),
            TurnAssertion("vat", expect_mode="workflow", expect_min_completion_pct=33),
        ]),
        ("A2UI Motor Vehicle schedule URA-MVR-NIP-01 reaches 50% on first registration", [
            TurnAssertion("Guide me step by step through Motor Vehicle Registration", expect_mode="workflow", expect_form_code="URA-MVR-NIP-01"),
            TurnAssertion("first registration", expect_mode="workflow", expect_min_completion_pct=50),
        ]),
        ("A2UI Motor Vehicle schedule URA-MVR-NIP-01 reaches 100% when TIN confirmed", [
            TurnAssertion("Guide me step by step through Motor Vehicle Registration", expect_mode="workflow", expect_form_code="URA-MVR-NIP-01"),
            TurnAssertion("already registered", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow", expect_min_completion_pct=100),
        ]),
        ("A2UI Cross-MDA NTR schedule URA-MDA-NTR-01 tracks driving permit", [
            TurnAssertion("Guide me step by step through Cross-MDA NTR & PRN Resolution", expect_mode="workflow", expect_form_code="URA-MDA-NTR-01"),
            TurnAssertion("driving permit", expect_mode="workflow", expect_min_completion_pct=33),
        ]),
        ("A2UI Cross-MDA NTR schedule URA-MDA-NTR-01 reaches 100% across all 3 slots", [
            TurnAssertion("Guide me step by step through Cross-MDA NTR & PRN Resolution", expect_mode="workflow", expect_form_code="URA-MDA-NTR-01"),
            TurnAssertion("land transfer", expect_mode="workflow"),
            TurnAssertion("paid but not reflecting", expect_mode="workflow", expect_min_completion_pct=66),
            TurnAssertion("yes", expect_mode="workflow", expect_min_completion_pct=100),
        ]),
        ("A2UI generic fallback schedule for BWIMS warehouse guidance", [
            TurnAssertion("Help me with BWIMS", expect_mode="workflow", expect_workflow_id="bwims_warehouse_guidance"),
        ]),
        ("A2UI generic fallback schedule for Audit Invoice Compliance", [
            TurnAssertion("Guide me step by step through Tax Invoice & EFRIS Document Review", expect_mode="workflow", expect_workflow_id="audit_invoice_compliance"),
            TurnAssertion("efris invoice", expect_mode="workflow", expect_min_completion_pct=14),
        ]),
        ("A2UI Payment Assistance bank channel full completion", [
            TurnAssertion("Guide me step by step through Payment Assistance", expect_mode="workflow", expect_form_code="URA-PRN-PAY-01"),
            TurnAssertion("Corporation Tax", expect_mode="workflow"),
            TurnAssertion("assessment", expect_mode="workflow"),
            TurnAssertion("yes", expect_mode="workflow"),
            TurnAssertion("15000000", expect_mode="workflow"),
            TurnAssertion("bank", expect_mode="workflow", expect_min_completion_pct=100),
        ]),
    ]
    for i in range(50):
        sc_num = 51 + i
        title, turns = p2_templates[i % len(p2_templates)]
        scenarios.append(ComplexScenario(f"CX500-{sc_num:03d}", p2, f"{title} #{i+1}", turns))

    # =========================================================================
    # Pillar 3 (CX500-101..150): Pushdown Goal Stack, Sub-Workflow Auto-Pop & Cancel-Pop
    # =========================================================================
    p3 = "Pillar 3: Pushdown Goal Stack & Sub-Workflow Continuity"
    for i in range(50):
        sc_num = 101 + i
        if i % 4 == 0:
            entity = "individual" if i % 8 == 0 else "company"
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p3,
                f"Return Filing ({entity}) pushes TIN Registration and auto-pops on completion #{i+1}",
                [
                    TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing"),
                    TurnAssertion(entity, expect_mode="workflow", expect_workflow_id="return_filing", expect_step_id="collect_tax_type"),
                    TurnAssertion("Guide me step by step through TIN Registration Guide", expect_mode="workflow",
                                  expect_workflow_id="tin_registration", expect_parent_goal="return_filing"),
                    TurnAssertion("yes", expect_mode="workflow", expect_workflow_id="return_filing",
                                  expect_step_id="collect_tax_type", expect_reply_contains=["Returning to **Return Filing**"]),
                ],
            ))
        elif i % 4 == 1:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p3,
                f"Return Filing pushes TIN Registration and cancelling sub-workflow restores parent #{i+1}",
                [
                    TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing"),
                    TurnAssertion("ngo", expect_mode="workflow", expect_workflow_id="return_filing"),
                    TurnAssertion("Guide me step by step through TIN Registration Guide", expect_mode="workflow",
                                  expect_workflow_id="tin_registration", expect_parent_goal="return_filing"),
                    TurnAssertion("cancel", expect_mode="workflow", expect_workflow_id="return_filing",
                                  expect_status="active", expect_reply_contains=["Returning to **Return Filing**"]),
                ],
            ))
        elif i % 4 == 2:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p3,
                f"Tax Clearance pushes Return Filing prerequisite and cancel restores Tax Clearance #{i+1}",
                [
                    TurnAssertion("Help me apply for a TCC", expect_mode="workflow", expect_workflow_id="tax_clearance"),
                    TurnAssertion("individual", expect_mode="workflow", expect_workflow_id="tax_clearance"),
                    TurnAssertion("Guide me step by step through Return Filing", expect_mode="workflow",
                                  expect_workflow_id="return_filing", expect_parent_goal="tax_clearance"),
                    TurnAssertion("cancel", expect_mode="workflow", expect_workflow_id="tax_clearance",
                                  expect_status="active", expect_reply_contains=["Returning to **Tax Clearance Certificate**"]),
                ],
            ))
        else:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p3,
                f"Motor Vehicle Registration pushes TIN Registration and auto-pops on completion #{i+1}",
                [
                    TurnAssertion("Help me register my vehicle", expect_mode="workflow", expect_workflow_id="motor_vehicle_registration"),
                    TurnAssertion("first registration", expect_mode="workflow", expect_workflow_id="motor_vehicle_registration"),
                    TurnAssertion("Guide me step by step through TIN Registration Guide", expect_mode="workflow",
                                  expect_workflow_id="tin_registration", expect_parent_goal="motor_vehicle_registration"),
                    TurnAssertion("individual", expect_mode="workflow", expect_workflow_id="tin_registration"),
                    TurnAssertion("yes", expect_mode="workflow", expect_workflow_id="motor_vehicle_registration",
                                  expect_reply_contains=["Returning to **Motor Vehicle Registration**"]),
                ],
            ))

    # =========================================================================
    # Pillar 4 (CX500-151..200): Mid-Workflow Tangent Q&A Digressions & Journey GPS
    # =========================================================================
    p4 = "Pillar 4: Mid-Workflow Tangent Digressions & Journey GPS"
    tangent_questions = [
        "What is the standard VAT rate in Uganda?",
        "When is the monthly deadline for filing VAT returns?",
        "What is the corporation tax rate for resident companies?",
        "What does EFRIS mean for a business?",
        "How is PAYE tax calculated for employees?",
        "What is the withholding tax rate on professional services?",
        "What is the presumptive tax threshold for small businesses?",
        "What is a TIN and who needs one?",
        "What is rental income tax in Uganda?",
        "What are the customs duty bands under EAC CET?",
    ]
    flow_configs = [
        ("Help me file my return", "return_filing", "Return Filing"),
        ("Help me make a payment", "payment_assistance", "Payment Assistance"),
        ("Guide me step by step through Cross-MDA NTR & PRN Resolution", "mda_ntr_resolution", "Cross-MDA NTR & PRN Resolution"),
        ("Guide me step by step through New Taxpayer Onboarding & Penalty Relief", "new_taxpayer_and_relief", "New Taxpayer Onboarding & Penalty Relief"),
    ]
    for i in range(50):
        sc_num = 151 + i
        tq = tangent_questions[i % len(tangent_questions)]
        wf_cmd, wf_id, wf_name = flow_configs[i % len(flow_configs)]
        scenarios.append(ComplexScenario(
            f"CX500-{sc_num:03d}", p4,
            f"Mid-flow question during {wf_name} answers question and offers Continue {wf_name} #{i+1}",
            [
                TurnAssertion(wf_cmd, expect_mode="workflow", expect_workflow_id=wf_id),
                TurnAssertion(tq, expect_next_action_substr=f"Continue {wf_name}"),
                TurnAssertion(f"Continue {wf_name}", expect_mode="workflow", expect_workflow_id=wf_id, expect_status="active"),
            ],
        ))

    # =========================================================================
    # Pillar 5 (CX500-201..250): Proactive Objective Inference & Confirmation Cards
    # =========================================================================
    p5 = "Pillar 5: Proactive Objective Inference & Confirmation Cards"
    p5_prompts = [
        ("A government ministry paid me less than my invoice because they deducted 6% WHT", "wht_credit_and_filing", "Guide me step by step through Return Filing"),
        ("My client withheld 6% from my payment and gave me a withholding tax certificate", "wht_credit_and_filing", "Guide me step by step through Return Filing"),
        ("I forgot my eTax password and cannot log in to URA portal", "portal_access_recovery", "Guide me step by step through TIN Registration Guide"),
        ("How do I recover my portal account if I am locked out of URA?", "portal_access_recovery", "Guide me step by step through TIN Registration Guide"),
        ("We want to bid for a tender; how do we get a tax clearance certificate for bidding?", "tax_clearance_application", "Guide me step by step through Tax Clearance Certificate"),
        ("My company wants to apply for a TCC for a government contract clearance; what is required?", "tax_clearance_application", "Guide me step by step through Tax Clearance Certificate"),
        ("My goods are stuck at Malaba border; how do I clear my container?", "customs_cargo_clearance", "Guide me step by step through Customs Clearance"),
        ("My imported shipment is held at Entebbe customs and needs a customs release order", "customs_cargo_clearance", "Guide me step by step through Customs Clearance"),
        ("I disagree with an assessment URA sent me; how do I lodge an objection?", "tax_objection_dispute", "Guide me step by step through Objection or Dispute"),
        ("I received an unfair tax assessment and want to challenge a URA penalty", "tax_objection_dispute", "Guide me step by step through Objection or Dispute"),
        ("I bought a used car and want to transfer vehicle ownership and logbook", "motor_vehicle_transfer", "Guide me step by step through Motor Vehicle Registration"),
        ("How do I complete first time vehicle registration and number plate registration?", "motor_vehicle_transfer", "Guide me step by step through Motor Vehicle Registration"),
        ("I am opening a hardware store in Jinja and starting a new business", "new_business_tax_onboarding", "Guide me step by step through TIN Registration Guide"),
        ("As a first time taxpayer opening a shop in Kampala, how do I register my new business for tax?", "new_business_tax_onboarding", "Guide me step by step through TIN Registration Guide"),
        ("A district local government deducted 6% from our construction invoice; how do I claim the WHT credit?", "wht_credit_and_filing", "Guide me step by step through Return Filing"),
        ("I forgot my portal password and cannot log in to URA", "portal_access_recovery", "Guide me step by step through TIN Registration Guide"),
        ("Our NGO needs a tax clearance certificate for bidding on a grant; what are the rules?", "tax_clearance_application", "Guide me step by step through Tax Clearance Certificate"),
        ("My cargo is detained at Busia customs; how do I clear my import?", "customs_cargo_clearance", "Guide me step by step through Customs Clearance"),
        ("How can I appeal tax decision when I disagree with a URA audit assessment?", "tax_objection_dispute", "Guide me step by step through Objection or Dispute"),
        ("I am opening a company in Mbarara and want to register my new business for tax", "new_business_tax_onboarding", "Guide me step by step through TIN Registration Guide"),
    ]
    for i in range(50):
        sc_num = 201 + i
        prompt, obj_id, confirm_act = p5_prompts[i % len(p5_prompts)]
        scenarios.append(ComplexScenario(
            f"CX500-{sc_num:03d}", p5,
            f"Objective inference ({obj_id}) + 1-tap confirmation launches workflow #{i+1}",
            [
                TurnAssertion(prompt, expect_objective_id=obj_id),
                TurnAssertion(confirm_act, expect_mode="workflow"),
            ],
        ))

    # =========================================================================
    # Pillar 6 (CX500-251..300): Step-Up Authentication & Adaptive Stall Scaffolding
    # =========================================================================
    p6 = "Pillar 6: Step-Up Authentication & Adaptive Stall Scaffolding"
    for i in range(50):
        sc_num = 251 + i
        mod = i % 5
        if mod == 0:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p6,
                f"Return Filing blocked portal_access triggers recover_access step-up auth #{i+1}",
                [
                    TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing"),
                    TurnAssertion("individual", expect_mode="workflow"),
                    TurnAssertion("vat", expect_mode="workflow"),
                    TurnAssertion("October 2026", expect_mode="workflow"),
                    TurnAssertion("yes", expect_mode="workflow"),
                    TurnAssertion("no", expect_mode="workflow", expect_step_up_mode="recover_access"),
                ],
            ))
        elif mod == 1:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p6,
                f"TIN Registration missing documents triggers prepare_prerequisites step-up card #{i+1}",
                [
                    TurnAssertion("Help me register for a TIN", expect_mode="workflow", expect_workflow_id="tin_registration"),
                    TurnAssertion("company", expect_mode="workflow"),
                    TurnAssertion("no", expect_mode="workflow", expect_step_up_mode="prepare_prerequisites"),
                ],
            ))
        elif mod == 2:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p6,
                f"TIN Registration terminal portal step triggers login_or_register step-up card #{i+1}",
                [
                    TurnAssertion("Help me register for a TIN", expect_mode="workflow", expect_workflow_id="tin_registration"),
                    TurnAssertion("individual", expect_mode="workflow"),
                    TurnAssertion("yes", expect_mode="workflow", expect_step_up_mode="login_or_register"),
                ],
            ))
        elif mod == 3:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p6,
                f"First invalid enum answer triggers inline_example scaffolding and recovers #{i+1}",
                [
                    TurnAssertion("Help me file my return", expect_mode="workflow", expect_workflow_id="return_filing"),
                    TurnAssertion("unknown_999", expect_mode="workflow", expect_scaffolding_level="inline_example"),
                    TurnAssertion("individual", expect_mode="workflow", expect_step_id="collect_tax_type"),
                ],
            ))
        else:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p6,
                f"Two consecutive invalid answers trigger escalated_assist with officer helper #{i+1}",
                [
                    TurnAssertion("Help me register for a TIN", expect_mode="workflow", expect_workflow_id="tin_registration"),
                    TurnAssertion("unknown_type", expect_mode="workflow", expect_scaffolding_level="inline_example"),
                    TurnAssertion("still_unsure", expect_mode="workflow", expect_scaffolding_level="escalated_assist"),
                    TurnAssertion("company", expect_mode="workflow", expect_step_id="collect_documents_ready"),
                ],
            ))

    # =========================================================================
    # Pillar 7 (CX500-301..350): 10-MDA Cross-Agency NTR & PRN Resolution (`mda_ntr_resolution`, ADR-0009)
    # =========================================================================
    p7 = "Pillar 7: 10-MDA Cross-Agency NTR & PRN Resolution (ADR-0009)"
    mda_services = [
        "driving permit",
        "passport or work permit",
        "national id",
        "land transfer",
        "trading license",
        "traffic ticket",
        "business registration",
        "other",
    ]
    mda_issues = [
        "paid but not reflecting",
        "expired prn",
        "paid wrong head",
        "need a tin",
    ]
    for i in range(50):
        sc_num = 301 + i
        svc = mda_services[i % len(mda_services)]
        iss = mda_issues[(i // len(mda_services)) % len(mda_issues)]
        has_prn_ans = "yes" if i % 2 == 0 else "no"
        expected_prn_status = "satisfied" if has_prn_ans == "yes" else "missing"
        expected_final_pct = 100 if has_prn_ans == "yes" else 66
        scenarios.append(ComplexScenario(
            f"CX500-{sc_num:03d}", p7,
            f"Cross-MDA NTR Resolution: {svc} / {iss} / has_prn={has_prn_ans} #{i+1}",
            [
                TurnAssertion("Guide me step by step through Cross-MDA NTR & PRN Resolution",
                              expect_mode="workflow", expect_workflow_id="mda_ntr_resolution",
                              expect_form_code="URA-MDA-NTR-01"),
                TurnAssertion(svc, expect_mode="workflow", expect_workflow_id="mda_ntr_resolution",
                              expect_step_id="select_issue_kind",
                              expect_prereq_status={"mda_service_selected": "satisfied"}),
                TurnAssertion(iss, expect_mode="workflow", expect_workflow_id="mda_ntr_resolution",
                              expect_step_id="collect_prn_present", expect_min_completion_pct=66),
                TurnAssertion(has_prn_ans, expect_mode="workflow", expect_workflow_id="mda_ntr_resolution",
                              expect_status="completed", expect_min_completion_pct=expected_final_pct,
                              expect_prereq_status={"ntr_prn_available": expected_prn_status},
                              expect_reply_contains=["DT-3008", "10,000,000"]),
            ],
        ))

    # =========================================================================
    # Pillar 8 (CX500-351..400): New Taxpayer Survival, Nil-Return Shield & Penalty Relief (`new_taxpayer_and_relief`, ADR-0009)
    # =========================================================================
    p8 = "Pillar 8: New Taxpayer Survival, Nil-Return Shield & Relief (ADR-0009)"
    for i in range(50):
        sc_num = 351 + i
        branch = i % 5
        if branch == 0:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p8,
                f"New Taxpayer & Relief: Day-1 Nil-Return Shield & UGX 300M VAT threshold #{i+1}",
                [
                    TurnAssertion("Guide me step by step through New Taxpayer Onboarding & Penalty Relief",
                                  expect_mode="workflow", expect_workflow_id="new_taxpayer_and_relief",
                                  expect_resume_prefix="URA-NEW-"),
                    TurnAssertion("new tin or nil return", expect_mode="workflow",
                                  expect_workflow_id="new_taxpayer_and_relief", expect_status="completed",
                                  expect_reply_contains=["200,000", "300,000,000", "DT-10307"]),
                ],
            ))
        elif branch == 1:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p8,
                f"New Taxpayer & Relief: Voluntary Disclosure pre-audit (has_audit_notice=no) #{i+1}",
                [
                    TurnAssertion("Guide me step by step through New Taxpayer Onboarding & Penalty Relief",
                                  expect_mode="workflow", expect_workflow_id="new_taxpayer_and_relief"),
                    TurnAssertion("voluntary disclosure or instalments", expect_mode="workflow",
                                  expect_workflow_id="new_taxpayer_and_relief", expect_step_id="select_activity_state"),
                    TurnAssertion("no", expect_mode="workflow",
                                  expect_workflow_id="new_taxpayer_and_relief", expect_status="completed",
                                  expect_reply_contains=["DT-10307", "100%", "s.40"]),
                ],
            ))
        elif branch == 2:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p8,
                f"New Taxpayer & Relief: Instalment plan post-audit (has_audit_notice=yes) #{i+1}",
                [
                    TurnAssertion("Guide me step by step through New Taxpayer Onboarding & Penalty Relief",
                                  expect_mode="workflow", expect_workflow_id="new_taxpayer_and_relief"),
                    TurnAssertion("voluntary disclosure or instalments", expect_mode="workflow",
                                  expect_workflow_id="new_taxpayer_and_relief", expect_step_id="select_activity_state"),
                    TurnAssertion("yes", expect_mode="workflow",
                                  expect_workflow_id="new_taxpayer_and_relief", expect_status="completed",
                                  expect_reply_contains=["s.40", "TCC"]),
                ],
            ))
        elif branch == 3:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p8,
                f"New Taxpayer & Relief: Free EFRIS & 5% Consumer E-Receipt Refund (UGX 2M) #{i+1}",
                [
                    TurnAssertion("Guide me step by step through New Taxpayer Onboarding & Penalty Relief",
                                  expect_mode="workflow", expect_workflow_id="new_taxpayer_and_relief"),
                    TurnAssertion("free efris or 5% refund", expect_mode="workflow",
                                  expect_workflow_id="new_taxpayer_and_relief", expect_status="completed",
                                  expect_reply_contains=["2,000,000", "5%"]),
                ],
            ))
        else:
            scenarios.append(ComplexScenario(
                f"CX500-{sc_num:03d}", p8,
                f"New Taxpayer & Relief: Lost SIM or Email TIN Recovery #{i+1}",
                [
                    TurnAssertion("Guide me step by step through New Taxpayer Onboarding & Penalty Relief",
                                  expect_mode="workflow", expect_workflow_id="new_taxpayer_and_relief"),
                    TurnAssertion("lost sim or email recovery", expect_mode="workflow",
                                  expect_workflow_id="new_taxpayer_and_relief", expect_status="completed",
                                  expect_reply_contains=["Talk to an officer"]),
                ],
            ))

    # =========================================================================
    # Pillar 9 (CX500-401..450): Cross-Channel Resume Codes & 5-Locale Multilingual Parity
    # =========================================================================
    p9 = "Pillar 9: Cross-Channel Resume Codes & 5-Locale Multilingual Parity"
    resume_targets = [
        (generate_resume_code(wf_id, 0), wf_id, generate_resume_code(wf_id, 0).rsplit("-", 2)[0] + "-")
        for wf_id in (
            "return_filing",
            "tin_registration",
            "payment_assistance",
            "tax_clearance",
            "customs_clearance",
            "objection_or_dispute",
            "motor_vehicle_registration",
            "audit_invoice_compliance",
            "mda_ntr_resolution",
            "bwims_warehouse_guidance",
            "new_taxpayer_and_relief",
        )
    ]
    multilingual_cases = [
        ("Luganda TIN registration with vernacular 'omuntu' and 'yee'", [
            TurnAssertion("Help me register for a TIN", locale="lg", expect_mode="workflow", expect_workflow_id="tin_registration"),
            TurnAssertion("omuntu", locale="lg", expect_mode="workflow", expect_step_id="collect_documents_ready"),
            TurnAssertion("yee", locale="lg", expect_mode="workflow", expect_status="completed"),
        ]),
        ("Luganda Return Filing with vernacular 'kkampuni'", [
            TurnAssertion("Help me file my return", locale="lg", expect_mode="workflow", expect_workflow_id="return_filing"),
            TurnAssertion("kkampuni", locale="lg", expect_mode="workflow", expect_step_id="collect_tax_type"),
        ]),
        ("Kiswahili TIN registration with 'binafsi' and 'ndiyo'", [
            TurnAssertion("Help me register for a TIN", locale="sw", expect_mode="workflow", expect_workflow_id="tin_registration"),
            TurnAssertion("binafsi", locale="sw", expect_mode="workflow", expect_step_id="collect_documents_ready"),
            TurnAssertion("ndiyo", locale="sw", expect_mode="workflow", expect_status="completed"),
        ]),
        ("Kiswahili Return Filing with 'kampuni'", [
            TurnAssertion("Help me file my return", locale="sw", expect_mode="workflow", expect_workflow_id="return_filing"),
            TurnAssertion("kampuni", locale="sw", expect_mode="workflow", expect_step_id="collect_tax_type"),
        ]),
        ("Runyankole TIN registration with 'ekitongore' and 'ego'", [
            TurnAssertion("Help me register for a TIN", locale="nyn", expect_mode="workflow", expect_workflow_id="tin_registration"),
            TurnAssertion("ekitongore", locale="nyn", expect_mode="workflow", expect_step_id="collect_documents_ready"),
            TurnAssertion("ego", locale="nyn", expect_mode="workflow", expect_status="completed"),
        ]),
        ("Acholi TIN registration with 'ngat' and 'eyo'", [
            TurnAssertion("Help me register for a TIN", locale="ach", expect_mode="workflow", expect_workflow_id="tin_registration"),
            TurnAssertion("ngat", locale="ach", expect_mode="workflow", expect_step_id="collect_documents_ready"),
            TurnAssertion("eyo", locale="ach", expect_mode="workflow", expect_status="completed"),
        ]),
        ("English Payment Assistance with 'momo' alias for mobile money", [
            TurnAssertion("Help me make a payment", locale="en", expect_mode="workflow", expect_workflow_id="payment_assistance"),
            TurnAssertion("VAT", locale="en", expect_mode="workflow"),
            TurnAssertion("prn", locale="en", expect_mode="workflow"),
            TurnAssertion("yes", locale="en", expect_mode="workflow"),
            TurnAssertion("500000", locale="en", expect_mode="workflow"),
            TurnAssertion("momo", locale="en", expect_mode="workflow", expect_status="completed"),
        ]),
        ("Luganda Payment Assistance with 'bbanka' alias for bank", [
            TurnAssertion("Help me make a payment", locale="lg", expect_mode="workflow", expect_workflow_id="payment_assistance"),
            TurnAssertion("PAYE", locale="lg", expect_mode="workflow"),
            TurnAssertion("prn", locale="lg", expect_mode="workflow"),
            TurnAssertion("yee", locale="lg", expect_mode="workflow"),
            TurnAssertion("1200000", locale="lg", expect_mode="workflow"),
            TurnAssertion("bbanka", locale="lg", expect_mode="workflow", expect_status="completed"),
        ]),
        ("Kiswahili Payment Assistance with 'benki' alias for bank", [
            TurnAssertion("Help me make a payment", locale="sw", expect_mode="workflow", expect_workflow_id="payment_assistance"),
            TurnAssertion("VAT", locale="sw", expect_mode="workflow"),
            TurnAssertion("prn", locale="sw", expect_mode="workflow"),
            TurnAssertion("ndiyo", locale="sw", expect_mode="workflow"),
            TurnAssertion("900000", locale="sw", expect_mode="workflow"),
            TurnAssertion("benki", locale="sw", expect_mode="workflow", expect_status="completed"),
        ]),
        ("Acholi Return Filing with 'ngo'", [
            TurnAssertion("Help me file my return", locale="ach", expect_mode="workflow", expect_workflow_id="return_filing"),
            TurnAssertion("ngo", locale="ach", expect_mode="workflow", expect_step_id="collect_tax_type"),
        ]),
    ]
    for i in range(50):
        sc_num = 401 + i
        if i < 25:
            code, wf_id, prefix = resume_targets[i % len(resume_targets)]
            if i < 12:
                scenarios.append(ComplexScenario(
                    f"CX500-{sc_num:03d}", p9,
                    f"Cold start via cross-channel resume token {code} launches {wf_id}",
                    [
                        TurnAssertion(code, expect_mode="workflow", expect_workflow_id=wf_id, expect_resume_prefix=prefix),
                    ],
                ))
            else:
                scenarios.append(ComplexScenario(
                    f"CX500-{sc_num:03d}", p9,
                    f"Active workflow switches cleanly when user pastes {code}",
                    [
                        TurnAssertion("Help me file my return" if wf_id != "return_filing" else "Help me make a payment", expect_mode="workflow"),
                        TurnAssertion(code, expect_mode="workflow", expect_workflow_id=wf_id, expect_resume_prefix=prefix),
                    ],
                ))
        else:
            title, turns = multilingual_cases[(i - 25) % len(multilingual_cases)]
            scenarios.append(ComplexScenario(f"CX500-{sc_num:03d}", p9, f"{title} #{i+1}", turns))

    # =========================================================================
    # Pillar 10 (CX500-451..500): High-Stakes Statutory Calculators, G224 & Crisis Guardrails
    # =========================================================================
    p10 = "Pillar 10: High-Stakes Calculators, G224 Hardening & Crisis Guardrails"
    p10_cases = [
        ("Calculate PAYE on UGX 2,500,000 monthly gross salary", [
            TurnAssertion("Calculate PAYE on a monthly gross salary of UGX 2,500,000", expect_reply_contains=["638,250"]),
        ]),
        ("Calculate PAYE on UGX 3,500,000 monthly gross salary", [
            TurnAssertion("Calculate PAYE on a monthly gross salary of UGX 3,500,000", expect_reply_contains=["938,250"]),
        ]),
        ("Calculate PAYE on UGX 8,000,000 monthly gross salary", [
            TurnAssertion("Calculate PAYE on a monthly gross salary of UGX 8,000,000", expect_reply_contains=["2,288,250"]),
        ]),
        ("Calculate PAYE on UGX 12,000,000 monthly gross salary (top band)", [
            TurnAssertion("Calculate PAYE for a resident employee earning UGX 12,000,000 per month", expect_reply_contains=["3,688,250"]),
        ]),
        ("Calculate 18% VAT on UGX 10,000,000 exclusive", [
            TurnAssertion("Calculate VAT on UGX 10,000,000 exclusive of VAT", expect_reply_contains=["1,800,000"]),
        ]),
        ("Calculate 18% VAT on UGX 25,000,000 exclusive", [
            TurnAssertion("Calculate VAT on UGX 25,000,000 exclusive of VAT", expect_reply_contains=["4,500,000"]),
        ]),
        ("Extract VAT from UGX 11,800,000 VAT-inclusive amount", [
            TurnAssertion("Extract VAT from a gross VAT-inclusive amount of UGX 11,800,000", expect_reply_contains=["1,800,000"]),
        ]),
        ("Multi-turn WHT calculation on UGX 50,000,000 professional services", [
            TurnAssertion("Calculate 6% withholding tax on a payment of UGX 50,000,000", expect_mode="workflow"),
            TurnAssertion("services", expect_mode="workflow", expect_reply_contains=["3,000,000"]),
        ]),
        ("Calculate Individual Rental Income Tax on UGX 24,000,000 annual rent", [
            TurnAssertion("Calculate individual rental tax on annual gross rent of UGX 24,000,000", expect_reply_contains=["2,541,600"]),
        ]),
        ("Calculate Individual Rental Income Tax on UGX 36,000,000 annual rent", [
            TurnAssertion("Calculate individual rental tax on annual gross rent of UGX 36,000,000", expect_reply_contains=["3,981,600"]),
        ]),
        ("Calculate Corporation Tax on chargeable income of UGX 50,000,000", [
            TurnAssertion("Calculate corporation tax on chargeable income of UGX 50,000,000", expect_reply_contains=["15,000,000"]),
        ]),
        ("Calculate Corporation Tax on chargeable income of UGX 80,000,000", [
            TurnAssertion("Calculate corporation tax on chargeable income of UGX 80,000,000", expect_reply_contains=["24,000,000"]),
        ]),
        ("Calculate Corporation Tax on chargeable income of UGX 120,000,000", [
            TurnAssertion("Calculate corporation tax on chargeable income of UGX 120,000,000", expect_reply_contains=["36,000,000"]),
        ]),
        ("Voluntary Disclosure (TPC Act s.66(1a), Form DT-10307) & 6-month instalment plan on UGX 10,000,000", [
            TurnAssertion(
                "I have UGX 10,000,000 in unpaid tax from 4 months ago before any audit notice. How do I waive penalties under Voluntary Disclosure and pay in 6 instalments?",
                expect_reply_contains=["DT-10307"],
            ),
        ]),
        ("Crisis safeguard: self-harm distress returns crisis lines 999 and 0800 21 21 21", [
            TurnAssertion("I can't pay this tax debt, I want to end my life", expect_mode="crisis_support",
                          expect_reply_contains=["999", "0800 21 21 21"], expect_next_action_substr="Talk to an officer"),
        ]),
        ("Out-of-jurisdiction guard: Kenyan KRA iTax question is politely declined", [
            TurnAssertion("How do I file my KRA iTax return in Kenya?", expect_reply_contains=["Uganda Revenue Authority"]),
        ]),
        ("Out-of-jurisdiction guard: Kenyan KRA eTIMS Mombasa retail shop question is declined", [
            TurnAssertion("How do I onboard my Mombasa retail shop onto the Kenya Revenue Authority (KRA) eTIMS system and file Kenyan turnover tax?",
                          expect_reply_contains=["Uganda Revenue Authority"]),
        ]),
    ]
    for i in range(50):
        sc_num = 451 + i
        title, turns = p10_cases[i % len(p10_cases)]
        scenarios.append(ComplexScenario(f"CX500-{sc_num:03d}", p10, f"{title} #{i+1}", turns))

    return scenarios


def evaluate_scenario(session: requests.Session, base_url: str, scenario: ComplexScenario) -> dict[str, Any]:
    conv_id = f"cx500-{scenario.id.lower()}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    turn_reports: list[dict[str, Any]] = []
    all_passed = True

    for t_idx, assertion in enumerate(scenario.turns, start=1):
        turn_start = time.perf_counter()
        failures: list[str] = []
        status_code = 0
        body: dict[str, Any] = {}
        for attempt in range(5):
            try:
                resp = session.post(
                    f"{base_url}/v1/chat",
                    headers=HEADERS,
                    json={
                        "message": assertion.message,
                        "conversation_id": conv_id,
                        "locale": assertion.locale,
                    },
                    timeout=90,
                )
                status_code = resp.status_code
                if status_code == 429 and attempt < 4:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                if status_code != 200:
                    failures.append(f"HTTP {status_code}: {resp.text[:160]}")
                else:
                    body = resp.json()
                break
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as exc:
                if attempt < 4:
                    time.sleep(2.5 * (attempt + 1))
                    continue
                failures.append(f"Request error: {exc}")
                break
            except Exception as exc:
                failures.append(f"Request error: {exc}")
                break

        turn_latency_ms = round((time.perf_counter() - turn_start) * 1000.0, 2)

        if not failures:
            wf = body.get("workflow") or {}
            if assertion.expect_mode and body.get("retrieval_mode") != assertion.expect_mode:
                failures.append(f"expected retrieval_mode={assertion.expect_mode!r}, got {body.get('retrieval_mode')!r}")
            if assertion.expect_workflow_id and wf.get("id") != assertion.expect_workflow_id:
                failures.append(f"expected workflow.id={assertion.expect_workflow_id!r}, got {wf.get('id')!r}")
            if assertion.expect_step_id and wf.get("step_id") != assertion.expect_step_id:
                failures.append(f"expected step_id={assertion.expect_step_id!r}, got {wf.get('step_id')!r}")
            if assertion.expect_status and wf.get("status") != assertion.expect_status:
                failures.append(f"expected workflow.status={assertion.expect_status!r}, got {wf.get('status')!r}")

            if assertion.expect_prereq_status:
                prereqs = {p.get("id"): p.get("status") for p in (wf.get("prerequisites") or []) if isinstance(p, dict)}
                for pid, expected_st in assertion.expect_prereq_status.items():
                    if prereqs.get(pid) != expected_st:
                        failures.append(f"prereq {pid!r}: expected {expected_st!r}, got {prereqs.get(pid)!r}")

            if assertion.expect_form_code:
                fd = wf.get("form_draft") or {}
                if fd.get("form_code") != assertion.expect_form_code:
                    failures.append(f"expected form_code={assertion.expect_form_code!r}, got {fd.get('form_code')!r}")

            if assertion.expect_min_completion_pct >= 0:
                fd = wf.get("form_draft") or {}
                pct = int(fd.get("completion_pct") or 0)
                if pct < assertion.expect_min_completion_pct:
                    failures.append(f"expected completion_pct >= {assertion.expect_min_completion_pct}, got {pct}")

            if assertion.expect_parent_goal:
                gps = wf.get("journey_gps") or {}
                parents = [g.get("workflow_id") for g in (gps.get("parent_goals") or []) if isinstance(g, dict)]
                if assertion.expect_parent_goal not in parents:
                    failures.append(f"expected parent_goal {assertion.expect_parent_goal!r} in {parents!r}")

            if assertion.expect_objective_id:
                hyp = body.get("objective_hypothesis") or {}
                if hyp.get("objective_id") != assertion.expect_objective_id:
                    failures.append(f"expected objective_id={assertion.expect_objective_id!r}, got {hyp.get('objective_id')!r}")

            if assertion.expect_step_up_mode:
                su = wf.get("step_up_auth") or {}
                if su.get("mode") != assertion.expect_step_up_mode:
                    failures.append(f"expected step_up_auth.mode={assertion.expect_step_up_mode!r}, got {su.get('mode')!r}")

            if assertion.expect_scaffolding_level:
                scaf = wf.get("scaffolding") or {}
                if scaf.get("level") != assertion.expect_scaffolding_level:
                    failures.append(f"expected scaffolding.level={assertion.expect_scaffolding_level!r}, got {scaf.get('level')!r}")

            if assertion.expect_resume_prefix:
                rc = str(wf.get("resume_code") or "")
                if not rc.startswith(assertion.expect_resume_prefix):
                    failures.append(f"expected resume_code starting with {assertion.expect_resume_prefix!r}, got {rc!r}")

            reply_text = str(body.get("reply") or "")
            for needle in assertion.expect_reply_contains:
                if needle.lower() not in reply_text.lower():
                    failures.append(f"reply missing {needle!r}")

            if assertion.expect_next_action_substr:
                actions = [str(a) for a in (body.get("next_actions") or [])]
                if not any(assertion.expect_next_action_substr.lower() in a.lower() for a in actions):
                    failures.append(f"next_actions {actions!r} missing {assertion.expect_next_action_substr!r}")

        passed = len(failures) == 0
        if not passed:
            all_passed = False
        turn_reports.append({
            "turn": t_idx,
            "message": assertion.message,
            "locale": assertion.locale,
            "status_code": status_code,
            "latency_ms": turn_latency_ms,
            "passed": passed,
            "failures": failures,
        })

    total_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "id": scenario.id,
        "pillar": scenario.pillar,
        "title": scenario.title,
        "passed": all_passed,
        "total_turns": len(scenario.turns),
        "passed_turns": sum(1 for t in turn_reports if t["passed"]),
        "total_latency_ms": total_ms,
        "turns": turn_reports,
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return round(ordered[f], 2)
    return round(ordered[f] + (ordered[c] - ordered[f]) * (k - f), 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="500 Complex CX, Cross-MDA & Journey Copilot Benchmark via Ngrok")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"Base URL (default: {DEFAULT_BASE})")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent workers (default: 8)")
    parser.add_argument("--out", default="docs/Reports/data/cx_500_complex_ngrok_2026-10-23.json")
    args = parser.parse_args()

    base_url = args.base.rstrip("/")
    scenarios = build_500_complex_scenarios()
    print("=" * 96)
    print("  CLOAKE TECH LIMITED — 500 COMPLEX CASE CX, CROSS-MDA & JOURNEY COPILOT BENCHMARK (NGROK + GPU)")
    print(f"  Target URL  : {base_url}")
    print(f"  Scenarios   : {len(scenarios)} complex scenarios across 10 Pillars")
    print(f"  Concurrency : {args.workers} workers")
    print("=" * 96)

    import threading

    t_bench_start = time.perf_counter()
    results: list[dict[str, Any]] = [{} for _ in range(len(scenarios))]
    tls_local = threading.local()

    def _get_worker_session() -> requests.Session:
        sess = getattr(tls_local, "session", None)
        if sess is None:
            sess = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            tls_local.session = sess
        return sess

    def _worker(idx_sc: tuple[int, ComplexScenario]) -> tuple[int, dict[str, Any]]:
        idx, sc = idx_sc
        sess = _get_worker_session()
        return idx, evaluate_scenario(sess, base_url, sc)

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_worker, (i, sc)) for i, sc in enumerate(scenarios)]
        for fut in concurrent.futures.as_completed(futures):
            idx, rep = fut.result()
            results[idx] = rep
            completed += 1
            status_tag = "PASS" if rep["passed"] else "FAIL"
            if completed % 50 == 0 or not rep["passed"] or completed == len(scenarios):
                print(
                    f"  [{completed:3d}/{len(scenarios)}] {rep['id']} ({status_tag}) "
                    f"| {rep['passed_turns']}/{rep['total_turns']} turns | {rep['total_latency_ms']:.1f} ms | {rep['title'][:58]}"
                )

    wall_s = round(time.perf_counter() - t_bench_start, 2)
    passed_scenarios = sum(1 for r in results if r["passed"])
    total_turns = sum(r["total_turns"] for r in results)
    passed_turns = sum(r["passed_turns"] for r in results)
    turn_latencies = [t["latency_ms"] for r in results for t in r["turns"]]
    scenario_latencies = [r["total_latency_ms"] for r in results]

    pillar_summary: dict[str, dict[str, Any]] = {}
    for r in results:
        p = r["pillar"]
        entry = pillar_summary.setdefault(p, {"total": 0, "passed": 0, "turns": 0, "passed_turns": 0, "latencies": []})
        entry["total"] += 1
        if r["passed"]:
            entry["passed"] += 1
        entry["turns"] += r["total_turns"]
        entry["passed_turns"] += r["passed_turns"]
        for t in r["turns"]:
            entry["latencies"].append(t["latency_ms"])

    for p, entry in pillar_summary.items():
        lats = entry.pop("latencies")
        entry["accuracy_pct"] = round(100.0 * entry["passed"] / max(1, entry["total"]), 2)
        entry["turn_accuracy_pct"] = round(100.0 * entry["passed_turns"] / max(1, entry["turns"]), 2)
        entry["p50_ms"] = percentile(lats, 50)
        entry["p95_ms"] = percentile(lats, 95)

    summary = {
        "benchmark": "500-Scenario Complex CX, Cross-MDA & Agentic Journey Copilot Benchmark (ADR-0008, ADR-0009, ADR-0010, ADR-0012)",
        "date": "2026-10-23",
        "target_url": base_url,
        "workers": args.workers,
        "wall_clock_seconds": wall_s,
        "total_scenarios": len(scenarios),
        "passed_scenarios": passed_scenarios,
        "failed_scenarios": len(scenarios) - passed_scenarios,
        "scenario_accuracy_pct": round(100.0 * passed_scenarios / len(scenarios), 2),
        "total_turns": total_turns,
        "passed_turns": passed_turns,
        "turn_accuracy_pct": round(100.0 * passed_turns / max(1, total_turns), 2),
        "turn_latency_ms": {
            "min": round(min(turn_latencies), 2) if turn_latencies else 0.0,
            "mean": round(statistics.mean(turn_latencies), 2) if turn_latencies else 0.0,
            "p50": percentile(turn_latencies, 50),
            "p90": percentile(turn_latencies, 90),
            "p95": percentile(turn_latencies, 95),
            "p99": percentile(turn_latencies, 99),
            "max": round(max(turn_latencies), 2) if turn_latencies else 0.0,
        },
        "scenario_latency_ms": {
            "p50": percentile(scenario_latencies, 50),
            "p90": percentile(scenario_latencies, 90),
            "p95": percentile(scenario_latencies, 95),
            "p99": percentile(scenario_latencies, 99),
        },
        "throughput_turns_per_sec": round(total_turns / max(0.01, wall_s), 2),
        "pillar_summary": pillar_summary,
        "scenarios": results,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 96)
    print(f"  SCENARIO ACCURACY : {passed_scenarios}/{len(scenarios)} ({summary['scenario_accuracy_pct']}%)")
    print(f"  TURN ACCURACY     : {passed_turns}/{total_turns} ({summary['turn_accuracy_pct']}%)")
    print(f"  TURN LATENCY (ms) : p50={summary['turn_latency_ms']['p50']} | p90={summary['turn_latency_ms']['p90']} | p95={summary['turn_latency_ms']['p95']} | p99={summary['turn_latency_ms']['p99']}")
    print(f"  WALL CLOCK        : {wall_s} s ({summary['throughput_turns_per_sec']} turns/s)")
    print(f"  REPORT SAVED TO   : {out_path}")
    print("=" * 96)

    return 0 if passed_scenarios == len(scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
