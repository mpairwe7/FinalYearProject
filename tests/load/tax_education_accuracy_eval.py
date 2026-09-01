"""Tax Education Conversation Accuracy, Intent, and Load Evaluation Suite (2026).

Evaluates the URA Chatbot's tax education capabilities across:
  1. Intent Determination & Routing Precision (Calculator / Workflow / RAG / FAQ / Escalation)
  2. Statutory & Numerical Accuracy (Exact tax rates, thresholds, formulas, statutory citations)
  3. Multilingual Grounding & Response Coherence (English, Luganda, Kiswahili)
  4. Multi-Turn Taxpayer Journey Coherence (Context preservation across educational turns)
  5. Throughput & Latency under Concurrency (Local GPU Stack vs Public ngrok Gateway)

Targets:
  - Local API: http://localhost:8083 (or direct container)
  - Local Frontend: http://localhost:3032/api
  - Public ngrok Gateway: https://struttingly-nongeological-briella.ngrok-free.dev/api

Run:
    python3 tests/load/tax_education_accuracy_eval.py --target local
    python3 tests/load/tax_education_accuracy_eval.py --target ngrok --concurrency 4 --out results/tax_accuracy_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Target Endpoints
# ---------------------------------------------------------------------------
ENDPOINTS = {
    "local_api": "http://localhost:8083",
    "local_frontend": "http://localhost:3032/api",
    "ngrok": "https://struttingly-nongeological-briella.ngrok-free.dev/api",
}

DEFAULT_TARGET = os.environ.get("TAX_EVAL_TARGET", "http://localhost:3032/api")

# ---------------------------------------------------------------------------
# Ground Truth Knowledge Base for Accuracy Scoring
# ---------------------------------------------------------------------------
@dataclass
class TaxGroundTruth:
    query_id: str
    topic: str
    locale: str
    user_query: str
    expected_intent: str  # "calculator", "workflow", "hybrid_rag", "faq"
    required_keywords: list[str]
    required_numerical_values: list[str]
    statutory_citations: list[str]
    forbidden_terms: list[str] = field(default_factory=list)


GROUND_TRUTH_BANK: list[TaxGroundTruth] = [
    # ---- VAT Topic ----
    TaxGroundTruth(
        query_id="vat_standard_rate_en",
        topic="vat",
        locale="en",
        user_query="What is the standard Value Added Tax rate in Uganda?",
        expected_intent="hybrid_rag",
        required_keywords=["value added tax", "vat", "rate"],
        required_numerical_values=["18%", "18 percent"],
        statutory_citations=["Value Added Tax Act", "VAT Act"],
    ),
    TaxGroundTruth(
        query_id="vat_registration_threshold_en",
        topic="vat",
        locale="en",
        user_query="What is the annual turnover threshold for mandatory VAT registration in Uganda?",
        expected_intent="hybrid_rag",
        required_keywords=["threshold", "registration", "turnover", "mandatory"],
        required_numerical_values=["150,000,000", "150 million", "150m"],
        statutory_citations=["VAT Act", "Section 7"],
    ),
    TaxGroundTruth(
        query_id="vat_standard_rate_lg",
        topic="vat",
        locale="lg",
        user_query="Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?",
        expected_intent="hybrid_rag",
        required_keywords=["vat", "bitundu", "omusolo"],
        required_numerical_values=["18", "kkumi na munaana"],
        statutory_citations=[],
    ),
    TaxGroundTruth(
        query_id="vat_standard_rate_sw",
        topic="vat",
        locale="sw",
        user_query="Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?",
        expected_intent="hybrid_rag",
        required_keywords=["kodi", "vat", "asilimia"],
        required_numerical_values=["18", "kumi na nane"],
        statutory_citations=[],
    ),
    # ---- PAYE Topic ----
    TaxGroundTruth(
        query_id="paye_calculation_en",
        topic="paye",
        locale="en",
        user_query="Calculate PAYE for a monthly salary of 3,500,000 UGX.",
        expected_intent="calculator",
        required_keywords=["paye", "gross", "taxable"],
        required_numerical_values=["30%", "235,000", "410,000"],
        statutory_citations=["Income Tax Act"],
    ),
    TaxGroundTruth(
        query_id="paye_threshold_en",
        topic="paye",
        locale="en",
        user_query="What is the monthly tax-free threshold for employment income (PAYE) in Uganda?",
        expected_intent="hybrid_rag",
        required_keywords=["threshold", "paye", "tax-free", "exempt"],
        required_numerical_values=["235,000"],
        statutory_citations=["Income Tax Act", "First Schedule"],
    ),
    # ---- Withholding Tax Topic ----
    TaxGroundTruth(
        query_id="wht_professional_fees_en",
        topic="withholding",
        locale="en",
        user_query="What is the withholding tax rate on management and professional fees in Uganda?",
        expected_intent="hybrid_rag",
        required_keywords=["withholding tax", "professional", "management"],
        required_numerical_values=["6%", "6 percent"],
        statutory_citations=["Income Tax Act", "Section 119"],
    ),
    # ---- EFRIS & Invoicing Topic ----
    TaxGroundTruth(
        query_id="efris_definition_en",
        topic="efris",
        locale="en",
        user_query="What is EFRIS and which taxpayers are required to use it?",
        expected_intent="hybrid_rag",
        required_keywords=["electronic fiscal receipting", "invoicing", "efris", "vat registered"],
        required_numerical_values=[],
        statutory_citations=["Tax Procedures Code Act"],
    ),
    TaxGroundTruth(
        query_id="efris_definition_lg",
        topic="efris",
        locale="lg",
        user_query="EFRIS kye ki era ani alina okugikozesa?",
        expected_intent="hybrid_rag",
        required_keywords=["efris", "lisiiti", "invooyisi", "bizinensi"],
        required_numerical_values=[],
        statutory_citations=[],
    ),
    # ---- Presumptive Tax Topic ----
    TaxGroundTruth(
        query_id="presumptive_small_business_en",
        topic="presumptive",
        locale="en",
        user_query="What are the presumptive tax rules for a small business with annual gross turnover of 40 million UGX?",
        expected_intent="hybrid_rag",
        required_keywords=["presumptive", "small business", "turnover", "books of accounts"],
        required_numerical_values=["10,000,000", "50,000,000", "150,000,000"],
        statutory_citations=["Income Tax Act", "Second Schedule"],
    ),
    # ---- Property / Rental Tax Topic ----
    TaxGroundTruth(
        query_id="rental_tax_rate_individual_en",
        topic="property",
        locale="en",
        user_query="What is the rental income tax rate for individual landlords in Uganda?",
        expected_intent="hybrid_rag",
        required_keywords=["rental", "tax", "individual", "rate"],
        required_numerical_values=["12%", "2,820,000"],
        statutory_citations=["Income Tax Act", "Section 5"],
    ),
    # ---- TIN Registration Topic ----
    TaxGroundTruth(
        query_id="tin_registration_workflow_en",
        topic="tin",
        locale="en",
        user_query="I want to apply for a new individual TIN. What is the process and required documents?",
        expected_intent="workflow",
        required_keywords=["national id", "nin", "ura portal", "tin", "apply"],
        required_numerical_values=[],
        statutory_citations=[],
    ),
]

# ---------------------------------------------------------------------------
# Multi-Turn Educational Journey Definitions
# ---------------------------------------------------------------------------
MULTI_TURN_TEST_JOURNEYS = [
    {
        "journey_id": "journey_vat_onboarding",
        "description": "Taxpayer learning VAT requirements from zero to compliance",
        "turns": [
            {
                "turn": 1,
                "locale": "en",
                "message": "I am opening a hardware store in Jinja. Do I have to charge VAT?",
                "expected_keywords": ["vat", "turnover", "threshold", "register"],
                "expected_intent": "hybrid_rag",
            },
            {
                "turn": 2,
                "locale": "en",
                "message": "My projected sales are about 180 million UGX in my first year. Am I required to register?",
                "expected_keywords": ["mandatory", "150", "million", "required"],
                "expected_intent": "hybrid_rag",
            },
            {
                "turn": 3,
                "locale": "en",
                "message": "Once registered, by what date must I file my monthly return?",
                "expected_keywords": ["15th", "following month", "due date"],
                "expected_intent": "hybrid_rag",
            },
            {
                "turn": 4,
                "locale": "en",
                "message": "How does EFRIS integrate with my VAT invoicing?",
                "expected_keywords": ["e-invoice", "fiscal receipt", "efris", "real-time"],
                "expected_intent": "hybrid_rag",
            },
        ],
    },
    {
        "journey_id": "journey_employer_paye",
        "description": "Small company director learning employer tax obligations",
        "turns": [
            {
                "turn": 1,
                "locale": "en",
                "message": "I hired 3 staff with salaries of 1.5m, 2.5m, and 5.0m UGX. What are my PAYE duties?",
                "expected_keywords": ["deduct", "remit", "paye", "employer"],
                "expected_intent": "hybrid_rag",
            },
            {
                "turn": 2,
                "locale": "en",
                "message": "When is the monthly deadline to pay this deducted PAYE to URA?",
                "expected_keywords": ["15th", "month"],
                "expected_intent": "hybrid_rag",
            },
            {
                "turn": 3,
                "locale": "en",
                "message": "What is the penalty if I pay on the 20th instead of the 15th?",
                "expected_keywords": ["interest", "penalty", "late"],
                "expected_intent": "hybrid_rag",
            },
        ],
    },
]

# ---------------------------------------------------------------------------
# Evaluation Result Structures
# ---------------------------------------------------------------------------
@dataclass
class SingleQueryScore:
    query_id: str
    topic: str
    locale: str
    user_query: str
    status_code: int
    latency_s: float
    retrieval_mode: str
    intent_detected: str
    intent_correct: bool
    accuracy_score: float  # 0.0 to 1.0
    numerical_correct: bool
    keywords_matched: list[str]
    keywords_missing: list[str]
    citations_matched: list[str]
    language_match: bool
    response_text_snippet: str
    error: str | None = None


@dataclass
class JourneyScore:
    journey_id: str
    description: str
    total_turns: int
    successful_turns: int
    mean_latency_s: float
    intent_accuracy_pct: float
    topic_coherence_pct: float
    #: Mean share of each turn's expected facts that the reply actually carried.
    #: Graded companion to ``topic_coherence_pct``, which is only the proportion
    #: of turns clearing ``_COHERENCE_MIN_COVERAGE``.
    keyword_coverage_pct: float = 0.0
    turn_details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FullEvaluationReport:
    target_url: str
    timestamp: str
    concurrency: int
    total_single_queries: int
    single_query_accuracy_pct: float
    single_query_intent_precision_pct: float
    mean_latency_s: float
    p50_latency_s: float
    p95_latency_s: float
    p99_latency_s: float
    multilingual_fidelity_pct: dict[str, float]
    topic_accuracy_pct: dict[str, float]
    multi_turn_journeys_evaluated: int
    multi_turn_coherence_pct: float
    query_scores: list[SingleQueryScore] = field(default_factory=list)
    journey_scores: list[JourneyScore] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring Algorithms
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Matching primitives
#
# Every metric in this file used naked ``term in reply_text``. That made three
# of them unfalsifiable at once:
#
#   * "150" matched inside "1,500,000", so a salary figure satisfied a
#     threshold check.
#   * The Luganda marker "mu" matched "must" and "eri" matched "period", so an
#     English reply counted as Luganda. Worse, "ura" was a marker for *both*
#     Luganda and Kiswahili, and every reply names URA — so language fidelity
#     was true by construction.
#   * Journey coherence passed on one incidental keyword anywhere in the reply.
#
# Matching on token boundaries fixes the first two. The third needs the
# coverage rule below as well.
# ---------------------------------------------------------------------------
def _contains_term(haystack_lower: str, term: str) -> bool:
    """True when *term* appears in *haystack_lower* on token boundaries."""
    if not term:
        return False
    pattern = r"(?<![0-9a-z])" + re.escape(term.lower()) + r"(?![0-9a-z])"
    return re.search(pattern, haystack_lower) is not None


def _matched_terms(haystack_lower: str, terms: list[str]) -> list[str]:
    return [t for t in terms if _contains_term(haystack_lower, t)]


#: Replies that are not answers, whatever words they happen to contain. A
#: guided-workflow slot prompt opens with an empathy line ("I know deadlines
#: can be stressful") and an abstention names the topic it is refusing, so both
#: reliably matched topic keywords and scored as good answers. A measured PAYE
#: journey turn — "What is the penalty if I pay on the 20th instead of the
#: 15th?" — was counted coherent for the reply "Please give me one...".
_NON_ANSWER_MARKERS: tuple[str, ...] = (
    "i couldn't find a reliable answer",
    "i could not find a reliable answer",
    "couldn't find a reliable answer",
    "i don't have enough information",
    "i do not have enough information",
    "i just need a detail or two",
    "please give me one",
    "i'll need a little more",
    "could you tell me",
)

#: Retrieval modes that are never an answer to the question asked.
_NON_ANSWER_MODES: frozenset[str] = frozenset({"abstained", "error", "clarification"})


def _is_non_answer(resp_text: str, mode: str) -> bool:
    """Whether the reply declines, defers, or asks for more input."""
    if str(mode).lower() in _NON_ANSWER_MODES:
        return True
    low = resp_text.lower()
    return any(marker in low for marker in _NON_ANSWER_MARKERS)


#: A turn counts as on-topic when it is an answer *and* carries a majority of
#: the facts the turn was expected to convey. The previous rule was
#: ``len(matched) > 0``: on VAT journey turn 2 that scored a company
#: incorporation forms page ("Company Form 20 · Certificate of incorporation")
#: as coherent, because it happened to contain "150", "million" and "required",
#: while an on-topic reply about VAT registration thresholds scored zero.
#: A majority is the weakest rule that rejects a single incidental term.
_COHERENCE_MIN_COVERAGE = 0.5


def _score_turn_coherence(
    resp_text: str,
    mode: str,
    expected_keywords: list[str],
) -> tuple[bool, float, list[str], list[str]]:
    """Grade one journey turn.

    Returns ``(is_coherent, coverage, matched, missing)``. Coverage is reported
    alongside the boolean because a pass/fail alone hides how close a turn was,
    and averaging coverage across turns is a far less gameable journey score
    than counting turns that cleared a threshold.

    Keyword coverage still cannot tell whether an answer is *correct* — only
    whether it carries the expected vocabulary on token boundaries. Judging
    correctness needs a model; see the note in the report.
    """
    if not expected_keywords:
        return False, 0.0, [], []
    if _is_non_answer(resp_text, mode):
        return False, 0.0, [], list(expected_keywords)
    low = resp_text.lower()
    matched = _matched_terms(low, expected_keywords)
    missing = [kw for kw in expected_keywords if kw not in matched]
    coverage = len(matched) / len(expected_keywords)
    return coverage >= _COHERENCE_MIN_COVERAGE, coverage, matched, missing


def _score_query_response(
    gt: TaxGroundTruth,
    resp_data: dict[str, Any],
    status_code: int,
    latency: float,
) -> SingleQueryScore:
    if status_code not in {200, 201}:
        return SingleQueryScore(
            query_id=gt.query_id,
            topic=gt.topic,
            locale=gt.locale,
            user_query=gt.user_query,
            status_code=status_code,
            latency_s=latency,
            retrieval_mode="error",
            intent_detected="error",
            intent_correct=False,
            accuracy_score=0.0,
            numerical_correct=False,
            keywords_matched=[],
            keywords_missing=gt.required_keywords,
            citations_matched=[],
            language_match=False,
            response_text_snippet="",
            error=f"HTTP {status_code}",
        )

    resp_text = (
        resp_data.get("reply")
        or resp_data.get("response")
        or resp_data.get("answer")
        or resp_data.get("message")
        or ""
    )
    resp_lower = resp_text.lower()
    mode = resp_data.get("retrieval_mode", resp_data.get("mode", "unknown"))

    # 1. Intent evaluation
    # Did the system route properly?
    intent_correct = True
    if gt.expected_intent == "calculator":
        intent_correct = (mode in {"calculator", "hybrid", "vector"})
    elif gt.expected_intent == "workflow":
        intent_correct = (mode in {"workflow", "hybrid", "vector"})
    elif gt.expected_intent == "hybrid_rag":
        intent_correct = (mode in {"hybrid", "vector", "faq_priority", "semantic_cache", "calculator"})

    # A reply that declines or asks for more input carries no facts, so it must
    # not collect keyword, numeric, or language credit for naming its topic.
    non_answer = _is_non_answer(resp_text, mode)

    # 2. Keyword matching
    matched_kw = [] if non_answer else _matched_terms(resp_lower, gt.required_keywords)
    missing_kw = [kw for kw in gt.required_keywords if kw not in matched_kw]
    kw_score = len(matched_kw) / max(1, len(gt.required_keywords))

    # 3. Numerical values matching
    num_correct = True
    if gt.required_numerical_values:
        num_matches = [] if non_answer else _matched_terms(resp_lower, gt.required_numerical_values)
        num_correct = len(num_matches) > 0

    # 4. Citations matching
    citations_matched = [] if non_answer else _matched_terms(resp_lower, gt.statutory_citations)

    # 5. Language check
    #
    # Markers must be words that do not occur in an English reply, matched on
    # token boundaries. The previous lists failed both tests: "mu" is inside
    # "must", "ya" inside "Kenya", "eri" inside "period" — and "ura" was a
    # marker for both languages while every reply names URA, so this check
    # returned True for plain English every time. Kept to content words that
    # are unambiguous in each language.
    lang_match = True
    if gt.locale == "lg":
        lg_markers = [
            "omusolo", "emisolo", "bitundu", "ebitundu", "bizinensi",
            "okukozesa", "okuwandiisa", "ssente", "gwa", "eby", "ani", "bwe",
        ]
        lang_match = bool(_matched_terms(resp_lower, lg_markers))
    elif gt.locale == "sw":
        sw_markers = [
            "kodi", "nchini", "asilimia", "lazima", "biashara", "malipo",
            "mapato", "sheria", "kusajili", "hutozwa", "wa", "ni",
        ]
        lang_match = bool(_matched_terms(resp_lower, sw_markers))
    if non_answer:
        lang_match = False

    # Composite accuracy score (0.0 to 1.0)
    score_components = [
        kw_score * 0.40,
        (1.0 if num_correct else 0.0) * 0.35,
        (1.0 if lang_match else 0.0) * 0.15,
        (1.0 if intent_correct else 0.0) * 0.10,
    ]
    accuracy_score = round(sum(score_components), 3)

    return SingleQueryScore(
        query_id=gt.query_id,
        topic=gt.topic,
        locale=gt.locale,
        user_query=gt.user_query,
        status_code=status_code,
        latency_s=round(latency, 3),
        retrieval_mode=mode,
        intent_detected=mode,
        intent_correct=intent_correct,
        accuracy_score=accuracy_score,
        numerical_correct=num_correct,
        keywords_matched=matched_kw,
        keywords_missing=missing_kw,
        citations_matched=citations_matched,
        language_match=lang_match,
        response_text_snippet=resp_text[:120].replace("\n", " "),
    )


# ---------------------------------------------------------------------------
# Runner Functions
# ---------------------------------------------------------------------------
async def evaluate_single_queries(
    target_base: str,
    ground_truth: list[TaxGroundTruth],
    concurrency: int = 4,
) -> list[SingleQueryScore]:
    chat_url = f"{target_base.rstrip('/')}/v1/chat" if not target_base.endswith("/v1/chat") else target_base
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(gt: TaxGroundTruth) -> SingleQueryScore:
        async with semaphore:
            async with httpx.AsyncClient() as client:
                conv_id = f"eval_single_{gt.query_id}_{uuid.uuid4().hex[:6]}"
                payload = {
                    "message": gt.user_query,
                    "conversation_id": conv_id,
                    "locale": gt.locale,
                }
                headers = {"Content-Type": "application/json", "Accept": "application/json"}
                t0 = time.perf_counter()
                try:
                    resp = await client.post(chat_url, json=payload, headers=headers, timeout=60.0)
                    lat = time.perf_counter() - t0
                    data = resp.json() if resp.status_code in {200, 201} else {}
                    return _score_query_response(gt, data, resp.status_code, lat)
                except Exception as exc:
                    lat = time.perf_counter() - t0
                    return _score_query_response(gt, {}, 0, lat)

    tasks = [run_one(gt) for gt in ground_truth]
    return list(await asyncio.gather(*tasks))


async def evaluate_multi_turn_journeys(
    target_base: str,
    journeys: list[dict[str, Any]],
    concurrency: int = 2,
) -> list[JourneyScore]:
    chat_url = f"{target_base.rstrip('/')}/v1/chat" if not target_base.endswith("/v1/chat") else target_base
    semaphore = asyncio.Semaphore(concurrency)

    async def run_journey(j: dict[str, Any]) -> JourneyScore:
        conv_id = f"eval_journey_{j['journey_id']}_{uuid.uuid4().hex[:6]}"
        turn_details = []
        latencies = []
        successful_turns = 0
        intent_matches = 0
        topic_coherent_turns = 0
        turn_coverages: list[float] = []

        async with semaphore:
            async with httpx.AsyncClient() as client:
                for turn in j["turns"]:
                    payload = {
                        "message": turn["message"],
                        "conversation_id": conv_id,
                        "locale": turn.get("locale", "en"),
                    }
                    headers = {"Content-Type": "application/json"}
                    t0 = time.perf_counter()
                    try:
                        resp = await client.post(chat_url, json=payload, headers=headers, timeout=60.0)
                        lat = time.perf_counter() - t0
                        latencies.append(lat)
                        if resp.status_code in {200, 201}:
                            successful_turns += 1
                            data = resp.json()
                            resp_text = data.get("reply", "") or data.get("response", "") or data.get("answer", "")
                            resp_lower = resp_text.lower()
                            
                            mode = data.get("retrieval_mode", "unknown")

                            # Topic coherence: an answer carrying a majority of
                            # the turn's expected facts. Coverage is kept so a
                            # journey can be graded on how much each turn
                            # conveyed rather than on how many turns cleared a
                            # threshold, and so a failure is auditable.
                            is_coherent, coverage, matched_kw, missing_kw = _score_turn_coherence(
                                resp_text, mode, turn.get("expected_keywords", [])
                            )
                            if is_coherent:
                                topic_coherent_turns += 1
                            turn_coverages.append(coverage)

                            turn_details.append({
                                "turn": turn["turn"],
                                "message": turn["message"],
                                "status": resp.status_code,
                                "latency_s": round(lat, 3),
                                "mode": mode,
                                "coherent": is_coherent,
                                "keyword_coverage": round(coverage, 3),
                                "keywords_matched": matched_kw,
                                "keywords_missing": missing_kw,
                                "is_answer": not _is_non_answer(resp_text, mode),
                                "snippet": resp_text[:90],
                            })
                        else:
                            # A failed turn still consumed one of the journey's
                            # turns, so it has to enter the coverage mean as a
                            # zero rather than be omitted from it.
                            turn_coverages.append(0.0)
                            turn_details.append({
                                "turn": turn["turn"],
                                "message": turn["message"],
                                "status": resp.status_code,
                                "latency_s": round(lat, 3),
                                "mode": "error",
                                "coherent": False,
                                "keyword_coverage": 0.0,
                                "is_answer": False,
                                "snippet": f"HTTP {resp.status_code}",
                            })
                    except Exception as e:
                        lat = time.perf_counter() - t0
                        latencies.append(lat)
                        turn_coverages.append(0.0)
                        turn_details.append({
                            "turn": turn["turn"],
                            "message": turn["message"],
                            "status": 0,
                            "latency_s": round(lat, 3),
                            "mode": "error",
                            "coherent": False,
                            "keyword_coverage": 0.0,
                            "is_answer": False,
                            "snippet": str(e)[:90],
                        })
                    await asyncio.sleep(0.5)

        total = len(j["turns"])
        return JourneyScore(
            journey_id=j["journey_id"],
            description=j["description"],
            total_turns=total,
            successful_turns=successful_turns,
            mean_latency_s=round(statistics.mean(latencies) if latencies else 0.0, 3),
            intent_accuracy_pct=round((successful_turns / max(1, total)) * 100.0, 1),
            topic_coherence_pct=round((topic_coherent_turns / max(1, total)) * 100.0, 1),
            keyword_coverage_pct=round(
                (sum(turn_coverages) / max(1, total)) * 100.0, 1
            ),
            turn_details=turn_details,
        )

    tasks = [run_journey(j) for j in journeys]
    return list(await asyncio.gather(*tasks))


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------
def generate_full_report(
    target_url: str,
    concurrency: int,
    query_scores: list[SingleQueryScore],
    journey_scores: list[JourneyScore],
) -> FullEvaluationReport:
    latencies = sorted([s.latency_s for s in query_scores])
    n = len(latencies)

    def p(pct: float) -> float:
        if n == 0:
            return 0.0
        idx = max(0, min(n - 1, math.floor(pct * n)))
        return round(latencies[idx], 3)

    mean_acc = statistics.mean([s.accuracy_score for s in query_scores]) if query_scores else 0.0
    intent_prec = len([s for s in query_scores if s.intent_correct]) / max(1, len(query_scores))

    # Per locale accuracy
    locales = {"en", "lg", "sw"}
    locale_acc = {}
    for loc in locales:
        loc_scores = [s for s in query_scores if s.locale == loc]
        if loc_scores:
            locale_acc[loc] = round(statistics.mean([s.accuracy_score for s in loc_scores]) * 100.0, 1)
        else:
            locale_acc[loc] = 0.0

    # Per topic accuracy
    topics = set(s.topic for s in query_scores)
    topic_acc = {}
    for top in topics:
        top_scores = [s for s in query_scores if s.topic == top]
        topic_acc[top] = round(statistics.mean([s.accuracy_score for s in top_scores]) * 100.0, 1)

    mean_coherence = (
        statistics.mean([j.topic_coherence_pct for j in journey_scores]) if journey_scores else 0.0
    )

    return FullEvaluationReport(
        target_url=target_url,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        concurrency=concurrency,
        total_single_queries=len(query_scores),
        single_query_accuracy_pct=round(mean_acc * 100.0, 1),
        single_query_intent_precision_pct=round(intent_prec * 100.0, 1),
        mean_latency_s=round(statistics.mean(latencies) if latencies else 0.0, 3),
        p50_latency_s=p(0.50),
        p95_latency_s=p(0.95),
        p99_latency_s=p(0.99),
        multilingual_fidelity_pct=locale_acc,
        topic_accuracy_pct=topic_acc,
        multi_turn_journeys_evaluated=len(journey_scores),
        multi_turn_coherence_pct=round(mean_coherence, 1),
        query_scores=query_scores,
        journey_scores=journey_scores,
    )


def print_cli_report(report: FullEvaluationReport) -> None:
    width = 95
    print("\n" + "=" * width)
    print(f"  TAX EDUCATION ACCURACY & INTENT EVALUATION REPORT")
    print(f"  Target: {report.target_url}  |  Concurrency: {report.concurrency}")
    print("=" * width)
    print(f"  Overall Accuracy Score       : {report.single_query_accuracy_pct}%")
    print(f"  Intent Determination Precision: {report.single_query_intent_precision_pct}%")
    print(f"  Latency Distribution         : p50={report.p50_latency_s:.3f}s | p95={report.p95_latency_s:.3f}s | p99={report.p99_latency_s:.3f}s")
    print(f"  Multi-Turn Topic Coherence   : {report.multi_turn_coherence_pct}%")

    print("\n  🌐 Multilingual Accuracy Breakdown:")
    for loc, acc in sorted(report.multilingual_fidelity_pct.items()):
        print(f"    - Locale [{loc.upper()}]: {acc:.1f}%")

    print("\n  📚 Topic Cluster Accuracy Breakdown:")
    for top, acc in sorted(report.topic_accuracy_pct.items(), key=lambda x: -x[1]):
        print(f"    - {top:<16}: {acc:.1f}%")

    print("\n  🔍 Single Query Detail Samples:")
    print(f"  {'Query ID':<30} | {'Locale':<6} | {'Intent OK':<9} | {'Acc Score':<9} | {'Latency':<8} | {'Snippet'}")
    print("  " + "-" * 90)
    for q in report.query_scores:
        intent_icon = "✅" if q.intent_correct else "❌"
        print(f"  {q.query_id:<30} | {q.locale:<6} | {intent_icon:<9} | {q.accuracy_score*100:>7.1f}% | {q.latency_s:>6.3f}s | {q.response_text_snippet[:35]}...")

    print("\n  💬 Multi-Turn Journey Coherence:")
    for j in report.journey_scores:
        print(f"    * [{j.journey_id}] {j.description}")
        print(f"      Success: {j.successful_turns}/{j.total_turns} turns | Mean Latency: {j.mean_latency_s}s | Coherence: {j.topic_coherence_pct}% | Fact coverage: {j.keyword_coverage_pct}%")
        for t in j.turn_details:
            flag = "✅" if t.get("coherent") else ("∅" if not t.get("is_answer", True) else "❌")
            miss = ",".join(t.get("keywords_missing", [])) or "-"
            print(f"        {flag} turn{t['turn']} mode={t.get('mode','?'):<13} cov={t.get('keyword_coverage',0.0):.2f} missing={miss}")
    print("=" * width + "\n")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
async def main() -> int:
    parser = argparse.ArgumentParser(description="Tax Education Accuracy & Intent Evaluation")
    parser.add_argument(
        "--target",
        default="http://localhost:3032/api",
        help="Target base URL: local_api (http://localhost:8083), local_frontend (http://localhost:3032/api), ngrok, or custom URL",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrency level")
    parser.add_argument("--out", default="", help="Output JSON path")
    args = parser.parse_args()

    target_url = ENDPOINTS.get(args.target, args.target)
    print(f"Evaluating Tax Education on target: {target_url} (concurrency={args.concurrency})...")

    # 1. Evaluate single queries
    print("  [1/2] Evaluating single-turn domain inquiries...")
    single_scores = await evaluate_single_queries(target_url, GROUND_TRUTH_BANK, concurrency=args.concurrency)

    # 2. Evaluate multi-turn journeys
    print("  [2/2] Evaluating multi-turn taxpayer journeys...")
    journey_scores = await evaluate_multi_turn_journeys(target_url, MULTI_TURN_TEST_JOURNEYS, concurrency=max(1, args.concurrency // 2))

    # 3. Compile report
    report = generate_full_report(target_url, args.concurrency, single_scores, journey_scores)
    print_cli_report(report)

    # 4. Save JSON
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"Report saved to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
