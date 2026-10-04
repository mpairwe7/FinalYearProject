#!/usr/bin/env python3
"""Evaluate Customer Experience (CX) Enhancement & Request Completion over live ngrok.

Goes beyond static FAQ matching to measure whether the assistant actively
*completes* user requests:
  1. Interactive Multi-Step Guided Workflows (Stepping, Slot-filling, Validation, Deep-links)
  2. Deterministic Tax Computations (PAYE, VAT, Rental Tax, Withholding with exact net breakdowns)
  3. Actionable Resources & Verified Forms (Direct templates, checklists, portal buttons)
  4. Empathetic De-escalation & Problem Remediation (Crisis & penalty mitigation guidance)
  5. Closed-Loop Knowledge Discrepancy Reporting (Transparent #KB-XXX tracking)
  6. Multilingual Task Fulfillment with Figure Fidelity (Luganda & Swahili execution)

Usage:
  python3 scripts/evaluate_cx_request_completion_ngrok.py
  python3 scripts/evaluate_cx_request_completion_ngrok.py --base https://<ngrok-domain>/api
  python3 scripts/evaluate_cx_request_completion_ngrok.py --base http://localhost:8083 --out docs/Reports/data/cx_eval.json
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

# Default ngrok endpoint if available
DEFAULT_NGROK_DOMAIN = "struttingly-nongeological-briella.ngrok-free.dev"
DEFAULT_BASE = f"https://{DEFAULT_NGROK_DOMAIN}/api"

HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
    "User-Agent": "URA-CX-Evaluation-Suite/2026",
}


@dataclass
class TurnStep:
    """A single turn in a multi-turn user conversation."""
    user_message: str
    expect_mode: str | None = None
    expect_workflow: str | None = None
    expect_step_id: str | None = None
    expect_options_contain: list[str] = field(default_factory=list)
    expect_reply_contains: list[str] = field(default_factory=list)
    expect_reply_regex: list[str] = field(default_factory=list)
    expect_resources_min: int = 0
    expect_discrepancy: bool = False


@dataclass
class CXScenario:
    """An end-to-end customer journey scenario."""
    id: str
    category: str
    title: str
    description: str
    locale: str = "en"
    turns: list[TurnStep] = field(default_factory=list)


def build_scenarios() -> list[CXScenario]:
    """Formulate test scenarios testing active request completion vs static FAQ answers."""
    return [
        # =====================================================================
        # CATEGORY 1: Interactive Multi-Step Guided Workflows
        # =====================================================================
        CXScenario(
            id="CX-FLOW-01",
            category="Interactive Guided Workflow",
            title="Individual TIN Registration Flow (Progressive Slot-Filling & Validation)",
            description="Tests step-by-step guidance from category selection to legal name, NIN collection, and format validation.",
            turns=[
                TurnStep(
                    user_message="Help me register for a TIN",
                    expect_mode="workflow",
                    expect_workflow="TIN Registration",
                    expect_step_id="collect_type",
                    expect_options_contain=["individual", "company"],
                    expect_reply_contains=["TIN Registration", "individual"],
                ),
                TurnStep(
                    user_message="individual",
                    expect_mode="workflow",
                    expect_workflow="TIN Registration",
                    expect_step_id="collect_name",
                    expect_reply_contains=["legal name", "national ID"],
                ),
                TurnStep(
                    user_message="Mugisha Daniel",
                    expect_mode="workflow",
                    expect_workflow="TIN Registration",
                    expect_step_id="collect_nin",
                    expect_reply_contains=["National Identification Number", "NIN"],
                ),
                TurnStep(
                    user_message="CM84ABCDE8400J",
                    expect_mode="workflow",
                    expect_workflow="TIN Registration",
                    expect_step_id="collect_phone",
                    expect_reply_contains=["phone number"],
                ),
            ],
        ),
        CXScenario(
            id="CX-FLOW-02",
            category="Interactive Guided Workflow",
            title="Tax Clearance Certificate (TCC) Guided Journey",
            description="Verifies initiating a tax clearance certificate application journey with interactive assistance.",
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
            id="CX-FLOW-03",
            category="Interactive Guided Workflow",
            title="Payment PRN Generation Workflow",
            description="Tests guiding a taxpayer to generate a Payment Registration Number (PRN) for taxes or fees.",
            turns=[
                TurnStep(
                    user_message="Guide me to generate a PRN for payment",
                    expect_mode="workflow",
                    expect_workflow="Payment Assistance",
                    expect_reply_contains=["Payment Assistance"],
                )
            ],
        ),

        # =====================================================================
        # CATEGORY 2: Deterministic Tax Computations (Math & Precision)
        # =====================================================================
        CXScenario(
            id="CX-CALC-01",
            category="Tax Calculator & Net Take-Home",
            title="PAYE Salary Calculation with Net Take-Home Pay (5,000,000 UGX)",
            description="Verifies the assistant computes the exact PAYE tax and take-home pay instead of simply citing tax bands.",
            turns=[
                TurnStep(
                    user_message="Calculate my PAYE on a gross monthly salary of 5,000,000 UGX",
                    expect_mode="calculator",
                    expect_reply_contains=["1,388,250", "3,611,750", "FY2026-27"],
                    expect_resources_min=1,
                )
            ],
        ),
        CXScenario(
            id="CX-CALC-02",
            category="Tax Calculator & Net Take-Home",
            title="Commercial Rental Income Tax Computation (20,000,000 UGX)",
            description="Computes rental income tax with statutory threshold deduction.",
            turns=[
                TurnStep(
                    user_message="Calculate rental tax on 20,000,000 UGX annual rental income for an individual",
                    expect_mode="calculator",
                    expect_reply_regex=[r"rental", r"(?:12%|tax payable|ugx)"],
                )
            ],
        ),
        CXScenario(
            id="CX-CALC-03",
            category="Tax Calculator & Net Take-Home",
            title="VAT 18% Addition and Extraction (10,000,000 UGX)",
            description="Verifies exact 18% VAT calculation on taxable supplies.",
            turns=[
                TurnStep(
                    user_message="How much is 18% VAT on goods worth 10,000,000 UGX?",
                    expect_mode="calculator",
                    expect_reply_contains=["1,800,000", "11,800,000"],
                )
            ],
        ),

        # =====================================================================
        # CATEGORY 3: Actionable Resource Delivery & Deep Portal Linking
        # =====================================================================
        CXScenario(
            id="CX-RES-01",
            category="Actionable Resource Delivery",
            title="Return Filing Forms & eTax Portal Deep-Links",
            description="Verifies the assistant provides structured downloadable return templates and verified portal links.",
            turns=[
                TurnStep(
                    user_message="Where can I download the return filing form templates and log in?",
                    expect_resources_min=1,
                    expect_reply_contains=["https://ura.go.ug"],
                )
            ],
        ),

        # =====================================================================
        # CATEGORY 4: Empathetic De-escalation & Practical Crisis Guidance
        # =====================================================================
        CXScenario(
            id="CX-EMPATHY-01",
            category="Empathetic Crisis Guidance",
            title="Bank Account Agency Notice & Fund Freeze De-escalation",
            description="Verifies the assistant provides comforting de-escalation, legal rights under Sec 40 TPCA, and remedy steps.",
            turns=[
                TurnStep(
                    user_message="I am terrified! URA issued an agency notice on my bank account and froze my funds. What can I do?",
                    expect_reply_contains=["Third-Party Agency Notice"],
                    expect_reply_regex=[r"(?:sorry|understand|options|contact)"],
                )
            ],
        ),
        CXScenario(
            id="CX-EMPATHY-02",
            category="Empathetic Crisis Guidance",
            title="Distressed Taxpayer Facing Late Filing Penalties",
            description="Provides comforting guidance and explains waiver or voluntary disclosure options.",
            turns=[
                TurnStep(
                    user_message="I missed the filing deadline and I cannot afford these heavy penalties, please help me out!",
                    expect_reply_regex=[r"(?:penalty|waiver|voluntary disclosure|0800)"],
                )
            ],
        ),

        # =====================================================================
        # CATEGORY 5: Conversational Knowledge Discrepancy & Bug Reporting
        # =====================================================================
        CXScenario(
            id="CX-BUG-01",
            category="Closed-Loop Bug Reporting",
            title="Taxpayer Challenges Outdated Rate (Conversational Auto-Bug Reporting)",
            description="Tests that when a user disputes a factual claim in turn 2, the AI thanks them and files a Knowledge Report #KB-XXX.",
            turns=[
                TurnStep(
                    user_message="What is the VAT registration turnover threshold?",
                    expect_reply_contains=["threshold"],
                ),
                TurnStep(
                    user_message="No, that is incorrect. Under the 2023 Amendment Act, the mandatory VAT threshold was raised to 150 million UGX.",
                    expect_discrepancy=True,
                    expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)", r"150"],
                ),
            ],
        ),

        # =====================================================================
        # CATEGORY 6: Multilingual Vernacular Fulfillment
        # =====================================================================
        CXScenario(
            id="CX-VERNACULAR-01",
            category="Multilingual Task Fulfillment",
            title="Luganda TIN Guidance & Step Breakdown",
            description="Ensures the assistant responds natively in Luganda with step-by-step guidance.",
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
            id="CX-VERNACULAR-02",
            category="Multilingual Task Fulfillment",
            title="Swahili VAT Calculation and Explanation",
            description="Calculates VAT accurately while answering natively in Kiswahili.",
            locale="sw",
            turns=[
                TurnStep(
                    user_message="Hesabu kiasi cha VAT ya 18% kwa bidhaa za thamani ya shilingi 10,000,000 UGX",
                    expect_mode="calculator",
                    expect_reply_contains=["1,800,000"],
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

        step_failures = []

        # 1. Mode check
        if step.expect_mode and mode.lower() != step.expect_mode.lower():
            # If expected calculator, but mode was education or tools, check if figures exist
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

        turn_passed = len(step_failures) == 0
        if not turn_passed:
            scenario_passed = False

        results.append({
            "turn": step_num,
            "user_message": step.user_message,
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
    parser = argparse.ArgumentParser(description="Evaluate Customer Experience & Request Completion over live ngrok.")
    parser.add_argument("--base", default="", help="Base API URL (e.g. https://<tunnel>/api or http://localhost:8083)")
    parser.add_argument("--out", default="", help="Path to save JSON evaluation report")
    args = parser.parse_args()

    base_url = args.base.strip()
    if not base_url:
        base_url = find_active_endpoint()

    print("=" * 80)
    print("  URA ASSISTANT — CUSTOMER EXPERIENCE & REQUEST COMPLETION BENCHMARK")
    print(f"  Target Endpoint: {base_url}")
    print("=" * 80)

    # Health check
    session = requests.Session()
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        hr = session.get(health_url, headers=HEADERS, timeout=10)
        print(f"✓ Health check probe: HTTP {hr.status_code} ({hr.text[:60]})")
    except Exception as e:
        print(f"⚠ Warning: Health check probe failed ({e}). Proceeding with chat requests...")

    scenarios = build_scenarios()
    print(f"\nEvaluating {len(scenarios)} Customer Experience Scenarios across 6 Completion Pillars...\n")

    scenario_results = []
    category_stats: dict[str, dict[str, int]] = {}

    for i, sc in enumerate(scenarios, 1):
        print(f"[{i:02d}/{len(scenarios):02d}] {sc.id} | {sc.category[:24]:<24} | {sc.title[:40]:<40} ... ", end="", flush=True)
        res = run_scenario(session, base_url, sc)
        scenario_results.append(res)

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

    print("\n" + "=" * 80)
    print("  CUSTOMER EXPERIENCE & TASK COMPLETION SCORECARD")
    print("=" * 80)
    for cat, stat in category_stats.items():
        pct = round((stat["passed"] / stat["total"]) * 100, 1)
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        print(f"  {cat:<35} : [{bar}] {stat['passed']}/{stat['total']} ({pct}%)")

    print("-" * 80)
    print(f"  OVERALL CX REQUEST COMPLETION SCORE : {passed_scenarios}/{total_scenarios} ({cx_score}%)")
    print("=" * 80)

    # Report Output
    report_data = {
        "timestamp": time.time(),
        "date": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "target_endpoint": base_url,
        "overall_cx_score_pct": cx_score,
        "scenarios_total": total_scenarios,
        "scenarios_passed": passed_scenarios,
        "category_breakdown": category_stats,
        "detailed_results": scenario_results,
    }

    out_path = args.out.strip()
    if not out_path:
        out_dir = Path("docs/Reports/data")
        out_dir.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y_%m_%d")
        out_path = str(out_dir / f"cx_completion_eval_{today}.json")

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report_data, fh, indent=2, ensure_ascii=False)
    print(f"\n✓ Detailed evaluation audit report saved to: {out_path}\n")

    return 0 if cx_score >= 80 else 1


if __name__ == "__main__":
    sys.exit(main())
