"""URA Chatbot service layer — production hybrid RAG.

This module provides the ``ChatModel`` singleton that backs every API
endpoint.  It loads the FAQ CSV knowledge base into memory (for tag
classification and as a keyword-search fallback) and, when a Qdrant
vector store is available, performs hybrid dense + BM25 retrieval with
cross-encoder reranking and passage-level grounding verification.

Architecture (2026 RAG best practice)::

    User query
      → InputGuard (OWASP LLM01 prompt-injection check)
      → HybridRetriever.search (dense + sparse RRF → cross-encoder rerank)
      → fallback: _simple_search (keyword overlap)
      → passage-level citation assembly
      → OutputGuard (PII redaction, grounding check – LLM02/LLM05/LLM09)
      → ChatResponse with citations + faithfulness score

References:
  - Lewis et al. "Retrieval-Augmented Generation" (nlp.cs.ucl.ac.uk)
  - OWASP LLM Top 10 (owasp.org)
  - RAGAS docs (docs.ragas.io)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any, Callable

from . import database as db
from . import documents as documents_module
from . import llm as llm_module
from .agents import AgentRoute, supervisor
from .agents.evaluator import RevisionBudget, evaluate
from .analytics import metrics
from .escalation_notify import notify_ticket_created, team_for_topic
from .agents.patterns.en import (
    _FAREWELL_PHRASES,
    _GRATITUDE_PHRASES,
    _GREETING_PHRASES,
    _GREETING_WORDS,
)
# Tier selection is pure policy over the supervisor's decision — no cloud
# SDK, no key, no network — so unlike the rest of ``providers`` it is safe
# to import at module scope.
from .providers.routing import log_tier, select_tier
from .cache import create_cache
from .calculator_router import (
    NEXT_ACTIONS_BY_TOOL,
    format_calc_reply,
    format_rate_reply,
    plan_calculation,
    plan_rate_lookup,
)
from .claim_verifier import verify_claims
from .corrective_rag import corrective_retrieve, needs_clarification
from .flags import flags
from .guardrails import STORE_RAW_PROMPTS, InputGuard, OutputGuard, redact_pii_text
from .memory import get_memory_service
from .query import detect_language, extract_question_span, rewrite as rewrite_query
from .resilience import CircuitBreaker
from .retriever import HybridRetriever
from .text_signals import (
    ABSTENTION_REPLY,
    CLARIFICATION_PROMPT,
    CONTACT_FOOTER,
    ESCALATION_REPLY_FOOTER,
    ESCALATION_REPLY_LEAD,
    FAREWELL_REPLY,
    GRATITUDE_REPLY,
    GREETING_REPLY,
    GROUNDED_REVISION_PREAMBLE,
    NO_HITS_REPLY,
    detect_user_distress,
    empathy_ack,
    tone_hint_for,
)
from .tracing import record_retrieval_metrics, record_token_usage, trace_rag_pipeline, trace_stage
from .workflows.registry import WorkflowRegistry, WorkflowSession, auto_load_flows

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
from ._root import PROJECT_ROOT as _PROJECT_ROOT

_DEFAULT_DATA_DIR = str(_PROJECT_ROOT / "Data" / "dataset")
_DATA_DIR = Path(os.getenv("DATA_DIR", _DEFAULT_DATA_DIR)).resolve()
# Guard against path traversal via DATA_DIR env var
if not _DATA_DIR.is_relative_to(_PROJECT_ROOT):
    logger.warning("DATA_DIR %s escapes project root; falling back to default", _DATA_DIR)
    _DATA_DIR = Path(_DEFAULT_DATA_DIR).resolve()

GROUNDING_THRESHOLD = float(os.getenv("GROUNDING_THRESHOLD", "0.3"))
LLM_DEADLINE_SECONDS = float(os.getenv("LLM_DEADLINE_SECONDS", "45"))
# Hard cap on the *total* wall-clock time one request may spend across the
# whole local -> Gemini -> Workers AI chain, not just each hop's own
# timeout. Each hop already has its own budget (LLM_DEADLINE_SECONDS for
# local, CF_HTTP_TIMEOUT=30s per cloud call) but nothing previously bounded
# their sum, so a single degraded request could hold a uvicorn worker for
# ~45s + 30s + 30s + 30s (Gemini + 3 Workers AI models) = up to ~165s.  On a
# small worker pool, a handful of concurrent slow requests exhausts every
# worker and every *new* request queues behind them until it also times
# out — observed live as Crane Cloud's /v1/chat hanging to a uniform ~51s
# then 504ing for every request after the first several, recovering only
# after a redeploy. This budget is checked before starting each subsequent
# hop (not mid-call — an in-flight HTTP call still runs to its own
# timeout), so the worst case becomes roughly this budget plus one more
# hop's own timeout instead of the sum of every hop's timeout.
LLM_TOTAL_BUDGET_SECONDS = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "70"))
SELF_REFLECT_ENABLED = os.getenv("SELF_REFLECT_ENABLED", "false").lower() == "true"
SELF_REFLECT_THRESHOLD = float(os.getenv("SELF_REFLECT_THRESHOLD", "0.4"))
_WORKFLOW_FLOWS_DIR = Path(__file__).resolve().parent / "workflows" / "flows"
_WORKFLOW_CANCEL_WORDS = {"cancel", "stop", "quit", "exit", "nevermind", "never mind"}
_WORKFLOW_SENSITIVE_SLOTS = {"nin", "company_reg", "ngo_reg", "phone", "email"}
_INFORMATIONAL_WORKFLOW_QUERY_RE = re.compile(
    r"\b(?:how\s+(?:do|does|can|would|should)\s+(?:i|we|one|my|a|an)"
    r"|what\s+are\s+the\s+steps|what\s+is\s+the\s+process|"
    r"requirements?|procedure|where\s+do\s+i)\b",
    re.IGNORECASE,
)
_EXPLICIT_WORKFLOW_START_RE = re.compile(
    r"\b(?:start|begin|launch|open|proceed|continue|guide\s+me|walk\s+me\s+through|"
    r"help\s+me\s+(?:apply|register|file|submit))\b",
    re.IGNORECASE,
)
_ACCOUNT_QUERY_RE = re.compile(
    r"\b(my\s+tin|my\s+filing|my\s+return|my\s+account|my\s+balance)\b",
    re.IGNORECASE,
)
_CUSTOMS_QUERY_RE = re.compile(
    r"\b(import|export|customs|bill\s+of\s+lading|cif|tariff|clearance)\b",
    re.IGNORECASE,
)
_OBJECTION_QUERY_RE = re.compile(
    r"\b(dispute|objection|appeal|assessment|audit|fraud|lawyer|court)\b",
    re.IGNORECASE,
)
# "pin" tolerated as the common typo for TIN in registration asks.
_TIN_REGISTRATION_QUERY_RE = re.compile(
    r"\b(?:register|get|obtain|apply)\b.*\b(?:tin|pin)\b"
    r"|\b(?:tin|pin)\b.*\b(?:register|get|obtain|apply)\b",
    re.IGNORECASE,
)
_TIN_ORG_QUERY_RE = re.compile(
    r"\b(organisations?|organizations?|compan(?:y|ies)|business(?:es)?|ngos?"
    r"|partnerships?|non[-\s]?individual|trusts?|saccos?|institutions?)\b",
    re.IGNORECASE,
)
_TIN_INDIVIDUAL_QUERY_RE = re.compile(
    r"\b(individuals?|myself|personal|for\s+me|my\s+own|nin)\b",
    re.IGNORECASE,
)

# Curated TIN procedure templates (KB-grounded: Instant TIN is NIN-holders
# only; organisations use the standard Non-Individual registration with
# incorporation documents and director TINs).
_TIN_INDIVIDUAL_STEPS = (
    "To register for an **instant TIN** as an individual:\n\n"
    "1. Go to ura.go.ug\n"
    "2. Click **Get a TIN**\n"
    "3. Choose **Instant TIN**\n"
    "4. Select **Individual**\n"
    "5. Enter your NIN and personal details\n"
    "6. Confirm you are not a robot\n"
    "7. Submit"
)
_TIN_ORG_STEPS = (
    "To register an **organisation** (company, NGO, partnership) for a TIN:\n\n"
    "1. Go to ura.go.ug\n"
    "2. Choose the standard **Non-Individual TIN registration** option\n"
    "3. Fill in the organisation's details\n"
    "4. Attach the incorporation/registration documents\n"
    "5. Provide the TINs of the directors\n"
    "6. Submit the application for URA review\n\n"
    "Note: Instant TIN is only available to individuals with a NIN."
)
_RETURN_FILING_QUERY_RE = re.compile(
    r"\b(?:file|submit|lodge)\b.*\b(?:return|returns)\b|\b(?:return|returns)\b.*\b(?:file|submit|lodge)\b",
    re.IGNORECASE,
)
_REGISTRATION_QUERY_RE = re.compile(
    r"\b(register|registration|get a tin|tin registration|apply for tin|obtain a tin)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Passage cleanup — strip PDF-extraction noise before surfacing a raw retrieved
# chunk to a user (omitted-image blocks, page footers, TOC dot leaders,
# letter-spaced headers, trailing page numbers).  No-op for clean FAQ answers.
# ---------------------------------------------------------------------------
_PIC_BLOCK_RE = re.compile(
    r"=*>?\s*picture\s*\[?\d+\s*[xX]\s*\d+\]?\s*intentionally omitted"
    r".*?-{2,}\s*End of picture text\s*-{2,}",
    re.IGNORECASE | re.DOTALL,
)
_PIC_MARK_RE = re.compile(
    r"-{2,}\s*(?:Start|End) of picture text\s*-{2,}"
    r"|picture\s*\[?\d+\s*[xX]\s*\d+\]?\s*intentionally omitted"
    r"|\bbr\s*-{2,}",
    re.IGNORECASE,
)
_PDF_FOOTER_RE = re.compile(
    r"\*+\s*A Guide to Taxation in Uganda\s*\|\s*[A-Za-z]+\s+Edition[\s\d]*", re.IGNORECASE
)
_PDF_EDITION_RE = re.compile(
    r"\|\s*(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)\s+Edition[\s\d]*",
    re.IGNORECASE,
)
_DOT_LEADER_RE = re.compile(r"(?:\.\s*){4,}")
_SPACED_LETTERS_RE = re.compile(r"(?:\b[A-Za-z]\b[ ]){4,}\b[A-Za-z]\b")
# A stray U+FFFD replacement character between two digits is almost always a
# decimal point that survived a lossy PDF text-extraction encoding step
# (observed corrupting handbook section numbers "8.0" -> "8�0").  Any
# other occurrence carries no recoverable meaning, so it is dropped outright.
_MOJIBAKE_DECIMAL_RE = re.compile(r"(?<=\d)�(?=\d)")
# pymupdf4llm converts PDFs to Markdown for the Vectorize-fallback corpus
# (raw handbook chunks, unlike the clean FAQ/teacher-QA answers Qdrant
# serves), so ATX headings and bold markers leak into extractive-fallback
# replies verbatim (e.g. "## **8.0 About Uganda Revenue Authority**")
# unless stripped here too.  Headings are turned into their own paragraph
# (instead of being deleted in place) so a multi-section chunk still reads
# as distinct paragraphs rather than one run-on wall of text.
_MD_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+")
_MD_BOLD_RE = re.compile(r"\*\*")
_PARA_BREAK_RE = re.compile(r"[ \t]*\n(?:[ \t]*\n)+[ \t]*")
_PARA_SENTINEL = "\x00PARA\x00"


def _clean_passage_text(text: str) -> str:
    """Remove PDF-extraction and Markdown artifacts from a retrieved chunk,
    preserving paragraph breaks; no-op for clean text."""
    if not text:
        return ""
    t = _PIC_BLOCK_RE.sub(" ", text)
    t = _PIC_MARK_RE.sub(" ", t)
    t = _PDF_FOOTER_RE.sub(" ", t)
    t = _PDF_EDITION_RE.sub(" ", t)
    t = _DOT_LEADER_RE.sub(" ", t)
    t = _SPACED_LETTERS_RE.sub(" ", t)
    t = _MOJIBAKE_DECIMAL_RE.sub(".", t)
    t = t.replace("�", "")
    t = _MD_HEADING_RE.sub(_PARA_SENTINEL, t)
    t = _MD_BOLD_RE.sub("", t)
    t = _PARA_BREAK_RE.sub(_PARA_SENTINEL, t)
    t = t.replace("\n", " ")  # any remaining single newline is a mid-paragraph line-wrap
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(rf"(?:{re.escape(_PARA_SENTINEL)}[ ]*)+", _PARA_SENTINEL, t)  # collapse runs
    t = re.sub(rf"^(?:{re.escape(_PARA_SENTINEL)}[ ]*)+", "", t)  # trim leading break
    t = re.sub(rf"(?:{re.escape(_PARA_SENTINEL)}[ ]*)+$", "", t)  # trim trailing break
    t = t.replace(_PARA_SENTINEL, "\n\n")
    t = re.sub(r"[\s;]+\d{1,3}\s*$", "", t)  # trailing orphan page number
    t = re.sub(r"[\s;]+\d{1,2}\.\s*$", "", t)  # trailing orphan list marker
    return t.strip()


def _trim_excerpt(text: str, limit: int = 700) -> str:
    """Trim to ~``limit`` chars at a sentence/clause boundary (never mid-word)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("; "), cut.rfind("\n"))
    if boundary > limit * 0.5:
        return cut[: boundary + 1].rstrip()
    return cut.rsplit(" ", 1)[0].rstrip()


def _structure_excerpt(text: str) -> str:
    """Put an inline numbered list (``...: 1. X; 2. Y``) onto its own lines so it
    renders as a Markdown list instead of a run-on."""
    t = re.sub(r":\s+(?=\d{1,2}\.\s)", ":\n\n", text, count=1)  # blank line before the list
    t = re.sub(r"\s*;\s+(?=\d{1,2}\.\s)", "\n", t)
    return t

# Shared executor for LLM calls — bounded so one slow generation cannot
# exhaust worker threads under load.  Size is small on purpose: Qwen runs
# one inference at a time per process anyway (no true batching without vLLM).
_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.getenv("LLM_MAX_CONCURRENCY", "2")),
    thread_name_prefix="llm",
)
_LLM_CIRCUIT = CircuitBreaker(
    name="llm",
    failure_threshold=3,
    reset_timeout=15.0,
    max_timeout=300.0,
)


def _build_fallback_prompt(
    query: str, passages: list[dict[str, Any]], locale: str, tone_hint: str = ""
) -> tuple[str, str]:
    """RAG system+user prompt for a cloud LLM fallback (Gemini / Workers AI)."""
    ctx = "\n\n".join(
        f"[{i + 1}] {(p.get('text') or p.get('answer') or p.get('question') or '')[:800]}"
        for i, p in enumerate(passages[:6])
    )
    system = (
        "You are the URA (Uganda Revenue Authority) tax assistant. Answer ONLY "
        "from the context below and cite passages like [1]. If the context does "
        "not contain the answer, say you don't have enough information. Be warm "
        "and respectful, never condescending."
    )
    if tone_hint:
        system += f" {tone_hint.strip()}"
    if locale and locale != "en":
        system += f" Respond in the user's language (locale={locale})."
    return system, f"Context:\n{ctx}\n\nQuestion: {query}"


# A generation that stops short with no terminal/citation punctuation reads
# as an early cutoff rather than a deliberately terse answer — genuine short
# replies still land on a sentence, quote, or citation boundary. Seen
# intermittently in production (e.g. "...The Electronic" with nothing after
# it) from whichever backend served that request. No length exemption: an
# earlier version only flagged replies under 60 chars, reasoning a longer
# unterminated reply was less certain to be truncated — but a distress-tone
# reply routinely opens with the model's own empathy sentence (tone_hint
# instructs it to) before the substantive answer, which alone can push a
# genuinely truncated reply over 60 chars (e.g. "I understand you're under
# time pressure! ... the fastest path to your answer:\n\nFor" — 85 chars,
# still cut off mid-word). Every reply on this path is expected to end in a
# sentence or a citation marker regardless of length, so "no terminator" is
# trusted on its own — deterministic templates/calculators never reach this
# check (they bypass the LLM entirely), so this can't misfire on them.
_REPLY_TERMINATORS = (".", "!", "?", '"', "'", ")", "]", "”", "’", "»")


