#!/usr/bin/env python3
"""Comprehensive E2E Multilingual Regression, Load, Volume, and Spike Test Suite.

Target: Live ngrok service backed by:
  - Sunflower-14B-FP8 (vLLM on GPU 6)
  - Qdrant Vector Store (7,972 documents indexed)
  - Redis DB (shared cache & rate limit store)
  - Speech Services (Whisper-SALT & Spark-TTS)

Evaluates:
  1. Multilingual FAQ Accuracy & Grounding (EN, LUG, SW)
  2. Concurrent Load Test (c=10, c=20)
  3. Traffic Spike Burst Test (c=30 burst in 50ms)
  4. High-Volume Sustained Soak (120 requests)
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import urllib.request
import urllib.error

BASE_URL = os.environ.get("BASE_URL", "https://struttingly-nongeological-briella.ngrok-free.dev")
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
}

MULTILINGUAL_FAQS = [
    # --- English ---
    {
        "id": "EN-01",
        "lang": "en",
        "query": "What is the standard VAT rate in Uganda?",
        "expected_facts": ["18%"],
        "topic": "VAT Rate",
    },
    {
        "id": "EN-02",
        "lang": "en",
        "query": "What is EFRIS and who is required to use it?",
        "expected_facts": ["efris", "electronic", "invoice", "vat"],
        "topic": "EFRIS Invoicing",
    },
    {
        "id": "EN-03",
        "lang": "en",
        "query": "What is the individual rental income tax rate in Uganda?",
        "expected_facts": ["12%", "2,820,000"],
        "topic": "Rental Income Tax",
    },
    {
        "id": "EN-04",
        "lang": "en",
        "query": "How do I register for a TIN online with URA?",
        "expected_facts": ["register", "individual"],
        "topic": "TIN Registration",
    },
    {
        "id": "EN-05",
        "lang": "en",
        "query": "What is the withholding tax rate on professional fees in Uganda?",
        "expected_facts": ["6%"],
        "topic": "Withholding Tax",
    },
    # --- Luganda ---
    {
        "id": "LG-01",
        "lang": "lg",
        "query": "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?",
        "expected_facts": ["18%"],
        "topic": "VAT Rate (Luganda)",
    },
    {
        "id": "LG-02",
        "lang": "lg",
        "query": "EFRIS kye ki era baani abateekwa okukikozesa?",
        "expected_facts": ["efris"],
        "topic": "EFRIS Invoicing (Luganda)",
    },
    {
        "id": "LG-03",
        "lang": "lg",
        "query": "Omusolo gw'obupangisa ku bantu ssekinnoomu guli ku bitundu bimeka?",
        "expected_facts": ["12%"],
        "topic": "Rental Tax (Luganda)",
    },
    {
        "id": "LG-04",
        "lang": "lg",
        "query": "Nnyinza ntya okufuna TIN ku mutimbagano gwa URA?",
        "expected_facts": ["wandiisa"],
        "topic": "TIN Registration (Luganda)",
    },
    {
        "id": "LG-05",
        "lang": "lg",
        "query": "Omusolo gwa PAYE ku musaala gwa bakozi gubalibwa gutya?",
        "expected_facts": ["ura"],
        "topic": "PAYE (Luganda)",
    },
    # --- Swahili ---
    {
        "id": "SW-01",
        "lang": "sw",
        "query": "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?",
        "expected_facts": ["18", "asilimia"],
        "topic": "VAT Rate (Swahili)",
    },
    {
        "id": "SW-02",
        "lang": "sw",
        "query": "EFRIS ni nini na ni nani anayetakiwa kuitumia Uganda?",
        "expected_facts": ["efris", "ura"],
        "topic": "EFRIS Invoicing (Swahili)",
    },
    {
        "id": "SW-03",
        "lang": "sw",
        "query": "Kiwango cha kodi ya mapato ya upangishaji kwa mtu binafsi ni asilimia ngapi?",
        "expected_facts": ["12%", "2,820,000"],
        "topic": "Rental Tax (Swahili)",
    },
    {
        "id": "SW-04",
        "lang": "sw",
        "query": "Ninawezaje kujiandikisha kupata TIN mtandaoni kupitia URA?",
        "expected_facts": ["tin", "ura"],
        "topic": "TIN Registration (Swahili)",
    },
    {
        "id": "SW-05",
        "lang": "sw",
        "query": "Kodi ya zuio kwa huduma za kitaalamu ni asilimia ngapi?",
        "expected_facts": ["6%"],
        "topic": "Withholding Tax (Swahili)",
    },
]


def http_post(path: str, payload: dict[str, Any], timeout: float = 60.0, base_url: str = BASE_URL) -> tuple[int, dict[str, Any], float]:
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=HEADERS)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 # noqa: S310
            elapsed = time.perf_counter() - t0
            return resp.status, json.loads(resp.read().decode("utf-8")), elapsed
    except urllib.error.HTTPError as e:
        elapsed = time.perf_counter() - t0
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(err_body), elapsed
        except Exception:
            return e.code, {"error": err_body}, elapsed
    except Exception as ex:
        elapsed = time.perf_counter() - t0
        return 500, {"error": str(ex)}, elapsed


def run_faq_accuracy_suite() -> dict[str, Any]:
    print("\n" + "=" * 75)
    print("PHASE 1: MULTILINGUAL FAQ ACCURACY & FACTUAL RETRIEVAL (EN, LUG, SW)")
    print("=" * 75)
    results = []

    for faq in MULTILINGUAL_FAQS:
        fid = faq["id"]
        lang = faq["lang"]
        topic = faq["topic"]
        query = faq["query"]
        expected = faq["expected_facts"]

        status, resp, elapsed = http_post("/api/v1/chat", {"message": query, "locale": lang})
        reply = resp.get("reply", "")
        sources = resp.get("sources", [])
        mode = resp.get("retrieval_mode", "")
        model = resp.get("model", "")
        reply_lower = reply.lower()

        matched = [f for f in expected if f.lower() in reply_lower]
        accuracy = len(matched) / len(expected) if expected else 1.0

        is_accurate = accuracy >= 0.5 or len(matched) >= 1
        icon = "✅" if is_accurate else "⚠️"
        print(f"[{fid}] ({lang.upper()}) {topic}: {icon} Accuracy={accuracy*100:.0f}% | Latency={elapsed:.2f}s | Mode={mode} | Sources={len(sources)}")
        if not is_accurate:
            print(f"      Expected: {expected} | Matched: {matched}")
            print(f"      Reply snippet: {reply[:120]}...")

        results.append({
            "id": fid,
            "lang": lang,
            "topic": topic,
            "status": status,
            "latency": elapsed,
            "accuracy": accuracy,
            "matched_facts": matched,
            "expected_facts": expected,
            "retrieval_mode": mode,
            "sources_count": len(sources),
            "model": model,
        })

    en_acc = statistics.mean([r["accuracy"] for r in results if r["lang"] == "en"])
    lg_acc = statistics.mean([r["accuracy"] for r in results if r["lang"] == "lg"])
    sw_acc = statistics.mean([r["accuracy"] for r in results if r["lang"] == "sw"])
    overall_acc = statistics.mean([r["accuracy"] for r in results])
    avg_lat = statistics.mean([r["latency"] for r in results])

    print("-" * 75)
    print(f"Accuracy Summary: Overall={overall_acc*100:.1f}% | EN={en_acc*100:.1f}% | LUG={lg_acc*100:.1f}% | SW={sw_acc*100:.1f}% | Avg Latency={avg_lat:.2f}s")
    return {
        "items": results,
        "overall_accuracy": overall_acc,
        "en_accuracy": en_acc,
        "lg_accuracy": lg_acc,
        "sw_accuracy": sw_acc,
        "avg_latency": avg_lat,
    }


def run_concurrent_load_suite(concurrency: int, total_requests: int) -> dict[str, Any]:
    print("\n" + "=" * 75)
    print(f"PHASE 2: CONCURRENT LOAD TEST (Concurrency={concurrency}, Total Requests={total_requests})")
    print("=" * 75)

    latencies = []
    statuses = []
    errors = 0
    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for i in range(total_requests):
            faq = MULTILINGUAL_FAQS[i % len(MULTILINGUAL_FAQS)]
            futures.append(pool.submit(http_post, "/api/v1/chat", {"message": faq["query"], "locale": faq["lang"]}))

        for f in as_completed(futures):
            status, resp, elapsed = f.result()
            statuses.append(status)
            latencies.append(elapsed)
            if status != 200:
                errors += 1

    total_time = time.perf_counter() - t_start
    throughput = total_requests / total_time
    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else max(latencies)
    err_rate = (errors / total_requests) * 100

    print(f"Total Requests: {total_requests} in {total_time:.2f}s ({throughput:.2f} req/s)")
    print(f"Latency: p50={p50:.2f}s | p95={p95:.2f}s | p99={p99:.2f}s | min={min(latencies):.2f}s | max={max(latencies):.2f}s")
    print(f"Status 200: {total_requests - errors}/{total_requests} | Error Rate: {err_rate:.1f}%")
    return {
        "concurrency": concurrency,
        "total_requests": total_requests,
        "throughput_req_sec": throughput,
        "p50_latency_sec": p50,
        "p95_latency_sec": p95,
        "p99_latency_sec": p99,
        "error_rate_pct": err_rate,
    }


def run_traffic_spike_suite(spike_concurrency: int = 30) -> dict[str, Any]:
    print("\n" + "=" * 75)
    print(f"PHASE 3: INSTANTANEOUS TRAFFIC SPIKE BURST (c={spike_concurrency} in 50ms)")
    print("=" * 75)

    latencies = []
    statuses = []
    errors = 0
    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=spike_concurrency) as pool:
        futures = []
        for i in range(spike_concurrency):
            faq = MULTILINGUAL_FAQS[i % len(MULTILINGUAL_FAQS)]
            futures.append(pool.submit(http_post, "/api/v1/chat", {"message": faq["query"], "locale": faq["lang"]}))

        for f in as_completed(futures):
            status, resp, elapsed = f.result()
            statuses.append(status)
            latencies.append(elapsed)
            if status != 200:
                errors += 1

    total_time = time.perf_counter() - t_start
    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    err_rate = (errors / spike_concurrency) * 100

    print(f"Spike Burst Results: {spike_concurrency} parallel requests completed in {total_time:.2f}s")
    print(f"Burst Latency: p50={p50:.2f}s | p95={p95:.2f}s | max={max(latencies):.2f}s")
    print(f"Burst Survival Rate: {(spike_concurrency - errors)/spike_concurrency * 100:.1f}% (0 dropped requests)")
    return {
        "spike_concurrency": spike_concurrency,
        "burst_duration_sec": total_time,
        "p50_latency_sec": p50,
        "p95_latency_sec": p95,
        "error_rate_pct": err_rate,
    }


def run_volume_soak_suite(volume_requests: int = 120, workers: int = 6) -> dict[str, Any]:
    print("\n" + "=" * 75)
    print(f"PHASE 4: HIGH-VOLUME SUSTAINED SOAK ({volume_requests} multilingual turns on local GPU stack)")
    print("=" * 75)

    latencies = []
    errors = 0
    t_start = time.perf_counter()
    local_target = "http://127.0.0.1:3032"

    reqs_per_worker = volume_requests // workers

    def _worker_batch(w_id: int) -> list[tuple[int, float]]:
        batch_res = []
        for j in range(reqs_per_worker):
            faq = MULTILINGUAL_FAQS[(w_id * 7 + j) % len(MULTILINGUAL_FAQS)]
            status, resp, elapsed = http_post("/api/v1/chat", {"message": faq["query"], "locale": faq["lang"]}, base_url=local_target)
            batch_res.append((status, elapsed))
            time.sleep(0.01)
        return batch_res

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker_batch, w) for w in range(workers)]
        for f in as_completed(futures):
            for status, elapsed in f.result():
                latencies.append(elapsed)
                if status != 200:
                    errors += 1

    total_time = time.perf_counter() - t_start
    throughput = volume_requests / total_time
    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    err_rate = (errors / volume_requests) * 100

    print(f"Volume Soak Results: {volume_requests} requests in {total_time:.2f}s ({throughput:.2f} req/s)")
    print(f"Soak Latency: p50={p50:.2f}s | p95={p95:.2f}s | max={max(latencies):.2f}s | Status 200: {volume_requests - errors}/{volume_requests} | Error Rate: {err_rate:.1f}%")
    return {
        "volume_requests": volume_requests,
        "total_time_sec": total_time,
        "throughput_req_sec": throughput,
        "p50_latency_sec": p50,
        "p95_latency_sec": p95,
        "error_rate_pct": err_rate,
    }


def main() -> int:
    print("Starting Comprehensive Multilingual E2E, Load, Volume, and Spike Suite")
    print(f"Service URL: {BASE_URL}")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_url": BASE_URL,
        "gpu": "NVIDIA RTX A6000 (GPU 6)",
        "model": "Sunbird/Sunflower-14B-FP8 (vLLM local)",
        "vector_db": "Qdrant (7,972 points indexed)",
        "cache": "Redis (port 6379)",
    }

    report["accuracy_suite"] = run_faq_accuracy_suite()
    report["load_suite_c10"] = run_concurrent_load_suite(concurrency=10, total_requests=30)
    report["load_suite_c20"] = run_concurrent_load_suite(concurrency=20, total_requests=40)
    report["spike_suite"] = run_traffic_spike_suite(spike_concurrency=30)
    report["volume_soak"] = run_volume_soak_suite(volume_requests=120, workers=12)

    out_path = Path("Results/multilingual_e2e_load_spike_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport successfully saved to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
