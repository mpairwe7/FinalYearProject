#!/usr/bin/env python3
"""Comprehensive Senior QA & Reviewer Test Suite:

E2E Regression, Fuzzy Typo-Tolerance, Spike Concurrency, Volume Throughput,
and Multimodal Speech (Whisper-Large-SALT STT + Spark-TTS-SALT) across EN, LG,
SW over Ngrok Gateway.
"""

import asyncio
import base64
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

GATEWAY = os.getenv("GATEWAY_URL", "https://struttingly-nongeological-briella.ngrok-free.dev/api")
HEADERS = {
    "ngrok-skip-browser-warning": "true",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# Test Cases Definitions (Aligned to FY2026-27 Statutes & Resolved Defect Fixes)
# ---------------------------------------------------------------------------

REGRESSION_CASES = [
    # Core Statutory English (FY2026-27 Rate Table)
    {"id": "REG-EN-01", "locale": "en", "query": "What is the standard VAT rate in Uganda?", "expected_statutory": ["18%"], "forbidden": ["United States", "IRS"]},
    {"id": "REG-EN-02", "locale": "en", "query": "What is the resident corporation tax rate?", "expected_statutory": ["30%"], "forbidden": []},
    {"id": "REG-EN-03", "locale": "en", "query": "What is the withholding tax rate on professional fees for residents?", "expected_statutory": ["6%"], "forbidden": []},
    {"id": "REG-EN-04", "locale": "en", "query": "What is the monthly tax-free threshold for PAYE in Uganda?", "expected_statutory": ["335,000"], "forbidden": []},
    {"id": "REG-EN-05", "locale": "en", "query": "What is the rental income tax rate for individuals?", "expected_statutory": ["12%"], "forbidden": []},

    # Issue #434 Regressions (Pronoun 'us' must not trigger US refusal)
    {"id": "REG-434-01", "locale": "en", "query": "Can you please tell us what the VAT rate is for local supplies?", "expected_statutory": ["18%"], "forbidden": ["United States", "IRS", "refuse"]},
    {"id": "REG-434-02", "locale": "en", "query": "Explain to us how individual rental tax is calculated in Kampala.", "expected_statutory": ["12%"], "forbidden": ["United States", "jurisdiction"]},
    {"id": "REG-434-03", "locale": "en", "query": "Advise us on the mandatory documents needed for a business TIN registration.", "expected_statutory": ["TIN"], "forbidden": ["United States", "IRS"]},

    # Issue #430 Regressions (Paraphrases & Anti-hijacking)
    {"id": "REG-430-01", "locale": "en", "query": "How much tax does an individual pay on gross rental earnings?", "expected_statutory": ["12%"], "forbidden": []},
    {"id": "REG-430-02", "locale": "en", "query": "My annual turnover was 120 million UGX last year from small retail trade.", "expected_statutory": [], "forbidden": ["Do you want to register for VAT right now?"]},

    # Core Statutory Luganda (Bantu ground truth)
    {"id": "REG-LG-01", "locale": "lg", "query": "Kiwalo ki eky'omusolo gwa VAT mu Uganda?", "expected_statutory": ["18"], "forbidden": ["IRS"]},
    {"id": "REG-LG-02", "locale": "lg", "query": "Omusolo gw'obupangisa bwa mayumba ku bantu ssekinnoomu guli ebitundu bimeka?", "expected_statutory": ["12"], "forbidden": []},
    {"id": "REG-LG-03", "locale": "lg", "query": "Nteekwa kusasula musolo gwa kampuni ku bitundu bimeka mu Uganda?", "expected_statutory": ["30"], "forbidden": []},
    {"id": "REG-LG-04", "locale": "lg", "query": "Nsiiba ntya okufuna namba ya TIN mu URA?", "expected_statutory": ["TIN"], "forbidden": []},

    # Core Statutory Swahili
    {"id": "REG-SW-01", "locale": "sw", "query": "Kiwango cha kodi ya ongezeko la thamani (VAT) nchini Uganda ni asilimia ngapi?", "expected_statutory": ["18"], "forbidden": ["IRS"]},
    {"id": "REG-SW-02", "locale": "sw", "query": "Kiwango cha kodi ya mapato ya kodi ya majengo ya kupangisha ni asilimia ngapi?", "expected_statutory": ["12"], "forbidden": []},
    {"id": "REG-SW-03", "locale": "sw", "query": "Kiwango cha kodi ya mapato ya makampuni ni kiasi gani?", "expected_statutory": ["30"], "forbidden": []},
    {"id": "REG-SW-04", "locale": "sw", "query": "Je, ninawezaje kupata namba ya TIN kutoka URA?", "expected_statutory": ["TIN"], "forbidden": []},
]

FUZZY_CASES = [
    # Typo tolerance EN
    {"id": "FUZZ-EN-01", "locale": "en", "query": "Whwt is the stndard VATT rate in Ugnda?", "expected_statutory": ["18%"]},
    {"id": "FUZZ-EN-02", "locale": "en", "query": "wht is the individul rentl inome tx percetnage?", "expected_statutory": ["12%"]},
    {"id": "FUZZ-EN-03", "locale": "en", "query": "whats the payee threshold befroe being taxed ugx?", "expected_statutory": ["335,000"]},

    # Typo tolerance & Dialectal noise LG
    {"id": "FUZZ-LG-01", "locale": "lg", "query": "omusolo gwa vaat guli ebitunndu bimeka mu uganda?", "expected_statutory": ["18"]},
    {"id": "FUZZ-LG-02", "locale": "lg", "query": "ompolo gwo bupangisa mu kampala ku bantu bimeka?", "expected_statutory": ["12"]},

    # Typo tolerance SW
    {"id": "FUZZ-SW-01", "locale": "sw", "query": "kiwango cha kodii ya vaat nchini ugnda ni asilimia ngapi?", "expected_statutory": ["18"]},
    {"id": "FUZZ-SW-02", "locale": "sw", "query": "kiwango ch kodi ya kodi ya nyumba za kupangisha ni nini?", "expected_statutory": ["12"]},

    # Code-switching & Vernacular slang
    {"id": "FUZZ-MIX-01", "locale": "lg", "query": "Nze ndi trader mu Kikuubo, VAT rate eri etya mu business zaffe?", "expected_statutory": ["18"]},
    {"id": "FUZZ-MIX-02", "locale": "sw", "query": "Habari zenu, ningependa kujua corporation tax rate ya company yangu nchini Uganda.", "expected_statutory": ["30"]},
]

SPEECH_CASES = [
    {"id": "SPEECH-EN", "locale": "en", "text": "The standard Value Added Tax rate in Uganda is 18 percent.", "voice": "en-US-AriaNeural", "content_type": "audio/mpeg"},
    {"id": "SPEECH-LG", "locale": "lg", "text": "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi mu Uganda.", "voice": "spark_salt_lg", "content_type": "audio/wav"},
    {"id": "SPEECH-SW", "locale": "sw", "text": "Kiwango cha kodi ya ongezeko la thamani nchini Uganda ni asilimia 18.", "voice": "spark_salt_sw", "content_type": "audio/wav"},
]

@dataclass
class TurnResult:
    case_id: str
    suite: str
    locale: str
    query: str
    status_code: int
    latency_s: float
    retrieval_mode: str
    reply_preview: str
    statutory_passed: bool
    negative_constraint_passed: bool
    error: str = ""

async def evaluate_chat_turn(client: httpx.AsyncClient, case: dict[str, Any], suite: str) -> TurnResult:
    case_id = case["id"]
    locale = case.get("locale", "en")
    query = case["query"]
    expected_statutory = case.get("expected_statutory", [])
    forbidden = case.get("forbidden", [])

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{GATEWAY}/v1/chat",
            json={
                "message": query,
                "session_id": f"qa-eval-{case_id}-{int(time.time()*1000)}",
                "locale": locale,
            },
            headers=HEADERS,
            timeout=60.0,
        )
        lat = time.perf_counter() - t0
        status = resp.status_code
        if status == 200:
            data = resp.json()
            reply = data.get("reply", "")
            mode = data.get("retrieval_mode", "unknown")

            stat_pass = True
            for exp in expected_statutory:
                clean_exp = exp.replace(",", "").replace("%", "")
                clean_reply = reply.replace(",", "")
                if exp not in reply and clean_exp not in clean_reply:
                    stat_pass = False
                    break

            neg_pass = True
            for forb in forbidden:
                if forb.lower() in reply.lower():
                    neg_pass = False
                    break

            return TurnResult(
                case_id=case_id,
                suite=suite,
                locale=locale,
                query=query,
                status_code=status,
                latency_s=round(lat, 3),
                retrieval_mode=mode,
                reply_preview=reply[:120].replace("\n", " "),
                statutory_passed=stat_pass,
                negative_constraint_passed=neg_pass,
            )
        else:
            return TurnResult(
                case_id=case_id,
                suite=suite,
                locale=locale,
                query=query,
                status_code=status,
                latency_s=round(lat, 3),
                retrieval_mode="error",
                reply_preview="",
                statutory_passed=False,
                negative_constraint_passed=False,
                error=f"HTTP {status}: {resp.text[:100]}",
            )
    except Exception as exc:
        lat = time.perf_counter() - t0
        return TurnResult(
            case_id=case_id,
            suite=suite,
            locale=locale,
            query=query,
            status_code=0,
            latency_s=round(lat, 3),
            retrieval_mode="exception",
            reply_preview="",
            statutory_passed=False,
            negative_constraint_passed=False,
            error=str(exc),
        )

