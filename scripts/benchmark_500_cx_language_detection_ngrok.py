#!/usr/bin/env python3
"""500 Complex Customer Experience (CX) Scenarios Auto Language Detection Benchmark.

Evaluates URA AI Assistant on 500 challenging, complex, and conversational CX edge cases
balanced across English (170), Luganda (165), and Swahili (165).

Tests:
1. Pure Auto Language Detection (no explicit client locale sent)
2. Handling of conversational politeness prefixes across languages:
   - English: "Please tell me:", "Could you clarify:", "Kindly explain:"
   - Luganda: "Bambi ŋŋamba:", "Nsaba onnyonnyole:", "Mwasuze mutya:"
   - Swahili: "Tafadhali nijuze:", "Naomba kuelewa:", "Habari yako:"
3. Code-switching resilience on complex multi-tax scenarios with statutory loanwords
4. Per-domain classification accuracy across 7 operational regimes
5. Real-world end-to-end latency & throughput across the live ngrok tunnel
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
DEFAULT_DATASET = "docs/presentation/edge_cases_500_dataset.json"
CHECKPOINT_PATH = "docs/Reports/data/eval_500_cx_lang_id_checkpoint.json"
OUTPUT_REPORT = "docs/Reports/AUTO_LANGUAGE_DETECTION_500_CX_REPORT.json"

MARKERS = {
    "en": ("the", "and", "you", "your", "for", "is", "are", "to", "of", "a", "must", "tax", "please", "can", "how", "what"),
    "lg": ("oba", "nga", "era", "eri", "kye", "bye", "gwa", "eby", "omu", "aba", "oku", "okw", "ekya", "ssente", "musolo", "wano", "buli", "ntya", "olina", "kola", "ddi", "bambi", "ŋŋamba"),
    "sw": ("ya", "wa", "kwa", "ni", "katika", "una", "kama", "hii", "kodi", "lazima", "unaweza", "yako", "kuwa", "je", "asilimia", "na", "jinsi", "gani", "nini", "vipi", "huduma", "tafadhali", "nijuze"),
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
    if scores[best] < 0.012:
        return "unknown"
    return best


@dataclass
class CXResult:
    case_id: int
    locale: str
    query: str
    domain: str
    reported_lang: str
    classified_lang: str
    is_correct: bool
    latency_ms: float
    status_code: int
    word_count: int


class Benchmark500CXRunner:
    def __init__(self, base_url: str, dataset_path: str, concurrency: int = 8):
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/api"):
            self.chat_url = f"{self.base_url}/v1/chat"
        else:
            self.chat_url = f"{self.base_url}/api/v1/chat"
        self.dataset_path = dataset_path
        self.concurrency = concurrency
        self.results: list[CXResult] = []

    def load_dataset(self) -> list[dict[str, Any]]:
        with open(self.dataset_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_checkpoint(self) -> set[int]:
        if os.path.exists(CHECKPOINT_PATH):
            try:
                with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("results", []):
                        self.results.append(CXResult(**item))
                    done_ids = {r.case_id for r in self.results}
                    print(f" Loaded {len(done_ids)} cached results from checkpoint.", flush=True)
                    return done_ids
            except Exception as e:
                print(f" Warning reading checkpoint: {e}", flush=True)
        return set()

    def save_checkpoint(self) -> None:
        try:
            os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
            with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
                json.dump({"results": [asdict(r) for r in self.results]}, f, indent=2)
        except Exception as e:
            print(f" Warning writing checkpoint: {e}", flush=True)

    async def evaluate_item(
        self, client: httpx.AsyncClient, case: dict[str, Any], sem: asyncio.Semaphore
    ) -> CXResult:
        async with sem:
            case_id = case.get("id", 0)
            expected_lang = case.get("locale", "en")
            query = case.get("query", "")
            domain = case.get("domain", "General")
            words = query.split()
            word_count = len(words)

            payload = {
                "message": query,
                # Explicitly DO NOT send 'locale' parameter to force server-side auto language detection
            }

            t0 = time.perf_counter()
            status_code = 0
            body: dict[str, Any] = {}
            retries = 0
            while retries <= 2:
                try:
                    resp = await client.post(
                        self.chat_url,
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "URA-LangID-500-Benchmark/1.0",
                            "ngrok-skip-browser-warning": "1",
                        },
                        timeout=50.0,
                    )
                    latency_ms = (time.perf_counter() - t0) * 1000
                    status_code = resp.status_code
                    if status_code == 200:
                        body = resp.json()
                    break
                except Exception as e:
                    retries += 1
                    if retries > 2:
                        latency_ms = (time.perf_counter() - t0) * 1000
                        status_code = 500
                        body = {"error": str(e)}
                        break
                    await asyncio.sleep(1.0)

            reported_lang = str(body.get("locale") or "unknown").lower()
            reply = body.get("reply", "")
            classified_lang = classify_text(reply)

            # Match criteria: server reported resolved locale matches expected, or linguistic analysis matches
            is_correct = (reported_lang == expected_lang) or (classified_lang == expected_lang)

            return CXResult(
                case_id=case_id,
                locale=expected_lang,
                query=query,
                domain=domain,
                reported_lang=reported_lang,
                classified_lang=classified_lang,
                is_correct=is_correct,
                latency_ms=round(latency_ms, 2),
                status_code=status_code,
                word_count=word_count,
            )

    async def run(self) -> dict[str, Any]:
        raw_dataset = self.load_dataset()
        done_ids = self.load_checkpoint()
        pending = [c for c in raw_dataset if c.get("id", 0) not in done_ids]

        print("\n" + "=" * 80, flush=True)
        print("🚀 500 COMPLEX CX SCENARIOS AUTO LANGUAGE DETECTION BENCHMARK", flush=True)
        print(f"Target Gateway:    {self.chat_url}", flush=True)
        print(f"Total Scenarios:   {len(raw_dataset)}", flush=True)
        print(f"Remaining to Run:  {len(pending)}", flush=True)
        print(f"Concurrency:       {self.concurrency}", flush=True)
        print("=" * 80 + "\n", flush=True)

        sem = asyncio.Semaphore(self.concurrency)
        start_time = time.perf_counter()

        async with httpx.AsyncClient(timeout=55.0) as client:
            batch_size = 20
            for i in range(0, len(pending), batch_size):
                batch = pending[i : i + batch_size]
                tasks = [self.evaluate_item(client, c, sem) for c in batch]
                batch_res = await asyncio.gather(*tasks)
                self.results.extend(batch_res)
                self.save_checkpoint()

                completed = len(self.results)
                acc = sum(1 for r in self.results if r.is_correct) / completed * 100
                mean_lat = statistics.mean([r.latency_ms for r in self.results[-len(batch):]])
                elapsed = time.perf_counter() - start_time
                qps = len(self.results) / elapsed if elapsed > 0 else 0

                print(
                    f"[{completed:03d}/{len(raw_dataset)}] "
                    f"Acc: {acc:5.1f}% | Batch Avg Latency: {mean_lat:6.1f}ms | Rate: {qps:4.2f} req/s",
                    flush=True,
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
            sub = [r for r in self.results if r.locale == l]
            sub_corr = sum(1 for r in sub if r.is_correct)
            by_lang[l] = {
                "count": len(sub),
                "correct": sub_corr,
                "accuracy_pct": round((sub_corr / len(sub) * 100) if sub else 0.0, 2),
                "avg_lat_ms": round(statistics.mean([r.latency_ms for r in sub]), 1) if sub else 0.0,
                "http_success_pct": round(sum(1 for r in sub if r.status_code == 200) / len(sub) * 100, 2) if sub else 0.0,
            }

        # Domain breakdown
        domains = sorted({r.domain for r in self.results})
        by_domain: dict[str, Any] = {}
        for d in domains:
            sub = [r for r in self.results if r.domain == d]
            sub_corr = sum(1 for r in sub if r.is_correct)
            by_domain[d] = {
                "count": len(sub),
                "correct": sub_corr,
                "accuracy_pct": round((sub_corr / len(sub) * 100) if sub else 0.0, 2),
                "avg_lat_ms": round(statistics.mean([r.latency_ms for r in sub]), 1) if sub else 0.0,
            }

        # Length breakdown
        short_q = [r for r in self.results if r.word_count < 8]
        med_q = [r for r in self.results if 8 <= r.word_count <= 18]
        long_q = [r for r in self.results if r.word_count > 18]

        def get_acc(sublist: list[CXResult]) -> float:
            return round((sum(1 for r in sublist if r.is_correct) / len(sublist) * 100), 2) if sublist else 0.0

        length_breakdown = {
            "short (<8 words)": {"count": len(short_q), "accuracy_pct": get_acc(short_q)},
            "medium (8-18 words)": {"count": len(med_q), "accuracy_pct": get_acc(med_q)},
            "long (>18 words)": {"count": len(long_q), "accuracy_pct": get_acc(long_q)},
        }

        mismatches = [
            {
                "id": r.case_id,
                "query": r.query,
                "expected": r.locale,
                "reported": r.reported_lang,
                "classified": r.classified_lang,
                "domain": r.domain,
            }
            for r in self.results
            if not r.is_correct
        ]

        report = {
            "summary": {
                "total_scenarios": total,
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
            "domain_breakdown": by_domain,
            "length_breakdown": length_breakdown,
            "mismatches": mismatches,
        }
        return report


def main():
    parser = argparse.ArgumentParser(description="500 Complex CX Scenarios Auto Language Detection")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL, help="Gateway base URL")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="Dataset JSON path")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrency")
    parser.add_argument("--output", default=OUTPUT_REPORT, help="Output JSON path")
    args = parser.parse_args()

    runner = Benchmark500CXRunner(
        base_url=args.gateway_url,
        dataset_path=args.dataset,
        concurrency=args.concurrency,
    )
    report = asyncio.run(runner.run())

    print("\n" + "=" * 80)
    print("📊 500 COMPLEX CX SCENARIOS BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"Total Evaluated:          {report['summary']['total_scenarios']}")
    print(f"Overall Accuracy:         {report['summary']['overall_accuracy_pct']}%")
    print(f"HTTP Availability:        {report['summary']['http_success_rate_pct']}%")
    print(f"Throughput:               {report['summary']['throughput_qps']} req/s")
    print(f"Median Latency (p50):     {report['performance_percentiles_ms']['p50']} ms")
    print(f"90th Percentile (p90):    {report['performance_percentiles_ms']['p90']} ms")
    print(f"95th Percentile (p95):    {report['performance_percentiles_ms']['p95']} ms")
    print("-" * 80)
    print("Language Breakdown:")
    for lang, data in report["language_breakdown"].items():
        print(f"  [{lang.upper()}] Accuracy: {data['accuracy_pct']}% ({data['correct']}/{data['count']}) | Avg Lat: {data['avg_lat_ms']} ms")
    print("-" * 80)
    print("Domain Breakdown:")
    for d, data in report["domain_breakdown"].items():
        print(f"  {d[:40]:<40} : {data['accuracy_pct']}% ({data['correct']}/{data['count']})")
    print("-" * 80)
    print("Length Breakdown:")
    for bucket, bdata in report["length_breakdown"].items():
        print(f"  {bucket}: {bdata['accuracy_pct']}% ({bdata['count']} queries)")
    if report["mismatches"]:
        print("-" * 80)
        print(f"Mismatches ({len(report['mismatches'])}):")
        for m in report["mismatches"][:10]:
            print(f"  [{m['id']}] Expected: {m['expected']} -> Reported: {m['reported']} | \"{m['query'][:60]}...\"")
    print("=" * 80 + "\n")

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Report exported to {args.output}")


if __name__ == "__main__":
    main()