def _looks_truncated(text: str) -> bool:
    """True if *text* reads like a generation that was cut off mid-stream."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return not stripped.endswith(_REPLY_TERMINATORS)


def _llm_cloud_fallback(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str = "",
    tone_hint: str = "",
    deadline: float | None = None,
) -> str:
    """Generate via Cloudflare Gemini / Workers AI when the primary LLM is down.

    Gated by ``cloudflare_fallback`` + ``LLM_FALLBACK_BACKEND`` and each provider's
    circuit breaker + free-tier budget, so it fires only when the primary is
    unavailable and the cloud path is configured/under budget. Returns "" to let
    the caller fall back to the best-hit FAQ answer.

    Each candidate backend that responds but :func:`_looks_truncated` is kept
    as a best-effort ``best`` rather than returned outright — the chain keeps
    trying the next backend for a complete answer, only settling for a short
    reply if nothing better comes back.

    ``deadline`` is an optional absolute ``time.monotonic()`` timestamp (see
    :data:`LLM_TOTAL_BUDGET_SECONDS`) checked before each hop so one request
    can't walk the whole chain unbounded — a small worker pool only needs a
    handful of requests each holding a worker for minutes to queue every
    other request behind them.  An in-flight HTTP call still runs to its own
    timeout; this only stops a *new* hop from starting once time is up.
    """
    if not flags.is_enabled("cloudflare_fallback"):
        return ""
    backend = os.getenv("LLM_FALLBACK_BACKEND", "").strip().lower()
    if backend not in ("gemini", "workers_ai"):
        return ""
    try:
        from .providers import breakers, budget
        from .providers import config as cfg
        from .providers import gateway as gw
        from .providers import routing
    except Exception:  # providers optional / deps missing
        return ""

    def _budget_exhausted() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    best = ""

    if _budget_exhausted():
        logger.warning("LLM fallback chain total budget exhausted before Gemini attempt")
    elif (
        backend == "gemini"
        and cfg.is_gemini_configured()
        and breakers.GEMINI_BREAKER.allow_request()
        and budget.try_consume_gemini_call()
    ):
        system, user = _build_fallback_prompt(query, passages, locale, tone_hint)
        try:
            text = gw.gemini_generate(user, system=system, max_tokens=512, temperature=0.2)
            breakers.GEMINI_BREAKER.record_success()
            routing.log_model_use("llm", "gemini_flash")
            if text and text.strip() and not _looks_truncated(text):
                logger.info("LLM fallback via Gemini succeeded")
                return text
            if text and text.strip():
                logger.warning(
                    "LLM fallback via Gemini looks truncated (%d chars) — trying Workers AI",
                    len(text.strip()),
                )
                best = best or text
        except Exception:
            breakers.GEMINI_BREAKER.record_failure()
            logger.warning("LLM Gemini fallback failed", exc_info=True)

    # Cloudflare Workers AI — the cloud-PRIMARY generator when
    # LLM_PRIMARY_BACKEND=workers_ai, otherwise the cloud fallback.  Reuse the
    # canonical message builder so cloud answers carry the FULL governed prompt
    # (formatting, citation, refusal, multilingual rules) + the LLM01 passage
    # scrub, matching local quality.  Chain: Llama 3.3 70B → Mistral Small 3.1
    # → Llama 4 Scout, all sharing CF_LLM_BREAKER + the neuron budget.
    messages = llm_module._build_messages(
        query=query,
        passages=passages,
        conversation_history=conversation_history,
        locale=locale,
        tokenizer=None,  # CF models have ≥24k context; skip local-tokenizer trim
        structured=llm_module.LLM_STRUCTURED_OUTPUT,
        personalization_context=personalization_context,
        tone_hint=tone_hint,
    )
    # Strip the Qwen-only /no_think directive — a no-op token for Llama/Mistral.
    if messages and messages[0].get("role") == "system":
        sys_content = messages[0]["content"]
        if sys_content.startswith("/no_think"):
            messages[0]["content"] = sys_content.split("\n", 1)[-1]
    cf_chain = (
        routing.CF_LLM_MODEL,
        routing.CF_LLM_FALLBACK_MODEL,
        routing.CF_LLM_FALLBACK_MODEL_2,
    )
    for model in cf_chain:
        if _budget_exhausted():
            logger.warning(
                "LLM fallback chain total budget exhausted — stopping before %s", model
            )
            break
        if not (
            cfg.is_cloudflare_configured()
            and breakers.CF_LLM_BREAKER.allow_request()
            and budget.try_consume_neurons(5)
        ):
            break
        try:
            text = gw.workers_ai_chat(messages, model=model, max_tokens=512, temperature=0.2)
            breakers.CF_LLM_BREAKER.record_success()
            routing.log_model_use("llm", model)
            if text and text.strip() and not _looks_truncated(text):
                logger.info("LLM via Workers AI (%s) succeeded", model)
                return text
            if text and text.strip():
                logger.warning(
                    "LLM via Workers AI (%s) looks truncated (%d chars) — trying next",
                    model,
                    len(text.strip()),
                )
                best = best or text
        except Exception:
            breakers.CF_LLM_BREAKER.record_failure()
            logger.warning("LLM Workers AI failed (model=%s)", model, exc_info=True)
    return best


def _stream_cloud_fallback(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str = "",
    tone_hint: str = "",
) -> Generator[str, None, None]:
    """Yield the cloud-fallback answer in word chunks for the SSE/WS stream path."""
    text = _llm_cloud_fallback(
        query,
        passages,
        conversation_history,
        locale,
        personalization_context,
        tone_hint,
        deadline=time.monotonic() + LLM_TOTAL_BUDGET_SECONDS,
    )
    if not text:
        return
    for chunk in re.findall(r"\S+\s*", text):
        yield chunk


def _cloud_llm_ready() -> bool:
    """True when the flag-gated cloud LLM fallback is configured to serve a
    reply on its own.

    Used by the chat entrypoints to keep RAG generation alive when the local
    LLM is entirely unavailable (``llm_module.is_available()`` False — e.g. an
    LLM-less deployment profile), not merely failing per-request.  Without
    this, the availability gates skip the generation step and the configured
    Cloudflare/Gemini tier never gets a chance.
    """
    if not flags.is_enabled("cloudflare_fallback"):
        return False
    backend = os.getenv("LLM_FALLBACK_BACKEND", "").strip().lower()
    if backend not in ("gemini", "workers_ai"):
        return False
    try:
        from .providers import config as cfg
    except Exception:  # providers optional / deps missing
        return False
    if backend == "gemini" and cfg.is_gemini_configured():
        return True
    return cfg.is_cloudflare_configured()


# Locales kept on the local Qwen3-8B (+ per-language LoRA adapters): the cloud
# models document no Luganda/Runyankole/Acholi support, so a blanket cloud flip
# would regress them.  Override with LOCAL_PRIMARY_LOCALES (comma-separated).
LOCAL_PRIMARY_LOCALES = frozenset(
    s.strip().lower()
    for s in os.getenv("LOCAL_PRIMARY_LOCALES", "lg,nyn,ach").split(",")
    if s.strip()
)


def _prefer_cloud_primary(locale: str) -> bool:
    """Hybrid model routing: should the cloud chain run BEFORE the local LLM?

    Opt-in via ``LLM_PRIMARY_BACKEND`` (``workers_ai``/``cloudflare``/``cloud``)
    so the default stays the resilient local-first behaviour.  Cloud leads for
    high-resource locales (English, Swahili, …) where the CF chain (Llama 3.3
    70B → Mistral Small 3.1 → Llama 4 Scout) beats the local 8B; the Ugandan
    languages in :data:`LOCAL_PRIMARY_LOCALES` stay local-primary, and local is
    the universal fallback for everyone.  Returns ``False`` whenever the cloud
    tier is not actually configured/ready, so misconfiguration degrades safely
    to local-first.
    """
    if os.getenv("LLM_PRIMARY_BACKEND", "local").strip().lower() not in (
        "workers_ai",
        "cloudflare",
        "cloud",
    ):
        return False
    if (locale or "en").strip().lower() in LOCAL_PRIMARY_LOCALES:
        return False
    return _cloud_llm_ready()


def _call_llm_with_deadline(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str = "",
    deadline_s: float = LLM_DEADLINE_SECONDS,
    tone_hint: str = "",
) -> str:
    """Generate a reply, honouring the hybrid cloud/local routing policy.

    For cloud-primary locales (see :func:`_prefer_cloud_primary`) the CF chain
    runs first with the local Qwen3-8B as the fallback; otherwise the resilient
    local-first path runs with the cloud chain as its fallback.
    """
    if _prefer_cloud_primary(locale):
        text = _llm_cloud_fallback(
            query,
            passages,
            conversation_history,
            locale,
            personalization_context,
            tone_hint,
            deadline=time.monotonic() + LLM_TOTAL_BUDGET_SECONDS,
        )
        if text and text.strip():
            return text
        logger.warning("Cloud-primary LLM unavailable/empty — falling back to local Qwen3-8B")
        return _local_llm_then_cloud(
            query,
            passages,
            conversation_history,
            locale,
            personalization_context,
            deadline_s,
            allow_cloud_fallback=False,  # cloud already attempted above
            tone_hint=tone_hint,
        )
    return _local_llm_then_cloud(
        query,
        passages,
        conversation_history,
        locale,
        personalization_context,
        deadline_s,
        allow_cloud_fallback=True,
        tone_hint=tone_hint,
    )


def _local_llm_then_cloud(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str = "",
    deadline_s: float = LLM_DEADLINE_SECONDS,
    *,
    allow_cloud_fallback: bool = True,
    tone_hint: str = "",
) -> str:
    """Run ``llm_module.generate`` under a hard wall-clock deadline.

    The generation runs on a bounded executor, guarded by a dedicated
    circuit breaker.  On timeout, breaker failure, or empty/exception we route
    to the cloud chain (when ``allow_cloud_fallback``) and otherwise return an
    empty string so the caller falls back to FAQ lookup.
    """
    # Shared budget for local + every cloud hop this request may walk (see
    # LLM_TOTAL_BUDGET_SECONDS). Starts now, before the local attempt, so a
    # local generation that runs close to its own deadline_s leaves
    # correspondingly less time for the cloud chain rather than the two
    # budgets stacking on top of each other.
    chain_deadline = time.monotonic() + LLM_TOTAL_BUDGET_SECONDS

    def _cloud() -> str:
        if not allow_cloud_fallback:
            return ""
        return _llm_cloud_fallback(
            query,
            passages,
            conversation_history,
            locale,
            personalization_context,
            tone_hint,
            deadline=chain_deadline,
        )

    if not _LLM_CIRCUIT.allow_request():
        logger.warning("LLM circuit breaker OPEN — trying cloud fallback")
        return _cloud()

    future = _LLM_EXECUTOR.submit(
        llm_module.generate,
        query=query,
        passages=passages,
        conversation_history=conversation_history,
        locale=locale,
        personalization_context=personalization_context,
        tone_hint=tone_hint,
    )
    try:
        reply = future.result(timeout=deadline_s)
        if reply and reply.strip():
            _LLM_CIRCUIT.record_success()
            if not _looks_truncated(reply):
                return reply
            # Responded, so this isn't a breaker-worthy failure — but the
            # reply looks cut off mid-stream, so try the cloud chain for a
            # complete answer before settling for it.
            logger.warning(
                "LLM reply looks truncated (%d chars, no terminal punctuation) — "
                "trying cloud fallback",
                len(reply.strip()),
            )
            cloud_reply = _cloud()
            return cloud_reply if (cloud_reply and cloud_reply.strip()) else reply
        # Empty reply — llm_module.generate logs+swallows its own errors and
        # returns "" (e.g. _vllm_generate on HTTP failure), so an empty string
        # is our only failure signal here. Mirror the streaming path: record a
        # failure and try the cloud fallback before the caller drops to the
        # extractive best-hit answer.
        _LLM_CIRCUIT.record_failure()
        logger.warning("LLM returned empty — trying cloud fallback")
        return _cloud()
    except concurrent.futures.TimeoutError:
        future.cancel()  # best-effort; transformers generate may ignore
        _LLM_CIRCUIT.record_failure()
        logger.warning("LLM deadline %.1fs exceeded", deadline_s)
        return _cloud()
    except Exception:
        _LLM_CIRCUIT.record_failure()
        logger.exception("LLM generation raised")
        return _cloud()


def stream_llm_tokens(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str = "",
    cancel_event: threading.Event | None = None,
    tone_hint: str = "",
) -> Generator[str, None, None]:
    """Stream LLM tokens through the shared circuit breaker.

    Mirrors :func:`_call_llm_with_deadline` for the SSE streaming path.
    Yields nothing when the breaker is OPEN, the generator raises,
    or no tokens are produced — the caller then falls back to
    returning the best-hit answer as a single event.

    Passing ``cancel_event`` lets the caller cooperatively stop token
    generation at the next yield boundary (e.g. when an SSE client
    disconnects or a WS client emits ``response.cancel``).  The
    underlying transformer thread cannot be killed; the next decoded
    token is the cancellation latency floor.

    Honours the hybrid routing policy: cloud-primary locales stream the CF
    answer first (chunked) with the local model as fallback; everyone else
    streams locally with the cloud chain as fallback.
    """
    if _prefer_cloud_primary(locale):
        saw_cloud = False
        for chunk in _stream_cloud_fallback(
            query, passages, conversation_history, locale, personalization_context, tone_hint
        ):
            if cancel_event is not None and cancel_event.is_set():
                return
            saw_cloud = True
            yield chunk
        if saw_cloud:
            return
        logger.warning("Cloud-primary stream unavailable — falling back to local Qwen3-8B")
        yield from _stream_local_then_cloud(
            query,
            passages,
            conversation_history,
            locale,
            personalization_context,
            cancel_event,
            allow_cloud_fallback=False,  # cloud already attempted above
            tone_hint=tone_hint,
        )
        return
    yield from _stream_local_then_cloud(
        query,
        passages,
        conversation_history,
        locale,
        personalization_context,
        cancel_event,
        allow_cloud_fallback=True,
        tone_hint=tone_hint,
    )


def _stream_local_then_cloud(
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str = "",
    cancel_event: threading.Event | None = None,
    *,
    allow_cloud_fallback: bool = True,
    tone_hint: str = "",
) -> Generator[str, None, None]:
    """Stream from the local model; route to the cloud chain on failure.

    Yields nothing when the breaker is OPEN, the generator raises, or no tokens
    are produced AND ``allow_cloud_fallback`` is False — the caller then falls
    back to returning the best-hit answer as a single event.
    """

    def _cloud_stream() -> Generator[str, None, None]:
        if allow_cloud_fallback:
            yield from _stream_cloud_fallback(
                query, passages, conversation_history, locale, personalization_context, tone_hint
            )

    if not llm_module.is_available():
        yield from _cloud_stream()
        return
    if not _LLM_CIRCUIT.allow_request():
        logger.warning("LLM circuit breaker OPEN — streaming via cloud fallback")
        yield from _cloud_stream()
        return

    saw_tokens = False
    try:
        for token in llm_module.generate_stream(
            query=query,
            passages=passages,
            conversation_history=conversation_history,
            locale=locale,
            personalization_context=personalization_context,
            tone_hint=tone_hint,
        ):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("LLM stream cancelled by caller")
                break
            if not token:
                continue
            saw_tokens = True
            yield token

        if saw_tokens:
            _LLM_CIRCUIT.record_success()
        else:
            # Empty stream counts as a soft failure — the breaker tracks
            # it so a continually empty worker eventually trips.
            _LLM_CIRCUIT.record_failure()
            yield from _cloud_stream()
    except Exception:
        _LLM_CIRCUIT.record_failure()
        logger.exception("LLM streaming raised")
        yield from _cloud_stream()
        return


def _call_llm_agentic(  # noqa: PLR0913 — all args are request-scoped config
    query: str,
    passages: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    *,
    tool_names: list[str] | None = None,
    max_iterations: int | None = None,
    personalization_context: str = "",
    tone_hint: str = "",
    tenant_id: str = "default",
    user_id: str = "",
    user_role: str = "public",
    granted_purposes: list[str] | None = None,
    deadline_s: float = LLM_DEADLINE_SECONDS * 2,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    agent_role: str = "",
) -> dict[str, Any]:
    """Run :func:`llm_module.generate_with_tools` under breaker + deadline.

    Returns the same dict shape as ``generate_with_tools``:
    ``{"text", "tool_calls", "iterations", "truncated"}``.  On breaker
    OPEN, timeout, or exception, returns an empty result so the caller
    can fall back to the non-agentic path (``_call_llm_with_deadline``).

    Deadline is doubled by default because the tool-call loop runs
    multiple generations — a typical 2-hop call takes ~2x a single
    generate().
    """
    empty = {"text": "", "tool_calls": [], "iterations": 0, "truncated": False}
    if not _LLM_CIRCUIT.allow_request():
        logger.warning("LLM circuit breaker OPEN — skipping agentic path")
        return empty

    if max_iterations is None:
        max_iterations = _resolve_tool_max_iterations()

    future = _LLM_EXECUTOR.submit(
        llm_module.generate_with_tools,
        query=query,
        passages=passages or None,
        tool_names=tool_names,
        conversation_history=conversation_history,
        locale=locale,
        max_iterations=max_iterations,
        personalization_context=personalization_context,
        tone_hint=tone_hint,
        tenant_id=tenant_id,
        user_id=user_id,
        user_role=user_role,
        granted_purposes=granted_purposes or [],
        event_callback=event_callback,
        agent_role=agent_role,
    )
    try:
        result = future.result(timeout=deadline_s)
        if result and result.get("text"):
            _LLM_CIRCUIT.record_success()
            return result
        # Empty text counts as a soft failure
        _LLM_CIRCUIT.record_failure()
        return result or empty
    except concurrent.futures.TimeoutError:
        future.cancel()
        _LLM_CIRCUIT.record_failure()
        logger.warning("agentic LLM deadline %.1fs exceeded", deadline_s)
        return empty
    except Exception:
        _LLM_CIRCUIT.record_failure()
        logger.exception("agentic LLM generation raised")
        return empty


# Phase 2: the historical default was 3 (UX defense — agentic UX was opaque
# so we capped runaway loops).  With Phase 2 events streamed to the client,
# the UI shows every iteration, so we raise the default to 10 and let
# operators override via LLM_TOOL_MAX_ITER up to a hard cap of 20.
_LLM_TOOL_MAX_ITER_HARD_CAP = 20
_LLM_TOOL_MAX_ITER_DEFAULT = 10


def _resolve_tool_max_iterations() -> int:
    """Resolve the per-turn tool-iteration budget from env."""
    raw = os.getenv("LLM_TOOL_MAX_ITER")
    if not raw:
        return _LLM_TOOL_MAX_ITER_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        logger.warning("LLM_TOOL_MAX_ITER=%r is not an int; using default", raw)
        return _LLM_TOOL_MAX_ITER_DEFAULT
    return max(1, min(value, _LLM_TOOL_MAX_ITER_HARD_CAP))


# Phase 6: per-turn deadline.  Default 120 s gives the agentic loop room
# for several tool round trips while still bounding worst-case latency.
# A turn that exceeds the deadline is cancelled cooperatively at the
# next token / await boundary.  Set to 0 to disable.
_TURN_DEADLINE_DEFAULT_S = 120


def _resolve_turn_deadline() -> float:
    raw = os.getenv("AGENTIC_TURN_DEADLINE_S")
    if raw is None:
        return _TURN_DEADLINE_DEFAULT_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("AGENTIC_TURN_DEADLINE_S=%r is not numeric; using default", raw)
        return _TURN_DEADLINE_DEFAULT_S


# ---------------------------------------------------------------------------
# Transport-agnostic chat turn generator (Phase 1 of the WS upgrade)
# ---------------------------------------------------------------------------

# Bounded queue for streamed tokens.  Unbounded queues are an OOM hazard
# when a slow client lets the LLM pump out faster than tokens are drained.
_STREAM_QUEUE_MAX = 256


def _apply_output_guards(
    model: Any,
    *,
    message: str,
    reply: str,
    hits: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    session_id: str | None,
    conversation_id: str,
    output_guard: Any,
    existing_handoff: dict[str, Any] | None = None,
    existing_ticket_id: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    """Run the full post-generation guard pipeline for a streamed turn.

    Faithfulness → claim verification → response judge → grounded revision →
    escalation → handoff → ticket.  This is called by BOTH the token-streaming
    branch and the agentic (tool-use) branch of :func:`run_chat_turn` so the
    two paths enforce an identical safety bar — previously the agentic branch
    only computed faithfulness and skipped the judge, revision, claim
    verification, and escalation/handoff/ticket steps (P0-3).

    ``reply`` is assumed already PII-redacted + sanitized by the caller.  The
    returned ``reply`` may differ (a grounded revision was substituted); when
    ``revised`` is True the caller should emit a ``("revision", reply)`` event.
    """
    contexts = [h.get("text") or h.get("answer", "") for h in hits]
    faith = HybridRetriever.compute_faithfulness(reply, contexts)
    escalate, esc_reason = output_guard.should_escalate(faith, hits)

    claim_report: dict[str, Any] | None = None
    if reply and hits and citations:
        try:
            claim_report = verify_claims(reply, citations, hits)
        except Exception:
            logger.debug("claim verification failed", exc_info=True)
            claim_report = None

    response_judge = model._evaluate_response_judge(
        message=message,
        reply=reply,
        hits=hits,
        citations=citations,
        faithfulness_score=faith,
        escalation_required=escalate,
        escalation_reason=esc_reason,
        claim_report=claim_report,
    )

    revised = False
    if response_judge.get("decision") == "revise" and response_judge.get("revised_reply"):
        reply = output_guard.sanitize(output_guard.redact_pii(response_judge["revised_reply"]))
        faith = HybridRetriever.compute_faithfulness(reply, contexts)
        escalate, esc_reason = output_guard.should_escalate(faith, hits)
        response_judge["applied_revision"] = True
        response_judge["final_decision"] = "escalate" if escalate else "approve"
        revised = True
        # Re-verify the substituted text so a revision can't smuggle in
        # unsupported claims.
        if citations:
            try:
                claim_report = verify_claims(reply, citations, hits)
                if claim_report.get("decision") == "escalate":
                    response_judge["final_decision"] = "escalate"
            except Exception:
                logger.debug("post-revision claim verification failed", exc_info=True)
    else:
        response_judge["final_decision"] = response_judge.get("decision", "approve")

    if response_judge.get("final_decision") == "escalate":
        escalate = True
        if not esc_reason:
            esc_reason = "; ".join(response_judge.get("reasons") or [])
    if claim_report is not None:
        response_judge["claim_verification"] = claim_report
    response_judge.pop("revised_reply", None)

    handoff = existing_handoff
    if escalate and not handoff:
        handoff = model._build_handoff_packet(
            message=message,
            reason=esc_reason,
            conversation_history=conversation_history or None,
            hits=hits,
            faithfulness_score=faith,
        )
    ticket_id = existing_ticket_id
    if escalate and not ticket_id:
        ticket_id = model._maybe_create_ticket(
            reason=esc_reason,
            user_query=message,
            bot_reply=reply,
            session_id=session_id or None,
            conversation_id=conversation_id or "",
            priority=(handoff or {}).get("priority", "normal"),
            handoff=handoff,
            response_judge=response_judge,
            user_id=user_id,
        )

    return {
        "reply": reply,
        "faithfulness": faith,
        "escalate": escalate,
        "escalation_reason": esc_reason,
        "response_judge": response_judge,
        "handoff": handoff,
        "ticket_id": ticket_id,
        "revised": revised,
        "claim_report": claim_report,
    }


async def run_chat_turn(  # noqa: PLR0912, PLR0915 — long but mirrors SSE generator one-to-one
    model: Any,
    *,
    message: str,
    conversation_id: str | None,
    top_k: int,
    locale: str,
    session_id: str | None,
    request_id: str | None,
    user_id: str | None,
    tenant_id: str,
    should_continue: Callable[[], "asyncio.Future[bool] | bool"] | None = None,
    cancel_event: threading.Event | None = None,
    sentence_batching: bool = True,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    user_role: str = "public",
    granted_purposes: list[str] | None = None,
    conversation_history_override: list[dict[str, str]] | None = None,
    turn_deadline_s: float | None = None,
    attachments: list[Any] | None = None,
) -> "AsyncIterator[tuple[str, Any]]":
    """Run a single chat turn and stream event tuples to the caller.

    This is the transport-agnostic core that backs both SSE
    (``/v1/chat/stream``) and WebSocket (``/v2/chat/stream``).  Yielded
    tuples have the shape ``(event_type, payload)``:

    * ``("metadata", dict)`` — pre-token metadata (sources, citations, ...).
    * ``("token", str)``     — a sanitized text chunk.
    * ``("revision", str)``  — judge revised the full reply; new text.
    * ``("grounding", dict)``— faithfulness, escalation, handoff, judge.
    * ``("done", "")``       — terminal frame for a turn.
    * ``("error", dict)``    — payload has ``code`` and ``message``.
    * ``("_log", dict)``     — internal final frame for adapter logging.
                               Payload: ``{"result", "full_reply", "elapsed_ms"}``.
                               Adapters consume this and do not forward it.

    Parameters
    ----------
    sentence_batching:
        SSE keeps this ``True`` to match historical behaviour; WS sets
        ``False`` for lower TTFT (per-token frames pass through the same
        OutputGuard sanitiser).
    should_continue:
        Optional callable polled between awaits.  Returning ``False``
        (or an awaitable that resolves to ``False``) cancels the turn.
        SSE wires this to ``request.is_disconnected``; WS sets it from
        its socket-state check.
    cancel_event:
        Optional :class:`threading.Event`.  When set (by ``response.cancel``,
        client disconnect, or a future deadline), token generation halts at
        the next decoded token.  Created internally if absent.
    event_callback:
        Phase 2 hook; ignored in Phase 1.
    """
    import inspect

    from .guardrails import OutputGuard

    _output_guard = OutputGuard()
    t0 = time.perf_counter()
    full_reply = ""
    result: dict[str, Any] = {}
    cancel_event = cancel_event or threading.Event()
    deadline_s = turn_deadline_s if turn_deadline_s is not None else _resolve_turn_deadline()

    async def _should_stop() -> bool:
        if cancel_event.is_set():
            return True
        if deadline_s > 0 and (time.perf_counter() - t0) > deadline_s:
            logger.warning("run_chat_turn deadline %.1fs exceeded", deadline_s)
            cancel_event.set()
            return True
        if should_continue is None:
            return False
        outcome = should_continue()
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return not bool(outcome)

    try:
        yield (
            "retrieval.started",
            {"top_k": top_k, "query_preview": message[:200]},
        )

        result = await asyncio.to_thread(
            model.generate_retrieval_only,
            message=message,
            conversation_id=conversation_id,
            top_k=top_k,
            locale=locale,
            session_id=session_id or None,
            request_id=request_id,
            user_id=user_id or None,
            tenant_id=tenant_id,
            conversation_history_override=conversation_history_override,
            attachments=attachments,
        )

        yield (
            "retrieval.completed",
            {
                "hit_count": len(result.get("_hits", []) or []),
                "retrieval_mode": result.get("retrieval_mode"),
                "sources": result.get("sources", []),
            },
        )

        # Short-circuit branches: blocked / abstained / clarification /
        # workflow / escalated — plus deterministic procedural replies
        # (``_short_circuit``) — skip the LLM stream and return a single
        # bundled payload.
        if result.get("retrieval_mode") in (
            "blocked",
            "abstained",
            "clarification",
            "workflow",
            "escalated",
        ) or result.get("_short_circuit"):
            yield ("metadata", _metadata_payload(result, include_short_circuit=True))
            full_reply = result.get("reply", "")
            yield ("token", full_reply)
            yield ("done", "")
            yield (
                "_log",
                {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
            )
            return

        yield ("metadata", _metadata_payload(result, include_short_circuit=False))

        hits = result.get("_hits", [])
        conversation_history = result.get("_history", [])
        rewritten_query = result.get("_rewritten", message)
        personalization_context = result.get("_personalization_context", "")
        tone_hint = str(result.get("_tone_hint") or "")
        distress = str(result.get("_distress") or "")

        # ── Phase 2: optional agentic branch ─────────────────────────
        # When tool_use is enabled, run the bounded tool-calling loop
        # and surface every tool event as part of the same stream.  The
        # final answer text is yielded as a single token frame because
        # the agentic path produces a complete reply (no per-token
        # streaming for tool calls — that's a tradeoff documented in
        # docs/ws_chat_protocol.md).
        if llm_module.is_available() and hits and flags.is_enabled("tool_use"):
            async for event in _stream_agentic_turn(
                rewritten_query=rewritten_query,
                hits=hits,
                conversation_history=conversation_history,
                locale=locale,
                personalization_context=str(personalization_context or ""),
                tone_hint=tone_hint,
                tenant_id=tenant_id,
                user_id=user_id or "",
                user_role=user_role,
                granted_purposes=granted_purposes or [],
                cancel_event=cancel_event,
                _output_guard=_output_guard,
            ):
                if event[0] == "_full_reply":
                    full_reply = event[1]
                    continue
                yield event

            if full_reply:
                # P0-3: run the SAME post-generation guard pipeline the
                # token-streaming branch uses (judge + claim verification +
                # grounded revision + escalation/handoff/ticket), not just a
                # bare faithfulness score.
                guard = _apply_output_guards(
                    model,
                    message=message,
                    reply=full_reply,
                    hits=hits,
                    citations=result.get("citations", []),
                    conversation_history=conversation_history,
                    session_id=session_id,
                    conversation_id=result.get("conversation_id") or conversation_id or "",
                    output_guard=_output_guard,
                    existing_handoff=result.get("handoff"),
                    existing_ticket_id=result.get("ticket_id", ""),
                    user_id=user_id or "",
                )
                full_reply = guard["reply"]
                if guard["revised"]:
                    yield ("revision", full_reply)
                result["handoff"] = guard["handoff"]
                result["response_judge"] = guard["response_judge"]
                result["ticket_id"] = guard["ticket_id"]
                yield (
                    "grounding",
                    {
                        "faithfulness_score": guard["faithfulness"],
                        "escalation_required": guard["escalate"],
                        "escalation_reason": guard["escalation_reason"],
                        "agent_role": result.get("agent_role", "rag_answerer"),
                        "handoff": guard["handoff"],
                        "response_judge": guard["response_judge"],
                        "next_actions": result.get("next_actions", []),
                        "ticket_id": guard["ticket_id"],
                    },
                )
                yield ("done", "")
                yield (
                    "_log",
                    {
                        "result": result,
                        "full_reply": full_reply,
                        "elapsed_ms": (time.perf_counter() - t0) * 1000,
                    },
                )
                return

        # The cloud fallback alone keeps token streaming on when no local LLM
        # is configured — stream_llm_tokens routes straight to it in that case.
        if hits and (llm_module.is_available() or _cloud_llm_ready()):
            loop = asyncio.get_running_loop()
            token_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue(
                maxsize=_STREAM_QUEUE_MAX
            )

            def _pump_tokens() -> None:
                try:
                    for token in stream_llm_tokens(
                        query=rewritten_query,
                        passages=hits,
                        conversation_history=conversation_history or None,
                        locale=locale,
                        personalization_context=str(personalization_context or ""),
                        cancel_event=cancel_event,
                        tone_hint=tone_hint,
                    ):
                        # Bounded queue: if the consumer is slow, block here
                        # rather than balloon memory.  call_soon_threadsafe
                        # schedules an awaitable put on the event loop.
                        future = asyncio.run_coroutine_threadsafe(
                            token_queue.put(("token", token)), loop
                        )
                        try:
                            future.result(timeout=30)
                        except Exception:
                            # Consumer gone / loop closed / queue full beyond
                            # backpressure window — stop pumping.
                            return
                finally:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            token_queue.put(("done", None)), loop
                        ).result(timeout=5)
                    except Exception:
                        pass

            threading.Thread(target=_pump_tokens, daemon=True).start()

            saw_streamed_token = False
            pending_stream_chunk = ""

            def _flush_stream_chunk(*, force: bool = False) -> str:
                nonlocal pending_stream_chunk, full_reply, saw_streamed_token
                if not pending_stream_chunk:
                    return ""
                if (
                    sentence_batching
                    and not force
                    and not re.search(r"(?:\n\s*\n|[.!?](?:\s|$))", pending_stream_chunk)
                ):
                    return ""
                sanitized = _output_guard.sanitize(pending_stream_chunk)
                pending_stream_chunk = ""
                if not sanitized:
                    return ""
                saw_streamed_token = True
                full_reply += sanitized
                return sanitized

            while True:
                if await _should_stop():
                    logger.info("chat turn cancelled mid-stream")
                    cancel_event.set()
                    return
                try:
                    event_type, payload = await asyncio.wait_for(
                        token_queue.get(), timeout=15
                    )
                except asyncio.TimeoutError:
                    # Heartbeat: yield an empty token tuple so adapters can
                    # send keepalive comments without inventing a new event.
                    yield ("_keepalive", "")
                    continue

                if event_type == "done":
                    final_chunk = _flush_stream_chunk(force=True)
                    if final_chunk:
                        yield ("token", final_chunk)
                    break

                pending_stream_chunk += payload or ""
                sanitized = _flush_stream_chunk()
                if not sanitized:
                    continue
                yield ("token", sanitized)

            if not saw_streamed_token:
                # Pump produced nothing (breaker open or empty stream) — the
                # tone_hint never reached a model, so the extractive fallback
                # carries the empathy acknowledgment itself (EI parity).
                full_reply = result.get("reply", "")
                if distress and full_reply:
                    full_reply = f"{empathy_ack(distress)}\n\n{full_reply}"
                yield ("token", full_reply)
                yield ("done", "")
                yield (
                    "_log",
                    {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
                )
                return

            full_reply = _output_guard.redact_pii(full_reply)

            guard = _apply_output_guards(
                model,
                message=message,
                reply=full_reply,
                hits=hits,
                citations=result.get("citations", []),
                conversation_history=conversation_history,
                session_id=session_id,
                conversation_id=result.get("conversation_id") or conversation_id or "",
                output_guard=_output_guard,
                existing_handoff=result.get("handoff"),
                existing_ticket_id=result.get("ticket_id", ""),
                user_id=user_id or "",
            )
            full_reply = guard["reply"]
            faith = guard["faithfulness"]
            escalate = guard["escalate"]
            esc_reason = guard["escalation_reason"]
            response_judge = guard["response_judge"]
            handoff = guard["handoff"]
            ticket_id = guard["ticket_id"]
            if guard["revised"]:
                yield ("revision", full_reply)

            result["handoff"] = handoff
            result["response_judge"] = response_judge
            result["ticket_id"] = ticket_id

            yield (
                "grounding",
                {
                    "faithfulness_score": faith,
                    "escalation_required": escalate,
                    "escalation_reason": esc_reason,
                    "agent_role": result.get("agent_role", "rag_answerer"),
                    "handoff": handoff,
                    "response_judge": response_judge,
                    "next_actions": result.get("next_actions", []),
                    "ticket_id": ticket_id,
                },
            )

            try:
                model._cache.put(
                    rewritten_query,
                    {
                        "reply": full_reply,
                        "sources": result.get("sources", []),
                        "citations": result.get("citations", []),
                        "faithfulness_score": faith,
                        "retrieval_mode": result.get("retrieval_mode"),
                        "model": result.get("model"),
                        "conversation_id": result.get("conversation_id"),
                        "locale": result.get("locale"),
                        "escalation_required": escalate,
                        "escalation_reason": esc_reason,
                        "agent_role": result.get("agent_role", "rag_answerer"),
                        "handoff": handoff,
                        "response_judge": response_judge,
                        "next_actions": result.get("next_actions", []),
                        "ticket_id": ticket_id,
                    },
                )
            except Exception:
                logger.debug("Stream cache store failed", exc_info=True)
        else:
            # No LLM tier at all — extractive reply, same EI parity as above.
            full_reply = result.get("reply", "")
            if distress and full_reply:
                full_reply = f"{empathy_ack(distress)}\n\n{full_reply}"
            yield ("token", full_reply)

        yield ("done", "")
        yield (
            "_log",
            {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
        )

    except Exception:
        logger.exception("run_chat_turn error")
        yield ("error", {"code": "internal", "message": "Internal server error"})
        yield ("done", "")
        yield (
            "_log",
            {"result": result, "full_reply": full_reply, "elapsed_ms": (time.perf_counter() - t0) * 1000},
        )


async def _stream_agentic_turn(  # noqa: PLR0913 — request-scoped configuration
    *,
    rewritten_query: str,
    hits: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None,
    locale: str,
    personalization_context: str,
    tone_hint: str = "",
    tenant_id: str,
    user_id: str,
    user_role: str,
    granted_purposes: list[str],
    cancel_event: threading.Event,
    _output_guard: Any,
) -> "AsyncIterator[tuple[str, Any]]":
    """Run the agentic tool-call loop and stream its events.

    Wraps :func:`_call_llm_agentic` in a coroutine task so we can drain
    its ``event_callback`` events while it's still running.  Yields:

    * ``("tool_call.started", dict)``
    * ``("tool_call.completed", dict)``
    * ``("tool_call.error", dict)``
    * ``("iteration.started", dict)``
    * ``("iteration.final", dict)``
    * ``("token", str)`` — the final answer text (single chunk)
    * ``("_full_reply", str)`` — internal: full text for the caller
                                 to pass to the grounding stage.
    """
    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)

    def _emit(ev: dict[str, Any]) -> None:
        if cancel_event.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(event_queue.put(ev), loop).result(
                timeout=5
            )
        except Exception:
            logger.debug("agentic event drop", exc_info=True)

    async def _run_in_thread() -> dict[str, Any]:
        return await asyncio.to_thread(
            _call_llm_agentic,
            query=rewritten_query,
            passages=hits,
            conversation_history=conversation_history,
            locale=locale,
            personalization_context=personalization_context,
            tone_hint=tone_hint,
            tenant_id=tenant_id,
            user_id=user_id,
            user_role=user_role,
            granted_purposes=granted_purposes,
            event_callback=_emit,
        )

    agentic_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(_run_in_thread())

    try:
        while not agentic_task.done():
            try:
                ev = await asyncio.wait_for(event_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if cancel_event.is_set():
                    agentic_task.cancel()
                    return
                continue
            yield (ev.get("type", "tool_event"), ev)

        # Drain anything queued after the task finished.
        while not event_queue.empty():
            ev = event_queue.get_nowait()
            yield (ev.get("type", "tool_event"), ev)

        agentic = agentic_task.result()
    except Exception:
        logger.exception("agentic stream failed")
        yield ("error", {"code": "agentic_failed", "message": "Tool loop error"})
        return

    text = (agentic or {}).get("text", "")
    if not text:
        # Nothing to say — yield an empty marker and let the caller
        # fall back to the regular streaming path on the next branch.
        return

    sanitized = _output_guard.sanitize(_output_guard.redact_pii(text))
    if sanitized:
        yield ("token", sanitized)
        yield ("_full_reply", sanitized)


def _metadata_payload(result: dict[str, Any], *, include_short_circuit: bool) -> dict[str, Any]:
    """Build the ``metadata`` event payload from a retrieval result."""
    payload: dict[str, Any] = {
        "sources": result.get("sources", []),
        "citations": result.get("citations", []),
        "retrieval_mode": result.get("retrieval_mode"),
        "model": result.get("model"),
        "conversation_id": result.get("conversation_id"),
        "locale": result.get("locale"),
        "agent_role": result.get("agent_role", "rag_answerer"),
        "response_judge": result.get("response_judge"),
        "next_actions": result.get("next_actions", []),
        "ticket_id": result.get("ticket_id", ""),
    }
    if include_short_circuit:
        payload.update(
            {
                "faithfulness_score": result.get("faithfulness_score"),
                "escalation_required": result.get("escalation_required", False),
                "escalation_reason": result.get("escalation_reason", ""),
                "workflow": result.get("workflow"),
                "handoff": result.get("handoff"),
            }
        )
    return payload


def _closing_courtesy_reply(message: str) -> str:
    """Reply for a gratitude/farewell turn, or "" when *message* is neither.

    Exact-phrase matching (max four words) on purpose: mixed messages like
    "thanks, but it still fails" must fall through to distress detection
    and retrieval rather than end the conversation on a sign-off.
    """
    text = message.strip().lower().strip("!.?, ")
    if len(text.split()) > 4:
        return ""
    if text in _GRATITUDE_PHRASES:
        return GRATITUDE_REPLY
    if text in _FAREWELL_PHRASES:
        return FAREWELL_REPLY
    return ""


def _load_faq_data(data_dir: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    """Load all ``ura_*_faqs.csv`` files into an in-memory FAQ index.

    Returns ``(faq_index, tag_labels)`` where *faq_index* is keyed by tag and
    *tag_labels* maps tag IDs to human-readable names.
    """
    faq_index: dict[str, list[dict[str, str]]] = {}
    tag_labels: dict[str, str] = {}

    if not data_dir.is_dir():
        logger.warning("FAQ data directory not found: %s", data_dir)
        return faq_index, tag_labels

    for csv_path in sorted(data_dir.glob("ura_*_faqs.csv")):
        tag = csv_path.stem.replace("ura_", "").replace("_faqs", "")
        label = tag.replace("_", " ").title()
        tag_labels[tag] = label

        entries: list[dict[str, str]] = []
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    q = (row.get("question") or row.get("Question") or "").strip()
                    a = (row.get("answer") or row.get("Answer") or "").strip()
                    if q and a:
                        entries.append({"question": q, "answer": a, "source": csv_path.name})
        except Exception:
            logger.exception("Failed to load %s", csv_path)

        if entries:
            faq_index[tag] = entries
            logger.info("Loaded %d FAQs from %s (tag=%s)", len(entries), csv_path.name, tag)

    logger.info(
        "FAQ index ready – %d tags, %d total entries",
        len(faq_index),
        sum(len(v) for v in faq_index.values()),
    )
    return faq_index, tag_labels


_STOP_WORDS = frozenset(
    "a an the is are was were be been am do does did will would shall should "
    "can could may might must have has had of in on at to for with by from "
    "and or not no nor but so if then than that this these those it its i me "
    "my we our you your he she they them their what which who whom how when "
    "where why all each every any some".split()
)

# Keyword retrieval is deliberately conservative.  A FAQ answer can contain
# common words such as "tax", "return", or "registration" while answering a
# completely different question.  Those accidental matches used to be passed
# to the LLM as authoritative context, which made an unrelated question look
# grounded because the generated answer matched the wrong passage.
_FAQ_QUERY_STOP_WORDS = _STOP_WORDS | frozenset(
    {
        "about",
        "could",
        "current",
        "details",
        "information",
        "know",
        "like",
        "please",
        "tell",
        "want",
    }
)
_FAQ_TERM_ALIASES = {
    "applications": "application",
    "deadlines": "deadline",
    "documents": "document",
    "filing": "file",
    "filings": "file",
    "imports": "import",
    "penalties": "penalty",
    "payments": "payment",
    "rates": "rate",
    "returns": "return",
    "taxes": "tax",
    "thresholds": "threshold",
    "vehicles": "vehicle",
}
_FAQ_MATCH_MIN = float(os.getenv("FAQ_MATCH_MIN", "0.58"))
_FAQ_MATCH_RELATIVE = float(os.getenv("FAQ_MATCH_RELATIVE", "0.82"))
# Minimum share of an FAQ question's own terms that the query must account for
# before a fully-contained query counts as being about that FAQ's subject.
_FAQ_SUBJECT_FOCUS_MIN = float(os.getenv("FAQ_SUBJECT_FOCUS_MIN", "0.34"))
# Canonical FAQ rows reach the retriever as ``csv`` from the in-memory loader
# and as ``faq_jsonl`` from the indexed corpus.  Both must pass the same
# intent-binding gate; listing only one silently exempts the other.
_FAQ_DOC_TYPES = {"csv", "faq_jsonl"}


def _faq_terms(text: str) -> set[str]:
    """Return normalized, query-bearing terms for FAQ binding.

    This is intentionally smaller and stricter than BM25's vocabulary.  BM25
    remains responsible for ranking, while these terms answer a separate
    question: does this FAQ actually contain the subject and intent the user
    asked about?
    """
    terms: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if raw in _FAQ_QUERY_STOP_WORDS:
            continue
        terms.add(_FAQ_TERM_ALIASES.get(raw, raw))
    return terms


def _faq_match_score(query: str, entry: dict[str, Any]) -> float:
    """Score whether an FAQ row is bound to *query*, independently of BM25.

    The score combines coverage in the full Q&A with how well the FAQ's own
    question balances against the query.  Requiring both prevents a generic
    answer paragraph from making an unrelated row look relevant.  Deadline
    questions also need temporal evidence; otherwise a topic-only hit such as
    "capital gains" can be mistaken for a filing deadline answer.

    The question term is an F1, not plain coverage, because coverage alone
    cannot separate rows once a short query is fully covered.  "What is VAT?"
    is wholly contained in "Is EFRIS optional for non-VAT taxpayers?", so both
    that row and the real definition scored 1.0 and the relative cutoff had
    nothing to work with.  Weighing precision alongside recall demotes a row
    whose question is far broader than what was asked, and is a no-op when the
    two agree.
    """
    query_terms = _faq_terms(query)
    if not query_terms:
        return 0.0

    question_terms = _faq_terms(str(entry.get("question", "")))
    answer_terms = _faq_terms(str(entry.get("answer", "")))
    body_terms = question_terms | answer_terms

    # Timing is an intent, not merely a topic.  Do not answer a deadline/due
    # date question from a passage that never states a timing rule.
    timing_terms = {"deadline", "due", "date", "period"}
    timing_evidence = {"deadline", "due", "date", "period", "monthly", "annual"}
    if query_terms & timing_terms and not (timing_evidence & body_terms):
        return 0.0

    body_coverage = len(query_terms & body_terms) / len(query_terms)
    matched = len(query_terms & question_terms)
    question_recall = matched / len(query_terms)
    question_precision = matched / len(question_terms) if question_terms else 0.0

    # Subject focus is an intent too.  A query wholly contained in a much
    # broader question is topic-adjacent, not answered by it: "What is VAT?"
    # sits inside "Is EFRIS optional for non-VAT taxpayers?", which is about
    # EFRIS.  Containment alone maxes out every coverage signal, so this is
    # gated rather than merely down-weighted.  The bar stays low so that a
    # short query against a legitimately more specific question — "VAT
    # registration" against "When is VAT registration compulsory for a
    # manufacturer?" — is still answered.
    if question_recall == 1.0 and question_precision < _FAQ_SUBJECT_FOCUS_MIN:
        return 0.0
    if question_recall + question_precision:
        question_fit = (
            2 * question_recall * question_precision / (question_recall + question_precision)
        )
    else:
        question_fit = 0.0
    score = (0.55 * body_coverage) + (0.45 * question_fit)
    return round(score, 4)


def _retain_faq_candidates(
    query: str,
    scored: list[tuple[float, dict[str, str], float]],
    top_k: int,
) -> list[dict[str, str]]:
    """Keep only FAQ rows with enough query coverage.

    The relative cutoff is important for near-duplicate FAQs: for example,
    "VAT registration threshold" should keep the compulsory-registration FAQ,
    not a nearby late-registration-penalty FAQ just because both contain VAT
    and registration.
    """
    if not scored:
        return []
    best_match = max(match for _rank, _entry, match in scored)
    cutoff = max(_FAQ_MATCH_MIN, best_match * _FAQ_MATCH_RELATIVE)
    retained: list[dict[str, str]] = []
    for rank, entry, match in sorted(
        scored,
        key=lambda item: (item[2], item[0]),
        reverse=True,
    ):
        if match < cutoff:
            continue
        out = dict(entry)
        # BM25 caches per-row statistics on the in-memory source entry; do not
        # leak those implementation details into retrieval metadata.
        out.pop("_bm25_tf", None)
        out.pop("_bm25_dl", None)
        # Preserve the historical field used to carry BM25/overlap strength
        # into the retrieval-hit shape.
        out["_overlap"] = rank
        out["_faq_match_score"] = match
        retained.append(out)
        if len(retained) >= top_k:
            break
    return retained

# --- BM25 keyword scoring ----------------------------------------------------
# Lazy module-level encoder loaded from Model/bm25_state.json (vocab + IDF +
# avg_dl + k1/b).  Used by _simple_search to score the keyword fallback
# properly — rare URA-domain terms (vat, paye, presumptive, …) get high
# weight, common words get near-zero weight.  Without the state file we
# transparently fall back to plain content-word overlap counting.
_BM25_ENCODER: Any = None
_BM25_LOAD_ATTEMPTED = False


def _get_bm25_encoder() -> Any:
    global _BM25_ENCODER, _BM25_LOAD_ATTEMPTED
    if _BM25_ENCODER is not None:
        return _BM25_ENCODER
    if _BM25_LOAD_ATTEMPTED:
        return None
    _BM25_LOAD_ATTEMPTED = True
    try:
        from .retriever import BM25SparseEncoder, BM25_STATE_PATH
        if not BM25_STATE_PATH.exists():
            logger.info("BM25 state %s not found; _simple_search will use overlap counting", BM25_STATE_PATH)
            return None
        with open(BM25_STATE_PATH) as f:
            state = json.load(f)
        _BM25_ENCODER = BM25SparseEncoder.from_dict(state)
        logger.info("BM25 keyword scoring active (vocab=%d, avg_dl=%.1f)",
                    len(_BM25_ENCODER._vocab), _BM25_ENCODER._avg_dl)
        return _BM25_ENCODER
    except Exception:
        logger.warning("Failed to load BM25 encoder; falling back to overlap counting", exc_info=True)
        return None


def _faq_bm25_score(query_tokens: list[str], entry: dict, encoder: Any) -> float:
    """BM25 score of an FAQ entry against pre-tokenized query tokens.

    Caches per-entry document statistics (TF + length) directly on the
    entry dict so subsequent calls are cheap — entries are loaded once
    at ChatModel init and reused for every request.
    """
    if "_bm25_tf" not in entry:
        doc_tokens = encoder._tokenize(f"{entry['question']} {entry['answer']}")
        entry["_bm25_tf"] = dict(Counter(doc_tokens))
        entry["_bm25_dl"] = len(doc_tokens)
    tf, dl = entry["_bm25_tf"], entry["_bm25_dl"]
    k1, b = encoder._k1, encoder._b
    avg_dl = max(encoder._avg_dl, 1.0)
    norm = 1 - b + b * dl / avg_dl
    score = 0.0
    seen: set[str] = set()
    for q_term in query_tokens:
        if q_term in seen:
            continue
        seen.add(q_term)
        tid = encoder._vocab.get(q_term)
        if tid is None:
            continue
        idf = encoder._idf.get(tid, 0.0)
        if idf <= 0.0:
            continue
        f_t = tf.get(q_term, 0)
        if f_t == 0:
            continue
        score += idf * f_t * (k1 + 1) / (f_t + k1 * norm)
    return score


def _simple_search(
    query: str,
    faq_index: dict[str, list[dict[str, str]]],
    top_k: int = 4,
    *,
    binding_query: str | None = None,
    locale: str | None = None,
) -> list[dict[str, str]]:
    """Keyword retrieval over the in-memory FAQ index.

    Prefers proper BM25 scoring (using the committed Model/bm25_state.json
    vocab+IDF+avg_dl) so rare domain terms like vat, paye, presumptive
    dominate ranking; falls back to plain content-word overlap counting
    when the BM25 state isn't present.  Each returned dict includes an
    ``_overlap`` key carrying the score (BM25 or overlap, depending on
    which path was taken) — kept under the same name to preserve the
    score_rrf wiring in _faq_hits_to_retrieval_hits.

    When *locale* names a non-English language and the question finds nothing,
    the query is translated to English and retried — see the comment on the
    retry below.
    """
    # Query rewriting expands abbreviations (for example VAT → "Value Added
    # Tax") and resolves conversational references.  Ranking can use the
    # rewritten form, but answer authorization must remain bound to the words
    # the user actually supplied; otherwise the expansion adds unmatched terms
    # and either rejects a valid FAQ or distorts the match score.
    match_query = binding_query or query
    encoder = _get_bm25_encoder()

    def _one_pass(search_text: str, bind_text: str) -> list[dict[str, str]]:
        """Score the whole index against *search_text*, gated on *bind_text*."""
        if encoder is not None:
            query_tokens = encoder._tokenize(search_text)
            if not query_tokens:
                return []
            scored: list[tuple[float, dict[str, str], float]] = []
            for entries in faq_index.values():
                for entry in entries:
                    s = _faq_bm25_score(query_tokens, entry, encoder)
                    if s > 0:
                        match = _faq_match_score(bind_text, entry)
                        if match > 0:
                            scored.append((s, entry, match))
            return _retain_faq_candidates(bind_text, scored, top_k)

        # Fallback: plain content-word overlap (pre-BM25 behaviour).
        query_tokens_set = _faq_terms(search_text)
        if not query_tokens_set:
            return []
        scored_fallback: list[tuple[float, dict[str, str], float]] = []
        for entries in faq_index.values():
            for entry in entries:
                q_tokens = _faq_terms(entry["question"])
                a_tokens = _faq_terms(entry["answer"])
                overlap = len(query_tokens_set & (q_tokens | a_tokens))
                if overlap > 0:
                    match = _faq_match_score(bind_text, entry)
                    if match > 0:
                        scored_fallback.append((float(overlap), entry, match))
        return _retain_faq_candidates(bind_text, scored_fallback, top_k)

    hits = _one_pass(query, match_query)
    if hits or not locale or locale == "en":
        return hits

    # Nothing matched and the question was not asked in English. The corpus is
    # English, so a Luganda or Runyankole question shares no terms with it:
    # BM25 has nothing to score, and the coverage gate rejects the little it
    # finds. Measured on the Luganda golden set, ALL 12 questions returned zero
    # candidates from this function; translating and retrying rescues 5.
    #
    # Standard translate-then-retrieve for a monolingual corpus, and
    # deliberately LAZY — it runs only after the untranslated attempt came back
    # empty. So the queries that already work keep their exact results and pay
    # no translation latency, and this can add candidates where there were none
    # but never displace or reorder existing ones.
    #
    # A dense-embedding path was built and measured for this instead, and
    # rejected — see ml/scripts/eval_retrieval.py. Fusing static embeddings cost
    # 14-26 points of Hit@1 at every weight tried, and their cosines did not
    # separate real matches from off-domain noise ("buy cheap flight tickets to
    # Dubai" outscored every genuine Luganda rescue), so no threshold admitted
    # the rescues without also admitting nonsense. A tax assistant answering an
    # off-topic question with a confident-looking tax FAQ is worse than
    # answering nothing.
    try:
        from . import sunbird

        english = sunbird.translate_to_english(query, locale)
    except Exception:  # noqa: BLE001 — translation is best-effort
        logger.debug("Retrieval translation failed for locale %s", locale, exc_info=True)
        return hits
    if not english or english.strip().lower() == query.strip().lower():
        return hits

    # Authorization binds to the TRANSLATED text, because that is what shares
    # vocabulary with the corpus. The user's own words cannot cover an English
    # FAQ by construction, so reusing them here would reject every candidate
    # this path exists to find. The gate itself is unchanged: an off-domain
    # translation still fails it.
    rescued = _one_pass(english, english)
    if rescued:
        logger.info(
            "Retrieval rescued by translation (%s -> en): %r -> %r, %d hit(s)",
            locale, query[:60], english[:60], len(rescued),
        )
    return rescued


def _faq_hits_to_retrieval_hits(entries: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Convert FAQ index rows into the retrieval-hit shape used downstream."""
    hits: list[dict[str, Any]] = []
    for entry in entries:
        tag = str(entry.get("tag") or "")
        hits.append(
            {
                "text": f"Question: {entry['question']}\nAnswer: {entry['answer']}",
                "answer": entry["answer"],
                "question": entry["question"],
                "source": entry["source"],
                "chunk_id": "",
                "page": "",
                "section": tag,
                "doc_type": "csv",
                "score_rrf": float(entry.get("_overlap", 0.0) or 0.0),
                "faq_match_score": float(entry.get("_faq_match_score", 0.0) or 0.0),
            }
        )
    return hits


