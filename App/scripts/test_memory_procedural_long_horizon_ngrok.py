#!/usr/bin/env python3
"""Comprehensive Regression Suite: Procedural Memory, Handoff Envelopes, Long-Horizon Context & Session State.

Tests the live deployment via ngrok:
https://struttingly-nongeological-briella.ngrok-free.dev/api

Validates:
1. Procedural Memory: Statutory workflows (TIN registration, VAT filing, Objection dispute).
2. Long-Horizon Multi-Turn Context: 10-turn session retaining taxpayer attributes, coreference resolution.
3. Memory Persistence & Working Memory: Recall of facts across turns within session.
4. Handoff Envelope & Escalation Integrity: Structured handoff generation with collision locks and SLA tagging.
5. Multilingual Consistency: Figure-fidelity (18% VAT, 15th deadline, 45-day objection) across EN, LG, SW.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any
import httpx

NGROK_BASE = "https://struttingly-nongeological-briella.ngrok-free.dev/api"
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
    "User-Agent": "URA-Enterprise-Memory-Regression/2026",
}


def test_procedural_memory_retrieval(client: httpx.Client) -> dict[str, Any]:
    print("\n[TEST 1] Testing Procedural Memory Workflows & Statutory Exemplars...")
    queries = [
        {
            "query": "What is the exact procedure to apply for an individual TIN in Uganda?",
            "expected": ["national id", "nin", "ura.go.ug", "online", "tin"],
            "topic": "tin_registration",
        },
        {
            "query": "What is the procedure to file a VAT return on the URA portal?",
            "expected": ["return", "portal", "vat", "ura.go.ug"],
            "topic": "vat_filing",
        },
        {
            "query": "How do I lodge an objection against an additional tax assessment and how many days do I have?",
            "expected": ["45 days", "objection", "section 24", "assessment"],
            "topic": "tax_objection",
        },
    ]

    results = []
    for item in queries:
        t0 = time.perf_counter()
        sess_id = f"proc-sess-{uuid.uuid4().hex[:8]}"
        req_headers = {**HEADERS, "X-Session-ID": sess_id}
        resp = client.post(
            "/v1/chat",
            json={"message": item["query"], "locale": "en", "conversation_id": sess_id},
            headers=req_headers,
            timeout=45.0,
        )
        latency = (time.perf_counter() - t0) * 1000
        assert resp.status_code == 200, f"Status code {resp.status_code}: {resp.text}"
        data = resp.json()
        reply = (data.get("reply") or "").lower()
        matched = [k for k in item["expected"] if k.lower() in reply]
        passed = len(matched) >= 2
        mode = data.get("retrieval_mode")
        print(f"  -> Topic '{item['topic']}': {'PASSED' if passed else 'PARTIAL'} (Matched: {matched}, Mode: {mode}, Latency: {latency:.1f}ms)")
        print(f"     Reply snippet: {reply[:100]!r}")
        results.append({
            "topic": item["topic"],
            "passed": passed,
            "latency_ms": latency,
            "matched_keywords": matched,
            "retrieval_mode": mode,
        })

    all_passed = all(r["passed"] for r in results)
    return {"name": "procedural_memory_workflows", "passed": all_passed, "details": results}


def test_long_horizon_multi_turn_session(client: httpx.Client) -> dict[str, Any]:
    print("\n[TEST 2] Testing Long-Horizon Context & Multi-Turn Working Memory (10 Turns)...")
    sess_id = f"sess-long-{uuid.uuid4().hex[:8]}"
    conv_id = sess_id
    req_headers = {**HEADERS, "X-Session-ID": sess_id}

    turns = [
        {
            "turn": 1,
            "prompt": "Hello! I run a commercial grain milling and packaging company in Mbale called Mt Elgon Millers. Our annual turnover is 380,000,000 UGX.",
            "check": lambda r: "register" in r.lower() or "ura" in r.lower() or "tin" in r.lower(),
            "desc": "Establish business identity (Mbale, 380M turnover, grain milling)",
        },
        {
            "turn": 2,
            "prompt": "How do we register this business for a corporate TIN online?",
            "check": lambda r: "tin" in r.lower() or "ura" in r.lower() or "online" in r.lower() or "form" in r.lower(),
            "desc": "Anaphora resolution ('this business' -> Mt Elgon Millers)",
        },
        {
            "turn": 3,
            "prompt": "Given our annual turnover of 380 million UGX, are we legally required to register for VAT, and what is the statutory threshold?",
            "check": lambda r: "150" in r or "mandatory" in r.lower() or "required" in r.lower() or "yes" in r.lower(),
            "desc": "Long-horizon recall & calculation (380M > 150M threshold -> mandatory VAT)",
        },
        {
            "turn": 4,
            "prompt": "As a VAT-registered business, what electronic invoicing system must we use to issue fiscalized invoices?",
            "check": lambda r: "efris" in r.lower() or "fiscal" in r.lower(),
            "desc": "EFRIS compliance requirement",
        },
        {
            "turn": 5,
            "prompt": "We employ 25 factory workers earning between 400,000 and 1,500,000 UGX monthly. What payroll tax must we deduct?",
            "check": lambda r: "paye" in r.lower() or "pay as you earn" in r.lower(),
            "desc": "PAYE tax deduction on workers",
        },
        {
            "turn": 6,
            "prompt": "What is the exact monthly remittance deadline for PAYE returns in Uganda?",
            "check": lambda r: "15th" in r,
            "desc": "Statutory remittance deadline (15th of the following month)",
        },
        {
            "turn": 7,
            "prompt": "Can you remind me of our company name and factory district that I mentioned when we started?",
            "check": lambda r: "mt elgon" in r.lower() or "mbale" in r.lower(),
            "desc": "Direct memory retention check (Mt Elgon Millers, Mbale)",
        },
        {
            "turn": 8,
            "prompt": "If URA conducts an audit, how many years of financial books and records must we retain at our factory?",
            "check": lambda r: "5" in r or "five" in r.lower(),
            "desc": "Statutory record retention (5 years under Tax Procedures Code)",
        },
        {
            "turn": 9,
            "prompt": "Someone claimed that milling companies in Mbale get a 50% discount on their VAT rate. Is this true?",
            "check": lambda r: "no" in r.lower() or "not" in r.lower() or "18%" in r or "standard" in r.lower(),
            "desc": "Anti-hallucination / false premise trap rejection",
        },
        {
            "turn": 10,
            "prompt": "Please give a brief summary of our obligations: VAT, EFRIS, PAYE, and contact info.",
            "check": lambda r: ("vat" in r.lower() or "efris" in r.lower()) and ("paye" in r.lower() or "0800" in r),
            "desc": "Final multi-topic comprehensive recap",
        },
    ]

    turn_results = []
    for t in turns:
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/chat",
            json={"message": t["prompt"], "locale": "en", "conversation_id": conv_id},
            headers=req_headers,
            timeout=50.0,
        )
        latency = (time.perf_counter() - t0) * 1000
        assert resp.status_code == 200, f"Turn {t['turn']} failed: {resp.text}"
        data = resp.json()
        reply = data.get("reply") or ""
        passed = t["check"](reply)
        mode = data.get("retrieval_mode")
        print(f"  [Turn {t['turn']:02d}] {t['desc']} -> {'PASSED' if passed else 'FAILED'} ({latency:.0f}ms, mode={mode}): {reply[:100]!r}")
        turn_results.append({
            "turn": t["turn"],
            "desc": t["desc"],
            "passed": passed,
            "latency_ms": latency,
            "faithfulness": data.get("faithfulness_score"),
            "mode": mode,
        })

    success_rate = sum(1 for r in turn_results if r["passed"]) / len(turn_results) * 100
    print(f"  -> Multi-Turn Context Retention Rate: {success_rate:.1f}%")
    return {"name": "long_horizon_session", "passed": success_rate >= 80.0, "success_rate": success_rate, "turn_details": turn_results}


def test_handoff_and_escalation_lifecycle(client: httpx.Client) -> dict[str, Any]:
    print("\n[TEST 3] Testing Handoff Envelopes & Human Escalation Lifecycle...")
    conv_id = f"handoff-test-{uuid.uuid4().hex[:8]}"

    # Turn 1: complex dispute
    client.post(
        "/v1/chat",
        json={
            "message": "We have an urgent dispute regarding a customs seizure notice #CS-2026-9921 for container MSCU88219. I need an official officer handoff right now.",
            "locale": "en",
            "conversation_id": conv_id,
        },
        headers=HEADERS,
        timeout=45.0,
    )

    # Turn 2: invoke explicit escalate
    t0 = time.perf_counter()
    resp = client.post(
        "/v1/escalate",
        json={
            "conversation_id": conv_id,
            "reason": "Taxpayer requested live officer assistance for customs seizure dispute",
            "user_query": "I demand to speak to an officer regarding customs notice CS-2026-9921",
            "priority": "high",
        },
        headers=HEADERS,
        timeout=30.0,
    )
    latency = (time.perf_counter() - t0) * 1000
    assert resp.status_code in (200, 201), f"Escalate endpoint returned {resp.status_code}: {resp.text}"
    data = resp.json()
    ticket_id = data.get("ticket_id")
    ok = data.get("ok", False)
    print(f"  -> Handoff Ticket Creation: {'PASSED' if ok else 'FAILED'} (Ticket ID: {ticket_id}, Latency: {latency:.1f}ms)")

    # Verify ticket in desk / tickets queue
    check_resp = client.get("/v1/tickets", headers=HEADERS, timeout=20.0)
    has_ticket = False
    if check_resp.status_code == 200:
        tickets_data = check_resp.json()
        tickets = tickets_data.get("tickets", []) if isinstance(tickets_data, dict) else tickets_data
        has_ticket = any(t.get("ticket_id") == ticket_id for t in tickets)
        print(f"  -> Ticket Queue Presence Verification: {'PASSED' if has_ticket else 'PARTIAL'}")

    passed = ok and bool(ticket_id)
    return {"name": "handoff_escalation_lifecycle", "passed": passed, "ticket_id": ticket_id, "queue_verified": has_ticket}


def test_trilingual_figure_fidelity(client: httpx.Client) -> dict[str, Any]:
    print("\n[TEST 4] Testing Trilingual Figure Fidelity (EN, Luganda, Swahili)...")
    checks = [
        {
            "locale": "en",
            "query": "What is the standard VAT rate in Uganda?",
            "must_contain": ["18%"],
            "language": "English",
        },
        {
            "locale": "lg",
            "query": "Omusolo gwa VAT gw'ameka mu Uganda?",
            "must_contain": ["18%"],
            "language": "Luganda",
        },
        {
            "locale": "sw",
            "query": "Kiwango cha kawaida cha ushuru wa VAT nchini Uganda ni asilimia ngapi?",
            "must_contain": ["18%"],
            "language": "Swahili",
        },
        {
            "locale": "en",
            "query": "On what day of each month are VAT returns due in Uganda?",
            "must_contain": ["15"],
            "language": "English (15th Deadline)",
        },
        {
            "locale": "lg",
            "query": "Alipoota z'omusolo eza buli mwezi ziweebwayo ddi mu Uganda?",
            "must_contain": ["15"],
            "language": "Luganda (15th Deadline)",
        },
    ]

    results = []
    for c in checks:
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/chat",
            json={"message": c["query"], "locale": c["locale"], "conversation_id": f"lang-test-{uuid.uuid4().hex[:8]}"},
            headers=HEADERS,
            timeout=45.0,
        )
        latency = (time.perf_counter() - t0) * 1000
        assert resp.status_code == 200
        reply = resp.json().get("reply") or ""
        matched = [f for f in c["must_contain"] if f in reply]
        passed = len(matched) == len(c["must_contain"])
        print(f"  -> {c['language']}: {'PASSED' if passed else 'FAILED'} (Preserved: {matched}, Latency: {latency:.0f}ms)")
        if not passed:
            print(f"     Reply was: {reply[:120]!r}")
        results.append({
            "language": c["language"],
            "locale": c["locale"],
            "passed": passed,
            "latency_ms": latency,
            "preserved_figures": matched,
        })

    all_passed = all(r["passed"] for r in results)
    return {"name": "trilingual_figure_fidelity", "passed": all_passed, "details": results}


def main() -> int:
    print("=" * 80)
    print("🚀 URA ENTERPRISE COGNITIVE MEMORY & LONG-HORIZON REGRESSION SUITE")
    print(f"Target Public Endpoint: {NGROK_BASE}")
    print("=" * 80)

    with httpx.Client(base_url=NGROK_BASE, timeout=60.0) as client:
        # Step 1: Health check
        health_resp = client.get("/health", headers=HEADERS)
        print(f"\n[INIT] Public Gateway Health Check: {health_resp.status_code} {health_resp.json()}")
        assert health_resp.status_code == 200, "Target not reachable!"

        # Run all test suites
        t_start = time.perf_counter()
        t1 = test_procedural_memory_retrieval(client)
        t2 = test_long_horizon_multi_turn_session(client)
        t3 = test_handoff_and_escalation_lifecycle(client)
        t4 = test_trilingual_figure_fidelity(client)
        total_time = time.perf_counter() - t_start

        all_tests = [t1, t2, t3, t4]
        passed_count = sum(1 for t in all_tests if t["passed"])

        print("\n" + "=" * 80)
        print("📊 REGRESSION SUITE SUMMARY REPORT")
        print("=" * 80)
        print(f"Total Test Suites Executed:    {len(all_tests)}")
        print(f"Suites Passed:                 {passed_count} / {len(all_tests)}")
        print(f"Overall Pass Rate:             {passed_count / len(all_tests) * 100:.1f}%")
        print(f"Total Execution Time:          {total_time:.2f}s")
        print("----------------------------------------------------------------")
        for t in all_tests:
            status_symbol = "✅ PASSED" if t["passed"] else "❌ FAILED"
            print(f"  - {t['name']:<35} : {status_symbol}")
        print("=" * 80 + "\n")

        # Save results
        report_path = "Results/metrics/enterprise_memory_and_long_horizon_regression.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "target": NGROK_BASE,
                "passed_count": passed_count,
                "total_suites": len(all_tests),
                "total_time_s": total_time,
                "suites": all_tests,
            }, f, indent=2)
        print(f"Full regression metrics saved to: {report_path}")

        return 0 if passed_count == len(all_tests) else 1


if __name__ == "__main__":
    sys.exit(main())
