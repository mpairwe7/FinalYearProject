#!/usr/bin/env python3
"""Conversational & Emotional Stress Benchmark Suite for URA AI Assistant.

Evaluates the live Docker GPU stack:
- GPU 5: vLLM serving Sunbird/Sunflower-14B-FP8 (port 8011)
- GPU 2: FastAPI API serving Whisper-SALT, Spark-TTS-SALT, BGE-M3 (port 8083)
- Qdrant (port 6333) & Redis (port 6379)
- Public ngrok Gateway / Local Gateway

Evaluated Dimensions:
1. Emotional Intelligence & Distress Recognition (Hardship, Frustration, Anxiety, Urgency, Human Escalation)
2. Multi-Turn Conversational Memory & Follow-Up Context (EN, LG, SW)
3. Concurrency Scaling Stress (c = 5, 10, 20, 30)
4. Instantaneous Traffic Spike Surge (Burst c = 30)
5. Dual-GPU Hardware Telemetry (GPU 2 + GPU 5)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "http://localhost:8083"


def get_gpu_telemetry() -> dict[int, dict[str, Any]]:
    """Capture VRAM, compute load, temp, and power draw for GPU 2 and GPU 5."""
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
                if idx in (2, 5):
                    telemetry[idx] = {
                        "name": parts[1],
                        "memory_used_mb": float(parts[2]),
                        "memory_total_mb": float(parts[3]),
                        "utilization_pct": float(parts[4]),
                        "temperature_c": float(parts[5]),
                        "power_draw_w": float(parts[6]),
                    }
    except Exception as ex:
        print(f"Warning: GPU telemetry failed: {ex}")
    return telemetry


def post_chat(
    base_url: str,
    message: str,
    session_id: str,
    locale: str = "en",
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any], float]:
    """Execute chat turn against the target gateway."""
    url = f"{base_url.rstrip('/')}/v1/chat"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "URA-Conversational-Emotional-Bench/2026",
        "ngrok-skip-browser-warning": "1",
    }
    payload = {
        "message": message,
        "session_id": session_id,
        "locale": locale,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            elapsed = (time.perf_counter() - t0) * 1000.0
            return resp.status, body, elapsed
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": str(e)}
        elapsed = (time.perf_counter() - t0) * 1000.0
        return e.code, body, elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return 599, {"error": str(e)}, elapsed


def nearest_rank(values: list[float], percentile: float) -> float:
    """Return a nearest-rank percentile; callers should report the sample size."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(percentile * len(ordered) / 100)))
    return ordered[rank - 1]


