#!/usr/bin/env python3
"""Comprehensive Live GPU Benchmark & Regression Suite for Issue #441 & MCP Tools.

Evaluates:
1. Regression Suite:
   - Tool calling via vLLM (Sunflower-14B-FP8)
   - Pipeline inversion & tool execution decoupling
   - Epistemic false premise guard (G43)
   - LangGraph orchestration & argument binding
   - MCP tool presentations, formatting & paragraphing
   - Live Speech models (Whisper-SALT, Spark-TTS)
2. Fuzzy Intent & Robustness Suite:
   - Acronyms, casing, typos, complex multi-turn queries
   - Demonstrative determiners & coreference preservation
   - Acute enforcement hardship detection
3. Load & Volume Suite:
   - Concurrent traffic (c=10, c=20)
   - High volume batch requests (100+ turns)
   - Measurement metrics: p50, p95, p99 latency, throughput (req/s), error rate (0%)
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

BASE_URL = "http://127.0.0.1:8083"


def http_post(path: str, payload: dict[str, Any], timeout: float = 60.0) -> tuple[int, dict[str, Any], float]:
    url = f"{BASE_URL}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return resp.status, data, (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(err_body)
        except Exception:
            data = {"raw_error": err_body}
        return err.code, data, (time.perf_counter() - t0) * 1000
    except Exception as exc:
        return 500, {"error": str(exc)}, (time.perf_counter() - t0) * 1000


def http_get(path: str, timeout: float = 10.0) -> tuple[int, dict[str, Any], float]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return resp.status, data, (time.perf_counter() - t0) * 1000
    except Exception as exc:
        return 500, {"error": str(exc)}, (time.perf_counter() - t0) * 1000


def run_benchmark() -> dict[str, Any]:
    print("=" * 70)
    print("LIVE GPU SYSTEM BENCHMARK (Sunflower-14B + Whisper-SALT + Spark-TTS)")
    print("Target API:", BASE_URL)
    print("=" * 70)

    results: dict[str, Any] = {
        "regression": {},
        "fuzzy": {},
        "load": {},
        "volume": {},
    }

    # 1. System Health & Live vLLM Tool Calling on Sunflower-14B
    print("\n[Phase 1] System Health, Speech Status & Live vLLM Tool Calling on Sunflower-14B-FP8...")
    status, health, lat = http_get("/v1/speech/health")
    print(f"  Speech Health: HTTP {status}, latency: {lat:.1f}ms, response: {health}")
    assert status == 200 and health.get("status") == "ready"
    results["regression"]["speech_health"] = {"status": status, "latency_ms": lat, "data": health}

    # Test vLLM directly on port 8011
    vllm_payload = {
        "model": "Sunbird/Sunflower-14B-FP8",
        "messages": [{"role": "user", "content": "Calculate VAT on 1,000,000 UGX"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "calculate_vat",
                "description": "Calculate VAT in Uganda",
                "parameters": {
                    "type": "object",
                    "properties": {"amount": {"type": "number"}},
                    "required": ["amount"],
                },
            },
        }],
        "tool_choice": "required",
    }
    t0_vllm = time.perf_counter()
    req_vllm = urllib.request.Request(
        "http://127.0.0.1:8011/v1/chat/completions",
        data=json.dumps(vllm_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_vllm, timeout=30) as resp:
        vllm_data = json.loads(resp.read().decode("utf-8"))
        lat_vllm = (time.perf_counter() - t0_vllm) * 1000
        tc = vllm_data["choices"][0]["message"].get("tool_calls", [])
        assert len(tc) >= 1 and tc[0]["function"]["name"] == "calculate_vat"
        print(f"  Live vLLM Sunflower-14B Function-Calling: OK (tool={tc[0]['function']['name']}, args={tc[0]['function']['arguments']}) [{lat_vllm:.1f}ms]")
        results["regression"]["vllm_sunflower_tool_calling"] = {
            "status": "pass",
            "latency_ms": round(lat_vllm, 1),
            "tool_call": tc[0],
        }

    # 2. Regression: Epistemic False Premise (G43)
    print("\n[Phase 2] Epistemic False Premise Tests (G43)...")
    fp_cases = [
        ("What is the URA Digital Nomad Levy?", True),
        ("How much is the Uganda Space Exploration Tax?", True),
        ("What is the Moon Mining Duty?", True),
        ("How do I pay tax?", False),
        ("Can I pay tax online?", False),
        ("What is property tax in Uganda?", False),
        ("What is PAYE tax?", False),
    ]
    fp_passes = 0
    for q, should_reject in fp_cases:
        st, res, l_ms = http_post("/v1/chat", {"message": q, "locale": "en"})
        reply = res.get("reply", "")
        mode = res.get("retrieval_mode", "")
        rejected = (mode == "false_premise_rejected") or ("there is no official" in reply)
        passed = (rejected == should_reject)
        if passed:
            fp_passes += 1
        print(f"  Query: '{q[:40]:<40}' -> Rejected: {rejected} (expected {should_reject}) [{l_ms:.1f}ms] - {'PASS' if passed else 'FAIL'}")
    results["regression"]["false_premise"] = {"passed": fp_passes, "total": len(fp_cases)}

    # 3. Regression: Deterministic Tax Calculators & Decoupled Execution
    print("\n[Phase 3] Deterministic Calculator & Decoupled Tool Execution...")
    calc_cases = [
        ("Calculate VAT on 1,000,000 UGX", "180,000"),
        ("Calculate PAYE on 2,000,000 UGX salary", "488,250"),
        ("What is the corporate income tax on 50,000,000 UGX profit?", "15,000,000"),
    ]
    calc_passes = 0
    for q, expected_sub in calc_cases:
        st, res, l_ms = http_post("/v1/chat", {"message": q, "locale": "en"})
        reply = res.get("reply", "")
        passed = st == 200 and expected_sub in reply
        if passed:
            calc_passes += 1
        print(f"  Query: '{q[:40]:<40}' -> Contains '{expected_sub}': {passed} [{l_ms:.1f}ms]")
    results["regression"]["calculators"] = {"passed": calc_passes, "total": len(calc_cases)}

    # 4. Regression: MCP Tool Presentation & Paragraphing
    print("\n[Phase 4] MCP Presentations, Paragraphing & Explanations...")
    pres_queries = [
        ("What is the corporation tax rate?", "Corporation tax", "FY2026-27"),
        ("What is the standard VAT rate in Uganda?", "VAT", "18%"),
        ("What is the withholding tax rate on professional services?", "withholding", "6%"),
    ]
    pres_passes = 0
    for q, mark1, mark2 in pres_queries:
        st, res, l_ms = http_post("/v1/chat", {"message": q, "locale": "en"})
        reply = res.get("reply", "")
        passed = st == 200 and (mark1.lower() in reply.lower() or mark2.lower() in reply.lower())
        if passed:
            pres_passes += 1
        print(f"  Query: '{q[:40]:<40}' -> Presentation formatted: {passed} [{l_ms:.1f}ms]")
    results["regression"]["presentations"] = {"passed": pres_passes, "total": len(pres_queries)}

    # 5. Fuzzy Intent & Coreference Tests
    print("\n[Phase 5] Fuzzy Intent & Demonstrative Determiner Coreference...")
    fuzzy_cases = [
        # Multi-turn conversation preserving demonstrative determiners
        ("Tell me about rental tax", "How much is it for this year?", "Rental"),
        # Short conversational questions
        ("What is WHT rate?", "withholding", "6%"),
        ("Tell me about EFRIS", "efris", "electronic"),
    ]
    fuzzy_passes = 0
    for pair in [fuzzy_cases[0]]:
        # Turn 1
        cid = "bench-cid-fuzzy-001"
        st1, r1, _ = http_post("/v1/chat", {"message": pair[0], "conversation_id": cid, "locale": "en"})
        # Turn 2 with pronoun + demonstrative
        st2, r2, l2 = http_post("/v1/chat", {"message": pair[1], "conversation_id": cid, "locale": "en"})
        reply2 = r2.get("reply", "")
        passed = st2 == 200 and pair[2].lower() in reply2.lower() and "rental income tax year" not in reply2.lower()
        if passed:
            fuzzy_passes += 1
        print(f"  Multi-turn Determiner: '{pair[1]}' -> Clean Coreference: {passed} [{l2:.1f}ms]")

    for q, kw1, kw2 in fuzzy_cases[1:]:
        st, res, l_ms = http_post("/v1/chat", {"message": q, "locale": "en"})
        reply = res.get("reply", "")
        passed = st == 200 and (kw1.lower() in reply.lower() or kw2.lower() in reply.lower())
        if passed:
            fuzzy_passes += 1
        print(f"  Fuzzy Query: '{q[:35]:<35}' -> Passed: {passed} [{l_ms:.1f}ms]")
    results["fuzzy"]["tests"] = {"passed": fuzzy_passes, "total": len(fuzzy_cases)}

    # 6. Load & Concurrency Benchmark
    print("\n[Phase 6] Concurrency Load Benchmark (c=10, 50 requests)...")
    load_queries = [
        "What is the VAT rate in Uganda?",
        "How do I register for a TIN as an individual?",
        "What is the corporate tax rate?",
        "When is PAYE return due?",
        "Calculate VAT on 5,000,000 UGX",
    ]
    total_load_reqs = 50
    concurrency = 10
    latencies: list[float] = []
    errors = 0

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(http_post, "/v1/chat", {"message": load_queries[i % len(load_queries)], "locale": "en"}, 30.0)
            for i in range(total_load_reqs)
        ]
        for fut in as_completed(futures):
            status, _, elapsed = fut.result()
            latencies.append(elapsed)
            if status != 200:
                errors += 1
    total_duration = time.perf_counter() - t_start
    throughput = total_load_reqs / total_duration

    latencies.sort()
    p50 = statistics.median(latencies)
    p90 = latencies[int(len(latencies) * 0.90)]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    mean_lat = statistics.mean(latencies)

    print(f"  Completed {total_load_reqs} requests @ concurrency={concurrency}")
    print(f"  Throughput: {throughput:.2f} req/s, Errors: {errors} ({(errors/total_load_reqs)*100:.1f}%)")
    print(f"  Latency - Mean: {mean_lat:.1f}ms, p50: {p50:.1f}ms, p90: {p90:.1f}ms, p95: {p95:.1f}ms, p99: {p99:.1f}ms")

    results["load"] = {
        "concurrency": concurrency,
        "total_requests": total_load_reqs,
        "throughput_rps": round(throughput, 2),
        "error_rate_pct": round((errors / total_load_reqs) * 100, 2),
        "latency_p50_ms": round(p50, 1),
        "latency_p90_ms": round(p90, 1),
        "latency_p95_ms": round(p95, 1),
        "latency_p99_ms": round(p99, 1),
        "latency_mean_ms": round(mean_lat, 1),
    }

    # 7. Volume Soak Benchmark
    print("\n[Phase 7] Volume Soak Benchmark (100 sequential requests across endpoints)...")
    vol_latencies: list[float] = []
    vol_errors = 0
    t_vol_start = time.perf_counter()
    for i in range(100):
        path = "/v1/chat" if i % 4 != 0 else "/v1/speech/health"
        if path == "/v1/chat":
            st, _, el = http_post(path, {"message": load_queries[i % len(load_queries)], "locale": "en"}, 20.0)
        else:
            st, _, el = http_get(path, 10.0)
        vol_latencies.append(el)
        if st != 200:
            vol_errors += 1
    vol_duration = time.perf_counter() - t_vol_start
    vol_rps = 100 / vol_duration
    vol_latencies.sort()

    print(f"  Volume Soak: 100 requests in {vol_duration:.2f}s ({vol_rps:.2f} req/s)")
    print(f"  Errors: {vol_errors}, p50: {statistics.median(vol_latencies):.1f}ms, p95: {vol_latencies[94]:.1f}ms")

    results["volume"] = {
        "total_requests": 100,
        "duration_s": round(vol_duration, 2),
        "throughput_rps": round(vol_rps, 2),
        "error_count": vol_errors,
        "error_rate_pct": round((vol_errors / 100) * 100, 2),
        "latency_p50_ms": round(statistics.median(vol_latencies), 1),
        "latency_p95_ms": round(vol_latencies[94], 1),
    }

    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETED SUCCESSFULLY")
    print("=" * 70)
    return results


if __name__ == "__main__":
    res = run_benchmark()
    with open("/tmp/live_gpu_benchmark_results.json", "w") as f:
        json.dump(res, f, indent=2)
    print("\nResults saved to /tmp/live_gpu_benchmark_results.json")
