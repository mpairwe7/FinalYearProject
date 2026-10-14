#!/usr/bin/env python3
"""50-Scenario Customer Experience (CX) & Request Completion Benchmark Suite.

Evaluates the URA AI Assistant over the live ngrok endpoint across 50 realistic,
diverse, and complex scenarios to measure active request completion rather
than passive FAQ matching:

  Pillar 1: Interactive Multi-Step Guided Workflows (10 scenarios)
  Pillar 2: Deterministic Tax Computations & Net Take-Home (8 scenarios)
  Pillar 3: Complex Narrative Stories & Situational Case Solving (10 scenarios)
  Pillar 4: Actionable Resource Delivery & Deep Portal Linking (5 scenarios)
  Pillar 5: Empathetic Crisis De-escalation & Enforcement Protection (5 scenarios)
  Pillar 6: Closed-Loop Knowledge Discrepancy & Bug Reporting (4 scenarios)
  Pillar 7: Multilingual Task Fulfillment (Luganda & Swahili) (5 scenarios)
  Pillar 8: Statutory Edge Cases & Exemption Boundary Probing (3 scenarios)

Usage:
  python3 scripts/evaluate_cx_50_scenarios_ngrok.py
  python3 scripts/evaluate_cx_50_scenarios_ngrok.py --base https://<ngrok-domain>/api
  python3 scripts/evaluate_cx_50_scenarios_ngrok.py --out docs/Reports/data/cx_50_eval.json
"""

from __future__ import annotations

import argparse
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
    "User-Agent": "URA-CX-50-Benchmark/2026",
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


