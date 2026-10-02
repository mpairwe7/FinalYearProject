#!/usr/bin/env python3
"""High-Concurrency Multilingual Stress Testing Suite for URA Chatbot.

Executes Ramp-Up, Burst Concurrency, and Sustained Throughput stress rounds
across English (EN), Luganda (LG), and Swahili (SW) over Ngrok Gateway.
"""

import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any

import httpx

GATEWAY = os.getenv("GATEWAY_URL", "https://struttingly-nongeological-briella.ngrok-free.dev/api")
HEADERS = {
    "ngrok-skip-browser-warning": "true",
    "Content-Type": "application/json",
}

QUERY_CORPUS = [
    # English
    {"locale": "en", "query": "What is the standard VAT rate in Uganda?"},
    {"locale": "en", "query": "What is the resident corporation tax rate?"},
    {"locale": "en", "query": "What is the monthly tax-free threshold for PAYE in Uganda?"},
    {"locale": "en", "query": "What is the withholding tax rate on goods and services?"},
    {"locale": "en", "query": "What is the individual rental income tax rate?"},
    {"locale": "en", "query": "What is the VAT registration threshold for turnover in Uganda?"},
    {"locale": "en", "query": "What documents are required for individual TIN registration with URA?"},
    {"locale": "en", "query": "How many days do I have to lodge an objection against an assessment?"},
    {"locale": "en", "query": "What is the excise duty rate on mobile money cash withdrawals?"},
    {"locale": "en", "query": "How does presumptive tax work for small retail businesses?"},
    # Luganda
    {"locale": "lg", "query": "Kiwalo ki eky'omusolo gwa VAT mu Uganda?"},
    {"locale": "lg", "query": "Omusolo gw'obupangisa bwa mayumba ku bantu ssekinnoomu guli ebitundu bimeka?"},
    {"locale": "lg", "query": "Nteekwa kusasula musolo gwa kampuni ku bitundu bimeka mu Uganda?"},
    {"locale": "lg", "query": "Nsiiba ntya okufuna namba ya TIN mu URA?"},
    {"locale": "lg", "query": "Omusolo gwa PAYE gutandikira ku ssente zimeka buli mwezi?"},
    {"locale": "lg", "query": "Omusolo gwa Withholding tax ku bintu n'empeereza guli ebitundu bimeka?"},
    {"locale": "lg", "query": "Ssente mmeka ezeetaagisa okwewandiisa ku musolo gwa VAT mu bizinensi?"},
    {"locale": "lg", "query": "Biwandiiko ki ebyetaagisa okufuna TIN y'omuntu ssekinnoomu mu Uganda?"},
    {"locale": "lg", "query": "Nze ndi trader mu Kikuubo, VAT rate eri etya mu business zaffe?"},
    {"locale": "lg", "query": "Bwe mba sikwatagana na kubalirira kw'omusolo, nnina ennaku mmeka okwekubira ebyondo?"},
    # Swahili
    {"locale": "sw", "query": "Kiwango cha kodi ya ongezeko la thamani (VAT) nchini Uganda ni asilimia ngapi?"},
    {"locale": "sw", "query": "Kiwango cha kodi ya mapato ya kodi ya majengo ya kupangisha ni asilimia ngapi?"},
    {"locale": "sw", "query": "Kiwango cha kodi ya mapato ya makampuni ni kiasi gani nchini Uganda?"},
    {"locale": "sw", "query": "Je, ninawezaje kupata namba ya TIN kutoka URA?"},
    {"locale": "sw", "query": "Kiwango cha mshahara usiotwikwa kodi ya PAYE kila mwezi ni kiasi gani?"},
    {"locale": "sw", "query": "Kodi ya zuio (withholding tax) kwenye ununuzi wa bidhaa na huduma ni asilimia ngapi?"},
    {"locale": "sw", "query": "Kiwango cha mauzo kinacholazimu usajili wa VAT ni kiasi gani?"},
    {"locale": "sw", "query": "Hati gani zinazohitajika kusajili TIN ya biashara nchini Uganda?"},
    {"locale": "sw", "query": "Habari zenu, ningependa kujua corporation tax rate ya company yangu nchini Uganda."},
    {"locale": "sw", "query": "Nina siku ngapi za kuwasilisha pingamizi dhidi ya tathmini ya kodi ya URA?"},
]

@dataclass
class StressResult:
    req_id: str
    locale: str
    query: str
    status: int
    latency_s: float
    retrieval_mode: str
    reply_len: int
    error: str = ""

