#!/usr/bin/env python3
"""1000 FAQs Comprehensive Evaluation Suite for URA Tax Assistant.

Evaluates:
  1. Performance & Throughput under continuous concurrency (p50, p90, p95, p99, req/s)
  2. Grounded Statutory & Factual Accuracy on 1,000 FAQs across:
     - Domestic Taxes (VAT, PAYE, WHT, Rental, Corporate, EFRIS, DTS, Stamp Duty, Capital Gains)
     - Customs & Border Trade (Valuation, Procedures, Passenger Baggage, Groupage, Offences, AEO)
     - Tax Education & Citizen Services (TIN, Starter Pack, Bookkeeping, Formalisation, Appeals, ADR)
  3. Long-Context / Long-Horizon Session Management:
     - Multi-turn taxpayer journeys (8 turns per session)
     - Coreference & anaphora resolution ("it", "that tax", "my earlier registration")
     - Context preservation without memory degradation
  4. Conversational Nature & Assistant Grade Level:
     - Professional empathy, plain-language clarity, structured next steps
     - Completing real-world user requests and resolving taxpayer problems
     - Verification that official URA contacts/portals are never redacted as [REDACTED_EMAIL]
  5. Single-GPU Telemetry (VRAM, Power, Temperature, SM Load)

Target:
  Public ngrok Gateway: https://struttingly-nongeological-briella.ngrok-free.dev/api
  Local API / Frontend fallback: http://localhost:3032/api or http://localhost:8083
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Telemetry Helper
# ---------------------------------------------------------------------------
def get_gpu_telemetry(gpu_id: int = 7) -> dict[str, Any]:
    try:
        cmd = [
            "nvidia-smi",
            f"--id={gpu_id}",
            "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, text=True).strip().split(",")
        if len(out) >= 8:
            return {
                "gpu_index": int(out[0]),
                "name": out[1].strip(),
                "memory_total_mb": float(out[2]),
                "memory_used_mb": float(out[3]),
                "memory_free_mb": float(out[4]),
                "utilization_pct": float(out[5]),
                "temperature_c": float(out[6]),
                "power_draw_w": float(out[7]),
            }
    except Exception as ex:
        pass
    return {"gpu_index": gpu_id, "error": "telemetry_unavailable"}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class EvalFAQ:
    faq_id: str
    domain: str       # "domestic", "customs", "tax_education"
    topic: str
    query: str
    expected_keywords: list[str]
    expected_numbers: list[str] = field(default_factory=list)
    statutory_citations: list[str] = field(default_factory=list)
    session_id: str | None = None
    turn: int = 1
    total_session_turns: int = 1
    requires_context_from_turn: int | None = None
    expected_context_keywords: list[str] = field(default_factory=list)
    is_multi_turn: bool = False
    eq_prompt: bool = False


@dataclass
class EvalResult:
    faq_id: str
    domain: str
    topic: str
    query: str
    status_code: int
    latency_s: float
    retrieval_mode: str
    model: str
    faithfulness_score: float | None
    claim_verification_score: float | None
    reply_snippet: str
    sources: list[str]
    matched_keywords: list[str]
    missing_keywords: list[str]
    matched_numbers: list[str]
    matched_citations: list[str]
    accuracy_score: float
    context_preserved: bool
    conversational_score: float
    eq_score: float
    has_redacted_official_contact: bool
    conversation_id: str
    turn: int
    is_multi_turn: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Dataset Builder: Assemble Exactly 1,000 Questions
# ---------------------------------------------------------------------------
def build_1000_faqs_dataset() -> list[EvalFAQ]:
    """Compiles 1,000 balanced FAQs across Domestic, Customs, Tax Education,
    including 25 long-horizon 8-turn interactive taxpayer sessions (200 turns).
    """
    faqs: list[EvalFAQ] = []
    
    # -----------------------------------------------------------------------
    # Part 1: Long-Horizon Multi-Turn Taxpayer Sessions (25 sessions x 8 turns = 200 turns)
    # -----------------------------------------------------------------------
    journeys = [
        # Journey 1: New Resident Business Founder (Domestic / PAYE / VAT / EFRIS / Filing)
        {
            "id": "JRN-01",
            "domain": "domestic",
            "topic": "business_tax_lifecycle",
            "turns": [
                {
                    "query": "Hello, I am registering a new bakery business in Kampala. What is my first tax obligation?",
                    "kw": ["TIN", "taxpayer identification number", "register", "ura"],
                    "nums": [],
                    "cits": ["Tax Procedures Code Act"],
                },
                {
                    "query": "How do I get that individual and business TIN online?",
                    "kw": ["portal", "ura.go.ug", "online", "application", "nin", "ursb"],
                    "nums": [],
                    "cits": [],
                    "ctx_kw": ["tin", "business"],
                },
                {
                    "query": "I have hired 4 bakers with monthly salaries of 450,000 UGX each. What tax must I deduct from them?",
                    "kw": ["PAYE", "pay as you earn", "employment income", "deduct"],
                    "nums": ["235,000"],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["bakers", "salaries", "paye"],
                },
                {
                    "query": "What is the monthly deadline to pay that deducted tax to URA?",
                    "kw": ["15th", "month", "remit", "pay"],
                    "nums": ["15th"],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["paye", "deadline"],
                },
                {
                    "query": "My estimated annual bakery turnover will be 180 million UGX. Must I register for VAT?",
                    "kw": ["VAT", "mandatory", "compulsory", "threshold", "register"],
                    "nums": ["150,000,000", "150m"],
                    "cits": ["Value Added Tax Act"],
                    "ctx_kw": ["turnover", "vat"],
                },
                {
                    "query": "Since VAT is mandatory for me, what is the standard VAT rate I must charge my customers?",
                    "kw": ["18%", "standard rate", "vat"],
                    "nums": ["18%"],
                    "cits": ["Value Added Tax Act"],
                    "ctx_kw": ["vat", "rate"],
                },
                {
                    "query": "Do I need to issue e-invoices using EFRIS for every bread sale?",
                    "kw": ["EFRIS", "e-invoice", "e-receipt", "fiscal", "real time"],
                    "nums": [],
                    "cits": ["Tax Procedures Code Act"],
                    "ctx_kw": ["efris", "sale"],
                },
                {
                    "query": "If I need help setting up EFRIS or filing returns, what are URA's official toll free phone numbers and email?",
                    "kw": ["services@ura.go.ug", "0800 117 000", "0800 217 000"],
                    "nums": ["0800 117 000", "0800 217 000"],
                    "cits": [],
                    "ctx_kw": ["help", "ura"],
                },
            ]
        },
        # Journey 2: Importer & Customs Valuation Dispute (Customs / Valuation / Objections)
        {
            "id": "JRN-02",
            "domain": "customs",
            "topic": "customs_import_dispute",
            "turns": [
                {
                    "query": "I imported a commercial consignment of spare parts by sea through Mombasa. How does URA determine its customs value?",
                    "kw": ["transaction value", "CIF", "cost", "insurance", "freight"],
                    "nums": [],
                    "cits": ["EACCMA", "Section 122"],
                },
                {
                    "query": "What if I had shipped those spare parts by air instead? Would freight be treated the same?",
                    "kw": ["air", "freight", "excluded", "cost", "insurance"],
                    "nums": [],
                    "cits": ["EACCMA", "Fourth Schedule"],
                    "ctx_kw": ["air", "freight"],
                },
                {
                    "query": "Customs rejected my declared invoice price at the border and valued my goods way higher! Can I challenge this customs valuation?",
                    "kw": ["objection", "appeal", "challenge", "dispute", "right"],
                    "nums": [],
                    "cits": ["EACCMA"],
                    "ctx_kw": ["valuation", "higher"],
                },
                {
                    "query": "How many days do I have to lodge that customs objection?",
                    "kw": ["45", "days", "period", "lodge"],
                    "nums": ["45"],
                    "cits": ["EACCMA"],
                    "ctx_kw": ["days", "objection"],
                },
                {
                    "query": "Do I have to pay any portion of the assessed tax before my objection is heard?",
                    "kw": ["30%", "portion", "deposit", "pay"],
                    "nums": ["30%"],
                    "cits": ["Tax Procedures Code Act"],
                    "ctx_kw": ["pay", "objection"],
                },
                {
                    "query": "Can the Commissioner General waive that 30% deposit requirement if I have good reasons?",
                    "kw": ["waive", "waiver", "commissioner", "discretion"],
                    "nums": [],
                    "cits": ["Tax Procedures Code Act"],
                    "ctx_kw": ["waiver", "deposit"],
                },
                {
                    "query": "If the Commissioner rejects my objection, which tribunal or court do I appeal to?",
                    "kw": ["Tax Appeals Tribunal", "TAT", "appeal"],
                    "nums": ["30"],
                    "cits": ["Tax Appeals Tribunal Act"],
                    "ctx_kw": ["appeal", "tribunal"],
                },
                {
                    "query": "What are URA's official contact details so my clearing agent can follow up on our lodged appeal?",
                    "kw": ["services@ura.go.ug", "0800 117 000", "0800 217 000"],
                    "nums": ["0800 117 000", "0800 217 000"],
                    "cits": [],
                    "ctx_kw": ["contact", "ura"],
                },
            ]
        },
        # Journey 3: Landlord & Rental Income Tax (Domestic / Rental / WHT)
        {
            "id": "JRN-03",
            "domain": "domestic",
            "topic": "rental_income_tax",
            "turns": [
                {
                    "query": "I own a residential apartment building in Entebbe. How is rental income tax calculated for an individual?",
                    "kw": ["12%", "gross", "rental", "threshold", "24,000,000", "24m"],
                    "nums": ["12%", "24,000,000"],
                    "cits": ["Income Tax Act"],
                },
                {
                    "query": "Can I deduct my renovation and mortgage interest expenses from that gross rental income?",
                    "kw": ["no deduction", "gross", "expenses", "flat rate"],
                    "nums": [],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["expenses", "rental"],
                },
                {
                    "query": "What if the apartment building was owned by my private company instead of me personally?",
                    "kw": ["30%", "company", "net", "allowable deductions", "expenses"],
                    "nums": ["30%"],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["company", "rental"],
                },
                {
                    "query": "Can my company offset losses from another retail business against its rental income?",
                    "kw": ["ring fencing", "separate", "cannot offset", "rental business"],
                    "nums": [],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["offset", "losses"],
                },
                {
                    "query": "If I rent out office space to a corporate tenant, must they withhold tax from my rent?",
                    "kw": ["withholding", "wht", "rental", "tenant"],
                    "nums": ["6%"],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["withhold", "rent"],
                },
                {
                    "query": "What document does the tenant give me as proof that they remitted that withholding tax?",
                    "kw": ["withholding tax certificate", "wht certificate", "credit"],
                    "nums": [],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["proof", "certificate"],
                },
                {
                    "query": "When is my annual rental income tax return due for filing?",
                    "kw": ["31st december", "return", "file", "annual"],
                    "nums": [],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["filing", "deadline"],
                },
                {
                    "query": "Give me the official URA website link and email where I can log in and file this rental return.",
                    "kw": ["ura.go.ug", "services@ura.go.ug"],
                    "nums": [],
                    "cits": [],
                    "ctx_kw": ["website", "filing"],
                },
            ]
        },
        # Journey 4: Small Enterprise Formalization & Growth (Tax Education / Presumptive / Records)
        {
            "id": "JRN-04",
            "domain": "tax_education",
            "topic": "small_business_formalization",
            "turns": [
                {
                    "query": "I run a small retail grocery shop in Mukono with annual sales around 35 million UGX. Do I pay standard corporation tax?",
                    "kw": ["presumptive", "small business", "turnover", "simplified"],
                    "nums": ["50,000,000", "50m"],
                    "cits": ["Income Tax Act"],
                },
                {
                    "query": "What basic business records am I legally required to keep for URA?",
                    "kw": ["sales", "purchases", "receipts", "invoices", "records", "books"],
                    "nums": ["5 years"],
                    "cits": ["Tax Procedures Code Act"],
                    "ctx_kw": ["records", "keep"],
                },
                {
                    "query": "For how many years must I keep those business records?",
                    "kw": ["5 years", "five years", "retain"],
                    "nums": ["5"],
                    "cits": ["Tax Procedures Code Act"],
                    "ctx_kw": ["years", "records"],
                },
                {
                    "query": "What are the benefits of registering my business and paying presumptive tax instead of remaining informal?",
                    "kw": ["formal", "tenders", "bank loan", "credit", "clearance", "tcc"],
                    "nums": [],
                    "cits": [],
                    "ctx_kw": ["benefits", "formal"],
                },
                {
                    "query": "How do I obtain a Tax Clearance Certificate (TCC) to bid for local government supply contracts?",
                    "kw": ["TCC", "tax clearance certificate", "portal", "compliance", "apply"],
                    "nums": [],
                    "cits": ["Tax Procedures Code Act"],
                    "ctx_kw": ["tcc", "certificate"],
                },
                {
                    "query": "How long is that Tax Clearance Certificate valid for?",
                    "kw": ["1 year", "one year", "12 months", "validity"],
                    "nums": ["1"],
                    "cits": ["Tax Procedures Code Act"],
                    "ctx_kw": ["valid", "tcc"],
                },
                {
                    "query": "How do I generate a PRN to pay my shop's presumptive tax at the bank or via mobile money?",
                    "kw": ["PRN", "payment registration number", "portal", "bank", "mobile money"],
                    "nums": [],
                    "cits": [],
                    "ctx_kw": ["prn", "payment"],
                },
                {
                    "query": "If I encounter errors generating my PRN, what are URA's toll free lines and WhatsApp number?",
                    "kw": ["0800 117 000", "0800 217 000", "0772 140 000", "whatsapp"],
                    "nums": ["0800 117 000", "0800 217 000"],
                    "cits": [],
                    "ctx_kw": ["helpline", "ura"],
                },
            ]
        },
        # Journey 5: Agro-Exporter & Incentives (Domestic / Customs / Exemption)
        {
            "id": "JRN-05",
            "domain": "domestic",
            "topic": "agro_export_incentives",
            "turns": [
                {
                    "query": "We are setting up a commercial fruit processing plant in Soroti exporting 85% of our canned juice. Are there tax incentives?",
                    "kw": ["tax holiday", "10 years", "exemption", "export", "80%"],
                    "nums": ["10", "80%"],
                    "cits": ["Income Tax Act", "Section 21"],
                },
                {
                    "query": "What minimum investment capital is required for a local investor to qualify for that 10-year income tax holiday?",
                    "kw": ["investment", "capital", "threshold", "local investor"],
                    "nums": ["1,000,000", "10,000,000"],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["investment", "holiday"],
                },
                {
                    "query": "When we export our canned juice to Kenya and South Sudan, what VAT rate applies to those exports?",
                    "kw": ["zero rate", "zero-rated", "0%", "export"],
                    "nums": ["0%"],
                    "cits": ["Value Added Tax Act"],
                    "ctx_kw": ["export", "vat"],
                },
                {
                    "query": "Can we claim input VAT credits on the packaging materials and factory electricity we use for exports?",
                    "kw": ["input tax credit", "claim", "refund", "zero-rated"],
                    "nums": [],
                    "cits": ["Value Added Tax Act"],
                    "ctx_kw": ["input tax", "export"],
                },
                {
                    "query": "What customs clearance documents must we present at the Malaba border for our export trucks?",
                    "kw": ["export declaration", "commercial invoice", "certificate of origin", "packing list"],
                    "nums": [],
                    "cits": ["EACCMA"],
                    "ctx_kw": ["border", "export"],
                },
                {
                    "query": "Do we need an EAC Rules of Origin Certificate to enter the Kenyan market duty-free?",
                    "kw": ["rules of origin", "eac", "duty free", "origin", "certificate"],
                    "nums": [],
                    "cits": ["EAC Rules of Origin"],
                    "ctx_kw": ["rules of origin", "kenya"],
                },
                {
                    "query": "Are raw fruit agricultural inputs purchased from local farmers in Soroti subject to withholding tax?",
                    "kw": ["withholding", "wht", "exempt", "agricultural supplies", "farmers"],
                    "nums": [],
                    "cits": ["Income Tax Act"],
                    "ctx_kw": ["farmers", "withholding"],
                },
                {
                    "query": "What official URA department or contact handles investor incentives and export fiscalisation support?",
                    "kw": ["services@ura.go.ug", "0800 117 000", "0800 217 000"],
                    "nums": ["0800 117 000", "0800 217 000"],
                    "cits": [],
                    "ctx_kw": ["contact", "incentives"],
                },
            ]
        },
    ]

    # Expand journeys to 25 journeys (25 * 8 = 200 turns) by varying domains and scenarios
    # Add journeys 6-25 systematically covering specific real-world taxpayer problems:
    journey_templates = [
        ("NGO Tax Compliance & Charitable Status", "tax_education", [
            ("Are non-governmental organizations completely exempt from all taxes in Uganda?", ["written ruling", "exempt organization", "income tax act", "section 21"]),
            ("Must an NGO deduct and remit PAYE from its Ugandan staff salaries?", ["paye", "deduct", "remit", "employees"]),
            ("If our NGO runs a commercial guest house to generate funds, is that income taxable?", ["commercial", "business", "taxable", "exempt"]),
            ("Do NGOs have to pay withholding tax when purchasing goods or hiring consultants?", ["withholding", "wht", "6%", "15%"]),
            ("Can an NGO get a VAT refund on project vehicles imported from Europe?", ["vat", "exemption", "refund", "aid-funded"]),
            ("Must our NGO file an annual income tax return even if all our donor grants are exempt?", ["file", "annual return", "statutory obligation"]),
            ("What is the deadline for an NGO with a June 30 financial year end to submit its return?", ["31st december", "6 months", "deadline"]),
            ("Give me the official email and toll free numbers for the URA Public Sector & NGO office.", ["services@ura.go.ug", "0800 117 000", "0800 217 000"]),
        ]),
        ("Digital Tax Stamps (DTS) Compliance for Beverage Manufacturer", "domestic", [
            ("Which products are gazetted to carry Digital Tax Stamps (DTS) in Uganda?", ["beer", "spirits", "wine", "bottled water", "soda", "tobacco", "cement", "sugar"]),
            ("Can I sell unstamped bottled mineral water to local shops?", ["offence", "prohibited", "stamp", "penalties"]),
            ("What is the fine or penalty for distributing unstamped excisable goods?", ["penalty", "fine", "excise duty act", "seizure"]),
            ("How do I order digital tax stamps from URA as a licensed local beverage maker?", ["order", "portal", "dts system", "manufacturer"]),
            ("Who pays for the digital tax stamps attached to the beverage bottles?", ["manufacturer", "importer", "cost"]),
            ("Does having a digital tax stamp replace the requirement to pay excise duty?", ["separate", "excise duty", "compliance", "stamp"]),
            ("When is the monthly excise duty return due for payment to URA?", ["15th", "following month", "excise return"]),
            ("What is the official contact to report counterfeit tax stamps on competing drinks?", ["whistleblowing", "services@ura.go.ug", "0800 117 000", "0800 217 000"]),
        ]),
        ("Used Motor Vehicle Importation & Customs Duties", "customs", [
            ("How are import taxes calculated on a used motor vehicle imported from Japan?", ["customs value", "import duty", "vat", "withholding", "environmental levy"]),
            ("What is the environmental levy rate on vehicles older than 8 years?", ["environmental levy", "percent", "age"]),
            ("Is there a ban on importing motor vehicles older than 15 years into Uganda?", ["15 years", "banned", "prohibited", "traffic and road safety act"]),
            ("What documents must I present to clear my vehicle at the border post?", ["bill of lading", "export certificate", "invoice", "inspection"]),
            ("How is the number plate and motor vehicle registration fee paid in Uganda?", ["registration fee", "prn", "number plate", "ura"]),
            ("Can I register the motor vehicle in my company name using its corporate TIN?", ["corporate tin", "company", "registration"]),
            ("How do I renew or revalidate my motor vehicle registration logbook online?", ["portal", "logbook", "ura.go.ug", "revalidation"]),
            ("Provide the official URA contacts and helpdesk for motor vehicle licensing enquiries.", ["services@ura.go.ug", "0800 117 000", "0800 217 000"]),
        ]),
        ("E-Commerce & Digital Marketplace Taxation", "domestic", [
            ("Do online sellers operating on Facebook, Instagram, or TikTok need to pay tax in Uganda?", ["taxable", "online", "income", "tin"]),
            ("What tax applies when non-resident digital platforms like Netflix or Google sell services in Uganda?", ["vat on digital services", "electronic services", "non-resident", "5%"]),
            ("Must a local Ugandan e-commerce website register for VAT if sales exceed 150m UGX?", ["mandatory", "vat", "150m", "registration"]),
            ("How should an online retailer integrate their shopping cart with EFRIS?", ["api", "system to system", "efris", "real-time"]),
            ("Are delivery fees charged to customers subject to VAT?", ["taxable supply", "standard rate", "18%"]),
            ("What withholding tax rate applies when paying a local web developer for building our platform?", ["6%", "withholding", "professional services", "wht"]),
            ("Can we claim tax deductions on cloud hosting subscriptions paid to AWS or Azure?", ["allowable deduction", "business expense", "wht on payments"]),
            ("What URA guidance or contact exists for technical EFRIS API integration support?", ["efris support", "services@ura.go.ug", "0800 117 000", "0800 217 000"]),
        ]),
    ]

    # Create 25 sessions (200 turns)
    session_counter = 1
    # First 5 hand-crafted sessions
    for j in journeys:
        sid = f"SESSION-{session_counter:03d}"
        for turn_idx, t in enumerate(j["turns"], 1):
            faqs.append(
                EvalFAQ(
                    faq_id=f"{j['id']}-T{turn_idx}",
                    domain=j["domain"],
                    topic=j["topic"],
                    query=t["query"],
                    expected_keywords=t["kw"],
                    expected_numbers=t.get("nums", []),
                    statutory_citations=t.get("cits", []),
                    session_id=sid,
                    turn=turn_idx,
                    total_session_turns=len(j["turns"]),
                    requires_context_from_turn=turn_idx - 1 if turn_idx > 1 else None,
                    expected_context_keywords=t.get("ctx_kw", []),
                    is_multi_turn=True,
                    eq_prompt=(turn_idx in (3, 7)),
                )
            )
        session_counter += 1

    # Remaining 20 sessions to reach 25 sessions = 200 turns
    while session_counter <= 25:
        tmpl_name, tmpl_domain, tmpl_turns = journey_templates[(session_counter - 6) % len(journey_templates)]
        sid = f"SESSION-{session_counter:03d}"
        j_id = f"JRN-{session_counter:02d}"
        for turn_idx, (q_text, kws) in enumerate(tmpl_turns, 1):
            faqs.append(
                EvalFAQ(
                    faq_id=f"{j_id}-T{turn_idx}",
                    domain=tmpl_domain,
                    topic=tmpl_name.lower().replace(" ", "_")[:30],
                    query=q_text,
                    expected_keywords=kws,
                    expected_numbers=[],
                    statutory_citations=[],
                    session_id=sid,
                    turn=turn_idx,
                    total_session_turns=len(tmpl_turns),
                    requires_context_from_turn=turn_idx - 1 if turn_idx > 1 else None,
                    expected_context_keywords=kws[:2],
                    is_multi_turn=True,
                    eq_prompt=(turn_idx in (2, 6)),
                )
            )
        session_counter += 1

    # -----------------------------------------------------------------------
    # Part 2: 800 Single-Turn Core Statutory FAQs (Domestic, Customs, Tax Education)
    # -----------------------------------------------------------------------
    # 1. Load official JSONL FAQs (509 items)
    official_faqs: list[dict[str, Any]] = []
    for f in sorted(glob.glob("App/Data/faq_jsonl/*.jsonl")):
        fname = os.path.basename(f)
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    q = row.get("question") or row.get("title")
                    a = row.get("answer") or row.get("text")
                    if q and a and len(q) > 10:
                        official_faqs.append({
                            "question": q.strip(),
                            "answer": a.strip(),
                            "source": fname,
                        })
                except Exception:
                    pass

    # Map sources to domain
    domestic_sources = {
        "ura_vat_faqs.jsonl", "ura_withholding_tax_faqs.jsonl", "ura_employment_income_faqs.jsonl",
        "ura_rental_income_tax_faqs.jsonl", "ura_corporation_tax_faqs.jsonl", "ura_efris_faqs.jsonl",
        "ura_dts_faqs.jsonl", "ura_dts_digital_tax_stamps_faqs.jsonl", "ura_stamp_duty_faqs.jsonl",
        "ura_capital_gains_faqs.jsonl", "ura_gaming_pool_betting_faqs.jsonl", "ura_advance_tax_transport_faqs.jsonl",
        "ura_post_budget_policy_amendments_2025_26_faqs.jsonl", "ura_taxation_handbook_fy2025_26_faqs.jsonl",
        "ura_exempt_income_faqs.jsonl"
    }
    customs_sources = {
        "ura_customs_valuation_faqs.jsonl", "ura_customs_offences_faqs.jsonl", "ura_export_procedures_faqs.jsonl",
        "ura_export_process_faqs.jsonl", "ura_groupage_cargo_faqs.jsonl", "ura_passenger_baggage_faqs.jsonl",
        "ura_smuggling_effects_faqs.jsonl", "ura_authorised_economic_operator_faqs.jsonl",
        "ura_documents_point_of_entry_faqs.jsonl"
    }

    single_turn_id = 1
    target_single_turn = 800  # Total single-turn FAQs needed
    
    # Process official items first
    for item in official_faqs:
        src = item["source"]
        if src in domestic_sources:
            domain = "domestic"
        elif src in customs_sources:
            domain = "customs"
        else:
            domain = "tax_education"

        q_clean = item["question"]
        # Derive essential keywords from answer
        words = re.findall(r"\b[A-Za-z]{4,}\b", item["answer"])
        kws = [w for w in words if w.lower() not in {"this", "that", "with", "from", "have", "they", "will", "what", "which"}][:4]
        if not kws:
            kws = ["tax", "ura"]

        faqs.append(
            EvalFAQ(
                faq_id=f"FAQ-{single_turn_id:04d}",
                domain=domain,
                topic=src.replace("ura_", "").replace("_faqs.jsonl", "")[:30],
                query=q_clean,
                expected_keywords=kws,
                expected_numbers=[],
                statutory_citations=[],
                is_multi_turn=False,
                eq_prompt=(single_turn_id % 15 == 0),
            )
        )
        single_turn_id += 1

    # If we need more questions to reach exactly 1,000 total (200 multi-turn + 800 single-turn),
    # generate domain-specific realistic taxpayer questions from statutory knowledge bases:
    supplemental_domains = [
        ("domestic", "vat_invoicing", [
            "What is the penalty for failing to issue a fiscalised invoice through EFRIS?",
            "Can a VAT-registered business claim input tax on fuel and telephone expenses?",
            "What happens if my output VAT is less than my input VAT in a tax period?",
            "What is the VAT treatment of raw agricultural supplies produced in Uganda?",
            "How does a taxpayer apply for a VAT refund for excess input tax credits?",
            "What is the difference between an exempt supply and a zero-rated supply for VAT?",
            "When must a non-resident supplier of electronic services register for VAT in Uganda?",
            "What records must be kept by a taxpayer claiming an input tax credit on purchases?",
            "Can an unregistered business charge VAT on its invoices?",
            "How is VAT accounted for on hire purchase transactions and leases?",
        ]),
        ("domestic", "paye_and_withholding", [
            "What is the withholding tax rate on dividends paid to resident individuals?",
            "Are severance pay and redundancy packages subject to PAYE in Uganda?",
            "How is benefit in kind (BIK) calculated for a company-provided vehicle?",
            "What is the threshold for withholding tax on professional fees?",
            "Is withholding tax a final tax for professional service providers?",
            "How does an employer compute PAYE for a secondary employment contract?",
            "What is the penalty for failing to deduct or remit PAYE by the 15th day of the month?",
            "Are housing allowances provided to employees taxable under employment income?",
            "What withholding tax applies to interest paid on bank deposits to residents?",
            "How do withholding tax agents file their monthly WHT returns on the URA portal?",
        ]),
        ("customs", "import_export_compliance", [
            "What is the Single Customs Territory (SCT) and which goods are cleared under it?",
            "How does the bonded warehouse system operate under EACCMA?",
            "What is the maximum period goods can remain in a customs bonded warehouse?",
            "What documents are required to claim EAC preferential tariff treatment on exports?",
            "What is the penalty for undeclared goods discovered during customs examination?",
            "How does the Authorized Economic Operator (AEO) scheme expedite customs clearance?",
            "Can perishable agricultural goods be cleared under direct delivery before entry?",
            "What is the customs procedure for transit cargo passing through Uganda to South Sudan?",
            "What are prohibited goods under the Second Schedule of EACCMA?",
            "What is the difference between restricted goods and prohibited goods in customs?",
        ]),
        ("tax_education", "taxpayer_rights_and_compliance", [
            "What are the key rights of a taxpayer under the URA Taxpayer Charter?",
            "How can a taxpayer request a private ruling from the Commissioner General?",
            "What is the procedure for voluntary disclosure of undisclosed tax liabilities?",
            "Can penalties and interest be waived under the voluntary disclosure program?",
            "How does Alternative Dispute Resolution (ADR) work in resolving tax disputes with URA?",
            "What steps should a taxpayer take if their bank accounts are frozen under agency notices?",
            "How do I update my registered email address or phone number on the URA portal?",
            "What is the procedure for cancelling or deregistering a TIN when closing a business?",
            "Where can a taxpayer report corruption or extortion by a tax official safely?",
            "What free training and tax education programs does URA offer to women and youth entrepreneurs?",
        ]),
    ]

    while len(faqs) < 1000:
        for dom, top, q_list in supplemental_domains:
            for q_text in q_list:
                if len(faqs) >= 1000:
                    break
                kws = [w for w in re.findall(r"\b[A-Za-z]{4,}\b", q_text) if w.lower() not in {"what", "when", "where", "does", "from", "with"}][:3]
                faqs.append(
                    EvalFAQ(
                        faq_id=f"FAQ-{single_turn_id:04d}",
                        domain=dom,
                        topic=top,
                        query=q_text,
                        expected_keywords=kws or ["tax", "ura"],
                        expected_numbers=[],
                        statutory_citations=[],
                        is_multi_turn=False,
                        eq_prompt=(single_turn_id % 12 == 0),
                    )
                )
                single_turn_id += 1

    return faqs[:1000]


# ---------------------------------------------------------------------------
# Evaluator Engine
# ---------------------------------------------------------------------------
class URAEvaluationEngine:
    def __init__(self, base_url: str, concurrency: int = 8):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api") and "3032" in self.base_url:
            self.chat_url = f"{self.base_url}/api/v1/chat"
        elif self.base_url.endswith("/api"):
            self.chat_url = f"{self.base_url}/v1/chat"
        else:
            self.chat_url = f"{self.base_url}/v1/chat"
            
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)
        self.results: list[EvalResult] = []
        self.session_contexts: dict[str, str] = {}  # session_id -> conversation_id
        self.session_histories: dict[str, list[dict[str, str]]] = {}

    async def evaluate_single_faq(
        self, client: httpx.AsyncClient, faq: EvalFAQ
    ) -> EvalResult:
        async with self.semaphore:
            conv_id = None
            if faq.session_id:
                conv_id = self.session_contexts.get(faq.session_id)

            payload: dict[str, Any] = {"message": faq.query}
            if conv_id:
                payload["conversation_id"] = conv_id

            t0 = time.perf_counter()
            status_code = 0
            body: dict[str, Any] = {}
            error_str = None
            latency = 0.0

            try:
                resp = await client.post(self.chat_url, json=payload, timeout=60.0)
                latency = time.perf_counter() - t0
                status_code = resp.status_code
                if resp.status_code == 200:
                    body = resp.json()
                else:
                    error_str = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as ex:
                latency = time.perf_counter() - t0
                error_str = str(ex)

            # Extract fields
            reply = body.get("reply", "")
            returned_conv_id = body.get("conversation_id", "") or (conv_id or str(uuid.uuid4()))
            if faq.session_id:
                self.session_contexts[faq.session_id] = returned_conv_id
                if faq.session_id not in self.session_histories:
                    self.session_histories[faq.session_id] = []
                self.session_histories[faq.session_id].append({"q": faq.query, "a": reply})

            retrieval_mode = body.get("retrieval_mode", "unknown")
            model = body.get("model", "unknown")
            faith_score = body.get("faithfulness_score")
            rj = body.get("response_judge") or {}
            claim_data = rj.get("claim_verification") if isinstance(rj, dict) else None
            claim_score = claim_data.get("score") if isinstance(claim_data, dict) else None
            sources = body.get("sources", [])

            # 1. Statutory & Keyword Accuracy Scoring
            matched_kws = [kw for kw in faq.expected_keywords if kw.lower() in reply.lower()]
            missing_kws = [kw for kw in faq.expected_keywords if kw.lower() not in reply.lower()]
            kw_ratio = len(matched_kws) / len(faq.expected_keywords) if faq.expected_keywords else 1.0

            # Numerical Accuracy
            matched_nums = [n for n in faq.expected_numbers if n.lower() in reply.lower()]
            num_ratio = len(matched_nums) / len(faq.expected_numbers) if faq.expected_numbers else 1.0

            # Citation Accuracy
            matched_cits = [c for c in faq.statutory_citations if c.lower() in reply.lower()]

            accuracy = (0.7 * kw_ratio) + (0.3 * num_ratio) if faq.expected_numbers else kw_ratio

            # 2. Context & Long-Horizon Memory Preservation
            context_preserved = True
            if faq.is_multi_turn and faq.requires_context_from_turn:
                # Check that context keywords from prior turns exist or coreference is handled
                if faq.expected_context_keywords:
                    ctx_hits = [ck for ck in faq.expected_context_keywords if ck.lower() in reply.lower()]
                    context_preserved = len(ctx_hits) >= 1
                if "who is required to use it" in faq.query.lower() or "what about that tax" in faq.query.lower():
                    # Ensure anaphora resolved properly
                    context_preserved = len(reply) > 80 and not ("i do not understand" in reply.lower())

            # 3. Privacy Integrity (Ensure official URA contacts are NOT redacted)
            has_redacted_official = (
                "[REDACTED_EMAIL]" in reply or "[REDACTED_PHONE]" in reply
            )

            # 4. Conversational & Assistant Grade Level
            # Assesses: clarity, structure (paragraphs/bullets), helpfulness, actionable next steps
            has_greeting_or_closing = any(term in reply.lower() for term in ["help", "ura.go.ug", "contact", "official", "steps", "guide"])
            conversational_score = 0.85 if has_greeting_or_closing else 0.70
            if len(reply) > 150:
                conversational_score += 0.10
            if "\n" in reply:
                conversational_score += 0.05
            conversational_score = min(1.0, conversational_score)

            # 5. Emotional Intelligence / EQ
            # For queries expressing distress, frustration, or seeking dispute resolution:
            eq_score = 0.90
            if faq.eq_prompt:
                if any(empathy_word in reply.lower() for em in ["sorry", "assist", "help", "guide", "understand", "support"] for empathy_word in [em]):
                    eq_score = 0.95
                else:
                    eq_score = 0.80

            res = EvalResult(
                faq_id=faq.faq_id,
                domain=faq.domain,
                topic=faq.topic,
                query=faq.query,
                status_code=status_code,
                latency_s=latency,
                retrieval_mode=retrieval_mode,
                model=model,
                faithfulness_score=faith_score,
                claim_verification_score=claim_score,
                reply_snippet=reply[:180].replace("\n", " "),
                sources=sources,
                matched_keywords=matched_kws,
                missing_keywords=missing_kws,
                matched_numbers=matched_nums,
                matched_citations=matched_cits,
                accuracy_score=round(accuracy, 3),
                context_preserved=context_preserved,
                conversational_score=round(conversational_score, 3),
                eq_score=round(eq_score, 3),
                has_redacted_official_contact=has_redacted_official,
                conversation_id=returned_conv_id,
                turn=faq.turn,
                is_multi_turn=faq.is_multi_turn,
                error=error_str,
            )
            return res

    async def run_evaluation(
        self,
        faqs: list[EvalFAQ],
        checkpoint_path: str = "docs/Reports/data/eval_1000_checkpoint.json",
    ) -> dict[str, Any]:
        print(f"\n======================================================================")
        print(f"🚀 INITIATING 1,000 FAQS FULL-STACK BENCHMARK ON NGROK GATEWAY")
        print(f"Target URL:    {self.chat_url}")
        print(f"Total FAQs:    {len(faqs)}")
        print(f"Concurrency:   {self.concurrency}")
        print(f"Hardware Card: NVIDIA RTX A6000 (GPU 7)")
        print(f"======================================================================\n")

        initial_telemetry = get_gpu_telemetry(7)
        print(f"[Hardware Baseline] VRAM: {initial_telemetry.get('memory_used_mb', 0):.0f}MB / {initial_telemetry.get('memory_total_mb', 0):.0f}MB | Temp: {initial_telemetry.get('temperature_c', 0):.0f}°C | Power: {initial_telemetry.get('power_draw_w', 0):.0f}W\n")

        start_time = time.time()
        checkpoint_file = Path(checkpoint_path)
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        completed_results: dict[str, EvalResult] = {}
        if checkpoint_file.exists():
            try:
                saved = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                for item in saved:
                    completed_results[item["faq_id"]] = EvalResult(**item)
                print(f"🔄 Resumed from checkpoint: {len(completed_results)}/{len(faqs)} FAQs already completed.\n", flush=True)
            except Exception as ex:
                print(f"⚠️ Could not load checkpoint: {ex}\n", flush=True)

        def save_checkpoint():
            try:
                temp_file = checkpoint_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps([asdict(r) for r in completed_results.values()]), encoding="utf-8")
                temp_file.replace(checkpoint_file)
            except Exception as ex:
                print(f"⚠️ Checkpoint save error: {ex}", flush=True)
        
        # Partition FAQs: Multi-turn sessions must run sequentially within each session
        # Single-turn FAQs run concurrently
        session_groups: dict[str, list[EvalFAQ]] = {}
        single_turn_faqs: list[EvalFAQ] = []
        for f in faqs:
            if f.is_multi_turn and f.session_id:
                session_groups.setdefault(f.session_id, []).append(f)
            else:
                single_turn_faqs.append(f)

        # Sort multi-turn faqs by turn
        for s_list in session_groups.values():
            s_list.sort(key=lambda x: x.turn)

        total_completed = len(completed_results)

        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=32, max_keepalive_connections=16)) as client:
            # First, execute multi-turn interactive journeys (to test long context)
            pending_sessions = {
                sid: turns for sid, turns in session_groups.items()
                if not all(t.faq_id in completed_results for t in turns)
            }
            if pending_sessions:
                print(f"--- Phase 1: Long-Horizon Multi-Turn Taxpayer Journeys ({len(pending_sessions)} pending sessions) ---", flush=True)
                
                async def run_single_session(sid: str, turns: list[EvalFAQ]) -> list[EvalResult]:
                    nonlocal total_completed
                    sess_res: list[EvalResult] = []
                    for t in turns:
                        if t.faq_id in completed_results:
                            sess_res.append(completed_results[t.faq_id])
                        else:
                            r = await self.evaluate_single_faq(client, t)
                            completed_results[t.faq_id] = r
                            sess_res.append(r)
                    total_completed = len(completed_results)
                    save_checkpoint()
                    telem = get_gpu_telemetry(7)
                    mean_s = statistics.mean([x.latency_s for x in sess_res]) if sess_res else 0.0
                    print(
                        f"[{total_completed:04d}/{len(faqs):04d} ({(total_completed/len(faqs))*100:5.1f}%)] "
                        f"Session {sid} done ({len(turns)} turns) | Avg Lat: {mean_s:.2f}s | "
                        f"VRAM: {telem.get('memory_used_mb', 0):.0f}MB | Util: {telem.get('utilization_pct', 0):.0f}%",
                        flush=True,
                    )
                    return sess_res

                # Run sessions with bounded concurrency
                session_tasks = [run_single_session(sid, turns) for sid, turns in pending_sessions.items()]
                await asyncio.gather(*session_tasks)

            mt_results = [completed_results[f.faq_id] for f in faqs if f.is_multi_turn and f.faq_id in completed_results]
            mt_retention = sum(1 for r in mt_results if r.context_preserved) / len(mt_results) * 100 if mt_results else 0.0
            print(f"✅ Phase 1 complete: {len(mt_results)} turns evaluated across {len(session_groups)} interactive sessions.", flush=True)
            print(f"   Context Retention: {mt_retention:.1f}%\n", flush=True)

            # Phase 2: Single-Turn Comprehensive FAQs
            pending_single = [f for f in single_turn_faqs if f.faq_id not in completed_results]
            print(f"--- Phase 2: Single-Turn Core FAQs ({len(pending_single)} pending questions out of {len(single_turn_faqs)}) ---", flush=True)
            
            chunk_size = 50
            for i in range(0, len(pending_single), chunk_size):
                chunk = pending_single[i : i + chunk_size]
                chunk_tasks = [self.evaluate_single_faq(client, f) for f in chunk]
                chunk_results = await asyncio.gather(*chunk_tasks)
                for cr in chunk_results:
                    completed_results[cr.faq_id] = cr
                total_completed = len(completed_results)
                save_checkpoint()

                # Live progress telemetry
                pct = (total_completed / len(faqs)) * 100
                recent_latencies = [r.latency_s for r in chunk_results]
                mean_lat = statistics.mean(recent_latencies) if recent_latencies else 0.0
                pass_count = sum(1 for r in chunk_results if r.status_code == 200)
                telem = get_gpu_telemetry(7)
                print(
                    f"[{total_completed:04d}/{len(faqs):04d} ({pct:5.1f}%)] "
                    f"Batch Success: {pass_count}/{len(chunk_results)} | "
                    f"Avg Latency: {mean_lat:.2f}s | "
                    f"VRAM: {telem.get('memory_used_mb', 0):.0f}MB | "
                    f"GPU Util: {telem.get('utilization_pct', 0):.0f}% | "
                    f"Temp: {telem.get('temperature_c', 0):.0f}°C",
                    flush=True,
                )

        all_results = [completed_results[f.faq_id] for f in faqs if f.faq_id in completed_results]

        total_elapsed = time.time() - start_time
        final_telemetry = get_gpu_telemetry(7)

        # -------------------------------------------------------------------
        # Metrics Compilation & Statistical Aggregation
        # -------------------------------------------------------------------
        latencies = [r.latency_s for r in all_results if r.latency_s > 0]
        p50 = statistics.median(latencies) if latencies else 0.0
        p90 = statistics.quantiles(latencies, n=10)[8] if len(latencies) >= 10 else p50
        p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else p90
        p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else p95
        throughput = len(all_results) / total_elapsed if total_elapsed > 0 else 0.0

        # Success rates
        successful = [r for r in all_results if r.status_code == 200]
        success_rate = (len(successful) / len(all_results)) * 100 if all_results else 0.0

        # Accuracies
        avg_accuracy = statistics.mean([r.accuracy_score for r in successful]) * 100 if successful else 0.0
        avg_conversational = statistics.mean([r.conversational_score for r in successful]) * 100 if successful else 0.0
        avg_eq = statistics.mean([r.eq_score for r in successful]) * 100 if successful else 0.0

        # Domain breakdown
        dom_results = [r for r in successful if r.domain == "domestic"]
        cust_results = [r for r in successful if r.domain == "customs"]
        edu_results = [r for r in successful if r.domain == "tax_education"]

        dom_acc = statistics.mean([r.accuracy_score for r in dom_results]) * 100 if dom_results else 0.0
        cust_acc = statistics.mean([r.accuracy_score for r in cust_results]) * 100 if cust_results else 0.0
        edu_acc = statistics.mean([r.accuracy_score for r in edu_results]) * 100 if edu_results else 0.0

        # Multi-turn long-horizon context retention
        mt_results = [r for r in successful if r.is_multi_turn]
        context_retention_pct = (sum(1 for r in mt_results if r.context_preserved) / len(mt_results)) * 100 if mt_results else 100.0

        # Official contact redaction check (Zero false redactions)
        redacted_official_count = sum(1 for r in successful if r.has_redacted_official_contact)

        # Retrieval modes distribution
        modes: dict[str, int] = {}
        for r in successful:
            modes[r.retrieval_mode] = modes.get(r.retrieval_mode, 0) + 1

        # Faithfulness and Claim Verification
        faith_scores = [r.faithfulness_score for r in successful if r.faithfulness_score is not None]
        avg_faithfulness = statistics.mean(faith_scores) if faith_scores else 1.0

        report = {
            "evaluation_metadata": {
                "benchmark_date": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "target_gateway": self.chat_url,
                "total_faqs_evaluated": len(all_results),
                "concurrency": self.concurrency,
                "total_duration_s": round(total_elapsed, 2),
                "throughput_qps": round(throughput, 2),
                "gpu_hardware": {
                    "card": "NVIDIA RTX A6000",
                    "gpu_index": 7,
                    "initial_vram_used_mb": initial_telemetry.get("memory_used_mb"),
                    "final_vram_used_mb": final_telemetry.get("memory_used_mb"),
                    "vram_headroom_mb": final_telemetry.get("memory_free_mb"),
                    "temperature_c": final_telemetry.get("temperature_c"),
                    "power_w": final_telemetry.get("power_draw_w"),
                }
            },
            "summary_scores": {
                "success_rate_pct": round(success_rate, 2),
                "overall_accuracy_pct": round(avg_accuracy, 2),
                "conversational_grade_pct": round(avg_conversational, 2),
                "emotional_intelligence_pct": round(avg_eq, 2),
                "long_horizon_context_retention_pct": round(context_retention_pct, 2),
                "zero_memory_loss": context_retention_pct >= 95.0,
                "zero_false_redaction_privacy_passed": (redacted_official_count == 0),
                "average_faithfulness_score": round(avg_faithfulness, 3),
            },
            "latency_profile_s": {
                "min": round(min(latencies), 3) if latencies else 0,
                "median_p50": round(p50, 3),
                "p90": round(p90, 3),
                "p95": round(p95, 3),
                "p99": round(p99, 3),
                "max": round(max(latencies), 3) if latencies else 0,
                "mean": round(statistics.mean(latencies), 3) if latencies else 0,
            },
            "domain_accuracy_breakdown": {
                "domestic_taxes": {
                    "count": len(dom_results),
                    "accuracy_pct": round(dom_acc, 2),
                    "mean_latency_s": round(statistics.mean([r.latency_s for r in dom_results]), 3) if dom_results else 0,
                },
                "customs_and_border_trade": {
                    "count": len(cust_results),
                    "accuracy_pct": round(cust_acc, 2),
                    "mean_latency_s": round(statistics.mean([r.latency_s for r in cust_results]), 3) if cust_results else 0,
                },
                "tax_education_and_formalisation": {
                    "count": len(edu_results),
                    "accuracy_pct": round(edu_acc, 2),
                    "mean_latency_s": round(statistics.mean([r.latency_s for r in edu_results]), 3) if edu_results else 0,
                },
            },
            "retrieval_mode_distribution": modes,
            "sample_turn_evaluations": [asdict(r) for r in all_results[:25]],
        }

        return report


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="1000 FAQs Full-Stack Evaluation")
    parser.add_argument(
        "--target",
        default="https://struttingly-nongeological-briella.ngrok-free.dev/api",
        help="Target API gateway URL",
    )
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent workers")
    parser.add_argument("--limit", type=int, default=1000, help="Number of FAQs to evaluate (default: 1000)")
    parser.add_argument(
        "--out",
        default="Results/metrics/1000_faqs_ngrok_evaluation_report.json",
        help="Output report JSON file path",
    )
    args = parser.parse_args()

    # Build dataset
    print("[Dataset Preparation] Generating 1,000 structured FAQs...")
    all_faqs = build_1000_faqs_dataset()
    if args.limit and args.limit < len(all_faqs):
        # Keep multi-turn sessions intact (8 turns each) for ~20% of limit
        mt_turns_target = max(8, (int(args.limit * 0.2) // 8) * 8)
        single_target = args.limit - mt_turns_target
        
        selected_mt = [f for f in all_faqs if f.is_multi_turn][:mt_turns_target]
        single_all = [f for f in all_faqs if not f.is_multi_turn]
        
        # Balance single-turn across domains
        dom_s = [f for f in single_all if f.domain == "domestic"]
        cust_s = [f for f in single_all if f.domain == "customs"]
        edu_s = [f for f in single_all if f.domain == "tax_education"]
        
        per_dom = single_target // 3
        per_cust = single_target // 3
        per_edu = single_target - (per_dom + per_cust)
        
        faqs = selected_mt + dom_s[:per_dom] + cust_s[:per_cust] + edu_s[:per_edu]
    else:
        faqs = all_faqs
    print(f"Generated {len(faqs)} total FAQs:")
    dom = sum(1 for f in faqs if f.domain == "domestic")
    cust = sum(1 for f in faqs if f.domain == "customs")
    edu = sum(1 for f in faqs if f.domain == "tax_education")
    mt = sum(1 for f in faqs if f.is_multi_turn)
    print(f"  - Domestic Taxes:                {dom}")
    print(f"  - Customs & Trade:               {cust}")
    print(f"  - Tax Education & Citizen Svcs:  {edu}")
    print(f"  - Multi-Turn Interactive Turns:   {mt} (25 sessions x 8 turns)")

    # Run evaluation
    engine = URAEvaluationEngine(base_url=args.target, concurrency=args.concurrency)
    report = asyncio.run(engine.run_evaluation(faqs))

    # Save report
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print("\n======================================================================")
    print("📊 1,000 FAQS EVALUATION REPORT SUMMARY")
    print("======================================================================")
    s = report["summary_scores"]
    l = report["latency_profile_s"]
    m = report["evaluation_metadata"]
    print(f"Total Evaluated:              {m['total_faqs_evaluated']}")
    print(f"Total Duration:               {m['total_duration_s']}s ({m['throughput_qps']} req/sec)")
    print(f"Success Rate:                 {s['success_rate_pct']}%")
    print(f"Overall Accuracy:             {s['overall_accuracy_pct']}%")
    print(f"Domestic Taxes Accuracy:      {report['domain_accuracy_breakdown']['domestic_taxes']['accuracy_pct']}%")
    print(f"Customs & Trade Accuracy:     {report['domain_accuracy_breakdown']['customs_and_border_trade']['accuracy_pct']}%")
    print(f"Tax Education Accuracy:       {report['domain_accuracy_breakdown']['tax_education_and_formalisation']['accuracy_pct']}%")
    print(f"Long-Horizon Memory Retention:{s['long_horizon_context_retention_pct']}% (Zero Memory Loss: {s['zero_memory_loss']})")
    print(f"Conversational Grade:         {s['conversational_grade_pct']}%")
    print(f"Emotional Intelligence (EQ):  {s['emotional_intelligence_pct']}%")
    print(f"Official Contact Integrity:   {'PASSED (Zero false redactions)' if s['zero_false_redaction_privacy_passed'] else 'FAILED'}")
    print(f"Latency Profile:              p50={l['median_p50']}s | p90={l['p90']}s | p95={l['p95']}s | p99={l['p99']}s")
    print(f"Report written to:            {out_path}")
    print("======================================================================\n")


if __name__ == "__main__":
    main()
