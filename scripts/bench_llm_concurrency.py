"""Measure an LLM endpoint's latency under concurrency (ADR-0033, phase 2).

The model decision is made on measurements. This sends the same set of tax
prompts to an OpenAI-compatible chat endpoint (vLLM serves one) at each
concurrency level and reports p50, p95 and errors per level, against the
NFR-01 target of p95 at most 3 s.

Run it on the GPU host, once per candidate model::

    python scripts/bench_llm_concurrency.py --base-url http://localhost:8000/v1 \\
        --model Qwen/Qwen3.8-27B-FP8 --levels 1 4 8 --requests 24 --out bench-27b.json

Compare the reports; route only the turns that need the larger model to it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

P95_TARGET_S = 3.0

PROMPTS = (
    "What is the standard VAT rate in Uganda, and which Act sets it?",
    "A return with UGX 5,000,000 of tax payable was filed 3 months late. Explain the late-filing penal tax.",
    "When is PAYE due each month, and how long does a taxpayer have to object to an assessment?",
    "Explain how VAT is charged on an import when customs duty also applies.",
)


@dataclass
class LevelReport:
    concurrency: int
    requests: int
    errors: int
    p50_s: float
    p95_s: float
    max_s: float
    meets_target: bool


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile; 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(q / 100 * len(ordered) + 0.5))))
    return ordered[rank - 1]


def summarise(concurrency: int, latencies: list[float], errors: int) -> LevelReport:
    p95 = percentile(latencies, 95)
    return LevelReport(
        concurrency=concurrency,
        requests=len(latencies) + errors,
        errors=errors,
        p50_s=round(statistics.median(latencies), 3) if latencies else 0.0,
        p95_s=round(p95, 3),
        max_s=round(max(latencies), 3) if latencies else 0.0,
        meets_target=bool(latencies) and errors == 0 and p95 <= P95_TARGET_S,
    )


async def run_level(send: Any, concurrency: int, requests: int) -> LevelReport:
    """Send *requests* prompts with at most *concurrency* in flight; *send* is an async callable."""
    gate = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0

    async def one(prompt: str) -> None:
        nonlocal errors
        async with gate:
            started = time.perf_counter()
            try:
                await send(prompt)
            except Exception:  # noqa: BLE001 - every failure counts as an error
                errors += 1
                return
            latencies.append(time.perf_counter() - started)

    await asyncio.gather(*(one(PROMPTS[i % len(PROMPTS)]) for i in range(requests)))
    return summarise(concurrency, latencies, errors)


def http_sender(base_url: str, model: str, max_tokens: int, timeout_s: float) -> Any:
    import httpx

    client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    async def send(prompt: str) -> None:
        response = await client.post("/chat/completions", json={
            "model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        })
        response.raise_for_status()

    return send


async def bench(send: Any, levels: list[int], requests: int) -> list[LevelReport]:
    return [await run_level(send, level, requests) for level in levels]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    send = http_sender(args.base_url, args.model, args.max_tokens, args.timeout)
    reports = asyncio.run(bench(send, args.levels, args.requests))
    document = {"model": args.model, "base_url": args.base_url, "p95_target_s": P95_TARGET_S,
                "levels": [asdict(r) for r in reports]}
    text = json.dumps(document, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)  # noqa: T201 - CLI output
    return 0 if all(r.meets_target for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
