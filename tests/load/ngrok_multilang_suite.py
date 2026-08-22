"""
Multilingual load / spike / stress / volume suite — URA Chatbot via ngrok.

Complements tests/load/k6-chat-slo.js, which is English-only, latency-only
and hits /v1/chat with a fixed `locale: "en"`. This suite exists because the
questions that matter for this deployment are not answered by latency alone:

  * does the LLM still generate real text under concurrency, or start
    abstaining / truncating / erroring?
  * does auto language detection still fire when the box is saturated?
    (service.py only auto-detects when the caller sends locale == "en", so
    every request here deliberately sends "en" and asserts on the locale the
    API reports back)
  * does an English question keep getting an English answer, a Luganda
    question a Luganda answer, and a Kiswahili question a Kiswahili one —
    under load, not just on a quiet box?

Reply-language verification is deliberately NOT done with the app's own
app.query.detect_language: lingua classifies this deployment's own Luganda
replies as `nyn`, so scoring the app's output with the app's own detector
would report a false failure. `classify_reply()` below is an independent
marker-density discriminator, and every phase keeps verbatim reply samples
so a human can confirm the automated verdict.

Run (see --help for phases):
    python tests/load/ngrok_multilang_suite.py --phase functional
    python tests/load/ngrok_multilang_suite.py --phase all --out results.json

Standards: ISO/IEC 25010:2023 §2 (Performance Efficiency), week08 NFR-01.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import random
import re
import statistics
import time
import wave
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

DEFAULT_BASE = "https://struttingly-nongeological-briella.ngrok-free.dev/api"


# ---------------------------------------------------------------------------
# Query bank — same intents across all three locales so a cross-language
# comparison is like-for-like. Mixed on purpose: "register for TIN" hits the
# guided-workflow router, the rate/EFRIS ones hit hybrid retrieval, and the
# PAYE one hits the calculator tool — so a phase exercises more than one
# code path rather than measuring the workflow short-circuit six times.
# ---------------------------------------------------------------------------
QUERIES: dict[str, list[tuple[str, str]]] = {
    "en": [
        ("vat_rate", "What is the current VAT rate in Uganda?"),
        ("tin_register", "How do I register for a TIN?"),
        ("penalties", "What are the penalties for filing my return late?"),
        ("efris", "What is EFRIS and who must use it?"),
        ("returns", "How do I file my annual tax returns?"),
        ("withholding", "What is withholding tax?"),
    ],
    "lg": [
        ("vat_rate", "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?"),
        ("tin_register", "Nnyinza ntya okwewandiisa okufuna TIN?"),
        ("penalties", "Bibonerezo ki ebiriwo bw'olwawo okuwaayo alipoota y'omusolo?"),
        ("efris", "EFRIS kye ki era ani alina okugikozesa?"),
        ("returns", "Nnyinza ntya okuwaayo alipoota y'omusolo eya buli mwaka?"),
        ("withholding", "Omusolo ogukwatibwa nga tennasasulwa kye ki?"),
    ],
    "sw": [
        ("vat_rate", "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?"),
        ("tin_register", "Ninawezaje kujisajili kupata namba ya TIN?"),
        ("penalties", "Ni adhabu gani kwa kuchelewa kuwasilisha marejesho ya kodi?"),
        ("efris", "EFRIS ni nini na ni nani anapaswa kuitumia?"),
        ("returns", "Ninawezaje kuwasilisha marejesho ya kodi ya mwaka?"),
        ("withholding", "Kodi ya zuio ni nini?"),
    ],
}

# Longer, multi-clause questions for the volume phase — bigger request
# payloads and answers that cannot be served from a short cached string.
VOLUME_QUERIES: dict[str, str] = {
    "en": (
        "I run a small retail business in Kampala that imports electronics from Dubai "
        "and also sells locally. My annual turnover is around 180 million shillings. "
        "Do I need to register for VAT, what customs duties apply to my imports, "
        "how does EFRIS affect my invoicing, and what records must I keep?"
    ),
    "lg": (
        "Nnina bizinensi entono mu Kampala gye ntundiramu ebyuma bya leediyo n'ebirala "
        "bye nnyingiza okuva e Dubai, era ntunda ne wano mu ggwanga. Buli mwaka nfuna "
        "obukadde nga kikumi mu kinaana. Nsaanidde okwewandiisa ku VAT? Musolo ki "
        "ogusasulwa ku byo bye nnyingiza, era EFRIS ekosa etya invoice zange?"
    ),
    "sw": (
        "Nina biashara ndogo ya rejareja mjini Kampala ambayo inaagiza vifaa vya "
        "kielektroniki kutoka Dubai na pia kuuza hapa nchini. Mapato yangu ya mwaka "
        "ni takriban milioni 180. Je, ninahitaji kujisajili kwa VAT, ni ushuru gani "
        "wa forodha unaotumika, na EFRIS inaathiri vipi ankara zangu?"
    ),
}


# ---------------------------------------------------------------------------
# Independent reply-language classifier
# ---------------------------------------------------------------------------
# Marker sets chosen to discriminate the three SUPPORTED_LOCALES from each
# other, not to be a general-purpose language ID. Luganda and Kiswahili are
# both Bantu and share some surface forms ("na", "ku"), so the markers below
# avoid the shared ones and lean on each language's distinctive morphology:
# Luganda's okw-/oku- infinitives, omu-/eby- noun classes and apostrophised
# elisions; Kiswahili's ni-/una-/ku- verb prefixes and its own function words.
_MARKERS: dict[str, tuple[str, ...]] = {
    "en": (
        "the", "and", "you", "your", "for", "is", "are", "to", "of", "a",
        "must", "with", "how", "what", "tax", "please", "if", "on", "can",
    ),
    "lg": (
        "oba", "nga", "era", "eri", "kye", "bye", "gwa", "eby", "omu", "aba",
        "oku", "okw", "ekya", "eky", "bw", "ente", "ssente", "musolo", "yo",
        "waayo", "lina", "nnyinza", "okuva", "wano", "ggwanga", "buli",
    ),
    "sw": (
        "ya", "wa", "kwa", "ni", "katika", "una", "kama", "hii", "hizo",
        "kodi", "lazima", "unaweza", "ambayo", "yako", "kuwa", "je",
        "asilimia", "marejesho", "biashara", "nchini", "na",
    ),
}

_WORD_RE = re.compile(r"[A-Za-z']+")


def classify_reply(text: str) -> tuple[str, dict[str, float]]:
    """Return (best_locale, per-locale marker density) for *text*.

    Density is markers-hit / total-words, so a long answer is not
    automatically scored higher than a short one. Returns "unknown" when no
    marker set clears a floor, which keeps an empty or abstained reply from
    being silently counted as a correct-language answer.
    """
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    if not words:
        return "unknown", {}
    scores: dict[str, float] = {}
    for loc, markers in _MARKERS.items():
        hits = sum(1 for w in words if w in markers)
        scores[loc] = hits / len(words)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 0.02:
        return "unknown", scores
    return best, scores


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------
@dataclass
class Record:
    phase: str
    started_at: float
    latency_s: float
    status: int
    expect_locale: str
    intent: str
    reported_locale: str | None = None
    retrieval_mode: str | None = None
    model: str | None = None
    n_sources: int = 0
    reply_len: int = 0
    reply_lang: str | None = None
    faithfulness: float | None = None
    error: str | None = None
    reply_sample: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None

    @property
    def locale_ok(self) -> bool:
        return self.ok and self.reported_locale == self.expect_locale

    @property
    def lang_ok(self) -> bool:
        return self.ok and self.reply_lang == self.expect_locale


@dataclass
class PhaseResult:
    name: str
    description: str
    started_at: str
    wall_s: float
    records: list[Record] = field(default_factory=list)


async def send_one(
    client: httpx.AsyncClient,
    base: str,
    phase: str,
    expect: str,
    intent: str,
    message: str,
    keep_sample: bool,
) -> Record:
    t0 = time.perf_counter()
    started = time.time()
    try:
        r = await client.post(
            f"{base}/v1/chat",
            # locale "en" on every request: service.py only runs auto-detection
            # when the caller sends "en", so this is the path under test.
            json={"message": message, "locale": "en"},
            headers={"X-Session-ID": f"loadtest-{phase}-{expect}-{random.randint(0, 10**9)}"},
        )
        dt = time.perf_counter() - t0
        rec = Record(
            phase=phase,
            started_at=started,
            latency_s=dt,
            status=r.status_code,
            expect_locale=expect,
            intent=intent,
        )
        if r.status_code == 200:
            body = r.json()
            reply = body.get("reply") or ""
            rec.reported_locale = body.get("locale")
            rec.retrieval_mode = body.get("retrieval_mode")
            rec.model = body.get("model")
            rec.n_sources = len(body.get("sources") or [])
            rec.reply_len = len(reply)
            rec.faithfulness = body.get("faithfulness_score")
            rec.reply_lang, _ = classify_reply(reply)
            if keep_sample:
                rec.reply_sample = reply[:400]
        else:
            rec.error = f"http_{r.status_code}"
        return rec
    except Exception as exc:  # noqa: BLE001
        return Record(
            phase=phase,
            started_at=started,
            latency_s=time.perf_counter() - t0,
            status=0,
            expect_locale=expect,
            intent=intent,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )


def _pick(locales: list[str]) -> tuple[str, str, str]:
    loc = random.choice(locales)
    intent, msg = random.choice(QUERIES[loc])
    return loc, intent, msg


async def run_constant(
    client: httpx.AsyncClient,
    base: str,
    phase: str,
    vus: int,
    duration_s: float,
    locales: list[str],
    think_s: float = 0.0,
    samples_per_locale: int = 2,
) -> list[Record]:
    """N concurrent virtual users hammering for *duration_s*."""
    records: list[Record] = []
    deadline = time.perf_counter() + duration_s
    sampled: dict[str, int] = {loc: 0 for loc in locales}

    async def vu() -> None:
        while time.perf_counter() < deadline:
            loc, intent, msg = _pick(locales)
            keep = sampled.get(loc, 99) < samples_per_locale
            if keep:
                sampled[loc] = sampled.get(loc, 0) + 1
            rec = await send_one(client, base, phase, loc, intent, msg, keep)
            records.append(rec)
            if think_s:
                await asyncio.sleep(think_s)

    await asyncio.gather(*(vu() for _ in range(vus)), return_exceptions=True)
    return records


async def run_ramp(
    client: httpx.AsyncClient,
    base: str,
    phase: str,
    stages: list[tuple[int, float]],
    locales: list[str],
) -> list[Record]:
    """Ramp concurrency through (target_vus, duration_s) stages.

    Each VU is an independent task with its own stop flag, so scaling down
    lets in-flight requests finish instead of cancelling them mid-generation
    (a cancelled request would otherwise be recorded as a client error and
    inflate the error rate with our own teardown).
    """
    records: list[Record] = []
    tasks: list[tuple[asyncio.Task, dict]] = []

    async def vu(flag: dict) -> None:
        while not flag["stop"]:
            loc, intent, msg = _pick(locales)
            records.append(await send_one(client, base, phase, loc, intent, msg, False))

    for target, dur in stages:
        while len(tasks) < target:
            flag = {"stop": False}
            tasks.append((asyncio.create_task(vu(flag)), flag))
        while len(tasks) > target:
            task, flag = tasks.pop()
            flag["stop"] = True
        await asyncio.sleep(dur)

    for _, flag in tasks:
        flag["stop"] = True
    if tasks:
        await asyncio.gather(*(t for t, _ in tasks), return_exceptions=True)
    return records


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
async def phase_functional(client: httpx.AsyncClient, base: str) -> PhaseResult:
    """Every intent × every locale, sequential, paced under the 30/min limit."""
    t0 = time.perf_counter()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    records: list[Record] = []
    for loc in ("en", "lg", "sw"):
        for intent, msg in QUERIES[loc]:
            records.append(await send_one(client, base, "functional", loc, intent, msg, True))
            await asyncio.sleep(2.2)  # ~27 req/min, just under the limiter
    return PhaseResult("functional", "correctness baseline, all intents x all locales",
                       started, time.perf_counter() - t0, records)


async def phase_ratelimit(client: httpx.AsyncClient, base: str) -> PhaseResult:
    """Deliberately exceed RATE_LIMIT, then confirm the service recovers."""
    t0 = time.perf_counter()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    records = await run_constant(client, base, "ratelimit", vus=12, duration_s=45,
                                 locales=["en", "lg", "sw"])
    await asyncio.sleep(65)  # let the per-minute window roll over
    records += await run_constant(client, base, "ratelimit_recovery", vus=1,
                                  duration_s=20, locales=["en"])
    return PhaseResult("ratelimit", "burst past the limiter, then verify recovery",
                       started, time.perf_counter() - t0, records)


async def phase_load(client: httpx.AsyncClient, base: str) -> PhaseResult:
    """Sustained expected-traffic concurrency."""
    t0 = time.perf_counter()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    records = await run_constant(client, base, "load", vus=4, duration_s=180,
                                 locales=["en", "lg", "sw"], think_s=1.0)
    return PhaseResult("load", "4 concurrent users, 3 min, mixed locales",
                       started, time.perf_counter() - t0, records)


async def phase_spike(client: httpx.AsyncClient, base: str) -> PhaseResult:
    """Idle -> sudden burst -> idle."""
    t0 = time.perf_counter()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    records = await run_ramp(client, base, "spike",
                             stages=[(1, 20), (20, 60), (1, 30)],
                             locales=["en", "lg", "sw"])
    return PhaseResult("spike", "1 -> 20 -> 1 VUs, abrupt",
                       started, time.perf_counter() - t0, records)


async def phase_stress(client: httpx.AsyncClient, base: str) -> PhaseResult:
    """Step the load up until something gives."""
    t0 = time.perf_counter()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    records = await run_ramp(client, base, "stress",
                             stages=[(2, 40), (4, 40), (8, 40), (16, 40), (32, 60)],
                             locales=["en", "lg", "sw"])
    return PhaseResult("stress", "stepped ramp 2->4->8->16->32 VUs",
                       started, time.perf_counter() - t0, records)


async def phase_volume(client: httpx.AsyncClient, base: str) -> PhaseResult:
    """Long, multi-clause questions — big payloads, long generations."""
    t0 = time.perf_counter()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    records: list[Record] = []
    deadline = time.perf_counter() + 240
    sampled: set[str] = set()

    async def vu() -> None:
        while time.perf_counter() < deadline:
            loc = random.choice(["en", "lg", "sw"])
            keep = loc not in sampled
            sampled.add(loc)
            records.append(
                await send_one(client, base, "volume", loc, "long_multiclause",
                               VOLUME_QUERIES[loc], keep)
            )

    await asyncio.gather(*(vu() for _ in range(6)), return_exceptions=True)
    return PhaseResult("volume", "6 VUs x 4 min of long multi-clause questions",
                       started, time.perf_counter() - t0, records)


async def phase_subsystems(client: httpx.AsyncClient, base: str) -> PhaseResult:
    """Prove each backing service is actually in the request path.

    "The container is healthy" is not evidence a service is being used —
    the deployment degrades silently in every one of these directions:
    Qdrant falls back to Cloudflare Vectorize, the semantic cache falls back
    to an in-process dict, Whisper-SALT falls through to the LoRA adapters,
    and Spark-TTS-SALT falls through to edge-tts. So each check below asserts
    on the field that names the tier that actually served the request, not on
    a 200.
    """
    t0 = time.perf_counter()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    recs: list[Record] = []

    def rec(intent: str, ok: bool, extra: dict, latency: float = 0.0) -> None:
        recs.append(Record(phase="subsystems", started_at=time.time(), latency_s=latency,
                           status=200 if ok else 0, expect_locale="en", intent=intent,
                           error=None if ok else "check_failed", extra=extra))

    # --- Qdrant: retrieval-bound questions must come back vector-backed ---
    r = await client.get(f"{base}/ready")
    ready = r.json() if r.status_code == 200 else {}
    rec("qdrant_ready", ready.get("retrieval_mode") in ("hybrid", "vector"),
        {"retrieval_mode": ready.get("retrieval_mode"), "capabilities": ready.get("capabilities")})

    # Deliberately NOT the vat_rate/tin_register questions from QUERIES: those
    # are answered by the calculator and guided-workflow short-circuits, which
    # return before the retriever is ever consulted, so a "0 sources" result
    # there says nothing about Qdrant. These reach the RAG route.
    for loc, msg in (("en", "What is EFRIS and who must use it?"),
                     ("lg", "EFRIS kye ki era ani alina okugikozesa?"),
                     ("sw", "EFRIS ni nini na ni nani anapaswa kuitumia?")):
        t = time.perf_counter()
        cr = await send_one(client, base, "subsystems", loc, f"qdrant_retrieval_{loc}", msg, True)
        cr.intent = f"qdrant_retrieval_{loc}"
        cr.status = 200 if (cr.ok and cr.n_sources > 0) else 0
        cr.extra = {"retrieval_mode": cr.retrieval_mode, "n_sources": cr.n_sources,
                    "grounded": cr.n_sources > 0}
        cr.latency_s = time.perf_counter() - t
        recs.append(cr)

    # --- Redis: same question twice; the semantic cache must serve #2 ---
    # Must be a question the LLM actually generates for. The deterministic
    # routers are already sub-second and are never cached, so timing one of
    # those measures nothing (an earlier version of this check used the VAT
    # rate question and reported a meaningless 1.0x "speedup").
    # Novel every run: a fixed question would already be in the cache from the
    # Qdrant check above (or from a previous run), so BOTH calls would be hits
    # and the measured speedup would be a meaningless 1.0x.
    probe = ("Explain the URA tax obligations for a business with an annual "
             f"turnover of {random.randint(120, 990)} million shillings.")
    t = time.perf_counter()
    a = await send_one(client, base, "subsystems", "en", "redis_cache_miss", probe, False)
    t_miss = time.perf_counter() - t
    t = time.perf_counter()
    b = await send_one(client, base, "subsystems", "en", "redis_cache_hit", probe, False)
    t_hit = time.perf_counter() - t
    speedup = (t_miss / t_hit) if t_hit else 0.0
    rec("redis_semantic_cache", speedup >= 2.0,
        {"first_s": round(t_miss, 2), "second_s": round(t_hit, 2),
         "speedup_x": round(speedup, 1), "identical_reply": a.reply_len == b.reply_len},
        latency=t_hit)

    # --- Speech tiers: which backend answers, per locale ---
    h = await client.get(f"{base}/v1/speech/health")
    hj = h.json() if h.status_code == 200 else {}
    rec("speech_health", bool(hj.get("enabled")), hj)

    for loc, phrase in (("en", "Value added tax in Uganda is eighteen percent."),
                        ("lg", "Omusolo gwa VAT mu Uganda guli ku bitundu kkumi na munaana."),
                        ("sw", "Kodi ya VAT nchini Uganda ni asilimia kumi na nane.")):
        # TTS -> ASR round trip: proves both models, and that the audio one
        # produced is intelligible to the other rather than merely non-silent.
        t = time.perf_counter()
        try:
            tr = await client.post(f"{base}/v1/tts", json={"text": phrase, "language": loc})
            tts_s = time.perf_counter() - t
            tj = tr.json() if tr.status_code == 200 else {}
            audio = base64.b64decode(tj.get("audio_base64") or "")
            rec(f"tts_{loc}", bool(audio) and not tj.get("error"),
                {"backend": tj.get("backend"), "voice": tj.get("voice"),
                 "duration_s": tj.get("duration_s"), "bytes": len(audio),
                 "latency_s": round(tts_s, 2), "error": tj.get("error")}, latency=tts_s)
            if not audio:
                continue
            # Only Spark-TTS-SALT returns RIFF/WAV. English has no Spark
            # speaker id, so it falls through to edge-tts, which returns MP3 —
            # /v1/asr wants raw PCM, so there is nothing to round-trip. Record
            # that as a skip rather than letting wave.open raise "file does not
            # start with RIFF id" and read as an ASR failure.
            if audio[:4] != b"RIFF":
                rec(f"asr_{loc}", True,
                    {"skipped": "TTS tier returned non-RIFF audio (edge-tts MP3)",
                     "tts_backend": tj.get("backend")})
                continue
            with wave.open(io.BytesIO(audio)) as w:
                sr = w.getframerate()
                pcm = w.readframes(w.getnframes())
            t = time.perf_counter()
            ar = await client.post(
                f"{base}/v1/asr", params={"sample_rate": sr, "language": loc},
                content=pcm,
                headers={"Content-Type": "application/octet-stream",
                         "X-Voice-Consent": "true"},
            )
            asr_s = time.perf_counter() - t
            aj = ar.json() if ar.status_code == 200 else {}
            text = (aj.get("text") or "").strip()
            rec(f"asr_{loc}", bool(text) and not aj.get("error"),
                {"backend": aj.get("backend"), "rtf": aj.get("rtf"),
                 "latency_s": round(asr_s, 2), "transcript": text[:200],
                 "source_phrase": phrase, "error": aj.get("error")}, latency=asr_s)
        except Exception as exc:  # noqa: BLE001
            rec(f"roundtrip_{loc}", False, {"exception": f"{type(exc).__name__}: {exc}"[:200]})

    return PhaseResult("subsystems", "Qdrant / Redis / Whisper-SALT / Spark-TTS-SALT activation",
                       started, time.perf_counter() - t0, recs)


PHASES = {
    "subsystems": phase_subsystems,
    "functional": phase_functional,
    "ratelimit": phase_ratelimit,
    "load": phase_load,
    "spike": phase_spike,
    "stress": phase_stress,
    "volume": phase_volume,
}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def summarize(pr: PhaseResult) -> dict[str, Any]:
    recs = pr.records
    ok = [r for r in recs if r.ok]
    lats = sorted(r.latency_s for r in ok)

    def pct(p: float) -> float | None:
        if not lats:
            return None
        idx = min(int(len(lats) * p / 100.0), len(lats) - 1)
        return round(lats[idx], 2)

    status_counts: dict[str, int] = {}
    for r in recs:
        key = str(r.status) if not r.error or r.status else r.error.split(":")[0]
        status_counts[key] = status_counts.get(key, 0) + 1

    per_locale: dict[str, Any] = {}
    for loc in ("en", "lg", "sw"):
        sub = [r for r in recs if r.expect_locale == loc]
        sub_ok = [r for r in sub if r.ok]
        if not sub:
            continue
        sub_lats = sorted(r.latency_s for r in sub_ok)
        per_locale[loc] = {
            "requests": len(sub),
            "ok": len(sub_ok),
            "locale_detected_correctly": sum(1 for r in sub_ok if r.locale_ok),
            "reply_in_expected_language": sum(1 for r in sub_ok if r.lang_ok),
            "empty_or_unknown_reply": sum(1 for r in sub_ok if r.reply_lang == "unknown"),
            "mean_reply_chars": round(statistics.mean([r.reply_len for r in sub_ok]), 0) if sub_ok else 0,
            "p95_s": round(sub_lats[min(int(len(sub_lats) * 0.95), len(sub_lats) - 1)], 2) if sub_lats else None,
        }

    return {
        "phase": pr.name,
        "description": pr.description,
        "started_at": pr.started_at,
        "wall_s": round(pr.wall_s, 1),
        "requests": len(recs),
        "ok": len(ok),
        "error_rate_pct": round(100.0 * (len(recs) - len(ok)) / len(recs), 2) if recs else 0.0,
        "throughput_rps": round(len(recs) / pr.wall_s, 2) if pr.wall_s else 0.0,
        "status_counts": status_counts,
        "latency": {"p50": pct(50), "p90": pct(90), "p95": pct(95), "p99": pct(99),
                    "max": round(lats[-1], 2) if lats else None},
        "retrieval_modes": {m: sum(1 for r in ok if r.retrieval_mode == m)
                            for m in sorted({r.retrieval_mode for r in ok if r.retrieval_mode})},
        "per_locale": per_locale,
        "samples": [
            {"locale": r.expect_locale, "intent": r.intent, "reported": r.reported_locale,
             "classified": r.reply_lang, "reply": r.reply_sample}
            for r in recs if r.reply_sample
        ][:12],
    }


def print_subsystems(pr: PhaseResult) -> None:
    print(f"\n{'=' * 74}")
    print(f"PHASE: subsystems — {pr.description}   ({pr.wall_s:.0f}s)")
    print(f"{'=' * 74}")
    for r in pr.records:
        mark = "PASS" if r.ok else "FAIL"
        detail = r.extra if r.extra else {}
        print(f"  [{mark}] {r.intent:<26} {detail}")


def print_summary(s: dict[str, Any]) -> None:
    print(f"\n{'=' * 74}")
    print(f"PHASE: {s['phase']} — {s['description']}")
    print(f"{'=' * 74}")
    print(f"  requests={s['requests']}  ok={s['ok']}  error_rate={s['error_rate_pct']}%  "
          f"throughput={s['throughput_rps']} rps  wall={s['wall_s']}s")
    print(f"  status: {s['status_counts']}")
    lat = s["latency"]
    print(f"  latency s: p50={lat['p50']} p90={lat['p90']} p95={lat['p95']} "
          f"p99={lat['p99']} max={lat['max']}")
    if s["retrieval_modes"]:
        print(f"  retrieval: {s['retrieval_modes']}")
    for loc, v in s["per_locale"].items():
        print(f"  [{loc}] req={v['requests']} ok={v['ok']} "
              f"locale_ok={v['locale_detected_correctly']}/{v['ok']} "
              f"reply_lang_ok={v['reply_in_expected_language']}/{v['ok']} "
              f"unknown={v['empty_or_unknown_reply']} "
              f"chars={v['mean_reply_chars']:.0f} p95={v['p95_s']}s")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--phase", default="functional",
                    help="one of: " + ", ".join(PHASES) + ", or 'all'")
    ap.add_argument("--out", default=None, help="write full JSON results here")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    names = list(PHASES) if args.phase == "all" else args.phase.split(",")
    random.seed(1337)  # reproducible query mix across runs

    summaries: list[dict[str, Any]] = []
    all_records: list[dict] = []
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        for name in names:
            fn = PHASES[name.strip()]
            print(f"\n>>> running phase: {name} ...", flush=True)
            pr = await fn(client, args.base)
            if pr.name == "subsystems":
                print_subsystems(pr)
                summaries.append({"phase": "subsystems", "description": pr.description,
                                  "wall_s": round(pr.wall_s, 1),
                                  "checks": [{"name": r.intent, "pass": r.ok, **r.extra}
                                             for r in pr.records]})
            else:
                s = summarize(pr)
                print_summary(s)
                summaries.append(s)
            all_records.extend(asdict(r) for r in pr.records)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"base": args.base, "summaries": summaries,
                       "records": all_records}, fh, indent=2, ensure_ascii=False)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