async def evaluate_speech_turn(client: httpx.AsyncClient, case: dict[str, Any]) -> dict[str, Any]:
    case_id = case["id"]
    locale = case["locale"]
    text = case["text"]
    voice = case["voice"]
    content_type = case.get("content_type", "audio/wav")

    # 1. TTS
    t0_tts = time.perf_counter()
    tts_status = 0
    audio_bytes = b""
    tts_err = ""
    try:
        resp = await client.post(
            f"{GATEWAY}/v1/tts",
            json={"text": text, "language": locale, "voice": voice},
            headers=HEADERS,
            timeout=45.0,
        )
        tts_status = resp.status_code
        if tts_status == 200:
            b64 = resp.json().get("audio_base64")
            if b64:
                audio_bytes = base64.b64decode(b64)
        else:
            tts_err = resp.text[:100]
    except Exception as e:
        tts_err = str(e)
    tts_lat = time.perf_counter() - t0_tts

    # 2. ASR
    asr_status = 0
    transcript = ""
    asr_lat = 0.0
    asr_err = ""
    if audio_bytes:
        t0_asr = time.perf_counter()
        try:
            resp = await client.post(
                f"{GATEWAY}/v1/asr?language={locale}",
                content=audio_bytes,
                headers={"Content-Type": content_type, "ngrok-skip-browser-warning": "true"},
                timeout=45.0,
            )
            asr_status = resp.status_code
            if asr_status == 200:
                transcript = resp.json().get("text", "")
            else:
                asr_err = resp.text[:100]
        except Exception as e:
            asr_err = str(e)
        asr_lat = time.perf_counter() - t0_asr

    return {
        "case_id": case_id,
        "locale": locale,
        "source_text": text,
        "tts_status": tts_status,
        "tts_latency_s": round(tts_lat, 3),
        "audio_bytes": len(audio_bytes),
        "tts_error": tts_err,
        "asr_status": asr_status,
        "asr_latency_s": round(asr_lat, 3),
        "asr_transcript": transcript,
        "asr_error": asr_err,
    }