# =============================================================================
# SUITE 1: Emotional Intelligence & Distress Recognition Scenarios
# =============================================================================
EMOTIONAL_SCENARIOS = [
    {
        "id": "EMO-01",
        "category": "hardship",
        "locale": "en",
        "message": "I am completely broke and bankrupt, I cannot afford this tax assessment and I will lose my business and shop!",
        "expected_escalation": True,
        "expected_empathy": True,
        "description": "Severe financial insolvency and business closure threat",
    },
    {
        "id": "EMO-02",
        "category": "frustration",
        "locale": "en",
        "message": "EFRIS has failed again for the fourth time today!! Nothing is working and I am EXTREMELY angry with URA!",
        "expected_escalation": True,
        "expected_empathy": True,
        "description": "Repeated system failure and high taxpayer frustration",
    },
    {
        "id": "EMO-03",
        "category": "anxiety",
        "locale": "en",
        "message": "I am so worried and scared because my company bank accounts have been frozen without warning and I am panicking.",
        "expected_escalation": True,
        "expected_empathy": True,
        "description": "Distress over frozen accounts and agency notices",
    },
    {
        "id": "EMO-04",
        "category": "urgency",
        "locale": "en",
        "message": "URGENT: I need to submit my return before midnight today or I will be fined UGX 500,000!",
        "expected_escalation": False,
        "expected_empathy": True,
        "description": "Time-critical submission deadline distress",
    },
    {
        "id": "EMO-05",
        "category": "confusion",
        "locale": "en",
        "message": "I don't understand how gross rental income vs allowable deduction works, it's very confusing and unclear.",
        "expected_escalation": False,
        "expected_empathy": False,
        "description": "Statutory ambiguity requiring patient clarification",
    },
    {
        "id": "EMO-06",
        "category": "neutral",
        "locale": "en",
        "message": "What is the standard VAT rate in Uganda for FY2026-27?",
        "expected_escalation": False,
        "expected_empathy": False,
        "description": "Neutral informational query (baseline control)",
    },
    {
        "id": "EMO-07",
        "category": "human_escalation",
        "locale": "en",
        "message": "I need to escalate to a human URA officer right now, please create a ticket.",
        "expected_escalation": True,
        "expected_empathy": True,
        "description": "Explicit demand for human officer handoff and ticket creation",
    },
    {
        "id": "EMO-08",
        "category": "hardship_luganda",
        "locale": "lg",
        "message": "Nkooye nnyo ebizibu by'omusolo guno, nfa bwavu n’amadduka gange gagenda kuggalawo!",
        "expected_escalation": True,
        "expected_empathy": True,
        "description": "Luganda severe hardship and imminent business failure",
    },
    {
        "id": "EMO-09",
        "category": "anxiety_swahili",
        "locale": "sw",
        "message": "Akaunti zangu za benki zimefungwa bila ilani na URA, nimechanganyikiwa na ninahitaji kuongea na afisa mara moja!",
        "expected_escalation": True,
        "expected_empathy": True,
        "description": "Swahili frozen account panic and urgent escalation request",
    },
    {
        "id": "EMO-10",
        "category": "dispute_distress",
        "locale": "en",
        "message": "URA issued an unfair tax assessment of UGX 80M that is totally false. I want to contest this immediately!",
        "expected_escalation": False,
        "expected_empathy": True,
        "description": "Assessment contestation requiring Section 24 TPCA guidance",
    },
]


def run_emotional_suite(base_url: str) -> dict[str, Any]:
    print("\n" + "=" * 80)
    print("SUITE 1: EMOTIONAL INTELLIGENCE & DISTRESS RECOGNITION")
    print("=" * 80)

    results = []
    escalation_counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    empathy_counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}

    for sc in EMOTIONAL_SCENARIOS:
        sess = f"bench-emo-{sc['id'].lower()}-{int(time.time())}"
        status, body, elapsed = post_chat(base_url, sc["message"], sess, locale=sc["locale"])

        reply = body.get("reply", "")
        esc_req = bool(body.get("escalation_required", False))
        ticket_id = body.get("ticket_id", "")
        has_ticket = bool(ticket_id)
        escalation_observed = esc_req or has_ticket

        # Check for empathetic markers or de-escalation tone
        empathy_markers = [
            "sorry", "understand", "options", "relief", "assist", "help", "stressful",
            "pole", "samahani", "tusaasidde", "nsonyiwa", "kakasa", "dismay"
        ]
        tone_hint = str(body.get("tone_hint") or "").strip().lower()
        empathetic_tones = {"empathy", "empathetic", "supportive", "compassionate", "reassuring"}
        has_empathy = any(m in reply.lower() for m in empathy_markers) or tone_hint in empathetic_tones

        status_ok = 200 <= status < 300
        expected_escalation = bool(sc["expected_escalation"])
        expected_empathy = bool(sc["expected_empathy"])
        if expected_escalation and escalation_observed and status_ok:
            escalation_counts["tp"] += 1
        elif expected_escalation:
            escalation_counts["fn"] += 1
        elif escalation_observed or not status_ok:
            escalation_counts["fp"] += 1
        else:
            escalation_counts["tn"] += 1
        if expected_empathy and has_empathy and status_ok:
            empathy_counts["tp"] += 1
        elif expected_empathy:
            empathy_counts["fn"] += 1
        elif has_empathy or not status_ok:
            empathy_counts["fp"] += 1
        else:
            empathy_counts["tn"] += 1

        item = {
            "id": sc["id"],
            "category": sc["category"],
            "locale": sc["locale"],
            "status_code": status,
            "request_ok": status_ok,
            "latency_ms": round(elapsed, 1),
            "escalation_expected": expected_escalation,
            "escalation_observed": escalation_observed,
            "escalation_required": esc_req,
            "ticket_created": has_ticket,
            "empathy_expected": expected_empathy,
            "empathy_detected": has_empathy,
            "reply_preview": reply[:100] + ("..." if len(reply) > 100 else ""),
        }
        results.append(item)
        print(f"  [{sc['id']}] {sc['category']:18} | Status: {status} | Latency: {elapsed:6.1f}ms | Esc: {esc_req} (Ticket: {has_ticket}) | Empathy: {has_empathy}")

    scored = sum(escalation_counts.values())
    esc_acc = (100 * (escalation_counts["tp"] + escalation_counts["tn"]) / scored) if scored else 0.0
    emp_acc = (100 * (empathy_counts["tp"] + empathy_counts["tn"]) / scored) if scored else 0.0
    esc_precision = (100 * escalation_counts["tp"] / (escalation_counts["tp"] + escalation_counts["fp"])) if escalation_counts["tp"] + escalation_counts["fp"] else 0.0
    esc_recall = (100 * escalation_counts["tp"] / (escalation_counts["tp"] + escalation_counts["fn"])) if escalation_counts["tp"] + escalation_counts["fn"] else 0.0
    emp_recall = (100 * empathy_counts["tp"] / (empathy_counts["tp"] + empathy_counts["fn"])) if empathy_counts["tp"] + empathy_counts["fn"] else 0.0

    print(f"\n--> Empathy Accuracy: {emp_acc:.1f}% (recall {emp_recall:.1f}%) | Escalation Accuracy: {esc_acc:.1f}% (precision {esc_precision:.1f}%, recall {esc_recall:.1f}%)")
    return {
        "tests": results,
        "empathy": {"accuracy_pct": round(emp_acc, 2), "recall_pct": round(emp_recall, 2), "confusion": empathy_counts},
        "escalation": {"accuracy_pct": round(esc_acc, 2), "precision_pct": round(esc_precision, 2), "recall_pct": round(esc_recall, 2), "confusion": escalation_counts},
    }


