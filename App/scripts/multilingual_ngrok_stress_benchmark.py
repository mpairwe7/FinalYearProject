#!/usr/bin/env python3
"""High-Concurrency Multilingual Stress Benchmark over Live Ngrok URL.

Target:
  - Tunnel: https://struttingly-nongeological-briella.ngrok-free.dev
  - Languages: English (en), Luganda (lg), Swahili (sw)
  - Hardware: GPU 2 (Dedicated Sunflower-14B vLLM) + GPU 4 (Dedicated Speech & Retriever)
  - Stack: Sunflower-14B, Whisper-Large-SALT, Spark-TTS-SALT, Redis, Qdrant, Next.js 16

Stress Stages:
  1. Multilingual Concurrent Interleaved Load (c=15, 45 requests evenly split across en, lg, sw)
  2. Heavy Concurrency Spike Burst (c=30 parallel requests across all 3 languages)
  3. Dynamic Multilingual Session Switching Stress (EN -> LG -> SW per session)
  4. Concurrent Multilingual Speech Synthesis (EN, LG Spark-TTS, SW TTS)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
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
    """Capture VRAM and utilization telemetry for GPU 2 and GPU 4."""
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


def http_post(
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, Any, float]:
    """Execute POST request against ngrok tunnel."""
    url = f"{NGROK_URL}{path}"
    req_headers = {
        "User-Agent": "URA-Multilingual-Stress-Bench/2026",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
    }
    if headers:
        req_headers.update(headers)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")  # nosec B310 # noqa: S310
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


# Test Query Matrix
STRESS_QUERIES = {
    "en": [
        "What is the threshold for mandatory VAT registration in Uganda?",
        "How do I apply for an instant TIN as an individual?",
        "When is the monthly deadline for filing and paying PAYE returns?",
        "What is the rental income tax rate for individual landlords?",
        "What is the withholding tax rate on goods and consultancy services?",
    ],
    "lg": [
        "Omuwendo gwa ssente ki ogukaka omusuubuzi okwewandiisa ku VAT mu Uganda?",
        "Biki ebyetaagisa okufuna namba y'omusolo eyitibwa TIN mu Uganda?",
        "Omusolo gwa PAYE ku bakozi gusasulwa ddi buli mwezi era ebiwandiiko biweerezebwa ddi?",
        "Omusolo gw'obupangisa ku mayumba guli ku bitundu bimeka mu Uganda?",
        "Bikolwa ki URA by’ewa?",
    ],
    "sw": [
        "Kiwango cha chini cha mauzo ya kila mwaka kinacholazimu usajili wa VAT nchini Uganda ni kipi?",
        "Ni mahitaji gani ya mtu binafsi kupata Namba ya Utambulisho wa Mlipakodi (TIN) nchini Uganda?",
        "Mwisho wa kuwasilisha na kulipa kodi ya PAYE ya wafanyakazi kila mwezi ni lini?",
        "Kiwango cha kodi ya mapato ya kodi ya nyumba kwa wamiliki binafsi nchini Uganda ni kipi?",
        "Kiwango cha kawaida cha kodi ya zuio (WHT) kwa huduma za ushauri na bidhaa ni asilimia ngapi?",
    ],
}


# =============================================================================
# STAGE 1: Interleaved Multilingual Concurrency (c=15, 45 requests)
# =============================================================================
def run_interleaved_stress(total_requests: int = 45, concurrency: int = 15) -> dict[str, Any]:
    log("=================================================================")
    log(f"STAGE 1: INTERLEAVED MULTILINGUAL CONCURRENCY STRESS (n={total_requests}, c={concurrency})")
    log("=================================================================")

    tasks = []
    langs = ["en", "lg", "sw"]
    for i in range(total_requests):
        lang = langs[i % len(langs)]
        q = STRESS_QUERIES[lang][(i // len(langs)) % len(STRESS_QUERIES[lang])]
        tasks.append((lang, q, i))

    t_start = time.perf_counter()
    latencies: list[float] = []
    status_codes: list[int] = []
    by_language: dict[str, list[float]] = {"en": [], "lg": [], "sw": []}

    def _worker(item: tuple[str, str, int]):
        lang, q, _ = item
        st, _data, lat = http_post("/api/v1/chat", {"message": q, "locale": lang}, timeout=60.0)
        return lang, st, lat

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_worker, t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            lang, st, lat = f.result()
            status_codes.append(st)
            latencies.append(lat)
            by_language[lang].append(lat)

    total_time = (time.perf_counter() - t_start) * 1000.0
    throughput = total_requests / (total_time / 1000.0)
    latencies.sort()
    success_count = sum(1 for s in status_codes if s == 200)

    p50 = latencies[len(latencies) // 2]
    p90 = latencies[int(len(latencies) * 0.90)]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[-1]

    log(f"  Total Requests:      {total_requests} (15 EN, 15 LG, 15 SW interleaved)")
    log(f"  Concurrency (Pool):  {concurrency} parallel threads")
    log(f"  Duration:            {total_time:.0f} ms")
    log(f"  Throughput:          {throughput:.2f} req/sec")
    log(f"  Success Rate:        {success_count}/{total_requests} ({success_count/total_requests*100:.1f}%)")
    log(f"  Latency p50:         {p50:.1f} ms")
    log(f"  Latency p90:         {p90:.1f} ms")
    log(f"  Latency p95:         {p95:.1f} ms")
    log(f"  Latency Max (p99):   {p99:.1f} ms")
    for l in langs:
        l_lats = sorted(by_language[l])
        l_p50 = l_lats[len(l_lats) // 2] if l_lats else 0
        log(f"    - {l.upper()} Median Latency (p50): {l_p50:.1f} ms")

    return {
        "stage": "interleaved_multilingual_stress",
        "total_requests": total_requests,
        "concurrency": concurrency,
        "duration_ms": total_time,
        "throughput_rps": throughput,
        "success_rate_pct": success_count / total_requests * 100,
        "p50_ms": p50,
        "p90_ms": p90,
        "p95_ms": p95,
        "p99_ms": p99,
        "by_language_p50_ms": {l: sorted(by_language[l])[len(by_language[l]) // 2] for l in langs if by_language[l]},
    }


# =============================================================================
# STAGE 2: Heavy Concurrency Spike Burst (c=30 parallel requests)
# =============================================================================
def run_heavy_burst_stress(burst_size: int = 30) -> dict[str, Any]:
    log("\n=================================================================")
    log(f"STAGE 2: HEAVY CONCURRENCY SPIKE BURST (c={burst_size})")
    log("=================================================================")

    tasks = []
    langs = ["en", "lg", "sw"]
    for i in range(burst_size):
        lang = langs[i % len(langs)]
        q = STRESS_QUERIES[lang][i % len(STRESS_QUERIES[lang])]
        tasks.append((lang, q, i))

    t_start = time.perf_counter()
    latencies: list[float] = []
    statuses: list[int] = []

    def _worker(item: tuple[str, str, int]):
        lang, q, _ = item
        st, _data, lat = http_post("/api/v1/chat", {"message": q, "locale": lang}, timeout=60.0)
        return st, lat

    with concurrent.futures.ThreadPoolExecutor(max_workers=burst_size) as pool:
        futures = [pool.submit(_worker, t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            st, lat = f.result()
            statuses.append(st)
            latencies.append(lat)

    total_time = (time.perf_counter() - t_start) * 1000.0
    throughput = burst_size / (total_time / 1000.0)
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[-1]
    success_count = sum(1 for s in statuses if s == 200)

    log(f"  Burst Concurrency:   {burst_size} simultaneous requests")
    log(f"  Total Duration:      {total_time:.0f} ms")
    log(f"  Throughput:          {throughput:.2f} req/sec")
    log(f"  Success Rate:        {success_count}/{burst_size} ({success_count/burst_size*100:.1f}%)")
    log(f"  Latency p50:         {p50:.1f} ms")
    log(f"  Latency p95:         {p95:.1f} ms")
    log(f"  Latency Max (p99):   {p99:.1f} ms")

    return {
        "stage": "heavy_burst_stress",
        "burst_size": burst_size,
        "duration_ms": total_time,
        "throughput_rps": throughput,
        "success_rate_pct": success_count / burst_size * 100,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
    }


# =============================================================================
# STAGE 3: Language Switching Conversational Stress (EN -> LG -> SW)
# =============================================================================
def run_language_switching_stress(sessions: int = 4) -> dict[str, Any]:
    log("\n=================================================================")
    log(f"STAGE 3: DYNAMIC LANGUAGE SWITCHING STRESS ({sessions} concurrent sessions)")
    log("=================================================================")

    def _run_session(sess_idx: int):
        session_id = f"stress-switch-{sess_idx}-{int(time.time()*1000)}"
        turn_results = []

        # Turn 1: English (Establish EFRIS context)
        q1 = "What is EFRIS and who must use it in Uganda?"
        st1, d1, lat1 = http_post(
            "/api/v1/chat",
            {"message": q1, "conversation_id": session_id, "locale": "en"},
            {"X-Session-ID": session_id},
            timeout=45.0,
        )
        r1 = d1.get("reply", "") if isinstance(d1, dict) else ""
        turn_results.append({
            "turn": 1,
            "lang": "en",
            "status": st1,
            "latency": lat1,
            "passed": st1 == 200 and any(w in r1.lower() for w in ("efris", "vat", "invoice")),
        })

        # Turn 2: Switch to Luganda (Ask about mobile use in Luganda)
        q2 = "Nnyinza okugikozesa ku ssimu eya bulijjo oba ku tablet?"
        st2, d2, lat2 = http_post(
            "/api/v1/chat",
            {"message": q2, "conversation_id": session_id, "locale": "lg"},
            {"X-Session-ID": session_id},
            timeout=45.0,
        )
        r2 = d2.get("reply", "") if isinstance(d2, dict) else ""
        turn_results.append({
            "turn": 2,
            "lang": "lg",
            "status": st2,
            "latency": lat2,
            "passed": st2 == 200 and any(w in r2.lower() for w in ("efris", "ssimu", "app", "omutimbagano", "ye")),
        })

        # Turn 3: Switch to Swahili (Ask about penalties in Swahili)
        q3 = "Ni adhabu gani zilizopo kwa kutotoa risiti kupitia mfumo huo?"
        st3, d3, lat3 = http_post(
            "/api/v1/chat",
            {"message": q3, "conversation_id": session_id, "locale": "sw"},
            {"X-Session-ID": session_id},
            timeout=45.0,
        )
        r3 = d3.get("reply", "") if isinstance(d3, dict) else ""
        turn_results.append({
            "turn": 3,
            "lang": "sw",
            "status": st3,
            "latency": lat3,
            "passed": st3 == 200 and any(w in r3.lower() for w in ("adhabu", "efris", "faini", "kodi", "risiti")),
        })

        return turn_results

    t_start = time.perf_counter()
    all_turns = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=sessions) as pool:
        futures = [pool.submit(_run_session, i) for i in range(sessions)]
        for f in concurrent.futures.as_completed(futures):
            all_turns.extend(f.result())

    total_time = (time.perf_counter() - t_start) * 1000.0
    total_turns = len(all_turns)
    passed_turns = sum(1 for t in all_turns if t["passed"])
    pass_rate = passed_turns / total_turns * 100

    log(f"  Sessions Tested:     {sessions} (3 turns each: EN -> LG -> SW)")
    log(f"  Total Turns:         {total_turns}")
    log(f"  Pass Rate:           {passed_turns}/{total_turns} ({pass_rate:.1f}%)")
    log(f"  Total Duration:      {total_time:.0f} ms")

    return {
        "stage": "language_switching_stress",
        "sessions": sessions,
        "total_turns": total_turns,
        "passed_turns": passed_turns,
        "pass_rate_pct": pass_rate,
        "duration_ms": total_time,
    }


# =============================================================================
# STAGE 4: Concurrent Multilingual Speech Synthesis
# =============================================================================
def run_concurrent_speech_stress(concurrency: int = 6) -> dict[str, Any]:
    log("\n=================================================================")
    log(f"STAGE 4: CONCURRENT MULTILINGUAL SPEECH SYNTHESIS (c={concurrency})")
    log("=================================================================")
    speech_tasks = [
        ("en", "Uganda Revenue Authority welcomes all taxpayers to file their returns online."),
        ("lg", "Tukusanyukidde mu kitongole ekisolooza emisolo ekya Uganda Revenue Authority."),
        ("sw", "Mamlaka ya Mapato ya Uganda inawakaribisha walipakodi wote."),
        ("en", "Ensure your Tax Clearance Certificate is updated before annual renewals."),
        ("lg", "Kakasa nti olina Satifikeeti y'Obuyonjo mu Musolo nga tennaggwako."),
        ("sw", "Hakikisha unawasilisha marejesho yako ya kodi ya PAYE kila mwezi."),
    ]

    t_start = time.perf_counter()
    latencies: list[float] = []
    statuses: list[int] = []

    def _worker(item: tuple[str, str]):
        loc, text = item
        st, data, lat = http_post("/api/v1/tts", {"text": text, "locale": loc}, timeout=30.0)
        return loc, st, len(data) if isinstance(data, (bytes, str)) else 0, lat

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_worker, t) for t in speech_tasks]
        for f in concurrent.futures.as_completed(futures):
            loc, st, bytes_len, lat = f.result()
            statuses.append(st)
            latencies.append(lat)
            log(f"  TTS [{loc.upper()}] -> HTTP {st} in {lat:.0f}ms ({bytes_len} bytes audio)")

    total_time = (time.perf_counter() - t_start) * 1000.0
    success_count = sum(1 for s in statuses if s == 200)

    log(f"  Speech Success Rate: {success_count}/{concurrency} ({success_count/concurrency*100:.1f}%)")
    log(f"  Total Duration:      {total_time:.0f} ms")

    return {
        "stage": "concurrent_speech_stress",
        "concurrency": concurrency,
        "success_rate_pct": success_count / concurrency * 100,
        "duration_ms": total_time,
        "latencies_ms": latencies,
    }


def main():
    log("=" * 80)
    log("STARTING MULTILINGUAL STRESS BENCHMARK (EN, LG, SW)")
    log(f"Target: {NGROK_URL}")
    log("=" * 80)

    gpu_init = get_gpu_telemetry()
    for idx, info in gpu_init.items():
        log(f"Init GPU {idx} ({info['name']}): {info['memory_used_mb']:.0f}/{info['memory_total_mb']:.0f} MiB ({info['utilization_pct']}%)")

    stage1 = run_interleaved_stress(total_requests=45, concurrency=15)
    stage2 = run_heavy_burst_stress(burst_size=30)
    stage3 = run_language_switching_stress(sessions=4)
    stage4 = run_concurrent_speech_stress(concurrency=6)

    gpu_post = get_gpu_telemetry()
    for idx, info in gpu_post.items():
        log(f"Post GPU {idx} ({info['name']}): {info['memory_used_mb']:.0f}/{info['memory_total_mb']:.0f} MiB ({info['utilization_pct']}%)")

    report = {
        "benchmark_timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "target_endpoint": NGROK_URL,
        "gpu_telemetry_init": gpu_init,
        "gpu_telemetry_post": gpu_post,
        "stages": {
            "interleaved_stress": stage1,
            "heavy_burst_stress": stage2,
            "language_switching_stress": stage3,
            "concurrent_speech_stress": stage4,
        },
    }

    out_file = Path("Results/metrics/multilingual_stress_test_report.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"\nReport successfully generated: {out_file}")


if __name__ == "__main__":
    main()
