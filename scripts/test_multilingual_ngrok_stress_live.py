#!/usr/bin/env python3
"""Live Multilingual Stress & Accuracy Benchmark over Public Ngrok URL.

Target:
  - Ngrok Endpoint: https://struttingly-nongeological-briella.ngrok-free.dev
  - Stack: GPU 2 (app-api:gpu), GPU 4 (orpheus-tts), GPU 5 (sunflower-14b vLLM)
  - Languages: English (en), Luganda (lg), Swahili (sw)

Stress Stages:
  1. Multilingual Concurrent Load (Concurrent Voice & Query Bursts across EN, LG, SW)
  2. Statutory Accuracy & Figure Fidelity (18% VAT, 2% penalty, TIN portal procedures)
  3. Real-Time Hardware Telemetry (NVIDIA RTX A6000 allocations across GPUs 2, 4, 5)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "App" / "backend"))

NGROK_URL = os.getenv(
    "NGROK_URL", "https://struttingly-nongeological-briella.ngrok-free.dev"
)
AUDIO_DIR = ROOT / "evals" / "call_replay" / "audio"


def log(msg: str) -> None:
    now = time.strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def pcm_of(key: str) -> bytes:
    import wave

    path = AUDIO_DIR / f"{key}.wav"
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


def get_gpu_telemetry() -> dict[int, dict[str, Any]]:
    """Capture VRAM and compute utilization across GPUs 2, 4, and 5."""
    telemetry = {}
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, text=True).strip().splitlines()
        for line in out:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 7:
                idx = int(parts[0])
                if idx in (2, 4, 5):
                    telemetry[idx] = {
                        "name": parts[1],
                        "memory_used_mb": float(parts[2]),
                        "memory_total_mb": float(parts[3]),
                        "utilization_pct": float(parts[4]),
                        "temperature_c": float(parts[5]),
                        "power_draw_w": float(parts[6]),
                    }
    except Exception as ex:
        log(f"Telemetry query warning: {ex}")
    return telemetry


def _checked_url(path: str, params: str = "") -> str:
    """NGROK_URL + *path*, refusing anything but http(s) (urllib would open file://)."""
    parsed = urlsplit(NGROK_URL)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("NGROK_URL must be an http(s) URL without embedded credentials")
    return f"{NGROK_URL.rstrip('/')}{path}" + (f"?{params}" if params else "")


def _post(url: str, timeout: float, **kwargs: Any) -> tuple[int, dict[str, Any], float]:
    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "URA-Stress-Bench/2026", **kwargs.pop("headers", {})}
    t0 = time.perf_counter()
    try:
        resp = httpx.post(url, headers=headers, timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        return 599, {"error": str(exc)}, (time.perf_counter() - t0) * 1000.0
    elapsed = (time.perf_counter() - t0) * 1000.0
    try:
        body = resp.json()
    except ValueError:
        body = {"error": resp.text[:200]}
    return resp.status_code, body, elapsed


def http_post_json(path: str, payload: dict[str, Any], timeout: float = 45.0) -> tuple[int, dict[str, Any], float]:
    return _post(_checked_url(path), timeout, json=payload)


def http_post_pcm(path: str, pcm: bytes, params: str = "", timeout: float = 45.0) -> tuple[int, dict[str, Any], float]:
    return _post(
        _checked_url(path, params),
        timeout,
        content=pcm,
        headers={"Content-Type": "application/octet-stream", "X-Voice-Consent": "true"},
    )


# -----------------------------------------------------------------------------
# Stress Test Scenarios & Queries
# -----------------------------------------------------------------------------
MULTILINGUAL_TAX_PROBES = [
    # English (en)
    ("en", "What is the standard VAT rate in Uganda?", "18%", ["18", "vat"]),
    ("en", "What is the late tax payment penalty per month?", "2%", ["2", "penalty", "month"]),
    ("en", "How do I register for an individual TIN?", "TIN", ["tin", "portal|ura.go.ug", "national id|nin"]),
    # Luganda (lg)
    ("lg", "VAT yange ntya okugisasula, era ebitundu bimeka?", "18%", ["18", "omusolo", "ebitundu", "kikumi"]),
    ("lg", "Kikondeere kki ku kukeerewa okusasula omusolo?", "2%", ["2", "omusolo", "mwezi"]),
    ("lg", "Nnyinza ntya okwewandiisa okufuna TIN yange okuva mu URA?", "TIN", ["tin", "ura", "omutimbagano", "portal"]),
    # Swahili (sw)
    ("sw", "Kiwango cha kawaida cha VAT nchini Uganda ni kiasi gani?", "18%", ["18", "vat", "asilimia"]),
    ("sw", "Adhabu ya kisheria ya kuchelewa kulipa kodi ni ipi?", "2%", ["2", "adhabu", "riba", "mwezi"]),
    ("sw", "Ninawezaje kujisajili kupata namba ya TIN kutoka URA?", "TIN", ["tin", "kujisajili", "ura", "kitambulisho"]),
]

VOICE_AUDIO_PROBES = [
    ("en", "en_vat", ["18", "vat"]),
    ("lg", "lg_vat", ["18", "omusolo", "ebitundu", "kikumi"]),
    ("sw", "sw_tin", ["tin", "kujisajili", "ura"]),
]


def figure_in(expected: str, reply: str) -> bool:
    """Whether *reply* states *expected* ("18%", "TIN"); a number must stand alone.

    "2%" is not found in "12%", "2026", "0.2" or "2,000", which a substring
    check would all count as the 2% late-payment rate.
    """
    figure = expected.lower().replace("%", "")
    if not figure.isdigit():
        return figure in reply
    return re.search(rf"(?<![\d.]){re.escape(figure)}(?![.,]?\d)", reply) is not None


def token_coverage(tokens: list[str], reply: str) -> tuple[int, float]:
    """(hits, share) of *tokens* found in *reply*, each at the start of a word.

    A token may list synonyms, "national id|nin": the TIN guide says "Enter
    your NIN", and a correct answer must not fail on wording.
    """
    hits = sum(
        1
        for token in tokens
        if any(re.search(rf"(?<![a-z0-9]){re.escape(alt)}", reply) for alt in token.lower().split("|"))
    )
    return hits, hits / len(tokens)


def run_benchmark() -> dict[str, Any]:
    log("=" * 70)
    log(f"Starting Multilingual Stress Benchmark over Live Ngrok: {NGROK_URL}")
    log("=" * 70)

    initial_telemetry = get_gpu_telemetry()
    log(f"Initial GPU 2 Memory: {initial_telemetry.get(2, {}).get('memory_used_mb', 0):.0f} MiB")
    log(f"Initial GPU 4 Memory: {initial_telemetry.get(4, {}).get('memory_used_mb', 0):.0f} MiB")
    log(f"Initial GPU 5 Memory: {initial_telemetry.get(5, {}).get('memory_used_mb', 0):.0f} MiB")

    # -------------------------------------------------------------------------
    # STAGE 1: Statutory Accuracy & Figure Fidelity (EN, LG, SW)
    # -------------------------------------------------------------------------
    log("\n[STAGE 1] Testing Statutory Accuracy & Figure Fidelity over Ngrok...")
    accuracy_results = []
    for lang, query, expected_fig, expected_tokens in MULTILINGUAL_TAX_PROBES:
        st, data, lat = http_post_json("/api/v1/chat", {"message": query, "locale": lang}, timeout=45.0)
        reply = (data.get("reply") or "").lower()
        has_fig = figure_in(expected_fig, reply)
        token_hits, coverage = token_coverage(expected_tokens, reply)
        passed = (st == 200) and has_fig and coverage >= 0.5

        log(f" -> [{lang.upper()}] Status={st} Latency={lat:.1f}ms FigMatch={has_fig} Tokens={token_hits}/{len(expected_tokens)}")
        accuracy_results.append({
            "language": lang,
            "query": query,
            "status": st,
            "latency_ms": lat,
            "expected_figure": expected_fig,
            "figure_matched": has_fig,
            "token_coverage": coverage,
            "passed": passed,
        })

    # -------------------------------------------------------------------------
    # STAGE 2: Concurrent Multilingual Load Stress (c=12 parallel queries)
    # -------------------------------------------------------------------------
    log("\n[STAGE 2] Executing Concurrent Multilingual Stress Burst (c=12)...")
    concurrent_tasks = []
    for i in range(24):
        item = MULTILINGUAL_TAX_PROBES[i % len(MULTILINGUAL_TAX_PROBES)]
        concurrent_tasks.append((item[0], item[1], i))

    t_burst_start = time.perf_counter()
    burst_latencies = []
    burst_status_codes = []
    by_lang_latencies = {"en": [], "lg": [], "sw": []}

    def _worker(task: tuple[str, str, int]):
        lang, q, _ = task
        status, data, lat = http_post_json("/api/v1/chat", {"message": q, "locale": lang}, timeout=60.0)
        return lang, status, lat

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_worker, t) for t in concurrent_tasks]
        for f in concurrent.futures.as_completed(futures):
            lang, status, lat = f.result()
            burst_status_codes.append(status)
            burst_latencies.append(lat)
            by_lang_latencies[lang].append(lat)

    burst_duration_s = time.perf_counter() - t_burst_start
    burst_rps = len(concurrent_tasks) / burst_duration_s
    success_rate = (sum(1 for s in burst_status_codes if s == 200) / len(burst_status_codes)) * 100.0

    log(f" -> Burst Completed: {len(concurrent_tasks)} requests in {burst_duration_s:.2f}s ({burst_rps:.2f} req/s)")
    log(f" -> Success Rate: {success_rate:.1f}% (p50={np.percentile(burst_latencies, 50):.1f}ms, p95={np.percentile(burst_latencies, 95):.1f}ms)")

    # -------------------------------------------------------------------------
    # STAGE 3: Multilingual Voice Audio Pipeline Stress (EN, LG, SW)
    # -------------------------------------------------------------------------
    log("\n[STAGE 3] Testing Multilingual Voice Endpoints (/api/v1/voice/chat) over Ngrok...")
    voice_results = []
    for lang, audio_key, keywords in VOICE_AUDIO_PROBES:
        pcm = pcm_of(audio_key)
        params = f"language={lang}&sample_rate=16000&tts_enabled=true"
        st, data, lat = http_post_pcm("/api/v1/voice/chat", pcm, params=params, timeout=45.0)
        transcript = data.get("transcript", "")
        reply = data.get("reply", "")
        has_audio = bool(data.get("reply_audio_base64"))
        audio_len = len(data.get("reply_audio_base64") or "")
        # Any non-empty reply used to pass ("or bool(reply)"), so a wrong answer
        # with audio counted as a working voice pipeline.
        keyword_hits, keyword_share = token_coverage(keywords, reply.lower())
        passed = (st == 200) and has_audio and keyword_share >= 0.5

        log(f" -> [{lang.upper()} Audio] Status={st} Latency={lat:.1f}ms AudioBytes={audio_len} KeywordHits={keyword_hits}/{len(keywords)}")
        voice_results.append({
            "language": lang,
            "audio_key": audio_key,
            "status": st,
            "latency_ms": lat,
            "transcript": transcript,
            "reply_snippet": reply[:60],
            "audio_bytes": audio_len,
            "has_audio": has_audio,
            "keyword_coverage": keyword_share,
            "passed": passed,
        })

    # -------------------------------------------------------------------------
    # STAGE 4: Hardware Telemetry Capture
    # -------------------------------------------------------------------------
    final_telemetry = get_gpu_telemetry()
    log("\n[STAGE 4] Final Hardware Telemetry:")
    for idx, g in final_telemetry.items():
        log(f" -> GPU {idx} ({g['name']}): VRAM={g['memory_used_mb']:.0f}/{g['memory_total_mb']:.0f} MiB, Util={g['utilization_pct']:.0f}%, Temp={g['temperature_c']}C, Power={g['power_draw_w']:.1f}W")

    # -------------------------------------------------------------------------
    # Report Assembly
    # -------------------------------------------------------------------------
    accuracy_pass_rate = (sum(1 for r in accuracy_results if r["passed"]) / len(accuracy_results)) * 100.0
    voice_pass_rate = (sum(1 for r in voice_results if r["passed"]) / len(voice_results)) * 100.0
    all_passed = (accuracy_pass_rate >= 90.0) and (success_rate >= 95.0) and (voice_pass_rate == 100.0)

    report = {
        "timestamp": time.time(),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target_url": NGROK_URL,
        "all_passed": all_passed,
        "telemetry_initial": initial_telemetry,
        "telemetry_final": final_telemetry,
        "accuracy_benchmarks": {
            "total_tested": len(accuracy_results),
            "pass_rate_pct": accuracy_pass_rate,
            "results": accuracy_results,
        },
        "concurrency_stress": {
            "total_requests": len(concurrent_tasks),
            "concurrency": 12,
            "duration_s": round(burst_duration_s, 2),
            "throughput_rps": round(burst_rps, 2),
            "success_rate_pct": round(success_rate, 1),
            "latency_ms_p50": round(float(np.percentile(burst_latencies, 50)), 1),
            "latency_ms_p95": round(float(np.percentile(burst_latencies, 95)), 1),
            "by_language_latency_p50": {
                lang: round(float(np.percentile(lats, 50)), 1) if lats else 0.0
                for lang, lats in by_lang_latencies.items()
            },
        },
        "voice_pipeline_benchmarks": {
            "total_tested": len(voice_results),
            "pass_rate_pct": voice_pass_rate,
            "results": voice_results,
        },
    }

    out_file = ROOT / "evals" / "reports" / "multilingual_ngrok_stress_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)

    log("=" * 70)
    log(f"Benchmark Complete! Report written to: {out_file}")
    log(f"Accuracy Pass Rate: {accuracy_pass_rate:.1f}%")
    log(f"Concurrency Success Rate: {success_rate:.1f}% (Throughput: {burst_rps:.2f} req/s)")
    log(f"Voice Pipeline Pass Rate: {voice_pass_rate:.1f}%")
    log(f"OVERALL STATUS: {'PASS' if all_passed else 'FAIL'}")
    log("=" * 70)
    return report


if __name__ == "__main__":
    rep = run_benchmark()
    sys.exit(0 if rep.get("all_passed") else 1)
