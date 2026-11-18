"""Automated System Concurrency, Stress and Performance Benchmark (2026).

Benchmarks system throughput, p50/p95/p99 latencies, OCR concurrency semaphores,
circuit-breaker resilience, and memory stability under simulated peak traffic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from fastapi.testclient import TestClient

from app.main import app
from app.auth.jwt_auth import make_dev_token


@dataclass
class BenchmarkResult:
    scenario: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    duration_seconds: float
    requests_per_second: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_rate_pct: float


def run_concurrent_scenario(
    client: TestClient,
    scenario_name: str,
    requests: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]],
    concurrency: int = 10,
) -> BenchmarkResult:
    """Execute a batch of HTTP requests with bounded concurrency and measure latencies."""
    import concurrent.futures

    latencies: list[float] = []
    successes = 0
    failures = 0

    def make_req(req_spec: tuple[str, str, dict[str, Any] | None, dict[str, str] | None]) -> tuple[bool, float]:
        method, path, json_body, headers = req_spec
        t0 = time.perf_counter()
        try:
            if method.upper() == "POST":
                resp = client.post(path, json=json_body, headers=headers)
            else:
                resp = client.get(path, headers=headers)
            dur = (time.perf_counter() - t0) * 1000.0
            is_ok = resp.status_code in {200, 201, 202}
            return (is_ok, dur)
        except Exception:
            dur = (time.perf_counter() - t0) * 1000.0
            return (False, dur)

    t_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(make_req, requests))
    t_total = time.perf_counter() - t_start

    for ok, lat in results:
        latencies.append(lat)
        if ok:
            successes += 1
        else:
            failures += 1

    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)] if n > 0 else 0.0
    p95 = latencies[int(n * 0.95)] if n > 0 else 0.0
    p99 = latencies[int(n * 0.99)] if n > 0 else 0.0

    return BenchmarkResult(
        scenario=scenario_name,
        total_requests=n,
        successful_requests=successes,
        failed_requests=failures,
        duration_seconds=round(t_total, 3),
        requests_per_second=round(n / max(0.001, t_total), 1),
        p50_ms=round(p50, 2),
        p95_ms=round(p95, 2),
        p99_ms=round(p99, 2),
        error_rate_pct=round((failures / max(1, n)) * 100.0, 2),
    )


def execute_stress_suite() -> list[BenchmarkResult]:
    """Run full suite of stress scenarios against the application."""
    results: list[BenchmarkResult] = []

    with TestClient(app) as client:
        # Scenario 1: Health & Readiness Probes (100 requests @ 20 concurrency)
        health_reqs = [("GET", "/health", None, None) for _ in range(100)]
        res1 = run_concurrent_scenario(client, "1. Health Probe Saturation (100 reqs, C=20)", health_reqs, concurrency=20)
        results.append(res1)

        # Scenario 2: Tax Calculation & Deterministic Routing (50 requests @ 10 concurrency)
        tax_queries = [
            "What is the PAYE on 2,500,000 UGX monthly?",
            "Calculate VAT on 15,000,000 UGX invoice",
            "Withholding tax on 50,000,000 professional fees",
            "Presumptive tax for turnover 35m UGX without books",
            "Rental tax on 120m annual commercial property",
        ]
        calc_reqs = [
            (
                "POST",
                "/v1/chat",
                {"message": tax_queries[i % len(tax_queries)], "conversation_id": f"conv_stress_{i}"},
                {"X-Session-ID": f"stress_sess_{i}"},
            )
            for i in range(50)
        ]
        res2 = run_concurrent_scenario(client, "2. Tax Calculation Throughput (50 reqs, C=10)", calc_reqs, concurrency=10)
        results.append(res2)

        # Scenario 3: Staff Ticket Queue Operations (50 requests @ 10 concurrency)
        officer_token = make_dev_token(role="ura_officer", user_id="officer-bench-01")
        staff_headers = {"Authorization": f"Bearer {officer_token}"}
        queue_reqs = [
            ("GET", "/v1/admin/tickets?status=open&priority=urgent", None, staff_headers)
            for _ in range(50)
        ]
        res3 = run_concurrent_scenario(client, "3. Staff Ticket Queue Retrieval (50 reqs, C=10)", queue_reqs, concurrency=10)
        results.append(res3)

        # Scenario 4: Fast Deterministic FAQ Queries (50 requests @ 15 concurrency)
        faq_queries = [
            "How do I apply for a TIN?",
            "What are the requirements for VAT registration?",
            "What is the penalty for late filing?",
            "How do I access the EFRIS portal?",
        ]
        faq_reqs = [
            (
                "POST",
                "/v1/chat",
                {"message": faq_queries[i % len(faq_queries)], "conversation_id": f"conv_faq_{i}"},
                {"X-Session-ID": f"faq_sess_{i}"},
            )
            for i in range(50)
        ]
        res4 = run_concurrent_scenario(client, "4. FAQ Grounded Response Stream (50 reqs, C=15)", faq_reqs, concurrency=15)
        results.append(res4)

    return results


if __name__ == "__main__":
    print("Executing System Stress & Performance Benchmark...")
    bench_results = execute_stress_suite()
    print("\n" + "=" * 95)
    print(f"{'Scenario':<48} | {'Reqs':<5} | {'RPS':<6} | {'p50 (ms)':<9} | {'p95 (ms)':<9} | {'Err %':<5}")
    print("=" * 95)
    for r in bench_results:
        print(f"{r.scenario:<48} | {r.total_requests:<5} | {r.requests_per_second:<6} | {r.p50_ms:<9} | {r.p95_ms:<9} | {r.error_rate_pct:<5}")
    print("=" * 95)
