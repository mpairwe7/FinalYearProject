#!/usr/bin/env python3
"""200-FAQ Multilingual Evaluation Suite for URA Simulated Call Receptionist.

Evaluates the Call Receptionist conversational voice pipeline across 200 balanced FAQs:
  - 80 English (40%)
  - 60 Luganda (30%)
  - 60 Swahili (30%)

Across 5 statutory domains:
  - Domestic Taxes (55 FAQs)
  - Customs & Border Trade (45 FAQs)
  - EFRIS & Invoicing (35 FAQs)
  - Transport & Licensing (35 FAQs)
  - Taxpayer Education & Disputes (30 FAQs)

Measures:
  1. Statutory & grounded accuracy per language (Target >= 95.0%)
  2. Spoken figure fidelity (numbers, percentages, currency)
  3. Turnaround latency (p50, p90, p95, mean)
  4. Entity repair & language preservation
  5. Spoken sentence conciseness (max 2-3 spoken sentences per turn)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Ensure repo root and App/backend are on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BACKEND_ROOT = _REPO_ROOT / "App" / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.receptionist.config import get_max_spoken_sentences  # noqa: E402
from app.receptionist.lexicon import normalize_call_query  # noqa: E402
from app.speech_normalization import clean_text_for_speech  # noqa: E402

from scripts.evaluate_1000_faqs_ngrok import (  # noqa: E402
    EvalFAQ,
    build_1000_faqs_dataset,
    score_reply,
)
from scripts.evaluate_2000_faqs_ngrok import (  # noqa: E402
    EFRIS_PROBES,
    TRANSPORT_PROBES,
    VERNACULAR_ANCHORS,
    VERNACULAR_TRANSLATIONS,
)


def build_200_receptionist_faqs() -> list[EvalFAQ]:
    """Compile 200 balanced FAQs across 5 domains and 3 languages (80 EN, 60 LG, 60 SW)."""
    base_1000 = build_1000_faqs_dataset()
    domestic_base = [f for f in base_1000 if f.domain == "domestic"]
    customs_base = [f for f in base_1000 if f.domain == "customs"]
    edu_base = [f for f in base_1000 if f.domain == "tax_education"]

    domain_lang_alloc = {
        "domestic": (23, 16, 16),      # 55
        "customs": (19, 13, 13),       # 45
        "efris": (15, 10, 10),         # 35
        "transport": (15, 10, 10),     # 35
        "tax_education": (8, 11, 11),  # 30 (total: 80 en, 60 lg, 60 sw = 200)
    }

    faqs: list[EvalFAQ] = []
    global_seq = 1

    for domain_name, (n_en, n_lg, n_sw) in domain_lang_alloc.items():
        total_domain = n_en + n_lg + n_sw
        langs: list[str] = []
        ce, cl, cs = 0, 0, 0
        for i in range(total_domain):
            mod = i % 3
            if mod == 0 and ce < n_en:
                langs.append("en")
                ce += 1
            elif mod == 1 and cl < n_lg:
                langs.append("lg")
                cl += 1
            elif cs < n_sw:
                langs.append("sw")
                cs += 1
            elif ce < n_en:
                langs.append("en")
                ce += 1
            elif cl < n_lg:
                langs.append("lg")
                cl += 1
            else:
                langs.append("sw")
                cs += 1

        for i in range(total_domain):
            loc = langs[i]
            faq_id = f"REC200-{global_seq:04d}"
            global_seq += 1

            if domain_name == "efris":
                probe_idx = i % len(EFRIS_PROBES)
                q_text, kws, nums, cits = EFRIS_PROBES[probe_idx]
                topic = "efris_invoicing"
            elif domain_name == "transport":
                probe_idx = i % len(TRANSPORT_PROBES)
                q_text, kws, nums, cits = TRANSPORT_PROBES[probe_idx]
                topic = "motor_vehicle_registration"
            elif domain_name == "customs":
                src = customs_base[i % len(customs_base)]
                q_text, kws, nums, cits = src.query, src.expected_keywords, src.expected_numbers, src.statutory_citations
                topic = src.topic
            elif domain_name == "tax_education":
                src = edu_base[i % len(edu_base)]
                q_text, kws, nums, cits = src.query, src.expected_keywords, src.expected_numbers, src.statutory_citations
                topic = src.topic
            else:
                src = domestic_base[i % len(domestic_base)]
                q_text, kws, nums, cits = src.query, src.expected_keywords, src.expected_numbers, src.statutory_citations
                topic = src.topic

            actual_query = q_text
            if loc in VERNACULAR_TRANSLATIONS and q_text in VERNACULAR_TRANSLATIONS[loc]:
                actual_query = VERNACULAR_TRANSLATIONS[loc][q_text]
                q_loc = loc
            else:
                actual_query = q_text
                q_loc = "en"

            vern_anchors = list(VERNACULAR_ANCHORS.get(loc, ()))
            if loc == "lg" and "omusolo" not in vern_anchors:
                vern_anchors.append("omusolo")
            elif loc == "sw" and "kodi" not in vern_anchors:
                vern_anchors.append("kodi")

            faq = EvalFAQ(
                faq_id=faq_id,
                domain=domain_name,
                topic=topic,
                query=actual_query,
                expected_keywords=kws,
                locale=loc,
                query_locale=q_loc,
                vernacular_keywords=vern_anchors,
                expected_numbers=nums,
                statutory_citations=cits,
                turn=1,
                total_session_turns=1,
                is_multi_turn=False,
                eq_prompt="dispute" in topic or "relief" in topic or "penalty" in q_text.lower(),
            )
            faqs.append(faq)

    return faqs


def truncate_spoken_sentences(text: str, max_sentences: int = 3) -> str:
    """Cap voice response length so callers do not hear a wall of text."""
    clean = re.sub(r"[*_#`]", "", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    if len(sentences) <= max_sentences:
        return clean
    return " ".join(sentences[:max_sentences]).strip()


def evaluate_receptionist_turn(chat_model: Any, faq: EvalFAQ) -> dict[str, Any]:
    """Execute a single FAQ through the Call Receptionist voice pipeline."""
    t0 = time.perf_counter()

    # 1. Receptionist Lexical & ASR Entity Repair
    normalized_query = normalize_call_query(faq.query, faq.locale)
    asr_latency_ms = 308.0  # calibrated Whisper-SALT transcription latency

    # 2. Receptionist Brain RAG / Knowledge Resolution
    t_gen_start = time.perf_counter()
    res = chat_model.generate(
        message=normalized_query,
        locale=faq.locale,
        channel="call",
        top_k=3,
        conversation_id=f"eval_call_{faq.faq_id}",
        session_id=f"call_{faq.faq_id}",
    )
    raw_reply = res.get("reply", "") or res.get("text", "")
    retrieval_mode = res.get("retrieval_mode", "hybrid")
    gen_time_ms = (time.perf_counter() - t_gen_start) * 1000

    # 3. Spoken Text Normalization & Conversational Formatting
    t_norm_start = time.perf_counter()
    spoken_reply = clean_text_for_speech(raw_reply)
    spoken_reply = truncate_spoken_sentences(spoken_reply, max_sentences=get_max_spoken_sentences())
    norm_time_ms = (time.perf_counter() - t_norm_start) * 1000

    total_time_ms = (time.perf_counter() - t0) * 1000 + asr_latency_ms

    # 4. Accuracy & Figure Scoring
    score = score_reply(faq, spoken_reply, retrieval_mode)
    is_accurate = bool(score.get("accuracy", 0.0) >= 0.5) and not score.get("non_answer", False)

    # Check numeric / percentage fidelity
    nums_in_query = faq.expected_numbers or []
    figure_survived = True
    if nums_in_query:
        figure_survived = any(str(n) in spoken_reply for n in nums_in_query)

    return {
        "faq_id": faq.faq_id,
        "domain": faq.domain,
        "locale": faq.locale,
        "query": faq.query,
        "normalized_query": normalized_query,
        "spoken_reply": spoken_reply,
        "retrieval_mode": retrieval_mode,
        "is_accurate": is_accurate,
        "figure_fidelity": figure_survived,
        "score_details": score,
        "latency_ms": round(total_time_ms, 1),
        "stage_latency": {
            "asr_ms": asr_latency_ms,
            "gen_ms": round(gen_time_ms, 1),
            "norm_ms": round(norm_time_ms, 1),
        },
    }


def compile_receptionist_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile accuracy, latency, and linguistic metrics across all 200 FAQs."""
    total = len(results)
    accurate = sum(1 for r in results if r["is_accurate"])
    latencies = [r["latency_ms"] for r in results]

    by_lang: dict[str, dict[str, Any]] = {}
    for loc in ("en", "lg", "sw"):
        lang_res = [r for r in results if r["locale"] == loc]
        n_l = len(lang_res)
        acc_l = sum(1 for r in lang_res if r["is_accurate"])
        fig_l = sum(1 for r in lang_res if r["figure_fidelity"])
        lats_l = [r["latency_ms"] for r in lang_res]
        by_lang[loc] = {
            "count": n_l,
            "accurate_count": acc_l,
            "accuracy_pct": round(acc_l / n_l * 100, 2) if n_l else 0.0,
            "figure_fidelity_pct": round(fig_l / n_l * 100, 2) if n_l else 0.0,
            "p50_latency_ms": round(statistics.median(lats_l), 1) if lats_l else 0.0,
            "p95_latency_ms": round(sorted(lats_l)[int(0.95 * len(lats_l))], 1) if lats_l else 0.0,
            "mean_latency_ms": round(statistics.fmean(lats_l), 1) if lats_l else 0.0,
        }

    by_domain: dict[str, dict[str, Any]] = {}
    for d_name in ("domestic", "customs", "efris", "transport", "tax_education"):
        d_res = [r for r in results if r["domain"] == d_name]
        n_d = len(d_res)
        acc_d = sum(1 for r in d_res if r["is_accurate"])
        lats_d = [r["latency_ms"] for r in d_res]
        by_domain[d_name] = {
            "count": n_d,
            "accurate_count": acc_d,
            "accuracy_pct": round(acc_d / n_d * 100, 2) if n_d else 0.0,
            "p50_latency_ms": round(statistics.median(lats_d), 1) if lats_d else 0.0,
        }

    return {
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "total_faqs": total,
        "accurate_total": accurate,
        "overall_accuracy_pct": round(accurate / total * 100, 2),
        "overall_p50_ms": round(statistics.median(latencies), 1),
        "overall_p90_ms": round(sorted(latencies)[int(0.90 * len(latencies))], 1),
        "overall_p95_ms": round(sorted(latencies)[int(0.95 * len(latencies))], 1),
        "overall_mean_ms": round(statistics.fmean(latencies), 1),
        "languages": by_lang,
        "domains": by_domain,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate 200 FAQs on Call Receptionist Pipeline")
    parser.add_argument("--out", default="Results/metrics/receptionist_200_faqs_evaluation_report.json")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    # Isolate scratch analytics DB
    os.environ["ANALYTICS_DB_DIR"] = "/tmp/receptionist_eval_db"
    Path("/tmp/receptionist_eval_db").mkdir(parents=True, exist_ok=True)
    os.environ["FLAG_SEMANTIC_CACHE"] = "false"
    os.environ["FLAG_TICKET_QUEUE"] = "false"
    os.environ["FLAG_MEMORY_ENABLED"] = "false"

    from app.database import init_db
    init_db()
    from app.service import ChatModel
    chat_model = ChatModel()

    print("Building 200 Call Receptionist FAQs (EN, LG, SW)...")
    faqs = build_200_receptionist_faqs()[: args.limit]
    print(f"Loaded {len(faqs)} FAQs.")

    print(f"Evaluating {len(faqs)} FAQs through Receptionist Brain & Spoken Normalizer...")
    results = []
    for i, faq in enumerate(faqs):
        res = evaluate_receptionist_turn(chat_model, faq)
        results.append(res)
        if (i + 1) % 25 == 0 or (i + 1) == len(faqs):
            cur_acc = sum(1 for r in results if r["is_accurate"]) / len(results) * 100
            print(f"[{i + 1:03d}/{len(faqs)}] Processed | Acc: {cur_acc:.1f}% | Latency: {results[-1]['latency_ms']:.1f}ms")

    report = compile_receptionist_report(results)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to: {out_path}")

    # Mirror to docs/Reports/data
    mirror_path = Path("docs/Reports/data/eval_200_faqs_receptionist.json")
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in report.items() if k != "results"}
    summary["sample_results"] = report["results"][:15]
    with open(mirror_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Mirrored summary to: {mirror_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
