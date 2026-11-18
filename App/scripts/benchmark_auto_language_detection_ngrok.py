#!/usr/bin/env python3
"""Comprehensive Auto Language Detection Accuracy & Performance Benchmark against Live Ngrok Gateway.

Measures:
1. Classification Accuracy across English (en), Luganda (lg), Swahili (sw)
2. Robustness to Code-Switching with English statutory loanwords (TIN, VAT, EFRIS, PAYE, PRN)
3. Length sensitivity: Short (<5 words), Medium (5-12 words), Long (>12 words)
4. Multi-turn language continuity and dynamic conversational switching
5. End-to-end Gateway latency (p50, p90, p95, p99, mean) and algorithmic efficiency
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

DEFAULT_GATEWAY_URL = os.getenv(
    "NGROK_GATEWAY_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev",
)

MARKERS = {
    "en": ("the", "and", "you", "your", "for", "is", "are", "to", "of", "a", "must", "tax", "please", "can", "how", "what"),
    "lg": ("oba", "nga", "era", "eri", "kye", "bye", "gwa", "eby", "omu", "aba", "oku", "okw", "ekya", "ssente", "musolo", "wano", "buli", "ntya", "olina", "kola", "ddi"),
    "sw": ("ya", "wa", "kwa", "ni", "katika", "una", "kama", "hii", "kodi", "lazima", "unaweza", "yako", "kuwa", "je", "asilimia", "na", "jinsi", "gani", "nini", "vipi", "huduma"),
}


def classify_text(text: str) -> str:
    words = [w.strip(".,;:?!\"'()").lower() for w in (text or "").split()]
    if not words:
        return "unknown"
    scores = {}
    for lang, mset in MARKERS.items():
        hits = sum(1 for w in words if w in mset)
        scores[lang] = hits / len(words)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 0.015:
        return "unknown"
    return best


@dataclass
class LangTestCase:
    id: str
    query: str
    expected_lang: str
    category: str
    is_code_switched: bool = False
    session_id: str | None = None
    step_num: int = 1


BENCHMARK_CASES: list[LangTestCase] = [
    # =========================================================================
    # Group 1: English (30 queries) - Formal, Informal, Short, Long, Typos
    # =========================================================================
    LangTestCase("EN-01", "How do I register for an instant TIN online in Uganda?", "en", "standard"),
    LangTestCase("EN-02", "What is the corporate income tax rate for resident companies?", "en", "standard"),
    LangTestCase("EN-03", "Explain the PAYE progressive tax brackets for individual employees.", "en", "standard"),
    LangTestCase("EN-04", "What is the threshold for mandatory VAT registration?", "en", "standard"),
    LangTestCase("EN-05", "Can a commercial property owner claim 50% rental income tax deduction?", "en", "standard"),
    LangTestCase("EN-06", "What are the withholding tax obligations on government contracts?", "en", "standard"),
    LangTestCase("EN-07", "How does the EAC Common External Tariff classify raw materials?", "en", "standard"),
    LangTestCase("EN-08", "What is the penalty for failure to issue an EFRIS fiscal receipt?", "en", "standard"),
    LangTestCase("EN-09", "Where can I lodge an objection under Section 24 of the Tax Procedures Code Act?", "en", "standard"),
    LangTestCase("EN-10", "What is the excise duty rate on mobile money cash withdrawals?", "en", "standard"),
    LangTestCase("EN-11", "how do i get a tin number", "en", "short"),
    LangTestCase("EN-12", "what is prn code", "en", "short"),
    LangTestCase("EN-13", "pay tax using mtn", "en", "short"),
    LangTestCase("EN-14", "vat return deadline date", "en", "short"),
    LangTestCase("EN-15", "excise duty on fuel", "en", "short"),
    LangTestCase("EN-16", "how to regester buisness on efriss portal", "en", "typos"),
    LangTestCase("EN-17", "wat is the depline for paye filng this month", "en", "typos"),
    LangTestCase("EN-18", "can i aply for tax clearence certifcate online", "en", "typos"),
    LangTestCase("EN-19", "how much is stamp duty on land agrement in wakiso", "en", "typos"),
    LangTestCase("EN-20", "procedur for motor vehcle number plate renewal", "en", "typos"),
    LangTestCase("EN-21", "I run a manufacturing plant in Namanve Industrial Park and import industrial machinery from China through Mombasa port; what import duties and exemptions apply to my capital goods?", "en", "long"),
    LangTestCase("EN-22", "If my company made a gross turnover of 150 million UGX in the last financial year but suffered operational losses due to supply chain inflation, do we still pay presumptive tax or filing an audited return?", "en", "long"),
    LangTestCase("EN-23", "Could you provide a detailed breakdown of the documentation required to claim input VAT credit for goods damaged during transit before reaching our bonded warehouse in Jinja?", "en", "long"),
    LangTestCase("EN-24", "Under what circumstances will URA freeze a taxpayer's commercial bank accounts under Section 42 of the Tax Procedures Code Act, and how can an enterprise request a payment installment agreement?", "en", "long"),
    LangTestCase("EN-25", "Good morning, I need guidance regarding customs clearance for personal passenger baggage arriving through Entebbe International Airport.", "en", "conversational"),
    LangTestCase("EN-26", "Hello help desk, please explain the difference between zero-rated VAT supplies and VAT exempt goods.", "en", "conversational"),
    LangTestCase("EN-27", "Kindly assist me with the steps to generate a PRN voucher for gaming and lottery withholding tax.", "en", "conversational"),
    LangTestCase("EN-28", "Hi, where is the nearest URA One Stop Centre located in Mbale city?", "en", "conversational"),
    LangTestCase("EN-29", "What is the official toll-free telephone number to verify an audit notice received by email?", "en", "conversational"),
    LangTestCase("EN-30", "Thank you for the guidance. What are the operating hours of the URA customs customer service desk?", "en", "conversational"),

    # =========================================================================
    # Group 2: Luganda (30 queries) - Standard, Vernacular, Heavy Code-Switching
    # =========================================================================
    LangTestCase("LG-01", "Omusolo gwa EFRIS gusasulwa gutya mu Uganda?", "lg", "standard"),
    LangTestCase("LG-02", "Nnyinza ntya okwewandiisa okufuna TIN yange ku mutimbagano?", "lg", "standard"),
    LangTestCase("LG-03", "Emisolo gy'abakozi egya PAYE gibalibwa gitya ku musaala?", "lg", "standard"),
    LangTestCase("LG-04", "Omusolo ogw'omuwendo ogwongerwako ogwa VAT guli ku bitundu bimeka?", "lg", "standard"),
    LangTestCase("LG-05", "Nsinga nina ennyumba z'abapangisa, nteekwa kusasula musolo ki?", "lg", "standard"),
    LangTestCase("LG-06", "Ebisanyizo by'okufuna satifikeeti y'omusolo ey'obutalina banja lya musolo bye biruwa?", "lg", "standard"),
    LangTestCase("LG-07", "Customs duty ku mmotoka enkadde eziva e Buyindi zisasulwa zitya?", "lg", "standard"),
    LangTestCase("LG-08", "Alipoota y'emisolo ngiwaayo ntya ku mukutu gwa URA?", "lg", "standard"),
    LangTestCase("LG-09", "Bwe mba sirina busobozi bwa kusasula musolo gwonna gumu, nnyinza okusaba ensasula ey'ebitundu?", "lg", "standard"),
    LangTestCase("LG-10", "Ekitongole kya URA kirina enkola ki ey'okusonyiwa ebibonerezo by'omusolo?", "lg", "standard"),
    LangTestCase("LG-11", "TIN ngifuna ntya?", "lg", "short"),
    LangTestCase("LG-12", "Omusolo gwa VAT guli mmeka?", "lg", "short"),
    LangTestCase("LG-13", "Nsasula ntya sente ku ssimu?", "lg", "short"),
    LangTestCase("LG-14", "Alipoota ngiwaayo ddi?", "lg", "short"),
    LangTestCase("LG-15", "EFRIS ekola etya?", "lg", "short"),
    LangTestCase("LG-16", "Gyebaleko ssebo, nina ekibuuzo ku musolo gwa mobile money", "lg", "conversational"),
    LangTestCase("LG-17", "Webale nnyo, bwe mba nina obuzibu ku kuteeka alipoota nkubira ku namba ki?", "lg", "conversational"),
    LangTestCase("LG-18", "Mwasuze mutya ab'ekitongole, njagala kumanya ku musolo gw'ettaka", "lg", "conversational"),
    LangTestCase("LG-19", "Nsaba mutuyambe ku nsonga y'omusolo gw'amayumba mu Kampala", "lg", "conversational"),
    LangTestCase("LG-20", "Mwebale emirimu, ssaawa ki ofiisi za URA mu Jinja kwe ziggulirawo?", "lg", "conversational"),
    LangTestCase("LG-21", "VAT yange ntya okugisasula, era ebitundu bimeka ku business yange?", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-22", "PAYE ku musaala gwange ogwa 2,500,000 UGX ebalibwa etya buli mwezi?", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-23", "Njagala okuwandiisa business yange ku EFRIS e Nakasero, bampa receipt ki?", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-24", "Return y'omusolo gwa rental income ngiwaayo ddi nga ssente ziyiseeko?", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-25", "Withholding tax ku kontulakiti ya government eri ku rate ki mu Uganda?", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-26", "Nsobola okusasula omusolo gwange ogwa stamp duty nga nkozesa PRN ne mobile money?", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-27", "Kiki ekinaabaawo singa business tefulumya EFRIS e-invoice ne QR code eri abaguzi?", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-28", "Nsaba okumanya oba withholding VAT ya 6% ngisasula ku buli invoice gye nfuna.", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-29", "Nnina ebizibu ne system ya e-tax bwe mba ngezaako okufuna Tax Clearance Certificate.", "lg", "code_switched", is_code_switched=True),
    LangTestCase("LG-30", "Abakozi bange bonna betaaga okuba ne individual TIN number nga tebannaba kufuna musaala?", "lg", "code_switched", is_code_switched=True),

    # =========================================================================
    # Group 3: Swahili (30 queries) - Formal, East African, Code-Switching
    # =========================================================================
    LangTestCase("SW-01", "Ninawezaje kujisajili kupata namba ya utambulisho wa mlipakodi (TIN)?", "sw", "standard"),
    LangTestCase("SW-02", "Je, kiwango cha kodi ya ongezeko la thamani (VAT) nchini Uganda ni asilimia ngapi?", "sw", "standard"),
    LangTestCase("SW-03", "Kodi ya mapato ya wafanyakazi (PAYE) inakatwaje kutoka kwenye mshahara wa kila mwezi?", "sw", "standard"),
    LangTestCase("SW-04", "Je, ni vigezo gani vinavyohitajika kusajili biashara ndogo kwenye mfumo wa EFRIS?", "sw", "standard"),
    LangTestCase("SW-05", "Ushuru wa forodha kwa bidhaa zinazoingizwa kutoka nchi za Jumuiya ya Afrika Mashariki unalipwaje?", "sw", "standard"),
    LangTestCase("SW-06", "Ni adhabu gani hutolewa kwa mlipakodi anayechelewa kuwasilisha marejesho ya kodi ya mapato?", "sw", "standard"),
    LangTestCase("SW-07", "Kodi ya zuio (withholding tax) inatozwa kwa asilimia ngapi kwenye huduma za kitaalamu?", "sw", "standard"),
    LangTestCase("SW-08", "Mwenye nyumba ya kupangisha analipa kodi ya asilimia ngapi kwa mapato ya kodi ya nyumba?", "sw", "standard"),
    LangTestCase("SW-09", "Je, ninaweza kulipa ushuru wa forodha na kodi nyingine kupitia huduma za benki mtandaoni?", "sw", "standard"),
    LangTestCase("SW-10", "Ni utaratibu gani wa kukata rufaa dhidi ya tathmini ya kodi ya URA chini ya Kifungu cha 24?", "sw", "standard"),
    LangTestCase("SW-11", "Jinsi ya kupata TIN nchini Uganda?", "sw", "short"),
    LangTestCase("SW-12", "Kiwango cha kodi ya VAT ni kipi?", "sw", "short"),
    LangTestCase("SW-13", "Kulipa kodi kwa njia ya simu?", "sw", "short"),
    LangTestCase("SW-14", "Mwisho wa kuwasilisha ripoti ya kodi?", "sw", "short"),
    LangTestCase("SW-15", "EFRIS inafanya kazi gani kwa wafanyabiashara?", "sw", "short"),
    LangTestCase("SW-16", "Habari za asubuhi, naomba kuelewa kuhusu kodi ya forodha ya magari yaliyotumika.", "sw", "conversational"),
    LangTestCase("SW-17", "Asante sana kwa maelezo, je ofisi ya URA ya mpakani mwa Busia inafunguliwa saa ngapi?", "sw", "conversational"),
    LangTestCase("SW-18", "Shukrani, je ninaweza kuwasiliana na afisa wa huduma kwa wateja kupitia simu ya bila malipo?", "sw", "conversational"),
    LangTestCase("SW-19", "Habari, naomba usaidizi kuhusu jinsi ya kurekebisha taarifa zangu za usajili wa TIN.", "sw", "conversational"),
    LangTestCase("SW-20", "Je, kuna msamaha wowote wa kodi kwa vifaa vya hospitali vinavyoingizwa nchini?", "sw", "conversational"),
    LangTestCase("SW-21", "Mimi ni mfanyabiashara na nina duka la rejareja; ninawezaje kujisajili kwa VAT na kutoa e-invoice?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-22", "Je, kampuni yangu inapaswa kulipa PAYE kwa wafanyakazi wa mkataba na vibarua?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-23", "Kiwango cha withholding tax kwenye malipo ya consulting contracts ni asilimia ngapi?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-24", "Ninawezaje kuzalisha namba ya PRN kwa ajili ya kulipa stamp duty ya ardhi kwa simu?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-25", "Kama mzigo wangu una thamani ya CIF chini ya dola 500, je nina haki ya kupata passenger baggage allowance?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-26", "Mfumo wa EFRIS unahitaji mashine ya electronic fiscal device au naweza kutumia URA web portal?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-27", "Marejesho ya VAT refund yanachukua muda gani baada ya ukaguzi wa mahesabu kukamilika?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-28", "Je, kuna kodi ya excise duty kwenye miamala ya kutuma na kutoa pesa za mobile money?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-29", "Ninahitaji Tax Clearance Certificate ili niweze kuomba zabuni ya serikali; ninaipataje kwenye e-tax?", "sw", "code_switched", is_code_switched=True),
    LangTestCase("SW-30", "Je, kodi ya rental income inatozwa kwenye mapato ghafi au kuna punguzo la matumizi?", "sw", "code_switched", is_code_switched=True),

    # =========================================================================
    # Group 4: Edge Cases & Explicit Directives (10 queries)
    # =========================================================================
    LangTestCase("EDGE-01", "TIN?", "en", "ambiguous_acronym"),
    LangTestCase("EDGE-02", "VAT rate", "en", "ambiguous_acronym"),
    LangTestCase("EDGE-03", "EFRIS portal", "en", "ambiguous_acronym"),
    LangTestCase("EDGE-04", "PRN generation", "en", "ambiguous_acronym"),
    LangTestCase("EDGE-05", "Gyebaleko!", "lg", "greeting_only"),
    LangTestCase("EDGE-06", "Habari yako", "sw", "greeting_only"),
    LangTestCase("EDGE-07", "Hello there", "en", "greeting_only"),
    LangTestCase("EDGE-08", "What is a TIN? Please explain it in Luganda.", "lg", "explicit_directive"),
    LangTestCase("EDGE-09", "VAT yange ntya okugisasula? Answer me in English please.", "en", "explicit_directive"),
    LangTestCase("EDGE-10", "How do I register for PAYE? Naomba unijibu kwa Kiswahili.", "sw", "explicit_directive"),
]


@dataclass
class TestResult:
    id: str
    query: str
    expected_lang: str
    reported_lang: str
    classified_lang: str
    locale_source: str
    is_correct: bool
    latency_ms: float
    status_code: int
    category: str
    is_code_switched: bool
    word_count: int


class AutoLanguageDetectionBenchmark:
    def __init__(self, base_url: str, concurrency: int = 4):
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/api"):
            self.chat_url = f"{self.base_url}/v1/chat"
        else:
            self.chat_url = f"{self.base_url}/api/v1/chat"
        self.concurrency = concurrency
        self.results: list[TestResult] = []

    async def execute_case(self, client: httpx.AsyncClient, case: LangTestCase, sem: asyncio.Semaphore) -> TestResult:
        async with sem:
            words = case.query.split()
            word_count = len(words)
            payload: dict[str, Any] = {
                "message": case.query,
                # Do NOT send explicit locale to exercise pure auto-detection
            }
            if case.session_id:
                payload["conversation_id"] = case.session_id

            t0 = time.perf_counter()
            status_code = 0
            body: dict[str, Any] = {}
            try:
                resp = await client.post(
                    self.chat_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "URA-LangID-Verifier/1.0",
                        "ngrok-skip-browser-warning": "1",
                    },
                    timeout=50.0,
                )
                latency_ms = (time.perf_counter() - t0) * 1000
                status_code = resp.status_code
                if status_code == 200:
                    body = resp.json()
            except Exception as e:
                latency_ms = (time.perf_counter() - t0) * 1000
                status_code = 500
                body = {"error": str(e)}

            reported_lang = str(body.get("locale") or "unknown").lower()
            locale_source = str(body.get("locale_source") or "unknown")
            reply = body.get("reply", "")
            classified_lang = classify_text(reply)

            # Match criteria: API reported locale matches expected, or response linguistic profile matches
            is_correct = (reported_lang == case.expected_lang) or (classified_lang == case.expected_lang)

            return TestResult(
                id=case.id,
                query=case.query,
                expected_lang=case.expected_lang,
                reported_lang=reported_lang,
                classified_lang=classified_lang,
                locale_source=locale_source,
                is_correct=is_correct,
                latency_ms=round(latency_ms, 2),
                status_code=status_code,
                category=case.category,
                is_code_switched=case.is_code_switched,
                word_count=word_count,
            )

    async def run(self) -> dict[str, Any]:
        checkpoint_path = "docs/Reports/data/eval_lang_id_checkpoint.json"
        os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
        done_ids = set()
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    for item in cached.get("results", []):
                        self.results.append(TestResult(**item))
                    done_ids = {r.id for r in self.results}
                    print(f"Loaded {len(done_ids)} cached results from checkpoint.", flush=True)
            except Exception as e:
                print(f"Warning reading checkpoint: {e}", flush=True)

        pending = [c for c in BENCHMARK_CASES if c.id not in done_ids]

        print("\n" + "=" * 80, flush=True)
        print("🌐 AUTOMATIC LANGUAGE DETECTION BENCHMARK (LIVE NGROK GATEWAY)", flush=True)
        print(f"Target Gateway:    {self.chat_url}", flush=True)
        print(f"Total Test Cases:  {len(BENCHMARK_CASES)}", flush=True)
        print(f"Remaining to Run:  {len(pending)}", flush=True)
        print(f"Concurrency:       {self.concurrency}", flush=True)
        print("=" * 80 + "\n", flush=True)

        sem = asyncio.Semaphore(self.concurrency)
        start_time = time.perf_counter()

        async with httpx.AsyncClient(timeout=55.0) as client:
            batch_size = 10
            for i in range(0, len(pending), batch_size):
                batch = pending[i : i + batch_size]
                tasks = [self.execute_case(client, c, sem) for c in batch]
                batch_res = await asyncio.gather(*tasks)
                self.results.extend(batch_res)

                # Save checkpoint
                with open(checkpoint_path, "w", encoding="utf-8") as f:
                    json.dump({"results": [asdict(r) for r in self.results]}, f, indent=2)

                completed = len(self.results)
                acc = sum(1 for r in self.results if r.is_correct) / completed * 100
                mean_lat = statistics.mean([r.latency_ms for r in self.results[-len(batch):]])
                print(
                    f"[{completed:03d}/{len(BENCHMARK_CASES)}] "
                    f"Acc: {acc:5.1f}% | Batch Avg Latency: {mean_lat:6.1f}ms",
                    flush=True
                )

        total_elapsed = time.perf_counter() - start_time
        return self.compile_report(total_elapsed)

    def compile_report(self, total_elapsed: float) -> dict[str, Any]:
        total = len(self.results)
        correct = sum(1 for r in self.results if r.is_correct)
        overall_acc = (correct / total * 100) if total else 0.0

        latencies = [r.latency_ms for r in self.results if r.status_code == 200]
        latencies.sort()
        n = len(latencies)
        p50 = latencies[int(0.50 * n)] if n else 0.0
        p90 = latencies[int(0.90 * n)] if n else 0.0
        p95 = latencies[int(0.95 * n)] if n else 0.0
        p99 = latencies[int(0.99 * n)] if n else 0.0
        mean_lat = statistics.mean(latencies) if n else 0.0

        # Language breakdown
        by_lang: dict[str, Any] = {}
        for l in ("en", "lg", "sw"):
            sub = [r for r in self.results if r.expected_lang == l]
            sub_corr = sum(1 for r in sub if r.is_correct)
            by_lang[l] = {
                "count": len(sub),
                "correct": sub_corr,
                "accuracy_pct": round((sub_corr / len(sub) * 100) if sub else 0.0, 2),
                "avg_lat_ms": round(statistics.mean([r.latency_ms for r in sub]), 1) if sub else 0.0,
            }

        # Code-switching breakdown
        code_sw = [r for r in self.results if r.is_code_switched]
        cs_corr = sum(1 for r in code_sw if r.is_correct)
        cs_acc = (cs_corr / len(code_sw) * 100) if code_sw else 0.0

        # Word length breakdown
        short_q = [r for r in self.results if r.word_count < 5]
        med_q = [r for r in self.results if 5 <= r.word_count <= 12]
        long_q = [r for r in self.results if r.word_count > 12]

        def get_acc(sublist: list[TestResult]) -> float:
            return round((sum(1 for r in sublist if r.is_correct) / len(sublist) * 100), 2) if sublist else 0.0

        length_breakdown = {
            "short (<5 words)": {"count": len(short_q), "accuracy_pct": get_acc(short_q)},
            "medium (5-12 words)": {"count": len(med_q), "accuracy_pct": get_acc(med_q)},
            "long (>12 words)": {"count": len(long_q), "accuracy_pct": get_acc(long_q)},
        }

        # Failure cases
        mismatches = [
            {
                "id": r.id,
                "query": r.query,
                "expected": r.expected_lang,
                "reported": r.reported_lang,
                "classified": r.classified_lang,
                "category": r.category,
            }
            for r in self.results
            if not r.is_correct
        ]

        report = {
            "summary": {
                "total_queries": total,
                "correct_classifications": correct,
                "overall_accuracy_pct": round(overall_acc, 2),
                "total_duration_s": round(total_elapsed, 2),
                "throughput_qps": round(total / total_elapsed if total_elapsed > 0 else 0.0, 2),
                "http_success_rate_pct": round(sum(1 for r in self.results if r.status_code == 200) / total * 100, 2),
            },
            "performance_percentiles_ms": {
                "p50": round(p50, 1),
                "p90": round(p90, 1),
                "p95": round(p95, 1),
                "p99": round(p99, 1),
                "mean": round(mean_lat, 1),
            },
            "language_breakdown": by_lang,
            "code_switching_accuracy_pct": round(cs_acc, 2),
            "length_breakdown": length_breakdown,
            "mismatches": mismatches,
        }
        return report


def main():
    parser = argparse.ArgumentParser(description="Auto Language Detection Verification")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL, help="Base Gateway URL")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrency")
    parser.add_argument("--output", default="docs/Reports/AUTO_LANGUAGE_DETECTION_ACCURACY_REPORT.json")
    args = parser.parse_args()

    bench = AutoLanguageDetectionBenchmark(base_url=args.gateway_url, concurrency=args.concurrency)
    report = asyncio.run(bench.run())

    print("\n" + "=" * 80)
    print("📊 BENCHMARK RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total Evaluated:          {report['summary']['total_queries']}")
    print(f"Overall Accuracy:         {report['summary']['overall_accuracy_pct']}%")
    print(f"HTTP Availability:        {report['summary']['http_success_rate_pct']}%")
    print(f"Throughput:               {report['summary']['throughput_qps']} req/s")
    print(f"Median Latency (p50):     {report['performance_percentiles_ms']['p50']} ms")
    print(f"90th Percentile (p90):    {report['performance_percentiles_ms']['p90']} ms")
    print(f"95th Percentile (p95):    {report['performance_percentiles_ms']['p95']} ms")
    print("-" * 80)
    print("Language Breakdown:")
    for lang, data in report["language_breakdown"].items():
        print(f"  [{lang.upper()}] Accuracy: {data['accuracy_pct']}% ({data['correct']}/{data['count']}) | Avg Latency: {data['avg_lat_ms']} ms")
    print("-" * 80)
    print(f"Code-Switching Accuracy:  {report['code_switching_accuracy_pct']}%")
    print("Length Breakdown:")
    for bucket, bdata in report["length_breakdown"].items():
        print(f"  {bucket}: {bdata['accuracy_pct']}% ({bdata['count']} queries)")
    if report["mismatches"]:
        print("-" * 80)
        print(f"Mismatches ({len(report['mismatches'])}):")
        for m in report["mismatches"]:
            print(f"  [{m['id']}] Expected: {m['expected']} -> Reported: {m['reported']} (Classified: {m['classified']}) | \"{m['query']}\"")
    print("=" * 80 + "\n")

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Report exported to {args.output}")


if __name__ == "__main__":
    main()
