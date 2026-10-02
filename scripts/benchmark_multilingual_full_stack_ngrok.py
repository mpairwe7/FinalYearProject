#!/usr/bin/env python3
"""Comprehensive Multilingual (EN, LG, SW) Full-Stack Benchmark Suite for URA Assistant.

Evaluates:
  1. Modalities:
     - TTT (Text-to-Text): Multilingual chat RAG & statutory reasoning in English, Luganda, Swahili
     - TTS (Text-to-Speech): Speech synthesis via Spark-TTS-SALT (lg, sw) and edge_tts (en)
     - STT (Speech-to-Text): ASR audio transcription via Whisper-SALT (en, lg, sw)
  2. Stress Profiles:
     - Load Scaling: Concurrency across modalities and languages
     - Volume Soak: Sustained queries across Domestic, Customs, and Tax Education
     - Traffic Spike: Instantaneous burst (c=12 in 50ms) to measure queue resilience & recovery
     - Fuzzy & Robustness: Typos, dropped vowels, Ugandan colloquialisms, and code-switching
  3. Metric Governance:
     - Per-locale accuracy & language consistency
     - Real-Time Factor (RTF) for speech
     - Latency percentiles (min, p50, p90, p95, p99, mean)
     - Single-GPU telemetry on NVIDIA RTX A6000 (GPU 7)

Target:
  https://struttingly-nongeological-briella.ngrok-free.dev/api
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Telemetry Helper
# ---------------------------------------------------------------------------
def get_gpu_telemetry(gpu_id: int = 7) -> dict[str, Any]:
    try:
        cmd = [
            "nvidia-smi",
            f"--id={gpu_id}",
            "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, text=True).strip().split(",")
        if len(out) >= 8:
            return {
                "gpu_index": int(out[0]),
                "name": out[1].strip(),
                "memory_total_mb": float(out[2]),
                "memory_used_mb": float(out[3]),
                "memory_free_mb": float(out[4]),
                "utilization_pct": float(out[5]),
                "temperature_c": float(out[6]),
                "power_draw_w": float(out[7]),
            }
    except Exception:
        pass
    return {"gpu_index": gpu_id, "error": "telemetry_unavailable"}


# ---------------------------------------------------------------------------
# Language Markers for Reply Verification
# ---------------------------------------------------------------------------
_LANG_MARKERS = {
    "en": ("the", "and", "you", "your", "for", "is", "are", "to", "of", "a", "tax", "please", "can", "rate", "must"),
    "lg": (
        "omusolo", "buli", "mu", "ne", "okufuna", "gwa", "nga", "era", "kye", "bye", "ebitundu",
        "ssente", "alipoota", "okola", "bwe", "ndi", "musanyufu", "okukuyamba", "okwewandiisa",
        "oli", "omuntu", "oba", "kitongole", "kampuni", "kino", "bino", "ku", "abakozi", "musaala",
        "amateeka", "abakozesa", "emisolo", "gavumenti", "omulimu", "tewali", "waggulu", "wansi",
        "gyonna", "kinnoomu", "enkola", "bizinensi",
    ),
    "sw": (
        "kodi", "ya", "wa", "kwa", "ni", "katika", "kujisajili", "asilimia", "thamani", "ushuru",
        "marejesho", "huduma", "lazima", "ninafurahi", "kukusaidia", "mtu", "binafsi", "shirika",
        "kampuni", "serikali", "kazi", "wafanyakazi", "mshahara", "mwaka", "mwezi", "nchini",
        "zaidi", "chini", "sheria", "viwango", "kiwango",
    ),
}

def detect_locale_marker(text: str) -> str:
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", text or "")]
    if not words:
        return "unknown"
    scores: dict[str, int] = {}
    for lang, markers in _LANG_MARKERS.items():
        hits = sum(1 for w in words if w in markers)
        scores[lang] = hits
    best = max(scores.items(), key=lambda x: x[1])
    return best[0] if best[1] > 0 else "unknown"


# ---------------------------------------------------------------------------
# Multilingual Question Bank
# ---------------------------------------------------------------------------
TTT_BENCHMARK_BANK = [
    # English (en)
    {"id": "EN-DOM-01", "lang": "en", "domain": "domestic", "topic": "vat", "query": "What is the standard VAT rate in Uganda?", "expected": ["18%"]},
    {"id": "EN-DOM-02", "lang": "en", "domain": "domestic", "topic": "paye", "query": "What is the monthly tax-exempt threshold for PAYE in Uganda?", "expected": ["235,000"]},
    {"id": "EN-DOM-03", "lang": "en", "domain": "domestic", "topic": "rental", "query": "What is the individual rental income tax rate in Uganda?", "expected": ["12%"]},
    {"id": "EN-CUST-01", "lang": "en", "domain": "customs", "topic": "valuation", "query": "What is the primary method of customs valuation under EACCMA?", "expected": ["transaction value"]},
    {"id": "EN-CUST-02", "lang": "en", "domain": "customs", "topic": "baggage", "query": "What is the passenger baggage duty-free allowance threshold?", "expected": ["500", "fifth schedule"]},
    {"id": "EN-EDU-01", "lang": "en", "domain": "tax_education", "topic": "tin", "query": "What is required to register for an individual instant TIN online?", "expected": ["NIN", "national id"]},
    {"id": "EN-EDU-02", "lang": "en", "domain": "tax_education", "topic": "appeals", "query": "How many days does a taxpayer have to lodge an objection to a tax decision?", "expected": ["45", "days"]},

    # Luganda (lg)
    {"id": "LG-DOM-01", "lang": "lg", "domain": "domestic", "topic": "vat", "query": "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?", "expected": ["18", "kikumi"]},
    {"id": "LG-DOM-02", "lang": "lg", "domain": "domestic", "topic": "paye", "query": "Mpeereza emitendera gy'omusolo gwa PAYE ku musaala gw'abakozi?", "expected": ["paye", "musaala", "235,000"]},
    {"id": "LG-DOM-03", "lang": "lg", "domain": "domestic", "topic": "rental", "query": "Omusolo gw'ennyumba ezipangisibwa ku muntu ssekinoomu gusasulwa ku bitundu bimeka?", "expected": ["12%"]},
    {"id": "LG-CUST-01", "lang": "lg", "domain": "customs", "topic": "baggage", "query": "Mpeereza ebikwata ku migugu gy'omusaabaze egitaliko musolo ku kisaawe?", "expected": ["500", "migugu", "musolo"]},
    {"id": "LG-EDU-01", "lang": "lg", "domain": "tax_education", "topic": "tin", "query": "Nnyinza ntya okwewandiisa okufuna namba ya TIN ku mutimbagano gwa URA?", "expected": ["tin", "ura.go.ug", "nin"]},
    {"id": "LG-EDU-02", "lang": "lg", "domain": "tax_education", "topic": "efris", "query": "EFRIS kye ki era kiki ekyetaagisa abasuubuzi okugikozesa?", "expected": ["efris", "ebiwandiiko", "bizinensi", "vat"]},

    # Swahili (sw)
    {"id": "SW-DOM-01", "lang": "sw", "domain": "domestic", "topic": "vat", "query": "Kiwango cha kodi ya ongezeko la thamani (VAT) nchini Uganda ni asilimia ngapi?", "expected": ["18", "kumi na nane"]},
    {"id": "SW-DOM-02", "lang": "sw", "domain": "domestic", "topic": "paye", "query": "Kiwango cha kodi ya PAYE inayotozwa kwenye mshahara wa mfanyakazi ni kipi?", "expected": ["paye", "235,000"]},
    {"id": "SW-DOM-03", "lang": "sw", "domain": "domestic", "topic": "rental", "query": "Kodi ya mapato ya upangishaji kwa mtu binafsi inatozwa kwa asilimia ngapi?", "expected": ["12%"]},
    {"id": "SW-CUST-01", "lang": "sw", "domain": "customs", "topic": "clearance", "query": "Taratibu za forodha za kusafirisha bidhaa nje ya nchi kupitia mpaka wa Malaba ni zipi?", "expected": ["forodha", "malaba", "safirisha"]},
    {"id": "SW-EDU-01", "lang": "sw", "domain": "tax_education", "topic": "tin", "query": "Ninawezaje kujisajili kupata namba ya TIN mtandaoni kupitia tovuti ya URA?", "expected": ["tin", "ura.go.ug", "nin"]},
    {"id": "SW-EDU-02", "lang": "sw", "domain": "tax_education", "topic": "efris", "query": "EFRIS ni nini na ni nani anayetakiwa kutoa ankara za kielektroniki?", "expected": ["efris", "ankara", "elektroniki"]},
]

FUZZY_BENCHMARK_BANK = [
    # English Typos & Colloquialisms
    {
        "id": "FUZZY-EN-01",
        "lang": "en",
        "type": "typos_and_missing_punctuation",
        "query": "wat is da vat rat in ugnda and do i nid to pay it",
        "expected": ["18%"],
    },
    {
        "id": "FUZZY-EN-02",
        "lang": "en",
        "type": "ugandan_colloquialism",
        "query": "banange how do i get dat instant tin from ura for my small duka",
        "expected": ["TIN", "ura.go.ug"],
    },
    {
        "id": "FUZZY-EN-03",
        "lang": "en",
        "type": "code_switching_en_lg",
        "query": "What is the penalty for okulwawo okuwaayo annual income tax return to URA?",
        "expected": ["penalty", "return", "late"],
    },
    # Luganda Typos & Mixed Phrasing
    {
        "id": "FUZZY-LG-01",
        "lang": "lg",
        "type": "luganda_typo",
        "query": "omuslo gwa efrs gusasulwa gutya buli mwzi mu uganda",
        "expected": ["efris", "invoice", "musolo"],
    },
    {
        "id": "FUZZY-LG-02",
        "lang": "lg",
        "type": "code_switching_lg_en",
        "query": "Sente zange eza rental income zisasulwako tax ya bitundu bimeka?",
        "expected": ["12%"],
    },
    # Swahili Typos & Colloquial Phrasing
    {
        "id": "FUZZY-SW-01",
        "lang": "sw",
        "type": "swahili_typo",
        "query": "kiwngo cha kdi ya vt ugnda ni ngapi jamani",
        "expected": ["18%"],
    },
    {
        "id": "FUZZY-SW-02",
        "lang": "sw",
        "type": "code_switching_sw_en",
        "query": "Ni adhabu gani for late filing ya kodi ya mapato nchini Uganda?",
        "expected": ["adhabu", "marejesho"],
    },
]

TTS_BENCHMARK_PROMPTS = [
    {"lang": "lg", "text": "Omusolo gwa EFRIS gusasulwa buli mwezi mu Uganda."},
    {"lang": "lg", "text": "Okufuna namba ya TIN kweri bwereere ku mutimbagano gwa URA."},
    {"lang": "sw", "text": "Kodi ya ongezeko la thamani nchini Uganda ni asilimia kumi na nane."},
    {"lang": "sw", "text": "Unaweza kuwasilisha marejesho ya kodi mtandaoni kupitia tovuti ya URA."},
    {"lang": "en", "text": "The standard Value Added Tax rate in Uganda is eighteen percent."},
    {"lang": "en", "text": "Taxpayers can register for an instant TIN free of charge on the official URA portal."},
]


# ---------------------------------------------------------------------------
# Benchmark Suite Runner
# ---------------------------------------------------------------------------
class FullStackMultilingualBenchmark:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        if "3032" in self.base_url or "ngrok" in self.base_url:
            if not self.base_url.endswith("/api"):
                self.api_base = f"{self.base_url}/api"
            else:
                self.api_base = self.base_url
        else:
            self.api_base = self.base_url

        self.chat_url = f"{self.api_base}/v1/chat"
        self.tts_url = f"{self.api_base}/v1/tts"
        self.asr_url = f"{self.api_base}/v1/asr"

    # --- Modality 1: TTT (Text-to-Text) ---
    async def run_ttt_query(self, client: httpx.AsyncClient, item: dict[str, Any]) -> dict[str, Any]:
        payload = {"message": item["query"], "locale": item["lang"]}
        t0 = time.perf_counter()
        try:
            resp = await client.post(self.chat_url, json=payload, timeout=60.0)
            latency_s = time.perf_counter() - t0
            body = resp.json() if resp.status_code == 200 else {}
            reply = body.get("reply", "")
            detected_locale = detect_locale_marker(reply)
            locale_match = (detected_locale == item["lang"]) or (item["lang"] == "en" and detected_locale in ("en", "unknown"))

            matched = [exp for exp in item.get("expected", []) if exp.lower() in reply.lower()]
            acc_score = len(matched) / len(item["expected"]) if item.get("expected") else 1.0

            return {
                "id": item["id"],
                "lang": item["lang"],
                "status": resp.status_code,
                "latency_s": round(latency_s, 3),
                "reply_snippet": reply.replace("\n", " ")[:120],
                "retrieval_mode": body.get("retrieval_mode", "unknown"),
                "faithfulness_score": body.get("faithfulness_score"),
                "detected_locale": detected_locale,
                "locale_match": locale_match,
                "accuracy_score": round(acc_score, 2),
            }
        except Exception as ex:
            return {
                "id": item["id"],
                "lang": item["lang"],
                "status": 0,
                "latency_s": round(time.perf_counter() - t0, 3),
                "error": str(ex),
                "accuracy_score": 0.0,
            }

    # --- Modality 2: TTS (Text-to-Speech) ---
    async def run_tts_synthesis(self, client: httpx.AsyncClient, prompt: dict[str, Any]) -> dict[str, Any]:
        payload = {"text": prompt["text"], "language": prompt["lang"]}
        t0 = time.perf_counter()
        try:
            resp = await client.post(self.tts_url, json=payload, timeout=60.0)
            latency_s = time.perf_counter() - t0
            body = resp.json() if resp.status_code == 200 else {}
            duration_s = body.get("duration_s", 0.0)
            rtf = latency_s / duration_s if duration_s > 0 else 0.0
            raw_audio = base64.b64decode(body.get("audio_base64", "")) if body.get("audio_base64") else b""

            return {
                "lang": prompt["lang"],
                "status": resp.status_code,
                "latency_s": round(latency_s, 3),
                "duration_s": round(duration_s, 3),
                "rtf": round(rtf, 2),
                "backend": body.get("backend", "unknown"),
                "voice": body.get("voice", "unknown"),
                "audio_bytes_len": len(raw_audio),
                "audio_bytes": raw_audio,
            }
        except Exception as ex:
            return {
                "lang": prompt["lang"],
                "status": 0,
                "latency_s": round(time.perf_counter() - t0, 3),
                "error": str(ex),
            }

    # --- Modality 3: STT (Speech-to-Text) ---
    async def run_stt_transcription(self, client: httpx.AsyncClient, lang: str, audio_bytes: bytes) -> dict[str, Any]:
        if not audio_bytes:
            return {"lang": lang, "status": 0, "error": "empty_audio_bytes"}
        t0 = time.perf_counter()
        url = f"{self.asr_url}?language={lang}"
        try:
            resp = await client.post(
                url,
                content=audio_bytes,
                headers={"Content-Type": "audio/wav"},
                timeout=60.0,
            )
            latency_s = time.perf_counter() - t0
            body = resp.json() if resp.status_code == 200 else {}
            return {
                "lang": lang,
                "status": resp.status_code,
                "latency_s": round(latency_s, 3),
                "text": body.get("text", ""),
                "rtf": body.get("rtf"),
                "backend": body.get("backend", "unknown"),
            }
        except Exception as ex:
            return {
                "lang": lang,
                "status": 0,
                "latency_s": round(time.perf_counter() - t0, 3),
                "error": str(ex),
            }

    # -----------------------------------------------------------------------
    # Main Suite Execution
    # -----------------------------------------------------------------------
    async def execute_all_benchmarks(self) -> dict[str, Any]:
        print(f"\n======================================================================")
        print(f"🌍 MULTILINGUAL FULL-STACK BENCHMARK SUITE (EN, LG, SW)")
        print(f"Target: {self.api_base}")
        print(f"Modalities: TTT, TTS, STT | Tests: Load, Volume, Spike, Fuzzy")
        print(f"Card: NVIDIA RTX A6000 (GPU 7)")
        print(f"======================================================================\n")

        initial_telem = get_gpu_telemetry(7)
        print(f"[Initial Telemetry] VRAM: {initial_telem.get('memory_used_mb', 0):.0f}MB | Temp: {initial_telem.get('temperature_c', 0):.0f}°C | Power: {initial_telem.get('power_draw_w', 0):.0f}W\n")

        overall_start = time.time()
        results: dict[str, Any] = {}

        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=32, max_keepalive_connections=16)) as client:
            # ---------------------------------------------------------------
            # Phase 1: Speech Pipeline (TTS -> STT Round-Trip) Across EN, LG, SW
            # ---------------------------------------------------------------
            print(">>> [Phase 1] Speech Pipeline Benchmark (TTS Synthesis & Whisper ASR STT)")
            tts_results = []
            stt_results = []
            for prompt in TTS_BENCHMARK_PROMPTS:
                tts_res = await self.run_tts_synthesis(client, prompt)
                tts_results.append(tts_res)
                audio_bytes = tts_res.get("audio_bytes", b"")
                print(f"  [TTS - {prompt['lang'].upper()}] {tts_res.get('latency_s')}s | dur={tts_res.get('duration_s')}s | RTF={tts_res.get('rtf')} | backend={tts_res.get('backend')}")

                if audio_bytes:
                    stt_res = await self.run_stt_transcription(client, prompt["lang"], audio_bytes)
                    stt_results.append(stt_res)
                    print(f"  [STT - {prompt['lang'].upper()}] {stt_res.get('latency_s')}s | RTF={stt_res.get('rtf')} | transcript=\"{stt_res.get('text', '')[:70]}...\"")

            results["phase_1_speech"] = {
                "tts": [{k: v for k, v in r.items() if k != "audio_bytes"} for r in tts_results],
                "stt": stt_results,
                "mean_tts_rtf_lg_sw": round(statistics.mean([r["rtf"] for r in tts_results if r.get("lang") in ("lg", "sw") and r.get("rtf")]), 2),
                "mean_stt_rtf": round(statistics.mean([r["rtf"] for r in stt_results if r.get("rtf")]), 2),
            }

            # ---------------------------------------------------------------
            # Phase 2: Multilingual Concurrent Load Test (c=6)
            # ---------------------------------------------------------------
            print("\n>>> [Phase 2] Concurrent Multilingual Load Scaling (c=6 across EN, LG, SW)")
            t_load_start = time.time()
            load_tasks = [self.run_ttt_query(client, item) for item in TTT_BENCHMARK_BANK]
            load_results = await asyncio.gather(*load_tasks)
            t_load_duration = time.time() - t_load_start

            load_latencies = [r["latency_s"] for r in load_results if r["status"] == 200]
            print(f"  Load Phase Completed in {t_load_duration:.2f}s ({len(load_results)/t_load_duration:.2f} qps)")
            print(f"  Latency Profile: p50={statistics.median(load_latencies):.2f}s | p95={statistics.quantiles(load_latencies, n=20)[18]:.2f}s")
            print(f"  Language Match Rate: {sum(1 for r in load_results if r.get('locale_match')) / len(load_results) * 100:.1f}%")

            results["phase_2_load"] = {
                "duration_s": round(t_load_duration, 2),
                "throughput_qps": round(len(load_results) / t_load_duration, 2),
                "queries_evaluated": len(load_results),
                "success_rate_pct": round(sum(1 for r in load_results if r["status"] == 200) / len(load_results) * 100, 2),
                "median_latency_s": round(statistics.median(load_latencies), 3),
                "p95_latency_s": round(statistics.quantiles(load_latencies, n=20)[18], 3) if len(load_latencies) >= 20 else round(max(load_latencies), 3),
                "language_fidelity_pct": round(sum(1 for r in load_results if r.get("locale_match")) / len(load_results) * 100, 2),
                "details": load_results,
            }

            # ---------------------------------------------------------------
            # Phase 3: Instantaneous Traffic Spike Test (15 Requests in 50ms)
            # ---------------------------------------------------------------
            print("\n>>> [Phase 3] Instantaneous Traffic Spike Burst (15 concurrent requests)")
            spike_queries = (TTT_BENCHMARK_BANK * 2)[:15]
            t_spike_start = time.time()
            spike_tasks = [self.run_ttt_query(client, q) for q in spike_queries]
            spike_results = await asyncio.gather(*spike_tasks)
            t_spike_duration = time.time() - t_spike_start

            spike_successes = sum(1 for r in spike_results if r["status"] == 200)
            spike_latencies = [r["latency_s"] for r in spike_results if r["status"] == 200]
            print(f"  Spike Burst Handled: {spike_successes}/{len(spike_queries)} successful in {t_spike_duration:.2f}s")
            print(f"  Peak Burst Latency: p50={statistics.median(spike_latencies):.2f}s | max={max(spike_latencies):.2f}s")

            results["phase_3_spike"] = {
                "burst_requests": len(spike_queries),
                "burst_success_rate_pct": round((spike_successes / len(spike_queries)) * 100, 2),
                "burst_duration_s": round(t_spike_duration, 2),
                "burst_p50_latency_s": round(statistics.median(spike_latencies), 3),
                "burst_max_latency_s": round(max(spike_latencies), 3),
            }

            # ---------------------------------------------------------------
            # Phase 4: High-Volume Multilingual Soak (60 Requests)
            # ---------------------------------------------------------------
            print("\n>>> [Phase 4] High-Volume Multilingual Sustained Soak (60 continuous queries)")
            volume_batch = (TTT_BENCHMARK_BANK * 4)[:60]
            chunk_size = 10
            volume_results = []
            t_vol_start = time.time()
            for chunk_idx in range(0, len(volume_batch), chunk_size):
                chunk = volume_batch[chunk_idx : chunk_idx + chunk_size]
                chunk_tasks = [self.run_ttt_query(client, q) for q in chunk]
                chunk_res = await asyncio.gather(*chunk_tasks)
                volume_results.extend(chunk_res)
                telem = get_gpu_telemetry(7)
                print(f"  Soak [{len(volume_results)}/60] VRAM: {telem.get('memory_used_mb', 0):.0f}MB | GPU Util: {telem.get('utilization_pct', 0):.0f}% | Temp: {telem.get('temperature_c', 0):.0f}°C")

            t_vol_duration = time.time() - t_vol_start
            vol_latencies = [r["latency_s"] for r in volume_results if r["status"] == 200]
            results["phase_4_volume"] = {
                "total_queries": len(volume_results),
                "duration_s": round(t_vol_duration, 2),
                "throughput_qps": round(len(volume_results) / t_vol_duration, 2),
                "success_rate_pct": round(sum(1 for r in volume_results if r["status"] == 200) / len(volume_results) * 100, 2),
                "p50_latency_s": round(statistics.median(vol_latencies), 3),
                "p95_latency_s": round(statistics.quantiles(vol_latencies, n=20)[18], 3) if len(vol_latencies) >= 20 else round(max(vol_latencies), 3),
            }

            # ---------------------------------------------------------------
            # Phase 5: Fuzzy Phrasing, Typos & Code-Switching Robustness
            # ---------------------------------------------------------------
            print("\n>>> [Phase 5] Fuzzy Input, Noise & Code-Switching Robustness")
            fuzzy_tasks = [self.run_ttt_query(client, item) for item in FUZZY_BENCHMARK_BANK]
            fuzzy_results = await asyncio.gather(*fuzzy_tasks)

            for fr, spec in zip(fuzzy_results, FUZZY_BENCHMARK_BANK):
                print(f"  [{spec['type']}] \"{spec['query']}\" -> {fr.get('reply_snippet')} (acc={fr.get('accuracy_score')})")

            fuzzy_acc = statistics.mean([r["accuracy_score"] for r in fuzzy_results]) * 100
            results["phase_5_fuzzy"] = {
                "total_fuzzy_evaluated": len(fuzzy_results),
                "fuzzy_accuracy_pct": round(fuzzy_acc, 2),
                "details": fuzzy_results,
            }

        total_elapsed = time.time() - overall_start
        final_telem = get_gpu_telemetry(7)

        # -------------------------------------------------------------------
        # Cross-Language Accuracy & Parity Summary
        # -------------------------------------------------------------------
        per_lang: dict[str, dict[str, Any]] = {}
        for l_code in ("en", "lg", "sw"):
            lang_items = [r for r in load_results if r.get("lang") == l_code and r.get("status") == 200]
            if lang_items:
                per_lang[l_code] = {
                    "count": len(lang_items),
                    "mean_accuracy_pct": round(statistics.mean([r["accuracy_score"] for r in lang_items]) * 100, 2),
                    "median_latency_s": round(statistics.median([r["latency_s"] for r in lang_items]), 3),
                    "language_match_pct": round(sum(1 for r in lang_items if r["locale_match"]) / len(lang_items) * 100, 2),
                }

        report = {
            "metadata": {
                "benchmark_date": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "target_gateway": self.api_base,
                "total_duration_s": round(total_elapsed, 2),
                "gpu_hardware": {
                    "card": "NVIDIA RTX A6000",
                    "gpu_index": 7,
                    "initial_vram_mb": initial_telem.get("memory_used_mb"),
                    "peak_vram_mb": final_telem.get("memory_used_mb"),
                    "vram_headroom_mb": final_telem.get("memory_free_mb"),
                    "temperature_c": final_telem.get("temperature_c"),
                    "power_w": final_telem.get("power_draw_w"),
                },
            },
            "per_language_equity_summary": per_lang,
            "benchmark_results": results,
        }

        return report


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Multilingual Full-Stack Benchmark Suite")
    parser.add_argument(
        "--target",
        default="https://struttingly-nongeological-briella.ngrok-free.dev/api",
        help="Target API gateway URL",
    )
    parser.add_argument(
        "--out",
        default="Results/metrics/multilingual_ngrok_load_stress_report.json",
        help="Output report JSON file path",
    )
    args = parser.parse_args()

    runner = FullStackMultilingualBenchmark(base_url=args.target)
    report = asyncio.run(runner.execute_all_benchmarks())

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print("\n======================================================================")
    print("📊 MULTILINGUAL FULL-STACK BENCHMARK SUMMARY REPORT")
    print("======================================================================")
    eq = report["per_language_equity_summary"]
    for lang in ("en", "lg", "sw"):
        if lang in eq:
            d = eq[lang]
            print(f"[{lang.upper()}] Accuracy: {d['mean_accuracy_pct']}% | Latency p50: {d['median_latency_s']}s | Language Fidelity: {d['language_match_pct']}%")

    p1 = report["benchmark_results"]["phase_1_speech"]
    print(f"Speech TTS RTF (lg, sw):      {p1['mean_tts_rtf_lg_sw']} (Spark-TTS-SALT)")
    print(f"Speech STT ASR RTF:           {p1['mean_stt_rtf']} (Whisper-SALT)")

    p3 = report["benchmark_results"]["phase_3_spike"]
    print(f"Spike Burst Success Rate:     {p3['burst_success_rate_pct']}% ({p3['burst_requests']} reqs in {p3['burst_duration_s']}s)")

    p5 = report["benchmark_results"]["phase_5_fuzzy"]
    print(f"Fuzzy & Code-Switching Acc:   {p5['fuzzy_accuracy_pct']}%")

    print(f"Report written to:            {out_path}")
    print("======================================================================\n")


if __name__ == "__main__":
    main()
