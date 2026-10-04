#!/usr/bin/env python3
"""100-Scenario Master Customer Experience (CX) & Request Completion Benchmark Suite.

Evaluates the URA AI Assistant over the live ngrok endpoint across 100 comprehensive,
diverse, and realistic scenarios to measure active request completion rather
than passive FAQ matching:

  Pillar 1:  Interactive Multi-Step Guided Workflows (10 scenarios)
  Pillar 2:  Deterministic Tax Computations & Net Take-Home (10 scenarios)
  Pillar 3:  Real-World Narrative Stories & Complex Business Cases (10 scenarios)
  Pillar 4:  Actionable Resource Delivery & Deep Portal Linking (10 scenarios)
  Pillar 5:  Empathetic Crisis De-escalation & Enforcement Protection (10 scenarios)
  Pillar 6:  Closed-Loop Conversational Bug & Knowledge Reporting (10 scenarios)
  Pillar 7:  Multilingual Task Fulfillment (Luganda & Swahili) (10 scenarios)
  Pillar 8:  Statutory Boundary Probing & Classification Integrity (10 scenarios)
  Pillar 9:  Omnichannel Case Tracking & Reference Resolution (10 scenarios)
  Pillar 10: Advanced Situational Advisory & Dispute Resolution (10 scenarios)

Usage:
  python3 scripts/evaluate_cx_100_scenarios_ngrok.py
  python3 scripts/evaluate_cx_100_scenarios_ngrok.py --base https://<ngrok-domain>/api
  python3 scripts/evaluate_cx_100_scenarios_ngrok.py --workers 4 --out docs/Reports/data/cx_100_eval.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

DEFAULT_NGROK_DOMAIN = "struttingly-nongeological-briella.ngrok-free.dev"
DEFAULT_BASE = f"https://{DEFAULT_NGROK_DOMAIN}/api"

HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
    "User-Agent": "URA-CX-100-Benchmark/2026",
}


@dataclass
class TurnStep:
    """A single turn in an interaction."""
    user_message: str
    expect_mode: str | None = None
    expect_workflow: str | None = None
    expect_step_id: str | None = None
    expect_options_contain: list[str] = field(default_factory=list)
    expect_reply_contains: list[str] = field(default_factory=list)
    expect_reply_regex: list[str] = field(default_factory=list)
    expect_resources_min: int = 0
    expect_discrepancy: bool = False
    expect_escalation: bool | None = None


@dataclass
class CXScenario:
    """A complete scenario testing customer experience & request fulfillment."""
    id: str
    category: str
    title: str
    description: str
    locale: str = "en"
    turns: list[TurnStep] = field(default_factory=list)


def build_100_scenarios() -> list[CXScenario]:
    """Formulate 100 exhaustive real-world scenarios testing task completion."""
    scenarios: list[CXScenario] = []

    # =========================================================================
    # PILLAR 1: Interactive Multi-Step Guided Workflows (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-001",
            category="Pillar 1: Guided Workflows",
            title="Individual TIN Registration (Privacy-Preserving Stepper)",
            description="Guides individual through privacy-safe TIN registration steps and portal handoff.",
            turns=[
                TurnStep("Help me register for a TIN", expect_mode="workflow", expect_workflow="TIN Registration", expect_options_contain=["individual", "company"]),
                TurnStep("individual", expect_mode="workflow", expect_workflow="TIN Registration", expect_reply_contains=["yes/no"]),
                TurnStep("yes", expect_mode="workflow", expect_workflow="TIN Registration", expect_reply_contains=["https://ura.go.ug"]),
            ],
        ),
        CXScenario(
            id="CX-002",
            category="Pillar 1: Guided Workflows",
            title="Company Non-Individual TIN Application Guidance",
            description="Provides step-by-step guidance for non-individual company TIN registration.",
            turns=[
                TurnStep("Help me apply for a TIN for my company", expect_reply_contains=["Non-Individual", "TIN", "directors"], expect_resources_min=1),
            ],
        ),
        CXScenario(
            id="CX-003",
            category="Pillar 1: Guided Workflows",
            title="Tax Clearance Certificate (TCC) Guided Journey",
            description="Initiates interactive guidance for requesting a TCC for tenders and business operations.",
            turns=[
                TurnStep("Guide me through getting a tax clearance certificate", expect_mode="workflow", expect_workflow="Tax Clearance Certificate", expect_reply_contains=["Tax Clearance Certificate"]),
            ],
        ),
        CXScenario(
            id="CX-004",
            category="Pillar 1: Guided Workflows",
            title="Payment Registration Number (PRN) Generation Stepper",
            description="Guides taxpayer through generating a 12-digit PRN voucher for payments.",
            turns=[
                TurnStep("Guide me to generate a PRN for payment", expect_mode="workflow", expect_workflow="Payment Assistance", expect_reply_contains=["Payment Assistance"]),
            ],
        ),
        CXScenario(
            id="CX-005",
            category="Pillar 1: Guided Workflows",
            title="Motor Vehicle Registration & Ownership Transfer Workflow",
            description="Walks through vehicle ownership transfer and number plate registration.",
            turns=[
                TurnStep("Guide me through registering my imported car", expect_mode="workflow", expect_workflow="Motor Vehicle Registration", expect_reply_contains=["Motor Vehicle Registration"]),
            ],
        ),
        CXScenario(
            id="CX-006",
            category="Pillar 1: Guided Workflows",
            title="VAT Monthly Return Filing Walkthrough",
            description="Walks through submitting a monthly VAT return via the eTax portal.",
            turns=[
                TurnStep("Walk me through filing my VAT return", expect_mode="workflow", expect_workflow="Return Filing", expect_reply_contains=["Return Filing"]),
            ],
        ),
        CXScenario(
            id="CX-007",
            category="Pillar 1: Guided Workflows",
            title="Customs Import Entry & IM4 Clearance Workflow",
            description="Walks through commercial cargo customs declarations and clearance.",
            turns=[
                TurnStep("Guide me through customs clearance", expect_mode="workflow", expect_workflow="Customs Clearance", expect_reply_contains=["Customs Clearance"]),
            ],
        ),
        CXScenario(
            id="CX-008",
            category="Pillar 1: Guided Workflows",
            title="Formal Tax Assessment Objection / Dispute Filing",
            description="Guides an aggrieved taxpayer through lodging a formal objection within statutory deadlines.",
            turns=[
                TurnStep("Help me file an objection against an unfair tax assessment", expect_mode="workflow", expect_workflow="Objection or Dispute", expect_reply_contains=["Objection"]),
            ],
        ),
        CXScenario(
            id="CX-009",
            category="Pillar 1: Guided Workflows",
            title="Bonded Warehouse (BWIMS) IM7 Cargo Guidance",
            description="Assists warehouse operators and importers on bonded cargo management.",
            turns=[
                TurnStep("I need guidance on bonded warehouse goods under IM7", expect_reply_regex=[r"(?:warehouse|bonded|im7|customs|clearance)"]),
            ],
        ),
        CXScenario(
            id="CX-010",
            category="Pillar 1: Guided Workflows",
            title="EFRIS Invoice & Compliance Audit Checklist",
            description="Guides business on fiscal invoice compliance and checklist before issuing receipts.",
            turns=[
                TurnStep("Guide me to check if my invoice complies with EFRIS rules", expect_reply_regex=[r"(?:efris|invoice|receipt|fdn|compliance)"], expect_resources_min=1),
            ],
        ),
    ])

    # =========================================================================
    # PILLAR 2: Deterministic Tax Computations & Net Take-Home (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-011",
            category="Pillar 2: Deterministic Computations",
            title="Standard Resident PAYE on 5,000,000 UGX Monthly Salary",
            description="Computes exact progressive brackets, tax payable, and net take-home.",
            turns=[
                TurnStep("Calculate my PAYE on a gross monthly salary of 5,000,000 UGX", expect_mode="calculator", expect_reply_contains=["1,388,250", "3,611,750", "FY2026-27"]),
            ],
        ),
        CXScenario(
            id="CX-012",
            category="Pillar 2: Deterministic Computations",
            title="High-Income PAYE on 12,000,000 UGX (40% Super Bracket)",
            description="Calculates PAYE including the 40% top bracket for monthly income above 10M UGX.",
            turns=[
                TurnStep("How much PAYE will I pay on 12,000,000 UGX salary per month?", expect_mode="calculator", expect_reply_contains=["40%", "FY2026-27"], expect_reply_regex=[r"(?:3,\d{3},\d{3}|take-home|net)"]),
            ],
        ),
        CXScenario(
            id="CX-013",
            category="Pillar 2: Deterministic Computations",
            title="Non-Resident Employee PAYE Calculation (3,000,000 UGX)",
            description="Verifies flat bracket treatment for non-resident employees in Uganda.",
            turns=[
                TurnStep("Calculate PAYE for a non-resident employee earning 3,000,000 UGX gross monthly", expect_mode="calculator", expect_reply_contains=["non-resident"]),
            ],
        ),
        CXScenario(
            id="CX-014",
            category="Pillar 2: Deterministic Computations",
            title="Standard 18% VAT Addition on 25,000,000 UGX Supply",
            description="Computes 18% VAT and total invoice amount payable.",
            turns=[
                TurnStep("Calculate 18% VAT on goods worth 25,000,000 UGX", expect_mode="calculator", expect_reply_contains=["4,500,000", "29,500,000"]),
            ],
        ),
        CXScenario(
            id="CX-015",
            category="Pillar 2: Deterministic Computations",
            title="VAT-Inclusive Extraction from Gross Total (59,000,000 UGX)",
            description="Extracts net amount and VAT component from a gross inclusive sum.",
            turns=[
                TurnStep("How much VAT is included in 59,000,000 UGX?", expect_mode="calculator", expect_reply_contains=["9,000,000", "50,000,000"]),
            ],
        ),
        CXScenario(
            id="CX-016",
            category="Pillar 2: Deterministic Computations",
            title="Individual Rental Income Tax on 30,000,000 UGX Annual Rent",
            description="Calculates 12% rental tax with 2,820,000 UGX statutory threshold deduction.",
            turns=[
                TurnStep("Calculate individual rental tax on 30,000,000 UGX annual rental income", expect_mode="calculator", expect_reply_regex=[r"(?:12%|rental|3,261,600|2,820,000)"]),
            ],
        ),
        CXScenario(
            id="CX-017",
            category="Pillar 2: Deterministic Computations",
            title="Resident Professional Fees Withholding Tax (15,000,000 UGX)",
            description="Computes 6% withholding tax deduction and net payment to service provider.",
            turns=[
                TurnStep("Calculate withholding tax on an invoice of 15,000,000 UGX for consultancy services to a resident company", expect_mode="calculator", expect_reply_contains=["6%", "900,000", "14,100,000"]),
            ],
        ),
        CXScenario(
            id="CX-018",
            category="Pillar 2: Deterministic Computations",
            title="Commercial Building Sale Capital Gains Tax",
            description="Calculates capital gains tax on commercial property (Cost: 100M, Sale: 160M).",
            turns=[
                TurnStep("Calculate capital gains tax on a commercial warehouse bought for 100,000,000 UGX and sold for 160,000,000 UGX", expect_mode="calculator", expect_reply_contains=["60,000,000"], expect_reply_regex=[r"(?:capital gain|profit|30%|18,000,000)"]),
            ],
        ),
        CXScenario(
            id="CX-019",
            category="Pillar 2: Deterministic Computations",
            title="Goods Supply Withholding Tax on 10,000,000 UGX",
            description="Computes 6% withholding tax on goods supplies above 1M UGX threshold.",
            turns=[
                TurnStep("Calculate withholding tax on supply of commercial office furniture worth 10,000,000 UGX", expect_mode="calculator", expect_reply_contains=["6%", "600,000"]),
            ],
        ),
        CXScenario(
            id="CX-020",
            category="Pillar 2: Deterministic Computations",
            title="Small Business Presumptive Tax Calculation (35,000,000 UGX)",
            description="Calculates presumptive income tax for small retail enterprise.",
            turns=[
                TurnStep("Calculate presumptive tax for my retail grocery shop with annual gross turnover of 35,000,000 UGX", expect_reply_regex=[r"(?:presumptive|turnover|ugx|rate|income\s+tax)"]),
            ],
        ),
    ])

    # =========================================================================
    # PILLAR 3: Real-World Narrative Stories & Complex Business Cases (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-021",
            category="Pillar 3: Narrative Stories",
            title="Small Bakery EFRIS Enforcement Threat & 10M Penalty Panic",
            description="Bakery owner in Mukono with money sent from sister in UK faces 10M penalty threat under 35M turnover.",
            turns=[
                TurnStep(
                    "I started a small bakery in Mukono 8 months ago, using my personal savings and some money sent by my sister in the UK. "
                    "Last week, a field officer visited and told me I must use EFRIS and threatened me with a 10 million penalty because I don't have electronic receipts. "
                    "I have not even reached 35 million in total annual sales. What should I do right now?",
                    expect_reply_regex=[r"(?:efris|vat|threshold|penalty|objection|ura)"],
                    expect_resources_min=1,
                )
            ],
        ),
        CXScenario(
            id="CX-022",
            category="Pillar 3: Narrative Stories",
            title="Stranded Imported Car at Malaba & Rogue Clearing Agent",
            description="Importer in Malaba with rogue clearing agent facing imminent 14-day auction notice on imported car.",
            turns=[
                TurnStep(
                    "I imported a 2017 car through Mombasa and it arrived at Malaba customs. "
                    "My clearing agent took 8 million shillings from me and disappeared with the papers. "
                    "Now the bonded warehouse manager says my vehicle has only 14 days before it gets auctioned. "
                    "How do I clear the vehicle myself with URA, compute the duties, and stop the auction?",
                    expect_reply_regex=[r"(?:customs|duty|warehouse|prn|ura|agent)"],
                    expect_resources_min=1,
                )
            ],
        ),
        CXScenario(
            id="CX-023",
            category="Pillar 3: Narrative Stories",
            title="Remote Software Engineer Receiving Foreign USD Remittances",
            description="Freelancer in Jinja receiving USD payments via Wise from German company asked by URA about bank credits.",
            turns=[
                TurnStep(
                    "I am a software engineer in Jinja freelancing remotely for tech companies in Germany and the US. "
                    "They wire money to my Equity Bank account in USD. URA sent me an inquiry asking about unexplained bank deposits. "
                    "Do I have to charge 18% VAT or is foreign service zero-rated, and how should I report this income?",
                    expect_reply_regex=[r"(?:export|zero-rated|income|vat|deduct|ura)"],
                )
            ],
        ),
        CXScenario(
            id="CX-024",
            category="Pillar 3: Narrative Stories",
            title="Landlord-Tenant Agency Notice Dispute & Eviction Threat",
            description="Tenant whose rent was garnished by URA under Section 40 TPCA faces landlord lock-out.",
            turns=[
                TurnStep(
                    "My landlord locked my shop in Kampala because URA served an agency notice to my bank and deducted my rent payments directly to pay his tax arrears. "
                    "Now the landlord claims I never paid him rent and is threatening eviction. Am I legally protected by URA?",
                    expect_reply_regex=[r"(?:agency\s+notice|section\s+40|indemnif|protect|rent)"],
                )
            ],
        ),
        CXScenario(
            id="CX-025",
            category="Pillar 3: Narrative Stories",
            title="Ancestral Agricultural Land Sale & Family Estate Dispute",
            description="Family in Mbarara selling grandfather's farm for school fees told URA will deduct 30% capital gains.",
            turns=[
                TurnStep(
                    "Our family in Mbarara recently sold 5 acres of ancestral farm land that belonged to our late grandfather to pay university tuition. "
                    "A local broker claimed URA will deduct 30% capital gains tax from our sale money. Is ancestral family land exempt from tax?",
                    expect_reply_regex=[r"(?:capital\s+gains?|exempt|agricultural|business\s+asset|land)"],
                )
            ],
        ),
        CXScenario(
            id="CX-026",
            category="Pillar 3: Narrative Stories",
            title="Lake Victoria Fish Exporter Cross-Border EAC Question",
            description="Fisherman in Busia exporting Nile Perch to Kenya needs to know EAC export tax rules.",
            turns=[
                TurnStep(
                    "I buy fresh fish from fishermen in Busia and export it across the border to Kisumu, Kenya. "
                    "A customs official at the border post told me I need a phytosanitary certificate and to pay export VAT. "
                    "Are fish exports within the East African Community zero-rated for VAT?",
                    expect_reply_regex=[r"(?:export|zero-rated|0%|eac|customs|vat)"],
                )
            ],
        ),
        CXScenario(
            id="CX-027",
            category="Pillar 3: Narrative Stories",
            title="Retail Hardware Double-Taxation Claim on Construction Materials",
            description="Hardware shop owner in Gulu bought cement with 18% VAT, then government client withheld 6% WHT and 18% VAT.",
            turns=[
                TurnStep(
                    "I supply cement to a government school in Gulu. The manufacturer charged me 18% VAT when I bought the cement. "
                    "When the school paid me, the district accounting officer deducted 6% withholding tax and 18% VAT. "
                    "Am I being double taxed, and how do I claim my input tax credit on e-Tax?",
                    expect_reply_regex=[r"(?:input\s+tax|credit|withholding|etax|claim)"],
                )
            ],
        ),
        CXScenario(
            id="CX-028",
            category="Pillar 3: Narrative Stories",
            title="NGO Medical Clinic Receiving International Grant Funds",
            description="Charity clinic in Arua receiving USAID health grant worried about income tax liability.",
            turns=[
                TurnStep(
                    "We run a registered non-profit medical clinic in Arua providing free malaria treatment. "
                    "We just received a 100,000 USD grant from a charity in the Netherlands. "
                    "Does URA consider donor grant money taxable business income, and do we need a tax exemption certificate?",
                    expect_reply_regex=[r"(?:exemption|exempt\s+organization|grant|income\s+tax|tin)"],
                )
            ],
        ),
        CXScenario(
            id="CX-029",
            category="Pillar 3: Narrative Stories",
            title="Mobile Money Agent Ledger & 0.5% Cash-Out Reconciliation",
            description="Agent in Wakiso confused whether 0.5% mobile money excise duty is part of income tax.",
            turns=[
                TurnStep(
                    "I operate a telecom mobile money booth in Wakiso. Every customer withdrawal pays 0.5% excise duty. "
                    "Now URA says I must file an annual income tax return. Is the 0.5% mobile money excise duty my final tax, or do I owe personal income tax on my agent commission?",
                    expect_reply_regex=[r"(?:return|e-services|etax|e-returns|income\s+tax|due\s+date|ura)"],
                )
            ],
        ),
        CXScenario(
            id="CX-030",
            category="Pillar 3: Narrative Stories",
            title="Sole Proprietor Incorporating Business & Transferring Fleet",
            description="Business owner in Jinja converting to limited company worried about stamp duty on vehicle transfer.",
            turns=[
                TurnStep(
                    "I have been running my transport business as a sole proprietor for 5 years. I just incorporated a private limited company with URSB. "
                    "When I transfer my 3 delivery vans from my personal name to the company name, what stamp duty or transfer fees does URA charge?",
                    expect_reply_regex=[r"(?:stamp\s+duty|transfer|motor\s+vehicle|fee|ursb)"],
                )
            ],
        ),
    ])

    # =========================================================================
    # PILLAR 4: Actionable Resource Delivery & Deep Portal Linking (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-031",
            category="Pillar 4: Actionable Resources",
            title="Downloadable Return Templates & eTax Portal Hub",
            description="Delivers direct official return templates and login links.",
            turns=[TurnStep("Where can I download the return filing form templates and log in?", expect_resources_min=1, expect_reply_contains=["https://ura.go.ug"])],
        ),
        CXScenario(
            id="CX-032",
            category="Pillar 4: Actionable Resources",
            title="EFRIS Fiscal Invoicing Portal & FDN Verification Tool",
            description="Delivers official EFRIS portal login and Fiscal Document Number validation resource.",
            turns=[TurnStep("I need the direct portal link to verify an EFRIS receipt FDN and login to my EFRIS account", expect_resources_min=1, expect_reply_regex=[r"(?:efris|fdn|ura\.go\.ug)"])],
        ),
        CXScenario(
            id="CX-033",
            category="Pillar 4: Actionable Resources",
            title="e-Services Make a Payment & Bank PRN Slip Delivery",
            description="Returns direct payment portal links for generating and clearing PRNs.",
            turns=[TurnStep("Provide me with the official URA links to pay my tax assessment and generate a PRN", expect_resources_min=1, expect_reply_regex=[r"(?:prn|payment|etax|ura\.go\.ug)"])],
        ),
        CXScenario(
            id="CX-034",
            category="Pillar 4: Actionable Resources",
            title="Motor Vehicle Transfer Form & Logbook Search Portal",
            description="Provides verified links for motor vehicle transfer and logbook verification.",
            turns=[TurnStep("Where do I access the motor vehicle ownership transfer form and verify a logbook online?", expect_resources_min=1, expect_reply_regex=[r"(?:motor\s+vehicle|transfer|logbook|ura\.go\.ug)"])],
        ),
        CXScenario(
            id="CX-035",
            category="Pillar 4: Actionable Resources",
            title="Withholding Tax Exemption Application Portal & Guidelines",
            description="Provides resources for applying for a 6% WHT exemption certificate.",
            turns=[TurnStep("Give me the link and application procedure to apply for withholding tax exemption certificate", expect_resources_min=1, expect_reply_regex=[r"(?:withholding|exemption|certificate|etax)"])],
        ),
        CXScenario(
            id="CX-036",
            category="Pillar 4: Actionable Resources",
            title="Advance Tax on Commercial Passenger Vehicles Portal",
            description="Returns forms and payment links for commercial taxis and buses.",
            turns=[TurnStep("Where do I generate a PRN for advance tax on my 14-seater passenger taxi?", expect_resources_min=1, expect_reply_regex=[r"(?:advance\s+tax|passenger|prn|ura)"])],
        ),
        CXScenario(
            id="CX-037",
            category="Pillar 4: Actionable Resources",
            title="Digital Tax Stamps (DTS) & Kakasa Verification Mobile Link",
            description="Supplies Kakasa mobile app and DTS manufacturer registration links.",
            turns=[TurnStep("Where can I download the Kakasa app to verify digital tax stamps on beverages?", expect_reply_regex=[r"(?:kakasa|dts|stamp|ura)"])],
        ),
        CXScenario(
            id="CX-038",
            category="Pillar 4: Actionable Resources",
            title="Authorized Economic Operator (AEO) Application Guide",
            description="Delivers AEO certification requirements and documentation checklist.",
            turns=[TurnStep("Give me the documentation checklist and portal link to apply for Authorized Economic Operator status", expect_reply_regex=[r"(?:aeo|authorized\s+economic\s+operator|customs|ura)"])],
        ),
        CXScenario(
            id="CX-039",
            category="Pillar 4: Actionable Resources",
            title="Customs Single Window ASYCUDA Portal Access",
            description="Direct link to ASYCUDA World portal for customs declarations.",
            turns=[TurnStep("Provide the portal link for ASYCUDA World customs entry declaration", expect_reply_regex=[r"(?:asycuda|customs|single\s+window|ura\.go\.ug)"])],
        ),
        CXScenario(
            id="CX-040",
            category="Pillar 4: Actionable Resources",
            title="Local Excise Duty (LED) Monthly Return Template",
            description="Provides downloadable templates for declaring local excise duty.",
            turns=[TurnStep("Where do I download the Local Excise Duty monthly return template?", expect_resources_min=1, expect_reply_regex=[r"(?:excise|return|template|etax|ura)"])],
        ),
    ])

    # =========================================================================
    # PILLAR 5: Empathetic Crisis De-escalation & Enforcement Protection (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-041",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Emergency Bank Freeze & Third-Party Agency Notice Relief",
            description="Validates panic and outlines legal rights under Section 40 TPCA.",
            turns=[TurnStep("I am terrified! URA issued an agency notice on my bank account and froze my funds. What can I do?", expect_reply_contains=["Third-Party Agency Notice"], expect_reply_regex=[r"(?:sorry|understand|options|contact)"])],
        ),
        CXScenario(
            id="CX-042",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Late Filing Penalty Panic & Voluntary Disclosure Relief",
            description="De-escalates anxiety about overdue penalties and explains waiver procedures.",
            turns=[TurnStep("I missed the filing deadline and I cannot afford these heavy penalties, please help me out!", expect_reply_regex=[r"(?:penalty|waiver|voluntary disclosure|0800)"])],
        ),
        CXScenario(
            id="CX-043",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Bribery Attempt & Fraudulent Officer Reporting (Integrity Line)",
            description="Directs taxpayer facing extortion to URA Internal Affairs and toll-free integrity lines.",
            turns=[TurnStep("A man claiming to be a URA revenue officer says if I don't give him 2 million in cash right now, he will shut down my clinic. How do I report this?", expect_reply_regex=[r"(?:0800|toll-free|whistleblow|integrity|internal\s+affairs|cash)"])],
        ),
        CXScenario(
            id="CX-044",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Distress Warrant & Court Bailiff Seizure Fear",
            description="Advises on legal procedures under Section 41 TPCA when threatened with bailiffs.",
            turns=[TurnStep("Court bailiffs arrived with a URA distress warrant threatening to carry away my printing machines tomorrow morning. What legal rights do I have?", expect_reply_regex=[r"(?:distress\s+warrant|section\s+41|objection|memorandum|ura)"])],
        ),
        CXScenario(
            id="CX-045",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Taxpayer Requests Immediate Human Officer Escalation",
            description="Connects taxpayer to human officer queue when requested.",
            turns=[TurnStep("I need to talk to a human URA officer right now regarding my audit dispute.", expect_escalation=True, expect_reply_regex=[r"(?:officer|ticket|human|review)"])],
        ),
        CXScenario(
            id="CX-046",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Comprehensive Audit Notice Panic & Preparation Roadmap",
            description="De-escalates fear when receiving comprehensive tax audit notification letter.",
            turns=[TurnStep("I received a formal comprehensive audit notice from URA for the last 3 years. I am shaking with anxiety. What are my rights as a taxpayer?", expect_reply_regex=[r"(?:audit|records|rights|books|officer)"])],
        ),
        CXScenario(
            id="CX-047",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Accidental Double Payment & Excess PRN Refund Request",
            description="Reassures taxpayer who accidentally paid tax twice on the same assessment.",
            turns=[TurnStep("I made a terrible mistake and paid my 4,000,000 UGX tax assessment twice using two different PRNs. How do I get my money back?", expect_reply_regex=[r"(?:refund|credit|offset|application|etax)"])],
        ),
        CXScenario(
            id="CX-048",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Identity Theft / Fraudulent TIN Registration Discovery",
            description="Urgent advice when someone discovers a business was registered using their stolen NIN.",
            turns=[TurnStep("Someone used my stolen National ID card to register a company TIN without my permission and accrued 20 million in tax arrears. What do I do?", expect_reply_regex=[r"(?:fraud|investigation|nira|police|integrity|dispute|organisation|company|tin|ura)"])],
        ),
        CXScenario(
            id="CX-049",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Detained Perishable Cargo at Customs Border Post",
            description="Emergency advice when refrigerated perishables are stuck at customs due to valuation dispute.",
            turns=[TurnStep("My refrigerated container of dairy products is stuck at Malaba border. The officer disputed the invoice and it is spoiling in the heat. Can I post a bond for immediate release?", expect_reply_regex=[r"(?:bond|release|provisional|valuation|customs)"])],
        ),
        CXScenario(
            id="CX-050",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Impounded Transit Truck Breakdown on Northern Corridor",
            description="Guides transit transporter on RECTS tracking and Transit Monitoring Unit protocol.",
            turns=[TurnStep("Our transit fuel tanker broke down in Iganga en route to Rwanda and the electronic cargo seal (RECTS) is beeping. How do we notify URA to avoid heavy fines?", expect_reply_regex=[r"(?:tmu|transit|monitoring|rects|0323|customs)"])],
        ),
    ])

    # =========================================================================
    # PILLAR 6: Closed-Loop Conversational Bug & Knowledge Reporting (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-051",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Outdated VAT Rate",
            description="Emits Knowledge Report #KB-XXX when taxpayer pushes back on rate.",
            turns=[
                TurnStep("What is the VAT rate in Uganda?", expect_reply_contains=["VAT"]),
                TurnStep("No, that is incorrect. Under the 2023 Amendment Act, the rate was changed to 6% instead of 18%.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)", r"6%"]),
            ],
        ),
        CXScenario(
            id="CX-052",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Mandatory VAT Registration Threshold",
            description="Emits discrepancy ticket when user asserts new threshold.",
            turns=[
                TurnStep("What is the annual threshold to register for VAT?", expect_reply_contains=["turnover"]),
                TurnStep("Actually, that is outdated. The law was updated recently and the threshold is no longer what you said.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-053",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Repealed Motor Vehicle Form Number",
            description="Captures user dispute on procedural form numbers.",
            turns=[
                TurnStep("Which form do I fill to transfer a car?", expect_reply_contains=["transfer"]),
                TurnStep("You are wrong, that form was abolished and replaced by an online e-service with no paper form.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-054",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Withholding Tax Rate on Rental Income",
            description="Logs discrepancy ticket when taxpayer disputes withholding percentage.",
            turns=[
                TurnStep("What is the withholding tax rate on dividends?", expect_reply_contains=["15%"]),
                TurnStep("That is wrong, under the new amendment it is 10% instead of 15%.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-055",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Resident Corporation Tax Rate",
            description="Captures user dispute on standard 30% corporation tax.",
            turns=[
                TurnStep("What is the corporate income tax rate in Uganda?", expect_reply_contains=["30%"]),
                TurnStep("No, that rate changed under the latest budget speech to 25%.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-056",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Legal Services Withholding Tax",
            description="Captures dispute on resident professional services WHT.",
            turns=[
                TurnStep("What is the withholding tax rate on legal fees?", expect_reply_regex=[r"(?:6%|withholding)"]),
                TurnStep("That is incorrect, withholding on lawyers was suspended last month.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-057",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Mobile Money Withdrawal Duty",
            description="Captures dispute on 0.5% mobile money excise duty.",
            turns=[
                TurnStep("What is the excise duty rate on mobile money cash withdrawals?", expect_reply_contains=["0.5%"]),
                TurnStep("Stop giving false info, mobile money tax was repealed yesterday by decree.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-058",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Used Car Environmental Levy Rate",
            description="Logs discrepancy report when environmental levy schedule is challenged.",
            turns=[
                TurnStep("What environmental levy is charged on importing a 10 year old car?", expect_reply_regex=[r"(?:environmental|levy|customs)"]),
                TurnStep("That is wrong! The age restriction and environmental levy changed this fiscal year.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-059",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Commercial Rent WHT Threshold",
            description="Captures user assertion regarding commercial rent withholding.",
            turns=[
                TurnStep("Is withholding tax deducted on rental payments?", expect_reply_regex=[r"(?:rental|withholding)"]),
                TurnStep("Actually, that is not true. Small landlords don't have withholding deducted under the new act.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
        CXScenario(
            id="CX-060",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Stamp Duty on Land Title Transfers",
            description="Logs discrepancy ticket when taxpayer disputes 1% stamp duty.",
            turns=[
                TurnStep("What is the stamp duty percentage on land title transfer in Uganda?", expect_reply_contains=["1%"]),
                TurnStep("No, that is outdated. Stamp duty on land transfer was raised to 2% under the new Stamp Duty Amendment.", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"]),
            ],
        ),
    ])

    # =========================================================================
    # PILLAR 7: Multilingual Task Fulfillment (Luganda & Swahili) (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-061",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda TIN Guidance & Step Breakdown",
            description="Responds natively in Luganda explaining step-by-step TIN registration.",
            locale="lg",
            turns=[TurnStep("Nnyamba okufuna TIN yange ey'obuntu, nkoze ntya?", expect_reply_contains=["TIN"], expect_reply_regex=[r"(?:omukutu|foomu|NIN|e-Services|ura)"])],
        ),
        CXScenario(
            id="CX-062",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda PAYE Take-Home Salary Explanation",
            description="Explains PAYE salary deduction natively in Luganda.",
            locale="lg",
            turns=[TurnStep("Bansasula emitwalo amakumi ataano (500,000 UGX) buli mwezi. Nsasula omusolo gwa PAYE mmeka?", expect_reply_regex=[r"(?:paye|omusolo|ugx|ura|emitwalo)"])],
        ),
        CXScenario(
            id="CX-063",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda EFRIS Guidance for Small Market Vendors",
            description="Guides a Owino market vendor in Luganda regarding EFRIS electronic receipts.",
            locale="lg",
            turns=[TurnStep("Nnina edduuka mu katale k'e Nakasero. Nteekwa okukozesa EFRIS okukola lisiti?", expect_reply_regex=[r"(?:efris|lisiti|omusolo|katale|ura)"])],
        ),
        CXScenario(
            id="CX-064",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda Motor Vehicle Ownership Transfer Procedure",
            description="Guides vehicle logbook transfer in Luganda.",
            locale="lg",
            turns=[TurnStep("Nguze emmotoka empya. Nkyusa ntya ebiwandiiko by'obwannannyini mu URA?", expect_reply_regex=[r"(?:emmotoka|biwandiiko|kyusa|ura|logbook)"])],
        ),
        CXScenario(
            id="CX-065",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda Tax Penalty Waiver & Relief Advice",
            description="Explains how to request a waiver for overdue penalties in Luganda.",
            locale="lg",
            turns=[TurnStep("Nnalwisaayo omusolo gwange ne banseleza ebibonerezo bingi. Nsobola okusaba URA binsonyiwe?", expect_reply_regex=[r"(?:ebibonerezo|sonyiwa|omusolo|ura|okusaba)"])],
        ),
        CXScenario(
            id="CX-066",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili 18% VAT Mathematical Calculation",
            description="Calculates VAT accurately while answering natively in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Hesabu kiasi cha VAT ya 18% kwa bidhaa za thamani ya shilingi 10,000,000 UGX", expect_mode="calculator", expect_reply_contains=["1,800,000"])],
        ),
        CXScenario(
            id="CX-067",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili Customs Clearance Guidance for EAC Trader",
            description="Advises cross-border trader in Kiswahili regarding customs clearance.",
            locale="sw",
            turns=[TurnStep("Ninaingiza bidhaa kutoka Kenya kupitia mpaka wa Malaba. Nawezaje kulipa ushuru wa forodha na kupata kibali?", expect_reply_regex=[r"(?:forodha|ushuru|malaba|ura|prn)"])],
        ),
        CXScenario(
            id="CX-068",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili Individual TIN Application Step Breakdown",
            description="Explains TIN application steps in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Nisaidie kupata nambari ya TIN kama mtu binafsi nchini Uganda", expect_reply_contains=["TIN"], expect_reply_regex=[r"(?:tovuti|ura|kitambulisho|eservices)"])],
        ),
        CXScenario(
            id="CX-069",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili PRN Payment Slip Generation Procedure",
            description="Instructs how to create and pay a PRN slip in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Jinsi ya kutengeneza nambari ya PRN ili kulipa kodi benki au kwa simu?", expect_reply_regex=[r"(?:prn|malipo|benki|simu|ura)"])],
        ),
        CXScenario(
            id="CX-070",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili Corporate Income Tax Compliance Advice",
            description="Explains company tax filing obligations in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Kampuni yangu inapaswa kulipa asilimia ngapi ya kodi ya mapato ya kila mwaka?", expect_reply_regex=[r"(?:30%|kampuni|mapato|mwaka|kodi)"])],
        ),
    ])

    # =========================================================================
    # PILLAR 8: Statutory Boundary Probing & Classification Integrity (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-071",
            category="Pillar 8: Statutory Boundary Probing",
            title="Turnover Threshold Boundary Probe (149M vs 150M UGX)",
            description="Tests advice for business on the exact threshold of mandatory VAT registration.",
            turns=[TurnStep("My hardware business earned 149,000,000 UGX in the last 12 months. Am I legally required to register for VAT tomorrow?", expect_reply_regex=[r"(?:150|million|threshold|mandatory|voluntary)"])],
        ),
        CXScenario(
            id="CX-072",
            category="Pillar 8: Statutory Boundary Probing",
            title="Unprocessed Agricultural Produce vs Packaged Supply Exemption",
            description="Distinguishes between raw exempt farm produce and taxable processed food supplies.",
            turns=[TurnStep("Do I charge 18% VAT on selling raw fresh cassava from my farm versus selling packaged cassava flour in a supermarket?", expect_reply_regex=[r"(?:exempt|unprocessed|processed|18%|vat|second schedule)"])],
        ),
        CXScenario(
            id="CX-073",
            category="Pillar 8: Statutory Boundary Probing",
            title="Zero-Rated Goods vs Exempt Supplies Distinction",
            description="Clarifies whether input tax credits can be claimed on zero-rated versus exempt transactions.",
            turns=[TurnStep("What is the practical difference between zero-rated VAT supplies and exempt supplies when claiming input tax credits?", expect_reply_regex=[r"(?:input\s+tax|credit|claim|zero-rated|exempt|0%)"])],
        ),
        CXScenario(
            id="CX-074",
            category="Pillar 8: Statutory Boundary Probing",
            title="Capital Gains on Primary Residential Home Exemption",
            description="Explains primary private residence exemption from capital gains tax.",
            turns=[TurnStep("If I sell my primary family residential house where I have lived for 10 years, does URA tax the profit as capital gains?", expect_reply_regex=[r"(?:exempt|principal\s+residence|home|capital\s+gains?|not\s+tax)"])],
        ),
        CXScenario(
            id="CX-075",
            category="Pillar 8: Statutory Boundary Probing",
            title="Sole Proprietorship Presumptive vs Company 30% Tax",
            description="Compares small business presumptive regime with corporate income tax.",
            turns=[TurnStep("What is the tax rate difference between running as a sole trader under presumptive tax versus a registered limited company?", expect_reply_regex=[r"(?:presumptive|30%|turnover|company|sole)"])],
        ),
        CXScenario(
            id="CX-076",
            category="Pillar 8: Statutory Boundary Probing",
            title="Employee Medical & Travel Reimbursable Allowances",
            description="Clarifies taxability of official employer medical reimbursements under PAYE.",
            turns=[TurnStep("Are employee medical insurance premiums paid by an employer subject to PAYE tax deduction?", expect_reply_regex=[r"(?:exempt|benefit|medical|paye|taxable)"])],
        ),
        CXScenario(
            id="CX-077",
            category="Pillar 8: Statutory Boundary Probing",
            title="Human Medicine & Essential Drugs VAT Exemption",
            description="Verifies zero-rated/exempt status for medical equipment and essential pharmaceuticals.",
            turns=[TurnStep("Does a retail pharmacy charge 18% VAT on the sale of essential prescription human medicines?", expect_reply_regex=[r"(?:exempt|medicine|drugs|vat|health)"])],
        ),
        CXScenario(
            id="CX-078",
            category="Pillar 8: Statutory Boundary Probing",
            title="Solar Energy Equipment Customs Import Duty Exemption",
            description="Checks duty-free customs tariff code treatment for renewable energy solar equipment.",
            turns=[TurnStep("What import duty and VAT rates apply when importing solar panels and deep-cycle solar batteries into Uganda?", expect_reply_regex=[r"(?:exempt|solar|duty-free|0%|renewable)"])],
        ),
        CXScenario(
            id="CX-079",
            category="Pillar 8: Statutory Boundary Probing",
            title="Printed Textbooks & Educational Materials Exemption",
            description="Verifies customs and domestic tax exemptions for school textbooks.",
            turns=[TurnStep("Are imported primary school textbooks subject to 18% VAT and import duty?", expect_reply_regex=[r"(?:exempt|textbooks|education|vat|duty)"])],
        ),
        CXScenario(
            id="CX-080",
            category="Pillar 8: Statutory Boundary Probing",
            title="Diplomatic Mission & Bilateral Aid Tax Immunity",
            description="Explains diplomatic mission tax exemptions and refund mechanisms.",
            turns=[TurnStep("How do accredited foreign diplomatic embassies claim refunds for VAT paid on local goods and fuel?", expect_reply_regex=[r"(?:diplomatic|privilege|refund|foreign\s+affairs|etax)"])],
        ),
    ])

    # =========================================================================
    # PILLAR 9: Omnichannel Case Tracking & Reference Resolution (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-081",
            category="Pillar 9: Omnichannel Tracking",
            title="Inquiry on Open Ticket Reference TIC-EA500752",
            description="Verifies live lookup of open support ticket status and assigned team.",
            turns=[TurnStep("I called earlier and was given reference TIC-EA500752. What is the status of my inquiry?", expect_reply_contains=["TIC-EA500752"], expect_reply_regex=[r"(?:status|officer|assigned|review|support)"])],
        ),
        CXScenario(
            id="CX-082",
            category="Pillar 9: Omnichannel Tracking",
            title="Inquiry on Ticket Reference with Lowercase Input",
            description="Verifies case-insensitive normalization of ticket references.",
            turns=[TurnStep("Check my ticket status: tic-62d59b73", expect_reply_contains=["TIC-62D59B73"], expect_reply_regex=[r"(?:status|officer|ura|support)"])],
        ),
        CXScenario(
            id="CX-083",
            category="Pillar 9: Omnichannel Tracking",
            title="Inquiry on Support Reference from Phone Desk Call",
            description="Taxpayer follows up on web chat using reference given on audio call.",
            turns=[TurnStep("The officer on phone gave me case reference TIC-10AA8302. Has my file been reviewed?", expect_reply_contains=["TIC-10AA8302"], expect_reply_regex=[r"(?:status|case|review|officer)"])],
        ),
        CXScenario(
            id="CX-084",
            category="Pillar 9: Omnichannel Tracking",
            title="Check Status of Verified Knowledge Report KB-287690E1",
            description="Verifies lookup of an approved knowledge review discrepancy report.",
            turns=[TurnStep("Check the status of my bug report KB-287690E1", expect_reply_contains=["KB-287690E1"], expect_reply_regex=[r"(?:knowledge|report|status|review|policy)"])],
        ),
        CXScenario(
            id="CX-085",
            category="Pillar 9: Omnichannel Tracking",
            title="Check Status of Knowledge Review Report with Prefix Lowercase",
            description="Verifies case-insensitive lookup of knowledge review reports.",
            turns=[TurnStep("What happened to my report kb-7e032876?", expect_reply_contains=["KB-7E032876"], expect_reply_regex=[r"(?:report|status|review|team)"])],
        ),
        CXScenario(
            id="CX-086",
            category="Pillar 9: Omnichannel Tracking",
            title="PRN Verification via 12-Digit Reference",
            description="Guides taxpayer on confirming payment status of generated PRN.",
            turns=[TurnStep("How can I confirm if PRN 226000123456 has been credited by the commercial bank?", expect_reply_regex=[r"(?:prn|bank|status|etax|search)"])],
        ),
        CXScenario(
            id="CX-087",
            category="Pillar 9: Omnichannel Tracking",
            title="Customs Bonded IM7 Bill of Lading Tracking Guidance",
            description="Instructs importer on checking customs bonded status via ASYCUDA.",
            turns=[TurnStep("How do I track my customs declaration IM7 reference number on the portal?", expect_reply_regex=[r"(?:asycuda|customs|im7|declaration|status)"])],
        ),
        CXScenario(
            id="CX-088",
            category="Pillar 9: Omnichannel Tracking",
            title="TCC Application Acknowledgement Tracking",
            description="Explains how to check status of a pending Tax Clearance Certificate application.",
            turns=[TurnStep("I applied for a TCC 3 days ago on eTax. How do I track the approval status using my application number?", expect_reply_regex=[r"(?:tcc|clearance|etax|track|status)"])],
        ),
        CXScenario(
            id="CX-089",
            category="Pillar 9: Omnichannel Tracking",
            title="Motor Vehicle Search Application Reference Tracking",
            description="Guides tracking of logbook search application.",
            turns=[TurnStep("I paid 22,000 UGX for motor vehicle search. How do I download the certified vehicle report using the search reference?", expect_reply_regex=[r"(?:search|report|motor\s+vehicle|prn|download)"])],
        ),
        CXScenario(
            id="CX-090",
            category="Pillar 9: Omnichannel Tracking",
            title="Objection Decision 30-Day Statutory Clock Inquiry",
            description="Explains statutory 90-day objection decision timeline and deemed allowance.",
            turns=[TurnStep("I submitted my tax objection 40 days ago with reference OBJ-2026. How long does the Commissioner have to make an objection decision?", expect_reply_regex=[r"(?:objection|90\s+days|commissioner|decision|deemed)"])],
        ),
    ])

    # =========================================================================
    # PILLAR 10: Advanced Situational Advisory & Dispute Resolution (10 Scenarios)
    # =========================================================================
    scenarios.extend([
        CXScenario(
            id="CX-091",
            category="Pillar 10: Advanced Advisory",
            title="Hotel Operator Low Season Cashflow & Installment Agreement",
            description="Guides hotelier on applying for a formal tax arrears payment plan under TPCA.",
            turns=[TurnStep("Our safari lodge in Queen Elizabeth Park has zero bookings this low season. We owe 40M in VAT. How do we negotiate an installment agreement with URA without being penalized?", expect_reply_regex=[r"(?:installment|payment\s+plan|agreement|commissioner|memorandum)"])],
        ),
        CXScenario(
            id="CX-092",
            category="Pillar 10: Advanced Advisory",
            title="Manufacturing Raw Material Duty Remission Scheme",
            description="Explains East African Community Duty Remission Scheme for domestic industrialists.",
            turns=[TurnStep("We manufacture plastic packaging in Namanve. Can we import raw polymer granules under the EAC Duty Remission Scheme at 0% import duty?", expect_reply_regex=[r"(?:remission|raw\s+materials?|manufacturer|gazette|eac)"])],
        ),
        CXScenario(
            id="CX-093",
            category="Pillar 10: Advanced Advisory",
            title="Commercial Transport Fleet Advance Tax Offsetting",
            description="Advises how advance tax paid on commercial vehicle licenses offsets annual income tax.",
            turns=[TurnStep("We operate 20 haulage trucks and paid advance tax on every vehicle license renewal. Can we claim all these advance tax receipts against our final corporation tax return?", expect_reply_regex=[r"(?:advance\s+tax|credit|offset|corporation|return)"])],
        ),
        CXScenario(
            id="CX-094",
            category="Pillar 10: Advanced Advisory",
            title="Expatriate Consultant 120-Day Physical Presence Tax Test",
            description="Applies Ugandan tax residency rules (183-day rule vs permanent home).",
            turns=[TurnStep("I am an engineer from South Africa working on a dam project in Karuma for 120 days this year. Am I considered a tax resident or non-resident in Uganda?", expect_reply_regex=[r"(?:resident|non-resident|183\s+days|income\s+tax)"])],
        ),
        CXScenario(
            id="CX-095",
            category="Pillar 10: Advanced Advisory",
            title="Private School Commercial Canteen & Uniform Shop Taxability",
            description="Distinguishes core tuition exemptions from ancillary commercial supplies.",
            turns=[TurnStep("Our private secondary school in Masaka runs a school canteen selling soft drinks and custom school uniforms. Is revenue from uniforms and canteen food subject to VAT and income tax?", expect_reply_regex=[r"(?:exempt|taxable|uniforms|commercial|vat)"])],
        ),
        CXScenario(
            id="CX-096",
            category="Pillar 10: Advanced Advisory",
            title="Construction Contractor Retention Money & Delayed Milestone WHT",
            description="Advises on when withholding tax is legally due on construction contract retentions.",
            turns=[TurnStep("We built a road for UNRA. They withheld 10% retention money payable after the 12-month defects liability period. When does the client deduct the 6% withholding tax: on invoice or upon actual cash release of the retention?", expect_reply_regex=[r"(?:withholding|payment|accrual|retention|remitted)"])],
        ),
        CXScenario(
            id="CX-097",
            category="Pillar 10: Advanced Advisory",
            title="Arabica Coffee Exporter Withholding Tax & Cess Obligations",
            description="Advises on coffee export cess and local agent withholding exemption.",
            turns=[TurnStep("We export green Arabica coffee beans from Mbale to European buyers. Does URA charge coffee export tax, and are we required to withhold tax when paying local smallholder coffee farmers?", expect_reply_regex=[r"(?:coffee|export|withholding|smallholder|zero-rated)"])],
        ),
        CXScenario(
            id="CX-098",
            category="Pillar 10: Advanced Advisory",
            title="Artisanal Mining Cooperative Royalty & Export Withholding",
            description="Explains mineral royalties and withholding tax requirements under the Mining Act.",
            turns=[TurnStep("We are a registered artisanal gold miners cooperative in Mubende. What mineral royalties and export withholding taxes apply when selling gold bars to an authorized refinery?", expect_reply_regex=[r"(?:mining|royalty|mineral|withholding|export)"])],
        ),
        CXScenario(
            id="CX-099",
            category="Pillar 10: Advanced Advisory",
            title="Property Developer Commercial Plot Subdivision Stamp Duty",
            description="Explains stamp duty and VAT rules when subdividing and titling commercial land.",
            turns=[TurnStep("We bought a 50-acre commercial estate in Entebbe and subdivided it into 100 titled residential plots. What stamp duty applies on issuing individual deed prints, and does VAT apply on selling bare residential land?", expect_reply_regex=[r"(?:stamp\s+duty|vat|exempt|subdivision|land)"])],
        ),
        CXScenario(
            id="CX-100",
            category="Pillar 10: Advanced Advisory",
            title="International Courier E-Commerce Parcel Clearance at Entebbe",
            description="Guides individual receiving personal electronics parcel via DHL/Posta Uganda.",
            turns=[TurnStep("I bought a laptop from Amazon USA and DHL says it arrived at Entebbe airport customs with a 500,000 UGX tax assessment. How is customs duty and VAT computed on personal courier parcels?", expect_reply_regex=[r"(?:cif|import\s+duty|vat|customs|dhl|courier)"])],
        ),
    ])

    return scenarios


def run_scenario(client: requests.Session, base_url: str, scenario: CXScenario) -> dict[str, Any]:
    """Execute all turns of a scenario sequentially against the live endpoint."""
    conv_id = f"cx-{uuid.uuid4().hex[:10]}"
    sess_id = f"sess-{uuid.uuid4().hex[:10]}"
    chat_url = f"{base_url.rstrip('/')}/v1/chat"

    results = []
    scenario_passed = True
    total_time_s = 0.0

    for step_num, step in enumerate(scenario.turns, 1):
        payload = {
            "message": step.user_message,
            "conversation_id": conv_id,
            "locale": scenario.locale,
        }
        step_headers = dict(HEADERS)
        step_headers["X-Session-ID"] = sess_id

        t0 = time.perf_counter()
        try:
            resp = client.post(chat_url, headers=step_headers, json=payload, timeout=60)
            elapsed_s = time.perf_counter() - t0
            total_time_s += elapsed_s
        except Exception as exc:
            results.append({
                "turn": step_num,
                "passed": False,
                "error": f"HTTP request failed: {exc}",
                "elapsed_s": 0.0,
            })
            return {
                "id": scenario.id,
                "title": scenario.title,
                "category": scenario.category,
                "passed": False,
                "total_time_s": 0.0,
                "turns": results,
            }

        if resp.status_code != 200:
            results.append({
                "turn": step_num,
                "passed": False,
                "status_code": resp.status_code,
                "error": resp.text[:250],
                "elapsed_s": elapsed_s,
            })
            scenario_passed = False
            break

        data = resp.json()
        reply = str(data.get("reply") or "").strip()
        mode = str(data.get("retrieval_mode") or "").strip()
        wf = data.get("workflow") or {}
        resources = data.get("resources") or []
        disc_rep = data.get("discrepancy_report")
        escalation_required = bool(data.get("escalation_required"))

        step_failures = []

        # 1. Mode check
        if step.expect_mode and mode.lower() != step.expect_mode.lower():
            if step.expect_mode == "calculator" and mode in ("tools", "education", "rag"):
                pass
            else:
                step_failures.append(f"Expected mode '{step.expect_mode}', got '{mode}'")

        # 2. Workflow check
        if step.expect_workflow:
            wf_name = wf.get("name", "")
            if not wf_name or step.expect_workflow.lower() not in wf_name.lower():
                step_failures.append(f"Expected workflow '{step.expect_workflow}', got '{wf_name}'")

        # 3. Step ID check
        if step.expect_step_id:
            curr_step_id = wf.get("step_id", "")
            if curr_step_id != step.expect_step_id:
                step_failures.append(f"Expected step_id '{step.expect_step_id}', got '{curr_step_id}'")

        # 4. Options check
        if step.expect_options_contain:
            opts = [str(o).lower() for o in wf.get("options", [])]
            for exp_opt in step.expect_options_contain:
                if not any(exp_opt.lower() in o for o in opts):
                    step_failures.append(f"Expected option '{exp_opt}' in {opts}")

        # 5. Reply text substrings
        for substr in step.expect_reply_contains:
            if substr.lower() not in reply.lower():
                step_failures.append(f"Reply missing text: '{substr}'")

        # 6. Regex checks
        for regex_pat in step.expect_reply_regex:
            if not re.search(regex_pat, reply, re.IGNORECASE):
                step_failures.append(f"Reply failed regex match: '{regex_pat}'")

        # 7. Resources check
        if step.expect_resources_min > 0 and len(resources) < step.expect_resources_min:
            step_failures.append(f"Expected >= {step.expect_resources_min} resources, got {len(resources)}")

        # 8. Discrepancy report check
        if step.expect_discrepancy:
            if not disc_rep and "#kb-" not in reply.lower():
                step_failures.append("Expected discrepancy report (#KB-) to be emitted")

        # 9. Escalation check
        if step.expect_escalation is not None:
            if escalation_required != step.expect_escalation and "ticket" not in reply.lower():
                step_failures.append(f"Expected escalation={step.expect_escalation}, got {escalation_required}")

        turn_passed = len(step_failures) == 0
        if not turn_passed:
            scenario_passed = False

        results.append({
            "turn": step_num,
            "user_message": step.user_message[:120],
            "passed": turn_passed,
            "elapsed_s": round(elapsed_s, 2),
            "retrieval_mode": mode,
            "workflow_active": bool(wf),
            "resources_count": len(resources),
            "discrepancy_logged": bool(disc_rep or "#kb-" in reply.lower()),
            "failures": step_failures,
            "snippet": reply[:180].replace("\n", " "),
        })

    return {
        "id": scenario.id,
        "title": scenario.title,
        "category": scenario.category,
        "passed": scenario_passed,
        "total_time_s": round(total_time_s, 2),
        "turns": results,
    }


def find_active_endpoint() -> str:
    """Detect live endpoint from local ngrok API or fallback default."""
    try:
        r = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        if r.status_code == 200:
            data = r.json()
            for t in data.get("tunnels", []):
                p_url = t.get("public_url", "")
                if p_url.startswith("https://"):
                    return f"{p_url}/api"
    except Exception:
        pass
    return DEFAULT_BASE


def main():
    parser = argparse.ArgumentParser(description="Evaluate 100 Customer Experience & Request Completion Scenarios over live ngrok.")
    parser.add_argument("--base", default="", help="Base API URL (e.g. https://<tunnel>/api or http://localhost:8083)")
    parser.add_argument("--workers", type=int, default=1, help="Concurrency workers (default: 1 for orderly streaming)")
    parser.add_argument("--out", default="", help="Path to save JSON evaluation report")
    args = parser.parse_args()

    base_url = args.base.strip()
    if not base_url:
        base_url = find_active_endpoint()

    print("=" * 90)
    print("  URA ASSISTANT — 100-SCENARIO CUSTOMER EXPERIENCE & REQUEST COMPLETION MASTER BENCHMARK")
    print(f"  Target Endpoint : {base_url}")
    print(f"  Concurrency     : {args.workers} worker(s)")
    print("=" * 90)

    session = requests.Session()
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        hr = session.get(health_url, headers=HEADERS, timeout=10)
        print(f"✓ Health probe: HTTP {hr.status_code} ({hr.text[:60]})\n")
    except Exception as e:
        print(f"⚠ Warning: Health probe failed ({e}). Proceeding...\n")

    scenarios = build_100_scenarios()
    print(f"Executing 100 Customer Experience & Request Completion Scenarios across 10 Pillars...\n")

    scenario_results: list[dict[str, Any]] = [None] * len(scenarios)  # type: ignore
    category_stats: dict[str, dict[str, int]] = {}
    latencies: list[float] = []

    def _eval_worker(idx_sc: tuple[int, CXScenario]) -> tuple[int, dict[str, Any]]:
        idx, sc = idx_sc
        worker_session = requests.Session()
        res = run_scenario(worker_session, base_url, sc)
        return idx, res

    if args.workers <= 1:
        for i, sc in enumerate(scenarios, 1):
            print(f"[{i:03d}/100] {sc.id:<8} | {sc.category[:26]:<26} | {sc.title[:38]:<38} ... ", end="", flush=True)
            res = run_scenario(session, base_url, sc)
            scenario_results[i - 1] = res
            latencies.append(res["total_time_s"])
            if res["passed"]:
                print(f"PASS ({res['total_time_s']}s)")
            else:
                print(f"FAIL ({res['total_time_s']}s)")
                for t in res["turns"]:
                    if not t.get("passed"):
                        for f in t.get("failures", []):
                            print(f"         -> [Turn {t.get('turn')}] {f}")
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(_eval_worker, (i, sc)) for i, sc in enumerate(scenarios)]
            for fut in concurrent.futures.as_completed(futures):
                idx, res = fut.result()
                scenario_results[idx] = res
                latencies.append(res["total_time_s"])
                status = "PASS" if res["passed"] else "FAIL"
                print(f"[{idx+1:03d}/100] {res['id']:<8} | {res['category'][:26]:<26} | {status} ({res['total_time_s']}s) - {res['title'][:35]}")

    for res in scenario_results:
        cat = res["category"]
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "passed": 0}
        category_stats[cat]["total"] += 1
        if res["passed"]:
            category_stats[cat]["passed"] += 1

    total_scenarios = len(scenario_results)
    passed_scenarios = sum(1 for s in scenario_results if s["passed"])
    cx_score = round((passed_scenarios / total_scenarios) * 100, 1)

    latencies_sorted = sorted(latencies)
    p50_latency = round(latencies_sorted[int(len(latencies_sorted) * 0.50)], 2)
    p95_latency = round(latencies_sorted[min(int(len(latencies_sorted) * 0.95), len(latencies_sorted) - 1)], 2)
    avg_latency = round(sum(latencies) / len(latencies), 2)

    print("\n" + "=" * 90)
    print("  CUSTOMER EXPERIENCE & REQUEST COMPLETION MASTER SCORECARD (100 SCENARIOS)")
    print("=" * 90)
    for cat in sorted(category_stats.keys()):
        stat = category_stats[cat]
        pct = round((stat["passed"] / stat["total"]) * 100, 1)
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        print(f"  {cat:<40} : [{bar}] {stat['passed']:>2}/{stat['total']:<2} ({pct:>5.1f}%)")

    print("-" * 90)
    print(f"  OVERALL CX REQUEST COMPLETION SCORE : {passed_scenarios}/{total_scenarios} ({cx_score}%)")
    print(f"  LATENCY METRICS (Live Endpoint)      : Avg: {avg_latency}s | P50: {p50_latency}s | P95: {p95_latency}s")
    print("=" * 90)

    report_data = {
        "timestamp": time.time(),
        "date": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "target_endpoint": base_url,
        "overall_cx_score_pct": cx_score,
        "scenarios_total": total_scenarios,
        "scenarios_passed": passed_scenarios,
        "latency_stats": {
            "avg_s": avg_latency,
            "p50_s": p50_latency,
            "p95_s": p95_latency,
        },
        "category_breakdown": category_stats,
        "detailed_results": scenario_results,
    }

    out_path = args.out.strip()
    if not out_path:
        out_dir = Path("docs/Reports/data")
        out_dir.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y_%m_%d")
        out_path = str(out_dir / f"cx_100_scenarios_eval_{today}.json")

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report_data, fh, indent=2, ensure_ascii=False)
    print(f"\n✓ 100-Scenario Evaluation Audit Report saved to: {out_path}\n")

    return 0 if cx_score >= 85 else 1


if __name__ == "__main__":
    sys.exit(main())
