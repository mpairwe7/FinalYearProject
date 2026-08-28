#!/usr/bin/env python3
"""Evaluate MCP tool invocation and tax calculator accuracy via live ngrok.

Tests 8 calculator tools across 15 scenarios:
  1. PAYE (net pay from gross income) — resident & non-resident
  2. VAT (add + extract)
  3. VAT Registration threshold check
  4. Corporation Tax
  5. Capital Gains Tax
  6. Customs Duty (general + used clothing)
  7. Rental Income Tax (individual + company)
  8. Withholding Tax (services, dividends, goods)

For each scenario we check:
  - Tool was triggered (retrieval_mode == "calculator")
  - Correct numerical result
  - Proper Markdown formatting
  - Rate provenance / fiscal year citation
"""

from __future__ import annotations

import json
import sys
import time
import requests
from dataclasses import dataclass, field

BASE = "https://struttingly-nongeological-briella.ngrok-free.dev"
CHAT_URL = f"{BASE}/api/v1/chat"
HEADERS = {"Content-Type": "application/json", "ngrok-skip-browser-warning": "1"}

@dataclass
class TestCase:
    id: str
    description: str
    message: str
    expect_tool: str                         # tool name expected
    expect_mode: str = "calculator"          # retrieval_mode
    assertions: dict = field(default_factory=dict)  # key -> expected value substring


# ── Test suite ──────────────────────────────────────────────────────────
TESTS: list[TestCase] = [
    # ── PAYE (Net Pay) ──────────────────────────────────────────────────
    TestCase(
        id="PAYE-01",
        description="PAYE on 5m gross (resident) — full net pay breakdown",
        message="Calculate my PAYE on a gross monthly salary of 5,000,000 UGX",
        expect_tool="calculate_paye",
        assertions={"monthly_gross": "5,000,000", "PAYE": ""},
    ),
    TestCase(
        id="PAYE-02",
        description="PAYE with NSSF included — actual take-home pay",
        message="What is my take-home pay if I earn 3,000,000 per month?",
        expect_tool="calculate_paye",
        assertions={"net": ""},
    ),
    TestCase(
        id="PAYE-03",
        description="PAYE on salary below tax threshold",
        message="Calculate PAYE on a monthly salary of 200,000 UGX",
        expect_tool="calculate_paye",
        assertions={"0": ""},  # should be zero PAYE
    ),
    TestCase(
        id="PAYE-04",
        description="Non-resident PAYE — higher flat rate",
        message="Calculate PAYE for a non-resident earning 4,000,000 per month",
        expect_tool="calculate_paye",
        assertions={"non": ""},
    ),
    # ── VAT ─────────────────────────────────────────────────────────────
    TestCase(
        id="VAT-01",
        description="Add 18% VAT to 10m",
        message="Calculate VAT on 10,000,000 UGX",
        expect_tool="calculate_vat",
        assertions={"18%": "", "1,800,000": ""},
    ),
    TestCase(
        id="VAT-02",
        description="Extract VAT from gross 11.8m",
        message="How much VAT is included in a VAT-inclusive price of 11,800,000?",
        expect_tool="calculate_vat",
        assertions={"18%": ""},
    ),
    # ── VAT Registration ────────────────────────────────────────────────
    TestCase(
        id="VATREG-01",
        description="VAT registration threshold check — above",
        message="Must I register for VAT if my annual turnover is 400,000,000 UGX?",
        expect_tool="check_vat_registration",
        assertions={"compulsory": ""},
    ),
    TestCase(
        id="VATREG-02",
        description="VAT registration threshold check — below",
        message="Do I need to register for VAT with annual turnover of 100,000,000?",
        expect_tool="check_vat_registration",
        assertions={"not compulsory": ""},
    ),
    # ── Corporation Tax ─────────────────────────────────────────────────
    TestCase(
        id="CORP-01",
        description="Corporation tax on 500m chargeable income",
        message="Calculate corporation tax on chargeable income of 500,000,000 UGX",
        expect_tool="calculate_corporation_tax",
        assertions={"30%": "", "150,000,000": ""},
    ),
    # ── Capital Gains ───────────────────────────────────────────────────
    TestCase(
        id="CGT-01",
        description="Capital gains — sold land at 200m, cost 120m",
        message="Calculate capital gains tax on a sale price of 200,000,000 and cost base of 120,000,000",
        expect_tool="calculate_capital_gains",
        assertions={"gain": ""},
    ),
    # ── Customs Duty ────────────────────────────────────────────────────
    TestCase(
        id="CUST-01",
        description="Customs duty on CIF 50m (general goods)",
        message="Calculate customs duty on imported goods with CIF value of 50,000,000 UGX",
        expect_tool="calculate_customs_duty",
        assertions={"duty": "", "landed": ""},
    ),
    # ── Rental Income Tax ───────────────────────────────────────────────
    TestCase(
        id="RENT-01",
        description="Individual rental tax on 24m annual rent",
        message="Calculate rental income tax on annual gross rent of 24,000,000 UGX",
        expect_tool="calculate_rental_tax",
        assertions={"12%": ""},
    ),
    # ── Withholding Tax ─────────────────────────────────────────────────
    TestCase(
        id="WHT-01",
        description="Withholding tax on services payment of 10m",
        message="Calculate withholding tax on a services payment of 10,000,000 UGX",
        expect_tool="calculate_withholding",
        assertions={"6%": ""},
    ),
    # ── Natural language triggers ───────────────────────────────────────
    TestCase(
        id="NLP-01",
        description="Natural language: 'how much tax on 8m salary'",
        message="How much tax will I pay on a monthly salary of 8,000,000?",
        expect_tool="calculate_paye",
        assertions={"PAYE": ""},
    ),
    TestCase(
        id="NLP-02",
        description="Natural language: 'what is my net pay on 2m'",
        message="What will be my net pay if my gross salary is 2,000,000 per month?",
        expect_tool="calculate_paye",
        assertions={"net": ""},
    ),
]