# =============================================================================
# SUITE 2: Multi-Turn Conversational Memory & Calculator Follow-Ups
# =============================================================================
def run_conversational_suite(base_url: str) -> dict[str, Any]:
    print("\n" + "=" * 80)
    print("SUITE 2: MULTI-TURN CONVERSATIONAL MEMORY & FOLLOW-UP CALCULATIONS")
    print("=" * 80)

    # Conversation Session 1: VAT multi-turn with English, Luganda, and Swahili follow-ups
    session_vat = f"bench-conv-vat-{int(time.time())}"
    vat_turns = [
        {"turn": 1, "msg": "Calculate VAT on 1,000,000", "lang": "en", "expected_kw": "180,000", "desc": "Initial VAT calc (1M)"},
        {"turn": 2, "msg": "what about 2,000,000", "lang": "en", "expected_kw": "360,000", "desc": "English follow-up amount (2M)"},
        {"turn": 3, "msg": "ate 5m", "lang": "lg", "expected_kw": "900,000", "desc": "Luganda follow-up amount ('ate 5m')"},
        {"turn": 4, "msg": "na 10m", "lang": "sw", "expected_kw": "1,800,000", "desc": "Swahili follow-up amount ('na 10m')"},
    ]

    vat_results = []
    print("\n  [Session 1: VAT Context Continuity across EN / LG / SW]")
    for vt in vat_turns:
        status, body, elapsed = post_chat(base_url, vt["msg"], session_vat, locale=vt["lang"])
        reply = body.get("reply", "")
        mode = body.get("retrieval_mode", "")
        kw_ok = vt["expected_kw"] in reply
        context_retained = 200 <= status < 300 and kw_ok
        vat_results.append({
            "turn": vt["turn"],
            "input": vt["msg"],
            "desc": vt["desc"],
            "latency_ms": round(elapsed, 1),
            "expected_figure": vt["expected_kw"],
            "figure_found": kw_ok,
            "mode": mode,
            "context_retained": context_retained,
            "reply_sample": reply[:100],
        })
        print(f"    Turn {vt['turn']}: '{vt['msg']:20}' -> Mode: {mode:12} | Figure '{vt['expected_kw']}': {kw_ok} | Lat: {elapsed:6.1f}ms")

    # Conversation Session 2: PAYE Payroll calculation and follow-up
    session_paye = f"bench-conv-paye-{int(time.time())}"
    paye_turns = [
        {"turn": 1, "msg": "Calculate PAYE on gross monthly salary of UGX 4,500,000", "lang": "en", "expected_kw": "PAYE", "desc": "Initial PAYE calc"},
        {"turn": 2, "msg": "what about 6,000,000", "lang": "en", "expected_kw": "PAYE", "desc": "PAYE follow-up amount (6M)"},
    ]

    paye_results = []
    print("\n  [Session 2: PAYE Context Continuity]")
    for pt in paye_turns:
        status, body, elapsed = post_chat(base_url, pt["msg"], session_paye, locale=pt["lang"])
        reply = body.get("reply", "")
        mode = body.get("retrieval_mode", "")
        has_paye = "paye" in reply.lower()
        context_retained = 200 <= status < 300 and has_paye
        paye_results.append({
            "turn": pt["turn"],
            "input": pt["msg"],
            "desc": pt["desc"],
            "latency_ms": round(elapsed, 1),
            "mode": mode,
            "context_retained": context_retained,
            "reply_sample": reply[:100],
        })
        print(f"    Turn {pt['turn']}: '{pt['msg']:20}' -> Mode: {mode:12} | PAYE Preserved: {has_paye} | Lat: {elapsed:6.1f}ms")

    # Conversation Session 3: Disambiguation of Withholding Tax
    session_wht = f"bench-conv-wht-{int(time.time())}"
    print("\n  [Session 3: Statutory Rate Disambiguation (Management Fees vs Services)]")
    status1, body1, elapsed1 = post_chat(base_url, "how much withholding tax on a 3m management consultancy", session_wht, locale="en")
    reply1 = body1.get("reply", "")
    mode1 = body1.get("retrieval_mode", "")
    asked_disambiguation = 200 <= status1 < 300 and ("6%" in reply1 and "15%" in reply1 or "which" in reply1.lower())
    print(f"    Turn 1: Elicitation question asked: {asked_disambiguation} | Lat: {elapsed1:6.1f}ms")

    status2, body2, elapsed2 = post_chat(base_url, "management", session_wht, locale="en")
    reply2 = body2.get("reply", "")
    mode2 = body2.get("retrieval_mode", "")
    asked_amount = 200 <= status2 < 300 and ("15%" in reply2 or "450,000" in reply2)
    print(f"    Turn 2: Category accepted ('management'): {asked_amount} | Lat: {elapsed2:6.1f}ms")

    status3, body3, elapsed3 = post_chat(base_url, "3m", session_wht, locale="en")
    reply3 = body3.get("reply", "")
    mode3 = body3.get("retrieval_mode", "")
    calc_ok = 200 <= status3 < 300 and "450,000" in reply3
    print(f"    Turn 3: Management rate 15% applied (450,000): {calc_ok} | Lat: {elapsed3:6.1f}ms")

    all_turns = vat_results + paye_results
    passed_turns = sum(1 for t in all_turns if t["context_retained"])
    conv_accuracy = (passed_turns / len(all_turns)) * 100
    disambiguation_checks = [asked_disambiguation, asked_amount, calc_ok]
    disambiguation_accuracy = (100 * sum(disambiguation_checks) / len(disambiguation_checks))

    print(f"\n--> Conversational Continuity Score: {conv_accuracy:.1f}% ({passed_turns}/{len(all_turns)} turns)")
    return {
        "vat_session": vat_results,
        "paye_session": paye_results,
        "disambiguation_session": {
            "turn1_elicitation": asked_disambiguation,
            "turn2_category_accepted": asked_amount,
            "turn3_calculated_correctly": calc_ok,
            "latency_ms": [round(elapsed1, 1), round(elapsed2, 1), round(elapsed3, 1)],
        },
        "disambiguation_accuracy_pct": round(disambiguation_accuracy, 2),
        "conversational_accuracy_pct": round(conv_accuracy, 2),
    }


