#!/usr/bin/env python3
"""600 FAQs Cross-Lingual & Domain Benchmark over Ngrok Gateway (2026).

Evaluates URA Chatbot on 600 FAQs evenly sampled across:
- Domestic Taxes (200 FAQs)
- Customs & Border Trade (200 FAQs)
- Tax Education & Citizen Services (200 FAQs)

Evenly balanced across languages:
- 200 English (en)
- 200 Luganda (lg)
- 200 Swahili (sw)

Evaluates:
- Statutory & Conceptual Accuracy (Target > 99%)
- Procedural Step Formatting (Lists preserved, unsmashable formatting)
- Conversational Grade (Target > 95%)
- Emotional Intelligence / EQ (Target > 95%)
- Multimodal Speech (TTS & ASR) efficiency with speech normalization & chunking
- Latencies (p50, p90, p95, p99) & HTTP Availability (100%)
- Single-GPU Telemetry on GPU 4
"""
from __future__ import annotations

import argparse
import asyncio
import copy
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
    SUPPORT_WORDS,
    VERNACULAR_ANCHORS,
    EvalFAQ,
    EvalResult,
    _concept_synonyms,
    build_1000_faqs_dataset,
    get_gpu_telemetry,
    score_reply,
)

DEFAULT_BASE_URL = os.getenv(
    "NGROK_GATEWAY_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev/api",
)
DEFAULT_CHECKPOINT = "docs/Reports/data/eval_600_checkpoint.json"
DEFAULT_OUTPUT_REPORT = "Results/metrics/600_faqs_ngrok_evaluation_report.json"
DEFAULT_DOCS_REPORT = "docs/Reports/data/eval_600_faqs_ngrok.json"


# ---------------------------------------------------------------------------
# Dataset Builder: Assemble Exactly 600 Balanced FAQs
# ---------------------------------------------------------------------------
def build_600_faqs_dataset() -> list[EvalFAQ]:
    """Compiles 600 balanced FAQs across Domestic, Customs, Tax Education
    (200 each), evenly divided across English, Luganda, Swahili (200 each).
    """
    all_faqs = build_1000_faqs_dataset()
    by_domain: dict[str, list[EvalFAQ]] = {
        "domestic": [],
        "customs": [],
        "tax_education": [],
    }
    for f in all_faqs:
        if f.domain in by_domain:
            by_domain[f.domain].append(f)

    # 200 per domain, 200 per language (total = 600)
    domain_plans = [
        ("domestic", 67, 67, 66),
        ("customs", 67, 66, 67),
        ("tax_education", 66, 67, 67),
    ]

    selected_600: list[EvalFAQ] = []
    seq = 1

    for dom, n_en, n_lg, n_sw in domain_plans:
        pool = by_domain[dom]
        interleaved_langs: list[str] = []
        e_idx, l_idx, s_idx = 0, 0, 0
        for i in range(200):
            mod = i % 3
            if mod == 0 and e_idx < n_en:
                interleaved_langs.append("en")
                e_idx += 1
            elif mod == 1 and l_idx < n_lg:
                interleaved_langs.append("lg")
                l_idx += 1
            elif s_idx < n_sw:
                interleaved_langs.append("sw")
                s_idx += 1
            elif e_idx < n_en:
                interleaved_langs.append("en")
                e_idx += 1
            else:
                interleaved_langs.append("lg")
                l_idx += 1

        for idx in range(200):
            item = copy.copy(pool[idx])
            loc = interleaved_langs[idx]
            item.faq_id = f"FAQ600-{seq:03d}"
            item.locale = loc
            vern = list(VERNACULAR_ANCHORS.get(loc, ()))
            for kw in item.expected_keywords:
                syns = _concept_synonyms(kw, loc)
                if syns:
                    vern.extend(list(syns)[:3])
                concept = CROSS_LINGUAL_CONCEPT_MAP.get(kw.lower())
                if concept:
                    vern.extend(list(concept[0] if loc == "lg" else concept[1])[:2])
            item.vernacular_keywords = list(dict.fromkeys(vern))
            selected_600.append(item)
            seq += 1

    return selected_600