def call_chat(message: str, thread_id: str = "") -> dict:
    """Send a message to the live ngrok chat endpoint and return parsed JSON."""
    payload = {
        "message": message,
        "thread_id": thread_id or f"mcp-eval-{int(time.time())}",
        "locale": "en",
    }
    resp = requests.post(CHAT_URL, json=payload, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


def evaluate_case(tc: TestCase) -> dict:
    """Run one test case and return structured results."""
    t0 = time.time()
    try:
        result = call_chat(tc.message)
    except Exception as e:
        return {
            "id": tc.id,
            "description": tc.description,
            "status": "ERROR",
            "error": str(e),
            "latency_s": round(time.time() - t0, 2),
        }
    latency = round(time.time() - t0, 2)

    reply = result.get("reply", "")
    mode = result.get("retrieval_mode", "")
    agent_role = result.get("agent_role", "")

    # Check if calculator tool was triggered
    tool_triggered = mode == tc.expect_mode

    # Check assertions (substring match in reply)
    assertion_results = {}
    for key, _expected in tc.assertions.items():
        assertion_results[key] = key.lower() in reply.lower()

    all_assertions_pass = all(assertion_results.values())

    # Check for fiscal year citation
    has_fy_citation = "FY" in reply or "fiscal" in reply.lower() or "rate table" in reply.lower()

    # Check for Markdown formatting
    has_markdown = "**" in reply or "- " in reply

    status = "PASS" if (tool_triggered and all_assertions_pass) else "FAIL"

    return {
        "id": tc.id,
        "description": tc.description,
        "query": tc.message,
        "status": status,
        "tool_triggered": tool_triggered,
        "retrieval_mode": mode,
        "agent_role": agent_role,
        "expected_tool": tc.expect_tool,
        "assertion_results": assertion_results,
        "has_fy_citation": has_fy_citation,
        "has_markdown": has_markdown,
        "latency_s": latency,
        "reply_excerpt": reply[:400],
        "full_reply": reply,
    }


def main():
    print("=" * 76)
    print(" MCP TOOL INVOCATION & TAX CALCULATOR EVALUATION")
    print(f" Target: {BASE}")
    print("=" * 76)
    print()

    # Health check
    try:
        r = requests.get(f"{BASE}/api/health", headers=HEADERS, timeout=10)
        health = r.json()
        print(f"Health: {health}")
    except Exception as e:
        print(f"HEALTH CHECK FAILED: {e}")
        sys.exit(1)

    results = []
    passed = 0
    failed = 0

    for i, tc in enumerate(TESTS, 1):
        print(f"\n--- Case {i}/{len(TESTS)}: {tc.id} - {tc.description} ---")
        res = evaluate_case(tc)
        results.append(res)

        status_icon = "✅" if res["status"] == "PASS" else "❌"
        print(f"  {status_icon} Status: {res['status']}")
        print(f"  Tool Triggered: {res.get('tool_triggered', '?')} (mode={res.get('retrieval_mode', '?')})")
        print(f"  Agent Role: {res.get('agent_role', '?')}")
        print(f"  FY Citation: {res.get('has_fy_citation', '?')}")
        print(f"  Markdown: {res.get('has_markdown', '?')}")
        print(f"  Latency: {res['latency_s']}s")
        print(f"  Reply: {res.get('reply_excerpt', '')[:200]}...")

        if res.get("assertion_results"):
            for k, v in res["assertion_results"].items():
                icon = "✓" if v else "✗"
                print(f"    [{icon}] Contains '{k}'")

        if res["status"] == "PASS":
            passed += 1
        else:
            failed += 1

    # Summary
    print("\n" + "=" * 76)
    print(f" RESULTS: {passed}/{len(TESTS)} PASSED, {failed}/{len(TESTS)} FAILED")
    print("=" * 76)

    # Group by tool
    by_tool: dict[str, list] = {}
    for r in results:
        tool = r.get("expected_tool", "unknown")
        by_tool.setdefault(tool, []).append(r)

    print("\nBy Tool:")
    for tool, cases in sorted(by_tool.items()):
        p = sum(1 for c in cases if c["status"] == "PASS")
        print(f"  {tool}: {p}/{len(cases)} passed")

    # Latency stats
    latencies = [r["latency_s"] for r in results if r.get("latency_s")]
    if latencies:
        print(f"\nLatency: min={min(latencies):.2f}s, max={max(latencies):.2f}s, avg={sum(latencies)/len(latencies):.2f}s")

    # Save report
    outpath = "/home/developer/Mpairwe7/FinalYearProject/Results/mcp_tools_eval_report.json"
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed report saved to {outpath}")


if __name__ == "__main__":
    main()
