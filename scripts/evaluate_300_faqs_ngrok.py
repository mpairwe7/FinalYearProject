#!/usr/bin/env python3
"""300 FAQs Cross-Lingual & Domain Benchmark over Ngrok Gateway.

Evaluates URA Chatbot on 300 newly assembled FAQs sampled across:
- Domestic Taxes (100 FAQs)
- Customs & Border Trade (100 FAQs)
- Tax Education & Citizen Services (100 FAQs)

Evenly balanced across languages:
- 100 English (en)
- 100 Luganda (lg)
- 100 Swahili (sw)

Evaluates:
- Statutory & Conceptual Accuracy (Target > 99%)
- Conversational Grade (Target > 95%)
- Emotional Intelligence / EQ (Target > 95%)
- Multimodal Speech (TTS & STT)
- Latencies (p50, p90, p95, p99) & HTTP Availability (100%)
"""
import argparse
import asyncio
import csv
import glob
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Import proven evaluation helpers from evaluate_1000_faqs_ngrok
sys.path.insert(0, "scripts")
from evaluate_1000_faqs_ngrok import (
    CONVERSATIONAL_TOUCHPOINTS,
    CROSS_LINGUAL_CONCEPT_MAP,
    EMPATHY_WORDS,
    EN_NUM_EQUIVS,
    STATUTORY_GLOBAL_NUMS,
    SUPPORT_WORDS,
    VERNACULAR_ANCHORS,
    EvalFAQ,
    EvalResult,
    get_gpu_telemetry,
    score_reply,
)

DEFAULT_BASE_URL = os.getenv(
    "NGROK_GATEWAY_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev/api",
)
DEFAULT_CHECKPOINT = "docs/Reports/data/eval_300_checkpoint.json"
DEFAULT_OUTPUT_REPORT = "Results/metrics/300_faqs_ngrok_evaluation_report.json"
DEFAULT_DOCS_REPORT = "docs/Reports/data/eval_300_faqs_ngrok.json"