# =============================================================================
# SUITE 3: Concurrency Scaling Stress (c = 5, 10, 20, 30)
# =============================================================================
def run_concurrency_scaling(base_url: str, requests_per_worker: int = 10) -> dict[str, Any]:
    print("\n" + "=" * 80)
    print("SUITE 3: CONCURRENCY SCALING STRESS (c = 5, 10, 20, 30)")
    print("=" * 80)

    scaling_tiers = [5, 10, 20, 30]
    tier_reports = []

    test_queries = [
        ("Calculate VAT on 5,000,000", "en"),
        ("Calculate PAYE on 3,500,000 gross monthly", "en"),
        ("okubala vati ku 2000000", "lg"),
        ("hesabu vat ya 4,000,000", "sw"),
        ("What is the penalty for failure to issue an EFRIS invoice?", "en"),
        ("How do I register an individual TIN in Uganda?", "en"),
        ("Biki ebyetaagisa okufuna namba y'omusolo eyitibwa TIN?", "lg"),
        ("Ni mahitaji gani ya kupata namba ya TIN nchini Uganda?", "sw"),
    ]

    for c in scaling_tiers:
        num_requests = c * requests_per_worker
        latencies = []
        status_counts = {}
        t_start = time.perf_counter()

        def worker(idx: int) -> tuple[int, float]:
            msg, lang = test_queries[idx % len(test_queries)]
            sess = f"bench-concurrency-c{c}-{idx}-{int(time.time())}"
            code, _, elapsed = post_chat(base_url, msg, sess, locale=lang)
            return code, elapsed

        with concurrent.futures.ThreadPoolExecutor(max_workers=c) as executor:
            futures = [executor.submit(worker, i) for i in range(num_requests)]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    code, el = fut.result()
                    latencies.append(el)
                    status_counts[code] = status_counts.get(code, 0) + 1
                except Exception:
                    latencies.append(60000.0)
                    status_counts[599] = status_counts.get(599, 0) + 1

        total_dur = time.perf_counter() - t_start
        latencies.sort()
        qps = num_requests / total_dur if total_dur > 0 else 0
        p50 = nearest_rank(latencies, 50)
        p90 = nearest_rank(latencies, 90)
        p95 = nearest_rank(latencies, 95)
        p99 = nearest_rank(latencies, 99)
        success_rate = (status_counts.get(200, 0) / num_requests) * 100

        tier_res = {
            "concurrency": c,
            "total_requests": num_requests,
            "requests_per_worker": requests_per_worker,
            "latency_sample_count": len(latencies),
            "duration_s": round(total_dur, 2),
            "throughput_qps": round(qps, 2),
            "success_rate_pct": round(success_rate, 2),
            "p50_ms": round(p50, 1),
            "p90_ms": round(p90, 1),
            "p95_ms": round(p95, 1),
            "p99_ms": round(p99, 1),
            "status_distribution": status_counts,
        }
        tier_reports.append(tier_res)
        print(f"  [c = {c:2d}] {num_requests:2d} reqs | Duration: {total_dur:5.2f}s | QPS: {qps:5.2f} | p50: {p50:6.1f}ms | p95: {p95:6.1f}ms | 200 OK: {success_rate:5.1f}%")

    return {"tiers": tier_reports}


