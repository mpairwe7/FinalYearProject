"""Corpus coverage against the questions taxpayers actually ask (issue #303).

The retrieval gates this repository already carries all score *fixed rows*:
``test_retrieval_regression_gate`` asks every indexed FAQ its own question,
``evaluate_rag`` scores ``Data/eval/rag_eval.jsonl``. Both answer "does the
corpus still retrieve what it retrieved yesterday". Neither can answer "is
there a common taxpayer question the corpus cannot answer at all", because
every question they ask is one the corpus was built from.

That is how the objections gap reached production: the only indexed sentence
on the subject was "Yes. You may lodge an objection if dissatisfied with an
assessment.", so self-retrieval was perfect and a taxpayer asking "how do I
object to a tax assessment I disagree with?" got the abstention copy.

This harness probes the other way round. ``Data/eval/coverage_bank.jsonl``
holds questions phrased the way contact-centre callers phrase them, in
English, Luganda and Kiswahili, each tagged with the domain it belongs to and
the facts a correct answer has to carry. Running it reports, per domain:

    answered   hits returned AND one of ``expect_any`` present   -> coverage
    weak       hits returned but none of the expected facts      -> gap
    abstained  nothing retrieved                                 -> gap
    deflected  a clarifying question, or an escalation to staff  -> gap
    skipped    never asked (see the corpus-mode note below)      -> not scored

``--fail-under-floor`` gates release on the per-domain floors in
``Data/eval/coverage_domains.yaml``; every abstention and weak answer is
printed by question so the output is a work list rather than a percentage.

Three modes:

``--mode corpus`` (default)
    Offline. Drives ``app.service``'s keyword + priority + filter path — the
    one production serves from when Qdrant and the LLM are absent — so it
    needs no network, no model and no vector store, and can gate every PR.
    The indexed corpus is English, so a Luganda or Kiswahili question only
    reaches it through ``english_retrieval_query``. Without ``--translate``
    those probes are reported as ``skipped`` and kept out of the coverage
    denominator rather than counted as abstentions — scoring them would
    report 0% Luganda coverage for a pipeline that translates before it
    retrieves, which is a number about the harness, not about the product.

``--mode api --base-url URL``
    Drives a running deployment over ``POST /v1/chat``, so the figure covers
    the whole pipeline (hybrid retrieval, LLM, guardrails, localisation).
    Needs a GPU stack; used for release evidence, not as the PR gate.

``--mode voice --base-url URL``
    The same bank spoken rather than typed: each question is synthesized with
    ``POST /v1/tts``, the audio is posted to ``POST /v1/voice/chat``, and the
    reply is scored the same way. A taxpayer who calls and a taxpayer who
    types are asking the same questions, so their coverage figures belong on
    the same axis — a domain that answers in text and abstains in voice is a
    gap even though the corpus is identical. Slow (four model calls per
    probe), so ``--sample-per-domain`` keeps a run to one question per domain
    per language unless told otherwise.

Usage::

    python -m ml.pipelines.corpus_coverage --languages en,lg,sw
    python -m ml.pipelines.corpus_coverage --fail-under-floor
    python -m ml.pipelines.corpus_coverage --mode api \\
        --base-url http://127.0.0.1:8083 --languages en,lg,sw \\
        --output Results/eval/coverage.json
    python -m ml.pipelines.corpus_coverage --mode voice \\
        --base-url http://127.0.0.1:8083 --languages en,lg,sw --sample-per-domain 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BANK_PATH = PROJECT_ROOT / "Data" / "eval" / "coverage_bank.jsonl"
DOMAINS_PATH = PROJECT_ROOT / "Data" / "eval" / "coverage_domains.yaml"
DATASET_DIR = PROJECT_ROOT / "Data" / "dataset"

#: The languages the taxpayer UI offers and this harness probes by default.
DEFAULT_LANGUAGES = ("en", "lg", "sw")

#: Terms that mean the same thing after translation, so an English
#: ``expect_any`` entry containing one can still be checked in a Luganda or
#: Kiswahili reply. Anything with a digit or a percent sign qualifies (rates,
#: deadlines, thresholds), plus the domain acronyms taxpayers and the corpus
#: both use untranslated. Everything else is an English word: a correct
#: Luganda answer paraphrases it, and scoring against it would report a
#: translation as a wrong answer.
_LANGUAGE_NEUTRAL_TERMS = frozenset(
    {"tin", "vat", "paye", "wht", "efris", "dts", "prn", "aeoi", "aeo",
     "asycuda", "nin", "ura", "cgt", "cit", "visa"}
)

#: Chat retrieval modes that mean "no answer was retrieved at all".
_ABSTAINING_MODES = frozenset({"abstained", "blocked"})

#: Modes where the product answered *something else* on purpose — it asked a
#: follow-up question, or handed the turn to an officer. Neither gives the
#: taxpayer their answer, so they count against coverage, but they are
#: reported under their own status: a dispute-framed objection escalating is
#: policy (see App/backend/tests/test_objection_retrieval.py), while a
#: clarification on "what is the VAT rate" is a miss. The gate cannot tell
#: those apart; a reviewer reading the gap list can.
_DEFLECTING_MODES = frozenset({"clarification", "escalated"})

#: Modes that answer without citing corpus passages — a guided workflow, a
#: rate-table lookup, the statutory graph. ``sources`` is empty by design
#: there, so an empty-sources check would score them as abstentions.
_SOURCELESS_ANSWER_MODES = frozenset(
    {"workflow", "calculator", "graph", "deterministic"}
)


# ---------------------------------------------------------------------------
# Bank + registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BankEntry:
    id: str
    domain: str
    theme: str
    question: dict[str, str]
    expect_any: tuple[str, ...]
    origin: str = ""
    #: Optional per-language overrides, ``expect_any_lg`` / ``expect_any_sw``
    #: in the JSONL. Without one, a non-English probe can only be scored on
    #: the language-neutral subset of ``expect_any``.
    expect_by_language: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def expect_for(self, language: str) -> tuple[tuple[str, ...], bool]:
        """``(terms, scorable)`` for *language*.

        English uses ``expect_any`` as written. Another language uses its
        override if the bank carries one, else the language-neutral subset —
        and if that subset is empty the probe is not scorable in that language
        at all, which the caller reports rather than guessing.
        """
        if language == "en":
            return self.expect_any, True
        override = self.expect_by_language.get(language)
        if override:
            return override, True
        neutral = tuple(t for t in self.expect_any if _is_language_neutral(t))
        return neutral, bool(neutral)


@dataclass
class DomainSpec:
    name: str
    label: str
    sources: tuple[str, ...]
    floor: float
    review: dict[str, str] = field(default_factory=dict)


def _is_language_neutral(term: str) -> bool:
    """True when *term* can be looked for in a reply written in any language."""
    stripped = term.strip().lower()
    return any(c.isdigit() or c == "%" for c in stripped) or stripped in _LANGUAGE_NEUTRAL_TERMS


def _display_path(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_bank(path: Path = BANK_PATH) -> list[BankEntry]:
    """Read the question bank, failing loudly on a malformed row.

    A silently skipped row is a question that stops being asked, which is the
    one failure mode this harness must not have.
    """
    entries: list[BankEntry] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as err:
                raise ValueError(f"{path.name}:{lineno}: {err}") from err
            missing = {"id", "domain", "question", "expect_any"} - set(row)
            if missing:
                raise ValueError(f"{path.name}:{lineno}: missing {sorted(missing)}")
            entries.append(
                BankEntry(
                    id=str(row["id"]),
                    domain=str(row["domain"]),
                    theme=str(row.get("theme", "")),
                    question={k: str(v) for k, v in dict(row["question"]).items()},
                    expect_any=tuple(str(t) for t in row["expect_any"]),
                    origin=str(row.get("origin", "")),
                    expect_by_language={
                        key[len("expect_any_"):]: tuple(str(t) for t in value)
                        for key, value in row.items()
                        if key.startswith("expect_any_") and value
                    },
                )
            )
    return entries


def load_overall_floor(path: Path = DOMAINS_PATH) -> float:
    """The whole-bank floor, gated alongside the per-domain ones.

    A four-question domain moves in 25-point steps, so its floor cannot be set
    tight without flapping. The bank as a whole has 100+ questions and can, so
    this is what catches slow drift that no single domain trips.
    """
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return float(raw.get("overall_floor", 0.0))


def load_domains(path: Path = DOMAINS_PATH) -> dict[str, DomainSpec]:
    """Read the domain registry, applying ``defaults`` to each domain."""
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    default_floor = float(defaults.get("floor", 0.6))
    default_review = dict(defaults.get("review") or {})

    specs: dict[str, DomainSpec] = {}
    for name, body in (raw.get("domains") or {}).items():
        body = body or {}
        review = dict(default_review)
        review.update(body.get("review") or {})
        specs[str(name)] = DomainSpec(
            name=str(name),
            label=str(body.get("label", name)),
            sources=tuple(str(s) for s in (body.get("sources") or ())),
            floor=float(body.get("floor", default_floor)),
            review=review,
        )
    return specs


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
@dataclass
class ProbeResult:
    id: str
    domain: str
    language: str
    question: str
    status: str  # answered | weak | abstained | deflected | unscorable | skipped | error
    top_source: str = ""
    top_question: str = ""
    matched: str = ""
    reply_excerpt: str = ""
    retrieval_mode: str = ""
    latency_s: float = 0.0
    translated: bool = False
    error: str = ""
    #: Per-stage voice telemetry; empty for the text modes.
    voice: dict[str, Any] = field(default_factory=dict)


def _classify(text: str, expect_any: Iterable[str], has_hits: bool) -> tuple[str, str]:
    """Return ``(status, matched_term)`` for a retrieved answer.

    ``expect_any`` are the facts a correct answer has to carry — the rate, the
    deadline, the system name. Retrieval that returns *something* without any
    of them is reported as ``weak`` rather than counted as coverage: a
    plausible-looking answer from the wrong FAQ is the failure this is meant
    to surface, and it is invisible to a hits/no-hits check.
    """
    if not has_hits:
        return "abstained", ""
    haystack = (text or "").casefold()
    for term in expect_any:
        if term.casefold() in haystack:
            return "answered", term
    return "weak", ""


def _corpus_prober(translate: bool) -> Callable[..., ProbeResult]:
    """Build a prober over the offline FAQ path production falls back to."""
    os.environ.setdefault("LLM_ENABLED", "false")
    os.environ.setdefault("QDRANT_ENABLED", "false")
    os.environ.setdefault("SPEECH_ENABLED", "false")

    from app.service import (  # noqa: PLC0415 — import cost only when this mode runs
        _DATA_DIR,
        ChatModel,
        _faq_hits_to_retrieval_hits,
        _filter_unbound_faq_hits,
        _load_faq_data,
        _prepend_unique,
        _promote_equivalent_faq_hits,
        _simple_search,
    )

    model = ChatModel.__new__(ChatModel)
    model._faq_index, _ = _load_faq_data(Path(_DATA_DIR))

    def _english(question: str, language: str) -> tuple[str, bool]:
        from app.query import english_retrieval_query  # noqa: PLC0415

        english = english_retrieval_query(question, language)
        return english, english.strip().casefold() != question.strip().casefold()

    def probe(entry: BankEntry, language: str) -> ProbeResult:
        question = entry.question.get(language, "")
        if not question:
            return ProbeResult(
                entry.id, entry.domain, language, "", "error",
                error=f"bank entry has no {language} question",
            )
        if language != "en" and not translate:
            return ProbeResult(
                entry.id, entry.domain, language, question, "skipped",
                error="corpus is English; rerun with --translate or --mode api",
            )
        t0 = time.perf_counter()
        search_query, translated = (question, False) if language == "en" else _english(
            question, language
        )
        if language != "en" and not translated:
            return ProbeResult(
                entry.id, entry.domain, language, question, "skipped",
                error="no MT backend reachable — question was not translated",
                latency_s=round(time.perf_counter() - t0, 4),
            )
        keyword = _simple_search(
            search_query, model._faq_index, top_k=4,
            binding_query=search_query, locale="en",
        )
        hits = _faq_hits_to_retrieval_hits(keyword)
        seen = {h.get("text", "")[:80] for h in hits}
        _prepend_unique(hits, model._priority_faq_hits(search_query, top_k=2), seen)
        hits = _promote_equivalent_faq_hits(
            search_query, _filter_unbound_faq_hits(search_query, hits)
        )
        body = " ".join(
            f"{h.get('question', '')} {h.get('answer', '') or h.get('text', '')}"
            for h in hits
        )
        expect, scorable = entry.expect_for(language)
        if not scorable:
            status, matched = "unscorable", ""
        else:
            status, matched = _classify(body, expect, bool(hits))
        top = hits[0] if hits else {}
        return ProbeResult(
            id=entry.id,
            domain=entry.domain,
            language=language,
            question=question,
            status=status,
            top_source=str(top.get("source", "")),
            top_question=str(top.get("question", ""))[:120],
            matched=matched,
            reply_excerpt=str(top.get("answer", "") or top.get("text", ""))[:200],
            retrieval_mode="keyword",
            latency_s=round(time.perf_counter() - t0, 4),
            translated=translated,
        )

    return probe


def _api_prober(base_url: str, timeout: float, top_k: int) -> Callable[..., ProbeResult]:
    """Build a prober that drives a running deployment over ``POST /v1/chat``."""
    import httpx  # noqa: PLC0415

    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def probe(entry: BankEntry, language: str) -> ProbeResult:
        question = entry.question.get(language, "")
        if not question:
            return ProbeResult(
                entry.id, entry.domain, language, "", "error",
                error=f"bank entry has no {language} question",
            )
        t0 = time.perf_counter()
        try:
            resp = client.post(
                "/v1/chat",
                json={"message": question, "locale": language, "top_k": top_k},
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as err:  # noqa: BLE001 — every failure is a reportable probe
            return ProbeResult(
                entry.id, entry.domain, language, question, "error",
                latency_s=round(time.perf_counter() - t0, 3),
                error=f"{type(err).__name__}: {err}"[:200],
            )
        mode = str(payload.get("retrieval_mode", ""))
        reply = str(payload.get("reply", ""))
        sources = list(payload.get("sources") or [])
        expect, scorable = entry.expect_for(language)
        if mode in _ABSTAINING_MODES:
            status, matched = "abstained", ""
        elif mode in _DEFLECTING_MODES:
            status, matched = "deflected", ""
        elif not scorable:
            status, matched = "unscorable", ""
        else:
            grounded = bool(sources) or mode in _SOURCELESS_ANSWER_MODES
            status, matched = _classify(reply, expect, grounded)
        return ProbeResult(
            id=entry.id,
            domain=entry.domain,
            language=language,
            question=question,
            status=status,
            top_source=sources[0] if sources else "",
            matched=matched,
            reply_excerpt=reply[:200],
            retrieval_mode=mode,
            latency_s=round(time.perf_counter() - t0, 3),
        )

    return probe


def sample_bank(bank: list[BankEntry], per_domain: int) -> list[BankEntry]:
    """First *per_domain* questions of each domain, in bank order.

    Deliberately not random: a coverage figure that moves because the sampler
    reseeded is indistinguishable from one that moved because the corpus
    changed, and this number is meant to gate releases.
    """
    seen: Counter[str] = Counter()
    out: list[BankEntry] = []
    for entry in bank:
        if seen[entry.domain] >= per_domain:
            continue
        seen[entry.domain] += 1
        out.append(entry)
    return out


def _token_recall(reference: str, hypothesis: str) -> float:
    """Fraction of the reference's word types the transcript recovered.

    Not WER. WER needs an alignment and a reference transcript of the audio;
    here the "reference" is the text that was synthesized, so this measures
    round-trip fidelity — how much of the question survived TTS and ASR — and
    is reported as such rather than as an ASR accuracy claim.
    """
    ref = {w for w in "".join(c if c.isalnum() else " " for c in reference.casefold()).split() if w}
    hyp = {w for w in "".join(c if c.isalnum() else " " for c in hypothesis.casefold()).split() if w}
    if not ref:
        return 0.0
    return round(len(ref & hyp) / len(ref), 4)


def _voice_prober(base_url: str, timeout: float, top_k: int) -> Callable[..., ProbeResult]:
    """Build a prober that speaks each question and listens to the answer.

    TTS -> /v1/voice/chat (ASR -> retrieval -> LLM -> TTS). The synthesized
    question is not human speech, so the transcript fidelity recorded here is
    an upper bound on real ASR accuracy; what it does test end to end is that
    every stage runs, in every language, and that the spoken pipeline reaches
    the same answers as the typed one.
    """
    import base64  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    client = httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        # Anonymous voice consent — /v1/asr and /v1/voice/chat are fail-closed
        # without it (main._require_voice_processing_consent).
        headers={"X-Voice-Consent": "true"},
    )

    def probe(entry: BankEntry, language: str) -> ProbeResult:
        question = entry.question.get(language, "")
        if not question:
            return ProbeResult(
                entry.id, entry.domain, language, "", "error",
                error=f"bank entry has no {language} question",
            )
        t0 = time.perf_counter()
        try:
            tts = client.post("/v1/tts", json={"text": question, "language": language})
            tts.raise_for_status()
            tts_body = tts.json()
            audio = base64.b64decode(tts_body.get("audio_base64") or "")
            if not audio:
                return ProbeResult(
                    entry.id, entry.domain, language, question, "error",
                    latency_s=round(time.perf_counter() - t0, 3),
                    error=f"TTS produced no audio: {tts_body.get('error') or 'empty'}",
                )
            chat = client.post(
                "/v1/voice/chat",
                params={
                    "language": language,
                    "top_k": top_k,
                    "tts_enabled": "true",
                    "sample_rate": tts_body.get("sample_rate", 16000),
                },
                content=audio,
                headers={"content-type": "audio/wav"},
            )
            chat.raise_for_status()
            payload = chat.json()
        except Exception as err:  # noqa: BLE001 — every failure is a reportable probe
            return ProbeResult(
                entry.id, entry.domain, language, question, "error",
                latency_s=round(time.perf_counter() - t0, 3),
                error=f"{type(err).__name__}: {err}"[:200],
            )

        transcript = str(payload.get("transcript") or "")
        reply = str(payload.get("reply") or "")
        mode = str(payload.get("retrieval_mode") or "")
        stage_error = str(payload.get("error") or "")
        expect, scorable = entry.expect_for(language)
        if stage_error:
            status, matched = "error", ""
        elif mode in _ABSTAINING_MODES:
            status, matched = "abstained", ""
        elif mode in _DEFLECTING_MODES:
            status, matched = "deflected", ""
        elif not scorable:
            status, matched = "unscorable", ""
        else:
            grounded = bool(payload.get("sources")) or mode in _SOURCELESS_ANSWER_MODES
            status, matched = _classify(reply, expect, grounded)

        return ProbeResult(
            id=entry.id,
            domain=entry.domain,
            language=language,
            question=question,
            status=status,
            top_source=(list(payload.get("sources") or []) or [""])[0],
            top_question=transcript[:120],
            matched=matched,
            reply_excerpt=reply[:200],
            retrieval_mode=mode,
            latency_s=round(time.perf_counter() - t0, 3),
            error=stage_error,
            voice={
                "tts_backend": tts_body.get("backend", ""),
                "tts_voice": tts_body.get("voice", ""),
                "tts_bytes": len(audio),
                "asr_backend": payload.get("asr_backend", ""),
                "asr_latency_s": payload.get("asr_latency_s", 0.0),
                "transcript": transcript,
                "transcript_recall": _token_recall(question, transcript),
                # VoiceChatResponse names this `reply_audio_base64`, not
                # `audio_base64` as /v1/tts does — reading the wrong key here
                # reports every spoken answer as silent.
                "reply_audio_bytes": len(
                    base64.b64decode(payload.get("reply_audio_base64") or "")
                ),
                "tts_reply_backend": payload.get("tts_backend", ""),
                "tts_skipped": bool(payload.get("tts_skipped")),
                "mt_backend": payload.get("mt_backend", ""),
                "total_latency_s": payload.get("total_latency_s", 0.0),
            },
        )

    return probe


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def summarise(
    results: list[ProbeResult],
    domains: dict[str, DomainSpec],
    languages: tuple[str, ...],
) -> dict[str, Any]:
    """Aggregate probe results into the per-domain report the gate reads."""
    # A skipped probe was never asked, and an unscorable one was asked but
    # cannot be judged in that language — this row's expected facts are English
    # words with no `expect_any_<lang>` override, so a correct translation and
    # a wrong answer look identical. Counting either as a miss reports a
    # product gap that is really a harness limit. Both stay visible in the
    # totals and in the listings, and out of every denominator.
    _UNSCORED = ("skipped", "unscorable")
    scored = [r for r in results if r.status not in _UNSCORED]

    per_domain: dict[str, Any] = {}
    for name, spec in sorted(domains.items()):
        rows = [r for r in scored if r.domain == name]
        if not rows:
            continue
        counts = Counter(r.status for r in rows)
        answered = counts.get("answered", 0)
        total = len(rows)
        by_language = {}
        for lang in languages:
            lang_rows = [r for r in rows if r.language == lang]
            if not lang_rows:
                continue
            lang_answered = sum(1 for r in lang_rows if r.status == "answered")
            by_language[lang] = {
                "total": len(lang_rows),
                "answered": lang_answered,
                "coverage": round(lang_answered / len(lang_rows), 4),
            }
        per_domain[name] = {
            "label": spec.label,
            "total": total,
            "answered": answered,
            "weak": counts.get("weak", 0),
            "abstained": counts.get("abstained", 0),
            "deflected": counts.get("deflected", 0),
            "errors": counts.get("error", 0),
            "skipped": sum(1 for r in results if r.domain == name and r.status == "skipped"),
            "unscorable": sum(
                1 for r in results if r.domain == name and r.status == "unscorable"
            ),
            "coverage": round(answered / total, 4) if total else 0.0,
            "floor": spec.floor,
            "passed": (answered / total if total else 0.0) >= spec.floor,
            "by_language": by_language,
            "review": spec.review,
        }

    overall = Counter(r.status for r in results)
    total = len(scored)
    by_language = {}
    for lang in languages:
        rows = [r for r in scored if r.language == lang]
        skipped = sum(1 for r in results if r.language == lang and r.status == "skipped")
        unscorable = sum(1 for r in results if r.language == lang and r.status == "unscorable")
        if not rows:
            by_language[lang] = {
                "total": 0, "answered": 0, "weak": 0, "abstained": 0, "deflected": 0,
                "errors": 0, "skipped": skipped, "unscorable": unscorable,
                "coverage": None, "translated": 0,
            }
            continue
        answered = sum(1 for r in rows if r.status == "answered")
        by_language[lang] = {
            "total": len(rows),
            "answered": answered,
            "weak": sum(1 for r in rows if r.status == "weak"),
            "abstained": sum(1 for r in rows if r.status == "abstained"),
            "deflected": sum(1 for r in rows if r.status == "deflected"),
            "errors": sum(1 for r in rows if r.status == "error"),
            "skipped": skipped,
            "unscorable": unscorable,
            "coverage": round(answered / len(rows), 4),
            "translated": sum(1 for r in rows if r.translated),
        }

    voice_rows = [r for r in results if r.voice]
    voice_summary: dict[str, Any] = {}
    if voice_rows:
        recalls = [r.voice.get("transcript_recall", 0.0) for r in voice_rows]
        voice_summary = {
            "probes": len(voice_rows),
            "tts_backends": dict(Counter(r.voice.get("tts_backend", "") for r in voice_rows)),
            "tts_voices": dict(Counter(r.voice.get("tts_voice", "") for r in voice_rows)),
            "asr_backends": dict(Counter(r.voice.get("asr_backend", "") for r in voice_rows)),
            "reply_tts_backends": dict(
                Counter(r.voice.get("tts_reply_backend", "") for r in voice_rows)
            ),
            "transcript_recall_mean": round(sum(recalls) / len(recalls), 4),
            "empty_transcripts": sum(1 for r in voice_rows if not r.voice.get("transcript")),
            "silent_replies": sum(
                1 for r in voice_rows if not r.voice.get("reply_audio_bytes")
            ),
            "tts_skipped_on_budget": sum(1 for r in voice_rows if r.voice.get("tts_skipped")),
            "mt_backends": dict(Counter(r.voice.get("mt_backend", "") for r in voice_rows)),
            "by_language": {
                lang: {
                    "probes": len(rows),
                    "transcript_recall_mean": round(
                        sum(x.voice.get("transcript_recall", 0.0) for x in rows) / len(rows), 4
                    ),
                    "tts_voices": sorted(
                        {x.voice.get("tts_voice", "") for x in rows if x.voice.get("tts_voice")}
                    ),
                    "asr_backends": sorted(
                        {x.voice.get("asr_backend", "") for x in rows if x.voice.get("asr_backend")}
                    ),
                }
                for lang in languages
                if (rows := [r for r in voice_rows if r.language == lang])
            },
        }

    return {
        "totals": {
            "probes": len(results),
            "scored": total,
            "answered": overall.get("answered", 0),
            "weak": overall.get("weak", 0),
            "abstained": overall.get("abstained", 0),
            "deflected": overall.get("deflected", 0),
            "skipped": overall.get("skipped", 0),
            "unscorable": overall.get("unscorable", 0),
            "errors": overall.get("error", 0),
            "coverage": round(overall.get("answered", 0) / total, 4) if total else 0.0,
        },
        "by_language": by_language,
        "by_domain": per_domain,
        "gaps": [
            asdict(r)
            for r in results
            if r.status in ("abstained", "weak", "deflected", "error")
        ],
        "skipped": [asdict(r) for r in results if r.status == "skipped"],
        "unscorable": [asdict(r) for r in results if r.status == "unscorable"],
        "voice": voice_summary,
    }


def print_report(report: dict[str, Any], languages: tuple[str, ...]) -> None:
    t = report["totals"]
    print()
    print("=" * 78)
    print("CORPUS COVERAGE — curated taxpayer question bank")
    print("=" * 78)
    print(
        f"probes {t['probes']}   scored {t['scored']}   answered {t['answered']}   "
        f"weak {t['weak']}   abstained {t['abstained']}   "
        f"deflected {t['deflected']}   unscorable {t['unscorable']}   "
        f"skipped {t['skipped']}   errors {t['errors']}   coverage {t['coverage']:.1%}"
        + (
            f" (floor {t['floor']:.0%}, {'ok' if t['passed'] else 'BELOW'})"
            if "floor" in t
            else ""
        )
    )
    print()
    print(f"{'domain':<16}{'label':<38}{'cov':>7}{'floor':>7}{'  ':>2}", end="")
    for lang in languages:
        print(f"{lang:>7}", end="")
    print()
    print("-" * 78)
    for name, row in report["by_domain"].items():
        flag = "ok " if row["passed"] else "BELOW"
        print(
            f"{name:<16}{row['label'][:36]:<38}{row['coverage']:>6.0%}"
            f"{row['floor']:>7.0%}  {flag:<5}",
            end="",
        )
        for lang in languages:
            cell = row["by_language"].get(lang)
            print(f"{cell['coverage']:>6.0%} " if cell else f"{'   -':>6} ", end="")
        print()
    print("-" * 78)
    for lang, row in report["by_language"].items():
        if row["coverage"] is None:
            print(f"  {lang}: not measured — {row['skipped']} probes skipped")
            continue
        print(
            f"  {lang}: {row['coverage']:.1%} answered, {row['weak']} weak, "
            f"{row['abstained']} abstained, {row['deflected']} deflected, "
            f"{row['errors']} errors"
            + (f", {row['translated']} translated for retrieval" if row.get("translated") else "")
            + (f", {row['skipped']} skipped" if row.get("skipped") else "")
            + (
                f", {row['unscorable']} unscorable in this language"
                if row.get("unscorable")
                else ""
            )
        )

    voice = report.get("voice") or {}
    if voice:
        print()
        print("VOICE PIPELINE — TTS -> /v1/voice/chat -> TTS")
        print("-" * 78)
        print(
            f"  {voice['probes']} spoken probes, mean transcript recall "
            f"{voice['transcript_recall_mean']:.1%}, "
            f"{voice['empty_transcripts']} empty transcripts, "
            f"{voice['silent_replies']} replies with no audio "
            f"({voice['tts_skipped_on_budget']} skipped on the time budget)"
        )
        for lang, row in voice["by_language"].items():
            print(
                f"  {lang}: recall {row['transcript_recall_mean']:.1%}   "
                f"voices {','.join(row['tts_voices']) or '-'}   "
                f"asr {','.join(row['asr_backends']) or '-'}"
            )
        print(f"  question TTS backends: {voice['tts_backends']}")
        print(f"  reply TTS backends:    {voice['reply_tts_backends']}")

    unscorable = report.get("unscorable") or []
    if unscorable:
        print()
        print(
            "UNSCORABLE — answered, but this row's expected facts are English "
            "words with no\n             expect_any_<lang> override, so a correct "
            "translation cannot be told\n             apart from a wrong answer. "
            "Add the override to bring these into the figure."
        )
        print("-" * 78)
        by_lang: Counter[str] = Counter(r["language"] for r in unscorable)
        for lang, count in sorted(by_lang.items()):
            ids = sorted({r["id"] for r in unscorable if r["language"] == lang})
            print(f"  {lang}: {count} probes — {', '.join(ids[:8])}"
                  + (f", +{len(ids) - 8} more" if len(ids) > 8 else ""))

    skipped = report.get("skipped") or []
    if skipped:
        reasons = Counter(r["error"] for r in skipped)
        print()
        print("SKIPPED — probes that were never asked, so they score nothing")
        print("-" * 78)
        for reason, count in reasons.most_common():
            print(f"  {count:>4}  {reason}")

    gaps = report["gaps"]
    if gaps:
        print()
        print("GAPS — every question that did not get a grounded, on-target answer")
        print("-" * 78)
        for gap in gaps:
            if gap["error"]:
                note = gap["error"]
            elif gap["status"] == "deflected":
                note = f"mode={gap['retrieval_mode']} — answered something else on purpose"
            elif gap["status"] == "weak":
                note = (
                    f"top hit {gap['top_source'] or '-'}: "
                    f"{gap['top_question'] or gap['reply_excerpt'][:80]!r}"
                )
            else:
                note = f"nothing retrieved (mode={gap['retrieval_mode'] or 'keyword'})"
            print(f"  [{gap['status']:>9}] {gap['domain']}/{gap['language']} "
                  f"{gap['id']}: {gap['question']}")
            print(f"              {note}")
    print()


# ---------------------------------------------------------------------------
# Registry / bank consistency (acceptance criteria 5 and 6)
# ---------------------------------------------------------------------------
def check_bank_covers_corpus(
    bank: list[BankEntry], domains: dict[str, DomainSpec], dataset_dir: Path = DATASET_DIR
) -> list[str]:
    """Return the reasons the bank does not yet cover the shipped corpus.

    Acceptance criterion 6: new corpus content requires an accompanying
    question. Enforced structurally rather than by review — every
    ``ura_*_faqs.csv`` has to be claimed by a domain, and every domain has to
    carry at least one question. That each question exists in all three
    languages is checked by ``tests/test_corpus_coverage_gate.py``, which is
    where the language set is defined.
    """
    problems: list[str] = []
    claimed: set[str] = set()
    for spec in domains.values():
        claimed.update(spec.sources)

    on_disk = {p.name for p in sorted(dataset_dir.glob("ura_*_faqs.csv"))}
    for name in sorted(on_disk - claimed):
        problems.append(
            f"corpus file {name} is in no domain of coverage_domains.yaml — "
            "add it to a domain and add a question to coverage_bank.jsonl"
        )
    for name in sorted(claimed - on_disk):
        problems.append(f"coverage_domains.yaml names {name}, which is not in Data/dataset")

    asked = {e.domain for e in bank}
    for name in sorted(set(domains) - asked):
        problems.append(f"domain {name!r} has no question in coverage_bank.jsonl")
    for name in sorted(asked - set(domains)):
        problems.append(f"coverage_bank.jsonl asks about unknown domain {name!r}")
    return problems


def check_reviews(domains: dict[str, DomainSpec], require_ura: bool) -> list[str]:
    """Return the domains whose question bank lacks a current URA sign-off.

    Acceptance criterion 5. The structural half — that every domain names a
    reviewer, a role and a date — always runs. ``require_ura`` adds the
    judgement half, failing any domain not signed off by a URA domain owner;
    it is off by default because none are yet, and a gate that fails every
    build on day one gets deleted rather than satisfied.
    """
    problems: list[str] = []
    for name, spec in sorted(domains.items()):
        review = spec.review or {}
        for key in ("by", "role", "date"):
            if not str(review.get(key, "")).strip():
                problems.append(f"domain {name!r} review is missing {key!r}")
        if require_ura and review.get("role") != "ura_domain_owner":
            problems.append(
                f"domain {name!r} is reviewed by {review.get('role', '?')!r}, "
                "not a URA domain owner"
            )
    return problems


def pending_ura_signoff(domains: dict[str, DomainSpec]) -> list[str]:
    return [n for n, s in sorted(domains.items()) if s.review.get("role") != "ura_domain_owner"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def run(
    languages: tuple[str, ...],
    prober: Callable[..., ProbeResult],
    bank: list[BankEntry],
    on_progress: Callable[[list[ProbeResult], BankEntry], None] | None = None,
) -> list[ProbeResult]:
    """Probe every bank entry in every language, in bank order.

    ``on_progress`` is called after each entry completes all its languages.
    A voice run is minutes per probe and hours per sweep; without a
    per-entry hook an interrupted run left nothing behind at all, and the
    only way to see how far it had got was to read the server's access log.
    Entry order means a partial run is a whole number of domains rather than
    a ragged edge.
    """
    results: list[ProbeResult] = []
    for entry in bank:
        for language in languages:
            results.append(prober(entry, language))
        if on_progress is not None:
            on_progress(results, entry)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mode", choices=("corpus", "api", "voice"), default="corpus")
    parser.add_argument("--base-url", default="http://127.0.0.1:8083")
    parser.add_argument("--languages", default=",".join(DEFAULT_LANGUAGES))
    parser.add_argument("--bank", type=Path, default=BANK_PATH)
    parser.add_argument("--domains", type=Path, default=DOMAINS_PATH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--translate",
        action="store_true",
        help="corpus mode: translate non-English questions before retrieval "
             "(needs a reachable MT backend; off by default so CI is offline)",
    )
    parser.add_argument(
        "--sample-per-domain",
        type=int,
        default=0,
        help="probe at most N questions per domain (0 = the whole bank). "
             "Defaults to 1 in voice mode, which is four model calls per probe.",
    )
    parser.add_argument("--fail-under-floor", action="store_true")
    parser.add_argument(
        "--require-ura-signoff",
        action="store_true",
        help="fail unless every domain's bank carries a URA domain-owner review",
    )
    args = parser.parse_args(argv)

    languages = tuple(x.strip() for x in args.languages.split(",") if x.strip())
    bank = load_bank(args.bank)
    domains = load_domains(args.domains)
    overall_floor = load_overall_floor(args.domains)

    structural = check_bank_covers_corpus(bank, domains)
    structural += check_reviews(domains, require_ura=args.require_ura_signoff)

    sample = args.sample_per_domain or (1 if args.mode == "voice" else 0)
    if sample:
        bank = sample_bank(bank, sample)

    if args.mode == "api":
        prober = _api_prober(args.base_url, args.timeout, args.top_k)
    elif args.mode == "voice":
        prober = _voice_prober(args.base_url, args.timeout, args.top_k)
    else:
        prober = _corpus_prober(translate=args.translate)

    started = time.time()

    def _build_report(rows: list[ProbeResult], complete: bool) -> dict[str, Any]:
        report = summarise(rows, domains, languages)
        report["meta"] = _meta(rows, complete)
        report["structural_problems"] = structural
        report["totals"]["floor"] = overall_floor
        report["totals"]["passed"] = report["totals"]["coverage"] >= overall_floor
        return report

    def _meta(rows: list[ProbeResult], complete: bool) -> dict[str, Any]:
        return {
            "mode": args.mode,
            "base_url": args.base_url if args.mode in ("api", "voice") else "",
            "languages": list(languages),
            "bank": _display_path(args.bank),
            "bank_size": len(bank),
            "sample_per_domain": sample,
            # Which revision of the bank produced these numbers. Editing one
            # row's `expect_any` changes what "answered" means, so two reports
            # are only comparable when this matches.
            "bank_sha256": hashlib.sha256(args.bank.read_bytes()).hexdigest()[:16],
            "domains_sha256": hashlib.sha256(args.domains.read_bytes()).hexdigest()[:16],
            "translate": bool(args.translate),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "duration_s": round(time.time() - started, 2),
            "complete": complete,
            "probes_done": len(rows),
            "probes_expected": len(bank) * len(languages),
            "pending_ura_signoff": pending_ura_signoff(domains),
        }

    def _checkpoint(rows: list[ProbeResult], _entry: BankEntry) -> None:
        """Write the partial report so an interrupted sweep is not wasted."""
        if not args.output:
            return
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(_build_report(rows, complete=False), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    results = run(languages, prober, bank, on_progress=_checkpoint)
    report = _build_report(results, complete=True)

    print_report(report, languages)

    if structural:
        print("BANK / REGISTRY PROBLEMS")
        print("-" * 78)
        for problem in structural:
            print(f"  - {problem}")
        print()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report written to {args.output}")

    failed = [n for n, row in report["by_domain"].items() if not row["passed"]]
    if structural:
        return 1
    if args.fail_under_floor:
        if failed:
            print(f"FAIL: {len(failed)} domain(s) below floor: {', '.join(failed)}")
            return 1
        if not report["totals"]["passed"]:
            print(
                f"FAIL: overall coverage {report['totals']['coverage']:.1%} "
                f"is below the {overall_floor:.0%} floor"
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
