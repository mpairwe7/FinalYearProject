#!/usr/bin/env python3
"""Live URA CX Team Usability & Enhancement Evaluation Runner.

Executes 10 realistic URA frontline customer experience scenarios directly against
the live ChatModel pipeline, measuring how CX enhancements solve real-world
contact centre bottlenecks:
  1. Transactional PRN voucher generation (autonomous execution vs deflection)
  2. Multi-tier PAYE salary math (deterministic calculation vs officer error)
  3. Vernacular EFRIS guidance for market vendors in Luganda (language fidelity)
  4. Cross-border customs tariff & CIF valuation in Swahili (EAC CET accuracy)
  5. Distressed taxpayer bank freeze de-escalation (s.42 TPCA payment plans)
  6. s.24 TPCA assessment objection stepper (statutory deadlines & 30% deposit)
  7. Inline EFRIS thermal receipt arithmetic audit (fraud & mismatch detection)
  8. Closed-loop knowledge discrepancy capture (KB-... tracking)
  9. Foreign tax deflection (statutory jurisdictional boundaries)
  10. Human officer escalation & structured handoff brief generation
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Setup paths
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BACKEND_ROOT = _REPO_ROOT / "App" / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Isolate analytics DB
os.environ["ANALYTICS_DB_DIR"] = "/tmp/ura_cx_live_eval_db"
Path("/tmp/ura_cx_live_eval_db").mkdir(parents=True, exist_ok=True)
os.environ["FLAG_SEMANTIC_CACHE"] = "false"
os.environ["FLAG_TICKET_QUEUE"] = "true"
os.environ["FLAG_MEMORY_ENABLED"] = "false"

from app.database import init_db  # noqa: E402

init_db()

from app.service import ChatModel  # noqa: E402

SCENARIOS = [
    {
        "id": "CX-LIVE-01",
        "category": "Transactional Execution",
        "title": "Autonomous PRN Voucher Generation (Land Stamp Duty)",
        "cx_problem": "Taxpayers were previously deflected to a 4-step questionnaire instead of receiving an actionable payment slip.",
        "locale": "en",
        "message": "Generate a PRN for 750,000 UGX stamp duty on my land sale in Wakiso.",
        "eval_criteria": ["PRN Number (10 digits)", "Assessment Code", "USSD Mobile Money (*165# / *185#)", "Validity (21 days)"],
    },
    {
        "id": "CX-LIVE-02",
        "category": "Deterministic Computations",
        "title": "Progressive PAYE Deduction & Net Salary Math (FY2026/27)",
        "cx_problem": "Manual arithmetic errors by contact centre staff when explaining progressive tax bands on gross income.",
        "locale": "en",
        "message": "My monthly gross salary is 1,500,000 UGX. Calculate my exact PAYE tax and take-home pay.",
        "eval_criteria": ["Tax-free threshold (235,000 UGX)", "10% band", "20% band", "30% band", "Net pay"],
    },
    {
        "id": "CX-LIVE-03",
        "category": "Multilingual Equity (Luganda)",
        "title": "Market Vendor EFRIS Penalty Guidance in Luganda",
        "cx_problem": "Language flipping to English and intimidating statutory text for small vernacular-speaking market traders.",
        "locale": "lg",
        "message": "Bampadde ekibonerezo kya emitwalo nkaaga (600,000 UGX) ku EFRIS naye ndi mutunzi mutono mu Owino. Nzikirizibwa okugifuna ntya?",
        "eval_criteria": ["Luganda response fidelity", "EFRIS solution for small business", "Empathy / courtesy opener"],
    },
    {
        "id": "CX-LIVE-04",
        "category": "Multilingual Equity (Swahili)",
        "title": "Cross-Border Customs CET & CIF Valuation in Swahili",
        "cx_problem": "Cross-border traders at OSBPs struggling to obtain fast customs tariff clarification in Swahili.",
        "locale": "sw",
        "message": "Kodi ya forodha ya kuingiza nguo za jumla (textiles) kutoka Kenya kupitia Malaba ni asilimia ngapi? Na thamani ya CIF inakokotolewaje?",
        "eval_criteria": ["Swahili language fidelity", "EAC CET tariff band (35%)", "CIF formula (Cost, Insurance, Freight)"],
    },
    {
        "id": "CX-LIVE-05",
        "category": "Distress De-escalation",
        "title": "Bank Account Agency Freeze Notice & Hardship Relief",
        "cx_problem": "Distressed taxpayers facing frozen bank accounts triggering panic; need immediate Section 42 installment relief options.",
        "locale": "en",
        "message": "URA has sent an agency notice to freeze my business account tomorrow! I cannot pay my workers and my family is suffering. Please help me stop this!",
        "eval_criteria": ["Empathetic de-escalation opener", "Section 42 TPCA payment agreement", "Toll-free helpline (0800 117 000)"],
    },
    {
        "id": "CX-LIVE-06",
        "category": "Procedural Workflows",
        "title": "Section 24 TPCA Assessment Objection & 30% Deposit Protocol",
        "cx_problem": "Taxpayers missing the strict 45-day statutory objection deadline or surprised by the mandatory 30% deposit rule.",
        "locale": "en",
        "message": "I received an unfair default tax assessment of 45,000,000 UGX. I want to lodge a formal objection.",
        "eval_criteria": ["45-day statutory deadline", "30% deposit requirement (or waiver)", "e-Tax portal objection stepper"],
    },
    {
        "id": "CX-LIVE-07",
        "category": "Document Compliance",
        "title": "Inline EFRIS Thermal Receipt Arithmetic Audit",
        "cx_problem": "Frontline officers manually recalculating VAT on suspicious receipts reported by consumers.",
        "locale": "en",
        "message": "Check this receipt: Subtotal 400,000 UGX, VAT charged 50,000 UGX, Total 450,000 UGX. Is this invoice compliant?",
        "eval_criteria": ["18% VAT arithmetic check", "Flags mismatch (Expected 72,000 UGX vs Stated 50,000 UGX)", "EFRIS verification guidance"],
    },
    {
        "id": "CX-LIVE-08",
        "category": "Closed-Loop Feedback",
        "title": "Capturing Taxpayer Feedback & Discrepancy Logging",
        "cx_problem": "Citizen feedback and rate challenges getting lost without reaching URA tax policy and legal teams.",
        "locale": "en",
        "message": "Report bug: Your withholding tax rate on agricultural goods is outdated, the new law changed it to 1%.",
        "eval_criteria": ["Acknowledgment of challenge", "Discrepancy / Bug logging tracking badge (KB-... or acknowledgment)"],
    },
    {
        "id": "CX-LIVE-09",
        "category": "Statutory Boundaries",
        "title": "Foreign Tax Regime Deflection (Jurisdiction Guard)",
        "cx_problem": "AI assistants hallucinating legal advice on foreign jurisdictions outside URA statutory mandate.",
        "locale": "en",
        "message": "What is the capital gains tax rate in Kenya according to the Kenya Revenue Authority?",
        "eval_criteria": ["Jurisdiction deflection", "Clarifies URA scope (Ugandan tax laws only)", "Refusal to advise on KRA"],
    },
    {
        "id": "CX-LIVE-10",
        "category": "Officer Handoff",
        "title": "Warm Escalation to Human Support Officer",
        "cx_problem": "Cold transfers forcing callers to re-explain their entire situation to a new officer from scratch.",
        "locale": "en",
        "message": "I need to speak to an officer right now to schedule an Alternative Dispute Resolution hearing.",
        "eval_criteria": ["Escalation offer / confirmation", "Contact routing (0800 117 000 / ticket)", "Warm transition tone"],
    },
]


def run_live_eval() -> dict[str, Any]:
    print("Initializing Live URA ChatModel Pipeline...")
    t0_init = time.perf_counter()
    model = ChatModel()
    print(f"ChatModel initialized in {(time.perf_counter() - t0_init):.2f}s.\n")

    results = []
    print("=" * 100)
    print("  URA CX TEAM LIVE USABILITY & ENHANCEMENT EVALUATION")
    print("=" * 100)

    for i, sc in enumerate(SCENARIOS, 1):
        print(f"\n[{i:02d}/10] Testing: {sc['title']} ({sc['locale'].upper()})")
        print(f"       Category : {sc['category']}")
        print(f"       CX Need  : {sc['cx_problem']}")
        print(f"       Prompt   : {sc['message']}")

        t0_turn = time.perf_counter()
        conv_id = f"cx_live_session_{sc['id'].lower()}"
        res = model.generate(
            message=sc["message"],
            locale=sc["locale"],
            conversation_id=conv_id,
            session_id=conv_id,
            channel="web",
        )
        elapsed_s = time.perf_counter() - t0_turn
        reply = (res.get("reply") or res.get("text") or "").strip()
        mode = res.get("retrieval_mode", "unknown")
        discrepancy = res.get("discrepancy_report")

        # Evaluate criteria matches
        matched_criteria = []
        for crit in sc["eval_criteria"]:
            tokens = [w.lower() for w in crit.split() if len(w) > 3]
            if any(t in reply.lower() for t in tokens):
                matched_criteria.append(crit)

        score_pct = (len(matched_criteria) / len(sc["eval_criteria"])) * 100

        print(f"       Result   : Elapsed: {elapsed_s:.2f}s | Mode: {mode} | Criteria Met: {len(matched_criteria)}/{len(sc['eval_criteria'])} ({score_pct:.0f}%)")
        print(f"       Snippet  : {reply[:180]}...")
        if discrepancy:
            print(f"       Bug Log  : Logged discrepancy: {discrepancy.get('report_id')}")

        results.append({
            "id": sc["id"],
            "title": sc["title"],
            "category": sc["category"],
            "cx_problem": sc["cx_problem"],
            "locale": sc["locale"],
            "prompt": sc["message"],
            "elapsed_s": round(elapsed_s, 2),
            "retrieval_mode": mode,
            "reply": reply,
            "eval_criteria": sc["eval_criteria"],
            "matched_criteria": matched_criteria,
            "criteria_score_pct": round(score_pct, 1),
            "passed": score_pct >= 50.0,
            "discrepancy": discrepancy,
        })

    summary = {
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "total_scenarios": len(results),
        "passed_scenarios": sum(1 for r in results if r["passed"]),
        "overall_score_pct": round(sum(r["criteria_score_pct"] for r in results) / len(results), 1),
        "avg_latency_s": round(sum(r["elapsed_s"] for r in results) / len(results), 2),
        "results": results,
    }

    out_json = _REPO_ROOT / "docs" / "Reports" / "data" / "ura_cx_team_live_eval_results.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\n[Artifact] Full evaluation JSON saved to: {out_json}")

    return summary


if __name__ == "__main__":
    run_live_eval()