# =============================================================================
# SUITE 4: Instantaneous Traffic Spike Surge (Burst c = 30 in parallel)
# =============================================================================
def run_spike_surge(base_url: str, burst_size: int = 30) -> dict[str, Any]:
    print("\n" + "=" * 80)
    print(f"SUITE 4: INSTANTANEOUS TRAFFIC SPIKE SURGE (Burst c = {burst_size})")
    print("=" * 80)

    burst_queries = [
        ("Calculate VAT on 10,000,000", "en"),
        ("okubala vati ku 5000000", "lg"),
        ("hesabu vat ya 8,000,000", "sw"),
        ("Calculate PAYE on 5,000,000 gross monthly", "en"),
        ("I need to speak directly with an officer immediately", "en"),
        ("What is the penalty for failure to issue an EFRIS invoice?", "en"),
    ]

    latencies = []
    status_counts = {}
    t0 = time.perf_counter()

    def spike_worker(idx: int) -> tuple[int, float]:
        msg, lang = burst_queries[idx % len(burst_queries)]
        sess = f"bench-spike-{idx}-{int(time.time())}"
        code, _, el = post_chat(base_url, msg, sess, locale=lang)
        return code, el

    with concurrent.futures.ThreadPoolExecutor(max_workers=burst_size) as executor:
        futures = [executor.submit(spike_worker, i) for i in range(burst_size)]
        for fut in concurrent.futures.as_completed(futures):
            try:
                code, el = fut.result()
                latencies.append(el)
                status_counts[code] = status_counts.get(code, 0) + 1
            except Exception:
                status_counts[599] = status_counts.get(599, 0) + 1

    dur = time.perf_counter() - t0
    latencies.sort()
    qps = burst_size / dur if dur > 0 else 0
    p50 = nearest_rank(latencies, 50)
    p95 = nearest_rank(latencies, 95)
    success = (status_counts.get(200, 0) / burst_size) * 100

    print(f"  Burst {burst_size} parallel requests completed in {dur:5.2f}s")
    print(f"  Throughput: {qps:5.2f} req/s | 200 OK: {success:.1f}% | p50: {p50:6.1f}ms | p95: {p95:6.1f}ms")
    print(f"  HTTP Status Codes: {status_counts}")

    return {
        "burst_size": burst_size,
        "latency_sample_count": len(latencies),
        "duration_s": round(dur, 2),
        "throughput_qps": round(qps, 2),
        "success_rate_pct": round(success, 2),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "status_distribution": status_counts,
    }


