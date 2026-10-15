"""Hybrid Speech-to-Speech (S2S) + Regulated Cascaded Router & Semantic Endpointing (2026).

Routes low-risk conversational turns (greetings, backchannels, empathy, non-numeric
slot prompts) through the sub-450ms fast S2S path while strictly enforcing the
cascaded STT -> English-Router -> Tool/RAG -> Figure-Protected MT -> TTS path
for any turn involving statutory tax rules, rates, money amounts, TINs, or PRNs.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Final

from ..calculator_router import detect_calculator_intent, has_money_amount
from ..flags import flags

logger = logging.getLogger(__name__)

_STATUTORY_OR_NUMERIC_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\b\d+\b|%|\b(?:vat|paye|wht|cit|pit|tin|prn|nin|efris|dts|tcc|asycuda|"
    r"rate|rates|tax|duty|customs|tariff|penalty|interest|deadline|section|act|"
    r"calculate|how\s+much|shillings?|ugx|usd|million|billion|thousand|"
    r"omusolo|ebitundu|obukadde|akakadde|emitwalo|shilingi|tiini|"
    r"kodi|ushuru|forodha|asilimia|milioni|marejesho)\b)",
    re.IGNORECASE,
)

_CONVERSATIONAL_FAST_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:hello|hi|hey|good\s+(?:morning|afternoon|evening)|thank\s+you|thanks|"
    r"ok(?:ay)?|yes|no|sure|alright|please\s+help|i\s+have\s+a\s+question|"
    r"oli\s+otya|gyebale\s+ko|wasuze\s+otya|osiibye\s+otya|webale|yee|nedda|kale|"
    r"habari|mambo|shikamoo|asante|sawa|ndiyo|hapana)\b",
    re.IGNORECASE,
)

_INCOMPLETE_DIGIT_TAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:\d{1,8}|zero|one|two|three|four|five|six|seven|eight|nine|"
    r"emu|bbiri|ssatu|nnya|ttaano|mukaaga|musanvu|munaana|mwenda|"
    r"moja|mbili|tatu|nne|tano|sita|saba|nane|tisa)$",
    re.IGNORECASE,
)

_TRAILING_CONJUNCTION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:and|or|but|because|if|when|about|for|with|to|"
    r"era|naye|oba|kubanga|ku|mu|wa|"
    r"na|au|lakini|kwa|kuhusu)$",
    re.IGNORECASE,
)


def _min_silence_ms() -> int:
    try:
        return max(150, int(os.getenv("VOICE_SEMANTIC_ENDPOINT_MIN_SILENCE_MS", "320")))
    except ValueError:
        return 320


def _numeric_silence_ms() -> int:
    try:
        return max(500, int(os.getenv("VOICE_SEMANTIC_ENDPOINT_NUMERIC_SILENCE_MS", "1100")))
    except ValueError:
        return 1100


@dataclass(frozen=True)
class HybridSpeechDecision:
    """Routing decision between fast conversational S2S and regulated cascaded RAG/Tools."""

    mode: str  # "s2s_fast" | "cascaded_guarded"
    reason: str
    contains_numeric_or_statutory: bool
    recommended_vad_silence_ms: int


def compute_semantic_endpoint_silence_ms(partial_text: str, locale: str = "en") -> int:
    """Compute adaptive VAD endpointing silence window (ms) from a partial transcript.

    Extends silence tolerance to ~1100ms when the caller is mid-TIN/PRN or ends on
    a conjunction; snaps down to ~320ms on syntactically complete utterances.
    """
    clean = (partial_text or "").strip()
    if not clean:
        return 500
    if _INCOMPLETE_DIGIT_TAIL_RE.search(clean) or _TRAILING_CONJUNCTION_RE.search(clean):
        return _numeric_silence_ms()
    if clean.endswith(("?", ".", "!")) or len(clean.split()) >= 4:
        return _min_silence_ms()
    return 450


def classify_hybrid_speech_mode(
    query: str,
    locale: str = "en",
    *,
    subject: str = "",
    active_workflow_slot_type: str = "",
) -> HybridSpeechDecision:
    """Decide whether *query* can use the fast S2S path or requires the guarded cascade."""
    clean = (query or "").strip()
    loc = (locale or "en").lower()
    silence_ms = compute_semantic_endpoint_silence_ms(clean, locale=loc)

    has_statutory = bool(
        has_money_amount(clean)
        or detect_calculator_intent(clean) is not None
        or _STATUTORY_OR_NUMERIC_RE.search(clean)
        or active_workflow_slot_type in {"tin", "currency", "amount", "date", "prn", "number"}
    )

    if not flags.is_enabled("hybrid_speech_s2s", subject=subject or None):
        return HybridSpeechDecision(
            mode="cascaded_guarded",
            reason="flag_disabled",
            contains_numeric_or_statutory=has_statutory,
            recommended_vad_silence_ms=silence_ms,
        )

    if has_statutory:
        decision = HybridSpeechDecision(
            mode="cascaded_guarded",
            reason="statutory_or_numeric_guard",
            contains_numeric_or_statutory=True,
            recommended_vad_silence_ms=silence_ms,
        )
    elif _CONVERSATIONAL_FAST_RE.search(clean) or len(clean.split()) <= 5:
        decision = HybridSpeechDecision(
            mode="s2s_fast",
            reason="conversational_low_risk",
            contains_numeric_or_statutory=False,
            recommended_vad_silence_ms=_min_silence_ms(),
        )
    else:
        decision = HybridSpeechDecision(
            mode="cascaded_guarded",
            reason="policy_grounding_required",
            contains_numeric_or_statutory=False,
            recommended_vad_silence_ms=silence_ms,
        )

    try:
        from ..analytics import metrics

        metrics.inc(
            "voice_hybrid_route_total",
            labels={"mode": decision.mode, "locale": loc},
        )
    except Exception:
        logger.debug("hybrid_s2s: metric record skipped", exc_info=True)
    return decision


_FAST_GREETING_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:hello|hi|hey|good\s+(?:morning|afternoon|evening)|"
    r"oli\s+otya|gyebale\s+ko|wasuze\s+otya|osiibye\s+otya|"
    r"habari(?:\s+za\s+(?:asubuhi|mchana|jioni))?|mambo|shikamoo)\W*$",
    re.IGNORECASE,
)

_FAST_THANKS_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:thank\s+you(?:\s+very\s+much)?|thanks(?:\s+a\s+lot)?|"
    r"webale(?:\s+nnyo)?|weebale(?:\s+nnyo)?|"
    r"asante(?:\s+sana)?|shukrani)\W*$",
    re.IGNORECASE,
)


def build_s2s_fast_reply(query: str, locale: str = "en") -> str | None:
    """Return an immediate sub-millisecond conversational response for pure S2S fast turns, or None."""
    clean = (query or "").strip()
    if not clean or _STATUTORY_OR_NUMERIC_RE.search(clean):
        return None
    loc = (locale or "en").strip().lower()
    if _FAST_GREETING_RE.search(clean):
        return {
            "lg": "Gyebale ko! Ndi kitongole kya URA. Nkuyambe ntya ku by'omusolo leero?",
            "sw": "Habari! Karibu Mamlaka ya Mapato ya Uganda (URA). Nikusaidie vipi kuhusu kodi leo?",
            "en": "Hello! Welcome to the Uganda Revenue Authority. How can I help you with your taxes today?",
        }.get(loc, "Hello! Welcome to the Uganda Revenue Authority. How can I help you with your taxes today?")
    if _FAST_THANKS_RE.search(clean):
        return {
            "lg": "Kale nnyo! Olina ekibuuzo ekirala ku musolo?",
            "sw": "Karibu sana! Je, una swali lingine kuhusu kodi?",
            "en": "You are very welcome! Do you have any other tax question I can help with?",
        }.get(loc, "You are very welcome! Do you have any other tax question I can help with?")
    return None