async def run_spike_test(client: httpx.AsyncClient, burst_size: int = 20) -> list[TurnResult]:
    print(f"\n--- Running Phase 4: Spike Test (Burst of {burst_size} Concurrent Requests) ---", flush=True)
    queries = [
        ("en", "What is the standard VAT rate in Uganda?"),
        ("en", "What is the resident corporation tax rate?"),
        ("lg", "Kiwalo ki eky'omusolo gwa VAT mu Uganda?"),
        ("lg", "Omusolo gw'obupangisa bwa mayumba guli ebitundu bimeka?"),
        ("sw", "Kiwango cha kodi ya ongezeko la thamani nchini Uganda ni asilimia ngapi?"),
        ("sw", "Kiwango cha kodi ya mapato ya makampuni ni kiasi gani?"),
    ]
    spike_cases = []
    for i in range(burst_size):
        locale, q = queries[i % len(queries)]
        spike_cases.append({
            "id": f"SPIKE-{i+1:02d}",
            "locale": locale,
            "query": q,
            "expected_statutory": ["18"] if ("VAT" in q or "ongezeko" in q) else [],
            "forbidden": [],
        })
    tasks = [evaluate_chat_turn(client, c, "spike") for c in spike_cases]
    results = await asyncio.gather(*tasks)
    for r in results:
        sym = "✓" if r.status_code == 200 else "✗"
        print(f"  [{sym}] {r.case_id} ({r.locale.upper()}) | HTTP {r.status_code} | {r.latency_s}s | Mode: {r.retrieval_mode}", flush=True)
    return results