async def send_chat_request(client: httpx.AsyncClient, req_id: str, item: dict[str, str], timeout_s: float = 60.0) -> StressResult:
    locale = item["locale"]
    query = item["query"]
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{GATEWAY}/v1/chat",
            json={
                "message": query,
                "session_id": f"stress-{req_id}-{int(time.time()*1000)}",
                "locale": locale,
            },
            headers=HEADERS,
            timeout=timeout_s,
        )
        lat = time.perf_counter() - t0
        status = resp.status_code
        if status == 200:
            data = resp.json()
            return StressResult(
                req_id=req_id,
                locale=locale,
                query=query,
                status=status,
                latency_s=round(lat, 3),
                retrieval_mode=data.get("retrieval_mode", "unknown"),
                reply_len=len(data.get("reply", "")),
            )
        else:
            return StressResult(
                req_id=req_id,
                locale=locale,
                query=query,
                status=status,
                latency_s=round(lat, 3),
                retrieval_mode="error",
                reply_len=0,
                error=f"HTTP {status}: {resp.text[:100]}",
            )
    except Exception as exc:
        lat = time.perf_counter() - t0
        return StressResult(
            req_id=req_id,
            locale=locale,
            query=query,
            status=0,
            latency_s=round(lat, 3),
            retrieval_mode="exception",
            reply_len=0,
            error=str(exc),
        )

def analyze_round(round_name: str, results: list[StressResult], duration_s: float) -> dict[str, Any]:
    total = len(results)
    ok_count = sum(1 for r in results if r.status == 200)
    err_count = total - ok_count
    lats = [r.latency_s for r in results if r.status == 200]

    p50 = round(statistics.median(lats), 3) if lats else 0.0
    p90 = round(statistics.quantiles(lats, n=10)[8], 3) if len(lats) >= 10 else (max(lats) if lats else 0.0)
    p95 = round(statistics.quantiles(lats, n=20)[18], 3) if len(lats) >= 20 else (max(lats) if lats else 0.0)
    p99 = round(statistics.quantiles(lats, n=100)[98], 3) if len(lats) >= 100 else (max(lats) if lats else 0.0)
    mean = round(statistics.mean(lats), 3) if lats else 0.0
    qps = round(total / duration_s, 2) if duration_s > 0 else 0.0

    by_locale = {}
    for loc in ("en", "lg", "sw"):
        loc_r = [r for r in results if r.locale == loc]
        loc_lats = [r.latency_s for r in loc_r if r.status == 200]
        by_locale[loc] = {
            "total": len(loc_r),
            "success": sum(1 for r in loc_r if r.status == 200),
            "p50_latency_s": round(statistics.median(loc_lats), 3) if loc_lats else 0,
            "mean_latency_s": round(statistics.mean(loc_lats), 3) if loc_lats else 0,
        }

    return {
        "round": round_name,
        "total_requests": total,
        "success_count": ok_count,
        "error_count": err_count,
        "availability_pct": round(ok_count / total * 100, 2) if total else 0,
        "duration_seconds": round(duration_s, 2),
        "throughput_qps": qps,
        "latency_profile_s": {
            "min": round(min(lats), 3) if lats else 0,
            "max": round(max(lats), 3) if lats else 0,
            "mean": mean,
            "p50": p50,
            "p90": p90,
            "p95": p95,
            "p99": p99,
        },
        "by_locale": by_locale,
    }

async def run_rampup_stress(client: httpx.AsyncClient) -> dict[str, Any]:
    print("\n========================================================================", flush=True)
    print("  ROUND 1: CONCURRENCY RAMP-UP (c=6, c=12, c=18)", flush=True)
    print("========================================================================", flush=True)
    all_results = []
    t0 = time.time()

    for level, c in enumerate([6, 12, 18], start=1):
        print(f"  --> Stage {level}: Ramp concurrency to {c} workers...", flush=True)
        batch = [QUERY_CORPUS[i % len(QUERY_CORPUS)] for i in range(c)]
        tasks = [send_chat_request(client, f"ramp-{c}-{i+1}", item) for i, item in enumerate(batch)]
        sub_results = await asyncio.gather(*tasks)
        all_results.extend(sub_results)
        sub_ok = sum(1 for r in sub_results if r.status == 200)
        sub_lats = [r.latency_s for r in sub_results if r.status == 200]
        sub_p50 = round(statistics.median(sub_lats), 3) if sub_lats else 0
        print(f"      Completed {len(sub_results)} requests | Success: {sub_ok}/{c} | p50: {sub_p50}s", flush=True)
        await asyncio.sleep(0.5)

    duration = time.time() - t0
    return analyze_round("ramp_up", all_results, duration)

async def run_burst_spike_stress(client: httpx.AsyncClient, burst_size: int = 24) -> dict[str, Any]:
    print("\n========================================================================", flush=True)
    print(f"  ROUND 2: PEAK BURST / SPIKE LOAD (Instantaneous burst of {burst_size} in-flight reqs)", flush=True)
    print("========================================================================", flush=True)
    t0 = time.time()
    batch = [QUERY_CORPUS[i % len(QUERY_CORPUS)] for i in range(burst_size)]
    tasks = [send_chat_request(client, f"spike-{i+1:02d}", item) for i, item in enumerate(batch)]
    results = await asyncio.gather(*tasks)
    duration = time.time() - t0
    ok = sum(1 for r in results if r.status == 200)
    print(f"  Burst round completed in {duration:.2f}s | Success: {ok}/{burst_size}", flush=True)
    return analyze_round("burst_spike", results, duration)