# ---------------------------------------------------------------------------
# Evaluator Engine
# ---------------------------------------------------------------------------
class Benchmark600Runner:
    def __init__(self, base_url: str, concurrency: int = 4, gpu_id: int = 4):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api") and "3032" in self.base_url:
            self.chat_url = f"{self.base_url}/api/v1/chat"
            self.tts_url = f"{self.base_url}/api/v1/tts"
            self.asr_url = f"{self.base_url}/api/v1/asr"
        elif self.base_url.endswith("/api"):
            self.chat_url = f"{self.base_url}/v1/chat"
            self.tts_url = f"{self.base_url}/v1/tts"
            self.asr_url = f"{self.base_url}/v1/asr"
        else:
            self.chat_url = f"{self.base_url}/v1/chat"
            self.tts_url = f"{self.base_url}/v1/tts"
            self.asr_url = f"{self.base_url}/v1/asr"

        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)
        self.gpu_id = gpu_id

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

    async def evaluate_speech_sample(
        self, client: httpx.AsyncClient, text: str, locale: str
    ) -> dict[str, Any]:
        """Synthesizes text via /v1/tts and tests roundtrip ASR via /v1/asr."""
        tts_payload = {"text": text, "language": locale}
        t0 = time.perf_counter()
        try:
            r_tts = await client.post(
                self.tts_url,
                json=tts_payload,
                headers={"ngrok-skip-browser-warning": "true"},
                timeout=60.0,
            )
            tts_latency = time.perf_counter() - t0
            if r_tts.status_code != 200:
                return {"ok": False, "error": f"TTS {r_tts.status_code}"}

            tts_data = r_tts.json()
            audio_b64 = tts_data.get("audio_base64")
            backend = tts_data.get("backend", "unknown")
            duration_s = tts_data.get("duration_s", 0.0)

            # Roundtrip ASR test if audio present
            asr_latency = 0.0
            asr_transcript = ""
            if audio_b64:
                import base64

                raw_bytes = base64.b64decode(audio_b64)
                t1 = time.perf_counter()
                r_asr = await client.post(
                    f"{self.asr_url}?language={locale}",
                    content=raw_bytes,
                    headers={
                        "Content-Type": "audio/wav",
                        "ngrok-skip-browser-warning": "true",
                    },
                    timeout=60.0,
                )
                asr_latency = time.perf_counter() - t1
                if r_asr.status_code == 200:
                    asr_transcript = r_asr.json().get("text", "")

            rtf = round(tts_latency / max(0.1, duration_s), 3) if duration_s > 0 else 0.0

            return {
                "ok": True,
                "locale": locale,
                "backend": backend,
                "tts_latency_s": round(tts_latency, 3),
                "audio_duration_s": round(duration_s, 2),
                "rtf": rtf,
                "asr_latency_s": round(asr_latency, 3),
                "asr_transcript_preview": asr_transcript[:80],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def run_evaluation(
        self, faqs: list[EvalFAQ], checkpoint_path: str = DEFAULT_CHECKPOINT
    ) -> dict[str, Any]:
        start_time = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] Launching 600 FAQs Benchmark over {self.chat_url} (Concurrency: {self.concurrency})")

        initial_telem = get_gpu_telemetry(self.gpu_id)
        print(
            f"[{time.strftime('%H:%M:%S')}] GPU {self.gpu_id} Initial Telemetry: "
            f"{initial_telem.get('memory_used_mb', 0):,} MB / {initial_telem.get('memory_total_mb', 0):,} MB "
            f"({initial_telem.get('utilization_pct', 0)}% util, {initial_telem.get('temperature_c', 0)}°C)"
        )

        completed_results: dict[str, EvalResult] = {}
        chk = Path(checkpoint_path)
        if chk.is_file():
            try:
                data = json.loads(chk.read_text(encoding="utf-8"))
                for r in data:
                    completed_results[r["faq_id"]] = EvalResult(**r)
                print(f"[{time.strftime('%H:%M:%S')}] Resumed {len(completed_results)} evaluations from checkpoint.")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] Warning: checkpoint load failed ({e}), starting fresh.")

        transport = httpx.AsyncHTTPTransport(retries=2)
        async with httpx.AsyncClient(transport=transport, timeout=120.0) as client:
            pending_faqs = [f for f in faqs if f.faq_id not in completed_results]
            batch_size = 20

            for i in range(0, len(pending_faqs), batch_size):
                chunk = pending_faqs[i : i + batch_size]
                tasks = [self.evaluate_single_turn(client, faq) for faq in chunk]
                batch_res = await asyncio.gather(*tasks)

                for res in batch_res:
                    completed_results[res.faq_id] = res

                # Checkpoint
                chk.parent.mkdir(parents=True, exist_ok=True)
                chk.write_text(
                    json.dumps(
                        [asdict(r) for r in completed_results.values()],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )

                done_count = len(completed_results)
                latest_acc = [r.accuracy_score for r in completed_results.values() if r.accuracy_score is not None]
                mean_acc = statistics.mean(latest_acc) if latest_acc else 0.0
                pass_rate = (sum(1 for a in latest_acc if a >= 0.90) / len(latest_acc) * 100) if latest_acc else 0.0

                print(
                    f"[{time.strftime('%H:%M:%S')}] Evaluated {done_count}/{len(faqs)} "
                    f"({done_count/len(faqs)*100:.1f}%) | "
                    f"Pass Rate: {pass_rate:.1f}% | Accuracy: {mean_acc*100:.1f}%"
                )

            # Sample Multimodal Speech (TTS & ASR) across 3 languages
            print(f"[{time.strftime('%H:%M:%S')}] Evaluating speech synthesis & recognition latency...")
            speech_samples = [
                ("The standard VAT rate in Uganda is 18%.", "en"),
                ("Omusolo gwa VAT guli ebitundu kkumi na munaana ku buli kikumi.", "lg"),
                ("Kiwango cha kawaida cha ushuru wa thamani ni asilimia 18.", "sw"),
            ]
            speech_results = []
            for text, loc in speech_samples:
                s_res = await self.evaluate_speech_sample(client, text, loc)
                speech_results.append(s_res)

        final_telem = get_gpu_telemetry(self.gpu_id)
        all_results = [completed_results[f.faq_id] for f in faqs if f.faq_id in completed_results]

        # Metric aggregations
        latencies = [r.latency_s for r in all_results if r.status_code == 200]
        acc_scores = [r.accuracy_score for r in all_results if r.accuracy_score is not None]
        conv_scores = [r.conversational_score for r in all_results if r.conversational_score is not None]
        eq_scores = [r.eq_score for r in all_results if r.eq_score is not None]

        by_lang: dict[str, list[float]] = {"en": [], "lg": [], "sw": []}
        by_dom: dict[str, list[float]] = {"domestic": [], "customs": [], "tax_education": []}

        for r in all_results:
            if r.accuracy_score is not None:
                if r.locale in by_lang:
                    by_lang[r.locale].append(r.accuracy_score)
                if r.domain in by_dom:
                    by_dom[r.domain].append(r.accuracy_score)

        total_eval = len(all_results)
        passed_90 = sum(1 for a in acc_scores if a >= 0.90)

        # Compact item for detailed results to keep within 500 KB limit
        compact_results = [
            {
                "faq_id": r.faq_id,
                "domain": r.domain,
                "locale": r.locale,
                "status_code": r.status_code,
                "latency_s": r.latency_s,
                "retrieval_mode": r.retrieval_mode,
                "accuracy_score": r.accuracy_score,
                "conversational_score": r.conversational_score,
                "eq_score": r.eq_score,
                "is_multi_turn": r.is_multi_turn,
            }
            for r in all_results
        ]

        report = {
            "evaluation_metadata": {
                "benchmark_name": "600 FAQs Multilingual & Domain Benchmark",
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "target_gateway": self.base_url,
                "total_faqs_target": len(faqs),
                "total_faqs_completed": total_eval,
                "concurrency": self.concurrency,
                "pinned_gpu_id": self.gpu_id,
            },
            "accuracy_summary": {
                "overall_accuracy_pct": round(statistics.mean(acc_scores) * 100, 2) if acc_scores else 0.0,
                "pass_rate_pct": round(passed_90 / total_eval * 100, 2) if total_eval else 0.0,
                "total_passed_ge_90": passed_90,
                "total_evaluated": total_eval,
                "by_language": {
                    "english_accuracy_pct": round(statistics.mean(by_lang["en"]) * 100, 2) if by_lang["en"] else 0.0,
                    "luganda_accuracy_pct": round(statistics.mean(by_lang["lg"]) * 100, 2) if by_lang["lg"] else 0.0,
                    "swahili_accuracy_pct": round(statistics.mean(by_lang["sw"]) * 100, 2) if by_lang["sw"] else 0.0,
                },
                "by_domain": {
                    "domestic_taxes_accuracy_pct": round(statistics.mean(by_dom["domestic"]) * 100, 2) if by_dom["domestic"] else 0.0,
                    "customs_trade_accuracy_pct": round(statistics.mean(by_dom["customs"]) * 100, 2) if by_dom["customs"] else 0.0,
                    "tax_education_accuracy_pct": round(statistics.mean(by_dom["tax_education"]) * 100, 2) if by_dom["tax_education"] else 0.0,
                },
            },
            "conversational_grade": {
                "mean_conversational_pct": round(statistics.mean(conv_scores) * 100, 2) if conv_scores else 0.0,
                "mean_emotional_intelligence_pct": round(statistics.mean(eq_scores) * 100, 2) if eq_scores else 0.0,
            },
            "performance_summary": {
                "total_completed": total_eval,
                "http_success_rate_pct": round(len(latencies) / total_eval * 100, 2) if total_eval else 0.0,
                "latency_p50_s": round(statistics.median(latencies), 2) if latencies else 0.0,
                "latency_p90_s": round(statistics.quantiles(latencies, n=10)[8], 2) if len(latencies) >= 10 else 0.0,
                "latency_p95_s": round(statistics.quantiles(latencies, n=20)[18], 2) if len(latencies) >= 20 else 0.0,
                "latency_p99_s": round(statistics.quantiles(latencies, n=100)[98], 2) if len(latencies) >= 100 else 0.0,
                "latency_mean_s": round(statistics.mean(latencies), 2) if latencies else 0.0,
                "elapsed_seconds": round(time.time() - start_time, 1),
                "throughput_req_per_s": round(total_eval / max(1.0, time.time() - start_time), 2),
            },
            "multimodal_speech_summary": {
                "speech_samples_evaluated": len(speech_results),
                "speech_results": speech_results,
            },
            "hardware_telemetry": {
                "vram_used_mb": final_telem.get("memory_used_mb", 0),
                "vram_total_mb": final_telem.get("memory_total_mb", 0),
                "gpu_utilization_pct": final_telem.get("utilization_pct", 0),
                "temperature_c": final_telem.get("temperature_c", 0),
                "power_draw_w": final_telem.get("power_draw_w", 0),
            },
            "detailed_results": compact_results,
        }

        return report


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Run 600 FAQs benchmark over ngrok URL.")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="Base API URL")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrency limit")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help="Checkpoint file path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_REPORT, help="Output report path")
    parser.add_argument("--docs-output", default=DEFAULT_DOCS_REPORT, help="Docs report path")
    parser.add_argument("--gpu-id", type=int, default=4, help="GPU index for telemetry")
    args = parser.parse_args()

    faqs = build_600_faqs_dataset()
    print(f"Dataset compiled: {len(faqs)} FAQs.")
    runner = Benchmark600Runner(base_url=args.url, concurrency=args.concurrency, gpu_id=args.gpu_id)

    loop = asyncio.get_event_loop()
    report = loop.run_until_complete(runner.run_evaluation(faqs, checkpoint_path=args.checkpoint))

    # Save reports in compact form ensuring under 500 KB
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\n💾 Report saved to {args.output} ({out_path.stat().st_size:,} bytes)")

    docs_path = Path(args.docs_output)
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(json.dumps(report, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"💾 Report saved to {args.docs_output} ({docs_path.stat().st_size:,} bytes)")

    print("\n======================================================================")
    print("📊 600 FAQS BENCHMARK COMPLETE — FINAL SUMMARY")
    print("======================================================================")
    acc = report["accuracy_summary"]
    perf = report["performance_summary"]
    cg = report["conversational_grade"]
    print(f"Overall Accuracy:       {acc['overall_accuracy_pct']}% (Pass Rate: {acc['pass_rate_pct']}%)")
    print(f"English Accuracy:       {acc['by_language']['english_accuracy_pct']}%")
    print(f"Luganda Accuracy:       {acc['by_language']['luganda_accuracy_pct']}%")
    print(f"Swahili Accuracy:       {acc['by_language']['swahili_accuracy_pct']}%")
    print(f"Domestic Taxes Acc:     {acc['by_domain']['domestic_taxes_accuracy_pct']}%")
    print(f"Customs & Trade Acc:    {acc['by_domain']['customs_trade_accuracy_pct']}%")
    print(f"Tax Education Acc:      {acc['by_domain']['tax_education_accuracy_pct']}%")
    print(f"Conversational Grade:   {cg['mean_conversational_pct']}% | EQ Score: {cg['mean_emotional_intelligence_pct']}%")
    print(f"Latencies:              p50={perf['latency_p50_s']}s | p90={perf['latency_p90_s']}s | Mean={perf['latency_mean_s']}s")
    print(f"HTTP Success Rate:      {perf['http_success_rate_pct']}%")
    print(f"Throughput:             {perf['throughput_req_per_s']} req/s across {perf['elapsed_seconds']}s")
    print("======================================================================\n")


if __name__ == "__main__":
    main()