async def run_volume_test(client: httpx.AsyncClient, total_requests: int = 30, concurrency: int = 6) -> list[TurnResult]:
    print(f"\n--- Running Phase 5: Sustained Volume Test ({total_requests} Requests at c={concurrency}) ---", flush=True)
    queries = [
        ("en", "What is the standard VAT rate in Uganda?"),
        ("en", "What is the resident corporation tax rate?"),
        ("en", "How do I register for a TIN as a sole proprietor?"),
        ("lg", "Nsiiba ntya okufuna namba ya TIN mu URA?"),
        ("lg", "Kiwalo ki eky'omusolo gwa VAT mu Uganda?"),
        ("sw", "Je, ninawezaje kupata namba ya TIN kutoka URA?"),
        ("sw", "Kiwango cha kodi ya ongezeko la thamani nchini Uganda ni asilimia ngapi?"),
    ]
    all_cases = []
    for i in range(total_requests):
        locale, q = queries[i % len(queries)]
        all_cases.append({
            "id": f"VOL-{i+1:03d}",
            "locale": locale,
            "query": q,
            "expected_statutory": [],
            "forbidden": [],
        })

    results = []
    for i in range(0, len(all_cases), concurrency):
        chunk = all_cases[i : i + concurrency]
        chunk_results = await asyncio.gather(*[evaluate_chat_turn(client, c, "volume") for c in chunk])
        results.extend(chunk_results)
        print(f"  Volume chunk {i//concurrency + 1}/{(len(all_cases)+concurrency-1)//concurrency} done: {len(results)}/{len(all_cases)} complete", flush=True)
    return results

