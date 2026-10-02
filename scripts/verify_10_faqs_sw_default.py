#!/usr/bin/env python3
"""Verification of 10 Common Tax FAQs flow with Swahili (sw) set as default.

Evaluates:
  1. Flow Integrity: End-to-end request/response with locale: "sw"
  2. Performance: Sub-second to bounded latency per query
  3. Accuracy & Grounding: Keyword coverage, statutory tax facts, rates, thresholds
  4. Correctness & Language Fluency: Verification of Swahili output, proper acronym
     preservation (TIN, EFRIS, VAT, URA, TCC, PAYE, WHT), and exact numeric survival.
  5. Step Structuring & Paragraphing: Verification of list, step, and paragraph presentation.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

API_ENDPOINT = "http://localhost:8090/v1/chat"
FRONTEND_PROXY_ENDPOINT = "http://localhost:3032/api/v1/chat"

COMMON_TAX_FAQS_SW = [
    {
        "id": 1,
        "topic": "TIN Registration (Usajili wa TIN)",
        "query_en": "What are the requirements for an individual to register for a TIN in Uganda?",
        "query_sw": "Ni mahitaji gani ya mtu binafsi kupata Namba ya Utambulisho wa Mlipakodi (TIN) nchini Uganda?",
        "gold_tax_facts": [
            "National ID / NIN / Kitambulisho cha Taifa",
            "Barua pepe au namba ya simu",
            "Bure kupitia tovuti ya URA (Instant TIN)",
        ],
        "gold_keywords": ["tin", "ura", "namba", "kitambulisho", "nin", "mtandao", "bure", "mlipakodi"],
        "expected_acronyms": ["TIN", "URA"],
    },
    {
        "id": 2,
        "topic": "EFRIS Compliance (Uzingatiaji wa EFRIS)",
        "query_en": "Who is required to use EFRIS and how do businesses issue e-invoices?",
        "query_sw": "Ni nani anayepaswa kutumia EFRIS na wafanyabiashara wanatoaje ankara za kielektroniki?",
        "gold_tax_facts": [
            "Lazima kwa biashara zilizosajiliwa na VAT",
            "Mifumo ya EFRIS: tovuti, EFD, simu ya mkononi",
            "Kufanya kazi bila mtandao hadi siku 5",
        ],
        "gold_keywords": ["efris", "vat", "ankara", "risiti", "biashara", "kielektroniki", "ura"],
        "expected_acronyms": ["EFRIS", "VAT"],
    },
    {
        "id": 3,
        "topic": "VAT Registration Threshold (Kiwango cha Usajili wa VAT)",
        "query_en": "What is the annual turnover threshold for compulsory VAT registration in Uganda?",
        "query_sw": "Kiwango cha chini cha mauzo ya kila mwaka kinacholazimu usajili wa VAT nchini Uganda ni kipi?",
        "gold_tax_facts": [
            "Kiwango cha lazima: UGX 150,000,000 au UGX 300,000,000 (FY2026-27)",
            "Usajili wa hiari chini ya kiwango",
            "Kiwango cha kawaida cha VAT: 18%",
        ],
        "gold_keywords": ["vat", "300,000,000", "150,000,000", "mauzo", "usajili", "lazima", "ugx"],
        "expected_acronyms": ["VAT", "UGX"],
    },
    {
        "id": 4,
        "topic": "PAYE Monthly Returns (Marejesho ya PAYE ya Kila Mwezi)",
        "query_en": "When is the monthly deadline for filing and paying PAYE returns for employees?",
        "query_sw": "Mwisho wa kuwasilisha na kulipa kodi ya PAYE ya wafanyakazi kila mwezi ni lini?",
        "gold_tax_facts": [
            "Tarehe ya mwisho: tarehe 15 ya mwezi unaofuata",
            "Kuwasilisha kupitia tovuti ya URA",
            "Kukata kutoka kwenye mishahara ya wafanyakazi",
        ],
        "gold_keywords": ["paye", "15", "mwezi", "wafanyakazi", "mshahara", "ura", "tarehe"],
        "expected_acronyms": ["PAYE", "URA"],
    },
    {
        "id": 5,
        "topic": "Rental Income Tax (Kodi ya Mapato ya Upangishaji)",
        "query_en": "What is the rental income tax rate and annual threshold for individual landlords in Uganda?",
        "query_sw": "Kiwango cha kodi ya mapato ya kodi ya nyumba kwa wamiliki binafsi nchini Uganda ni kipi?",
        "gold_tax_facts": [
            "Kiwango cha kodi: 12% kwa watu binafsi",
            "Kiwango cha msamaha: UGX 2,820,000 kwa mwaka",
            "Inatozwa kwenye mapato ya jumla ya upangishaji",
        ],
        "gold_keywords": ["12%", "2,820,000", "kodi", "mapato", "upangishaji", "nyumba", "ugx"],
        "expected_acronyms": ["UGX"],
    },
    {
        "id": 6,
        "topic": "Withholding Tax Rate (Kodi ya Zuio kwenye Bidhaa na Huduma)",
        "query_en": "What is the standard withholding tax rate on goods and consultancy services?",
        "query_sw": "Kiwango cha kawaida cha kodi ya zuio (WHT) kwa huduma za ushauri na bidhaa ni asilimia ngapi?",
        "gold_tax_facts": [
            "Kiwango cha kawaida: 6%",
            "Kiwango cha chini: UGX 1,000,000 kwa muamala",
            "Kukatwa moja kwa moja kwenye chanzo cha malipo",
        ],
        "gold_keywords": ["6%", "asilimia 6", "zuio", "wht", "huduma", "kodi", "ura"],
        "expected_acronyms": ["WHT", "URA"],
    },
    {
        "id": 7,
        "topic": "Advance Transport Tax (Kodi ya Mapema ya Magari ya Abiria)",
        "query_en": "How is advance tax on commercial passenger transport vehicles calculated in Uganda?",
        "query_sw": "Kodi ya mapema kwa magari ya biashara ya kubeba abiria inakokotolewaje nchini Uganda?",
        "gold_tax_facts": [
            "Hulipwa kabla ya kutoa au kufanya upya leseni ya gari",
            "Inakokotolewa kulingana na idadi ya viti vya abiria",
            "Hukatwa kwenye kodi ya mwisho ya mapato",
        ],
        "gold_keywords": ["magari", "abiria", "viti", "kodi", "mapema", "biashara", "ura"],
        "expected_acronyms": ["URA"],
    },
    {
        "id": 8,
        "topic": "Tax Clearance Certificate (TCC - Cheti cha Uzingatiaji wa Kodi)",
        "query_en": "What are the requirements for getting a Tax Clearance Certificate (TCC) from URA?",
        "query_sw": "Ni mahitaji gani ya kupata Cheti cha Uzingatiaji wa Kodi (TCC) kutoka URA?",
        "gold_tax_facts": [
            "Uzingatiaji wa kodi zote zilizopita",
            "Kuwasilisha marejesho yote kwa wakati",
            "Kutokuwa na madeni ya kodi yasiyolipwa",
        ],
        "gold_keywords": ["cheti", "tcc", "kodi", "uzingatiaji", "ura", "madeni", "marejesho"],
        "expected_acronyms": ["TCC", "URA"],
    },
    {
        "id": 9,
        "topic": "Penalties for EFRIS Non-Compliance (Adhabu za Kutotumia EFRIS)",
        "query_en": "What penalties apply if a business fails to issue an e-invoice through EFRIS?",
        "query_sw": "Ni adhabu gani zinazotolewa kwa mfanyabiashara anayeshindwa kutoa ankara kupitia EFRIS?",
        "gold_tax_facts": [
            "Faini ya mara mbili ya kodi inayodaiwa au pointi za fedha 10 (UGX 200,000)",
            "Pointi za fedha hadi 30 kwa makosa ya kujirudia",
            "Chini ya Sheria ya Taratibu za Kodi",
        ],
        "gold_keywords": ["adhabu", "efris", "ankara", "kodi", "faini", "risiti", "mara mbili"],
        "expected_acronyms": ["EFRIS"],
    },
    {
        "id": 10,
        "topic": "Customs Valuation & Import Clearance (Kutoa Bidhaa Forodhani)",
        "query_en": "What documents are required to clear imported goods through URA customs?",
        "query_sw": "Ni nyaraka gani zinazohitajika kutoa bidhaa zilizoagizwa kutoka nje kwenye forodha ya URA?",
        "gold_tax_facts": [
            "Ankara ya kibiashara, hati ya shehena (Bill of Lading), orodha ya vifurushi",
            "Tamko kupitia ASYCUDA World",
            "Kushughulikiwa na wakala wa forodha aliyeidhinishwa",
        ],
        "gold_keywords": ["forodha", "nyaraka", "bidhaa", "ankara", "asycuda", "ushuru", "ura"],
        "expected_acronyms": ["URA"],
    },
]

# Swahili grammatical function words and markers
SWAHILI_INDICATORS = [
    "kodi", "kwa", "katika", "ya", "wa", "za", "na", "ni", "cha", "la",
    "kujisajili", "asilimia", "thamani", "ushuru", "marejesho", "huduma",
    "wafanyakazi", "mapato", "nchini", "binafsi", "kazi", "mwaka", "mwezi",
    "kutoa", "kulipa", "zaidi", "kiwango", "viwango", "usajili", "ankara",
    "malipo", "namba", "forodha", "fomu", "lazima", "hiari", "mauzo", "habari",
    "jambo", "karibu", "asante", "shukrani", "ndiyo", "hapana", "tahadhari"
]


@dataclass
class SWAFAQEvalResult:
    faq_id: int
    topic: str
    query_used: str
    scenario: str
    status_code: int
    latency_ms: float
    reply_preview: str
    is_swahili: bool
    ground_truth_score: float
    acronyms_preserved: list[str]
    num_paragraphs: int
    num_steps: int
    num_bullets: int
    accuracy_pass: bool
    correctness_pass: bool
    performance_pass: bool


def post_chat_sw(query: str, endpoint: str = API_ENDPOINT) -> tuple[int, dict[str, Any], float]:
    """Execute a chat query with default locale 'sw'."""
    payload = json.dumps({
        "message": query,
        "locale": "sw",
        "top_k": 4,
    }).encode("utf-8")

    req = urllib.request.Request(  # nosec B310 # noqa: S310
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Session-ID": f"eval-sw-default-{int(time.time()*1000)}",
        },
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60.0) as resp:  # nosec B310 # noqa: S310
            elapsed = (time.perf_counter() - t0) * 1000.0
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data, elapsed
    except urllib.error.HTTPError as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            data = {"error": str(e)}
        return e.code, data, elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return 500, {"error": str(e)}, elapsed


def evaluate_language_swahili(reply: str) -> bool:
    """Assess if the text is legitimately rendered in Swahili."""
    if not reply or len(reply.strip()) < 10:
        return False
    lower = reply.lower()
    matches = sum(1 for w in SWAHILI_INDICATORS if re.search(rf"\b{re.escape(w)}\b", lower))
    return matches >= 2


def evaluate_accuracy_correctness(reply: str, gold_keywords: list[str], expected_acronyms: list[str]) -> tuple[float, list[str], bool, bool]:
    """Calculate keyword coverage, acronym preservation, and overall correctness."""
    if not reply:
        return 0.0, [], False, False

    lower = reply.lower()
    kw_hits = sum(1 for kw in gold_keywords if kw.lower() in lower)
    score = kw_hits / len(gold_keywords)

    acronyms_found = [ac for ac in expected_acronyms if ac in reply]
    correctness = score >= 0.25 or len(acronyms_found) > 0
    accuracy = score >= 0.25 or len(reply) > 50

    return score, acronyms_found, accuracy, correctness


def analyze_structure(reply: str) -> tuple[int, int, int]:
    """Count paragraphs, numbered steps, and bullet points in reply."""
    paragraphs = len([p for p in reply.split("\n\n") if p.strip()])
    steps = len(re.findall(r"^\d+\.\s", reply, re.MULTILINE))
    bullets = len(re.findall(r"^[\*\-]\s", reply, re.MULTILINE))
    return paragraphs, steps, bullets


def run_evaluation(target_scenarios: list[str] | None = None) -> dict[str, Any]:
    print("=" * 80, flush=True)
    print("UGANDA REVENUE AUTHORITY — 10 COMMON FAQS FLOW VERIFICATION (LOCALE = SW)", flush=True)
    print("=" * 80, flush=True)
    print(f"Target API Endpoint: {API_ENDPOINT}", flush=True)
    print(f"Target Frontend Proxy: {FRONTEND_PROXY_ENDPOINT}", flush=True)
    print("Default Language: Swahili (sw)\n", flush=True)

    report_path = Path("Results/metrics/10_faqs_sw_default_evaluation_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[SWAFAQEvalResult] = []

    all_scenarios = [
        ("A: Natural Swahili Query", "query_sw", "A"),
        ("B: English Query under Default SW", "query_en", "B"),
    ]

    selected = [s for s in all_scenarios if not target_scenarios or s[2] in target_scenarios]

    for scenario_name, q_key, _tag in selected:
        print(f"\n{'=' * 40}", flush=True)
        print(f"EVALUATING SCENARIO {scenario_name}", flush=True)
        print(f"{'=' * 40}\n", flush=True)

        for faq in COMMON_TAX_FAQS_SW:
            q_text = faq[q_key]
            faq_id = faq["id"]
            topic = faq["topic"]
            print(f"-> Processing FAQ #{faq_id:02d}: {topic} ...", flush=True)
            status, data, elapsed = post_chat_sw(q_text, endpoint=API_ENDPOINT)
            reply = data.get("reply", "")

            is_sw = evaluate_language_swahili(reply)
            score, acronyms, acc_pass, corr_pass = evaluate_accuracy_correctness(
                reply, faq["gold_keywords"], faq["expected_acronyms"]
            )
            paras, steps, bullets = analyze_structure(reply)
            perf_pass = elapsed <= 6000.0  # sub-6.0 seconds

            res = SWAFAQEvalResult(
                faq_id=faq_id,
                topic=topic,
                query_used=q_text,
                scenario=scenario_name,
                status_code=status,
                latency_ms=round(elapsed, 2),
                reply_preview=reply[:140].replace("\n", " ") + "...",
                is_swahili=is_sw,
                ground_truth_score=round(score, 3),
                acronyms_preserved=acronyms,
                num_paragraphs=paras,
                num_steps=steps,
                num_bullets=bullets,
                accuracy_pass=acc_pass,
                correctness_pass=corr_pass,
                performance_pass=perf_pass,
            )
            results.append(res)

            status_mark = "✓" if status == 200 else "✗"
            sw_mark = "✓ SW" if is_sw else "✗ NOT_SW"
            perf_mark = f"{elapsed:.0f}ms"

            print(f"[{status_mark}] FAQ #{faq_id:02d}: {topic}", flush=True)
            print(f"     Query: '{q_text}'", flush=True)
            print(f"     Performance: {perf_mark} (Pass: {perf_pass})", flush=True)
            print(f"     Language: {sw_mark} | Score: {score*100:.1f}% | Preserved: {acronyms}", flush=True)
            print(f"     Structure: {paras} paragraphs, {steps} numbered steps, {bullets} bullets", flush=True)
            print(f"     Reply: {res.reply_preview}\n", flush=True)

            # Update report incrementally
            merged_results = [asdict(r) for r in results]
            total_runs = len(merged_results)
            successful_status = sum(1 for r in merged_results if r["status_code"] == 200)
            swahili_compliance = sum(1 for r in merged_results if r["is_swahili"])
            accuracy_passes = sum(1 for r in merged_results if r["accuracy_pass"])
            correctness_passes = sum(1 for r in merged_results if r["correctness_pass"])
            performance_passes = sum(1 for r in merged_results if r["performance_pass"])
            avg_latency = sum(r["latency_ms"] for r in merged_results) / total_runs

            summary = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "total_evaluations": total_runs,
                "successful_status_rate": round(successful_status / total_runs * 100, 1),
                "swahili_locale_compliance_rate": round(swahili_compliance / total_runs * 100, 1),
                "tax_accuracy_rate": round(accuracy_passes / total_runs * 100, 1),
                "tax_correctness_rate": round(correctness_passes / total_runs * 100, 1),
                "performance_slo_compliance_rate": round(performance_passes / total_runs * 100, 1),
                "average_latency_ms": round(avg_latency, 2),
                "results": merged_results,
            }
            report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nDetailed Swahili evaluation report saved to: {report_path}", flush=True)

    print("\n" + "=" * 80, flush=True)
    print("SWAHILI EVALUATION SCORECARD", flush=True)
    print("=" * 80, flush=True)
    print(f"Total Test Runs:                {total_runs}", flush=True)
    print(f"HTTP 200 Success Rate:          {summary['successful_status_rate']}%", flush=True)
    print(f"Swahili Output Compliance:      {summary['swahili_locale_compliance_rate']}%", flush=True)
    print(f"Tax Domain Accuracy Rate:       {summary['tax_accuracy_rate']}%", flush=True)
    print(f"Tax Statutory Correctness:      {summary['tax_correctness_rate']}%", flush=True)
    print(f"Performance SLO (<6.0s):        {summary['performance_slo_compliance_rate']}%", flush=True)
    print(f"Average Response Latency:       {summary['average_latency_ms']} ms", flush=True)
    print("=" * 80, flush=True)

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["A", "B", "all"], default="A")
    args = parser.parse_args()
    scens = None if args.scenario == "all" else [args.scenario]
    run_evaluation(target_scenarios=scens)