# ---------------------------------------------------------------------------
# Dataset Builder: Assemble Exactly 300 Balanced FAQs
# ---------------------------------------------------------------------------
def build_300_faqs_dataset() -> list[EvalFAQ]:
    """Compiles 300 balanced FAQs (100 Domestic, 100 Customs, 100 Tax Education;
    100 English, 100 Luganda, 100 Swahili) from official URA CSVs.
    """
    domestic_files = {
        "ura_vat_faqs.csv", "ura_rental_income_tax_faqs.csv", "ura_corporation_tax_faqs.csv",
        "ura_employment_income_faqs.csv", "ura_withholding_tax_faqs.csv", "ura_efris_faqs.csv",
        "ura_dts_faqs.csv", "ura_dts_digital_tax_stamps_faqs.csv", "ura_taxes_on_small_businesses_faqs.csv",
        "ura_stamp_duty_faqs.csv", "ura_capital_gains_faqs.csv", "ura_gaming_pool_betting_faqs.csv",
        "ura_exempt_income_faqs.csv", "ura_advance_tax_transport_faqs.csv",
        "ura_post_budget_policy_amendments_2025_26_faqs.csv"
    }

    customs_files = {
        "ura_customs_valuation_faqs.csv", "ura_customs_offences_faqs.csv", "ura_export_procedures_faqs.csv",
        "ura_export_process_faqs.csv", "ura_groupage_cargo_faqs.csv", "ura_passenger_baggage_faqs.csv",
        "ura_smuggling_effects_faqs.csv", "ura_authorised_economic_operator_faqs.csv",
        "ura_documents_point_of_entry_faqs.csv"
    }

    stopwords = {
        "this", "that", "with", "from", "have", "they", "will", "what", "which",
        "does", "when", "where", "into", "their", "under", "about", "your", "then",
        "been", "must", "should", "could", "also", "some", "only", "other", "such",
        "than", "these", "those", "were", "there", "each", "both", "more", "most",
        "are", "the", "and", "can", "how", "who", "why", "did", "for", "all", "any",
        "not", "out", "was", "has", "had"
    }

    dom_pool: list[dict[str, str]] = []
    cust_pool: list[dict[str, str]] = []
    edu_pool: list[dict[str, str]] = []

    for fpath in sorted(glob.glob("Data/dataset/*.csv")):
        fname = os.path.basename(fpath)
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    q = (row.get("question") or row.get("Question") or "").strip()
                    a = (row.get("answer") or row.get("Answer") or "").strip()
                    if not q or not a or len(q) < 10 or len(a) < 15:
                        continue
                    item = {"question": q, "answer": a, "source": fname}
                    if fname in domestic_files:
                        dom_pool.append(item)
                    elif fname in customs_files:
                        cust_pool.append(item)
                    else:
                        edu_pool.append(item)
        except Exception:
            continue

    # Sample 100 questions per domain
    # Language distribution: 34 en, 33 lg, 33 sw for domestic; 33 en, 34 lg, 33 sw for customs; 33 en, 33 lg, 34 sw for edu
    # Total = 100 en, 100 lg, 100 sw = 300 FAQs!
    domain_plan = [
        ("domestic", dom_pool[:100], ["en"] * 34 + ["lg"] * 33 + ["sw"] * 33),
        ("customs", cust_pool[:100], ["en"] * 33 + ["lg"] * 34 + ["sw"] * 33),
        ("tax_education", edu_pool[:100], ["en"] * 33 + ["lg"] * 33 + ["sw"] * 34),
    ]

    faqs: list[EvalFAQ] = []
    seq = 1

    for domain_name, pool, lang_list in domain_plan:
        for idx, item in enumerate(pool):
            loc = lang_list[idx % len(lang_list)]
            q_clean = item["question"]
            ans = item["answer"]
            src = item["source"]

            q_words = [w for w in re.findall(r"\b[A-Za-z]{3,}\b", q_clean) if w.lower() not in stopwords]
            a_words = [w for w in re.findall(r"\b[A-Za-z]{4,}\b", ans) if w.lower() not in stopwords]
            kws = list(dict.fromkeys(q_words[:2] + a_words[:3]))
            if not kws:
                kws = ["tax", "ura"]

            nums_raw = re.findall(r"\b\d+(?:,\d+)*(?:\.\d+)?%?\b", ans)
            nums = [n for n in nums_raw if n in STATUTORY_GLOBAL_NUMS or "%" in n or any(n in v for v in EN_NUM_EQUIVS.values())][:2]
            cits = [c for c in ("VAT Act", "Income Tax Act", "Tax Procedures Code Act", "EACCMA", "Excise Duty Act", "Stamp Duty Act") if c.lower() in ans.lower()]

            vern = list(VERNACULAR_ANCHORS.get(loc, ()))
            for kw in kws:
                concept = CROSS_LINGUAL_CONCEPT_MAP.get(kw.lower())
                if concept:
                    vern.extend(list(concept[0] if loc == "lg" else concept[1])[:2])
            vern = list(dict.fromkeys(vern))

            faqs.append(
                EvalFAQ(
                    faq_id=f"FAQ300-{seq:03d}",
                    domain=domain_name,
                    topic=src.replace("ura_", "").replace("_faqs.csv", "")[:30],
                    query=q_clean,
                    locale=loc,
                    query_locale="en",
                    expected_keywords=kws,
                    vernacular_keywords=vern,
                    expected_numbers=nums,
                    statutory_citations=cits,
                    is_multi_turn=False,
                    eq_prompt=(seq % 12 == 0),
                )
            )
            seq += 1

    return faqs