async def main():
    print("========================================================================", flush=True)
    print("  URA CHATBOT SENIOR QA BENCHMARK & E2E SYSTEM STRESS AUDIT", flush=True)
    print(f"  Gateway Target: {GATEWAY}", flush=True)
    print("========================================================================", flush=True)

    limits = httpx.Limits(max_keepalive_connections=64, max_connections=128)
    async with httpx.AsyncClient(limits=limits, timeout=90.0) as client:
        # Phase 1: Core Statutory & Defect Regression (concurrency = 3)
        print("\n--- Phase 1: Statutory & Defect Regression Suite ---", flush=True)
        regression_results = []
        for i in range(0, len(REGRESSION_CASES), 3):
            chunk = REGRESSION_CASES[i : i + 3]
            chunk_res = await asyncio.gather(*[evaluate_chat_turn(client, c, "regression") for c in chunk])
            for res in chunk_res:
                regression_results.append(res)
                sym = "✓" if (res.status_code == 200 and res.statutory_passed and res.negative_constraint_passed) else "✗"
                print(f"  [{sym}] {res.case_id} ({res.locale.upper()}) | HTTP {res.status_code} | {res.latency_s}s | Mode: {res.retrieval_mode} | Pass: {res.statutory_passed}", flush=True)

        # Phase 2: Fuzzy & Typo Tolerance Suite (concurrency = 2)
        print("\n--- Phase 2: Fuzzy & Typo-Tolerance Suite ---", flush=True)
        fuzzy_results = []
        for i in range(0, len(FUZZY_CASES), 2):
            chunk = FUZZY_CASES[i : i + 2]
            chunk_res = await asyncio.gather(*[evaluate_chat_turn(client, c, "fuzzy") for c in chunk])
            for res in chunk_res:
                fuzzy_results.append(res)
                sym = "✓" if (res.status_code == 200 and res.statutory_passed) else "✗"
                print(f"  [{sym}] {res.case_id} ({res.locale.upper()}) | HTTP {res.status_code} | {res.latency_s}s | Mode: {res.retrieval_mode} | Pass: {res.statutory_passed}", flush=True)

        # Phase 3: Speech Pipeline Audio Verification (Whisper + Spark-TTS)
        print("\n--- Phase 3: Multimodal Speech Pipeline (Whisper + Spark-TTS) ---", flush=True)
        speech_results = []
        for sc in SPEECH_CASES:
            res = await evaluate_speech_turn(client, sc)
            speech_results.append(res)
            print(f"  [TTS {sc['locale'].upper()}] HTTP {res['tts_status']} | Bytes: {res['audio_bytes']} | {res['tts_latency_s']}s", flush=True)
            print(f"  [ASR {sc['locale'].upper()}] HTTP {res['asr_status']} | Transcript: '{res['asr_transcript'][:60]}...' | {res['asr_latency_s']}s", flush=True)

        # Phase 4: Spike Concurrency Test (Burst = 20)
        spike_results = await run_spike_test(client, burst_size=20)

        # Phase 5: Sustained Volume Test (30 requests, c=6)
        volume_results = await run_volume_test(client, total_requests=30, concurrency=6)

    # Aggregations & Metrics
    all_chat = regression_results + fuzzy_results + spike_results + volume_results
    total_chat = len(all_chat)
    success_chat = sum(1 for r in all_chat if r.status_code == 200)
    latencies = [r.latency_s for r in all_chat if r.status_code == 200]

    reg_pass = sum(1 for r in regression_results if r.statutory_passed and r.negative_constraint_passed)
    fuzzy_pass = sum(1 for r in fuzzy_results if r.statutory_passed)
    spike_success = sum(1 for r in spike_results if r.status_code == 200)
    volume_success = sum(1 for r in volume_results if r.status_code == 200)

    tts_success = sum(1 for r in speech_results if r["tts_status"] == 200 and r["audio_bytes"] > 0)
    asr_success = sum(1 for r in speech_results if r["asr_status"] == 200 and len(r["asr_transcript"]) > 0)

    p50 = round(statistics.median(latencies), 3) if latencies else 0.0
    p90 = round(statistics.quantiles(latencies, n=10)[8], 3) if len(latencies) >= 10 else 0.0
    p95 = round(statistics.quantiles(latencies, n=20)[18], 3) if len(latencies) >= 20 else 0.0
    p99 = round(statistics.quantiles(latencies, n=100)[98], 3) if len(latencies) >= 100 else (max(latencies) if latencies else 0.0)

    modes: dict[str, int] = {}
    for r in all_chat:
        modes[r.retrieval_mode] = modes.get(r.retrieval_mode, 0) + 1

    report_payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "gateway": GATEWAY,
        "summary": {
            "total_queries": total_chat,
            "overall_http_success_rate": round(success_chat / total_chat * 100, 2),
            "regression_pass_rate": round(reg_pass / len(regression_results) * 100, 2),
            "fuzzy_pass_rate": round(fuzzy_pass / len(fuzzy_results) * 100, 2),
            "spike_success_rate": round(spike_success / len(spike_results) * 100, 2),
            "volume_success_rate": round(volume_success / len(volume_results) * 100, 2),
            "tts_speech_success_rate": round(tts_success / len(speech_results) * 100, 2),
            "asr_speech_success_rate": round(asr_success / len(speech_results) * 100, 2),
        },
        "latency_profile_seconds": {
            "p50": p50,
            "p90": p90,
            "p95": p95,
            "p99": p99,
            "min": round(min(latencies), 3) if latencies else 0,
            "max": round(max(latencies), 3) if latencies else 0,
            "mean": round(statistics.mean(latencies), 3) if latencies else 0,
        },
        "retrieval_mode_breakdown": modes,
        "speech_evaluations": speech_results,
        "sample_regression_turns": [asdict(r) for r in regression_results],
        "sample_fuzzy_turns": [asdict(r) for r in fuzzy_results],
    }

    out_file = "Results/metrics/qa_e2e_stress_audit_report.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(report_payload, f, indent=2)

    print("\n========================================================================", flush=True)
    print("  QA AUDIT & E2E STRESS BENCHMARK SUMMARY", flush=True)
    print("========================================================================", flush=True)
    print(f"Total Requests Executed:    {total_chat}", flush=True)
    print(f"HTTP Availability / Health: {report_payload['summary']['overall_http_success_rate']}%", flush=True)
    print(f"Statutory Regression Pass:  {report_payload['summary']['regression_pass_rate']}% ({reg_pass}/{len(regression_results)})", flush=True)
    print(f"Fuzzy / Typo Robustness:    {report_payload['summary']['fuzzy_pass_rate']}% ({fuzzy_pass}/{len(fuzzy_results)})", flush=True)
    print(f"Spike Burst Resilience:     {report_payload['summary']['spike_success_rate']}% ({spike_success}/{len(spike_results)})", flush=True)
    print(f"Sustained Volume Success:   {report_payload['summary']['volume_success_rate']}% ({volume_success}/{len(volume_results)})", flush=True)
    print(f"Speech TTS Success Rate:    {report_payload['summary']['tts_speech_success_rate']}% ({tts_success}/{len(speech_results)})", flush=True)
    print(f"Speech STT Success Rate:    {report_payload['summary']['asr_speech_success_rate']}% ({asr_success}/{len(speech_results)})", flush=True)
    print(f"Latency Percentiles:        p50={p50}s | p90={p90}s | p95={p95}s | p99={p99}s", flush=True)
    print(f"Report Artifact Saved:      {out_file}", flush=True)
    print("========================================================================\n", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
