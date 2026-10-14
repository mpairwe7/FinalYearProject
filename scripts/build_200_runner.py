#!/usr/bin/env python3
"""Builds the complete scripts/evaluate_cx_200_scenarios_ngrok.py script."""

from pathlib import Path

DEST = Path("scripts/evaluate_cx_200_scenarios_ngrok.py")

RUNNER_CODE = '''#!/usr/bin/env python3
"""200-Scenario Master Customer Experience (CX) & Autonomous Request Completion Benchmark Suite.

Evaluates the URA AI Assistant over the live ngrok endpoint across 200 comprehensive,
diverse, and realistic scenarios to measure active request completion rather
than passive FAQ matching:

  Pillar 1:  Interactive Multi-Step Guided Workflows & Steppers (20 scenarios)
  Pillar 2:  Deterministic Tax Computations & Multi-Bracket Math (20 scenarios)
  Pillar 3:  Real-World Narrative Stories & Complex Business Cases (20 scenarios)
  Pillar 4:  Autonomous Transactional PRN Vouchers & Resource Delivery (20 scenarios)
  Pillar 5:  Inline EFRIS Fiscal Invoice & Thermal Receipt Audit (20 scenarios)
  Pillar 6:  Empathetic Crisis De-escalation & Legal Rights Protection (20 scenarios)
  Pillar 7:  Closed-Loop Knowledge Discrepancy & Bug Reporting (20 scenarios)
  Pillar 8:  Multilingual Task Fulfillment (Luganda & Swahili) (20 scenarios)
  Pillar 9:  Statutory Boundary Probing & Classification Integrity (20 scenarios)
  Pillar 10: Advanced Situational Advisory & Omnichannel Tracking (20 scenarios)

Usage:
  python3 scripts/evaluate_cx_200_scenarios_ngrok.py
  python3 scripts/evaluate_cx_200_scenarios_ngrok.py --base https://<ngrok-domain>/api
  python3 scripts/evaluate_cx_200_scenarios_ngrok.py --workers 5 --out docs/Reports/data/cx_200_eval.json
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

from scripts.evaluate_cx_100_scenarios_ngrok import (
    TurnStep,
    CXScenario,
    build_100_scenarios,
    run_scenario,
    DEFAULT_NGROK_DOMAIN,
    DEFAULT_BASE,
    HEADERS,
)
from scripts.extra_100_scenarios import build_extra_100_scenarios


def build_200_scenarios() -> list[CXScenario]:
    """Assemble 200 scenarios, exactly 20 per pillar, ordered CX-001 to CX-200."""
    base_100 = build_100_scenarios()
    extra_100 = build_extra_100_scenarios()

    pillars = [
        "Pillar 1: Guided Workflows",
        "Pillar 2: Deterministic Computations",
        "Pillar 3: Narrative Stories",
        "Pillar 4: Actionable Resources",
        "Pillar 5: Empathetic Crisis Guidance",
        "Pillar 6: Closed-Loop Bug Reporting",
        "Pillar 7: Multilingual Fulfillment",
        "Pillar 8: Statutory Boundary Probing",
        "Pillar 9: Omnichannel Tracking",
        "Pillar 10: Advanced Advisory",
    ]

    grouped: dict[str, list[CXScenario]] = {p: [] for p in pillars}

    # First add 10 from base_100
    for sc in base_100:
        if sc.category in grouped:
            grouped[sc.category].append(sc)

    # Then add 10 from extra_100
    for sc in extra_100:
        if sc.category in grouped:
            grouped[sc.category].append(sc)

    final_scenarios: list[CXScenario] = []
    sc_idx = 1
    for p in pillars:
        sc_list = grouped[p]
        for sc in sc_list:
            sc.id = f"CX-{sc_idx:03d}"
            sc.category = p
            final_scenarios.append(sc)
            sc_idx += 1

    return final_scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description="200-Scenario Master CX Benchmark over Live Ngrok Endpoint")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"API base URL (default: {DEFAULT_BASE})")
    parser.add_argument("--workers", type=int, default=5, help="Concurrent scenario workers (default: 5)")
    parser.add_argument("--out", default="", help="Custom output JSON path (default: docs/Reports/data/cx_200_scenarios_eval_<date>.json)")
    args = parser.parse_args()

    base_url = args.base.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]

    print("=" * 90)
    print("  URA ASSISTANT — 200-SCENARIO MASTER CX & REQUEST COMPLETION BENCHMARK")
    print(f"  Target Endpoint : {base_url}")
    print(f"  Concurrency     : {args.workers} worker(s)")
    print("=" * 90)

    # Health check
    session = requests.Session()
    health_url = f"{base_url}/health" if "/api" not in base_url else base_url.replace("/api", "/health")
    try:
        hr = session.get(health_url, headers=HEADERS, timeout=15)
        print(f"✓ Health probe: HTTP {hr.status_code} ({hr.text[:50]})\\n")
    except Exception as exc:
        print(f"✗ Health probe failed ({exc}) — proceeding anyway\\n")

    scenarios = build_200_scenarios()
    print(f"Executing 200 Customer Experience & Request Completion Scenarios across 10 Pillars...\\n")

    scenario_results: list[dict[str, Any]] = [{} for _ in range(len(scenarios))]
    category_stats: dict[str, dict[str, int]] = {}
    latencies: list[float] = []

    def _eval_worker(idx_sc: tuple[int, CXScenario]) -> tuple[int, dict[str, Any]]:
        idx, sc = idx_sc
        worker_session = requests.Session()
        res = run_scenario(worker_session, base_url, sc)
        return idx, res

    if args.workers <= 1:
        for i, sc in enumerate(scenarios, 1):
            print(f"[{i:03d}/200] {sc.id:<8} | {sc.category[:26]:<26} | {sc.title[:38]:<38} ... ", end="", flush=True)
            res = run_scenario(session, base_url, sc)
            scenario_results[i - 1] = res
            latencies.append(res["total_time_s"])
            status = "PASS" if res["passed"] else "FAIL"
            print(f"{status} ({res['total_time_s']}s)")
            if not res["passed"]:
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
                print(f"[{idx+1:03d}/200] {res['id']:<8} | {res['category'][:26]:<26} | {status} ({res['total_time_s']}s) - {res['title'][:35]}")

    transient_failures = [
        (i, sc) for i, sc in enumerate(scenarios)
        if not scenario_results[i].get("passed") and any("HTTP request failed" in str(t.get("error", "")) for t in scenario_results[i].get("turns", []))
    ]
    if transient_failures:
        print(f"\\nRetrying {len(transient_failures)} scenarios that experienced transient tunnel connection dropouts...")
        time.sleep(1.0)
        for i, sc in transient_failures:
            res = run_scenario(session, base_url, sc)
            if res.get("passed"):
                scenario_results[i] = res
                print(f"  ✓ {sc.id} passed on recovery retry ({res['total_time_s']}s)")

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

    print("\\n" + "=" * 90)
    print("  CUSTOMER EXPERIENCE & REQUEST COMPLETION MASTER SCORECARD (200 SCENARIOS)")
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
        out_path = str(out_dir / f"cx_200_scenarios_eval_{today}.json")

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report_data, fh, indent=2, ensure_ascii=False)
    print(f"\\n✓ 200-Scenario Evaluation Audit Report saved to: {out_path}\\n")

    return 0 if cx_score >= 85 else 1


if __name__ == "__main__":
    sys.exit(main())
'''

with open(DEST, "w", encoding="utf-8") as fh:
    fh.write(RUNNER_CODE)

print("scripts/evaluate_cx_200_scenarios_ngrok.py generated successfully.")
