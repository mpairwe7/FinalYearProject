"""Fact extractor — rule-based today, LLM-backed after Phase 15 full.

Walks a finished conversation and emits a list of ``FactCandidate``
objects: `(category, subject, predicate, object, confidence)`
plus source provenance.  The caller (`MemoryService`) then writes
those with confidence ≥ threshold to :class:`SemanticMemory`.

Design: rule-based first pass so this works without a live LLM; an
LLM-backed fallback using Qwen2.5 JSON-mode is wired as a TODO for
Phase 16 full when the eval budget is available.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FactCandidate:
    category: str
    subject: str
    predicate: str
    object_value: str
    confidence: float
    source_turn: int = 0
    rule_id: str = ""


# ---------------------------------------------------------------------------
# Rule patterns
# ---------------------------------------------------------------------------
_TAXPAYER_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(i\s*am|i'm|i\s+run)\s+a\s+sole[- ]?trader\b", re.IGNORECASE), "sole_trader"),
    (re.compile(r"\b(my\s+)?(company|firm|business)\b.*\b(ltd|limited|plc|inc)\b", re.IGNORECASE), "company"),
    (re.compile(r"\bngo\b|\bnon[- ]profit\b", re.IGNORECASE), "ngo"),
    (re.compile(r"\bnon[- ]resident\b", re.IGNORECASE), "non_resident"),
    (re.compile(r"\bpartnership\b", re.IGNORECASE), "partnership"),
]

_INDUSTRY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bretail\b|\bshop\b", re.IGNORECASE), "retail"),
    (re.compile(r"\bimport(?:s|ing|er)?\b", re.IGNORECASE), "import_export"),
    (re.compile(r"\bconstruction\b|\bbuilding\b", re.IGNORECASE), "construction"),
    (re.compile(r"\brestaurant\b|\bhotel\b|\bcatering\b", re.IGNORECASE), "hospitality"),
    (re.compile(r"\btransport\b|\blogistics\b", re.IGNORECASE), "transport"),
    (re.compile(r"\bagricultur(?:e|al)\b|\bfarm(?:ing)?\b", re.IGNORECASE), "agriculture"),
    (re.compile(r"\btech(?:nology)?\b|\bsoftware\b", re.IGNORECASE), "technology"),
]

_REGISTRATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bregistered\s+for\s+vat\b|\bi\s+pay\s+vat\b", re.IGNORECASE), "vat"),
    (re.compile(r"\bpaye\b", re.IGNORECASE), "paye"),
    (re.compile(r"\bwithholding\s+tax\b|\bwht\b", re.IGNORECASE), "wht"),
    (re.compile(r"\bcorporation\s+tax\b|\bcit\b", re.IGNORECASE), "cit"),
]

_LANGUAGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bluganda\b|\bmunange\b|\bwabula\b", re.IGNORECASE), "lg"),
]


class FactExtractor:
    """Rule-based extractor over conversation turns.

    Each extraction pass walks a list of ``{role, content}`` turns
    (user turns only — assistant text is ignored for facts) and
    emits candidates.  Confidence starts at 0.85 for strong
    phrases (``I am a sole trader``) and degrades for weaker matches.
    """

    def extract(self, turns: list[dict[str, str]]) -> list[FactCandidate]:
        candidates: list[FactCandidate] = []
        for i, turn in enumerate(turns):
            if turn.get("role") not in ("user", "user_message"):
                continue
            text = str(
                turn.get("content")
                or turn.get("user_message")
                or turn.get("message", "")
            )
            if not text:
                continue
            candidates.extend(self._scan(text, source_turn=i))
        return self._dedupe(candidates)

    def _scan(self, text: str, source_turn: int) -> list[FactCandidate]:
        out: list[FactCandidate] = []

        for pat, value in _TAXPAYER_TYPE_PATTERNS:
            if pat.search(text):
                out.append(FactCandidate(
                    category="taxpayer_type",
                    subject="user",
                    predicate="is_a",
                    object_value=value,
                    confidence=0.85,
                    source_turn=source_turn,
                    rule_id=f"taxpayer:{pat.pattern[:30]}",
                ))
                break  # one taxpayer type per user

        for pat, value in _INDUSTRY_PATTERNS:
            if pat.search(text):
                out.append(FactCandidate(
                    category="industry",
                    subject="user",
                    predicate="operates_in",
                    object_value=value,
                    confidence=0.75,
                    source_turn=source_turn,
                    rule_id=f"industry:{value}",
                ))

        for pat, value in _REGISTRATION_PATTERNS:
            if pat.search(text):
                out.append(FactCandidate(
                    category="registered_vat" if value == "vat" else "registered_tax",
                    subject="user",
                    predicate="is_registered_for",
                    object_value=value,
                    confidence=0.80,
                    source_turn=source_turn,
                    rule_id=f"reg:{value}",
                ))

        for pat, value in _LANGUAGE_PATTERNS:
            if pat.search(text):
                out.append(FactCandidate(
                    category="primary_language",
                    subject="user",
                    predicate="speaks",
                    object_value=value,
                    confidence=0.70,
                    source_turn=source_turn,
                    rule_id="language",
                ))

        return out

    def _dedupe(self, candidates: list[FactCandidate]) -> list[FactCandidate]:
        """Collapse duplicate (category, predicate, object) triples, keeping max confidence."""
        seen: dict[tuple[str, str, str], FactCandidate] = {}
        for c in candidates:
            key = (c.category, c.predicate, c.object_value)
            existing = seen.get(key)
            if existing is None or c.confidence > existing.confidence:
                seen[key] = c
        return list(seen.values())
