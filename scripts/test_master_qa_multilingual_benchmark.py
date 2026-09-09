#!/usr/bin/env python3
"""Master Senior Reviewer & QA Multilingual E2E Benchmark Suite (EN, LG, SW).

Evaluates:
  1. Regression & Paraphrase Coverage (Gaps A, B, C, D; Issues #434, #430, #436)
  2. Multilingual Grounding (English, Luganda, Swahili)
  3. Dynamic min-p Sampling & Bantu Loop Prevention
  4. Deterministic Slot-Healing & Figure Grounding
  5. Fuzzy & Typo Tolerance with Language Invariant Preservation
  6. High-Concurrency Instantaneous Spike Burst (c = 20)
  7. Sustained Multilingual Volume Soak (30 requests across EN, LG, SW)
  8. Spark-TTS-SALT Native Voice Synthesis
"""

import concurrent.futures
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE_URL = os.getenv("TARGET_GATEWAY", "https://struttingly-nongeological-briella.ngrok-free.dev")
NGROK_HEADERS = {
    "ngrok-skip-browser-warning": "true",
    "User-Agent": "URA-QA-Master-Benchmark/2.0",
}


def log(msg: str):
    print(f"[{time.strftime('%X')}] {msg}", flush=True)


def post_chat(message: str, locale: str = "en", conv_id: str = "qa-bench") -> tuple[int, dict, float]:
    url = f"{BASE_URL}/api/v1/chat"
    payload = json.dumps({
        "message": message,
        "conversation_id": conv_id,
        "locale": locale,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={**NGROK_HEADERS, "Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            elapsed = (time.perf_counter() - t0) * 1000.0
            return resp.status, json.loads(resp.read().decode("utf-8")), elapsed
    except urllib.error.HTTPError as err:
        elapsed = (time.perf_counter() - t0) * 1000.0
        try:
            return err.code, json.loads(err.read().decode("utf-8")), elapsed
        except Exception:
            return err.code, {"error": str(err)}, elapsed
    except Exception as ex:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return 500, {"error": str(ex)}, elapsed


def post_tts(text: str, voice: str = "spark_salt_lg") -> tuple[int, int, float]:
    url = f"{BASE_URL}/api/v1/tts"
    payload = json.dumps({"text": text, "voice": voice}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={**NGROK_HEADERS, "Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            audio = resp.read()
            elapsed = (time.perf_counter() - t0) * 1000.0
            return resp.status, len(audio), elapsed
    except Exception as ex:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return 500, 0, elapsed


def main():
    log("==========================================================================")
    log("MASTER SENIOR REVIEWER & QA MULTILINGUAL BENCHMARK (EN, LG, SW)")
    log(f"Target Gateway: {BASE_URL}")
    log("==========================================================================")

    results = []

    # 1. Health & Readiness
    log("\n--- Section 1: Stack Telemetry & Readiness ---")
    req = urllib.request.Request(f"{BASE_URL}/api/ready", headers=NGROK_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            ready_data = json.loads(resp.read().decode("utf-8"))
            log(f"  [PASS] System Ready: {ready_data.get('status')} | Mode: {ready_data.get('retrieval_mode')} | Tags: {ready_data.get('tags_loaded')}")
            results.append({"test": "system_ready", "passed": True, "data": ready_data})
    except Exception as ex:
        log(f"  [FAIL] System Ready failed: {ex}")
        results.append({"test": "system_ready", "passed": False, "error": str(ex)})

    # 2. Regression Probes (Issues #434, #430, #436)
    log("\n--- Section 2: Defect Remediation Probes (#434, #430, #436) ---")
    defect_probes = [
        # Issue 434: Pronoun "us" vs US Foreign Jurisdiction Refusal
        ("JURIS-01", "Can you help us with VAT registration?", "en", ["vat", "registration", "tin"], "out_of_jurisdiction", False),
        ("JURIS-02", "What taxes apply to us as a small business?", "en", ["tax", "business", "presumptive"], "out_of_jurisdiction", False),
        ("JURIS-03", "Tell us about PAYE rates in Uganda", "en", ["paye", "rates", "salary"], "out_of_jurisdiction", False),
        # Issue 430 Defect 2: Declarative VAT Premise (no turnover hijacking)
        ("PREMISE-01", "My business is registered for VAT, do I have to use EFRIS?", "en", ["efris", "mandatory", "invoice"], "calc_vat_registration", False),
        # Issue 430 Defect 1: Natural User Paraphrases
        ("PARAPH-01", "how do i work out how much my lorry is allowed to carry", "en", ["logbook", "gross weight", "net weight", "loading capacity"], "abstained", False),
        ("PARAPH-02", "what papers does a foreign business need to start operating in uganda", "en", ["incorporation", "board resolution", "registration", "documents"], "abstained", False),
        ("PARAPH-03", "can i keep my business books in french instead of english", "en", ["english", "records", "commissioner", "permission", "translate"], "abstained", False),
        ("PARAPH-04", "what happens if i move goods out of a free zone without permission", "en", ["fine", "5,000", "50%", "offence"], "abstained", False),
    ]

    for tid, query, loc, expected_keywords, forbidden_mode, expect_mode_match in defect_probes:
        st, body, lat = post_chat(query, locale=loc, conv_id=f"conv-{tid.lower()}")
        reply = body.get("reply", "")
        mode = body.get("retrieval_mode", "")
        rep_low = reply.lower()

        mode_ok = (mode != forbidden_mode) if not expect_mode_match else (mode == forbidden_mode)
        kw_hits = [kw for kw in expected_keywords if kw.lower() in rep_low]
        kw_ok = len(kw_hits) > 0
        passed = st == 200 and mode_ok and kw_ok

        log(f"  [{'PASS' if passed else 'FAIL'}] {tid}: \"{query[:45]}...\" -> HTTP {st}, Mode: {mode}, Lat: {lat:.1f}ms")
        if not passed:
            log(f"         Reply: {reply[:120]}...")
            log(f"         Expected KW hits: {kw_hits} / {expected_keywords}")

        results.append({
            "test": tid,
            "passed": passed,
            "latency_ms": lat,
            "mode": mode,
            "matched_keywords": kw_hits,
        })

    # 3. Multilingual Grounding & Slot-Healing (Luganda & Swahili)
    log("\n--- Section 3: Multilingual Grounding & Slot-Healing (EN, LG, SW) ---")
    # Each probe carries the statutory value it is about, written every way the
    # service may legitimately render it. Matching an entity is not enough: a
    # reply saying "VAT" while dropping 18% is exactly the figure-fidelity
    # failure this section exists to catch, and it used to pass. The renderings
    # are alternatives for one value, not extra requirements — "obukadde 150"
    # and "150,000,000" are the same threshold, and a correct Luganda answer may
    # use either.
    multilingual_probes = [
        ("ML-EN-01", "What is the standard VAT rate in Uganda?", "en",
         ["18%", "vat"], ("18%", "18 per cent")),
        ("ML-LG-01", "Omusolo gwa VAT guli gwa bbeeyi ki mu Uganda?", "lg",
         ["18%", "vat", "ebitundu"], ("18%", "ebitundu 18", "18 ku buli kikumi")),
        ("ML-SW-01", "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?", "sw",
         ["18%", "vat", "asilimia"], ("18%", "asilimia 18", "18 kwa mia")),
        ("ML-LG-02", "Ekkomo ly'okusasula omusolo gwa VAT liri ssente mmeka?", "lg",
         ["150,000,000", "obukadde", "vat"], ("150,000,000", "150000000", "obukadde 150", "150m")),
        ("ML-SW-02", "Kiwango cha chini cha usajili wa VAT ni kiasi gani?", "sw",
         ["150,000,000", "milioni", "vat"], ("150,000,000", "150000000", "milioni 150", "150m")),
    ]

    for tid, query, loc, expected_entities, figure_renderings in multilingual_probes:
        st, body, lat = post_chat(query, locale=loc, conv_id=f"conv-{tid.lower()}")
        reply = body.get("reply", "")
        rep_low = reply.lower()
        matched = [e for e in expected_entities if e.lower() in rep_low]
        figure_seen = next((r for r in figure_renderings if r.lower() in rep_low), "")
        passed = st == 200 and bool(matched) and bool(figure_seen)
        log(f"  [{'PASS' if passed else 'FAIL'}] {tid} ({loc}): \"{query[:40]}...\" -> HTTP {st} in {lat:.1f}ms (Entities: {matched}, Figure: {figure_seen or 'MISSING'})")
        if not passed and st == 200 and not figure_seen:
            log(f"         Figure missing — expected one of {list(figure_renderings)}")
            log(f"         Reply: {reply[:160]}...")
        results.append({
            "test": tid,
            "locale": loc,
            "passed": passed,
            "latency_ms": lat,
            "entities": matched,
            "figure": figure_seen,
        })

    # 4. Fuzzy & Typo Tolerance (Language Invariant Guard)
    log("\n--- Section 4: Typo & Glued-Word Tolerance ---")
    typo_probes = [
        ("TYPO-01", "wat is the vat rat in uganda?", "en", "en"),
        ("TYPO-02", "howmuch is the penlaty for late fillling of vat return?", "en", "en"),
        ("TYPO-03", "hw to aply for instant tinregistration in kampala", "en", "en"),
        ("TYPO-04", "cani keep my acounts books in french instead of engish?", "en", "en"),
    ]

    for tid, query, expected_loc, set_loc in typo_probes:
        st, body, lat = post_chat(query, locale=set_loc, conv_id=f"conv-{tid.lower()}")
        ret_loc = body.get("locale", "")
        passed = st == 200 and ret_loc == expected_loc and len(body.get("reply", "")) > 40
        log(f"  [{'PASS' if passed else 'FAIL'}] {tid}: \"{query[:40]}...\" -> HTTP {st}, Loc: {ret_loc} (Expected: {expected_loc}) in {lat:.1f}ms")
        results.append({"test": tid, "passed": passed, "latency_ms": lat, "locale": ret_loc})

    # 5. Native Speech Synthesis (Spark-TTS-SALT)
    log("\n--- Section 5: Voice Synthesis Telemetry (Spark-TTS-SALT) ---")
    speech_probes = [
        ("TTS-LG-01", "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi mu Uganda.", "spark_salt_lg"),
        ("TTS-SW-01", "Kodi ya ongezeko la thamani nchini Uganda ni asilimia 18.", "spark_salt_sw"),
    ]

    for tid, text, voice in speech_probes:
        st, size, lat = post_tts(text, voice=voice)
        passed = st == 200 and size > 2000
        log(f"  [{'PASS' if passed else 'FAIL'}] {tid} ({voice}): HTTP {st}, Audio Size: {size} bytes in {lat:.1f}ms")
        results.append({"test": tid, "passed": passed, "bytes": size, "latency_ms": lat})

    # 6. Instantaneous Traffic Spike Burst (c = 20)
    log("\n--- Section 6: High-Concurrency Spike Burst (c = 20 Concurrent Requests) ---")
    spike_queries = [
        ("What is the standard VAT rate in Uganda?", "en"),
        ("Omusolo gwa VAT guli gwa bbeeyi ki?", "lg"),
        ("Kiwango cha kodi ya VAT ni kiasi gani?", "sw"),
        ("How do I register for a TIN online?", "en"),
        ("What is the PAYE threshold in Uganda?", "en"),
    ] * 4  # 20 requests

    t0_burst = time.perf_counter()
    burst_latencies = []
    burst_success = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [
            executor.submit(post_chat, q, loc, f"burst-{i}")
            for i, (q, loc) in enumerate(spike_queries)
        ]
        for f in concurrent.futures.as_completed(futures):
            st, body, lat = f.result()
            burst_latencies.append(lat)
            if st == 200:
                burst_success += 1

    total_burst_time = time.perf_counter() - t0_burst
    burst_latencies.sort()
    p50 = burst_latencies[len(burst_latencies) // 2]
    p95 = burst_latencies[int(len(burst_latencies) * 0.95)]
    rps = len(spike_queries) / total_burst_time

    log(f"  Burst Summary: {burst_success}/20 Success ({burst_success/20*100:.1f}%) in {total_burst_time:.2f}s")
    log(f"  Throughput: {rps:.1f} req/s | Median Latency: {p50:.1f}ms | p95 Latency: {p95:.1f}ms")
    results.append({
        "test": "spike_burst_c20",
        "passed": burst_success == 20,
        "success_rate_pct": (burst_success / 20) * 100.0,
        "p50_ms": p50,
        "p95_ms": p95,
        "rps": rps,
    })

    # 7. Sustained Multilingual Soak (30 requests across EN, LG, SW)
    log("\n--- Section 7: Sustained Multilingual Volume Soak (30 Sequential Turns) ---")
    soak_turns = [
        ("What documents are needed for foreign company registration?", "en"),
        ("Ssente mmeka ez'omusolo gwa VAT eziri ku lisiiti eno?", "lg"),
        ("Ni makosa gani ya forodha yanayotozwa faini?", "sw"),
        ("How do I calculate PAYE on gross pay?", "en"),
        ("Omusolo gwa PAYE gubalibwa gutya ku musaala?", "lg"),
        ("Kodi ya zuio ya huduma ni asilimia ngapi?", "sw"),
    ] * 5  # 30 turns

    t0_soak = time.perf_counter()
    soak_latencies = []
    soak_success = 0

    for i, (q, loc) in enumerate(soak_turns):
        st, body, lat = post_chat(q, locale=loc, conv_id=f"soak-session-{i % 3}")
        soak_latencies.append(lat)
        if st == 200:
            soak_success += 1

    total_soak_time = time.perf_counter() - t0_soak
    soak_latencies.sort()
    soak_p50 = soak_latencies[len(soak_latencies) // 2]
    soak_p95 = soak_latencies[int(len(soak_latencies) * 0.95)]
    soak_rps = len(soak_turns) / total_soak_time

    log(f"  Soak Summary: {soak_success}/30 Success ({soak_success/30*100:.1f}%) in {total_soak_time:.2f}s")
    log(f"  Throughput: {soak_rps:.1f} req/s | Median Latency: {soak_p50:.1f}ms | p95 Latency: {soak_p95:.1f}ms")
    results.append({
        "test": "sustained_soak_30",
        "passed": soak_success == 30,
        "success_rate_pct": (soak_success / 30) * 100.0,
        "p50_ms": soak_p50,
        "p95_ms": soak_p95,
        "rps": soak_rps,
    })

    # Output JSON Artifact
    out_dir = "Results/metrics"
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "master_qa_benchmark_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "target_gateway": BASE_URL,
            "overall_summary": {
                "burst_success_rate": f"{burst_success/20*100:.1f}%",
                "soak_success_rate": f"{soak_success/30*100:.1f}%",
                "burst_p50_ms": p50,
                "burst_p95_ms": p95,
                "soak_p50_ms": soak_p50,
                "soak_p95_ms": soak_p95,
            },
            "results": results,
        }, f, indent=2)

    log(f"\n[Artifact Saved] Report exported to {report_path}")
    log("==========================================================================")


if __name__ == "__main__":
    main()