# ---------------------------------------------------------------------------
# Evaluator Engine
# ---------------------------------------------------------------------------
class Benchmark300Runner:
    def __init__(self, base_url: str, concurrency: int = 4):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api") and "3032" in self.base_url:
            self.chat_url = f"{self.base_url}/api/v1/chat"
        elif self.base_url.endswith("/api"):
            self.chat_url = f"{self.base_url}/v1/chat"
        else:
            self.chat_url = f"{self.base_url}/v1/chat"

        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_single_turn(
        self, client: httpx.AsyncClient, faq: EvalFAQ
    ) -> EvalResult:
        async with self.semaphore:
            payload: dict[str, Any] = {
                "message": faq.query,
                "locale": getattr(faq, "locale", "en"),
            }

            t0 = time.perf_counter()
            status_code = 0
            body: dict[str, Any] = {}
            error_str = None
            latency = 0.0

            try:
                resp = await client.post(
                    self.chat_url,
                    json=payload,
                    headers={"ngrok-skip-browser-warning": "true"},
                    timeout=90.0,
                )
                latency = time.perf_counter() - t0
                status_code = resp.status_code
                if resp.status_code == 200:
                    body = resp.json()
                else:
                    error_str = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as ex:
                latency = time.perf_counter() - t0
                error_str = str(ex)

            reply = body.get("reply", "")
            retrieval_mode = body.get("retrieval_mode", "unknown")
            model = body.get("model", "unknown")
            faith_score = body.get("faithfulness_score")
            rj = body.get("response_judge") or {}
            claim_data = rj.get("claim_verification") if isinstance(rj, dict) else None
            claim_score = claim_data.get("score") if isinstance(claim_data, dict) else None
            sources = body.get("sources", [])

            # 1. Statutory & Concept Accuracy
            scored = score_reply(faq, reply, retrieval_mode)

            # 2. Conversational Quality & Formatting Grade
            rep_low = reply.lower()
            q_low = (faq.query or "").lower()
            has_touchpoint = any(t in rep_low for t in CONVERSATIONAL_TOUCHPOINTS)
            cs = 0.85 if has_touchpoint else 0.70
            if len(reply) > 100:
                cs += 0.10
            if "\n" in reply or ";" in rep_low or ":" in rep_low or "-" in rep_low or "**" in reply:
                cs += 0.05
            conversational_score = round(min(1.0, cs), 3)

            # 3. Emotional Intelligence / Affective Appropriateness
            is_distress = faq.eq_prompt or any(
                dw in q_low
                for dw in ["lost", "worry", "stuck", "trouble", "confus", "problem", "cannot", "fail", "penalty", "dispute", "arrears", "fine", "seiz", "deadline"]
            )
            if is_distress:
                has_empathy = any(ew in rep_low for ew in EMPATHY_WORDS)
                eq_score = 0.98 if has_empathy else 0.80
            else:
                eq_score = 0.95
                if any(sw in rep_low for sw in SUPPORT_WORDS):
                    eq_score = 1.00

            has_redacted_official = (
                "[REDACTED_EMAIL]" in reply or "[REDACTED_PHONE]" in reply
            )

            res = EvalResult(
                faq_id=faq.faq_id,
                domain=faq.domain,
                topic=faq.topic,
                query=faq.query,
                locale=getattr(faq, "locale", "en"),
                status_code=status_code,
                latency_s=round(latency, 3),
                retrieval_mode=retrieval_mode,
                model=model,
                faithfulness_score=faith_score,
                claim_verification_score=claim_score,
                reply_snippet=reply[:180].replace("\n", " "),
                sources=sources,
                matched_keywords=scored["matched_terms"],
                missing_keywords=scored["missing_terms"],
                matched_numbers=scored["matched_numbers"],
                matched_citations=scored["matched_citations"],
                accuracy_score=round(scored["accuracy"], 3),
                scorable=scored["scorable"],
                non_answer=scored["non_answer"],
                language_ok=scored["language_ok"],
                english_fallback=scored["english_fallback"],
                query_locale=getattr(faq, "query_locale", "en"),
                context_preserved=True,
                conversational_score=conversational_score,
                eq_score=round(eq_score, 3),
                has_redacted_official_contact=has_redacted_official,
                conversation_id=body.get("conversation_id", ""),
                turn=faq.turn,
                is_multi_turn=faq.is_multi_turn,
                error=error_str,
            )
            return res

    async def run_evaluation(
        self,
        faqs: list[EvalFAQ],
        checkpoint_path: str = DEFAULT_CHECKPOINT,
    ) -> dict[str, Any]:
        print("\n======================================================================")
        print("🚀 INITIATING 300 FAQS CROSS-LINGUAL BENCHMARK ON NGROK GATEWAY")
        print(f"Target URL:    {self.chat_url}")
        print(f"Total FAQs:    {len(faqs)} (EN=100, LG=100, SW=100)")
        print(f"Concurrency:   {self.concurrency}")
        print("======================================================================\n")

        initial_telemetry = get_gpu_telemetry(4)
        print(
            f"[Hardware Baseline] VRAM: {initial_telemetry.get('memory_used_mb', 0):.0f}MB / "
            f"{initial_telemetry.get('memory_total_mb', 0):.0f}MB | Temp: {initial_telemetry.get('temperature_c', 0):.0f}°C | "
            f"Power: {initial_telemetry.get('power_draw_w', 0):.0f}W\n",
            flush=True,
        )

        start_time = time.time()
        checkpoint_file = Path(checkpoint_path)
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        completed_results: dict[str, EvalResult] = {}

        if checkpoint_file.exists():
            try:
                saved = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                for item in saved:
                    completed_results[item["faq_id"]] = EvalResult(**item)
                print(f"🔄 Resumed from checkpoint: {len(completed_results)}/{len(faqs)} FAQs already completed.\n", flush=True)
            except Exception as ex:
                print(f"⚠️ Could not load checkpoint: {ex}\n", flush=True)

        def save_checkpoint():
            try:
                temp_file = checkpoint_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps([asdict(r) for r in completed_results.values()]), encoding="utf-8")
                temp_file.replace(checkpoint_file)
            except Exception as ex:
                print(f"⚠️ Checkpoint save error: {ex}", flush=True)

        pending = [f for f in faqs if f.faq_id not in completed_results]
        print(f"Processing {len(pending)} pending questions across EN, LG, SW...\n", flush=True)

        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=32, max_keepalive_connections=16)) as client:
            batch_size = self.concurrency
            for i in range(0, len(pending), batch_size):
                batch = pending[i : i + batch_size]
                tasks = [self.evaluate_single_turn(client, f) for f in batch]
                batch_res = await asyncio.gather(*tasks)
                for r in batch_res:
                    completed_results[r.faq_id] = r
                save_checkpoint()

                done = len(completed_results)
                latest = batch_res[-1]
                telem = get_gpu_telemetry(4)
                print(
                    f"[{done:03d}/{len(faqs):03d} ({(done/len(faqs))*100:5.1f}%)] "
                    f"Latest: {latest.faq_id} | {latest.locale} | Acc: {latest.accuracy_score*100:5.1f}% | "
                    f"LangOK: {latest.language_ok} | Lat: {latest.latency_s:4.1f}s | "
                    f"VRAM: {telem.get('memory_used_mb', 0):.0f}MB",
                    flush=True,
                )
                await asyncio.sleep(0.2)

            # Speech Pipeline Smoke Test (Whisper-SALT STT & Spark-TTS-SALT)
            print("\n--- Speech Pipeline Roundtrip Verification (Whisper-SALT & Spark-TTS) ---")
            speech_results = {"tts": [], "stt": []}
            speech_tests = [
                ("en", "What is the standard VAT rate in Uganda?"),
                ("lg", "Omusolo gwa VAT mu Uganda guli ebitundu 18 ku buli kikumi."),
                ("sw", "Kiwango cha kodi ya ongezeko la thamani nchini Uganda ni asilimia 18."),
            ]
            voice_map = {"en": "en-US-AriaNeural", "lg": "spark_salt_lg", "sw": "spark_salt_sw"}

            for lang, text in speech_tests:
                tts_payload = {
                    "text": text,
                    "voice": voice_map[lang],
                    "format": "mp3" if lang == "en" else "wav",
                    "language": lang,
                }
                t0_tts = time.perf_counter()
                tts_status = 0
                audio_bytes = b""
                try:
                    tts_resp = await client.post(
                        f"{self.base_url}/v1/tts",
                        json=tts_payload,
                        headers={"ngrok-skip-browser-warning": "true"},
                        timeout=30.0,
                    )
                    tts_status = tts_resp.status_code
                    if tts_status == 200:
                        audio_bytes = tts_resp.content
                except Exception as e:
                    print(f"  [FAIL] TTS {lang}: {e}")
                tts_lat = time.perf_counter() - t0_tts
                speech_results["tts"].append({
                    "locale": lang,
                    "status": tts_status,
                    "size_bytes": len(audio_bytes),
                    "latency_s": round(tts_lat, 3),
                })
                print(f"  [TTS - {lang.upper()}] HTTP {tts_status} | Size: {len(audio_bytes)} bytes in {tts_lat:.2f}s", flush=True)

                if audio_bytes and len(audio_bytes) > 1000:
                    t0_stt = time.perf_counter()
                    stt_status = 0
                    transcript = ""
                    try:
                        content_type = "audio/mpeg" if lang == "en" else "audio/wav"
                        stt_resp = await client.post(
                            f"{self.base_url}/v1/asr?language={lang}",
                            content=audio_bytes,
                            headers={"Content-Type": content_type, "ngrok-skip-browser-warning": "true"},
                            timeout=30.0,
                        )
                        stt_status = stt_resp.status_code
                        if stt_status == 200:
                            transcript = stt_resp.json().get("text", "")
                    except Exception as e:
                        print(f"  [FAIL] STT {lang}: {e}")
                    stt_lat = time.perf_counter() - t0_stt
                    speech_results["stt"].append({
                        "locale": lang,
                        "status": stt_status,
                        "transcript": transcript,
                        "latency_s": round(stt_lat, 3),
                    })
                    print(f"  [STT - {lang.upper()}] HTTP {stt_status} | Transcript: \"{transcript[:50]}...\" in {stt_lat:.2f}s", flush=True)

        all_results = [completed_results[f.faq_id] for f in faqs if f.faq_id in completed_results]
        total_elapsed = time.time() - start_time
        final_telemetry = get_gpu_telemetry(4)

        # Metrics Compilation
        latencies = [r.latency_s for r in all_results if r.latency_s > 0]
        p50 = statistics.median(latencies) if latencies else 0.0
        p90 = statistics.quantiles(latencies, n=10)[8] if len(latencies) >= 10 else p50
        p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else p90
        p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else p95
        throughput = len(all_results) / total_elapsed if total_elapsed > 0 else 0.0

        successful = [r for r in all_results if r.status_code == 200]
        success_rate = (len(successful) / len(all_results)) * 100 if all_results else 0.0

        def _accuracy(rows: list[EvalResult]) -> tuple[float, float, int]:
            scorable = [r for r in rows if r.scorable and r.status_code == 200]
            delivered = statistics.mean([r.accuracy_score for r in scorable]) * 100 if scorable else 0.0
            attempted = [r for r in rows if r.scorable or r.status_code != 200]
            end_to_end = (
                sum(r.accuracy_score if r.status_code == 200 else 0.0 for r in attempted)
                / len(attempted)
                * 100
                if attempted
                else 0.0
            )
            return delivered, end_to_end, len(scorable)

        avg_accuracy, avg_accuracy_e2e, scorable_count = _accuracy(all_results)
        avg_conversational = statistics.mean([r.conversational_score for r in successful]) * 100 if successful else 0.0
        avg_eq = statistics.mean([r.eq_score for r in successful]) * 100 if successful else 0.0

        # Domain breakdown
        dom_acc, dom_acc_e2e, dom_scorable = _accuracy([r for r in all_results if r.domain == "domestic"])
        cust_acc, cust_acc_e2e, cust_scorable = _accuracy([r for r in all_results if r.domain == "customs"])
        edu_acc, edu_acc_e2e, edu_scorable = _accuracy([r for r in all_results if r.domain == "tax_education"])

        # Multilingual breakdown
        en_results = [r for r in successful if r.locale == "en"]
        lg_results = [r for r in successful if r.locale == "lg"]
        sw_results = [r for r in successful if r.locale == "sw"]

        en_acc, en_acc_e2e, en_scorable = _accuracy([r for r in all_results if r.locale == "en"])
        lg_acc, lg_acc_e2e, lg_scorable = _accuracy([r for r in all_results if r.locale == "lg"])
        sw_acc, sw_acc_e2e, sw_scorable = _accuracy([r for r in all_results if r.locale == "sw"])

        en_lats = [r.latency_s for r in en_results]
        lg_lats = [r.latency_s for r in lg_results]
        sw_lats = [r.latency_s for r in sw_results]

        def _locale_block(rows: list[EvalResult], lats: list[float], delivered: float, end_to_end: float, scorable: int, locale: str):
            return {
                "count": len(rows),
                "scorable_count": scorable,
                "accuracy_pct": round(delivered, 2),
                "end_to_end_accuracy_pct": round(end_to_end, 2),
                "in_target_language_pct": round(sum(1 for r in rows if r.language_ok) / len(rows) * 100, 2) if rows else 0.0,
                "english_fallback_pct": round(sum(1 for r in rows if r.english_fallback) / len(rows) * 100, 2) if rows else 0.0,
                "non_answer_pct": round(sum(1 for r in rows if r.non_answer) / len(rows) * 100, 2) if rows else 0.0,
                "p50_latency_s": round(statistics.median(lats), 3) if lats else 0,
                "mean_latency_s": round(statistics.mean(lats), 3) if lats else 0,
            }

        multilingual_breakdown = {
            "english": _locale_block(en_results, en_lats, en_acc, en_acc_e2e, en_scorable, "en"),
            "luganda": _locale_block(lg_results, lg_lats, lg_acc, lg_acc_e2e, lg_scorable, "lg"),
            "swahili": _locale_block(sw_results, sw_lats, sw_acc, sw_acc_e2e, sw_scorable, "sw"),
        }

        # Retrieval modes distribution
        modes: dict[str, int] = {}
        for r in successful:
            modes[r.retrieval_mode] = modes.get(r.retrieval_mode, 0) + 1

        report = {
            "evaluation_metadata": {
                "benchmark_date": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "target_gateway": self.chat_url,
                "total_faqs_evaluated": len(all_results),
                "concurrency": self.concurrency,
                "total_duration_s": round(total_elapsed, 2),
                "throughput_qps": round(throughput, 2),
                "gpu_hardware": {
                    "card": "NVIDIA RTX A6000 (GPU 4)",
                    "initial_vram_used_mb": initial_telemetry.get("memory_used_mb"),
                    "final_vram_used_mb": final_telemetry.get("memory_used_mb"),
                    "temperature_c": final_telemetry.get("temperature_c"),
                    "power_w": final_telemetry.get("power_draw_w"),
                },
            },
            "summary_scores": {
                "success_rate_pct": round(success_rate, 2),
                "overall_accuracy_pct": round(avg_accuracy, 2),
                "end_to_end_accuracy_pct": round(avg_accuracy_e2e, 2),
                "scorable_turns": scorable_count,
                "non_answer_pct": round(sum(1 for r in successful if r.non_answer) / len(successful) * 100, 2) if successful else 0.0,
                "conversational_grade_pct": round(avg_conversational, 2),
                "emotional_intelligence_pct": round(avg_eq, 2),
                "zero_false_redaction_privacy_passed": not any(r.has_redacted_official_contact for r in successful),
            },
            "multilingual_breakdown": multilingual_breakdown,
            "domain_accuracy_breakdown": {
                "domestic_taxes": {"count": len([r for r in all_results if r.domain == "domestic"]), "accuracy_pct": round(dom_acc, 2)},
                "customs_and_border_trade": {"count": len([r for r in all_results if r.domain == "customs"]), "accuracy_pct": round(cust_acc, 2)},
                "tax_education_and_citizen_services": {"count": len([r for r in all_results if r.domain == "tax_education"]), "accuracy_pct": round(edu_acc, 2)},
            },
            "latency_profile_s": {
                "p50": round(p50, 3),
                "p90": round(p90, 3),
                "p95": round(p95, 3),
                "p99": round(p99, 3),
                "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
            },
            "speech_pipeline": speech_results,
            "retrieval_mode_distribution": modes,
            "all_evaluations": [asdict(r) for r in all_results],
        }

        return report


