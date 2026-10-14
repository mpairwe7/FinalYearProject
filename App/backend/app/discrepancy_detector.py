"""Automated conversational bug & knowledge discrepancy detector.

Identifies when a taxpayer in a multi-turn conversation points out false,
outdated, or inaccurate factual information provided in the assistant's
preceding reply, while carefully distinguishing genuine factual disputes from:
1. User constraint clarifications ("No, I meant for a partnership")
2. Emotional complaints ("This tax rate is unfair")
3. Ambiguous topic changes

Also generates respectful, anti-sycophantic response templates acknowledging
the dispute and providing an automated review tracking ID.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .guardrails import redact_pii_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Positive indicators of an explicit bug or discrepancy report
_EXPLICIT_BUG_PATTERNS = [
    re.compile(
        r"\b(?:report\s+(?:a\s+)?(?:bug|discrepancy|error|mistake|issue)|"
        r"bug\s+report(?::|\b)|"
        r"error\s+report(?::|\b)|"
        r"i\s+(?:want|need|would\s+like)\s+to\s+report\s+(?:a\s+)?(?:bug|error|discrepancy|mistake)|"
        r"there\s+(?:is|'s)\s+a\s+(?:bug|discrepancy|factual\s+error)\b)\b",
        re.IGNORECASE,
    ),
]

# Positive indicators of a factual disagreement
_DISPUTE_PATTERNS = [
    re.compile(
        r"\b(?:"
        r"(?:that|this|it|the\s+(?:answer|information|reply|response|calculation)|your\s+(?:answer|information|reply|response|calculation))"
        r"(?:'s|\s+is|\s+was)?\s+(?:wrong|incorrect|false|untrue|outdated|not\s+true|not\s+right|a\s+mistake|inaccurate|erroneous|misleading)|"
        r"you(?:'re|\s+are)\s+(?:wrong|mistaken|incorrect)|"
        r"you\s+made\s+an?\s+(?:error|mistake)|"
        r"(?:there\s+(?:is|'s)|i\s+found)\s+an?\s+(?:error|bug|mistake|discrepancy)|"
        r"(?:the\s+)?(?:rate|threshold|law|rule|act|procedure|fee|penalty)\s+(?:changed|was\s+updated|was\s+amended|is\s+actually|has\s+been\s+repealed)|"
        r"under\s+the\s+(?:20\d\d\s+)?(?:amendment|amendment\s+act|finance\s+act|statutory\s+instrument)|"
        r"actually\b.*?\b(?:is|was|changed|now|reduced|increased|raised|repealed|became|amended)\b|"
        r"it\s+(?:is|was)\s+(?:increased|reduced|raised|lowered|changed)\s+to\b|"
        r"instead\s+of\s+\d+|"
        r"not\s+\d+(?:\.\d+)?%\s*,?\s*(?:it(?:'s|\s+is)|but)\b|"
        r"stop\s+giving\s+(?:false|wrong|outdated)\s+info|"
        r"(?:report|reporting)\s+(?:a\s+)?(?:bug|discrepancy|error|mistake)|"
        r"bug\s+report\b|"
        r"error\s+report\b)\b",
        re.IGNORECASE,
    ),
    # Luganda dispute indicators
    re.compile(
        r"\b(?:nedda\b.*?\b(?:si\s+kituufu|kikyamu|si\s+kituukire|ssi\s+\d+|si\s+\d+|gukyuse|byakyusibwa)\b|"
        r"ebbago\s+(?:lyakyusibwa|lyakyuusibwa)|"
        r"amateeka\s+makadde|omusolo\s+gukyuse|"
        r"waliwo\s+(?:ensobi|akazito|obuzibu))\b",
        re.IGNORECASE,
    ),
    # Swahili dispute indicators
    re.compile(
        r"\b(?:hapana\b.*?\b(?:si\s+sahihi|si\s+kweli|siyo\s+kweli|siyo\s+\d+|si\s+\d+|imebadilika|kimebadilika)\b|"
        r"sheria\s+imebadilika|kiwango\s+kimebadilika|hilo\s+ni\s+kosa|"
        r"kuna\s+(?:kosa|hitilafu))\b",
        re.IGNORECASE,
    ),
]

# Negative indicators: User is simply refining their situation or asking a clarification
_CLARIFICATION_PATTERNS = [
    re.compile(
        r"\b(?:no\s*,?\s*(?:i\s+meant|i\s+mean|for\s+my|my\s+business|my\s+company|i\s+am|i'm|i\s+have|i\s+don't\s+have)|"
        r"not\s+what\s+i\s+(?:asked|wanted|meant)|"
        r"i\s+didn't\s+ask\s+(?:for|about)|"
        r"what\s+about\s+(?:for\s+)?(?:an?\s+)?(?:individual|partnership|foreigner|company)|"
        r"no\s*,?\s*what\s+i\s+need\b|"
        r"sorry\s*,?\s*i\s+meant\b)\b",
        re.IGNORECASE,
    ),
]

# Emotional venting without factual assertion
_SENTIMENT_COMPLAINT_PATTERNS = [
    re.compile(
        r"\b(?:this\s+(?:tax|rate|fee|law|country)\s+is\s+(?:unfair|crazy|ridiculous|too\s+high|robbery|bad)|"
        r"why\s+(?:do\s+we|must\s+i)\s+pay\s+so\s+much|"
        r"ura\s+is\s+stealing)\b",
        re.IGNORECASE,
    ),
]

_RATE_CLUES = re.compile(r"(?:\b\d+(?:\.\d+)?%|\b(?:percent|rate|ugx|shillings|threshold|bracket|cap)\b)", re.IGNORECASE)
_PROCEDURE_CLUES = re.compile(r"\b(?:form|step|portal|e-services|application|prn|apply|process|procedure)\b", re.IGNORECASE)
_AMENDMENT_CLUES = re.compile(r"\b(?:act|amendment|gazette|repealed|statutory|law|section|schedule|202[0-9])\b", re.IGNORECASE)


@dataclass(frozen=True)
class DiscrepancyDetectionResult:
    """Outcome of evaluating whether the user message disputes previous bot claims."""

    is_dispute: bool
    discrepancy_type: str = "outdated_law"
    confidence: float = 0.0
    bot_statement: str = ""
    user_correction: str = ""
    suggested_priority: str = "normal"


def _split_into_sentences(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return [p.strip() for p in parts if p.strip()]


def _extract_challenged_statement(bot_reply: str, user_message: str) -> str:
    """Identify which sentence or claim in bot_reply the user is most likely challenging."""
    sentences = _split_into_sentences(bot_reply)
    if not sentences:
        return (bot_reply or "")[:300].strip()
    if len(sentences) == 1:
        return sentences[0]

    # Look for sentence with highest lexical/numerical overlap with user message
    user_words = set(re.findall(r"\b[a-zA-Z0-9_%]+\b", user_message.lower()))
    user_words -= {"the", "a", "an", "is", "was", "it", "that", "you", "to", "in", "of", "and", "not", "no"}

    best_sentence = sentences[0]
    best_overlap = -1

    for s in sentences:
        s_words = set(re.findall(r"\b[a-zA-Z0-9_%]+\b", s.lower()))
        overlap = len(user_words.intersection(s_words))
        if overlap > best_overlap:
            best_overlap = overlap
            best_sentence = s

    return best_sentence


def detect_discrepancy(
    user_message: str,
    previous_bot_reply: str = "",
) -> DiscrepancyDetectionResult:
    """Analyze whether user_message asserts a factual error or outdated info in previous_bot_reply."""
    u_text = str(user_message or "").strip()
    b_text = str(previous_bot_reply or "").strip()

    if not u_text or len(u_text) < 4:
        return DiscrepancyDetectionResult(is_dispute=False)

    # Check explicit bug report first (works even without previous bot reply)
    is_explicit_bug = any(p.search(u_text) for p in _EXPLICIT_BUG_PATTERNS)
    if is_explicit_bug and not b_text:
        discrepancy_type = "outdated_law"
        if _RATE_CLUES.search(u_text):
            discrepancy_type = "incorrect_rate"
        elif _PROCEDURE_CLUES.search(u_text):
            discrepancy_type = "procedure_changed"
        priority = "high" if re.search(r"\b(?:202[3-6]|amendment\s+act|statutory\s+instrument|finance\s+act)\b", u_text, re.IGNORECASE) else "normal"
        return DiscrepancyDetectionResult(
            is_dispute=True,
            discrepancy_type=discrepancy_type,
            confidence=0.95,
            bot_statement="User-reported discrepancy in tax guidance or calculation",
            user_correction=redact_pii_text(u_text),
            suggested_priority=priority,
        )

    if not b_text:
        return DiscrepancyDetectionResult(is_dispute=False)

    # 1. Check for clarification exclusions (e.g. "No, I meant for an NGO")
    for pat in _CLARIFICATION_PATTERNS:
        if pat.search(u_text):
            return DiscrepancyDetectionResult(is_dispute=False)

    # 2. Check for emotional complaints
    for pat in _SENTIMENT_COMPLAINT_PATTERNS:
        if pat.search(u_text):
            return DiscrepancyDetectionResult(is_dispute=False)

    # 3. Check for dispute signals
    matched = is_explicit_bug or any(pat.search(u_text) for pat in _DISPUTE_PATTERNS)
    if not matched:
        return DiscrepancyDetectionResult(is_dispute=False)

    # Determine discrepancy category
    discrepancy_type = "outdated_law"
    if _AMENDMENT_CLUES.search(u_text):
        discrepancy_type = "outdated_law"
    elif _RATE_CLUES.search(u_text) or _RATE_CLUES.search(b_text):
        discrepancy_type = "incorrect_rate"
    elif _PROCEDURE_CLUES.search(u_text):
        discrepancy_type = "procedure_changed"

    # Identify challenged sentence
    challenged = _extract_challenged_statement(b_text, u_text) or b_text[:300].strip()

    # Prioritization: legal/amendment/rate disputes with explicit citations or dates get high priority
    priority = "normal"
    if re.search(r"\b(?:202[3-6]|amendment\s+act|statutory\s+instrument|finance\s+act)\b", u_text, re.IGNORECASE):
        priority = "high"

    return DiscrepancyDetectionResult(
        is_dispute=True,
        discrepancy_type=discrepancy_type,
        confidence=0.9,
        bot_statement=redact_pii_text(challenged),
        user_correction=redact_pii_text(u_text),
        suggested_priority=priority,
    )


def format_discrepancy_acknowledgement(
    report_id: str,
    baseline_topic: str = "current URA statutory schedules",
    locale: str = "en",
    user_assertion: str = "",
) -> str:
    """Generate an objective, appreciative, and anti-sycophantic conversational acknowledgment."""
    short_id = report_id[-8:] if len(report_id) >= 8 else report_id
    assertion_note = f"\n\nWe have recorded your assertion: \"{user_assertion[:200]}\"." if user_assertion else ""
    if locale == "lg":
        return (
            f"Webale nnyo okulambika kino! Okusinziira ku mateeka n'entegeka za URA ezikozesebwa kati, "
            f"ebisangiddwawo byandiba nga byakyusibwa. Ntaddeyo alipoota y'okwetegereza ekyakyusiddwa "
            f"(#KB-{short_id}) eri ttiimu y'ebyamateeka n'ebisolo okugikakasa.{assertion_note}"
        )
    if locale == "sw":
        return (
            f"Asante sana kwa kuonyesha jambo hili! Kulingana na miongozo ya sasa ya URA, "
            f"kunaweza kuwa na mabadiliko ya kisheria au viwango. Nimewasilisha ripoti ya uhakiki "
            f"(#KB-{short_id}) kwa timu yetu ya kiufundi ili kuthibitisha.{assertion_note}"
        )
    return (
        f"Thank you for pointing this out! Based on our {baseline_topic}, "
        f"tax statutory provisions and rates are subject to periodic amendments. "
        f"I have automatically submitted a Knowledge Discrepancy Report (**#KB-{short_id}**) "
        f"for our tax policy and technical review team to verify against the latest gazettes.{assertion_note}"
    )
