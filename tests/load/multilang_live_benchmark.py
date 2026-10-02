"""Live Ngrok Multilingual Concurrency & Resilience Suite (EN, LG, SW).

Executes:
1. Load Test (Steady sustained concurrency under nominal traffic)
2. Volume Test (High volume & complex composite queries)
3. Stress Test (Progressively stepped concurrency up to saturation)
4. Spike Test (Instantaneous shock burst to test queueing and zero 5xx)

Outputs JSON artifact and concise metrics summary for URA_CX_ENGAGEMENT_GUIDELINE_REPORT.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

GATEWAY_URL = os.getenv(
    "LIVE_GATEWAY_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat",
)

# Test query corpora across the 3 official national languages
BANK = {
    "en": [
        ("vat_rate", "What is the standard VAT rate in Uganda?"),
        ("paye_bands", "How does PAYE work on a salary of 3,500,000 UGX?"),
        ("efris_rules", "What is EFRIS and which businesses are required to issue e-invoices?"),
        ("customs_solar", "What are the customs import duties on solar panels and batteries?"),
        ("tin_steps", "What documents do I need to register for an individual TIN?"),
        ("withholding", "What is the withholding tax rate on commercial supplies of goods?"),
        ("late_penalty", "What is the penalty for filing my monthly VAT return late?"),
        ("rental_tax", "How is rental tax calculated for an individual landlord?"),
    ],
    "lg": [
        ("vat_rate", "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?"),
        ("paye_bands", "Omusolo gwa PAYE gubalibwa gutya ku musaala gwa 3,500,000 UGX?"),
        ("efris_rules", "EFRIS kye ki era bizinensi ki ezirina okukozesa e-invoice?"),
        ("customs_solar", "Musolo ki ogusasulwa ku masannyalaze g'enjuba n'ebikozesebwa?"),
        ("tin_steps", "Biwandiiko ki ebyetaagisa okufuna TIN y'omuntu ssekinoomu?"),
        ("withholding", "Omusolo ogukwatibwa ku bintu ebitundibwa guli ku bitundu bimeka?"),
        ("late_penalty", "Kibonerezo ki ekiriwo bw'olwawo okuwaayo alipoota ya VAT?"),
        ("rental_tax", "Omusolo gw'ennyumba ezipangisibwa gubalibwa gutya?"),
    ],
    "sw": [
        ("vat_rate", "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?"),
        ("paye_bands", "Kodi ya PAYE inakokotolewaje kwa mshahara wa UGX 3,500,000?"),
        ("efris_rules", "EFRIS ni nini na ni biashara gani zinazopaswa kutumia ankara za kielektroniki?"),
        ("customs_solar", "Ushuru wa forodha kwenye paneli za sola na betri ni kiasi gani?"),
        ("tin_steps", "Ni hati gani zinazohitajika kusajili namba ya TIN ya mtu binafsi?"),
        ("withholding", "Kiwango cha kodi ya zuio kwenye usambazaji wa bidhaa ni kipi?"),
        ("late_penalty", "Ni adhabu gani kwa kuchelewa kuwasilisha marejesho ya VAT?"),
        ("rental_tax", "Kodi ya pango inakokotolewaje kwa mwenye nyumba binafsi?"),
    ],
}

VOLUME_QUERIES = {
    "en": (
        "I operate an electronics importing enterprise in downtown Kampala with an annual turnover "
        "of approximately 220 million UGX. I purchase solar batteries from Kenya and inverters from Dubai. "
        "Explain my comprehensive URA obligations regarding VAT registration, EFRIS invoice issuance, "
        "customs duty exemptions on renewable energy equipment, and withholding tax on local supplies."
    ),
    "lg": (
        "Nnina bizinensi etunda ebyuma by'amasannyalaze mu Kampala ng'omwaka nfuna obukadde 220 UGX. "
        "Nnyingiza bbaatule z'enjuba okuva mu Kenya n'inverter okuva e Dubai. Nnyonnyola ebikwata ku "
        "kwewandiisa ku VAT, enkola ya EFRIS mu kufulumya invoice, emisolo gy'oku mwalo ku by'enjuba, "
        "n'omusolo ogukwatibwa ku kutunda ebintu wano mu ggwanga."
    ),
    "sw": (
        "Ninaendesha biashara ya kuagiza vifaa vya kielektroniki katikati mwa jiji la Kampala nikiwa na "
        "mapato ya kila mwaka ya takriban milioni 220 UGX. Ninaagiza betri za sola kutoka Kenya na inverters "
        "kutoka Dubai. Eleza wajibu wangu kamili kwa URA kuhusu usajili wa VAT, utoaji wa ankara za EFRIS, "
        "msamaha wa ushuru wa forodha kwa vifaa vya nishati jua, na kodi ya zuio kwa bidhaa za ndani."
    ),
}


@dataclass
class TurnResult:
    profile: str
    locale: str
    intent: str
    status: int
    latency_s: float
    retrieval_mode: str = ""
    reply_chars: int = 0
    figures_retained: bool = True
    error: str = ""


@dataclass
class ProfileSummary:
    name: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate_pct: float
    duration_s: float
    throughput_qps: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float
    per_locale: dict[str, Any] = field(default_factory=dict)


async def execute_request(
    client: httpx.AsyncClient,
    profile: str,
    locale: str,
    intent: str,
    message: str,
) -> TurnResult:
    t0 = time.perf_counter()
    session_id = f"bench-{profile}-{locale}-{random.randint(100000, 999999)}"
    try:
        resp = await client.post(
            GATEWAY_URL,
            json={"message": message, "locale": locale},
            headers={
                "X-Session-ID": session_id,
                "ngrok-skip-browser-warning": "1",
            },
        )
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            data = resp.json()
            reply = data.get("reply", "")
            mode = data.get("retrieval_mode", "")
            # Verify numbers in query survive in reply when applicable
            figures_ok = True
            if "18%" in message or "3,500,000" in message or "220" in message:
                if "18%" in message and "18%" not in reply:
                    figures_ok = False
            return TurnResult(
                profile=profile,
                locale=locale,
                intent=intent,
                status=resp.status_code,
                latency_s=dt,
                retrieval_mode=mode,
                reply_chars=len(reply),
                figures_retained=figures_ok,
            )
        return TurnResult(
            profile=profile,
            locale=locale,
            intent=intent,
            status=resp.status_code,
            latency_s=dt,
            error=f"HTTP {resp.status_code}: {resp.text[:120]}",
        )
    except Exception as exc:
        dt = time.perf_counter() - t0
        return TurnResult(
            profile=profile,
            locale=locale,
            intent=intent,
            status=0,
            latency_s=dt,
            error=f"{type(exc).__name__}: {str(exc)[:120]}",
        )


def compute_metrics(name: str, results: list[TurnResult], wall_s: float) -> ProfileSummary:
    total = len(results)
    ok = [r for r in results if r.status == 200]
    failures = total - len(ok)
    err_pct = round((failures / total * 100.0), 2) if total > 0 else 0.0
    qps = round(len(ok) / wall_s, 2) if wall_s > 0 else 0.0

    lats_ms = sorted(r.latency_s * 1000.0 for r in ok) if ok else [0.0]

    def quantile(p: float) -> float:
        if not lats_ms:
            return 0.0
        idx = min(int(len(lats_ms) * p), len(lats_ms) - 1)
        return round(lats_ms[idx], 1)

    per_loc = {}
    for loc in ("en", "lg", "sw"):
        loc_res = [r for r in results if r.locale == loc]
        loc_ok = [r for r in loc_res if r.status == 200]
        loc_lats = sorted(r.latency_s * 1000.0 for r in loc_ok) if loc_ok else [0.0]
        per_loc[loc] = {
            "total": len(loc_res),
            "ok": len(loc_ok),
            "success_rate": round(len(loc_ok) / len(loc_res) * 100.0, 1) if loc_res else 0.0,
            "p50_ms": round(loc_lats[int(len(loc_lats) * 0.50)], 1) if loc_ok else 0.0,
            "p95_ms": round(loc_lats[int(len(loc_lats) * 0.95)], 1) if loc_ok else 0.0,
            "mean_chars": round(sum(r.reply_chars for r in loc_ok) / len(loc_ok), 0) if loc_ok else 0,
        }

    return ProfileSummary(
        name=name,
        total_requests=total,
        successful_requests=len(ok),
        failed_requests=failures,
        error_rate_pct=err_pct,
        duration_s=round(wall_s, 2),
        throughput_qps=qps,
        p50_ms=quantile(0.50),
        p90_ms=quantile(0.90),
        p95_ms=quantile(0.95),
        p99_ms=quantile(0.99),
        min_ms=round(min(lats_ms), 1) if ok else 0.0,
        max_ms=round(max(lats_ms), 1) if ok else 0.0,
        mean_ms=round(sum(lats_ms) / len(lats_ms), 1) if ok else 0.0,
        per_locale=per_loc,
    )


# ---------------------------------------------------------------------------
# Test Profiles
# ---------------------------------------------------------------------------
async def run_load_profile(client: httpx.AsyncClient) -> ProfileSummary:
    """Load Test: 6 concurrent workers executing 60 mixed queries."""
    print("--> Starting [1/4] LOAD TEST (Concurrency: 6 VUs, 60 requests EN/LG/SW)...", flush=True)
    concurrency = 6
    total_target = 60
    results: list[TurnResult] = []
    t0 = time.perf_counter()

    queue = asyncio.Queue()
    for _ in range(total_target):
        loc = random.choice(["en", "lg", "sw"])
        intent, msg = random.choice(BANK[loc])
        queue.put_nowait((loc, intent, msg))

    async def worker():
        while not queue.empty():
            loc, intent, msg = await queue.get()
            res = await execute_request(client, "load", loc, intent, msg)
            results.append(res)
            queue.task_done()
            await asyncio.sleep(0.05)

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await queue.join()
    for w in workers:
        w.cancel()
    wall = time.perf_counter() - t0
    return compute_metrics("Load Test (Sustained)", results, wall)


async def run_volume_profile(client: httpx.AsyncClient) -> ProfileSummary:
    """Volume Test: 8 concurrent workers processing 45 deep multi-clause enterprise prompts."""
    print("--> Starting [2/4] VOLUME TEST (Concurrency: 8 VUs, 45 deep multi-clause queries)...", flush=True)
    concurrency = 8
    total_target = 45
    results: list[TurnResult] = []
    t0 = time.perf_counter()

    queue = asyncio.Queue()
    for _ in range(total_target):
        loc = random.choice(["en", "lg", "sw"])
        msg = VOLUME_QUERIES[loc]
        queue.put_nowait((loc, "volume_enterprise", msg))

    async def worker():
        while not queue.empty():
            loc, intent, msg = await queue.get()
            res = await execute_request(client, "volume", loc, intent, msg)
            results.append(res)
            queue.task_done()
            await asyncio.sleep(0.05)

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await queue.join()
    for w in workers:
        w.cancel()
    wall = time.perf_counter() - t0
    return compute_metrics("Volume Test (Heavy Multi-clause)", results, wall)


async def run_stress_profile(client: httpx.AsyncClient) -> ProfileSummary:
    """Stress Test: Stepped ramp 4 -> 8 -> 16 -> 24 concurrent workers."""
    print("--> Starting [3/4] STRESS TEST (Stepped concurrency: 4 -> 8 -> 16 -> 24 VUs)...", flush=True)
    results: list[TurnResult] = []
    t0 = time.perf_counter()

    steps = [4, 8, 16, 24]
    for step_concurrency in steps:
        print(f"    ... stepping to {step_concurrency} concurrent VUs", flush=True)
        batch_tasks = []
        for _ in range(step_concurrency):
            loc = random.choice(["en", "lg", "sw"])
            intent, msg = random.choice(BANK[loc])
            batch_tasks.append(execute_request(client, f"stress_{step_concurrency}", loc, intent, msg))
        batch_results = await asyncio.gather(*batch_tasks)
        results.extend(batch_results)
        await asyncio.sleep(0.2)

    wall = time.perf_counter() - t0
    return compute_metrics("Stress Test (Ramped Concurrency)", results, wall)


async def run_spike_profile(client: httpx.AsyncClient) -> ProfileSummary:
    """Spike Test: Sudden unannounced burst of 32 simultaneous requests."""
    print("--> Starting [4/4] SPIKE TEST (Instantaneous burst of 32 simultaneous requests)...", flush=True)
    results: list[TurnResult] = []
    t0 = time.perf_counter()

    burst_size = 32
    tasks = []
    for _ in range(burst_size):
        loc = random.choice(["en", "lg", "sw"])
        intent, msg = random.choice(BANK[loc])
        tasks.append(execute_request(client, "spike_burst", loc, intent, msg))

    burst_results = await asyncio.gather(*tasks)
    results.extend(burst_results)

    wall = time.perf_counter() - t0
    return compute_metrics("Spike Test (Instantaneous Burst)", results, wall)


async def main():
    print(f"===================================================================")
    print(f"URA CHATBOT LIVE NGROK BENCHMARK: EN, LG, SW")
    print(f"Target: {GATEWAY_URL}")
    print(f"===================================================================")

    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=45.0, limits=limits, headers={"ngrok-skip-browser-warning": "1"}) as client:
        # 1. Load Test
        load_summary = await run_load_profile(client)
        # 2. Volume Test
        vol_summary = await run_volume_profile(client)
        # 3. Stress Test
        stress_summary = await run_stress_profile(client)
        # 4. Spike Test
        spike_summary = await run_spike_profile(client)

    all_summaries = [load_summary, vol_summary, stress_summary, spike_summary]

    print(f"\n===================================================================")
    print(f"EMPIRICAL MULTILINGUAL BENCHMARK RESULTS")
    print(f"===================================================================")
    for s in all_summaries:
        print(f"\n[{s.name}]")
        print(f"  Requests: {s.successful_requests}/{s.total_requests} OK (Error Rate: {s.error_rate_pct}%)")
        print(f"  Throughput: {s.throughput_qps} QPS | Duration: {s.duration_s}s")
        print(f"  Latency (ms): p50={s.p50_ms} | p90={s.p90_ms} | p95={s.p95_ms} | p99={s.p99_ms} | mean={s.mean_ms}")
        print(f"  Per-Locale Breakdown:")
        for loc, data in s.per_locale.items():
            print(f"    - {loc.upper()}: {data['ok']}/{data['total']} passed ({data['success_rate']}%) | p50={data['p50_ms']}ms | p95={data['p95_ms']}ms | mean_chars={data['mean_chars']}")

    # Save to JSON
    out_path = "docs/presentation/multilang_concurrency_audit_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in all_summaries], f, indent=2)
    print(f"\nSaved structured audit artifact to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