def build_50_scenarios() -> list[CXScenario]:
    """Formulate 50 exhaustive real-world scenarios testing task completion."""
    return [
        # =====================================================================
        # PILLAR 1: Interactive Multi-Step Guided Workflows (10 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-01",
            category="Interactive Guided Workflow",
            title="Individual TIN Registration (Privacy-Preserving Stepper)",
            description="Guides individual from applicant type to document readiness and portal handoff.",
            turns=[
                TurnStep(
                    user_message="Help me register for a TIN",
                    expect_mode="workflow",
                    expect_workflow="TIN Registration",
                    expect_step_id="collect_type",
                    expect_options_contain=["individual", "company"],
                ),
                TurnStep(
                    user_message="individual",
                    expect_mode="workflow",
                    expect_workflow="TIN Registration",
                    expect_step_id="collect_documents_ready",
                    expect_reply_contains=["yes/no"],
                ),
                TurnStep(
                    user_message="yes",
                    expect_mode="workflow",
                    expect_workflow="TIN Registration",
                    expect_step_id="summary",
                    expect_reply_contains=["https://ura.go.ug"],
                ),
            ],
        ),
        CXScenario(
            id="CX-02",
            category="Interactive Guided Workflow",
            title="Company Non-Individual TIN Application Workflow",
            description="Provides exact step-by-step guidance for non-individual entity TIN registration with official portal forms.",
            turns=[
                TurnStep(
                    user_message="Help me apply for a TIN for my company",
                    expect_reply_contains=["Non-Individual", "TIN", "directors"],
                    expect_resources_min=1,
                ),
            ],
        ),
        CXScenario(
            id="CX-03",
            category="Interactive Guided Workflow",
            title="Tax Clearance Certificate (TCC) Application Journey",
            description="Initiates interactive guidance for requesting a TCC for government tenders.",
            turns=[
                TurnStep(
                    user_message="Guide me through getting a tax clearance certificate",
                    expect_mode="workflow",
                    expect_workflow="Tax Clearance Certificate",
                    expect_reply_contains=["Tax Clearance Certificate"],
                )
            ],
        ),
        CXScenario(
            id="CX-04",
            category="Interactive Guided Workflow",
            title="Payment Registration Number (PRN) Generation Stepper",
            description="Guides taxpayer through generating a 12-digit PRN voucher.",
            turns=[
                TurnStep(
                    user_message="Guide me to generate a PRN for payment",
                    expect_mode="workflow",
                    expect_workflow="Payment Assistance",
                    expect_reply_contains=["Payment Assistance"],
                )
            ],
        ),
        CXScenario(
            id="CX-05",
            category="Interactive Guided Workflow",
            title="Motor Vehicle Registration & Transfer Workflow",
            description="Guides motor vehicle ownership transfer process.",
            turns=[
                TurnStep(
                    user_message="Guide me through registering my imported car",
                    expect_mode="workflow",
                    expect_workflow="Motor Vehicle Registration",
                    expect_reply_contains=["Motor Vehicle Registration"],
                )
            ],
        ),
        CXScenario(
            id="CX-06",
            category="Interactive Guided Workflow",
            title="VAT Monthly Return Filing Guidance",
            description="Guides step-by-step return filing on the eTax portal.",
            turns=[
                TurnStep(
                    user_message="Walk me through filing my VAT return",
                    expect_mode="workflow",
                    expect_workflow="Return Filing",
                    expect_reply_contains=["Return Filing"],
                )
            ],
        ),
        CXScenario(
            id="CX-07",
            category="Interactive Guided Workflow",
            title="Customs Import Entry & IM4 Clearance Workflow",
            description="Guides an importer through customs declarations and single window clearance.",
            turns=[
                TurnStep(
                    user_message="Guide me through customs clearance",
                    expect_mode="workflow",
                    expect_workflow="Customs Clearance",
                    expect_reply_contains=["Customs Clearance"],
                )
            ],
        ),
        CXScenario(
            id="CX-08",
            category="Interactive Guided Workflow",
            title="Formal Tax Assessment Objection / Dispute Filing",
            description="Guides an aggrieved taxpayer through lodging an objection within statutory timelines.",
            turns=[
                TurnStep(
                    user_message="Help me file an objection against an unfair tax assessment",
                    expect_mode="workflow",
                    expect_workflow="Objection or Dispute",
                    expect_reply_contains=["Objection"],
                )
            ],
        ),
        CXScenario(
            id="CX-09",
            category="Interactive Guided Workflow",
            title="Bonded Warehouse (BWIMS) IM7 Cargo Guidance",
            description="Assists warehouse operators and importers on bonded goods tracking.",
            turns=[
                TurnStep(
                    user_message="I need guidance on bonded warehouse goods under IM7",
                    expect_reply_regex=[r"(?:warehouse|bonded|im7|customs|clearance)"],
                )
            ],
        ),
        CXScenario(
            id="CX-10",
            category="Interactive Guided Workflow",
            title="EFRIS Invoice & Compliance Audit Checklist",
            description="Guides business on fiscal invoice compliance and checklist before issuing receipts.",
            turns=[
                TurnStep(
                    user_message="Guide me to check if my invoice complies with EFRIS rules",
                    expect_reply_regex=[r"(?:efris|invoice|receipt|fdn|compliance)"],
                    expect_resources_min=1,
                )
            ],
        ),

        # =====================================================================
        # PILLAR 2: Deterministic Tax Computations & Net Take-Home (8 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-11",
            category="Deterministic Tax Computation",
            title="Standard Resident PAYE on 5,000,000 UGX Monthly Salary",
            description="Computes exact progressive brackets, tax payable, and net take-home.",
            turns=[
                TurnStep(
                    user_message="Calculate my PAYE on a gross monthly salary of 5,000,000 UGX",
                    expect_mode="calculator",
                    expect_reply_contains=["1,388,250", "3,611,750", "FY2026-27"],
                )
            ],
        ),
        CXScenario(
            id="CX-12",
            category="Deterministic Tax Computation",
            title="High-Income PAYE on 12,000,000 UGX (40% Super Bracket)",
            description="Calculates PAYE including the 40% top bracket for monthly income above 10M UGX.",
            turns=[
                TurnStep(
                    user_message="How much PAYE will I pay on 12,000,000 UGX salary per month?",
                    expect_mode="calculator",
                    expect_reply_contains=["40%", "FY2026-27"],
                    expect_reply_regex=[r"(?:3,\d{3},\d{3}|take-home|net)"],
                )
            ],
        ),
        CXScenario(
            id="CX-13",
            category="Deterministic Tax Computation",
            title="Non-Resident Employee PAYE Calculation (3,000,000 UGX)",
            description="Verifies flat bracket treatment for non-resident employees in Uganda.",
            turns=[
                TurnStep(
                    user_message="Calculate PAYE for a non-resident employee earning 3,000,000 UGX gross monthly",
                    expect_mode="calculator",
                    expect_reply_contains=["non-resident"],
                )
            ],
        ),
        CXScenario(
            id="CX-14",
            category="Deterministic Tax Computation",
            title="Standard 18% VAT Addition on 25,000,000 UGX Supply",
            description="Computes 18% VAT and total invoice amount payable.",
            turns=[
                TurnStep(
                    user_message="Calculate 18% VAT on goods worth 25,000,000 UGX",
                    expect_mode="calculator",
                    expect_reply_contains=["4,500,000", "29,500,000"],
                )
            ],
        ),
        CXScenario(
            id="CX-15",
            category="Deterministic Tax Computation",
            title="VAT-Inclusive Extraction from Gross Total (59,000,000 UGX)",
            description="Extracts net amount and VAT component from a gross inclusive sum.",
            turns=[
                TurnStep(
                    user_message="How much VAT is included in 59,000,000 UGX?",
                    expect_mode="calculator",
                    expect_reply_contains=["9,000,000", "50,000,000"],
                )
            ],
        ),
        CXScenario(
            id="CX-16",
            category="Deterministic Tax Computation",
            title="Individual Rental Income Tax on 30,000,000 UGX Annual Rent",
            description="Calculates 12% rental tax with 2,820,000 UGX statutory deduction.",
            turns=[
                TurnStep(
                    user_message="Calculate individual rental tax on 30,000,000 UGX annual rental income",
                    expect_mode="calculator",
                    expect_reply_regex=[r"(?:12%|rental|3,261,600|2,820,000)"],
                )
            ],
        ),
        CXScenario(
            id="CX-17",
            category="Deterministic Tax Computation",
            title="Resident Professional Fees Withholding Tax (15,000,000 UGX)",
            description="Computes 6% withholding tax deduction and net payment to service provider.",
            turns=[
                TurnStep(
                    user_message="Calculate withholding tax on an invoice of 15,000,000 UGX for consultancy services to a resident company",
                    expect_mode="calculator",
                    expect_reply_contains=["6%", "900,000", "14,100,000"],
                )
            ],
        ),
        CXScenario(
            id="CX-18",
            category="Deterministic Tax Computation",
            title="Commercial Building Sale Capital Gains Tax",
            description="Calculates capital gains tax on commercial property (Cost: 100M, Sale: 160M).",
            turns=[
                TurnStep(
                    user_message="Calculate capital gains tax on a commercial warehouse bought for 100,000,000 UGX and sold for 160,000,000 UGX",
                    expect_mode="calculator",
                    expect_reply_contains=["60,000,000"],
                    expect_reply_regex=[r"(?:capital gain|profit|30%|18,000,000)"],
                )
            ],
        ),

        # =====================================================================
        # PILLAR 3: Complex Narrative Stories & Situational Problem-Solving (10 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-19",
            category="Narrative Story Resolution",
            title="Small Bakery EFRIS Enforcement Threat & 10M Penalty Panic",
            description="Bakery owner in Mukono with money sent from sister in UK faces 10M penalty threat under 35M turnover.",
            turns=[
                TurnStep(
                    user_message=(
                        "I started a small bakery in Mukono 8 months ago, using my personal savings and some money sent by my sister in the UK. "
                        "Last week, a field officer visited and told me I must use EFRIS and threatened me with a 10 million penalty because I don't have electronic receipts. "
                        "I have not even reached 35 million in total annual sales. What should I do right now?"
                    ),
                    expect_reply_regex=[r"(?:efris|vat|threshold|penalty|objection|ura)"],
                    expect_resources_min=1,
                )
            ],
        ),
        CXScenario(
            id="CX-20",
            category="Narrative Story Resolution",
            title="Stranded Imported Car at Malaba & Rogue Clearing Agent",
            description="Importer in Malaba with rogue clearing agent facing imminent 14-day auction notice on imported car.",
            turns=[
                TurnStep(
                    user_message=(
                        "I imported a 2017 car through Mombasa and it arrived at Malaba customs. "
                        "My clearing agent took 8 million shillings from me and disappeared with the papers. "
                        "Now the bonded warehouse manager says my vehicle has only 14 days before it gets auctioned. "
                        "How do I clear the vehicle myself with URA, compute the duties, and stop the auction?"
                    ),
                    expect_reply_regex=[r"(?:customs|duty|warehouse|prn|ura|agent)"],
                    expect_resources_min=1,
                )
            ],
        ),
        CXScenario(
            id="CX-21",
            category="Narrative Story Resolution",
            title="Remote Software Engineer Receiving Foreign USD Remittances",
            description="Freelancer in Jinja receiving USD payments via Wise from German company asked by URA about bank credits.",
            turns=[
                TurnStep(
                    user_message=(
                        "I am a software engineer in Jinja freelancing remotely for tech companies in Germany and the US. "
                        "They wire money to my Equity Bank account in USD. URA sent me an inquiry asking about unexplained bank deposits. "
                        "Do I have to charge 18% VAT or is foreign service zero-rated, and how should I report this income?"
                    ),
                    expect_reply_regex=[r"(?:export|zero-rated|income|vat|deduct|ura)"],
                )
            ],
        ),
        CXScenario(
            id="CX-22",
            category="Narrative Story Resolution",
            title="Landlord-Tenant Agency Notice Dispute & Eviction Threat",
            description="Tenant whose rent was garnished by URA under Section 40 TPCA faces landlord lock-out.",
            turns=[
                TurnStep(
                    user_message=(
                        "My landlord locked my shop in Kampala because URA served an agency notice to my bank and deducted my rent payments directly to pay his tax arrears. "
                        "Now the landlord claims I never paid him rent and is threatening eviction. Am I legally protected by URA?"
                    ),
                    expect_reply_regex=[r"(?:agency\s+notice|section\s+40|indemnif|protect|rent)"],
                )
            ],
        ),
        CXScenario(
            id="CX-23",
            category="Narrative Story Resolution",
            title="Ancestral Agricultural Land Sale & Family Estate Dispute",
            description="Family in Mbarara selling grandfather's farm for school fees told URA will deduct 30% capital gains.",
            turns=[
                TurnStep(
                    user_message=(
                        "Our family in Mbarara recently sold 5 acres of ancestral farm land that belonged to our late grandfather to pay university tuition. "
                        "A local broker claimed URA will deduct 30% capital gains tax from our sale money. Is ancestral family land exempt from tax?"
                    ),
                    expect_reply_regex=[r"(?:capital\s+gains?|exempt|agricultural|business\s+asset|land)"],
                )
            ],
        ),
        CXScenario(
            id="CX-24",
            category="Narrative Story Resolution",
            title="Lake Victoria Fish Exporter Cross-Border EAC Question",
            description="Fisherman in Busia exporting Nile Perch to Kenya needs to know EAC export tax rules.",
            turns=[
                TurnStep(
                    user_message=(
                        "I buy fresh fish from fishermen in Busia and export it across the border to Kisumu, Kenya. "
                        "A customs official at the border post told me I need a phytosanitary certificate and to pay export VAT. "
                        "Are fish exports within the East African Community zero-rated for VAT?"
                    ),
                    expect_reply_regex=[r"(?:export|zero-rated|0%|eac|customs|vat)"],
                )
            ],
        ),
        CXScenario(
            id="CX-25",
            category="Narrative Story Resolution",
            title="Retail Hardware Double-Taxation Claim on Construction Materials",
            description="Hardware shop owner in Gulu bought cement with 18% VAT, then government client withheld 6% WHT and 18% VAT.",
            turns=[
                TurnStep(
                    user_message=(
                        "I supply cement to a government school in Gulu. The manufacturer charged me 18% VAT when I bought the cement. "
                        "When the school paid me, the district accounting officer deducted 6% withholding tax and 18% VAT. "
                        "Am I being double taxed, and how do I claim my input tax credit on e-Tax?"
                    ),
                    expect_reply_regex=[r"(?:input\s+tax|credit|withholding|etax|claim)"],
                )
            ],
        ),
        CXScenario(
            id="CX-26",
            category="Narrative Story Resolution",
            title="NGO Medical Clinic Receiving International Grant Funds",
            description="Charity clinic in Arua receiving USAID health grant worried about income tax liability.",
            turns=[
                TurnStep(
                    user_message=(
                        "We run a registered non-profit medical clinic in Arua providing free malaria treatment. "
                        "We just received a 100,000 USD grant from a charity in the Netherlands. "
                        "Does URA consider donor grant money taxable business income, and do we need a tax exemption certificate?"
                    ),
                    expect_reply_regex=[r"(?:exemption|exempt\s+organization|grant|income\s+tax|tin)"],
                )
            ],
        ),
        CXScenario(
            id="CX-27",
            category="Narrative Story Resolution",
            title="Mobile Money Agent Ledger & 0.5% Cash-Out Reconciliation",
            description="Agent in Wakiso confused whether 0.5% mobile money excise duty is part of income tax.",
            turns=[
                TurnStep(
                    user_message=(
                        "I operate a telecom mobile money booth in Wakiso. Every customer withdrawal pays 0.5% excise duty. "
                        "Now URA says I must file an annual income tax return. Is the 0.5% mobile money excise duty my final tax, or do I owe personal income tax on my agent commission?"
                    ),
                    expect_reply_regex=[r"(?:return|e-services|etax|e-returns|income\s+tax|due\s+date|ura)"],
                )
            ],
        ),
        CXScenario(
            id="CX-28",
            category="Narrative Story Resolution",
            title="Sole Proprietor Incorporating Business & Transferring Fleet",
            description="Business owner in Jinja converting to limited company worried about stamp duty on vehicle transfer.",
            turns=[
                TurnStep(
                    user_message=(
                        "I have been running my transport business as a sole proprietor for 5 years. I just incorporated a private limited company with URSB. "
                        "When I transfer my 3 delivery vans from my personal name to the company name, what stamp duty or transfer fees does URA charge?"
                    ),
                    expect_reply_regex=[r"(?:stamp\s+duty|transfer|motor\s+vehicle|fee|ursb)"],
                )
            ],
        ),

        # =====================================================================
        # PILLAR 4: Actionable Resource Delivery & Deep Portal Linking (5 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-29",
            category="Actionable Resource Delivery",
            title="Downloadable Return Templates & eTax Portal Hub",
            description="Delivers direct official return templates and login links.",
            turns=[
                TurnStep(
                    user_message="Where can I download the return filing form templates and log in?",
                    expect_resources_min=1,
                    expect_reply_contains=["https://ura.go.ug"],
                )
            ],
        ),
        CXScenario(
            id="CX-30",
            category="Actionable Resource Delivery",
            title="EFRIS Fiscal Invoicing Portal & FDN Verification Tool",
            description="Delivers official EFRIS portal login and Fiscal Document Number validation resource.",
            turns=[
                TurnStep(
                    user_message="I need the direct portal link to verify an EFRIS receipt FDN and login to my EFRIS account",
                    expect_resources_min=1,
                    expect_reply_regex=[r"(?:efris|fdn|ura\.go\.ug)"],
                )
            ],
        ),
        CXScenario(
            id="CX-31",
            category="Actionable Resource Delivery",
            title="e-Services Make a Payment & Bank PRN Slip Delivery",
            description="Returns direct payment portal links for generating and clearing PRNs.",
            turns=[
                TurnStep(
                    user_message="Provide me with the official URA links to pay my tax assessment and generate a PRN",
                    expect_resources_min=1,
                    expect_reply_regex=[r"(?:prn|payment|etax|ura\.go\.ug)"],
                )
            ],
        ),
        CXScenario(
            id="CX-32",
            category="Actionable Resource Delivery",
            title="Motor Vehicle Transfer Form & Logbook Search Portal",
            description="Provides verified links for motor vehicle transfer and logbook verification.",
            turns=[
                TurnStep(
                    user_message="Where do I access the motor vehicle ownership transfer form and verify a logbook online?",
                    expect_resources_min=1,
                    expect_reply_regex=[r"(?:motor\s+vehicle|transfer|logbook|ura\.go\.ug)"],
                )
            ],
        ),
        CXScenario(
            id="CX-33",
            category="Actionable Resource Delivery",
            title="Withholding Tax Exemption Application Portal & Guidelines",
            description="Provides resources for applying for a 6% WHT exemption certificate.",
            turns=[
                TurnStep(
                    user_message="Give me the link and application procedure to apply for withholding tax exemption certificate",
                    expect_resources_min=1,
                    expect_reply_regex=[r"(?:withholding|exemption|certificate|etax)"],
                )
            ],
        ),

        # =====================================================================
        # PILLAR 5: Empathetic Crisis De-escalation & Enforcement Protection (5 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-34",
            category="Empathetic Crisis Guidance",
            title="Emergency Bank Freeze & Third-Party Agency Notice Relief",
            description="Validates panic and outlines legal rights under Section 40 TPCA.",
            turns=[
                TurnStep(
                    user_message="I am terrified! URA issued an agency notice on my bank account and froze my funds. What can I do?",
                    expect_reply_contains=["Third-Party Agency Notice"],
                    expect_reply_regex=[r"(?:sorry|understand|options|contact)"],
                )
            ],
        ),
        CXScenario(
            id="CX-35",
            category="Empathetic Crisis Guidance",
            title="Late Filing Penalty Panic & Voluntary Disclosure Relief",
            description="De-escalates anxiety about overdue penalties and explains waiver procedures.",
            turns=[
                TurnStep(
                    user_message="I missed the filing deadline and I cannot afford these heavy penalties, please help me out!",
                    expect_reply_regex=[r"(?:penalty|waiver|voluntary disclosure|0800)"],
                )
            ],
        ),
        CXScenario(
            id="CX-36",
            category="Empathetic Crisis Guidance",
            title="Bribery Attempt & Fraudulent Officer Reporting (Integrity Line)",
            description="Directs taxpayer facing extortion to URA Internal Affairs and toll-free integrity lines.",
            turns=[
                TurnStep(
                    user_message="A man claiming to be a URA revenue officer says if I don't give him 2 million in cash right now, he will shut down my clinic. How do I report this?",
                    expect_reply_regex=[r"(?:0800|toll-free|whistleblow|integrity|internal\s+affairs|cash)"],
                )
            ],
        ),
        CXScenario(
            id="CX-37",
            category="Empathetic Crisis Guidance",
            title="Distress Warrant & Court Bailiff Seizure Fear",
            description="Advises on legal procedures under Section 41 TPCA when threatened with bailiffs.",
            turns=[
                TurnStep(
                    user_message="Court bailiffs arrived with a URA distress warrant threatening to carry away my printing machines tomorrow morning. What legal rights do I have?",
                    expect_reply_regex=[r"(?:distress\s+warrant|section\s+41|objection|memorandum|ura)"],
                )
            ],
        ),
        CXScenario(
            id="CX-38",
            category="Empathetic Crisis Guidance",
            title="Taxpayer Requests Immediate Human Officer Escalation",
            description="Connects taxpayer to human officer queue when requested.",
            turns=[
                TurnStep(
                    user_message="I need to talk to a human URA officer right now regarding my audit dispute.",
                    expect_escalation=True,
                    expect_reply_regex=[r"(?:officer|ticket|human|review)"],
                )
            ],
        ),

        # =====================================================================
        # PILLAR 6: Closed-Loop Conversational Bug & Knowledge Reporting (4 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-39",
            category="Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Outdated VAT Rate",
            description="Emits Knowledge Report #KB-XXX when taxpayer pushes back on rate.",
            turns=[
                TurnStep(
                    user_message="What is the VAT rate in Uganda?",
                    expect_reply_contains=["VAT"],
                ),
                TurnStep(
                    user_message="No, that is incorrect. Under the 2023 Amendment Act, the rate was changed to 6% instead of 18%.",
                    expect_discrepancy=True,
                    expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)", r"6%"],
                ),
            ],
        ),
        CXScenario(
            id="CX-40",
            category="Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Mandatory VAT Registration Threshold",
            description="Emits discrepancy ticket when user asserts new threshold.",
            turns=[
                TurnStep(
                    user_message="What is the annual threshold to register for VAT?",
                    expect_reply_contains=["turnover"],
                ),
                TurnStep(
                    user_message="Actually, that is outdated. The law was updated recently and the threshold is no longer what you said.",
                    expect_discrepancy=True,
                    expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"],
                ),
            ],
        ),
        CXScenario(
            id="CX-41",
            category="Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Repealed Motor Vehicle Form Number",
            description="Captures user dispute on procedural form numbers.",
            turns=[
                TurnStep(
                    user_message="Which form do I fill to transfer a car?",
                    expect_reply_contains=["transfer"],
                ),
                TurnStep(
                    user_message="You are wrong, that form was abolished and replaced by an online e-service with no paper form.",
                    expect_discrepancy=True,
                    expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"],
                ),
            ],
        ),
        CXScenario(
            id="CX-42",
            category="Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Withholding Tax Rate on Rental Income",
            description="Logs discrepancy ticket when taxpayer disputes withholding percentage.",
            turns=[
                TurnStep(
                    user_message="What is the withholding tax rate on dividends?",
                    expect_reply_contains=["15%"],
                ),
                TurnStep(
                    user_message="That is wrong, under the new amendment it is 10% instead of 15%.",
                    expect_discrepancy=True,
                    expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"],
                ),
            ],
        ),

        # =====================================================================
        # PILLAR 7: Multilingual Task Fulfillment (Luganda & Swahili) (5 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-43",
            category="Multilingual Task Fulfillment",
            title="Luganda TIN Guidance & Step Breakdown",
            description="Responds natively in Luganda explaining step-by-step TIN registration.",
            locale="lg",
            turns=[
                TurnStep(
                    user_message="Nnyamba okufuna TIN yange ey'obuntu, nkoze ntya?",
                    expect_reply_contains=["TIN"],
                    expect_reply_regex=[r"(?:omukutu|foomu|NIN|e-Services|ura)"],
                )
            ],
        ),
        CXScenario(
            id="CX-44",
            category="Multilingual Task Fulfillment",
            title="Luganda PAYE Take-Home Salary Explanation",
            description="Explains PAYE salary deduction natively in Luganda.",
            locale="lg",
            turns=[
                TurnStep(
                    user_message="Bansasula emitwalo amakumi ataano (500,000 UGX) buli mwezi. Nsasula omusolo gwa PAYE mmeka?",
                    expect_reply_regex=[r"(?:paye|omusolo|ugx|ura|emitwalo)"],
                )
            ],
        ),
        CXScenario(
            id="CX-45",
            category="Multilingual Task Fulfillment",
            title="Luganda EFRIS Guidance for Small Market Vendors",
            description="Guides a Owino market vendor in Luganda regarding EFRIS electronic receipts.",
            locale="lg",
            turns=[
                TurnStep(
                    user_message="Nnina edduuka mu katale k'e Nakasero. Nteekwa okukozesa EFRIS okukola lisiti?",
                    expect_reply_regex=[r"(?:efris|lisiti|omusolo|katale|ura)"],
                )
            ],
        ),
        CXScenario(
            id="CX-46",
            category="Multilingual Task Fulfillment",
            title="Swahili 18% VAT Mathematical Calculation",
            description="Calculates 18% VAT on 10M UGX and replies in Kiswahili with exact figures.",
            locale="sw",
            turns=[
                TurnStep(
                    user_message="Hesabu kiasi cha VAT ya 18% kwa bidhaa za thamani ya shilingi 10,000,000 UGX",
                    expect_mode="calculator",
                    expect_reply_contains=["1,800,000"],
                )
            ],
        ),
        CXScenario(
            id="CX-47",
            category="Multilingual Task Fulfillment",
            title="Swahili Customs Clearance Guidance for East African Trader",
            description="Advises cross-border trader in Kiswahili regarding customs clearance.",
            locale="sw",
            turns=[
                TurnStep(
                    user_message="Ninaingiza bidhaa kutoka Kenya kupitia mpaka wa Malaba. Nawezaje kulipa ushuru wa forodha na kupata kibali?",
                    expect_reply_regex=[r"(?:forodha|ushuru|malaba|ura|prn)"],
                )
            ],
        ),

        # =====================================================================
        # PILLAR 8: Statutory Edge Cases & Exemption Boundaries (3 Scenarios)
        # =====================================================================
        CXScenario(
            id="CX-48",
            category="Statutory Boundary Probing",
            title="Turnover Threshold Boundary Probe (149M vs 150M UGX)",
            description="Tests advice for business on the exact threshold of mandatory VAT registration.",
            turns=[
                TurnStep(
                    user_message="My hardware business earned 149,000,000 UGX in the last 12 months. Am I legally required to register for VAT tomorrow?",
                    expect_reply_regex=[r"(?:150|million|threshold|mandatory|voluntary)"],
                )
            ],
        ),
        CXScenario(
            id="CX-49",
            category="Statutory Boundary Probing",
            title="Unprocessed Agricultural Produce vs Packaged Supply Exemption",
            description="Distinguishes between raw exempt farm produce and taxable processed food supplies.",
            turns=[
                TurnStep(
                    user_message="Do I charge 18% VAT on selling raw fresh cassava from my farm versus selling packaged cassava flour in a supermarket?",
                    expect_reply_regex=[r"(?:exempt|unprocessed|processed|18%|vat|second schedule)"],
                )
            ],
        ),
        CXScenario(
            id="CX-50",
            category="Statutory Boundary Probing",
            title="Zero-Rated Goods vs Exempt Supplies Distinction",
            description="Clarifies whether input tax credits can be claimed on zero-rated versus exempt transactions.",
            turns=[
                TurnStep(
                    user_message="What is the practical difference between zero-rated VAT supplies and exempt supplies when claiming input tax credits?",
                    expect_reply_regex=[r"(?:input\s+tax|credit|claim|zero-rated|exempt|0%)"],
                )
            ],
        ),
    ]


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
    parser = argparse.ArgumentParser(description="Evaluate 50 Customer Experience & Request Completion Scenarios over live ngrok.")
    parser.add_argument("--base", default="", help="Base API URL (e.g. https://<tunnel>/api or http://localhost:8083)")
    parser.add_argument("--out", default="", help="Path to save JSON evaluation report")
    args = parser.parse_args()

    base_url = args.base.strip()
    if not base_url:
        base_url = find_active_endpoint()

    print("=" * 85)
    print("  URA ASSISTANT — 50-SCENARIO CUSTOMER EXPERIENCE & REQUEST COMPLETION BENCHMARK")
    print(f"  Target Endpoint: {base_url}")
    print("=" * 85)

    session = requests.Session()
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        hr = session.get(health_url, headers=HEADERS, timeout=10)
        print(f"✓ Health probe: HTTP {hr.status_code} ({hr.text[:60]})\n")
    except Exception as e:
        print(f"⚠ Warning: Health probe failed ({e}). Proceeding...\n")

    scenarios = build_50_scenarios()
    print(f"Evaluating {len(scenarios)} Customer Experience Scenarios across 8 Pillars...\n")

    scenario_results = []
    category_stats: dict[str, dict[str, int]] = {}
    latencies: list[float] = []

    for i, sc in enumerate(scenarios, 1):
        print(f"[{i:02d}/{len(scenarios):02d}] {sc.id:<10} | {sc.category[:22]:<22} | {sc.title[:38]:<38} ... ", end="", flush=True)
        res = run_scenario(session, base_url, sc)
        scenario_results.append(res)
        latencies.append(res["total_time_s"])

        cat = sc.category
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "passed": 0}
        category_stats[cat]["total"] += 1
        if res["passed"]:
            category_stats[cat]["passed"] += 1
            print(f"PASS ({res['total_time_s']}s)")
        else:
            print(f"FAIL ({res['total_time_s']}s)")
            for t in res["turns"]:
                if not t.get("passed"):
                    for f in t.get("failures", []):
                        print(f"       -> [Turn {t.get('turn')}] {f}")

    total_scenarios = len(scenario_results)
    passed_scenarios = sum(1 for s in scenario_results if s["passed"])
    cx_score = round((passed_scenarios / total_scenarios) * 100, 1)

    latencies_sorted = sorted(latencies)
    p50_latency = round(latencies_sorted[int(len(latencies_sorted) * 0.50)], 2)
    p95_latency = round(latencies_sorted[min(int(len(latencies_sorted) * 0.95), len(latencies_sorted) - 1)], 2)
    avg_latency = round(sum(latencies) / len(latencies), 2)

    print("\n" + "=" * 85)
    print("  CUSTOMER EXPERIENCE & REQUEST COMPLETION SCORECARD (50 SCENARIOS)")
    print("=" * 85)
    for cat, stat in category_stats.items():
        pct = round((stat["passed"] / stat["total"]) * 100, 1)
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        print(f"  {cat:<35} : [{bar}] {stat['passed']:>2}/{stat['total']:<2} ({pct:>5.1f}%)")

    print("-" * 85)
    print(f"  OVERALL CX REQUEST COMPLETION SCORE : {passed_scenarios}/{total_scenarios} ({cx_score}%)")
    print(f"  LATENCY METRICS (Live Endpoint)      : Avg: {avg_latency}s | P50: {p50_latency}s | P95: {p95_latency}s")
    print("=" * 85)

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
        out_path = str(out_dir / f"cx_50_scenarios_eval_{today}.json")

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report_data, fh, indent=2, ensure_ascii=False)
    print(f"\n✓ 50-Scenario Evaluation Audit Report saved to: {out_path}\n")

    return 0 if cx_score >= 80 else 1


if __name__ == "__main__":
    sys.exit(main())
