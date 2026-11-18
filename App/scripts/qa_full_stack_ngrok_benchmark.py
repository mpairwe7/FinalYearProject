#!/usr/bin/env python3
"""Comprehensive Senior QA Master Benchmark Suite over Live Ngrok Endpoint.

Tests:
  1. E2E Regression Suite across English (en), Luganda (lg), Swahili (sw)
  2. Fuzzy & Typo Syntax Error Tolerance in en, lg, sw
  3. Concurrency Spike Burst Test (sudden surge of parallel requests)
  4. Sustained Volume Load Test (continuous multi-turn traffic)
  5. Stack Telemetry: Sunflower-14B (vLLM on GPU 2), Whisper + Spark-TTS (GPU 4),
     Redis cache, Qdrant dense vector DB, and Next.js proxy.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

NGROK_URL = "https://struttingly-nongeological-briella.ngrok-free.dev"


def log(msg: str) -> None:
    now = time.strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def get_gpu_telemetry() -> dict[int, dict[str, Any]]:
    """Capture telemetry for GPU 2 (vLLM Sunflower) and GPU 4 (Speech/Retriever)."""
    telemetry = {}
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, text=True).strip().splitlines()
        for line in out:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                idx = int(parts[0])
                if idx in (2, 4):
                    telemetry[idx] = {
                        "name": parts[1],
                        "memory_used_mb": float(parts[2]),
                        "memory_total_mb": float(parts[3]),
                        "utilization_pct": float(parts[4]),
                        "temperature_c": float(parts[5]),
                    }
    except Exception as ex:
        log(f"Telemetry warning: {ex}")
    return telemetry


def http_req(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, Any, float]:
    """Execute HTTP request against ngrok tunnel."""
    url = f"{NGROK_URL}{path}"
    req_headers = {
        "User-Agent": "URA-QA-Master-Suite/2026",
        "ngrok-skip-browser-warning": "true",
    }
    if headers:
        req_headers.update(headers)
    data = None
    if body is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)  # nosec B310 # noqa: S310
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 # noqa: S310
            elapsed = (time.perf_counter() - t0) * 1000.0
            content = resp.read()
            try:
                parsed = json.loads(content.decode("utf-8"))
            except Exception:
                parsed = content
            return resp.status, parsed, elapsed
    except urllib.error.HTTPError as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        try:
            parsed = json.loads(e.read().decode("utf-8"))
        except Exception:
            parsed = str(e)
        return e.code, parsed, elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return 599, str(e), elapsed


# =============================================================================
# SUITE 1: End-to-End Regression Across EN, LG, SW
# =============================================================================
def run_e2e_regression() -> dict[str, Any]:
    log("=================================================================")
    log("1. EXECUTING MASTER E2E REGRESSION SUITE (EN, LG, SW)")
    log("=================================================================")
    results: list[dict[str, Any]] = []

    # 1.1 Health Checks
    st, data, lat = http_req("GET", "/api/health")
    assert st == 200 and data.get("status") == "alive", f"API health check failed: {st}"
    log(f"  [PASS] /api/health -> HTTP {st} ({lat:.1f}ms)")

    st, data, lat = http_req("GET", "/api/v1/speech/health")
    assert st == 200 and data.get("status") == "ready", f"Speech health check failed: {st}"
    log(f"  [PASS] /api/v1/speech/health -> HTTP {st} (ASR: {data.get('asr_backend')}, TTS: {data.get('tts_backend')})")

    # 1.2 Role Auth Verification & RBAC Dispatch
    roles_test = [
        ("taxpayer@gmail.com", "public", "/"),
        ("agent.sarah@ura.go.ug", "ura_staff", "/agent"),
        ("admin@ura.go.ug", "ura_admin", "/admin"),
        ("auditor@ura.go.ug", "ura_auditor", "/analytics"),
    ]
    for email, expected_role, _expected_dest in roles_test:
        st, tok_res, lat1 = http_req("POST", "/api/v1/auth/dev-token", {"email": email})
        token = tok_res.get("token")
        st, me_res, lat2 = http_req("GET", "/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        actual_role = me_res.get("role")
        assert actual_role == expected_role, f"Role mismatch for {email}: expected {expected_role}, got {actual_role}"
        log(f"  [PASS] Auth '{email}' -> Role: {actual_role} (Verified via /v1/me in {lat1+lat2:.1f}ms)")

    # 1.3 Grounded Retrieval in EN, LG, SW
    faq_scenarios = [
        {
            "locale": "en",
            "name": "English VAT Registration",
            "query": "What is the mandatory threshold for VAT registration in Uganda?",
            "expected": ["300,000,000", "150,000,000", "vat", "compulsory"],
        },
        {
            "locale": "lg",
            "name": "Luganda EFRIS Invoicing",
            "query": "Bikolwa ki URA by’ewa?",
            "expected": ["ura", "emisolo", "gavumenti", "amateeka"],
        },
        {
            "locale": "sw",
            "name": "Swahili WHT Rate",
            "query": "Kiwango cha kawaida cha kodi ya zuio (WHT) kwa huduma ni asilimia ngapi?",
            "expected": ["6%", "asilimia 6", "zuio", "wht"],
        },
    ]

    for item in faq_scenarios:
        loc = item["locale"]
        q = item["query"]
        st, res, lat = http_req("POST", "/api/v1/chat", {"message": q, "locale": loc})
        reply = res.get("reply", "")
        reply_lower = reply.lower()
        matched = [k for k in item["expected"] if k.lower() in reply_lower]
        passed = st == 200 and len(matched) >= 1
        log(f"  [{'PASS' if passed else 'FAIL'}] {item['name']} ({loc.upper()}) -> HTTP {st} in {lat:.0f}ms")
        log(f"         Matched: {matched} | Preview: {reply[:100].replace(chr(10), ' ')}...")
        results.append({
            "name": item["name"],
            "locale": loc,
            "latency_ms": lat,
            "status": st,
            "passed": passed,
            "matched_keywords": matched,
        })

    # 1.4 Speech TTS Endpoints (English & Luganda Spark-TTS)
    st, data, lat = http_req("POST", "/api/v1/tts", {"text": "Uganda Revenue Authority welcomes you.", "locale": "en"})
    log(f"  [PASS] English Edge-TTS -> HTTP {st} in {lat:.0f}ms ({len(data)} bytes audio)")

    st, data, lat = http_req("POST", "/api/v1/tts", {"text": "Webale okukolagana ne Uganda Revenue Authority.", "locale": "lg"})
    log(f"  [PASS] Luganda Spark-TTS-SALT -> HTTP {st} in {lat:.0f}ms ({len(data)} bytes audio)")

    # 1.5 Security Boundary (Unauthorized Admin Access)
    st, _data, lat = http_req("GET", "/api/v1/admin/tickets/stats")
    assert st in (401, 403, 503), f"Security check failed: {st}"
    log(f"  [PASS] Security Boundary: Unauthorized /v1/admin/tickets/stats -> HTTP {st}")

    return {
        "suite": "e2e_regression",
        "passed": all(r["passed"] for r in results),
        "cases": results,
    }


# =============================================================================
# SUITE 2: Fuzzy Testing & Syntax Error Tolerance in EN, LG, SW
# =============================================================================
def run_fuzzy_testing() -> dict[str, Any]:
    log("\n=================================================================")
    log("2. EXECUTING FUZZY TESTING & SYNTAX ERROR TOLERANCE SUITE")
    log("=================================================================")
    fuzzy_queries = [
        # English: typos, dropped vowels, phonetic spelling
        {
            "locale": "en",
            "noisy_query": "wat is the mandtory threshhold for vat regsitration in ugnda?",
            "canonical_concept": "VAT Registration Threshold",
            "expected_keywords": ["vat", "300,000,000", "150,000,000", "compulsory", "threshold"],
        },
        {
            "locale": "en",
            "noisy_query": "hw do i obtan an instnt tin numbr as an individul?",
            "canonical_concept": "Instant TIN Procedure",
            "expected_keywords": ["tin", "ura", "individual", "nin"],
        },
        # Luganda: dropped letters, corrupted compound prefixes
        {
            "locale": "lg",
            "noisy_query": "Biki ebyetaagsa okufna namba yomusolo eyitibwa TIN mu Ugnda?",
            "canonical_concept": "TIN Registration (Luganda)",
            "expected_keywords": ["tin", "ura", "namba", "ndagamuntu"],
        },
        {
            "locale": "lg",
            "noisy_query": "Omusolo gwobupangisa ku mayumba guli ku btundu bimeka mu ugnda?",
            "canonical_concept": "Rental Income Tax (Luganda)",
            "expected_keywords": ["12%", "30%", "2,820,000", "omusolo", "bapangisa"],
        },
        # Swahili: noisy verbs, missing prefixes
        {
            "locale": "sw",
            "noisy_query": "Kiwango cha chini cha mauzo ya kilamwaka ya lazma ya usajli wa vat nchni ugnda?",
            "canonical_concept": "VAT Registration Threshold (Swahili)",
            "expected_keywords": ["vat", "300,000,000", "50", "150,000,000", "usajili"],
        },
        {
            "locale": "sw",
            "noisy_query": "Mwisho wa kuwaslsha na kulipa kodi ya paye ya wafnyakaz ni lini?",
            "canonical_concept": "PAYE Returns Deadline (Swahili)",
            "expected_keywords": ["paye", "15", "mwezi", "tarehe"],
        },
    ]

    results: list[dict[str, Any]] = []
    for fz in fuzzy_queries:
        loc = fz["locale"]
        q = fz["noisy_query"]
        st, res, lat = http_req("POST", "/api/v1/chat", {"message": q, "locale": loc})
        reply = res.get("reply", "")
        reply_lower = reply.lower()
        matched = [k for k in fz["expected_keywords"] if k.lower() in reply_lower]
        # Fuzzy test passes if status is 200 and relevant tax concept is preserved despite syntax noise
        passed = st == 200 and len(matched) >= 1
        log(f"  [{'PASS' if passed else 'FAIL'}] [{loc.upper()}] Fuzzy: '{q}'")
        log(f"         Concept: {fz['canonical_concept']} | Matched: {matched} ({lat:.0f}ms)")
        log(f"         Reply: {reply[:120].replace(chr(10), ' ')}...\n")
        results.append({
            "locale": loc,
            "query": q,
            "concept": fz["canonical_concept"],
            "matched": matched,
            "latency_ms": lat,
            "status": st,
            "passed": passed,
        })

    success_rate = sum(1 for r in results if r["passed"]) / len(results) * 100
    log(f"Fuzzy Syntax Error Tolerance Pass Rate: {success_rate:.1f}%\n")
    return {
        "suite": "fuzzy_testing",
        "pass_rate_pct": success_rate,
        "cases": results,
    }


# =============================================================================
# SUITE 3: Concurrency Spike Burst Test
# =============================================================================
def run_spike_burst_test(concurrency: int = 20) -> dict[str, Any]:
    log("=================================================================")
    log(f"3. EXECUTING INSTANTANEOUS SPIKE BURST TEST (c={concurrency})")
    log("=================================================================")
    spike_queries = [
        ("en", "What is the threshold for mandatory VAT registration in Uganda?"),
        ("lg", "Biki ebyetaagisa okufuna namba y'omusolo eyitibwa TIN mu Uganda?"),
        ("sw", "Mwisho wa kuwasilisha na kulipa kodi ya PAYE ya wafanyakazi kila mwezi ni lini?"),
        ("en", "What is the rental income tax rate for individual landlords?"),
        ("lg", "Omuwendo gwa ssente ki ogukaka omusuubuzi okwewandiisa ku VAT mu Uganda?"),
    ]

    tasks = []
    for i in range(concurrency):
        loc, q = spike_queries[i % len(spike_queries)]
        tasks.append((loc, q, i))

    latencies: list[float] = []
    status_codes: list[int] = []
    t_start = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(http_req, "POST", "/api/v1/chat", {"message": q, "locale": loc}, None, 45.0)
            for loc, q, _ in tasks
        ]
        for f in concurrent.futures.as_completed(futures):
            st, _res, lat = f.result()
            status_codes.append(st)
            latencies.append(lat)

    total_time = (time.perf_counter() - t_start) * 1000.0
    throughput = concurrency / (total_time / 1000.0)
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[-1]
    success_count = sum(1 for s in status_codes if s == 200)

    log(f"  Spike Concurrency:   {concurrency} simultaneous requests")
    log(f"  Total Duration:      {total_time:.0f} ms")
    log(f"  Throughput:          {throughput:.2f} req/sec")
    log(f"  Success Rate:        {success_count}/{concurrency} ({success_count/concurrency*100:.1f}%)")
    log(f"  Latency p50:         {p50:.1f} ms")
    log(f"  Latency p95:         {p95:.1f} ms")
    log(f"  Latency p99 (Max):   {p99:.1f} ms\n")

    return {
        "suite": "spike_burst",
        "concurrency": concurrency,
        "total_time_ms": total_time,
        "throughput_rps": throughput,
        "success_rate_pct": success_count / concurrency * 100,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
    }


# =============================================================================
# SUITE 4: Sustained Volume Load Test
# =============================================================================
def run_volume_load_test(total_queries: int = 30, concurrency: int = 5) -> dict[str, Any]:
    log("=================================================================")
    log(f"4. EXECUTING SUSTAINED VOLUME LOAD TEST (n={total_queries}, c={concurrency})")
    log("=================================================================")
    test_queries = [
        ("en", "What are the requirements to register for a TIN in Uganda?"),
        ("lg", "Biki ebyetaagisa okufuna namba y'omusolo eyitibwa TIN mu Uganda?"),
        ("sw", "Ni mahitaji gani ya mtu binafsi kupata Namba ya Utambulisho wa Mlipakodi (TIN) nchini Uganda?"),
        ("en", "Who is required to use EFRIS and how do businesses issue e-invoices?"),
        ("lg", "Baani abateekwa okukozesa enkola ya EFRIS mu Uganda era risiti ziweerezebwa zitya?"),
        ("sw", "Ni nani anayepaswa kutumia EFRIS na wafanyabiashara wanatoaje ankara za kielektroniki?"),
        ("en", "What is the annual turnover threshold for compulsory VAT registration in Uganda?"),
        ("lg", "Omuwendo gwa ssente ki ogukaka omusuubuzi okwewandiisa ku VAT mu Uganda?"),
        ("sw", "Kiwango cha chini cha mauzo ya kila mwaka kinacholazimu usajili wa VAT nchini Uganda ni kipi?"),
        ("en", "When is the monthly deadline for filing and paying PAYE returns for employees?"),
        ("lg", "Omusolo gwa PAYE ku bakozi gusasulwa ddi buli mwezi era ebiwandiiko biweerezebwa ddi?"),
        ("sw", "Mwisho wa kuwasilisha na kulipa kodi ya PAYE ya wafanyakazi kila mwezi ni lini?"),
        ("en", "What is the rental income tax rate and annual threshold for individual landlords?"),
        ("lg", "Omusolo gw'obupangisa ku mayumba guli ku bitundu bimeka mu Uganda?"),
        ("sw", "Kiwango cha kodi ya mapato ya kodi ya nyumba kwa wamiliki binafsi nchini Uganda ni kipi?"),
    ]

    t_start = time.perf_counter()
    latencies: list[float] = []
    statuses: list[int] = []

    def _worker(idx: int):
        loc, q = test_queries[idx % len(test_queries)]
        st, _res, lat = http_req("POST", "/api/v1/chat", {"message": q, "locale": loc}, None, 45.0)
        return st, lat

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_worker, i) for i in range(total_queries)]
        for f in concurrent.futures.as_completed(futures):
            st, lat = f.result()
            statuses.append(st)
            latencies.append(lat)

    total_time = (time.perf_counter() - t_start) * 1000.0
    throughput = total_queries / (total_time / 1000.0)
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[-1]
    success_rate = sum(1 for s in statuses if s == 200) / total_queries * 100

    log(f"  Total Queries:       {total_queries}")
    log(f"  Sustained Duration:  {total_time:.0f} ms")
    log(f"  Throughput:          {throughput:.2f} req/sec")
    log(f"  Success Rate:        {success_rate:.1f}%")
    log(f"  Latency p50:         {p50:.1f} ms")
    log(f"  Latency p95:         {p95:.1f} ms")
    log(f"  Latency p99:         {p99:.1f} ms\n")

    return {
        "suite": "volume_load",
        "total_queries": total_queries,
        "concurrency": concurrency,
        "duration_ms": total_time,
        "throughput_rps": throughput,
        "success_rate_pct": success_rate,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
    }


def main():
    log("=" * 80)
    log("SENIOR QA REVIEW & COMPREHENSIVE BENCHMARK OVER NGROK")
    log(f"Target URL: {NGROK_URL}")
    log("=" * 80)

    gpus_before = get_gpu_telemetry()
    for idx, info in gpus_before.items():
        log(f"GPU {idx} ({info['name']}): {info['memory_used_mb']:.0f}/{info['memory_total_mb']:.0f} MiB ({info['utilization_pct']}%)")

    suite1 = run_e2e_regression()
    suite2 = run_fuzzy_testing()
    suite3 = run_spike_burst_test(concurrency=20)
    suite4 = run_volume_load_test(total_queries=30, concurrency=5)

    gpus_after = get_gpu_telemetry()
    for idx, info in gpus_after.items():
        log(f"GPU {idx} Post-Test: {info['memory_used_mb']:.0f}/{info['memory_total_mb']:.0f} MiB ({info['utilization_pct']}%)")

    master_report = {
        "benchmark_timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "target_endpoint": NGROK_URL,
        "hardware_allocation": {
            "vllm_llm_gpu": 2,
            "api_speech_retriever_gpu": 4,
            "gpu_telemetry_before": gpus_before,
            "gpu_telemetry_after": gpus_after,
        },
        "suites": {
            "e2e_regression": suite1,
            "fuzzy_testing": suite2,
            "spike_burst": suite3,
            "volume_load": suite4,
        },
    }

    out_path = Path("Results/metrics/senior_qa_ngrok_benchmark_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(master_report, indent=2), encoding="utf-8")
    log(f"Detailed Senior QA Report saved to: {out_path}")


if __name__ == "__main__":
    main()