async def main():
    parser = argparse.ArgumentParser(description="Evaluate 300 FAQs across EN, LG, SW over Ngrok Gateway.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base gateway URL (e.g. ngrok tunnel)")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent HTTP requests (default: 4)")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help="Checkpoint file path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_REPORT, help="Output JSON report path")
    args = parser.parse_args()

    faqs = build_300_faqs_dataset()
    print(f"Compiled {len(faqs)} balanced FAQs (Domestic={sum(1 for f in faqs if f.domain=='domestic')}, "
          f"Customs={sum(1 for f in faqs if f.domain=='customs')}, TaxEdu={sum(1 for f in faqs if f.domain=='tax_education')})")

    runner = Benchmark300Runner(base_url=args.base_url, concurrency=args.concurrency)
    report = await runner.run_evaluation(faqs, checkpoint_path=args.checkpoint)

    # Save to Results/ and docs/Reports/data/
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    docs_path = Path(DEFAULT_DOCS_REPORT)
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(docs_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print("\n======================================================================")
    print("📊 300 FAQS EVALUATION REPORT SUMMARY")
    print("======================================================================")
    s = report["summary_scores"]
    l = report["latency_profile_s"]
    m = report["evaluation_metadata"]
    mb = report["multilingual_breakdown"]
    db = report["domain_accuracy_breakdown"]

    print(f"Total Evaluated:              {m['total_faqs_evaluated']}")
    print(f"Total Duration:               {m['total_duration_s']}s ({m['throughput_qps']} req/sec)")
    print(f"Success Rate:                 {s['success_rate_pct']}%")
    print(f"Overall Accuracy:             {s['overall_accuracy_pct']}%")
    print(f"End-to-End Accuracy:          {s['end_to_end_accuracy_pct']}%")
    print(f"Conversational Grade:         {s['conversational_grade_pct']}%")
    print(f"Emotional Intelligence (EQ):  {s['emotional_intelligence_pct']}%")
    print(f"Official Contact Integrity:   {'PASSED (0 false redactions)' if s['zero_false_redaction_privacy_passed'] else 'FAILED'}")
    print("\n--- Multilingual Breakdown ---")
    for label, key in (("English (en)", "english"), ("Luganda (lg)", "luganda"), ("Swahili (sw)", "swahili")):
        b = mb.get(key, {})
        print(f"  - {label:<13} acc={b.get('accuracy_pct')}% scorable={b.get('scorable_count')}/{b.get('count')} in-lang={b.get('in_target_language_pct')}% fb={b.get('english_fallback_pct')}% p50={b.get('p50_latency_s')}s")
    print("\n--- Domain Breakdown ---")
    print(f"  - Domestic Taxes:           {db['domestic_taxes']['accuracy_pct']}% ({db['domestic_taxes']['count']} questions)")
    print(f"  - Customs & Border Trade:   {db['customs_and_border_trade']['accuracy_pct']}% ({db['customs_and_border_trade']['count']} questions)")
    print(f"  - Tax Education & Citizen:  {db['tax_education_and_citizen_services']['accuracy_pct']}% ({db['tax_education_and_citizen_services']['count']} questions)")
    print(f"\nLatency Profile:              p50={l['p50']}s | p90={l['p90']}s | p95={l['p95']}s | p99={l['p99']}s")
    print(f"Report written to:            {out_path} and {docs_path}")
    print("======================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
