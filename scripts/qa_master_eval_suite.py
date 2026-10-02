#!/usr/bin/env python3
"""Senior Reviewer & QA Master Validation Suite.

Executes complete quality assurance validation across:
1. Non-IT 1-Click Login & Auth Flow (dev-token minting + /v1/me validation)
2. E2E Core Regression (Chat, TTS Spark-SALT, ASR Whisper-SALT, Health, Ready)
3. Syntax Error & Fuzzy Robustness (Glued words, inverted questions, misspellings)
4. Language Preservation Invariant (Misspellings NEVER alter response language)
5. Traffic Spike Burst (Concurrent instant burst)
6. High-Volume Multilingual Soak (Sustained multilingual load)
7. GPU 2 Hardware Telemetry & Thermal Headroom
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

TARGET_URL = os.environ.get("TARGET_URL", "http://127.0.0.1:8090")
NGROK_URL = os.environ.get("NGROK_URL", "https://struttingly-nongeological-briella.ngrok-free.dev")

HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
}


def get_gpu_telemetry(gpu_id: int = 2) -> dict[str, Any]:
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
    except Exception as ex:
        print(f"Warning: GPU {gpu_id} telemetry failed: {ex}")
    return {"gpu_index": gpu_id, "error": "telemetry_unavailable"}


def http_request(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    base_url: str = TARGET_URL,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any], float]:
    url = f"{base_url.rstrip('/')}{path}"
    req_headers = dict(HEADERS)
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.perf_counter() - t0) * 1000
            content = resp.read()
            try:
                body = json.loads(content.decode("utf-8"))
            except Exception:
                body = {"raw_len": len(content)}
            return resp.status, body, elapsed
    except urllib.error.HTTPError as ex:
        elapsed = (time.perf_counter() - t0) * 1000
        try:
            body = json.loads(ex.read().decode("utf-8"))
        except Exception:
            body = {"error": str(ex)}
        return ex.code, body, elapsed
    except Exception as ex:
        elapsed = (time.perf_counter() - t0) * 1000
        return 500, {"error": str(ex)}, elapsed


def run_qa_suite() -> dict[str, Any]:
    print("=" * 80)
    print("SENIOR REVIEWER & QA MASTER VALIDATION SUITE")
    print(f"Local GPU Target: {TARGET_URL} | Ngrok Tunnel: {NGROK_URL}")
    print("=" * 80)

    gpu_baseline = get_gpu_telemetry(2)
    print(f"\n[GPU 2 Baseline] Memory Used: {gpu_baseline.get('memory_used_mb', 0):.0f} / "
          f"{gpu_baseline.get('memory_total_mb', 0):.0f} MiB | "
          f"Free: {gpu_baseline.get('memory_free_mb', 0):.0f} MiB | "
          f"Temp: {gpu_baseline.get('temperature_c', 0):.0f}°C")

    report: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "target_url": TARGET_URL,
        "ngrok_url": NGROK_URL,
        "gpu_telemetry_initial": gpu_baseline,
        "sections": {},
    }

    # -------------------------------------------------------------------------
    # 1. Non-IT 1-Click Login & Staff Auth Flow
    # -------------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("SECTION 1: Non-IT 1-Click Login & Staff Token Flow (Eliminating Python CLI)")
    print("-" * 70)
    auth_tests = [
        {"role": "ura_staff", "email": "agent.sarah@ura.go.ug", "user_id": "agent-sarah", "expected_dest": "/agent"},
        {"role": "ura_admin", "email": "admin@ura.go.ug", "user_id": "admin-user", "expected_dest": "/admin"},
        {"role": "ura_auditor", "email": "auditor@ura.go.ug", "user_id": "auditor-user", "expected_dest": "/analytics"},
        {"role": "public", "email": "taxpayer@gmail.com", "user_id": "citizen-user", "expected_dest": "/"},
        {"role": "ura_admin", "email": "admin@gmail.com", "user_id": "spoof-user", "expected_dest": "/", "expected_role": "public"},
    ]
    auth_results = []
    for test in auth_tests:
        code, body, el = http_request("/v1/auth/dev-token", method="POST", payload=test)
        token = body.get("token", "")
        token_valid = code == 200 and len(token.split(".")) == 3
        expected_role = test.get("expected_role", test["role"])
        dest_matches = body.get("redirect_url") == test["expected_dest"]
        
        # Test /v1/me whoami verification using the token
        me_code, me_body, me_el = http_request("/v1/me", headers={"Authorization": f"Bearer {token}"})
        role_matches = me_body.get("role") == expected_role
        test_passed = token_valid and role_matches and dest_matches
        
        auth_results.append({
            "requested_role": test["role"],
            "resolved_role": body.get("role"),
            "redirect_url": body.get("redirect_url"),
            "status_code": code,
            "token_minted": token_valid,
            "me_status_code": me_code,
            "me_role": me_body.get("role"),
            "latency_ms": round(el, 2),
            "pass": test_passed,
        })
        status_sym = "PASS" if test_passed else "FAIL"
        print(f"  [{status_sym}] Mint & Verify '{test['role']}' -> HTTP {code} in {el:.1f}ms | Role: {me_body.get('role')} | Dest: {body.get('redirect_url')}")

    report["sections"]["non_it_auth"] = auth_results

    # -------------------------------------------------------------------------
    # 2. End-to-End Core System & Model Regression
    # -------------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("SECTION 2: E2E Core Regression (Health, Models, Speech, RAG, Rate Tables)")
    print("-" * 70)
    reg_tests = [
        {"name": "System Health", "path": "/health", "method": "GET", "expected_status": 200},
        {"name": "System Ready", "path": "/ready", "method": "GET", "expected_status": 200},
        {"name": "Speech Health (Whisper + Spark)", "path": "/v1/speech/health", "method": "GET", "expected_status": 200},
        {"name": "VAT Rate Calculation (Calculator Router)", "path": "/v1/chat", "method": "POST",
         "payload": {"message": "What is the standard VAT rate in Uganda?", "locale": "en"}, "expected_status": 200, "keyword": "18%"},
        {"name": "PAYE Tax Filing Procedure (RAG Workflow)", "path": "/v1/chat", "method": "POST",
         "payload": {"message": "How do I file my PAYE return for August 2026?", "locale": "en"}, "expected_status": 200, "keyword": "PAYE"},
        {"name": "Spark-TTS-SALT Luganda Voice Synthesis", "path": "/v1/tts", "method": "POST",
         "payload": {"text": "Omusolo gwa EFRIS gusasulwa buli mwezi mu Uganda.", "language": "lg"}, "expected_status": 200, "expected_voice": "spark_salt_lg"},
        {"name": "Spark-TTS-SALT Swahili Voice Synthesis", "path": "/v1/tts", "method": "POST",
         "payload": {"text": "Kodi ya ongezeko la thamani nchini Uganda ni asilimia kumi na nane.", "language": "sw"}, "expected_status": 200, "expected_voice": "spark_salt_sw"},
    ]
    reg_results = []
    for rt in reg_tests:
        code, body, el = http_request(rt["path"], method=rt["method"], payload=rt.get("payload"))
        passed = (code == rt["expected_status"])
        if rt.get("keyword") and passed:
            reply = body.get("reply", "")
            passed = rt["keyword"].lower() in reply.lower()
        if rt.get("expected_voice") and passed:
            voice = body.get("voice", "")
            passed = (voice == rt["expected_voice"])

        reg_results.append({
            "test": rt["name"],
            "status_code": code,
            "latency_ms": round(el, 2),
            "pass": passed,
        })
        status_sym = "PASS" if passed else "FAIL"
        print(f"  [{status_sym}] {rt['name']} -> HTTP {code} in {el:.1f}ms")

    report["sections"]["core_regression"] = reg_results

    # -------------------------------------------------------------------------
    # 3. Fuzzy Robustness & Language Preservation Invariant
    # -------------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("SECTION 3: Syntax Errors & Fuzzy Typo Tolerance (Language Invariant Guard)")
    print("-" * 70)
    fuzzy_cases = [
        {
            "id": "TYPO-EN-01",
            "description": "Domain Typo: Withholding Tax ('witholding', 'assessmnt')",
            "query": "How do I pay assessmnt witholding tax?",
            "expected_locale": "en",
            "must_contain_any": ["withholding", "tax", "paye"],
        },
        {
            "id": "TYPO-EN-02",
            "description": "General English + Domain Typo ('wat', 'vat rat')",
            "query": "wat is the vat rat in uganda?",
            "expected_locale": "en",
            "must_contain_any": ["18%", "vat"],
        },
        {
            "id": "TYPO-EN-03",
            "description": "SMS Slang + Glued Words ('hw to aply for tin')",
            "query": "hw to aply for tin online",
            "expected_locale": "en",
            "must_contain_any": ["tin", "register", "apply"],
        },
        {
            "id": "TYPO-EN-04",
            "description": "Double-Letter Noise & Slips ('penlaty', 'fillling', 'retun')",
            "query": "penlaty for late fillling of vat retun",
            "expected_locale": "en",
            "must_contain_any": ["penalty", "return", "vat"],
        },
        {
            "id": "TYPO-EN-05",
            "description": "Inverted Auxiliary Question Syntax ('how i can get', 'tinregistration')",
            "query": "how i can get my tinregistration in kampala?",
            "expected_locale": "en",
            "must_contain_any": ["tin", "register"],
        },
        {
            "id": "TYPO-EN-06",
            "description": "Glued Words + Service Misspelling ('howmuch', 'servise')",
            "query": "howmuch is the servise fee for efris invoyce?",
            "expected_locale": "en",
            "must_contain_any": ["efris", "fee", "free", "invoice"],
        },
        {
            "id": "TYPO-EN-07",
            "description": "Dropped Subject Pronoun ('am having a problm with my calender')",
            "query": "am having a problm with my calender and filing dedline",
            "expected_locale": "en",
            "must_contain_any": ["deadline", "filing", "return"],
        },
        {
            "id": "TYPO-EN-08",
            "description": "Grammar Tense Confusion ('did not filed')",
            "query": "is there penalty if i did not filed my tax return?",
            "expected_locale": "en",
            "must_contain_any": ["penalty", "return", "file"],
        },
        # Genuine Luganda & Swahili queries to prove legitimate multi-language routing works
        {
            "id": "LANG-LG-01",
            "description": "Authentic Luganda VAT Query",
            "query": "Omusolo gwa VAT guli gwa bbeeyi ki mu Uganda?",
            "expected_locale": "lg",
            "must_contain_any": ["18%", "ebitundu"],
        },
        {
            "id": "LANG-SW-01",
            "description": "Authentic Swahili VAT Query",
            "query": "Kodi ya VAT nchini Uganda ni kiasi gani?",
            "expected_locale": "sw",
            "must_contain_any": ["18%", "asilimia", "kodi"],
        },
    ]

    fuzzy_results = []
    for fc in fuzzy_cases:
        code, body, el = http_request("/v1/chat", method="POST", payload={"message": fc["query"], "locale": "en" if "EN" in fc["id"] else fc["expected_locale"]})
        returned_locale = body.get("locale", "")
        reply = body.get("reply", "")
        
        # INVARIANT: Misspellings in English must NEVER flip locale to lg or sw
        locale_preserved = (returned_locale == fc["expected_locale"])
        content_relevant = any(k.lower() in reply.lower() for k in fc["must_contain_any"])
        passed = (code == 200 and locale_preserved and content_relevant)

        fuzzy_results.append({
            "id": fc["id"],
            "description": fc["description"],
            "query": fc["query"],
            "returned_locale": returned_locale,
            "expected_locale": fc["expected_locale"],
            "locale_preserved": locale_preserved,
            "content_relevant": content_relevant,
            "latency_ms": round(el, 2),
            "pass": passed,
        })
        status_sym = "PASS" if passed else "FAIL"
        print(f"  [{status_sym}] {fc['id']}: '{fc['query'][:42]}...' -> Locale: '{returned_locale}' (Expected: '{fc['expected_locale']}') in {el:.1f}ms")

    report["sections"]["fuzzy_and_language_preservation"] = fuzzy_results

    # -------------------------------------------------------------------------
    # 4. Instantaneous Traffic Spike Burst (c = 20 in burst window)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("SECTION 4: Instantaneous Traffic Spike Burst (c = 20 concurrent requests)")
    print("-" * 70)
    spike_concurrency = 20
    spike_prompts = [
        "What is the VAT rate in Uganda?",
        "How do I apply for a TIN?",
        "What are the PAYE brackets?",
        "Explain EFRIS electronic invoicing.",
        "When is the deadline for rental tax return?",
    ]
    spike_latencies = []
    spike_errors = 0
    t0_spike = time.perf_counter()

    def _spike_worker(idx: int) -> float:
        msg = spike_prompts[idx % len(spike_prompts)]
        code, _, elapsed = http_request("/v1/chat", method="POST", payload={"message": msg, "locale": "en"}, timeout=60.0)
        if code != 200:
            raise RuntimeError(f"HTTP {code}")
        return elapsed

    with ThreadPoolExecutor(max_workers=spike_concurrency) as executor:
        futs = [executor.submit(_spike_worker, i) for i in range(spike_concurrency)]
        for f in as_completed(futs):
            try:
                spike_latencies.append(f.result())
            except Exception:
                spike_errors += 1

    dur_spike = time.perf_counter() - t0_spike
    spike_latencies.sort()
    p50_spike = statistics.median(spike_latencies) if spike_latencies else 0
    p95_spike = spike_latencies[int(len(spike_latencies) * 0.95)] if spike_latencies else 0
    rps_spike = spike_concurrency / dur_spike if dur_spike > 0 else 0

    spike_report = {
        "burst_requests": spike_concurrency,
        "duration_s": round(dur_spike, 3),
        "throughput_rps": round(rps_spike, 1),
        "latency_p50_ms": round(p50_spike, 2),
        "latency_p95_ms": round(p95_spike, 2),
        "errors": spike_errors,
        "error_rate_pct": round((spike_errors / spike_concurrency) * 100, 2),
        "pass": spike_errors == 0,
    }
    print(f"  [SPIKE RESULT] {spike_concurrency} burst requests in {dur_spike:.2f}s ({rps_spike:.1f} RPS)")
    print(f"  Latency p50: {p50_spike:.1f}ms | p95: {p95_spike:.1f}ms | Error Rate: {spike_report['error_rate_pct']}% | Status: {'PASS' if spike_errors == 0 else 'DEGRADED'}")
    report["sections"]["traffic_spike"] = spike_report

    # -------------------------------------------------------------------------
    # 5. Sustained High-Volume Multilingual Soak (35 turns)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("SECTION 5: Sustained High-Volume Multilingual Soak (35 requests)")
    print("-" * 70)
    volume_requests = 35
    soak_prompts = [
        {"msg": "What is the standard VAT rate in Uganda?", "loc": "en"},
        {"msg": "How can I register for a TIN as an individual?", "loc": "en"},
        {"msg": "What are the penalties for late return submission?", "loc": "en"},
        {"msg": "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?", "loc": "lg"},
        {"msg": "Nnyinza ntya okufuna TIN ku mutimbagano gwa URA?", "loc": "lg"},
        {"msg": "Kodi ya ongezeko la thamani nchini Uganda ni kiasi gani?", "loc": "sw"},
        {"msg": "Ninawezaje kujisajili kupata TIN ya biashara?", "loc": "sw"},
    ]
    soak_latencies = []
    soak_errors = 0
    t0_soak = time.perf_counter()

    def _soak_worker(idx: int) -> float:
        item = soak_prompts[idx % len(soak_prompts)]
        code, _, elapsed = http_request("/v1/chat", method="POST", payload={"message": item["msg"], "locale": item["loc"]}, timeout=60.0)
        if code != 200:
            raise RuntimeError(f"HTTP {code}")
        return elapsed

    with ThreadPoolExecutor(max_workers=5) as executor:
        futs = [executor.submit(_soak_worker, i) for i in range(volume_requests)]
        for f in as_completed(futs):
            try:
                soak_latencies.append(f.result())
            except Exception:
                soak_errors += 1

    dur_soak = time.perf_counter() - t0_soak
    soak_latencies.sort()
    p50_soak = statistics.median(soak_latencies) if soak_latencies else 0
    p95_soak = soak_latencies[int(len(soak_latencies) * 0.95)] if soak_latencies else 0
    rps_soak = volume_requests / dur_soak if dur_soak > 0 else 0

    soak_report = {
        "soak_requests": volume_requests,
        "concurrency": 5,
        "duration_s": round(dur_soak, 3),
        "throughput_rps": round(rps_soak, 1),
        "latency_p50_ms": round(p50_soak, 2),
        "latency_p95_ms": round(p95_soak, 2),
        "errors": soak_errors,
        "error_rate_pct": round((soak_errors / volume_requests) * 100, 2),
        "pass": soak_errors == 0,
    }
    print(f"  [SOAK RESULT] {volume_requests} requests in {dur_soak:.2f}s ({rps_soak:.1f} RPS)")
    print(f"  Latency p50: {p50_soak:.1f}ms | p95: {p95_soak:.1f}ms | Error Rate: {soak_report['error_rate_pct']}% | Status: {'PASS' if soak_errors == 0 else 'DEGRADED'}")
    report["sections"]["volume_soak"] = soak_report

    # -------------------------------------------------------------------------
    # 6. Ngrok Public Gateway E2E Verification
    # -------------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("SECTION 6: Live Ngrok Public Gateway Verification")
    print("-" * 70)
    ngrok_tests = [
        {"path": "/api/health", "method": "GET", "desc": "Public Gateway Health"},
        {"path": "/api/v1/speech/health", "method": "GET", "desc": "Public Gateway Speech Health"},
        {"path": "/api/v1/auth/dev-token", "method": "POST", "payload": {"role": "ura_staff", "email": "officer@ura.go.ug"}, "desc": "1-Click Non-IT Login via Ngrok"},
        {"path": "/api/v1/chat", "method": "POST", "payload": {"message": "wat is the vat rat?", "locale": "en"}, "desc": "Typo Query ('wat is vat rat') via Ngrok"},
    ]
    ngrok_results = []
    for nt in ngrok_tests:
        code, body, el = http_request(nt["path"], method=nt["method"], payload=nt.get("payload"), base_url=NGROK_URL)
        passed = (code == 200)
        ngrok_results.append({
            "test": nt["desc"],
            "path": nt["path"],
            "status_code": code,
            "latency_ms": round(el, 2),
            "pass": passed,
        })
        status_sym = "PASS" if passed else "FAIL"
        print(f"  [{status_sym}] {nt['desc']} -> HTTP {code} in {el:.1f}ms")

    report["sections"]["ngrok_gateway"] = ngrok_results

    # -------------------------------------------------------------------------
    # 7. Post-Test Hardware Telemetry
    # -------------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("SECTION 7: Post-Test Hardware Telemetry & Thermal Health (GPU 2)")
    print("-" * 70)
    gpu_final = get_gpu_telemetry(2)
    report["gpu_telemetry_final"] = gpu_final
    print(f"  [GPU 2 Post-Test] Memory: {gpu_final.get('memory_used_mb', 0):.0f} / {gpu_final.get('memory_total_mb', 0):.0f} MiB "
          f"({gpu_final.get('memory_free_mb', 0):.0f} MiB free) | "
          f"Temp: {gpu_final.get('temperature_c', 0):.0f}°C | "
          f"Power: {gpu_final.get('power_draw_w', 0):.1f}W | "
          f"Thermal Headroom: {90 - gpu_final.get('temperature_c', 0):.0f}°C")

    # Save complete JSON report
    out_dir = Path("Results/metrics")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "qa_master_eval_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n Master QA Report saved to: {out_file}")
    print("=" * 80)
    return report


if __name__ == "__main__":
    rep = run_qa_suite()
    all_passed = all(
        all(item.get("pass", True) for item in (sec if isinstance(sec, list) else [sec]))
        for sec in rep["sections"].values()
    )
    sys.exit(0 if all_passed else 1)