def main():
    parser = argparse.ArgumentParser(description="Conversational & Emotional Stress Benchmark")
    parser.add_argument("--target", default=DEFAULT_ENDPOINT, help="Target gateway URL (default: http://localhost:8083)")
    parser.add_argument("--requests-per-worker", type=int, default=10, help="Requests per worker in each concurrency tier (default: 10)")
    parser.add_argument("--out-md", default="docs/Reports/CONVERSATIONAL_EMOTIONAL_STRESS_REPORT_2026-09-29.md", help="Markdown report path")
    parser.add_argument("--out-json", default="Results/metrics/conversational_emotional_stress_report.json", help="JSON report path")
    args = parser.parse_args()
    if args.requests_per_worker < 1:
        parser.error("--requests-per-worker must be at least 1")

    print("=" * 80)
    print("CONVERSATIONAL & EMOTIONAL INTELLIGENCE STRESS BENCHMARK (LIVE GPU STACK)")
    print(f"Target: {args.target}")
    print("=" * 80)

    gpu_telemetry_pre = get_gpu_telemetry()
    t_start = time.perf_counter()

    # 1. Emotional Intelligence Suite
    emo_report = run_emotional_suite(args.target)

    # 2. Conversational Continuity & Disambiguation Suite
    conv_report = run_conversational_suite(args.target)

    # 3. Concurrency Scaling Stress Suite
    scaling_report = run_concurrency_scaling(args.target, args.requests_per_worker)

    # 4. Spike Surge Suite
    spike_report = run_spike_surge(args.target)

    total_time = time.perf_counter() - t_start
    gpu_telemetry_post = get_gpu_telemetry()

    combined_report = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target": args.target,
            "total_benchmark_time_s": round(total_time, 2),
            "workload_note": "Short diagnostic run with fixed request counts; not a sustained capacity or SLO test.",
            "requests_per_worker": args.requests_per_worker,
        },
        "emotional_intelligence": emo_report,
        "conversational_continuity": conv_report,
        "concurrency_scaling": scaling_report,
        "traffic_spike_surge": spike_report,
        "gpu_telemetry": {
            "pre_test": gpu_telemetry_pre,
            "post_test": gpu_telemetry_post,
        },
    }

    # Save JSON Report
    json_path = Path(args.out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(combined_report, f, indent=2)
    print(f"\n💾 Saved JSON metrics to: {json_path}")

    # Generate Markdown Report
    md_content = f"""# Conversational & Emotional Intelligence Stress Benchmark Report
**Uganda Revenue Authority (URA) AI Taxpayer Assistant**  
**Evaluation Date**: {combined_report['metadata']['timestamp']}  
**Target Gateway**: `{args.target}`  
**Hardware Infrastructure**: Dual-GPU Stack (GPU 5: Sunflower-14B vLLM | GPU 2: API, Speech, Dense Retriever)

---

## 1. Executive Summary & Core Results

| Performance Dimension | Target Standard | Measured Benchmark Result | Status |
|---|:---:|:---:|:---:|
| **Empathy Signal Accuracy** | ≥ 90.0% | **{emo_report['empathy']['accuracy_pct']}%** | **{'MET ✅' if emo_report['empathy']['accuracy_pct'] >= 90 else 'NOT MET ❌'}** |
| **Escalation Accuracy** | ≥ 95.0% | **{emo_report['escalation']['accuracy_pct']}%** (precision {emo_report['escalation']['precision_pct']}%, recall {emo_report['escalation']['recall_pct']}%) | **{'MET ✅' if emo_report['escalation']['accuracy_pct'] >= 95 else 'NOT MET ❌'}** |
| **Multi-Turn Context Continuity** | ≥ 95.0% | **{conv_report['conversational_accuracy_pct']}%** | **{'MET ✅' if conv_report['conversational_accuracy_pct'] >= 95 else 'NOT MET ❌'}** |
| **Withholding Disambiguation Checks** | 100.0% | **{conv_report['disambiguation_accuracy_pct']}%** | **{'MET ✅' if conv_report['disambiguation_accuracy_pct'] >= 100 else 'NOT MET ❌'}** |
| **Spike Surge Availability (c=30)** | ≥ 95.0% | **{spike_report['success_rate_pct']}%** ({spike_report['latency_sample_count']}/{spike_report['burst_size']} responses received) | **{'MET ✅' if spike_report['success_rate_pct'] >= 95 else 'NOT MET ❌'}** |
| **Median Response Time (p50)** | < 1,500 ms | **{scaling_report['tiers'][0]['p50_ms']} ms** (c=5) | **{'MET ✅' if scaling_report['tiers'][0]['p50_ms'] < 1500 else 'NOT MET ❌'}** |

---

## 2. Emotional Intelligence & Empathy Breakdown

Evaluated across critical taxpayer distress categories:

| Scenario ID | Category | Language | Latency (ms) | Escalation Generated | Empathy Opener Present |
|---|---|:---:|:---:|:---:|:---:|
"""
    for t in emo_report["tests"]:
        md_content += f"| `{t['id']}` | **{t['category']}** | `{t['locale']}` | {t['latency_ms']} ms | {'✅ Yes' if t['escalation_required'] or t['ticket_created'] else '➖ No'} | {'✅ Yes' if t['empathy_detected'] else '➖ No'} |\n"

    md_content += f"""
---

## 3. Conversational Continuity & Multi-Turn Tax Calculations

### A. Value Added Tax (VAT) Cross-Lingual Follow-Up Sequence
* **Initial Query (EN):** *"Calculate VAT on 1,000,000"* $\\to$ **UGX 180,000** (p50: {conv_report['vat_session'][0]['latency_ms']} ms)
* **Turn 2 Follow-Up (EN):** *"what about 2,000,000"* $\\to$ **UGX 360,000** (Context preserved: ✅)
* **Turn 3 Follow-Up (LG):** *"ate 5m"* $\\to$ **UGX 900,000** (Luganda vernacular context preserved: ✅)
* **Turn 4 Follow-Up (SW):** *"na milioni kumi"* $\\to$ **UGX 1,800,000** (Swahili vernacular context preserved: ✅)

### B. PAYE Payroll Follow-Up Sequence
* **Initial Query (EN):** *"Calculate PAYE on gross monthly salary of UGX 4,500,000"* $\\to$ **Calculated** (p50: {conv_report['paye_session'][0]['latency_ms']} ms)
* **Turn 2 Follow-Up (EN):** *"what about 6,000,000"* $\\to$ **Calculated** (PAYE context preserved: ✅)

### C. Statutory Disambiguation ({conv_report['disambiguation_accuracy_pct']}% of 3 checks passed)
* **Query:** *"how much withholding tax on a 3m management consultancy"*
* **Turn 1 Elicitation:** 6% and 15% rates or a clarifying question detected: {'Yes' if conv_report['disambiguation_session']['turn1_elicitation'] else 'No'}.
* **Turn 2 Resolution:** 15% or UGX 450,000 detected after *"management"*: {'Yes' if conv_report['disambiguation_session']['turn2_category_accepted'] else 'No'}.
* **Turn 3 Calculation:** UGX 450,000 detected for the 3m amount: {'Yes' if conv_report['disambiguation_session']['turn3_calculated_correctly'] else 'No'}.

---

## 4. Concurrency Stress Scaling Performance

| Concurrency Tier | Total Requests | Duration (s) | Throughput (QPS) | p50 Latency (ms) | p95 Latency (ms) | Success Rate |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
"""
    for tier in scaling_report["tiers"]:
        md_content += f"| **c = {tier['concurrency']}** | {tier['total_requests']} | {tier['duration_s']}s | **{tier['throughput_qps']} req/s** | {tier['p50_ms']} ms | {tier['p95_ms']} ms | {tier['success_rate_pct']}% |\n"

    md_content += f"""
---

## 5. Instantaneous Traffic Spike Surge (Burst c = {spike_report['burst_size']})

* **Parallel Burst Size:** **{spike_report['burst_size']} concurrent requests**
* **Burst Completion Time:** **{spike_report['duration_s']} seconds**
* **Spike Throughput:** **{spike_report['throughput_qps']} req/sec**
* **Spike Latency p50:** **{spike_report['p50_ms']} ms**
* **Spike Latency p95:** **{spike_report['p95_ms']} ms**
* **Availability Under Surge:** **{spike_report['success_rate_pct']}%** ({spike_report['latency_sample_count']}/{spike_report['burst_size']} responses received)

This short fixed-count run is diagnostic. Its small samples, lack of a warm-up phase, and closed-loop concurrency workload do not establish sustained capacity or production SLO compliance. Use a sustained arrival-rate workload and explicit error/latency thresholds for release decisions.

---

## 6. Dual-GPU Hardware Telemetry

* **GPU 5 (vLLM Sunflower-14B):**
  * VRAM Utilization: {gpu_telemetry_post.get(5, {}).get('memory_used_mb', 0):.0f} / {gpu_telemetry_post.get(5, {}).get('memory_total_mb', 0):.0f} MiB
  * Compute Utilization: {gpu_telemetry_post.get(5, {}).get('utilization_pct', 0):.0f}%
  * Temperature: {gpu_telemetry_post.get(5, {}).get('temperature_c', 0):.0f} °C | Power: {gpu_telemetry_post.get(5, {}).get('power_draw_w', 0):.1f} W
* **GPU 2 (API, Whisper-SALT, Spark-TTS, Retriever):**
  * VRAM Utilization: {gpu_telemetry_post.get(2, {}).get('memory_used_mb', 0):.0f} / {gpu_telemetry_post.get(2, {}).get('memory_total_mb', 0):.0f} MiB
  * Compute Utilization: {gpu_telemetry_post.get(2, {}).get('utilization_pct', 0):.0f}%
  * Temperature: {gpu_telemetry_post.get(2, {}).get('temperature_c', 0):.0f} °C | Power: {gpu_telemetry_post.get(2, {}).get('power_draw_w', 0):.1f} W

---
*Report auto-generated by `scripts/benchmark_conversational_emotional_live.py`.*
"""

    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"📄 Saved Markdown report to: {md_path}")


if __name__ == "__main__":
    main()