def _filter_unbound_faq_hits(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove canonical FAQ passages that do not answer the requested intent.

    Keyword hits already carry ``faq_match_score``.  This second pass also
    protects hybrid/indexed FAQ rows, whose stored metadata may not have been
    produced by the in-memory loader.  Non-FAQ PDF and attachment passages are
    left untouched and continue through the normal semantic/grounding gates.
    """
    faq_rows = [
        h
        for h in hits
        if str(h.get("doc_type", "")).lower() in _FAQ_DOC_TYPES
        and str(h.get("source", "")).lower().startswith("ura_")
        and str(h.get("source", "")).lower().endswith("_faqs.csv")
    ]
    if not faq_rows:
        return hits

    scores: dict[int, float] = {}
    for idx, hit in enumerate(hits):
        if hit not in faq_rows:
            continue
        score = float(hit.get("faq_match_score") or _faq_match_score(query, hit))
        scores[idx] = score

    best = max(scores.values(), default=0.0)
    cutoff = max(_FAQ_MATCH_MIN, best * _FAQ_MATCH_RELATIVE)
    filtered: list[dict[str, Any]] = []
    for idx, hit in enumerate(hits):
        if idx not in scores or scores[idx] >= cutoff:
            if idx in scores:
                hit["faq_match_score"] = scores[idx]
            filtered.append(hit)
    return filtered


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------
class ChatModel:
    """Unified service that backs all API endpoints.

    On initialisation it loads the FAQ CSV corpus into memory and attempts
    to connect to Qdrant for hybrid retrieval.  If Qdrant is unavailable
    the service degrades gracefully to keyword-only search.
    """

    def __init__(self) -> None:
        self.name = llm_module.LLM_MODEL or "unknown"
        self._faq_index, self._tag_labels = _load_faq_data(_DATA_DIR)

        # Hybrid retriever (graceful degradation)
        self._retriever = HybridRetriever()
        self._retriever_ready = self._retriever.initialize()

        # OWASP LLM Top 10 guardrails
        self._input_guard = InputGuard()
        self._output_guard = OutputGuard()

        # LLM generation (Phase 2 — true RAG)
        self._llm_available = llm_module.is_available()

        # Semantic cache — factory selects in-process vs Redis backend
        # based on CACHE_BACKEND env (see cache.py).
        self._cache = create_cache()
        if self._retriever_ready and self._retriever._dense_model:
            self._cache.set_model(self._retriever._dense_model)

        self._workflow_count = 0
        if _WORKFLOW_FLOWS_DIR.is_dir():
            try:
                self._workflow_count = auto_load_flows(_WORKFLOW_FLOWS_DIR)
            except Exception:
                logger.exception("Workflow auto-load failed from %s", _WORKFLOW_FLOWS_DIR)

        mode = "hybrid (Qdrant)" if self._retriever_ready else "keyword-only (fallback)"
        gen_mode = f"LLM ({self.name})" if self._llm_available else "FAQ lookup (fallback)"
        logger.info(
            "ChatModel initialised – %s mode, %s gen, %d tags, %d workflows",
            mode,
            gen_mode,
            len(self._faq_index),
            self._workflow_count,
        )

    @staticmethod
    def _workflow_view(
        session: WorkflowSession,
        *,
        name: str,
        status: str,
        pending_slot: str = "",
    ) -> dict[str, Any]:
        """Return UI-safe workflow metadata without echoing sensitive slot values."""
        filled_slots = [k for k in session.slots if session.slots.get(k) not in ("", None)]
        masked_slots = sorted(set(filled_slots) & _WORKFLOW_SENSITIVE_SLOTS)
        visible_slots = sorted(set(filled_slots) - _WORKFLOW_SENSITIVE_SLOTS)
        return {
            "id": session.workflow_id,
            "name": name,
            "status": status,
            "current_step_idx": session.current_step_idx,
            "filled_slots": visible_slots,
            "masked_slots": masked_slots,
            "pending_slot": pending_slot,
            "completed": status == "completed",
        }

    @staticmethod
    def _default_next_actions(
        *,
        agent_role: str,
        workflow: dict[str, Any] | None = None,
        handoff: dict[str, Any] | None = None,
        escalation_required: bool = False,
    ) -> list[str]:
        if workflow and workflow.get("status") == "active":
            return [
                "Reply with the requested detail to continue the guided process.",
                "Send 'cancel' if you want to leave this workflow and ask a different question.",
            ]
        if handoff:
            return [
                "Prepare the listed reference details before speaking to a URA officer.",
                "Use the URA Contact Centre if you need immediate human assistance.",
            ]
        if agent_role == "clarification_agent":
            return ["Reply with the missing detail so I can answer more precisely."]
        if escalation_required:
            return [
                "Review the cited URA sources before acting on this answer.",
                "Ask for human support if your case is account-specific or time-sensitive.",
            ]
        return []

    @staticmethod
    def _has_inline_citations(reply: str) -> bool:
        return bool(re.search(r"\[\d+\]", reply or ""))

    def _load_personalization_state(self, user_id: str | None) -> dict[str, Any] | None:
        """Return consent-gated profile + memory context for personalization."""
        if not user_id or not flags.is_enabled("memory_enabled"):
            return None

        try:
            snapshot = get_memory_service().read_all(user_id, purpose="personalization")
        except Exception:
            logger.debug("personalization read failed", exc_info=True)
            return None

        if not snapshot.consent_granted:
            return None

        profile = db.get_user_profile(user_id) or {}
        lines: list[str] = []
        prefill_slots: dict[str, Any] = {}

        display_name = str(profile.get("display_name", "")).strip()
        if display_name:
            lines.append(f"- Preferred name: {display_name}")

        taxpayer_type = str(profile.get("taxpayer_type", "")).strip().lower()
        if taxpayer_type and taxpayer_type != "unknown":
            lines.append(f"- Taxpayer type: {taxpayer_type.replace('_', ' ')}")
            prefill_slots["taxpayer_type"] = taxpayer_type

        detail_level = str(profile.get("detail_level", "")).strip().lower()
        if detail_level:
            lines.append(f"- Preferred explanation depth: {detail_level}")

        registered = profile.get("registered_tax_types") or []
        if isinstance(registered, list) and registered:
            lines.append(f"- Registered tax types: {', '.join(str(t) for t in registered[:4])}")

        fact_labels = {
            "taxpayer_type": "Known taxpayer type",
            "industry": "Industry",
            "registered_tax": "Registered tax",
            "registered_vat": "VAT registration",
            "primary_language": "Preferred language",
        }
        seen_facts: set[tuple[str, str]] = set()
        for fact in snapshot.facts[:5]:
            label = fact_labels.get(fact.category)
            value = str(fact.object_value).strip()
            if not label or not value:
                continue
            key = (label, value.lower())
            if key in seen_facts:
                continue
            seen_facts.add(key)
            lines.append(f"- {label}: {value}")
            if fact.category == "taxpayer_type" and "taxpayer_type" not in prefill_slots:
                prefill_slots["taxpayer_type"] = value.lower()

        for episode in snapshot.episodic[:2]:
            summary = str(episode.get("summary", "")).strip()
            topic = str(episode.get("topic_tag", "")).strip()
            if not summary:
                continue
            summary = summary[:140] + ("..." if len(summary) > 140 else "")
            if topic:
                lines.append(f"- Recent topic ({topic}): {summary}")
            else:
                lines.append(f"- Recent topic: {summary}")

        working = snapshot.working if isinstance(snapshot.working, dict) else {}
        last_topic = str(working.get("last_topic", "")).strip()
        if last_topic:
            lines.append(f"- Last conversation topic: {last_topic}")

        prompt_context = "\n".join(lines)
        if not prompt_context and not prefill_slots:
            return None

        return {
            "consent_granted": True,
            "profile": profile,
            "prefill_slots": prefill_slots,
            "prompt_context": prompt_context,
        }

    @staticmethod
    def _apply_personalization_to_workflow(
        session: WorkflowSession,
        personalization: dict[str, Any] | None,
    ) -> None:
        if not personalization:
            return
        for slot_name, value in (personalization.get("prefill_slots") or {}).items():
            if slot_name and value not in ("", None) and slot_name not in session.slots:
                session.slots[slot_name] = value

    @staticmethod
    def _content_tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower())) - _STOP_WORDS

    @staticmethod
    def _extract_grounded_answer_text(hit: dict[str, Any]) -> str:
        answer = str(hit.get("answer", "") or "").strip()
        if answer:
            return _clean_passage_text(answer)
        text = str(hit.get("text", "") or "").strip()
        if text.lower().startswith("question:") and "\nanswer:" in text.lower():
            parts = re.split(r"\nanswer:\s*", text, maxsplit=1, flags=re.IGNORECASE)
            text = parts[1] if len(parts) == 2 else text
        return _clean_passage_text(text)

    @staticmethod
    def _format_procedure_steps(text: str, lead: str) -> str:
        """Render a run-on procedural answer as a numbered Markdown list.

        FAQ procedures separate major steps with ``;`` and use ``→`` for
        navigation *within* a step (kept inline). Returns ``lead`` followed by a
        numbered list when ≥2 steps are found, otherwise a single paragraph
        ``"{lead} {text}"`` so non-procedural text is left untouched.
        """
        clean = " ".join((text or "").split())
        parts = [p.strip(" .;") for p in clean.split(";")]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            return f"{lead} {clean}".strip()
        steps = "\n".join(f"{i}. {p[:1].upper() + p[1:]}" for i, p in enumerate(parts, 1))
        return f"{lead}\n\n{steps}"

    @classmethod
    def _build_grounded_revision(
        cls,
        hits: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        query: str = "",
    ) -> str:
        excerpts: list[str] = []
        query_tokens = cls._content_tokens(query)

        def rank(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
            idx, hit = item
            body = f"{hit.get('question') or ''} {hit.get('answer') or hit.get('text') or ''}"
            tokens = cls._content_tokens(body)
            overlap = len(query_tokens & tokens) if query_tokens else 0
            priority = 0
            if "tin" in query.lower() and "instant tin" in body.lower():
                priority += 8
            if "return" in query.lower() and "file a return" in body.lower():
                priority += 8
            if str(hit.get("source", "")).lower() in {
                "ura_objection_appeals_faqs.csv",
                "ura_double_taxation_agreements_faqs.csv",
            }:
                priority -= 8
            return (overlap + priority + float(hit.get("score_rrf") or 0.0) / 100.0, -idx)

        def is_near_duplicate(tokens: set[str], seen: set[str]) -> bool:
            if not tokens or not seen:
                return False
            overlap = len(tokens & seen)
            union = len(tokens | seen)
            if union and overlap / union > 0.6:
                return True
            # Jaccard alone misses a short excerpt that is almost entirely a
            # *subset* of a much longer one (two editions of the same section,
            # where one excerpt's 700-char trim window reaches further before
            # cutting off) — its unique tail content dilutes the union enough
            # to pull Jaccard under the threshold even though the shorter
            # excerpt is essentially all duplicate. A containment ratio over
            # the shorter excerpt's own token count catches that case too.
            shorter = min(len(tokens), len(seen))
            return shorter >= 15 and overlap / shorter > 0.75

        ranked_hits = [hit for _, hit in sorted(enumerate(hits), key=rank, reverse=True)]
        excerpt_tokens: list[set[str]] = []
        for hit in ranked_hits:
            if len(excerpts) >= 2:
                break
            text = cls._extract_grounded_answer_text(hit)  # PDF-artifact-cleaned
            if len(text) < 40:  # skip empty / artifact-only chunks
                continue
            excerpt = _structure_excerpt(_trim_excerpt(text, 700))
            # Trimming can leave a PDF footnote number dangling at the new
            # end of the excerpt ("...remit to URA. 1") — strip it. Numbers
            # BEFORE the final punctuation (amounts, hotlines) are untouched.
            excerpt = re.sub(r"(?<=[.!?)])\s+\d{1,3}\s*$", "", excerpt).rstrip()
            # Different handbook fiscal-year editions often carry near-identical
            # wording for the same section, so the top-ranked hits can be the
            # same passage from two editions. This gate skips a near-duplicate
            # in favour of the next genuinely distinct hit instead of showing
            # the user the same content twice.
            tokens = cls._content_tokens(excerpt)
            if any(is_near_duplicate(tokens, seen) for seen in excerpt_tokens):
                continue
            # References intentionally stay OUT of the prose — they reach the
            # UI via the result's citations/sources (grounded-context panel),
            # matching the deterministic-reply convention.
            excerpts.append(excerpt)
            excerpt_tokens.append(tokens)
        if not excerpts:
            return ""
        body = "\n\n".join(excerpts)
        return f"{GROUNDED_REVISION_PREAMBLE}\n\n{body}"

    def _finalize_reply(self, reply: str) -> str:
        """Apply response-side safety cleanup to generated, revised, and cached text."""
        cleaned = self._output_guard.redact_pii(str(reply or ""))
        cleaned = self._output_guard.sanitize(cleaned)
        leakage = self._output_guard.check_prompt_leakage(cleaned)
        return leakage.sanitized_text

    def _finalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return a shallow copy with a production-safe user-facing reply."""
        out = dict(result)
        out["reply"] = self._finalize_reply(str(out.get("reply", "")))
        return out

    @staticmethod
    def _recompute_for_verification(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Re-run a calculator through MCP for the numeric verifier.

        Deliberately goes through :class:`app.mcp.MCPClient` rather than
        calling the calculator directly: the verification must exercise
        the same routing, authorization and validation the agent's own
        call did, or it is checking a different code path than the one
        that produced the answer.

        Runs as ``public`` with no consent grants — every calculator is
        ``read_only`` and ``low`` risk, so verification cannot reach a
        tool the taxpayer's own turn could not.
        """
        from .mcp import get_client

        result = get_client().call_tool(tool, arguments, user_role="public")
        payload = getattr(result, "result", None)
        return payload if isinstance(payload, dict) else {"ok": False}

    @staticmethod
    def _escalate_on_numeric_mismatch(
        verification: dict[str, Any] | None,
        escalate: bool,
        reason: str,
    ) -> tuple[bool, str]:
        """Escalate when a figure disagrees with its own calculator.

        Only on a *confirmed* mismatch. A check that could not run —
        the question was not a calculation, an input was missing, the
        transport was down — leaves the answer exactly as it was;
        treating "unverified" as "wrong" would escalate most of the
        traffic and teach staff to ignore the queue.
        """
        if not verification:
            return escalate, reason
        if "numerically_consistent" in (verification.get("failures") or []):
            return True, "Stated figure disagrees with the calculator that produced it"
        return escalate, reason

    @staticmethod
    def _graph_hits(query: str) -> list[dict[str, Any]]:
        """Statutory graph claims as a retrieval hit, or nothing.

        **Not RRF fusion**, despite what the architecture proposal said.
        The graph is projected from the effective-dated rate tables, not
        from the passage corpus, so its claims have no passage ids to
        rank against — there is nothing for reciprocal rank fusion to
        fuse. Once prose provisions are extracted from the crawl and
        linked to chunk ids, a genuine third RRF leg becomes possible;
        pretending this is one would misdescribe where the answer came
        from.

        What it is instead is the pattern already used next door by
        ``_priority_faq_hits``: a high-authority source injected ahead
        of the retrieved passages. The authority claim is stronger here
        than for the FAQ hits — every figure carries its Act, its
        section and its fiscal year, and an unreconciled figure carries
        that mark too — so an answer built on it can be checked rather
        than trusted.

        Returns at most one hit. The claims for one question belong in
        one passage: split across several, the reranker can keep the
        rate and drop the threshold that gates it, which is the exact
        join the graph exists to preserve.
        """
        if not flags.is_enabled("graph_fusion") or not flags.is_enabled("tax_graph"):
            return []
        try:
            from .graph.shadow import graph_answer_for

            rendered = graph_answer_for(query)
        except Exception:
            # A retrieval leg must never take down the turn.
            logger.warning("graph leg unavailable", exc_info=True)
            return []
        if not rendered.strip():
            return []
        return [
            {
                "text": (
                    "Statutory rate positions (from the effective-dated URA "
                    "rate tables, with the Act behind each figure):\n" + rendered
                ),
                "question": "",
                "answer": rendered,
                "source": "URA rate tables (statutory graph)",
                "chunk_id": "",
                "page": "",
                "section": "statutory-graph",
                "doc_type": "graph",
                "score_rrf": 0.0,
            }
        ]

    def _priority_faq_hits(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        """Inject high-precision FAQ hits for common procedures that reranking can miss."""
        if not _TIN_REGISTRATION_QUERY_RE.search(query):
            if not _RETURN_FILING_QUERY_RE.search(query):
                return []
            candidates: list[dict[str, str]] = []
            for tag in ("processes_systems", "taxpayer_starter_pack", "taxation_handbook_fy2025_26"):
                for entry in self._faq_index.get(tag, []):
                    text = f"{entry['question']} {entry['answer']}".lower()
                    if "return" in text and any(
                        term in text for term in ("file a return", "return filing", "due")
                    ):
                        enriched = dict(entry)
                        enriched["tag"] = tag
                        enriched["_overlap"] = "99"
                        candidates.append(enriched)

            candidates.sort(
                key=lambda e: (
                    "how do i file a return" in e["question"].lower(),
                    "what is a return filing" in e["question"].lower(),
                    len(e["answer"]),
                ),
                reverse=True,
            )
            return _faq_hits_to_retrieval_hits(candidates[:top_k])

        candidates: list[dict[str, str]] = []
        for tag in ("instant_tin_application", "processes_systems", "taxpayer_starter_pack"):
            for entry in self._faq_index.get(tag, []):
                text = f"{entry['question']} {entry['answer']}".lower()
                if "tin" in text and any(
                    term in text for term in ("register", "registration", "apply", "get a tin")
                ):
                    enriched = dict(entry)
                    enriched["tag"] = tag
                    enriched["_overlap"] = "99"
                    candidates.append(enriched)

        if not candidates:
            return []

        def score(entry: dict[str, str]) -> tuple[int, int]:
            question = entry["question"].lower()
            text = f"{entry['question']} {entry['answer']}".lower()
            exact = int("how do i apply for an instant tin" in question)
            procedure = int("go to ura.go.ug" in text and "get a tin" in text)
            return (exact + procedure, len(text))

        candidates.sort(key=score, reverse=True)
        return _faq_hits_to_retrieval_hits(candidates[:top_k])

    def _deterministic_procedure_reply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> tuple[str, bool]:
        """Return (reply, curated) vetted procedural answers without LLM synthesis.

        ``curated`` is True when the reply body is a fully hand-vetted template
        (faithfulness 1.0 by construction); False when it is assembled from
        retrieved hits and should be scored against them like any answer.

        References are intentionally NOT embedded inline here — they reach the UI via the
        result's ``citations`` / ``sources`` (the grounded-context panel), so the prose
        stays clean, stepwise Markdown. ``citations`` is kept on the signature for callers.
        """
        if _TIN_REGISTRATION_QUERY_RE.search(query):
            # Organisation asks are answered from the curated non-individual
            # template regardless of hits (the instant-TIN FAQ hits are
            # individual-specific).
            if _TIN_ORG_QUERY_RE.search(query):
                return self._tin_procedure_reply("organisation"), True

            apply_hit = next(
                (
                    h
                    for h in hits
                    if h.get("source") == "ura_instant_tin_application_faqs.csv"
                    and "apply for an instant tin" in str(h.get("question", "")).lower()
                ),
                None,
            )
            help_hit = next(
                (
                    h
                    for h in hits
                    if h.get("source") == "ura_instant_tin_application_faqs.csv"
                    and "contact" in str(h.get("question", "")).lower()
                ),
                None,
            )
            if apply_hit:
                contact = CONTACT_FOOTER
                if help_hit:
                    contact = self._extract_grounded_answer_text(help_hit)
                reply = self._tin_procedure_reply("individual", contact)
                if not _TIN_INDIVIDUAL_QUERY_RE.search(query):
                    # Type unspecified and the clarification workflow didn't
                    # intercept (workflows disabled) — point organisations to
                    # their path instead of silently assuming individual.
                    reply += (
                        "\n\n_Registering an **organisation** instead? Ask me about "
                        "non-individual TIN registration._"
                    )
                return reply, True

        if _RETURN_FILING_QUERY_RE.search(query):
            file_hit = next(
                (
                    h
                    for h in hits
                    if h.get("source") == "ura_processes_systems_faqs.csv"
                    and "how do i file a return" in str(h.get("question", "")).lower()
                ),
                None,
            )
            due_hit = next(
                (
                    h
                    for h in hits
                    if "return" in str(h.get("question", "")).lower()
                    and "due" in str(h.get("question", "")).lower()
                ),
                None,
            )
            if file_hit:
                lines = [
                    self._format_procedure_steps(
                        self._extract_grounded_answer_text(file_hit),
                        "To file your annual tax return:",
                    )
                ]
                if due_hit:
                    lines.append(f"**Due date:** {self._extract_grounded_answer_text(due_hit)}")
                lines.append(CONTACT_FOOTER)
                return "\n\n".join(lines), False

        return "", False

    @staticmethod
    def _tin_procedure_reply(kind: str, contact: str = "") -> str:
        """Curated TIN registration steps for an individual or an organisation."""
        steps = _TIN_ORG_STEPS if kind == "organisation" else _TIN_INDIVIDUAL_STEPS
        return f"{steps}\n\n{contact or CONTACT_FOOTER}"

    def _deterministic_result(
        self,
        *,
        reply: str,
        curated: bool,
        hits: list[dict[str, Any]],
        sources: list[str],
        citations: list[dict[str, Any]],
        retrieval_mode: str,
        thread_id: str,
        locale: str,
        agent_role: str,
    ) -> dict[str, Any]:
        """Result envelope for a deterministic procedural reply (KB-grounded).

        Curated templates are faithful by construction and score 1.0;
        replies assembled from retrieved hits are scored against those hits
        like any other answer. Shared by the REST and streaming paths so
        the same question earns the same score on both.
        """
        contexts = [str(h.get("text") or h.get("answer") or "") for h in hits]
        faith = 1.0 if curated else HybridRetriever.compute_faithfulness(reply, contexts)
        return {
            "reply": reply,
            "sources": sources,
            "citations": citations,
            "faithfulness_score": faith,
            "retrieval_mode": retrieval_mode,
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": False,
            "escalation_reason": "",
            "agent_role": agent_role,
            "handoff": None,
            "response_judge": {
                "decision": "approve",
                "final_decision": "approve",
                "applied_revision": False,
                "reasons": ["curated deterministic template"] if curated else [],
                "confidence_band": "high" if faith >= 0.65 else "medium",
            },
            "next_actions": self._default_next_actions(agent_role=agent_role),
            "ticket_id": "",
        }

    def _evaluate_response_judge(
        self,
        *,
        message: str,
        reply: str,
        hits: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        faithfulness_score: float | None,
        escalation_required: bool,
        escalation_reason: str,
        claim_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Classify the draft reply as approve / revise / escalate."""
        reasons: list[str] = []
        decision = "approve"

        if escalation_required:
            decision = "escalate"
            if escalation_reason:
                reasons.append(escalation_reason)

        if decision != "escalate" and _ACCOUNT_QUERY_RE.search(message):
            decision = "escalate"
            reasons.append("account-specific query needs authenticated lookup or human review")

        if not reply.strip():
            reasons.append("reply was empty")

        if decision != "escalate" and hits and citations and not self._has_inline_citations(reply):
            reasons.append("reply did not expose visible citation markers")
            # Only revise if faithfulness is also below threshold — a well-grounded
            # answer without explicit [N] markers is acceptable.
            if faithfulness_score is not None and faithfulness_score < 0.5:
                decision = "revise"

        if faithfulness_score is not None:
            if faithfulness_score < 0.2:
                decision = "escalate"
                reasons.append("grounding confidence is critically low")
            elif decision != "escalate" and faithfulness_score < max(GROUNDING_THRESHOLD, 0.35):
                decision = "revise"
                reasons.append("grounding confidence is below the release threshold")

        if claim_report:
            claim_decision = str(claim_report.get("decision", "approve"))
            if claim_decision == "escalate":
                decision = "escalate"
                reasons.append("claim verification found unsupported factual claims")
            elif claim_decision == "revise" and decision != "escalate":
                decision = "revise"
                if claim_report.get("uncited_claims"):
                    reasons.append("claim verification found uncited factual claims")
                if claim_report.get("unsupported_claims"):
                    reasons.append("claim verification found weakly supported factual claims")

        revised_reply = ""
        if decision == "revise":
            revised_reply = self._build_grounded_revision(hits, citations, message)
            if not revised_reply:
                decision = "escalate"
                reasons.append("no deterministic grounded fallback was available")

        if faithfulness_score is None:
            confidence_band = "medium" if decision == "approve" else "low"
        elif faithfulness_score >= 0.65 and decision == "approve":
            confidence_band = "high"
        elif faithfulness_score >= 0.35 and decision != "escalate":
            confidence_band = "medium"
        else:
            confidence_band = "low"

        return {
            "decision": decision,
            "final_decision": decision,
            "applied_revision": False,
            "reasons": reasons,
            "confidence_band": confidence_band,
            "revised_reply": revised_reply,
        }

    @staticmethod
    def _escalation_transcript(
        conversation_id: str,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        """Snapshot the conversation for the officer handling this ticket.

        Taken at escalation time rather than joined on read.  ``conversations``
        is purged after ``CONVERSATION_TTL_DAYS`` (7) while a ticket can sit in
        the queue far longer, so a live join would show an officer an empty
        transcript for any week-old ticket — exactly the case where the
        taxpayer has been waiting and least wants to start over.
        """
        try:
            return db.get_conversation_transcript(
                conversation_id=conversation_id or None,
                session_id=session_id or None,
            )
        except Exception:
            logger.exception("failed to snapshot transcript for escalation")
            return []

    @staticmethod
    def _deliver_officer_reply(conversation_id: str) -> str:
        """Return an undelivered officer reply for this conversation.

        Closes the loop escalation left open: the taxpayer was told a
        human would follow up, and until now the officer's answer sat in
        a queue the taxpayer could not see. Marking delivery *after*
        composing the text means a failure re-delivers rather than
        silently dropping it — being told twice is a far smaller harm
        than never being told.

        Never raises: a broken ticket store must not take out the chat.
        """
        if not conversation_id:
            return ""
        try:
            pending = db.pending_officer_reply(conversation_id)
        except Exception:
            logger.exception("officer-reply lookup failed")
            return ""
        if not pending or not pending.get("officer_reply"):
            return ""

        officer = str(pending.get("assignee") or "").strip()
        lead = (
            f"A URA officer ({officer}) has replied to your case:"
            if officer
            else "A URA officer has replied to your case:"
        )
        text = f"{lead}\n\n{pending['officer_reply']}"
        try:
            db.mark_reply_delivered(str(pending.get("id", "")))
        except Exception:
            logger.exception("failed to mark officer reply delivered; it will re-deliver")
        return text

    def _maybe_create_ticket(
        self,
        *,
        reason: str,
        user_query: str,
        bot_reply: str,
        session_id: str | None,
        conversation_id: str,
        priority: str = "normal",
        handoff: dict[str, Any] | None = None,
        response_judge: dict[str, Any] | None = None,
        user_id: str = "",
    ) -> str:
        """Persist a structured escalation ticket when the queue is enabled."""
        if not flags.is_enabled("ticket_queue"):
            return ""
        if not reason and not handoff:
            final_decision = str((response_judge or {}).get("final_decision", "")).lower()
            if final_decision != "escalate":
                return ""

        # The turn being escalated is not in `conversations` yet — it is
        # logged by the caller after generate() returns — so append it to
        # the snapshot rather than leaving the officer without the very
        # message that triggered the handoff.
        transcript = self._escalation_transcript(conversation_id, session_id)
        transcript.append(
            {
                "user_message": self.redact_for_storage(user_query),
                "bot_reply": self.redact_for_storage(bot_reply),
                "created_at": time.time(),
                "sources": [],
                "topic_tag": "escalated",
            }
        )

        # One conversation, one officer.  Without this a taxpayer who
        # asks for a human three times opens three tickets, and three
        # officers each start the conversation from the beginning.
        try:
            existing = db.find_open_ticket(conversation_id)
        except Exception:
            logger.exception("open-ticket lookup failed; creating a new ticket")
            existing = None
        if existing:
            ticket_id = str(existing.get("id", ""))
            logger.info("escalation reuses open ticket %s", ticket_id)
            if handoff is not None and ticket_id:
                handoff["ticket_id"] = ticket_id
                handoff["reused_existing_ticket"] = True
            return ticket_id

        try:
            ticket = db.create_ticket(
                reason=reason,
                user_query=self.redact_for_storage(user_query),
                bot_reply=self.redact_for_storage(bot_reply),
                session_id=session_id or None,
                conversation_id=conversation_id,
                priority=priority,
                handoff=handoff,
                response_judge=response_judge,
                transcript=transcript,
                user_id=user_id,
                # Route on the topic the handoff packet already
                # classified, so an officer sees their own queue rather
                # than triaging a mixed one by reading every row.
                team=team_for_topic(str((handoff or {}).get("topic", ""))),
            )
            ticket_id = ticket.get("id", "")
            if handoff is not None and ticket_id:
                handoff["ticket_id"] = ticket_id
        except Exception:
            # The taxpayer is about to be told a human will follow up.
            # Say so loudly enough that someone notices nobody will:
            # `error` rather than `exception`-and-swallow, and a flag on
            # the handoff so the caller can degrade honestly instead of
            # promising a ticket that does not exist.
            logger.exception(
                "ESCALATION LOST: ticket persistence failed for conversation %s (reason=%s)",
                conversation_id or "?",
                reason[:80],
            )
            if handoff is not None:
                handoff["ticket_persisted"] = False
                handoff["delivery_warning"] = (
                    "This escalation could not be queued — contact the taxpayer "
                    "through the channels listed rather than waiting for a ticket."
                )
            return ""

        # Announce it.  A ticket nobody is told about is a queue entry,
        # not a handoff.  Never blocks the reply and never raises.
        try:
            notify_ticket_created(ticket)
        except Exception:
            logger.warning("escalation notification dispatch failed", exc_info=True)
        try:
            # Straight to any staff watching the queue, on every replica.
            from .ticket_events import build_event, publish  # noqa: PLC0415

            publish(build_event(ticket))
        except Exception:
            logger.warning("ticket event publish failed", exc_info=True)
        return ticket_id

    def _persist_personalization_turn(
        self,
        *,
        user_id: str | None,
        conversation_id: str,
        message: str,
        reply: str,
        agent_role: str,
        personalization: dict[str, Any] | None,
        workflow: dict[str, Any] | None = None,
    ) -> None:
        """Update working memory and absorb the latest consented turn."""
        if not user_id or not personalization or not personalization.get("consent_granted"):
            return
        try:
            memsvc = get_memory_service()
            memsvc.update_working(
                user_id,
                last_topic=workflow.get("name") if workflow else agent_role,
                last_agent_role=agent_role,
                last_conversation_id=conversation_id,
            )
            turns = [
                {"role": "user", "content": message},
                {"role": "assistant", "content": reply},
            ]
            memsvc.absorb_conversation(user_id, conversation_id, turns)
        except Exception:
            logger.debug("personalization persistence failed", exc_info=True)

    @staticmethod
    def _handoff_topic(message: str, reason: str = "") -> str:
        text = f"{message} {reason}".strip()
        if _OBJECTION_QUERY_RE.search(text):
            return "objection_or_dispute"
        if _ACCOUNT_QUERY_RE.search(text):
            return "account_specific"
        if _CUSTOMS_QUERY_RE.search(text):
            return "customs"
        if _REGISTRATION_QUERY_RE.search(text):
            return "registration"
        return "general_tax_support"

    @classmethod
    def _handoff_required_details(cls, topic: str) -> list[str]:
        lookup = {
            "objection_or_dispute": [
                "assessment or objection reference number",
                "tax type and filing period",
                "supporting notices or correspondence",
            ],
            "account_specific": [
                "TIN or registered taxpayer email",
                "tax type and return period",
                "any reference number shown in the URA portal",
            ],
            "customs": [
                "entry or declaration number",
                "consignment or bill of lading reference",
                "goods description and customs value details",
            ],
            "registration": [
                "taxpayer type (individual, company, or NGO)",
                "registration number or NIN where applicable",
                "preferred callback channel",
            ],
            "general_tax_support": [
                "the exact URA process you are trying to complete",
                "the tax type involved",
                "any deadline or notice you are working against",
            ],
        }
        return lookup.get(topic, lookup["general_tax_support"])

    def _build_handoff_packet(
        self,
        *,
        message: str,
        reason: str,
        conversation_history: list[dict[str, str]] | None = None,
        hits: list[dict[str, Any]] | None = None,
        faithfulness_score: float | None = None,
        ticket_id: str = "",
    ) -> dict[str, Any]:
        """Create a structured, UI-safe packet for human triage."""
        topic = self._handoff_topic(message, reason)
        priority = "normal"
        lowered_reason = reason.lower()
        if any(term in lowered_reason for term in ("legal", "dispute", "appeal", "audit", "fraud")):
            priority = "high"
        if faithfulness_score is not None and faithfulness_score < 0.2:
            priority = "high"
        if topic == "account_specific" and priority == "normal":
            priority = "high"

        # The officer's context now travels on the ticket as a full
        # transcript (see _escalation_transcript). This stays as a short
        # at-a-glance preview for the queue list, where shipping every
        # conversation would be wasteful — it is no longer the only
        # thing a human receives.
        redacted_context = [
            self.redact_for_storage(turn.get("user_message", ""))[:180]
            for turn in (conversation_history or [])[-2:]
            if turn.get("user_message")
        ]

        # Sentiment at the point of transfer. A handoff that arrives
        # without it makes the officer rediscover the taxpayer's state
        # from scratch, and decides nothing about how to open. Reuses
        # the same classifier the reply paths use, so the ticket cannot
        # disagree with the tone the bot just used.
        distress = detect_user_distress(message)
        turn_count = len(conversation_history or []) + 1
        # Warm transfer = brief the officer before they engage. Reserved
        # for the states where opening cold makes things worse: hardship,
        # anger, and a taxpayer who has already repeated themselves.
        warm = bool(distress in ("hardship", "frustration", "anxiety") or turn_count >= 4)
        sources = []
        for hit in (hits or [])[:3]:
            source = str(hit.get("source", "")).strip()
            if source and source not in sources:
                sources.append(source)

        summary = (
            f"User needs human help with {topic.replace('_', ' ')}. "
            f"Reason: {reason or 'low-confidence automated handling'}."
        )
        if redacted_context:
            summary += f" Recent context: {' | '.join(redacted_context)}."
        if faithfulness_score is not None:
            summary += f" Faithfulness score: {faithfulness_score:.2f}."

        return {
            "summary": summary,
            "topic": topic,
            "priority": priority,
            "ticket_id": ticket_id,
            "required_details": self._handoff_required_details(topic),
            "recent_context": redacted_context,
            "sources_reviewed": sources,
            "sentiment": distress or "neutral",
            "transfer_style": "warm" if warm else "cold",
            "turns_before_handoff": turn_count,
            "opening_guidance": (
                empathy_ack(distress)
                if distress
                else "Answer directly; the taxpayer is not distressed."
            ),
            "contact_channels": [
                "https://ura.go.ug",
                "0800 117 000 / 0800 217 000",
                "WhatsApp 0772 140 000",
            ],
        }

    @staticmethod
    def _handoff_packet_text(packet: dict[str, Any]) -> str:
        lines = [
            f"Handoff summary: {packet.get('summary', '')}",
            f"Priority: {packet.get('priority', 'normal')}",
        ]
        required = packet.get("required_details") or []
        if required:
            lines.append("Required details: " + "; ".join(required))
        sources = packet.get("sources_reviewed") or []
        if sources:
            lines.append("Sources reviewed: " + ", ".join(sources))
        return "\n".join(lines)[:2000]

    @staticmethod
    def _restore_workflow_session(row: dict[str, Any]) -> WorkflowSession:
        return WorkflowSession(
            workflow_id=str(row.get("workflow_id", "")),
            current_step_idx=int(row.get("current_step_idx") or 0),
            slots=dict(row.get("slots") or {}),
            completed=str(row.get("status", "")) == "completed",
        )

    def _advance_workflow(
        self,
        session: WorkflowSession,
        user_input: str,
    ) -> tuple[Any, list[str]]:
        """Advance a workflow and execute any deterministic tool steps inline."""
        tool_messages: list[str] = []
        turn = WorkflowRegistry.advance(session, user_input)
        while turn.tool_call:
            try:
                from .mcp import get_client  # noqa: PLC0415

                call = get_client().call_tool(
                    turn.tool_call.get("name", ""),
                    turn.tool_call.get("arguments", {}) or {},
                    user_role="public",
                )
                result = call.result
            except Exception:
                logger.exception("workflow tool execution failed")
                result = {"ok": False, "error": "workflow tool execution failed"}
            explanation = result.get("explanation") or result.get("message") or ""
            if explanation:
                tool_messages.append(str(explanation))
            # A provisional rate table must caveat the guided flow too, not
            # only the single-shot calculator fast path.
            warning = result.get("verification_warning")
            if warning:
                tool_messages.append(f"_{warning}_")
            turn = WorkflowRegistry.advance(session, "")
        return turn, tool_messages

    def _maybe_handle_fast_paths(
        self,
        *,
        message: str,
        rewritten: str,
        thread_id: str,
        locale: str,
    ) -> dict[str, Any] | None:
        """Deterministic fast paths, in precedence order (both chat paths):

        1. TIN-registration asks with an unspecified taxpayer type start a
           one-question clarification (individual vs organisation);
        2. calculations with figures compute instantly, without figures they
           elicit the missing details;
        3. rate questions answer from the versioned FY rate table.
        """
        return (
            self._maybe_handle_tin_clarification(
                message=message, rewritten=rewritten, thread_id=thread_id, locale=locale
            )
            or self._maybe_handle_calculator(
                message=message, rewritten=rewritten, thread_id=thread_id, locale=locale
            )
            or self._maybe_handle_rate_lookup(
                message=message, rewritten=rewritten, thread_id=thread_id, locale=locale
            )
        )

    def _maybe_handle_rate_lookup(
        self,
        *,
        message: str,
        rewritten: str,
        thread_id: str,
        locale: str,
    ) -> dict[str, Any] | None:
        """Answer "what is the current VAT rate?"-style questions exactly.

        Uses the same versioned FY rate table the calculators use, so the
        reply is a real figure instead of retrieval passages that happen to
        mention the tax. Falls back to RAG when the authority manifest is
        stale or the question names no known rate.
        """
        rate_plan = plan_rate_lookup(message) or plan_rate_lookup(rewritten)
        if rate_plan is None:
            return None
        try:
            from .tax.tables import get_table  # noqa: PLC0415
            from .tools.rates import _authority_payload  # noqa: PLC0415

            authority_ok, _status = _authority_payload()
            if not authority_ok:
                logger.info("rate fast path skipped: authority manifest not fresh")
                return None
            reply_text, next_actions = format_rate_reply(rate_plan, get_table())
        except Exception:
            logger.exception("rate lookup fast path failed")
            return None
        if not reply_text:
            return None
        return {
            "reply": self._finalize_reply(reply_text),
            "sources": [],
            "citations": [],
            "faithfulness_score": None,
            "retrieval_mode": "calculator",
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": False,
            "escalation_reason": "",
            "agent_role": "tool_specialist",
            "handoff": None,
            "response_judge": {
                "decision": "approve",
                "final_decision": "approve",
                "applied_revision": False,
                "reasons": ["official rate table"],
                "confidence_band": "high",
            },
            "next_actions": next_actions,
            "ticket_id": "",
        }

    def _maybe_handle_tin_clarification(
        self,
        *,
        message: str,
        rewritten: str,
        thread_id: str,
        locale: str,
    ) -> dict[str, Any] | None:
        """Ask individual-vs-organisation before giving TIN registration steps.

        Fires only when the ask names neither type; typed asks are answered
        immediately by the deterministic template, and completion of this
        one-question flow is special-cased in ``_maybe_handle_workflow``.
        """
        combined = f"{message or ''} {rewritten or ''}"
        if not _TIN_REGISTRATION_QUERY_RE.search(combined):
            return None
        if _TIN_ORG_QUERY_RE.search(combined) or _TIN_INDIVIDUAL_QUERY_RE.search(combined):
            return None
        if not flags.is_enabled("workflows") or self._workflow_count <= 0:
            return None
        wf = WorkflowRegistry.get("tin_procedure_help")
        if wf is None:
            return None
        session = WorkflowRegistry.create_session(wf.id)
        if session is None:
            return None
        turn, _tool_messages = self._advance_workflow(session, "")
        prompt = turn.question or ""
        db.upsert_workflow_session(
            thread_id,
            session.workflow_id,
            session.current_step_idx,
            session.slots,
            status="active",
            last_prompt=prompt,
        )
        workflow = self._workflow_view(
            session,
            name=wf.name,
            status="active",
            pending_slot=turn.slot_name,
        )
        return {
            "reply": f"Happy to help you get registered!\n\n{prompt}",
            "sources": [],
            "citations": [],
            "faithfulness_score": None,
            "retrieval_mode": "workflow",
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": False,
            "escalation_reason": "",
            "agent_role": "workflow_guide",
            "workflow": workflow,
            "next_actions": self._default_next_actions(
                agent_role="workflow_guide",
                workflow=workflow,
            ),
        }

    def _maybe_handle_calculator(
        self,
        *,
        message: str,
        rewritten: str,
        thread_id: str,
        locale: str,
    ) -> dict[str, Any] | None:
        """Deterministic tax-calculator fast path (REST and streaming parity).

        A calculation ask whose message already carries every figure is
        answered instantly from the registered calculator tool — exact
        arithmetic, no LLM. When something is missing, the matching guided
        calculator workflow starts pre-filled with everything the message
        did contain, so the user is asked only for what's absent.
        """
        plan = plan_calculation(message) or plan_calculation(rewritten)
        if plan is None:
            return None

        if not plan.missing:
            try:
                from .mcp import get_client  # noqa: PLC0415

                call = get_client().call_tool(plan.tool, dict(plan.params), user_role="public")
                result = call.result
            except Exception:
                logger.exception("calculator tool execution failed")
                return None
            if not result.get("ok"):
                logger.info("calculator rejected extracted args: %s", result.get("error", ""))
                return None
            reply = self._finalize_reply(
                format_calc_reply(plan.tool, result, plan.assumptions)
            )
            return {
                "reply": reply,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "calculator",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "tool_specialist",
                "handoff": None,
                "response_judge": {
                    "decision": "approve",
                    "final_decision": "approve",
                    "applied_revision": False,
                    "reasons": ["deterministic tax calculator"],
                    "confidence_band": "high",
                },
                "next_actions": NEXT_ACTIONS_BY_TOOL.get(plan.tool, []),
                "ticket_id": "",
            }

        # Missing details → guided elicitation via the matching workflow,
        # sharing the durable-session machinery (and flag gate) of
        # _maybe_handle_workflow so mid-flow answers keep working.
        if not flags.is_enabled("workflows") or self._workflow_count <= 0:
            return None
        wf = WorkflowRegistry.get(plan.workflow_id)
        if wf is None:
            return None
        session = WorkflowRegistry.create_session(plan.workflow_id)
        if session is None:
            return None
        session.slots.update(plan.params)

        turn, tool_messages = self._advance_workflow(session, "")
        prompt = turn.question or ""
        if tool_messages:
            prompt = "\n\n".join(tool_messages + [prompt]).strip()
        status = "completed" if (session.completed or turn.is_complete) else "active"
        db.upsert_workflow_session(
            thread_id,
            session.workflow_id,
            session.current_step_idx,
            session.slots,
            status=status,
            last_prompt=prompt,
        )
        workflow = self._workflow_view(
            session,
            name=wf.name,
            status=status,
            pending_slot=turn.slot_name,
        )
        intro = "I can work that out for you — I just need a detail or two."
        if plan.assumptions:
            intro += (
                "\n\n_I'll assume: "
                + "; ".join(plan.assumptions)
                + " — correct me if that's wrong._"
            )
        reply = f"{intro}\n\n{prompt}" if prompt else intro
        return {
            "reply": reply,
            "sources": [],
            "citations": [],
            "faithfulness_score": None,
            "retrieval_mode": "workflow",
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": False,
            "escalation_reason": "",
            "agent_role": "workflow_guide",
            "workflow": workflow,
            "next_actions": self._default_next_actions(
                agent_role="workflow_guide",
                workflow=workflow,
            ),
        }

    def _maybe_handle_workflow(
        self,
        *,
        message: str,
        rewritten: str,
        thread_id: str,
        locale: str,
        personalization: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Start or continue a durable guided workflow when appropriate."""
        if not flags.is_enabled("workflows") or self._workflow_count <= 0:
            return None

        persisted = db.get_workflow_session(thread_id)
        if persisted and persisted.get("status") == "active":
            session = self._restore_workflow_session(persisted)
            self._apply_personalization_to_workflow(session, personalization)
            wf = WorkflowRegistry.get(session.workflow_id)
            if wf is None:
                db.complete_workflow_session(thread_id, status="cancelled")
                return None
            user_input = (message or "").strip()
            if user_input.lower() in _WORKFLOW_CANCEL_WORDS:
                db.complete_workflow_session(thread_id, status="cancelled")
                workflow = self._workflow_view(
                    session,
                    name=wf.name,
                    status="cancelled",
                )
                return {
                    "reply": (
                        f"I've stopped the {wf.name} workflow. Ask a new URA question whenever "
                        "you're ready."
                    ),
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "workflow",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "workflow_guide",
                    "workflow": workflow,
                    "next_actions": ["Ask a new question or restart the guided process later."],
                }

            turn, tool_messages = self._advance_workflow(session, user_input)

            # The TIN clarification flow ends in a curated deterministic
            # answer keyed on the collected taxpayer kind — not a generic
            # workflow completion prompt.
            if wf.id == "tin_procedure_help" and (session.completed or turn.is_complete):
                db.complete_workflow_session(thread_id, status="completed")
                kind = str(session.slots.get("taxpayer_kind", "individual"))
                workflow = self._workflow_view(session, name=wf.name, status="completed")
                return {
                    "reply": self._finalize_reply(self._tin_procedure_reply(kind)),
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": 1.0,
                    "retrieval_mode": "workflow",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "workflow_guide",
                    "handoff": None,
                    "response_judge": {
                        "decision": "approve",
                        "final_decision": "approve",
                        "applied_revision": False,
                        "reasons": ["curated deterministic template"],
                        "confidence_band": "high",
                    },
                    "workflow": workflow,
                    "next_actions": self._default_next_actions(
                        agent_role="workflow_guide",
                        workflow=workflow,
                    ),
                }

            status = "completed" if (session.completed or turn.is_complete) else "active"
            prompt = turn.question or persisted.get("last_prompt", "") or f"Let's continue the {wf.name} workflow."
            if tool_messages:
                prompt = "\n\n".join(tool_messages + [prompt]).strip()
            if status == "completed":
                db.upsert_workflow_session(
                    thread_id,
                    session.workflow_id,
                    session.current_step_idx,
                    session.slots,
                    status="completed",
                    last_prompt=prompt,
                )
            else:
                db.upsert_workflow_session(
                    thread_id,
                    session.workflow_id,
                    session.current_step_idx,
                    session.slots,
                    status="active",
                    last_prompt=prompt,
                )
            workflow = self._workflow_view(
                session,
                name=wf.name,
                status=status,
                pending_slot=turn.slot_name,
            )
            return {
                "reply": prompt,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "workflow",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "workflow_guide",
                "workflow": workflow,
                "next_actions": self._default_next_actions(
                    agent_role="workflow_guide",
                    workflow=workflow,
                ),
            }

        matched = WorkflowRegistry.match_trigger(message) or WorkflowRegistry.match_trigger(rewritten)
        if matched is None:
            return None
        combined_query = f"{message or ''} {rewritten or ''}".strip()
        if (
            _INFORMATIONAL_WORKFLOW_QUERY_RE.search(combined_query)
            and not _EXPLICIT_WORKFLOW_START_RE.search(combined_query)
        ):
            return None

        session = WorkflowRegistry.create_session(matched.id)
        if session is None:
            return None
        self._apply_personalization_to_workflow(session, personalization)

        turn, tool_messages = self._advance_workflow(session, "")
        prompt = turn.question or f"Let's start the {matched.name} workflow."
        if tool_messages:
            prompt = "\n\n".join(tool_messages + [prompt]).strip()
        status = "completed" if (session.completed or turn.is_complete) else "active"
        db.upsert_workflow_session(
            thread_id,
            session.workflow_id,
            session.current_step_idx,
            session.slots,
            status=status,
            last_prompt=prompt,
        )
        workflow = self._workflow_view(
            session,
            name=matched.name,
            status=status,
            pending_slot=turn.slot_name,
        )
        reply = (
            f"I can guide you through the {matched.name} process step by step.\n\n{prompt}"
            if prompt
            else f"I can guide you through the {matched.name} process step by step."
        )
        return {
            "reply": reply,
            "sources": [],
            "citations": [],
            "faithfulness_score": None,
            "retrieval_mode": "workflow",
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": False,
            "escalation_reason": "",
            "agent_role": "workflow_guide",
            "workflow": workflow,
            "next_actions": self._default_next_actions(
                agent_role="workflow_guide",
                workflow=workflow,
            ),
        }

    # -- Chat (RAG) ---------------------------------------------------------
    def generate(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 6,
        locale: str = "en",
        session_id: str | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        user_role: str = "public",
        granted_purposes: list[str] | None = None,
        attachments: list[documents_module.DocumentRecord] | None = None,
    ) -> dict[str, Any]:
        """Return a grounded, cited answer via hybrid retrieval + guardrails.

        ``attachments`` are pre-analysed documents (``documents.DocumentRecord``)
        resolved by the endpoint from ``ChatRequest.attachment_ids``; their
        extracted content is injected as top-priority grounding passages.
        """
        t0 = time.perf_counter()
        thread_id = conversation_id or str(uuid.uuid4())
        agent_role = "rag_answerer"

        with trace_rag_pipeline(message, request_id=request_id) as trace_ctx:
            timings = trace_ctx["timings"]
            trace_ctx["user_id"] = user_id or ""
            trace_ctx["tenant_id"] = tenant_id or "default"

            # 0. Multi-turn memory — fetch recent conversation history (Phase 4)
            conversation_history: list[dict[str, str]] = []
            history_session_id = None if conversation_id else session_id
            if conversation_id or history_session_id:
                try:
                    conversation_history = db.get_recent_turns(
                        session_id=history_session_id,
                        conversation_id=conversation_id,
                        limit=5,
                    )
                except Exception:
                    logger.debug("Failed to fetch conversation history", exc_info=True)

            # 0b. Query rewriting — spell correction, abbreviation expansion,
            #     coreference resolution from history (Phase 4)
            with trace_stage("query_rewrite", timings=timings):
                rewritten = rewrite_query(message, history=conversation_history or None)

            # 0c. Language detection — auto-detect user's language for
            #     adapter routing and locale-aware responses.
            if locale == "en":
                with trace_stage("lang_detect", timings=timings):
                    detected_locale = detect_language(message)
                    if detected_locale != "en":
                        locale = detected_locale
                        logger.info("Auto-detected locale: %s", locale)

            personalization = self._load_personalization_state(user_id)
            # Attachment turns are never cache-served or cache-stored: the answer
            # is specific to the attached document, not the query text alone.
            cache_allowed = personalization is None and not attachments

            # Emotional-intelligence signal for this turn: adapts the LLM
            # opening line (tone_hint) and prefixes deterministic replies
            # with a short empathy acknowledgment. Never cached.
            distress = detect_user_distress(message)
            tone_hint = tone_hint_for(distress)

            # A distressed message mixes emotional preamble with the real
            # question; retrieving on the raw combination dilutes relevance
            # enough to false-abstain, so retrieval searches on just the
            # question span when one is extractable. Cache keys, deterministic-
            # template matching, and workflow/calculator routing still use the
            # full `rewritten` text unchanged.
            retrieval_query = rewritten
            # _simple_search's binding_query keeps FAQ-match authorization
            # bound to the user's own (unexpanded) words rather than
            # rewrite()'s abbreviation expansion — see its docstring. That
            # same authorization gate must drop the distress preamble too,
            # or it silently re-dilutes match coverage and rejects the very
            # FAQ retrieval_query just found, independent of the search step.
            binding_query = message
            if distress:
                question_span = extract_question_span(rewritten)
                if question_span:
                    retrieval_query = question_span
                message_question_span = extract_question_span(message)
                if message_question_span:
                    binding_query = message_question_span

            # 1. Input guardrails FIRST (OWASP LLM01) — check original message
            with trace_stage("input_guard", timings=timings):
                guard = self._input_guard.check(message)
            if not guard.allowed:
                blocked = {
                    "reply": guard.reason,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "blocked",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "safety_guard",
                    "next_actions": ["Rephrase your question about a URA service — I'm glad to help."],
                }
                self._audit_turn(
                    message=message, result=blocked, session_id=session_id, trace_ctx=trace_ctx
                )
                return blocked

            if flags.is_enabled("workflows"):
                with trace_stage("workflow_router", timings=timings):
                    workflow_result = self._maybe_handle_workflow(
                        message=message,
                        rewritten=rewritten,
                        thread_id=thread_id,
                        locale=locale,
                        personalization=personalization,
                    )
                if workflow_result:
                    if distress and workflow_result.get("reply"):
                        workflow_result["reply"] = (
                            f"{empathy_ack(distress)}\n\n{workflow_result['reply']}"
                        )
                    trace_ctx["agent_role"] = "workflow_guide"
                    self._audit_turn(
                        message=message,
                        result=workflow_result,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return workflow_result

            # 1a1b. Deterministic tax calculator — instant when the message
            #       carries the figures, guided elicitation when it doesn't.
            with trace_stage("calculator_router", timings=timings):
                calc_result = self._maybe_handle_fast_paths(
                    message=message,
                    rewritten=rewritten,
                    thread_id=thread_id,
                    locale=locale,
                )
            if calc_result:
                if distress and calc_result.get("reply"):
                    calc_result["reply"] = f"{empathy_ack(distress)}\n\n{calc_result['reply']}"
                trace_ctx["agent_role"] = calc_result.get("agent_role", "tool_specialist")
                self._audit_turn(
                    message=message,
                    result=calc_result,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return calc_result

            # 1a1. A human answered. Deliver it before anything else —
            #      the taxpayer was told someone would follow up, and the
            #      officer's answer outranks anything the bot would say.
            officer_note = self._deliver_officer_reply(thread_id)
            if officer_note:
                delivered = {
                    "reply": officer_note,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "officer_reply",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "human_officer",
                    "next_actions": self._default_next_actions(agent_role="human_officer"),
                }
                self._audit_turn(
                    message=message,
                    result=delivered,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return delivered

            # 1a2. Greeting detection — always active, independent of agentic_mode
            _q_lower = message.strip().lower().strip("!.?,")
            _q_words = message.strip().split()
            if len(_q_words) <= 3 and (
                _q_lower in _GREETING_WORDS
                or _q_lower in _GREETING_PHRASES
                or all(w.lower().strip("!.?,") in _GREETING_WORDS for w in _q_words)
            ):
                greeted = {
                    "reply": GREETING_REPLY,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "greeting",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "greeting_agent",
                    "next_actions": [
                        "Ask about TIN registration",
                        "Learn about VAT",
                        "File a tax return",
                    ],
                }
                self._audit_turn(
                    message=message,
                    result=greeted,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return greeted

            # 1a3. Gratitude / farewell — closing courtesy, same always-on
            # short-circuit as greetings (no retrieval, never scored).
            closing_reply = _closing_courtesy_reply(message)
            if closing_reply:
                closing = {
                    "reply": closing_reply,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "greeting",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "greeting_agent",
                    "next_actions": [
                        "Ask about TIN registration",
                        "Learn about VAT",
                        "File a tax return",
                    ],
                }
                self._audit_turn(
                    message=message,
                    result=closing,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return closing

            # 1b. Semantic cache check AFTER guardrails (Phase 5)
            if cache_allowed:
                with trace_stage("cache_lookup", timings=timings):
                    cached = self._cache.get(rewritten, locale=locale)
                if cached:
                    logger.info("generate: cache HIT for query=%s", message[:50])
                    return self._finalize_result({
                        **cached,
                        "conversation_id": thread_id,
                        "locale": locale,
                    })

            # 1c. Phase 14-C — supervisor routing.  When FLAG_AGENTIC_MODE
            #     is on, the supervisor classifies the query and routes
            #     to one of: RAG (default), TOOLS, a specialist, CLARIFY
            #     (ask for details), or ESCALATE (human handoff).
            route_decision = None
            force_agentic = False
            force_tool_whitelist: list[str] | None = None
            if flags.is_enabled("agentic_mode"):
                with trace_stage("supervisor", timings=timings):
                    route_decision = supervisor.classify(
                        rewritten,
                        has_conversation_history=bool(conversation_history),
                        locale=locale,
                    )
                trace_ctx["agent_route"] = route_decision.route.value
                trace_ctx["agent_route_confidence"] = (
                    route_decision.route_confidence
                    if hasattr(route_decision, "route_confidence")
                    else route_decision.confidence
                )
                logger.info(
                    "supervisor: route=%s confidence=%.2f reason=%s",
                    route_decision.route.value,
                    route_decision.confidence,
                    route_decision.reason,
                )

                # Capability tier for this turn.  The route decision is
                # already made and costs nothing extra to reuse, so the
                # tier is selected here and carried on the trace whether
                # or not the flag is on — off, it reports T1, which is
                # what the single configured model has always been.
                tier_decision = select_tier(
                    route_decision.route.value,
                    confidence=route_decision.confidence,
                    tool_count=len(route_decision.suggested_tools),
                    locale=locale,
                    escalation_reason=route_decision.reason,
                    distress=detect_user_distress(rewritten) or "",
                    enabled=flags.is_enabled("model_tiering"),
                )
                trace_ctx["model_tier"] = tier_decision.tier.value
                trace_ctx["model_tier_reason"] = tier_decision.reason
                log_tier("chat", tier_decision)

                # Early returns — CLARIFY and ESCALATE don't need retrieval.
                if route_decision.route == AgentRoute.GREET:
                    greeted = {
                        "reply": GREETING_REPLY,
                        "sources": [],
                        "citations": [],
                        "faithfulness_score": None,
                        "retrieval_mode": "greeting",
                        "model": self.name,
                        "conversation_id": thread_id,
                        "locale": locale,
                        "escalation_required": False,
                        "escalation_reason": "",
                        "agent_role": "greeting_agent",
                        "next_actions": [
                            "Ask about TIN registration",
                            "Learn about VAT",
                            "File a tax return",
                        ],
                    }
                    self._audit_turn(
                        message=message,
                        result=greeted,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return greeted
                if route_decision.route == AgentRoute.CLARIFY:
                    clarified = {
                        "reply": route_decision.clarification_question
                        or CLARIFICATION_PROMPT,
                        "sources": [],
                        "citations": [],
                        "faithfulness_score": None,
                        "retrieval_mode": "clarification",
                        "model": self.name,
                        "conversation_id": thread_id,
                        "locale": locale,
                        "escalation_required": False,
                        "escalation_reason": "",
                        "agent_role": "clarification_agent",
                        "next_actions": self._default_next_actions(
                            agent_role="clarification_agent",
                        ),
                    }
                    self._audit_turn(
                        message=message,
                        result=clarified,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return clarified
                if route_decision.route == AgentRoute.ESCALATE:
                    ticket_id = ""
                    handoff = None
                    response_judge = {
                        "decision": "escalate",
                        "final_decision": "escalate",
                        "applied_revision": False,
                        "reasons": [route_decision.reason] if route_decision.reason else [],
                        "confidence_band": "low",
                    }
                    if flags.is_enabled("handoff_summaries"):
                        handoff = self._build_handoff_packet(
                            message=message,
                            reason=route_decision.reason,
                            conversation_history=conversation_history or None,
                        )

                    reply = ESCALATION_REPLY_LEAD
                    ticket_id = self._maybe_create_ticket(
                        reason=route_decision.reason,
                        user_query=message,
                        bot_reply=reply,
                        session_id=session_id,
                        conversation_id=thread_id,
                        priority=(handoff or {}).get("priority", "normal"),
                        handoff=handoff,
                        response_judge=response_judge,
                        user_id=user_id or "",
                    )
                    if ticket_id:
                        trace_ctx["ticket_id"] = ticket_id
                    if ticket_id:
                        reply += f" (ticket {ticket_id[:8]})"
                    reply += ESCALATION_REPLY_FOOTER
                    escalated = {
                        "reply": reply,
                        "sources": [],
                        "citations": [],
                        "faithfulness_score": None,
                        "retrieval_mode": "escalated",
                        "model": self.name,
                        "conversation_id": thread_id,
                        "locale": locale,
                        "escalation_required": True,
                        "escalation_reason": route_decision.reason,
                        "agent_role": "escalation_triage",
                        "handoff": handoff,
                        "response_judge": response_judge,
                        "next_actions": self._default_next_actions(
                            agent_role="escalation_triage",
                            handoff=handoff,
                            escalation_required=True,
                        ),
                        "ticket_id": ticket_id,
                    }
                    self._audit_turn(
                        message=message,
                        result=escalated,
                        session_id=session_id,
                        trace_ctx=trace_ctx,
                    )
                    return escalated

                # TOOLS / SPECIALIST routes force the agentic LLM path
                # downstream, with the supervisor's suggested tool whitelist.
                if route_decision.route in (
                    AgentRoute.TOOLS,
                    AgentRoute.TAX_SPECIALIST,
                    AgentRoute.CUSTOMS_SPECIALIST,
                ):
                    force_agentic = True
                    route_role_map = {
                        AgentRoute.TOOLS: "tool_specialist",
                        AgentRoute.TAX_SPECIALIST: "tax_specialist",
                        AgentRoute.CUSTOMS_SPECIALIST: "customs_specialist",
                    }
                    agent_role = route_role_map.get(route_decision.route, "tool_specialist")
                    if route_decision.suggested_tools:
                        force_tool_whitelist = list(route_decision.suggested_tools)
                    trace_ctx["specialist"] = route_decision.route.value

            # 2. Try hybrid retrieval using rewritten query
            hits: list[dict[str, Any]] = []
            retrieval_mode = "keyword"

            # Auto-reconnect if Qdrant was lost after initial startup
            if not self._retriever_ready and not self._retriever._ready:
                self._retriever_ready = self._retriever.initialize()

            if self._retriever_ready:
                with trace_stage("hybrid_search", timings=timings):
                    search_t0 = time.perf_counter()
                    hits = self._retriever.search(retrieval_query, top_k=top_k)
                    search_ms = (time.perf_counter() - search_t0) * 1000
                if hits:
                    retrieval_mode = "hybrid"
                    record_retrieval_metrics(len(hits), search_ms)
                # Update readiness if retriever was disconnected during search
                self._retriever_ready = self._retriever._ready

            # 3. Fallback to keyword search if Qdrant returned nothing.
            # Use _faq_hits_to_retrieval_hits so the per-hit _overlap count
            # carries into score_rrf (was hardcoded 0.0 here, which guaranteed
            # OutputGuard.should_abstain rejected every keyword-only hit on
            # the BM25-only Crane Cloud profile).
            if not hits:
                with trace_stage("keyword_search_fallback", timings=timings):
                    kw_hits = _simple_search(
                        retrieval_query,
                        self._faq_index,
                        top_k=top_k,
                        binding_query=binding_query,
                        locale=locale,
                    )
                    hits = _faq_hits_to_retrieval_hits(kw_hits)

            # 3b. Corrective RAG — re-retrieve if quality is low (Phase 6)
            if hits and self._retriever_ready:
                with trace_stage("corrective_rag", timings=timings):
                    hits, was_corrected = corrective_retrieve(
                        retrieval_query, self._retriever, hits, top_k=top_k
                    )
                    if was_corrected:
                        retrieval_mode = "hybrid_corrected"

            # 3b2. Language-aware retrieval boosting — when the detected
            #      locale is non-English, boost hits whose metadata
            #      matches the detected language (e.g. Luganda FAQ sources).
            if locale != "en" and hits:
                locale_keywords = {
                    "lg": {"luganda", "oluganda", "lg"},
                    "sw": {"swahili", "kiswahili", "sw"},
                    "nyn": {"runyankole", "nkore", "nyn"},
                    "ach": {"acholi", "ach"},
                }
                boost_terms = locale_keywords.get(locale, set())
                if boost_terms:
                    for h in hits:
                        source = (h.get("source") or "").lower()
                        text_preview = (h.get("text") or "")[:200].lower()
                        if any(t in source or t in text_preview for t in boost_terms):
                            h["score_rrf"] = h.get("score_rrf", 0.5) + 0.3
                    # Re-sort by boosted score
                    hits.sort(key=lambda x: x.get("score_rrf", 0), reverse=True)

            # 3c. Always blend top FAQ keyword hits AFTER corrective RAG
            #     so precise CSV FAQ steps are never filtered out by reranking.
            with trace_stage("faq_blend", timings=timings):
                kw_hits = _simple_search(
                    retrieval_query,
                    self._faq_index,
                    top_k=2,
                    binding_query=binding_query,
                    locale=locale,
                )
                priority_hits = self._priority_faq_hits(retrieval_query, top_k=2)
                seen_texts = {h.get("text", "")[:80] for h in hits}
                # Graph claims go in first: they carry the statutory
                # basis and the fiscal year, so where they and a passage
                # disagree the passage is the one that is out of date.
                for h in self._graph_hits(retrieval_query):
                    if h.get("text", "")[:80] not in seen_texts:
                        hits.insert(0, h)
                        seen_texts.add(h.get("text", "")[:80])
                        retrieval_mode = "graph"
                for h in priority_hits:
                    if h.get("text", "")[:80] not in seen_texts:
                        hits.insert(0, h)
                        seen_texts.add(h.get("text", "")[:80])
                        retrieval_mode = "faq_priority"
                for h in kw_hits:
                    faq_text = f"Question: {h['question']}\nAnswer: {h['answer']}"
                    if faq_text[:80] not in seen_texts:
                        hits.append({
                            "text": faq_text,
                            "answer": h["answer"],
                            "question": h["question"],
                            "source": h["source"],
                            "chunk_id": "",
                            "page": "",
                            "section": h.get("tag", ""),
                            "doc_type": "csv",
                            "score_rrf": 0.5,
                        })
                        seen_texts.add(faq_text[:80])

            # Bind indexed FAQ passages to the user's original intent before
            # they can be used as LLM context or as an extractive fallback.
            # Same binding_query as above — a distress preamble in `message`
            # would otherwise re-dilute this second authorization gate too
            # and filter every hit out (score below cutoff for all of them).
            hits = _filter_unbound_faq_hits(binding_query, hits)

            # 3d. Attached documents — prepend as top-priority grounding hits.
            #     They flow through the same LLM01 scrub + spotlight markers
            #     as retrieved passages (llm._build_messages) and count as
            #     grounding for faithfulness scoring. The raw user message
            #     stays subject to the normal input guardrails above.
            if attachments:
                hits = documents_module.attachment_passages(attachments) + hits

            # 3c. Clarification check — ask for more details if query is ambiguous
            #     (skipped for attachment turns: "what is this?" is answerable
            #     from the attached document, not ambiguous)
            clarification = None if attachments else needs_clarification(message, hits)
            if clarification:
                clarify_result = {
                    "reply": clarification,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "clarification",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "clarification_agent",
                    "next_actions": self._default_next_actions(
                        agent_role="clarification_agent",
                    ),
                }
                self._audit_turn(
                    message=message,
                    result=clarify_result,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return clarify_result

            deterministic_reply = ""
            deterministic_curated = False
            deterministic_sources: list[str] = []
            deterministic_citations: list[dict[str, Any]] = []
            # Attachment turns always go to the LLM — a canned procedure
            # template cannot read the attached document.
            if hits and not attachments:
                deterministic_sources = list({h.get("source", "") for h in hits if h.get("source")})
                deterministic_citations = HybridRetriever.build_citations(hits)
                deterministic_reply, deterministic_curated = self._deterministic_procedure_reply(
                    rewritten, hits, deterministic_citations
                )
            if deterministic_reply:
                reply = self._finalize_reply(deterministic_reply)
                result = self._deterministic_result(
                    reply=reply,
                    curated=deterministic_curated,
                    hits=hits,
                    sources=deterministic_sources,
                    citations=deterministic_citations,
                    retrieval_mode=retrieval_mode,
                    thread_id=thread_id,
                    locale=locale,
                    agent_role=agent_role,
                )
                if cache_allowed:
                    # Cache the neutral copy — a calm user hitting this entry
                    # later must not receive someone else's empathy opener.
                    self._cache.put(rewritten, dict(result))
                if distress:
                    reply = f"{empathy_ack(distress)}\n\n{reply}"
                    result["reply"] = reply
                self._persist_personalization_turn(
                    user_id=user_id,
                    conversation_id=thread_id,
                    message=message,
                    reply=reply,
                    agent_role=agent_role,
                    personalization=personalization,
                )
                self._audit_turn(
                    message=message,
                    result=result,
                    session_id=session_id,
                    trace_ctx=trace_ctx,
                )
                return result

            # 4. Calibrated abstention — refuse to answer when confidence too low
            with trace_stage("abstention_check", timings=timings):
                # Attached documents are always usable grounding — never abstain.
                should_abstain = not attachments and self._output_guard.should_abstain(hits)
            if should_abstain:
                reply = ABSTENTION_REPLY
                if distress:
                    reply = f"{empathy_ack(distress)}\n\n{reply}"
                escalate, esc_reason = self._output_guard.should_escalate(None, hits)
                handoff = None
                response_judge = {
                    "decision": "escalate" if escalate else "approve",
                    "final_decision": "escalate" if escalate else "approve",
                    "applied_revision": False,
                    "reasons": [esc_reason] if esc_reason else [],
                    "confidence_band": "low",
                }
                if flags.is_enabled("handoff_summaries") and escalate:
                    handoff = self._build_handoff_packet(
                        message=message,
                        reason=esc_reason,
                        conversation_history=conversation_history or None,
                        hits=hits,
                    )
                ticket_id = self._maybe_create_ticket(
                    reason=esc_reason,
                    user_query=message,
                    bot_reply=reply,
                    session_id=session_id,
                    conversation_id=thread_id,
                    priority=(handoff or {}).get("priority", "normal"),
                    handoff=handoff,
                    response_judge=response_judge,
                    user_id=user_id or "",
                )
                abstained = {
                    "reply": reply,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "abstained",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": escalate,
                    "escalation_reason": esc_reason,
                    "agent_role": agent_role,
                    "handoff": handoff,
                    "response_judge": response_judge,
                    "next_actions": self._default_next_actions(
                        agent_role=agent_role,
                        handoff=handoff,
                        escalation_required=escalate,
                    ),
                    "ticket_id": ticket_id,
                }
                self._audit_turn(
                    message=message, result=abstained, session_id=session_id, trace_ctx=trace_ctx
                )
                return abstained

            # 5. Build response with citations
            extractive_fallback = False
            if hits:
                sources = list({h.get("source", "") for h in hits if h.get("source")})
                citations = HybridRetriever.build_citations(hits)
                contexts = [h.get("text") or h.get("answer", "") for h in hits]

                # Phase 2: LLM synthesis from top-k passages (true RAG).
                # The cloud fallback alone is enough to keep generation on
                # when no local LLM is configured (_call_llm_with_deadline
                # routes there via the breaker/empty-reply handling).
                if self._llm_available or _cloud_llm_ready():
                    # Phase 14-B/C: agentic path is active when either
                    # FLAG_TOOL_USE is on (tool calling for everyone), or
                    # the supervisor routed this specific request to it
                    # (force_agentic).  The supervisor can also narrow
                    # the tool whitelist (force_tool_whitelist).  Tool
                    # calling runs on the local model only, so the agentic
                    # branch additionally requires local availability.
                    use_agentic = (
                        force_agentic or flags.is_enabled("tool_use")
                    ) and self._llm_available
                    if use_agentic:
                        with trace_stage("llm_agentic", timings=timings):
                            agentic = _call_llm_agentic(
                                query=rewritten,
                                passages=hits,
                                conversation_history=conversation_history or None,
                                locale=locale,
                                tool_names=force_tool_whitelist,
                                personalization_context=(
                                    (personalization or {}).get("prompt_context", "")
                                ),
                                tone_hint=tone_hint,
                                tenant_id=tenant_id or "default",
                                user_id=user_id or "",
                                user_role=user_role,
                                granted_purposes=granted_purposes or [],
                                # The supervisor already decided which
                                # specialist this is; give it the
                                # instructions that go with the label.
                                agent_role=agent_role,
                            )
                        reply = agentic.get("text", "")
                        if agentic.get("tool_calls"):
                            trace_ctx["tool_calls"] = [
                                tc.get("name") for tc in agentic["tool_calls"]
                            ]
                            trace_ctx["tool_iterations"] = agentic.get("iterations", 0)
                        if not reply:
                            # Agentic produced no text (breaker OPEN, deadline,
                            # or empty completion).  Run the plain RAG chain —
                            # _call_llm_with_deadline carries the cloud
                            # fallback — before dropping to the extractive
                            # best-hit answer, mirroring stream_chat_turn's
                            # fall-through to stream_llm_tokens.
                            with trace_stage("llm_generate", timings=timings):
                                reply = _call_llm_with_deadline(
                                    query=rewritten,
                                    passages=hits,
                                    conversation_history=conversation_history or None,
                                    locale=locale,
                                    personalization_context=(
                                        (personalization or {}).get("prompt_context", "")
                                    ),
                                    tone_hint=tone_hint,
                                )
                    else:
                        with trace_stage("llm_generate", timings=timings):
                            reply = _call_llm_with_deadline(
                                query=rewritten,
                                passages=hits,
                                conversation_history=conversation_history or None,
                                locale=locale,
                                personalization_context=(
                                    (personalization or {}).get("prompt_context", "")
                                ),
                                tone_hint=tone_hint,
                            )
                    # Optional structured-output parse (LLM_STRUCTURED_OUTPUT=true)
                    if reply and llm_module.LLM_STRUCTURED_OUTPUT and not use_agentic:
                        valid_refs = [str(i) for i in range(1, len(hits) + 1)]
                        parsed = llm_module.parse_structured_reply(reply, valid_refs)
                        if parsed["structured"]:
                            reply = parsed["answer"]
                            # Filter citations to refs the model actually cited
                            cited_refs = set(parsed["citations"])
                            if cited_refs:
                                citations = [
                                    c for c in citations if c["ref"].strip("[]") in cited_refs
                                ]
                            trace_ctx["structured_output"] = True
                            if parsed["abstain"]:
                                retrieval_mode = "abstained"
                    if not reply:
                        # Fallback to best-hit answer if LLM fails, times out,
                        # or the circuit breaker is open
                        best = hits[0]
                        reply = best.get("answer") or best.get("text", "")
                        if citations and not re.search(r"\[\d{1,3}\]", reply):
                            reply = f"{reply.rstrip()} [1]"
                        extractive_fallback = True
                else:
                    # FAQ lookup fallback (no LLM configured)
                    best = hits[0]
                    reply = best.get("answer") or best.get("text", "")
                    if citations and not re.search(r"\[\d{1,3}\]", reply):
                        reply = f"{reply.rstrip()} [1]"
                    extractive_fallback = True
            else:
                reply = NO_HITS_REPLY
                if distress:
                    reply = f"{empathy_ack(distress)}\n\n{reply}"
                sources = []
                citations = []
                contexts = []

            # 6. Output guardrails (OWASP LLM02 + LLM05 + LLM07)
            with trace_stage("output_guard", timings=timings):
                reply = self._finalize_reply(reply)
                leakage = self._output_guard.check_prompt_leakage(reply)
                if leakage.flags:
                    trace_ctx["prompt_leakage"] = True

            # 7. Grounding verification (OWASP LLM09)
            faithfulness_score: float | None = None
            reflect_fired = False
            if contexts:
                with trace_stage("grounding", timings=timings):
                    faith = HybridRetriever.compute_faithfulness(reply, contexts)
                    faithfulness_score = faith

                # 7b. Self-reflection — regenerate once if grounding is weak
                # (Self-RAG, 2023/2024).  Feature-flagged off by default
                # because it doubles LLM latency when triggered.
                # Honours FLAG_SELF_REFLECT / legacy SELF_REFLECT_ENABLED env.
                if (
                    (flags.is_enabled("self_reflect") or SELF_REFLECT_ENABLED)
                    and self._llm_available
                    and faith is not None
                    and faith < SELF_REFLECT_THRESHOLD
                ):
                    with trace_stage("self_reflect", timings=timings):
                        revise_query = (
                            f"Previous answer:\n{reply}\n\n"
                            f"Check whether every factual claim is supported "
                            f"by the retrieved passages. Rewrite the answer "
                            f"so that every claim is grounded, or explicitly "
                            f"say which parts cannot be verified.\n\n"
                            f"Original question: {rewritten}"
                        )
                        revised = _call_llm_with_deadline(
                            query=revise_query,
                            passages=hits,
                            conversation_history=conversation_history or None,
                            locale=locale,
                            personalization_context=(personalization or {}).get(
                                "prompt_context", ""
                            ),
                            tone_hint=tone_hint,
                        )
                    if revised:
                        reply = self._output_guard.sanitize(self._output_guard.redact_pii(revised))
                        leakage = self._output_guard.check_prompt_leakage(reply)
                        reply = leakage.sanitized_text
                        faith = HybridRetriever.compute_faithfulness(reply, contexts)
                        faithfulness_score = faith
                        reflect_fired = True

                with trace_stage("grounding", timings=timings):
                    grounding = self._output_guard.check_grounding(
                        reply, contexts, GROUNDING_THRESHOLD
                    )
                    reply = grounding.sanitized_text
                    trace_ctx["faithfulness"] = faith
                    if reflect_fired:
                        trace_ctx["self_reflected"] = True

            # 7c. Deterministic verification of money answers.
            #
            #     Self-reflection above is a model grading its own output
            #     against a faithfulness score; it catches ungrounded
            #     prose and misses arithmetic. Whether a figure is right
            #     is not a judgement call, so this re-derives it through
            #     the same calculator the agent would have used and
            #     compares it against what the reply actually printed.
            #     No model, no tokens — and it can reject on its own.
            if flags.is_enabled("evaluator_optimizer") and reply:
                with trace_stage("numeric_verification", timings=timings):
                    verdict = evaluate(
                        rewritten,
                        reply,
                        call_tool=self._recompute_for_verification,
                        faithfulness=faithfulness_score,
                    )
                trace_ctx["numeric_verification"] = {
                    "accepted": verdict.accepted,
                    "failures": verdict.failures(),
                    "unverified": list(verdict.unverified),
                }
                if not verdict.numerically_consistent:
                    # A figure that disagrees with its own calculator is
                    # the one error a taxpayer will act on.
                    logger.warning(
                        "numeric verification rejected the reply: %s",
                        verdict.detail.get("money", {}).get("reason", ""),
                    )
                    metrics.inc("numeric_verification_rejected_total")

                    # 7d. One bounded revision.
                    #
                    #     The evaluator knows the right figure — it just
                    #     recomputed it — so the revision is told what to
                    #     state rather than asked to think again. That is
                    #     the difference between an optimizer and a retry:
                    #     a critique the reviser has to interpret is a
                    #     second chance to get it wrong.
                    #
                    #     Budgeted at one, on money-bearing turns only.
                    #     Unbounded critique-revise is a cost incident
                    #     with a quality story attached.
                    budget = RevisionBudget()
                    allowed, why = budget.may_revise(
                        carries_money=True, escalation_bound=False
                    )
                    if allowed and self._llm_available:
                        with trace_stage("numeric_revision", timings=timings):
                            revised = _call_llm_with_deadline(
                                query=(
                                    f"{verdict.revision_note}\n\n"
                                    f"Rewrite the answer below so it states that "
                                    f"figure, keeping its citations and its "
                                    f"structure. Change nothing else.\n\n"
                                    f"Answer:\n{reply}\n\n"
                                    f"Question: {rewritten}"
                                ),
                                passages=hits,
                                conversation_history=conversation_history or None,
                                locale=locale,
                                personalization_context=(personalization or {}).get(
                                    "prompt_context", ""
                                ),
                                tone_hint=tone_hint,
                            )
                            budget.spend()
                        if revised:
                            candidate = self._output_guard.sanitize(
                                self._output_guard.redact_pii(revised)
                            )
                            candidate = self._output_guard.check_prompt_leakage(
                                candidate
                            ).sanitized_text
                            # Re-verify. A revision that did not fix the
                            # figure must not be published just because it
                            # is newer — the budget is spent either way,
                            # so the only question is which text is right.
                            recheck = evaluate(
                                rewritten,
                                candidate,
                                call_tool=self._recompute_for_verification,
                                faithfulness=faithfulness_score,
                            )
                            trace_ctx["numeric_revision"] = {
                                "attempted": True,
                                "fixed": recheck.numerically_consistent,
                            }
                            if recheck.numerically_consistent:
                                reply = candidate
                                verdict = recheck
                                trace_ctx["numeric_verification"] = {
                                    "accepted": recheck.accepted,
                                    "failures": recheck.failures(),
                                    "unverified": list(recheck.unverified),
                                }
                                metrics.inc("numeric_revision_fixed_total")
                            else:
                                metrics.inc("numeric_revision_failed_total")
                    else:
                        trace_ctx["numeric_revision"] = {
                            "attempted": False,
                            "reason": why if not allowed else "llm unavailable",
                        }

            # 8. Escalation check
            escalate, esc_reason = self._output_guard.should_escalate(faithfulness_score, hits)
            if flags.is_enabled("evaluator_optimizer") and not escalate:
                escalate, esc_reason = self._escalate_on_numeric_mismatch(
                    trace_ctx.get("numeric_verification"), escalate, esc_reason
                )
            claim_report = None
            # An extractive fallback is copied from the selected cited FAQ
            # answer, so lexical claim verification would incorrectly label
            # every sentence "uncited" even though it is not LLM-synthesized.
            # It already carries [1] and is scored against the source passage.
            if hits and citations and reply and not extractive_fallback:
                with trace_stage("claim_verification", timings=timings):
                    claim_report = verify_claims(reply, citations, hits)
                    trace_ctx["claim_verification"] = {
                        "decision": claim_report.get("decision"),
                        "score": claim_report.get("score"),
                        "claim_count": claim_report.get("claim_count"),
                        "unsupported_count": len(claim_report.get("unsupported_claims") or []),
                        "uncited_count": len(claim_report.get("uncited_claims") or []),
                    }
            response_judge = self._evaluate_response_judge(
                message=message,
                reply=reply,
                hits=hits,
                citations=citations,
                faithfulness_score=faithfulness_score,
                escalation_required=escalate,
                escalation_reason=esc_reason,
                claim_report=claim_report,
            )
            if claim_report is not None:
                response_judge["claim_verification"] = claim_report
            if response_judge["decision"] == "revise" and response_judge.get("revised_reply"):
                reply = self._output_guard.sanitize(
                    self._output_guard.redact_pii(response_judge["revised_reply"])
                )
                if contexts:
                    faithfulness_score = HybridRetriever.compute_faithfulness(reply, contexts)
                escalate, esc_reason = self._output_guard.should_escalate(faithfulness_score, hits)
                response_judge["applied_revision"] = True
                response_judge["final_decision"] = "escalate" if escalate else "approve"
                if faithfulness_score is not None:
                    response_judge["confidence_band"] = (
                        "high" if faithfulness_score >= 0.65 else "medium"
                    )
                if citations:
                    claim_report = verify_claims(reply, citations, hits)
                    response_judge["claim_verification"] = claim_report
                    if claim_report.get("decision") == "escalate":
                        response_judge["final_decision"] = "escalate"
                        response_judge.setdefault("reasons", []).append(
                            "claim verification still found unsupported factual claims"
                        )
            else:
                response_judge["final_decision"] = response_judge["decision"]

            if response_judge["final_decision"] == "escalate" and not esc_reason:
                esc_reason = "; ".join(response_judge.get("reasons") or []) or (
                    "response_judge requested human review"
                )
            if response_judge["final_decision"] == "escalate":
                escalate = True
            response_judge.pop("revised_reply", None)

            handoff = None
            if flags.is_enabled("handoff_summaries") and escalate:
                handoff = self._build_handoff_packet(
                    message=message,
                    reason=esc_reason,
                    conversation_history=conversation_history or None,
                    hits=hits,
                    faithfulness_score=faithfulness_score,
                )
            ticket_id = self._maybe_create_ticket(
                reason=esc_reason,
                user_query=message,
                bot_reply=reply,
                session_id=session_id,
                conversation_id=thread_id,
                priority=(handoff or {}).get("priority", "normal"),
                handoff=handoff,
                response_judge=response_judge,
                user_id=user_id or "",
            )
            if ticket_id:
                trace_ctx["ticket_id"] = ticket_id

            trace_ctx["num_sources"] = len(sources)
            trace_ctx["locale"] = locale

            # Record real token usage (gen_ai.usage.input_tokens /
            # gen_ai.usage.output_tokens).  Uses the loaded Qwen tokenizer
            # via llm_module.count_tokens; falls back to word count when
            # the tokenizer is not available (LLM disabled).
            input_tokens = llm_module.count_tokens(message)
            output_tokens = llm_module.count_tokens(reply)
            trace_ctx["input_tokens"] = input_tokens
            trace_ctx["output_tokens"] = output_tokens
            record_token_usage(input_tokens, output_tokens)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "generate: mode=%s hits=%d faith=%.2f ms=%.1f locale=%s escalate=%s stages=%s",
            retrieval_mode,
            len(hits),
            faithfulness_score or 0,
            elapsed_ms,
            locale,
            escalate,
            trace_ctx.get("timings", {}),
        )

        result = {
            "reply": reply,
            "sources": sources,
            "citations": citations,
            "faithfulness_score": faithfulness_score,
            "retrieval_mode": retrieval_mode,
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": escalate,
            "escalation_reason": esc_reason,
            "agent_role": agent_role,
            "handoff": handoff,
            "response_judge": response_judge,
            "next_actions": self._default_next_actions(
                agent_role=agent_role,
                handoff=handoff,
                escalation_required=escalate,
            ),
            "ticket_id": ticket_id,
        }
        result = self._finalize_result(result)
        reply = result["reply"]

        # Store in semantic cache (Phase 5) — a copy, so the empathy prefix
        # below never reaches a later (possibly calm) user via a cache hit.
        if cache_allowed and retrieval_mode not in ("blocked", "abstained"):
            self._cache.put(rewritten, dict(result))

        # EI parity for the extractive fallback: with every LLM tier down the
        # tone_hint never reached a model, so carry the acknowledgment here —
        # after scoring and caching, same as the deterministic branch above.
        if distress and extractive_fallback and reply:
            reply = f"{empathy_ack(distress)}\n\n{reply}"
            result["reply"] = reply

        self._persist_personalization_turn(
            user_id=user_id,
            conversation_id=thread_id,
            message=message,
            reply=reply,
            agent_role=agent_role,
            personalization=personalization,
        )

        # Phase 21 — audit ledger append (happy path).
        self._audit_turn(
            message=message,
            result=result,
            session_id=session_id,
            trace_ctx=trace_ctx,
        )

        return result

    # -- Audit helper (Phase 21) -------------------------------------
    def _audit_turn(
        self,
        *,
        message: str,
        result: dict[str, Any],
        session_id: str | None,
        trace_ctx: dict[str, Any] | None = None,
    ) -> None:
        """Append an immutable audit event for this turn.

        Called from every return site in :py:meth:`generate` so
        blocked / clarification / escalated / abstained / happy-path
        outcomes all end up in the ledger.  Payload excludes raw
        query/reply content (we store SHA-256 hashes) so the
        audit chain is useful for regulatory replay without becoming
        a second PII store.  Gated on ``FLAG_AUDIT_LEDGER``.

        Failures are swallowed — a broken audit DB must never
        block a user response.
        """
        if not flags.is_enabled("audit_ledger"):
            return
        try:
            import hashlib as _hashlib

            from .audit import get_ledger

            trace_ctx = trace_ctx or {}
            reply = result.get("reply", "") or ""
            payload = {
                "query_sha256": _hashlib.sha256((message or "").encode("utf-8")).hexdigest(),
                "reply_sha256": _hashlib.sha256(reply.encode("utf-8")).hexdigest(),
                "retrieval_mode": result.get("retrieval_mode", ""),
                "num_sources": len(result.get("sources", [])),
                "num_citations": len(result.get("citations", [])),
                "faithfulness_score": result.get("faithfulness_score"),
                "escalation_required": bool(result.get("escalation_required")),
                "escalation_reason": result.get("escalation_reason", ""),
                "model": result.get("model", self.name),
                "locale": result.get("locale", "en"),
                "conversation_id": result.get("conversation_id") or "",
                "input_tokens": llm_module.count_tokens(message),
                "output_tokens": llm_module.count_tokens(reply),
                "tool_calls": trace_ctx.get("tool_calls", []),
                "tool_iterations": trace_ctx.get("tool_iterations", 0),
                "agent_route": trace_ctx.get("agent_route", ""),
                "ticket_id": result.get("ticket_id", ""),
            }
            audit_tenant_id = str(trace_ctx.get("tenant_id") or "default")[:128]
            audit_user_id = str(trace_ctx.get("user_id") or session_id or "")[:128]
            get_ledger().append(
                event_type="generate",
                payload=payload,
                tenant_id=audit_tenant_id,
                user_id=audit_user_id,
            )
        except Exception:
            logger.debug("audit ledger append failed", exc_info=True)

    def generate_retrieval_only(
        self,
        message: str,
        conversation_id: str | None = None,
        top_k: int = 6,
        locale: str = "en",
        session_id: str | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        conversation_history_override: list[dict[str, str]] | None = None,
        attachments: list[documents_module.DocumentRecord] | None = None,
    ) -> dict[str, Any]:
        """Run retrieval + guardrails but skip LLM generation (for SSE streaming).

        Returns the same dict as ``generate()`` but with ``_hits`` and
        ``_history`` included so the streaming endpoint can pass them to
        the LLM stream.

        Passing ``conversation_history_override`` (Phase 3 of the WS
        upgrade) skips the SQLite history fetch — callers with their own
        in-memory cache (e.g. a long-lived WebSocket session) avoid one
        DB round trip per turn.
        """
        thread_id = conversation_id or str(uuid.uuid4())
        agent_role = "rag_answerer"

        # Multi-turn memory (Phase 4 -> overridable in Phase 29)
        conversation_history: list[dict[str, str]] = []
        if conversation_history_override is not None:
            conversation_history = list(conversation_history_override)
        else:
            history_session_id = None if conversation_id else session_id
            if conversation_id or history_session_id:
                try:
                    conversation_history = db.get_recent_turns(
                        session_id=history_session_id,
                        conversation_id=conversation_id,
                        limit=5,
                    )
                except Exception:
                    logger.debug("Failed to fetch conversation history", exc_info=True)

        # Query rewriting (Phase 4)
        rewritten = rewrite_query(message, history=conversation_history or None)

        # Language detection — auto-detect for adapter routing
        if locale == "en":
            detected_locale = detect_language(message)
            if detected_locale != "en":
                locale = detected_locale
                logger.info("Auto-detected locale: %s (streaming)", locale)

        personalization = self._load_personalization_state(user_id)
        # Attachment turns are never cache-served or cache-stored: the answer
        # is specific to the attached document, not the query text alone.
        cache_allowed = personalization is None and not attachments

        # Emotional-intelligence signal (parity with generate()): tone hint
        # for the LLM stream, empathy prefix for deterministic short-circuits.
        distress = detect_user_distress(message)
        tone_hint = tone_hint_for(distress)

        # See generate()'s matching comment: retrieve on the question span
        # alone when distress framing is present, so it doesn't dilute
        # relevance into a false abstention.
        retrieval_query = rewritten
        binding_query = message
        if distress:
            question_span = extract_question_span(rewritten)
            if question_span:
                retrieval_query = question_span
            message_question_span = extract_question_span(message)
            if message_question_span:
                binding_query = message_question_span

        # Input guardrails (OWASP LLM01)
        guard = self._input_guard.check(message)
        if not guard.allowed:
            return {
                "reply": guard.reason,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "blocked",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "safety_guard",
                "next_actions": ["Rephrase your question about a URA service — I'm glad to help."],
                "_hits": [],
                "_history": [],
            }

        workflow_result = self._maybe_handle_workflow(
            message=message,
            rewritten=rewritten,
            thread_id=thread_id,
            locale=locale,
            personalization=personalization,
        )
        if workflow_result:
            if distress and workflow_result.get("reply"):
                workflow_result["reply"] = (
                    f"{empathy_ack(distress)}\n\n{workflow_result['reply']}"
                )
            return {
                **workflow_result,
                "_hits": [],
                "_history": conversation_history,
                "_rewritten": rewritten,
                "_personalization_context": (personalization or {}).get("prompt_context", ""),
            }

        # Deterministic tax calculator (parity with generate()) — instant
        # answer or guided elicitation, both as a single bundled payload.
        calc_result = self._maybe_handle_fast_paths(
            message=message,
            rewritten=rewritten,
            thread_id=thread_id,
            locale=locale,
        )
        if calc_result:
            if distress and calc_result.get("reply"):
                calc_result["reply"] = f"{empathy_ack(distress)}\n\n{calc_result['reply']}"
            return {
                **calc_result,
                "_hits": [],
                "_history": conversation_history,
                "_rewritten": rewritten,
                "_personalization_context": (personalization or {}).get("prompt_context", ""),
                "_short_circuit": True,
            }

        # Greeting detection — always active (streaming path)
        _q_lower_s = message.strip().lower().strip("!.?,")
        _q_words_s = message.strip().split()
        if len(_q_words_s) <= 3 and (
            _q_lower_s in _GREETING_WORDS
            or _q_lower_s in _GREETING_PHRASES
            or all(w.lower().strip("!.?,") in _GREETING_WORDS for w in _q_words_s)
        ):
            return {
                "reply": GREETING_REPLY,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "greeting",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "greeting_agent",
                "next_actions": [
                    "Ask about TIN registration",
                    "Learn about VAT",
                    "File a tax return",
                ],
                "_hits": [],
                "_history": [],
            }

        # Gratitude / farewell — parity with the REST path.
        closing_reply = _closing_courtesy_reply(message)
        if closing_reply:
            return {
                "reply": closing_reply,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "greeting",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "greeting_agent",
                "next_actions": [
                    "Ask about TIN registration",
                    "Learn about VAT",
                    "File a tax return",
                ],
                "_hits": [],
                "_history": [],
            }

        # Semantic cache check (Phase 5)
        if cache_allowed:
            cached = self._cache.get(rewritten, locale=locale)
            if cached:
                return self._finalize_result({
                    **cached,
                    "conversation_id": thread_id,
                    "locale": locale,
                })

        route_decision = None
        if flags.is_enabled("agentic_mode"):
            route_decision = supervisor.classify(
                rewritten,
                has_conversation_history=bool(conversation_history),
                locale=locale,
            )
            if route_decision.route == AgentRoute.CLARIFY:
                return {
                    "reply": route_decision.clarification_question
                    or CLARIFICATION_PROMPT,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "clarification",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": False,
                    "escalation_reason": "",
                    "agent_role": "clarification_agent",
                    "next_actions": self._default_next_actions(
                        agent_role="clarification_agent",
                    ),
                    "_hits": [],
                    "_history": [],
                }
            if route_decision.route == AgentRoute.ESCALATE:
                ticket_id = ""
                handoff = None
                response_judge = {
                    "decision": "escalate",
                    "final_decision": "escalate",
                    "applied_revision": False,
                    "reasons": [route_decision.reason] if route_decision.reason else [],
                    "confidence_band": "low",
                }
                if flags.is_enabled("handoff_summaries"):
                    handoff = self._build_handoff_packet(
                        message=message,
                        reason=route_decision.reason,
                        conversation_history=conversation_history or None,
                    )
                reply = ESCALATION_REPLY_LEAD + ESCALATION_REPLY_FOOTER
                ticket_id = self._maybe_create_ticket(
                    reason=route_decision.reason,
                    user_query=message,
                    bot_reply=reply,
                    session_id=session_id,
                    conversation_id=thread_id,
                    priority=(handoff or {}).get("priority", "normal"),
                    handoff=handoff,
                    response_judge=response_judge,
                    user_id=user_id or "",
                )
                return {
                    "reply": reply,
                    "sources": [],
                    "citations": [],
                    "faithfulness_score": None,
                    "retrieval_mode": "escalated",
                    "model": self.name,
                    "conversation_id": thread_id,
                    "locale": locale,
                    "escalation_required": True,
                    "escalation_reason": route_decision.reason,
                    "agent_role": "escalation_triage",
                    "handoff": handoff,
                    "response_judge": response_judge,
                    "next_actions": self._default_next_actions(
                        agent_role="escalation_triage",
                        handoff=handoff,
                        escalation_required=True,
                    ),
                    "ticket_id": ticket_id,
                    "_hits": [],
                    "_history": [],
                    "_personalization_context": (personalization or {}).get("prompt_context", ""),
                }
            if route_decision.route == AgentRoute.TOOLS:
                agent_role = "tool_specialist"
            elif route_decision.route == AgentRoute.TAX_SPECIALIST:
                agent_role = "tax_specialist"
            elif route_decision.route == AgentRoute.CUSTOMS_SPECIALIST:
                agent_role = "customs_specialist"

        hits: list[dict[str, Any]] = []
        retrieval_mode = "keyword"

        if not self._retriever_ready and not self._retriever._ready:
            self._retriever_ready = self._retriever.initialize()

        if self._retriever_ready:
            hits = self._retriever.search(retrieval_query, top_k=top_k)
            if hits:
                retrieval_mode = "hybrid"
            self._retriever_ready = self._retriever._ready

        # Mirror the REST path's keyword fallback — _faq_hits_to_retrieval_hits
        # carries _overlap into score_rrf so the abstention guard sees a real
        # signal (previously hardcoded 0.0 here, same bug as line 2167).
        if not hits:
            kw_hits = _simple_search(
                retrieval_query,
                self._faq_index,
                top_k=top_k,
                binding_query=binding_query,
                locale=locale,
            )
            hits = _faq_hits_to_retrieval_hits(kw_hits)

        # Corrective RAG (Phase 6)
        if hits and self._retriever_ready:
            hits, was_corrected = corrective_retrieve(retrieval_query, self._retriever, hits, top_k=top_k)
            if was_corrected:
                retrieval_mode = "hybrid_corrected"

        # Language-aware retrieval boosting (streaming path)
        if locale != "en" and hits:
            locale_keywords = {
                "lg": {"luganda", "oluganda", "lg"},
                "sw": {"swahili", "kiswahili", "sw"},
                "nyn": {"runyankole", "nkore", "nyn"},
                "ach": {"acholi", "ach"},
            }
            boost_terms = locale_keywords.get(locale, set())
            if boost_terms:
                for h in hits:
                    source = (h.get("source") or "").lower()
                    text_preview = (h.get("text") or "")[:200].lower()
                    if any(t in source or t in text_preview for t in boost_terms):
                        h["score_rrf"] = h.get("score_rrf", 0.5) + 0.3
                hits.sort(key=lambda x: x.get("score_rrf", 0), reverse=True)

        # Blend top FAQ keyword hits after corrective RAG (parity with the
        # REST path, including the priority FAQ hits the deterministic
        # procedural fast path depends on).
        kw_hits = _simple_search(
            retrieval_query,
            self._faq_index,
            top_k=2,
            binding_query=binding_query,
            locale=locale,
        )
        # Graph claims first — same reasoning as the SSE path above.
        graph_hits = self._graph_hits(retrieval_query)
        if graph_hits:
            hits = graph_hits + hits
            retrieval_mode = "graph"
        priority_hits = self._priority_faq_hits(retrieval_query, top_k=2)
        seen_texts = {h.get("text", "")[:80] for h in hits}
        for h in priority_hits:
            if h.get("text", "")[:80] not in seen_texts:
                hits.insert(0, h)
                seen_texts.add(h.get("text", "")[:80])
                retrieval_mode = "faq_priority"
        for h in kw_hits:
            faq_text = f"Question: {h['question']}\nAnswer: {h['answer']}"
            if faq_text[:80] not in seen_texts:
                hits.append({
                    "text": faq_text, "answer": h["answer"],
                    "question": h["question"], "source": h["source"],
                    "chunk_id": "", "page": "", "section": h.get("tag", ""),
                    "doc_type": "csv", "score_rrf": 0.5,
                })
                seen_texts.add(faq_text[:80])

        # Apply the same FAQ intent binding as the REST path before streaming
        # can expose a passage to the model.
        hits = _filter_unbound_faq_hits(binding_query, hits)

        # Attached documents — prepend as top-priority grounding hits
        # (parity with generate(); see the comment there).
        if attachments:
            hits = documents_module.attachment_passages(attachments) + hits

        # Clarification check (Phase 6) — skipped for attachment turns
        clarification = None if attachments else needs_clarification(message, hits)
        if clarification:
            return {
                "reply": clarification,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "clarification",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": False,
                "escalation_reason": "",
                "agent_role": "clarification_agent",
                "next_actions": self._default_next_actions(
                    agent_role="clarification_agent",
                ),
                "_hits": [],
                "_history": [],
            }

        # Deterministic procedural fast path — parity with the REST path so
        # curated KB answers stream with their real (high) faithfulness
        # instead of being re-synthesised and re-scored via the LLM.
        # Attachment turns always go to the LLM (templates can't read docs).
        if hits and not attachments:
            deterministic_sources = list({h.get("source", "") for h in hits if h.get("source")})
            deterministic_citations = HybridRetriever.build_citations(hits)
            deterministic_reply, deterministic_curated = self._deterministic_procedure_reply(
                rewritten, hits, deterministic_citations
            )
            if deterministic_reply:
                result = self._deterministic_result(
                    reply=self._finalize_reply(deterministic_reply),
                    curated=deterministic_curated,
                    hits=hits,
                    sources=deterministic_sources,
                    citations=deterministic_citations,
                    retrieval_mode=retrieval_mode,
                    thread_id=thread_id,
                    locale=locale,
                    agent_role=agent_role,
                )
                if cache_allowed:
                    self._cache.put(rewritten, dict(result))
                if distress:
                    result["reply"] = f"{empathy_ack(distress)}\n\n{result['reply']}"
                return {
                    **result,
                    "_hits": hits,
                    "_history": conversation_history,
                    "_rewritten": rewritten,
                    "_short_circuit": True,
                }

        if not attachments and self._output_guard.should_abstain(hits):
            reply = ABSTENTION_REPLY
            if distress:
                reply = f"{empathy_ack(distress)}\n\n{reply}"
            escalate, esc_reason = self._output_guard.should_escalate(None, hits)
            handoff = None
            response_judge = {
                "decision": "escalate" if escalate else "approve",
                "final_decision": "escalate" if escalate else "approve",
                "applied_revision": False,
                "reasons": [esc_reason] if esc_reason else [],
                "confidence_band": "low",
            }
            if flags.is_enabled("handoff_summaries") and escalate:
                handoff = self._build_handoff_packet(
                    message=message,
                    reason=esc_reason,
                    conversation_history=conversation_history or None,
                    hits=hits,
                )
            ticket_id = self._maybe_create_ticket(
                reason=esc_reason,
                user_query=message,
                bot_reply=reply,
                session_id=session_id,
                conversation_id=thread_id,
                priority=(handoff or {}).get("priority", "normal"),
                handoff=handoff,
                response_judge=response_judge,
                user_id=user_id or "",
            )
            return {
                "reply": reply,
                "sources": [],
                "citations": [],
                "faithfulness_score": None,
                "retrieval_mode": "abstained",
                "model": self.name,
                "conversation_id": thread_id,
                "locale": locale,
                "escalation_required": escalate,
                "escalation_reason": esc_reason,
                "agent_role": agent_role,
                "handoff": handoff,
                "response_judge": response_judge,
                "next_actions": self._default_next_actions(
                    agent_role=agent_role,
                    handoff=handoff,
                    escalation_required=escalate,
                ),
                "ticket_id": ticket_id,
                "_hits": [],
                "_history": [],
                "_personalization_context": (personalization or {}).get("prompt_context", ""),
            }

        sources = list({h.get("source", "") for h in hits if h.get("source")})
        citations = HybridRetriever.build_citations(hits)
        best = hits[0] if hits else {}
        reply = best.get("answer") or best.get("text", "")
        if citations and not re.search(r"\[\d{1,3}\]", reply):
            reply = f"{reply.rstrip()} [1]"

        # Escalation check (same as sync path)
        escalate, esc_reason = self._output_guard.should_escalate(None, hits)
        response_judge = self._evaluate_response_judge(
            message=message,
            reply=reply,
            hits=hits,
            citations=citations,
            faithfulness_score=None,
            escalation_required=escalate,
            escalation_reason=esc_reason,
        )
        if response_judge["decision"] == "revise" and response_judge.get("revised_reply"):
            reply = response_judge["revised_reply"]
            response_judge["applied_revision"] = True
            response_judge["final_decision"] = "approve"
        else:
            response_judge["final_decision"] = response_judge["decision"]
        if response_judge["final_decision"] == "escalate":
            escalate = True
            if not esc_reason:
                esc_reason = "; ".join(response_judge.get("reasons") or [])
        response_judge.pop("revised_reply", None)
        handoff = None
        if flags.is_enabled("handoff_summaries") and escalate:
            handoff = self._build_handoff_packet(
                message=message,
                reason=esc_reason,
                conversation_history=conversation_history or None,
                hits=hits,
            )
        ticket_id = self._maybe_create_ticket(
            reason=esc_reason,
            user_query=message,
            bot_reply=reply,
            session_id=session_id,
            conversation_id=thread_id,
            priority=(handoff or {}).get("priority", "normal"),
            handoff=handoff,
            response_judge=response_judge,
            user_id=user_id or "",
        )

        return {
            "reply": reply,
            "sources": sources,
            "citations": citations,
            "faithfulness_score": None,
            "retrieval_mode": retrieval_mode,
            "model": self.name,
            "conversation_id": thread_id,
            "locale": locale,
            "escalation_required": escalate,
            "escalation_reason": esc_reason,
            "agent_role": agent_role,
            "handoff": handoff,
            "response_judge": response_judge,
            "next_actions": self._default_next_actions(
                agent_role=agent_role,
                handoff=handoff,
                escalation_required=escalate,
            ),
            "ticket_id": ticket_id,
            "_hits": hits,
            "_history": conversation_history,
            "_rewritten": rewritten,
            "_personalization_context": (personalization or {}).get("prompt_context", ""),
            "_tone_hint": tone_hint,
            "_distress": distress,
        }

    @staticmethod
    def redact_for_storage(text: str) -> str:
        """Redact PII before database persistence (privacy-by-design)."""
        if STORE_RAW_PROMPTS:
            return text
        return redact_pii_text(text)

    @staticmethod
    def contexts_json(result: dict[str, Any] | None, limit: int = 8) -> str:
        """Serialise the top-k retrieved passage texts for this turn (P0-2).

        Persisted alongside the conversation so the eval harness can score
        faithfulness against the ACTUAL retrieved context instead of the
        answer itself. Returns ``"[]"`` when no hits are available. Passages
        are knowledge-base text (not user PII), so they are stored verbatim —
        EXCEPT attachment passages, which carry user document content and are
        replaced by a placeholder (analytics.db must never hold them).
        """
        hits = (result or {}).get("_hits") or []
        texts = []
        for h in hits[:limit]:
            if h.get("doc_type") == "attachment":
                texts.append(f"[user attachment: {h.get('source') or 'attached:document'}]")
            else:
                texts.append(str(h.get("text") or h.get("answer") or "").strip())
        try:
            return json.dumps([t for t in texts if t])
        except Exception:
            return "[]"

    # -- Classification -----------------------------------------------------
    def classify(self, text: str, top_k: int = 1) -> dict[str, Any]:
        """Classify *text* against known FAQ tags by keyword overlap."""
        t0 = time.perf_counter()
        query_tokens = set(text.lower().split())
        tag_scores: dict[str, float] = {}

        for tag, entries in self._faq_index.items():
            score = 0.0
            for entry in entries:
                tokens = set(entry["question"].lower().split())
                score += len(query_tokens & tokens)
            if score > 0:
                tag_scores[tag] = score

        total = sum(tag_scores.values()) or 1.0
        ranked = sorted(tag_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        predictions = [
            {
                "tag": tag,
                "confidence": round(score / total, 4),
                "label": self._tag_labels.get(tag, tag.replace("_", " ").title()),
            }
            for tag, score in ranked
        ]

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"predictions": predictions, "processing_time_ms": round(elapsed_ms, 2)}

    def classify_batch(self, texts: list[str]) -> dict[str, Any]:
        """Classify multiple texts in one call."""
        t0 = time.perf_counter()
        results = []
        for text in texts:
            single = self.classify(text, top_k=1)
            if single["predictions"]:
                p = single["predictions"][0]
                results.append({"text": text, "tag": p["tag"], "confidence": p["confidence"]})
            else:
                results.append({"text": text, "tag": "unknown", "confidence": 0.0})

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"results": results, "processing_time_ms": round(elapsed_ms, 2)}

    # -- Tags & FAQ ---------------------------------------------------------
    def list_tags(self) -> dict[str, Any]:
        """Return all known FAQ tags."""
        tags = [
            {
                "id": tag,
                "name": self._tag_labels.get(tag, tag.replace("_", " ").title()),
                "description": f"Questions about {self._tag_labels.get(tag, tag).lower()}",
            }
            for tag in sorted(self._faq_index)
        ]
        return {"tags": tags, "total": len(tags)}

    def get_faq(self, tag: str) -> dict[str, Any] | None:
        """Return FAQ entries for a specific *tag*."""
        entries = self._faq_index.get(tag)
        if entries is None:
            return None
        return {"tag": tag, "faqs": entries, "total": len(entries)}