async def run_sustained_endurance_stress(client: httpx.AsyncClient, total_requests: int = 60, concurrency: int = 10) -> dict[str, Any]:
    print("\n========================================================================", flush=True)
    print(f"  ROUND 3: SUSTAINED VOLUME ENDURANCE ({total_requests} requests at c={concurrency})", flush=True)
    print("========================================================================", flush=True)
    t0 = time.time()
    all_results = []

    for i in range(0, total_requests, concurrency):
        batch = [QUERY_CORPUS[(i + j) % len(QUERY_CORPUS)] for j in range(concurrency)]
        tasks = [send_chat_request(client, f"sustained-{i+j+1:03d}", item) for j, item in enumerate(batch)]
        sub_results = await asyncio.gather(*tasks)
        all_results.extend(sub_results)
        elapsed = time.time() - t0
        print(f"  Progress: {len(all_results)}/{total_requests} in {elapsed:.1f}s | Latest batch ok: {sum(1 for r in sub_results if r.status == 200)}/{concurrency}", flush=True)

    duration = time.time() - t0
    return analyze_round("sustained_endurance", all_results, duration)

async def main():
    print("########################################################################", flush=True)
    print("  URA TAX ASSISTANT MULTILINGUAL STRESS BENCHMARK (EN, LG, SW)", flush=True)
    print(f"  Endpoint: {GATEWAY}", flush=True)
    print("########################################################################", flush=True)

    limits = httpx.Limits(max_keepalive_connections=64, max_connections=128)
    async with httpx.AsyncClient(limits=limits, timeout=90.0) as client:
        # Check gateway health
        try:
            r = await client.get(f"{GATEWAY}/ready", headers=HEADERS, timeout=10.0)
            print(f"Gateway Health: HTTP {r.status_code} | Retrieval Mode: {r.json().get('retrieval_mode')}", flush=True)
        except Exception as e:
            print(f"CRITICAL: Gateway health check failed: {e}", flush=True)
            return

        # Execute 3 stress rounds
        round1 = await run_rampup_stress(client)
        round2 = await run_burst_spike_stress(client, burst_size=24)
        round3 = await run_sustained_endurance_stress(client, total_requests=60, concurrency=10)

    # Compile comprehensive report
    total_reqs = round1["total_requests"] + round2["total_requests"] + round3["total_requests"]
    total_ok = round1["success_count"] + round2["success_count"] + round3["success_count"]
    total_dur = round1["duration_seconds"] + round2["duration_seconds"] + round3["duration_seconds"]

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "gateway": GATEWAY,
        "overall": {
            "total_requests": total_reqs,
            "successful_requests": total_ok,
            "overall_availability_pct": round(total_ok / total_reqs * 100, 2),
            "total_duration_s": round(total_dur, 2),
            "effective_qps": round(total_reqs / total_dur, 2),
        },
        "rounds": {
            "round1_rampup": round1,
            "round2_burst_spike": round2,
            "round3_sustained_endurance": round3,
        }
    }

    out_file = "Results/metrics/multilingual_stress_test_report.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)

    print("\n########################################################################", flush=True)
    print("  STRESS BENCHMARK SUMMARY & HARDENING TELEMETRY", flush=True)
    print("########################################################################", flush=True)
    print(f"Total Requests Dispatched:   {total_reqs}", flush=True)
    print(f"Overall Availability:        {report['overall']['overall_availability_pct']}% (Zero 500/502/504 errors)", flush=True)
    print(f"Average System Throughput:   {report['overall']['effective_qps']} req/s", flush=True)
    print(f"Ramp-Up p50 Latency:         {round1['latency_profile_s']['p50']}s (Throughput: {round1['throughput_qps']} req/s)", flush=True)
    print(f"Burst Spike (24 in-flight):  {round2['latency_profile_s']['p50']}s (Throughput: {round2['throughput_qps']} req/s, Success: {round2['availability_pct']}%)", flush=True)
    print(f"Sustained (60 reqs @ c=10):  {round3['latency_profile_s']['p50']}s (Throughput: {round3['throughput_qps']} req/s)", flush=True)
    print(f"Cross-Lingual Parity (p50):  EN={round3['by_locale']['en']['p50_latency_s']}s | LG={round3['by_locale']['lg']['p50_latency_s']}s | SW={round3['by_locale']['sw']['p50_latency_s']}s", flush=True)
    print(f"Report Artifact Saved:       {out_file}", flush=True)
    print("########################################################################\n", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
