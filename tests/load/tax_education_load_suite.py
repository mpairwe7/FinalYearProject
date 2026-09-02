"""Tax Education Conversation Volume & Load Test Suite — URA Chatbot.

Tests how the assistant handles tax education conversations under realistic
concurrency, volume, and adversarial dialogue patterns by measuring:

  • Latency distributions (p50 / p95 / p99 / max)
  • Throughput (RPS) per educational topic cluster
  • Error rate and grounding rate (answer vs abstain)
  • Conversation coherence across multi-turn sessions
  • Retrieval mode reported (vector / hybrid / keyword / abstained)
  • Language localisation fidelity for en / lg / sw
  • Degradation gradient across VU ramp-up phases

Target: live HF Space  https://landwind22-ura-chatbot.hf.space
Companion: tests/load/ngrok_multilang_suite.py (ngrok GPU stack)
Standard: ISO/IEC 25010:2023 §2 (Performance Efficiency), NFR-01 (p95 ≤ 15 s)

Run:
    # smoke — quick sanity (≈60 s)
    python3 tests/load/tax_education_load_suite.py --phase smoke

    # full suite — all phases, save JSON report
    python3 tests/load/tax_education_load_suite.py --phase all --out results/tax_edu_load.json

    # single topic cluster deep dive
    python3 tests/load/tax_education_load_suite.py --phase vat,paye --vu 8

    # volume phase with multi-turn sessions
    python3 tests/load/tax_education_load_suite.py --phase volume --sessions 20

    # characterise raw rate-limiter behaviour (no retry, no delay)
    python3 tests/load/tax_education_load_suite.py --phase smoke --no-retry --delay 0

Usage with proxy (ngrok / Crane Cloud):
    TAX_BASE=https://your-ngrok-url.ngrok-free.app \\
        python3 tests/load/tax_education_load_suite.py --phase all

Rate-limiter notes:
    The HF Space ships RATE_LIMIT=30/minute keyed on remote address.
    All tunnel traffic from a single host shares ONE bucket.  By default
    this suite respects the limiter via per-request jitter + exponential
    backoff on 429.  Pass --no-retry --delay 0 to measure raw 429 rates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get(
    "TAX_BASE", "https://landwind22-ura-chatbot.hf.space"
).rstrip("/")

CHAT_ENDPOINT = f"{BASE_URL}/v1/chat"
HEALTH_ENDPOINT = f"{BASE_URL}/health"
READY_ENDPOINT = f"{BASE_URL}/ready"

REQUEST_TIMEOUT = 60.0  # generous; HF Space may cold-start

HTTP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "TaxEduLoadSuite/1.0",
}

# NFR thresholds (seconds)
NFR_P95_S = float(os.environ.get("NFR_P95_S", "20.0"))
NFR_P99_S = float(os.environ.get("NFR_P99_S", "35.0"))
NFR_MAX_ERROR_PCT = float(os.environ.get("NFR_MAX_ERROR_PCT", "5.0"))

# ---------------------------------------------------------------------------
# Rate-limiter awareness
#
# The HF Space ships RATE_LIMIT=30/minute keyed on get_remote_address.
# When the test runner and the HF Space nginx proxy both sit behind NAT,
# ALL requests from this machine share a single 30/min bucket regardless
# of how many VUs are used (the same finding as the 2026-08-22 run §5).
#
# Strategy (default, --no-retry not set):
#   1. Request STARTS are spaced globally by _INTER_REQUEST_DELAY_S + U(0, 1)s
#      of jitter, so the aggregate stays well under 0.5 RPS (= 30/min).
#      The spacing is global rather than per-VU on purpose: every VU shares one
#      bucket, so per-VU sleeping would still let N concurrent VUs issue N
#      requests at once and the aggregate claim would not hold.
#   2. On HTTP 429 the request is retried up to _RETRY_MAX times with
#      exponential backoff (_RETRY_BACKOFF_BASE ** attempt seconds).
#   3. After _RETRY_MAX retries the result is recorded as a 429 failure.
#
# Pass --no-retry --delay 0 to disable both, measuring raw limiter behaviour
# (reproduces the ratelimit phase from the 2026-08-22 multilingual run).
# ---------------------------------------------------------------------------
_DEFAULT_DELAY_S = float(os.environ.get("INTER_REQUEST_DELAY_S", "2.2"))
_RETRY_MAX = int(os.environ.get("RETRY_MAX", "3"))
_RETRY_BACKOFF_BASE = float(os.environ.get("RETRY_BACKOFF_BASE", "2.0"))

# Module-level flags — overridden by --no-retry / --delay CLI args at startup.
_ENABLE_RETRY: bool = True
_INTER_REQUEST_DELAY_S: float = _DEFAULT_DELAY_S

# The pacer state behind the strategy note above. `--delay` used to be parsed
# into _INTER_REQUEST_DELAY_S and then read by nothing at all, so every flat
# phase ran at full concurrency into the documented 30/min limiter and the
# reported RPS, error rates and latency percentiles described throttling the
# suite believed it was avoiding. `--no-retry --delay 0` was likewise identical
# to the default, so the raw-limiter comparison the docstring offers could not
# be made.
_pacer_lock: asyncio.Lock | None = None
_next_request_at: float = 0.0


async def _await_rate_slot() -> None:
    """Block until this request may start, honouring the global spacing.

    `--delay 0` disables it entirely, which is what makes the documented
    raw-limiter measurement possible.
    """
    global _pacer_lock, _next_request_at
    if _INTER_REQUEST_DELAY_S <= 0:
        return
    if _pacer_lock is None:  # created lazily so it binds to the running loop
        _pacer_lock = asyncio.Lock()
    async with _pacer_lock:
        now = time.monotonic()
        if now < _next_request_at:
            await asyncio.sleep(_next_request_at - now)
        _next_request_at = time.monotonic() + _INTER_REQUEST_DELAY_S + random.uniform(0, 1)

# ---------------------------------------------------------------------------
# Tax Education Query Banks
# Organised into topic clusters that map to real URA curriculum areas:
#   1. VAT & Registration       2. PAYE / Employment taxes
#   3. Withholding Tax          4. EFRIS & Invoicing
#   5. TIN & Taxpayer Identity  6. Returns & Penalties
#   7. Customs & Trade          8. Business Presumptive Tax
#   9. Property / Rental Tax
# Each cluster has English + Luganda + Kiswahili variants.
# ---------------------------------------------------------------------------
QUERY_BANK: dict[str, dict[str, list[tuple[str, str]]]] = {
    # ---- 1. VAT ----
    "vat": {
        "en": [
            ("vat_rate", "What is the current VAT rate in Uganda?"),
            ("vat_threshold", "What annual turnover threshold triggers mandatory VAT registration?"),
            ("vat_exempt", "Which goods and services are exempt from VAT in Uganda?"),
            ("vat_refund", "How do I claim a VAT refund from URA?"),
            ("vat_return_deadline", "When is the deadline for filing VAT returns each month?"),
            ("vat_zero_rated", "What does zero-rated VAT mean, and which exports qualify?"),
            ("vat_invoice", "What information must a VAT invoice include to be valid?"),
        ],
        "lg": [
            ("vat_rate", "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?"),
            ("vat_threshold", "Obukadde bwa buli mwaka obutuusa ku wo musaanidde okwewandiisa ku VAT?"),
            ("vat_exempt", "Ebintu ki ebivuunama ku VAT mu Uganda?"),
        ],
        "sw": [
            ("vat_rate", "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?"),
            ("vat_threshold", "Mapato ya mwaka ngapi yanayohitaji usajili wa lazima wa VAT?"),
            ("vat_exempt", "Bidhaa na huduma zipi zimesamehewa VAT Uganda?"),
        ],
    },
    # ---- 2. PAYE ----
    "paye": {
        "en": [
            ("paye_calc", "How is PAYE calculated for an employee earning 3,500,000 UGX per month?"),
            ("paye_bands", "What are the current PAYE tax bands in Uganda for 2024?"),
            ("paye_employer", "What are an employer's obligations for PAYE remittance to URA?"),
            ("paye_nssf", "How does NSSF interact with PAYE calculations?"),
            ("paye_deadline", "When must PAYE be remitted to URA each month?"),
            ("paye_penalty", "What penalty applies if an employer fails to remit PAYE on time?"),
        ],
        "lg": [
            ("paye_calc", "PAYE ya mulimu ow'omusawo afuna akasilingi 3,500,000 buli mwezi gisuwanganyizibwa etya?"),
            ("paye_employer", "Munanyi w'omulimu alina emirimu ki ku PAYE gw'okutuma URA?"),
        ],
        "sw": [
            ("paye_calc", "PAYE inakokotolewa vipi kwa mfanyakazi anayepata UGX 3,500,000 kwa mwezi?"),
            ("paye_employer", "Mwajiri ana wajibu gani wa kuwasilisha PAYE kwa URA?"),
        ],
    },
    # ---- 3. Withholding Tax ----
    "withholding": {
        "en": [
            ("wht_rate", "What is the withholding tax rate on professional fees in Uganda?"),
            ("wht_scope", "Which payments attract withholding tax obligations?"),
            ("wht_certificate", "How do I obtain a withholding tax certificate from URA?"),
            ("wht_credit", "Can withholding tax paid be credited against income tax?"),
            ("wht_exemption", "Who is exempt from withholding tax in Uganda?"),
        ],
        "lg": [
            ("wht_rate", "Omusolo ogukwatibwa nga tennasasulwa ku misaasi gya obukugu guli ku bitundu bimeka?"),
            ("wht_scope", "Okusasulwa ki okukwatibwa omusolo mu Uganda?"),
        ],
        "sw": [
            ("wht_rate", "Kiwango cha kodi ya zuio kwenye ada za kitaalamu Uganda ni ngapi?"),
            ("wht_scope", "Malipo yapi yanayovutia wajibu wa kodi ya zuio?"),
        ],
    },
    # ---- 4. EFRIS & Invoicing ----
    "efris": {
        "en": [
            ("efris_what", "What is EFRIS and who is required to use it?"),
            ("efris_register", "How do I register my business on the EFRIS portal?"),
            ("efris_invoice", "How do I generate a valid e-invoice through EFRIS?"),
            ("efris_penalty", "What is the penalty for non-compliance with EFRIS requirements?"),
            ("efris_receipt", "What is an electronic fiscal receipt, and is it the same as a tax invoice?"),
            ("efris_offline", "What happens if EFRIS is offline — can I still issue invoices?"),
        ],
        "lg": [
            ("efris_what", "EFRIS kye ki era ani alina okugikozesa?"),
            ("efris_register", "Nnyinza ntya okwewandiisa bizinensi yange ku EFRIS portal?"),
        ],
        "sw": [
            ("efris_what", "EFRIS ni nini na ni nani anapaswa kuitumia?"),
            ("efris_penalty", "Ni adhabu gani kwa kutofuata mahitaji ya EFRIS?"),
        ],
    },
    # ---- 5. TIN & Registration ----
    "tin": {
        "en": [
            ("tin_get", "How do I apply for a Tax Identification Number (TIN) in Uganda?"),
            ("tin_required", "Is a TIN required for all business transactions above a threshold?"),
            ("tin_lost", "What do I do if I lose my TIN certificate?"),
            ("tin_individual", "Can an individual (not a company) get a TIN?"),
            ("tin_foreign", "Can a foreign investor obtain a TIN in Uganda?"),
        ],
        "lg": [
            ("tin_get", "Nnyinza ntya okufuna Ennamba y'Omusolo (TIN) mu Uganda?"),
            ("tin_required", "TIN inaakola ku bisuubuzi byonna ebisinga obuwa obw'esnanda?"),
        ],
        "sw": [
            ("tin_get", "Ninawezaje kupata Nambari ya Utambulisho wa Kodi (TIN) Uganda?"),
            ("tin_individual", "Je, mtu binafsi (si kampuni) anaweza kupata TIN?"),
        ],
    },
    # ---- 6. Returns & Penalties ----
    "returns": {
        "en": [
            ("filing_deadline", "When is the deadline for filing annual income tax returns in Uganda?"),
            ("late_penalty", "What is the penalty for submitting a tax return after the deadline?"),
            ("amend_return", "Can I amend a tax return I already filed with URA?"),
            ("nil_return", "What is a nil return and when must I file one?"),
            ("provisional_tax", "What is provisional tax and who must pay it?"),
        ],
        "lg": [
            ("filing_deadline", "Obudde obw'okuwaayo alipoota y'omusolo bw'omwaka mu Uganda bujja ddi?"),
            ("late_penalty", "Obuzito ki obugwa n'owaayo alipoota y'omusolo oluvannyuma lw'obudde?"),
        ],
        "sw": [
            ("filing_deadline", "Tarehe ya mwisho ya kuwasilisha marejesho ya kodi ya mapato ya mwaka Uganda?"),
            ("late_penalty", "Ni adhabu gani kwa kuwasilisha marejesho ya kodi baada ya tarehe ya mwisho?"),
        ],
    },
    # ---- 7. Customs & Trade ----
    "customs": {
        "en": [
            ("import_duty", "How is import duty calculated on goods entering Uganda?"),
            ("customs_value", "What is customs value and how does URA determine it?"),
            ("duty_free", "What goods are duty-free when imported into Uganda?"),
            ("export_incentive", "What tax incentives exist for Ugandan exporters?"),
        ],
        "lg": [
            ("import_duty", "Omusolo w'okutwala ebintu okuva hanze era gusuwanganyizibwa etya mu Uganda?"),
        ],
        "sw": [
            ("import_duty", "Ushuru wa kuagiza unakokotolewa vipi kwa bidhaa zinazoingia Uganda?"),
        ],
    },
    # ---- 8. Presumptive Tax ----
    "presumptive": {
        "en": [
            ("presumptive_who", "Who qualifies for the presumptive tax regime in Uganda?"),
            ("presumptive_rate", "How is presumptive tax calculated for a turnover of 50 million UGX?"),
            ("presumptive_vs_normal", "What is the difference between presumptive tax and normal income tax?"),
            ("presumptive_exit", "When must a business leave the presumptive tax regime?"),
        ],
        "lg": [
            ("presumptive_who", "Ani alina okusasula omusolo gwa presumptive mu Uganda?"),
        ],
        "sw": [
            ("presumptive_who", "Ni nani anastahili mfumo wa kodi ya msingi Uganda?"),
        ],
    },
    # ---- 9. Rental & Property Tax ----
    "property": {
        "en": [
            ("rental_rate", "What is the rental tax rate for commercial property income in Uganda?"),
            ("rental_individual", "How much rental income can an individual earn before paying tax?"),
            ("property_register", "Must landlords register with URA for rental income?"),
        ],
        "lg": [
            ("rental_rate", "Omusolo w'obutunze ku muga gw'obusuubi mu Uganda guli ku bitundu bimeka?"),
        ],
        "sw": [
            ("rental_rate", "Kiwango cha kodi ya mpangilio kwa mapato ya mali ya kibiashara Uganda?"),
        ],
    },
}

# ---------------------------------------------------------------------------
# Multi-turn conversation scenarios — simulates taxpayers working through
# a full educational topic with realistic follow-up questions.
# ---------------------------------------------------------------------------
MULTI_TURN_SCENARIOS: list[list[dict[str, str]]] = [
    # A: New business owner — full VAT onboarding journey
    [
        {"message": "I'm starting a new retail business in Uganda. Do I need to register for VAT?", "locale": "en"},
        {"message": "My annual turnover will be about 120 million UGX. Does that change things?", "locale": "en"},
        {"message": "What documents do I need to register for VAT with URA?", "locale": "en"},
        {"message": "Once registered, how often must I file VAT returns?", "locale": "en"},
        {"message": "What happens if I miss a VAT filing deadline?", "locale": "en"},
    ],
    # B: New employer — full PAYE education
    [
        {"message": "I just hired my first employee at 4.2 million UGX per month. What PAYE do I deduct?", "locale": "en"},
        {"message": "Do I also need to deduct NSSF from their salary?", "locale": "en"},
        {"message": "When must I remit the PAYE to URA each month?", "locale": "en"},
        {"message": "What records must I keep for my employees' payroll taxes?", "locale": "en"},
    ],
    # C: Luganda — EFRIS onboarding
    [
        {"message": "EFRIS kye ki era tikiri kugigyibwa omukono?", "locale": "lg"},
        {"message": "Nnyinza ntya okwewandiisa ku EFRIS portal?", "locale": "lg"},
        {"message": "Nsaanidde okufulumya invoice zonna nga nkozesa EFRIS?", "locale": "lg"},
    ],
    # D: IT consultant — withholding tax
    [
        {"message": "I provide IT consulting services. My client says they must withhold tax from my invoice. Is that correct?", "locale": "en"},
        {"message": "What is the withholding tax rate on consulting fees?", "locale": "en"},
        {"message": "How do I get a credit for this withholding tax when I file my returns?", "locale": "en"},
        {"message": "Can I apply for a withholding tax exemption certificate?", "locale": "en"},
    ],
    # E: Kiswahili — TIN registration journey
    [
        {"message": "Ninahitaji TIN ili niweze kufungua akaunti ya benki kwa biashara yangu.", "locale": "sw"},
        {"message": "Ninawezaje kupata TIN haraka?", "locale": "sw"},
        {"message": "Ni nyaraka zipi ninazohitaji kwa usajili wa TIN?", "locale": "sw"},
    ],
    # F: Owino Market small trader — presumptive tax
    [
        {"message": "I sell secondhand clothes at Owino Market. My sales are about 40 million UGX a year. What tax should I pay?", "locale": "en"},
        {"message": "What is presumptive tax? How is it different from income tax?", "locale": "en"},
        {"message": "How do I pay presumptive tax to URA?", "locale": "en"},
    ],
    # G: Importer — customs journey
    [
        {"message": "I import electronics from China. How is import duty calculated?", "locale": "en"},
        {"message": "What is customs value — do I use invoice price or something else?", "locale": "en"},
        {"message": "Are any electronics duty-free in Uganda?", "locale": "en"},
        {"message": "What documents must I present at customs for clearance?", "locale": "en"},
    ],
    # H: Landlord — rental property tax
    [
        {"message": "I own two apartments that I rent out. Do I pay tax on the rental income?", "locale": "en"},
        {"message": "What is the rental tax rate for residential property?", "locale": "en"},
        {"message": "Must I register with URA specifically for rental income?", "locale": "en"},
    ],
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class RequestResult:
    scenario: str
    conversation_id: str
    turn: int
    locale: str
    topic: str
    query: str
    status_code: int
    latency_s: float
    retrieval_mode: str
    sources_count: int
    answered: bool
    error: str | None = None


@dataclass
class PhaseResult:
    phase: str
    profile: str
    total_requests: int
    successful: int
    failed: int
    error_rate_pct: float
    duration_s: float
    rps: float
    p50_s: float
    p95_s: float
    p99_s: float
    max_s: float
    grounded_pct: float
    locale_breakdown: dict[str, int] = field(default_factory=dict)
    topic_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)
    nfr_p95_pass: bool = False
    nfr_error_pass: bool = False
    samples: list[RequestResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
async def _chat_once(
    client: httpx.AsyncClient,
    message: str,
    conversation_id: str,
    locale: str = "en",
    turn: int = 0,
    topic: str = "general",
    scenario: str = "unknown",
) -> RequestResult:
    payload = {
        "message": message,
        "conversation_id": conversation_id,
        "locale": locale,
    }
    await _await_rate_slot()
    # Restarted on every retry below: this measures how long the server took,
    # and backoff plus a pacer wait is time the client chose to spend. Leaving
    # it running across retries would fold that into the p95 the NFR gate
    # reads, so a retried request would look like a slow one.
    t0 = time.perf_counter()
    attempts = 0
    max_attempts = _RETRY_MAX if _ENABLE_RETRY else 0

    while True:
        try:
            resp = await client.post(
                CHAT_ENDPOINT, json=payload, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT
            )
            latency = time.perf_counter() - t0

            if resp.status_code == 429 and attempts < max_attempts:
                attempts += 1
                backoff = (_RETRY_BACKOFF_BASE ** attempts) + random.uniform(0.5, 1.5)
                await asyncio.sleep(backoff)
                # A retry is a new request and must take its turn in the global
                # pacer like any other. Skipping it let a retry overtake the
                # workers waiting their slot — at exactly the moment the server
                # had just said it was over its limit.
                await _await_rate_slot()
                t0 = time.perf_counter()
                continue

            if resp.status_code not in {200, 201}:
                return RequestResult(
                    scenario=scenario,
                    conversation_id=conversation_id,
                    turn=turn,
                    locale=locale,
                    topic=topic,
                    query=message[:80],
                    status_code=resp.status_code,
                    latency_s=latency,
                    retrieval_mode="error",
                    sources_count=0,
                    answered=False,
                    error=f"HTTP {resp.status_code}",
                )
            body = resp.json()
            mode = body.get("retrieval_mode", body.get("mode", "unknown"))
            sources = len(body.get("sources", body.get("context", [])))
            # ``ChatResponse.reply`` is the field the API actually returns
            # (App/backend/app/models.py). Reading only response/answer/message
            # meant this was False for every successful request. Nothing reports
            # it today, so the effect was latent rather than a wrong published
            # number — but it would have quietly zeroed any metric built on it.
            answered = bool(
                body.get("reply")
                or body.get("response")
                or body.get("answer")
                or body.get("message")
            )
            return RequestResult(
                scenario=scenario,
                conversation_id=conversation_id,
                turn=turn,
                locale=locale,
                topic=topic,
                query=message[:80],
                status_code=resp.status_code,
                latency_s=latency,
                retrieval_mode=mode,
                sources_count=sources,
                answered=answered,
            )
        except Exception as exc:
            if attempts < max_attempts and isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout)):
                attempts += 1
                backoff = (_RETRY_BACKOFF_BASE ** attempts) + random.uniform(0.5, 1.5)
                await asyncio.sleep(backoff)
                await _await_rate_slot()
                t0 = time.perf_counter()
                continue
            return RequestResult(
                scenario=scenario,
                conversation_id=conversation_id,
                turn=turn,
                locale=locale,
                topic=topic,
                query=message[:80],
                status_code=0,
                latency_s=time.perf_counter() - t0,
                retrieval_mode="error",
                sources_count=0,
                answered=False,
                error=str(exc)[:120],
            )


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------
async def run_flat_phase(
    phase_name: str,
    profile: str,
    requests: list[dict[str, Any]],
    concurrency: int,
) -> PhaseResult:
    """Run a flat (non-conversational) batch of independent chat requests."""
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(req: dict[str, Any]) -> RequestResult:
        async with semaphore:
            async with httpx.AsyncClient() as client:
                return await _chat_once(client, **req)

    t_start = time.perf_counter()
    tasks = [bounded(r) for r in requests]
    results: list[RequestResult] = list(await asyncio.gather(*tasks))
    duration = time.perf_counter() - t_start

    return _aggregate(phase_name, profile, results, duration)


async def run_multi_turn_phase(
    phase_name: str,
    profile: str,
    scenarios: list[list[dict[str, str]]],
    concurrency: int,
) -> PhaseResult:
    """Run multi-turn conversation scenarios with session state."""
    semaphore = asyncio.Semaphore(concurrency)
    all_results: list[RequestResult] = []

    async def run_scenario(turns: list[dict[str, str]], scenario_id: str) -> list[RequestResult]:
        conv_id = f"edu_load_{scenario_id}_{uuid.uuid4().hex[:6]}"
        scenario_results: list[RequestResult] = []
        async with semaphore:
            async with httpx.AsyncClient() as client:
                for turn_idx, turn in enumerate(turns):
                    result = await _chat_once(
                        client,
                        message=turn["message"],
                        conversation_id=conv_id,
                        locale=turn.get("locale", "en"),
                        turn=turn_idx,
                        topic="multi_turn",
                        scenario=scenario_id,
                    )
                    scenario_results.append(result)
                    # Inter-turn pause to simulate realistic user think time
                    if turn_idx < len(turns) - 1:
                        await asyncio.sleep(0.4)
        return scenario_results

    t_start = time.perf_counter()
    tasks = [
        run_scenario(turns, f"scen_{i:02d}")
        for i, turns in enumerate(scenarios)
    ]
    scenario_batches = await asyncio.gather(*tasks)
    for batch in scenario_batches:
        all_results.extend(batch)
    duration = time.perf_counter() - t_start

    return _aggregate(phase_name, profile, all_results, duration)


def _aggregate(
    phase: str, profile: str, results: list[RequestResult], duration: float
) -> PhaseResult:
    latencies = [r.latency_s for r in results]
    successes = [r for r in results if r.status_code in {200, 201}]
    failures = [r for r in results if r.status_code not in {200, 201}]
    grounded = [r for r in successes if r.retrieval_mode not in {"abstained", "error", "unknown"}]

    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)

    def pct(p: float) -> float:
        if n == 0:
            return 0.0
        idx = max(0, min(n - 1, math.floor(p * n)))
        return round(latencies_sorted[idx], 3)

    locale_breakdown: dict[str, int] = {}
    for r in successes:
        locale_breakdown[r.locale] = locale_breakdown.get(r.locale, 0) + 1

    topic_latencies: dict[str, list[float]] = {}
    for r in results:
        topic_latencies.setdefault(r.topic, []).append(r.latency_s)
    topic_breakdown = {
        t: {
            "p95_s": round(sorted(lats)[max(0, math.floor(0.95 * len(lats)))], 3),
            "count": len(lats),
        }
        for t, lats in topic_latencies.items()
    }

    error_pct = round(len(failures) / max(1, n) * 100.0, 2)
    p95 = pct(0.95)

    return PhaseResult(
        phase=phase,
        profile=profile,
        total_requests=n,
        successful=len(successes),
        failed=len(failures),
        error_rate_pct=error_pct,
        duration_s=round(duration, 2),
        rps=round(n / max(0.001, duration), 2),
        p50_s=pct(0.50),
        p95_s=p95,
        p99_s=pct(0.99),
        max_s=round(max(latencies, default=0.0), 3),
        grounded_pct=round(len(grounded) / max(1, len(successes)) * 100.0, 1),
        locale_breakdown=locale_breakdown,
        topic_breakdown=topic_breakdown,
        nfr_p95_pass=p95 <= NFR_P95_S,
        nfr_error_pass=error_pct <= NFR_MAX_ERROR_PCT,
        samples=results,
    )


# ---------------------------------------------------------------------------
# Phase factories
# ---------------------------------------------------------------------------
def _build_flat_requests(
    topics: list[str] | None = None, repeats: int = 1
) -> list[dict[str, Any]]:
    """Build a flat list of single-turn requests from the query bank."""
    reqs: list[dict[str, Any]] = []
    for topic, locales in QUERY_BANK.items():
        if topics and topic not in topics:
            continue
        for locale, queries in locales.items():
            for intent, message in queries:
                for _ in range(repeats):
                    reqs.append({
                        "message": message,
                        "conversation_id": f"flat_{topic}_{intent}_{locale}_{uuid.uuid4().hex[:6]}",
                        "locale": locale,
                        "turn": 0,
                        "topic": topic,
                        "scenario": f"flat_{topic}_{locale}",
                    })
    return reqs


async def phase_smoke() -> list[PhaseResult]:
    """Smoke: one English request per topic cluster, C=1."""
    print("[smoke] Probing health + one request per topic cluster...")
    async with httpx.AsyncClient() as client:
        h = await client.get(HEALTH_ENDPOINT, timeout=15.0)
        r = await client.get(READY_ENDPOINT, timeout=20.0)
        print(f"  health={h.json()}")
        print(f"  ready={r.json()}")

    reqs = _build_flat_requests(repeats=1)
    seen_topics: set[str] = set()
    single_reqs = []
    for req in reqs:
        if req["topic"] not in seen_topics and req["locale"] == "en":
            single_reqs.append(req)
            seen_topics.add(req["topic"])

    result = await run_flat_phase(
        "smoke", "C=1, 1×topic, English only", single_reqs, concurrency=1
    )
    return [result]


async def phase_load(vu: int = 4) -> list[PhaseResult]:
    """Load: all clusters, all locales, C=vu, repeats=2."""
    print(f"[load] All topic clusters, all locales, VU={vu}, 2×...")
    reqs = _build_flat_requests(repeats=2)
    result = await run_flat_phase(
        "load", f"C={vu}, all-topics, all-locales, 2×", reqs, concurrency=vu
    )
    return [result]


async def phase_spike(peak_vu: int = 20) -> list[PhaseResult]:
    """Spike: ramp 1→peak→1, measuring p95 degradation at each tier."""
    results: list[PhaseResult] = []
    reqs_bank = _build_flat_requests(repeats=1)
    for vu in [1, max(1, peak_vu // 4), max(1, peak_vu // 2), peak_vu, max(1, peak_vu // 2), 1]:
        sample = reqs_bank[: min(len(reqs_bank), max(10, vu * 3))]
        label = f"spike_vu{vu}"
        print(f"  [spike] VU={vu}, {len(sample)} requests...")
        r = await run_flat_phase(label, f"spike ramp VU={vu}", sample, concurrency=vu)
        results.append(r)
    return results


async def phase_volume(sessions: int = 12) -> list[PhaseResult]:
    """Volume: multi-turn educational conversation sessions."""
    print(f"[volume] {sessions} multi-turn sessions, C=4...")
    expanded = [
        MULTI_TURN_SCENARIOS[i % len(MULTI_TURN_SCENARIOS)] for i in range(sessions)
    ]
    result = await run_multi_turn_phase(
        "volume", f"{sessions} multi-turn sessions, C=4", expanded, concurrency=4
    )
    return [result]


async def phase_stress(max_vu: int = 24) -> list[PhaseResult]:
    """Stress: stepped ramp 2→4→8→16→max_vu, finds capacity cliff."""
    results: list[PhaseResult] = []
    reqs = _build_flat_requests(repeats=2)
    for vu in [2, 4, 8, 16, max_vu]:
        batch_size = min(len(reqs), vu * 4)
        sample = reqs[:batch_size]
        print(f"  [stress] VU={vu}, {batch_size} requests...")
        r = await run_flat_phase(f"stress_vu{vu}", f"stress step VU={vu}", sample, concurrency=vu)
        results.append(r)
    return results


async def phase_topic(topics: list[str], vu: int = 6) -> list[PhaseResult]:
    """Topic deep dive: all locales, repeats=3."""
    print(f"[topic] Deep dive: {topics}, VU={vu}, 3×...")
    reqs = _build_flat_requests(topics=topics, repeats=3)
    if not reqs:
        print(f"  No requests found for topics {topics}")
        print(f"  Available topics: {sorted(QUERY_BANK.keys())}")
        return []
    result = await run_flat_phase(
        f"topic_{'_'.join(topics)}", f"C={vu}, topics={topics}, 3×", reqs, concurrency=vu
    )
    return [result]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _badge(passed: bool) -> str:
    return "✅ PASS" if passed else "❌ FAIL"


def print_phase_report(pr: PhaseResult, verbose: bool = False) -> None:
    w = 80
    print("\n" + "=" * w)
    print(f"  PHASE   : {pr.phase}")
    print(f"  Profile : {pr.profile}")
    print("=" * w)
    print(f"  Requests : {pr.total_requests:4d}  |  OK: {pr.successful}  |  Err: {pr.failed}")
    print(f"  Duration : {pr.duration_s:.1f}s  |  RPS: {pr.rps:.2f}")
    print(
        f"  Latency  : p50={pr.p50_s:.3f}s  p95={pr.p95_s:.3f}s  "
        f"p99={pr.p99_s:.3f}s  max={pr.max_s:.3f}s"
    )
    print(f"  Error %  : {pr.error_rate_pct:.1f}%    |  Grounded%: {pr.grounded_pct:.1f}%")
    print(f"  NFR p95  : {_badge(pr.nfr_p95_pass)}  (≤ {NFR_P95_S}s)")
    print(f"  NFR err  : {_badge(pr.nfr_error_pass)}  (≤ {NFR_MAX_ERROR_PCT}%)")

    if pr.locale_breakdown:
        print(
            "  Locales  : "
            + "  |  ".join(f"{k}={v}" for k, v in sorted(pr.locale_breakdown.items()))
        )

    # Retrieval mode distribution
    modes: dict[str, int] = {}
    for r in pr.samples:
        modes[r.retrieval_mode] = modes.get(r.retrieval_mode, 0) + 1
    if modes:
        print(
            "  Retrieval: "
            + "  |  ".join(f"{k}={v}" for k, v in sorted(modes.items()))
        )

    if verbose and pr.topic_breakdown:
        print("\n  Topic p95 breakdown:")
        for topic, stats in sorted(pr.topic_breakdown.items(), key=lambda x: -x[1]["p95_s"]):
            print(f"    {topic:<20} p95={stats['p95_s']:.3f}s  n={stats['count']}")

    if verbose:
        errors = [r for r in pr.samples if r.error]
        if errors:
            print(f"\n  Sample errors ({min(5, len(errors))} of {len(errors)}):")
            for e in errors[:5]:
                print(f"    [{e.locale}] {e.topic} | {e.query[:55]!r} → {e.error}")
    print()


def print_summary_table(phases: list[PhaseResult]) -> None:
    w = 115
    print("\n" + "=" * w)
    print(
        f"{'Phase':<24} | {'Profile':<36} | {'Reqs':>5} | {'RPS':>5} | "
        f"{'p95(s)':>7} | {'Err%':>5} | {'Grnd%':>6} | {'NFR':>7}"
    )
    print("=" * w)
    for pr in phases:
        nfr = "✅" if (pr.nfr_p95_pass and pr.nfr_error_pass) else "❌"
        print(
            f"{pr.phase:<24} | {pr.profile:<36} | {pr.total_requests:>5} | {pr.rps:>5.2f} | "
            f"{pr.p95_s:>7.3f} | {pr.error_rate_pct:>5.1f} | {pr.grounded_pct:>6.1f} | {nfr:>7}"
        )
    print("=" * w)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
AVAILABLE_PHASES = (
    {"smoke", "load", "spike", "volume", "stress", "all"} | set(QUERY_BANK.keys())
)


async def main(args: argparse.Namespace) -> int:
    global _ENABLE_RETRY, _INTER_REQUEST_DELAY_S
    if getattr(args, "no_retry", False):
        _ENABLE_RETRY = False
    if hasattr(args, "delay") and args.delay is not None:
        _INTER_REQUEST_DELAY_S = float(args.delay)

    requested = {p.strip() for p in args.phase.split(",")}
    if "all" in requested:
        requested = {"smoke", "load", "volume", "spike", "stress"}

    topic_phases = requested & set(QUERY_BANK.keys())
    standard_phases = requested - topic_phases

    all_results: list[PhaseResult] = []

    if "smoke" in standard_phases:
        all_results.extend(await phase_smoke())

    if "load" in standard_phases:
        all_results.extend(await phase_load(vu=args.vu))

    if "volume" in standard_phases:
        all_results.extend(await phase_volume(sessions=args.sessions))

    if "spike" in standard_phases:
        all_results.extend(await phase_spike(peak_vu=args.spike_peak))

    if "stress" in standard_phases:
        all_results.extend(await phase_stress(max_vu=args.stress_max))

    if topic_phases:
        all_results.extend(await phase_topic(list(topic_phases), vu=args.vu))

    if not all_results:
        print(
            f"No phases matched. Available: {sorted(AVAILABLE_PHASES)}",
            file=sys.stderr,
        )
        return 1

    for pr in all_results:
        print_phase_report(pr, verbose=args.verbose)

    print_summary_table(all_results)

    all_pass = all(pr.nfr_p95_pass and pr.nfr_error_pass for pr in all_results)
    print(f"\nOverall NFR gate: {'✅ ALL PASS' if all_pass else '❌ SOME FAIL'}")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        export = []
        for pr in all_results:
            d = asdict(pr)
            d.pop("samples", None)  # omit raw samples from JSON summary
            export.append(d)
        with open(args.out, "w") as fh:
            json.dump(
                {
                    "phases": export,
                    "nfr_p95_s": NFR_P95_S,
                    "nfr_error_pct": NFR_MAX_ERROR_PCT,
                    "base_url": BASE_URL,
                },
                fh,
                indent=2,
            )
        print(f"Report written → {args.out}")

    return 0 if all_pass else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Tax Education Conversation Load & Volume Suite — URA Chatbot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--phase",
        default="smoke",
        help=(
            "Comma-separated phases: smoke, load, volume, spike, stress, all, "
            + ", ".join(sorted(QUERY_BANK.keys()))
        ),
    )
    p.add_argument(
        "--vu", type=int, default=4,
        help="Virtual users (concurrency) for load / topic phases",
    )
    p.add_argument(
        "--sessions", type=int, default=12,
        help="Number of multi-turn sessions for volume phase",
    )
    p.add_argument(
        "--spike-peak", type=int, default=20,
        help="Peak VU for spike ramp",
    )
    p.add_argument(
        "--stress-max", type=int, default=24,
        help="Maximum VU for stress ramp",
    )
    p.add_argument(
        "--delay", type=float, default=_DEFAULT_DELAY_S,
        help="Inter-request delay floor per VU in seconds (default: 2.2s)",
    )
    p.add_argument(
        "--no-retry", action="store_true",
        help="Disable retry on HTTP 429 to measure raw rate-limiting behaviour",
    )
    p.add_argument(
        "--out", default="",
        help="Path for JSON report output (e.g. results/tax_edu_load.json)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Print topic p95 breakdown and error samples",
    )
    return p


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args)))
