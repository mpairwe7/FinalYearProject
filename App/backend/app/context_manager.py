"""Rolling context & multi-turn memory management (2026).

Provides:
1. ``normalize_history_turns``: Robust schema normalizer converting any
   message list or turn dict sequence into standard ``[{"user_message": "...", "bot_reply": "..."}]``.
2. ``extract_conversation_entities``: Slot-filler extracting active tax domains,
   taxpayer status, figures, reference numbers, and core subject entities across turns.
3. ``RollingContextManager``: Hierarchical context manager maintaining:
   - Verbatim recent turns (last 4-6 turns) for high-fidelity prompt generation.
   - Compact structured rolling summary of older turns (1..N-K) to prevent
     memory loss in extended multi-turn sessions (>5 turns).
   - Structured entities extracted from the bounded history window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Known Tax Domain Patterns & Entities
# ---------------------------------------------------------------------------
_TAX_TOPIC_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(rental\s+(?:income\s+)?tax|rental\s+income|tenants?|landlords?)\b", re.I), "Rental Income Tax", "rental_tax"),
    (re.compile(r"\b(paye|pay\s+as\s+you\s+earn|salary|gross\s+pay|net\s+pay|employment\s+income)\b", re.I), "PAYE (Pay As You Earn)", "paye"),
    (re.compile(r"\b(efris|electronic\s+fiscal\s+(?:receipting|invoicing|device|system)?)\b", re.I), "EFRIS", "efris"),
    (re.compile(r"\b(value\s+added\s+tax|vat\b)\b", re.I), "Value Added Tax (VAT)", "vat"),
    (re.compile(r"\b(withholding\s+tax|wht\b|kodi\s+ya\s+zuio|zuio)\b", re.I), "Withholding Tax (WHT)", "wht"),
    (re.compile(r"\b(corporat(?:e|ion)\s+(?:income\s+)?tax|company\s+tax|cit\b)\b", re.I), "Corporation Tax (CIT)", "cit"),
    (re.compile(r"\b(register(?:ing|ation)?\s+for\s+(?:a\s+)?tin|get\s+(?:a\s+)?tin|apply\s+for\s+(?:a\s+)?tin|tin\s+registration|obtain\s+(?:a\s+)?tin)\b", re.I), "TIN Registration", "tin_registration"),
    (re.compile(r"\b(tin\b|tax\s+identification\s+number)\b", re.I), "TIN", "tin"),
    (re.compile(r"\b(customs|forodha|import\s+duty|export\s+duty|tariffs?|clearance|asycuda|single\s+customs)\b", re.I), "Customs & Import/Export Duty", "customs"),
    (re.compile(r"\b(stamp\s+duty|land\s+transfer|property\s+transfer)\b", re.I), "Stamp Duty", "stamp_duty"),
    (re.compile(r"\b(local\s+excise\s+duty|excise\s+duty|dts|digital\s+tax\s+stamps?)\b", re.I), "Excise Duty / DTS", "excise_duty"),
    (re.compile(r"\b(motor\s+vehicle|logbook|driving\s+licen[sc]e|number\s+plate|vehicle\s+transfer)\b", re.I), "Motor Vehicle Registration", "motor_vehicle"),
    (re.compile(r"\b(tax\s+clearance\s+certificate|tcc\b)\b", re.I), "Tax Clearance Certificate (TCC)", "tcc"),
    (re.compile(r"\b(objection|dispute|assessment\s+notice|tax\s+appeals\s+tribunal|tat\b)\b", re.I), "Objection & Dispute Resolution", "objection"),
    (re.compile(r"\b(gaming|betting|lottery)\b", re.I), "Gaming & Betting Tax", "gaming_tax"),
    (re.compile(r"\b(advance\s+tax|passenger\s+commercial\s+vehicles?)\b", re.I), "Advance Tax", "advance_tax"),
]

_TAXPAYER_STATUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(individual|sole\s+(?:trader|proprietor)|myself|personal)\b", re.I), "Individual / Sole Proprietor"),
    (re.compile(r"\b(company|corporation|ltd|limited|partnership|ngo|institution|business)\b", re.I), "Company / Organization"),
    (re.compile(r"\b(non[-\s]?resident)\b", re.I), "Non-Resident"),
    (re.compile(r"\b(resident)\b", re.I), "Resident"),
]


#: Earlier turns the LLM prompt replays verbatim (``llm._build_messages``).
#: Every older turn is in the rolling summary instead, so none falls between
#: the two (G120: the prompt used the last three while the summary started at
#: turn seven, and turns four to six reached the model in neither).
PROMPT_VERBATIM_TURNS = 3

_TURN_EXTRAS = ("locale", "user_message_en", "bot_reply_en")


def _with_locale(turn: dict[str, str], source: dict[str, Any]) -> dict[str, str]:
    """*turn* plus *source*'s stored ``locale`` and English forms, when present."""
    for key in _TURN_EXTRAS:
        stored = str(source.get(key) or "").strip()
        if stored:
            turn[key] = stored
    return turn


def english_view(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    """Turns as the models should read them: in English wherever it was stored.

    A Luganda or Swahili turn is stored with its English form (G119). The
    generator answers in English, and the rewriter, entity extraction and
    summary match English patterns, so they all read that form; the stored
    ``locale`` is kept for the answer-language decision.
    """
    out: list[dict[str, str]] = []
    for turn in turns:
        view = {
            "user_message": turn.get("user_message_en") or turn.get("user_message", ""),
            "bot_reply": turn.get("bot_reply_en") or turn.get("bot_reply", ""),
        }
        if turn.get("locale"):
            view["locale"] = turn["locale"]
        out.append(view)
    return out


def normalize_history_turns(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Normalize any history format into standard turn pairs.

    Handles:
    - Standard turn dicts: ``{"user_message": "...", "bot_reply": "..."}``
    - Role-based message lists: ``[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]``
    - Mixed or legacy keys: ``{"query": ..., "reply": ...}`` or ``{"user": ..., "assistant": ...}``

    A turn's stored ``locale`` (the language it was answered in) is kept when
    present: the answer language of the next turn is continued from it rather
    than re-detected from text (:mod:`app.language_state`).
    """
    if not history:
        return []

    # Case 1: Already paired turn dicts
    if all(isinstance(h, dict) and ("user_message" in h or "bot_reply" in h) for h in history):
        return [
            _with_locale(
                {
                    "user_message": str(h.get("user_message") or "").strip(),
                    "bot_reply": str(h.get("bot_reply") or "").strip(),
                },
                h,
            )
            for h in history
            if isinstance(h, dict) and (h.get("user_message") or h.get("bot_reply"))
        ]

    # Case 2: Flat list of role-based messages
    turns: list[dict[str, str]] = []
    current_user = ""
    for item in history:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or item.get("text") or item.get("message") or "").strip()

        if role == "user":
            if current_user:
                turns.append({"user_message": current_user, "bot_reply": ""})
            current_user = str(item.get("content_en") or "").strip() or content
        elif role in ("assistant", "system", "bot", "model"):
            reply = str(item.get("content_en") or "").strip() or content
            turns.append(_with_locale({"user_message": current_user, "bot_reply": reply}, item))
            current_user = ""
        elif "user_message" in item or "bot_reply" in item:
            turns.append({
                "user_message": str(item.get("user_message") or "").strip(),
                "bot_reply": str(item.get("bot_reply") or "").strip(),
            })
        elif "query" in item or "reply" in item:
            turns.append({
                "user_message": str(item.get("query") or "").strip(),
                "bot_reply": str(item.get("reply") or "").strip(),
            })

    if current_user:
        turns.append({"user_message": current_user, "bot_reply": ""})

    return turns


@dataclass
class ConversationEntities:
    """Structured slots extracted from multi-turn dialogue."""
    tax_topics: list[str] = field(default_factory=list)
    tax_topic_keys: list[str] = field(default_factory=list)
    taxpayer_types: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    reference_numbers: list[str] = field(default_factory=list)
    active_subject: str = ""


def extract_conversation_entities(turns: list[dict[str, str]]) -> ConversationEntities:
    """Extract domain entities and intent slots across multi-turn history."""
    topics: list[str] = []
    topic_keys: list[str] = []
    taxpayer_types: list[str] = []
    amounts: list[str] = []
    ref_numbers: list[str] = []
    active_subject = ""

    amount_re = re.compile(
        r"\b(?:ugx|ug\s?shs?|shs|bukadde|emitwalo|milioni|shilingi)?\s*(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:m|k|bn|million|thousand|billion|bukadde|mitwalo))\b"
        r"|\b(?:bukadde|emitwalo|milioni|shilingi)\s+\d+(?:,\d{3})*(?:\.\d+)?\b",
        re.I,
    )
    ref_re = re.compile(
        r"\b(?:TIN|PRN|ARN|REF|ASSESSMENT)(?:[-\s#:]|(?:is|no\.?|number)\s*)+([A-Z0-9]{5,18})\b",
        re.I,
    )

    for turn in turns:
        user_msg = turn.get("user_message", "")

        # Only derive persistent context from user input. Assistant-generated
        # claims are not evidence about the taxpayer's circumstances.
        user_matched_topic = ""
        for pat, label, key in _TAX_TOPIC_PATTERNS:
            if pat.search(user_msg):
                if label not in topics:
                    topics.append(label)
                    topic_keys.append(key)
                user_matched_topic = label

        if not user_matched_topic and re.search(r"\b(omusolo|kodi|ushuru)\b", user_msg, re.I):
            label, key = "Tax", "general_tax"
            if label not in topics:
                topics.append(label)
                topic_keys.append(key)
            user_matched_topic = label

        if user_matched_topic:
            active_subject = user_matched_topic

        # Taxpayer status
        for pat, status_label in _TAXPAYER_STATUS_PATTERNS:
            if pat.search(user_msg) and status_label not in taxpayer_types:
                taxpayer_types.append(status_label)

        # Monetary figures
        found_amounts = amount_re.findall(user_msg)
        for amt in found_amounts:
            amt_clean = amt.strip()
            if amt_clean and amt_clean not in amounts:
                amounts.append(amt_clean)

        # Reference numbers
        found_refs = ref_re.findall(user_msg)
        for ref in found_refs:
            if ref not in ref_numbers:
                ref_numbers.append(ref)

    return ConversationEntities(
        tax_topics=topics,
        tax_topic_keys=topic_keys,
        taxpayer_types=taxpayer_types,
        amounts=amounts,
        reference_numbers=ref_numbers,
        active_subject=active_subject,
    )


def summarize_older_turns(turns: list[dict[str, str]]) -> str:
    """Generate a high-density, concise abstractive summary of older conversation turns."""
    if not turns:
        return ""

    entities = extract_conversation_entities(turns)
    lines: list[str] = []

    if entities.tax_topics:
        lines.append(f"Tax domains discussed: {', '.join(entities.tax_topics)}.")
    if entities.taxpayer_types:
        lines.append(f"Taxpayer status/type: {', '.join(entities.taxpayer_types)}.")
    if entities.amounts:
        lines.append(f"Financial figures mentioned: {', '.join(entities.amounts[-3:])}.")
    if entities.reference_numbers:
        lines.append("A tax or payment reference number appeared earlier; its value is omitted.")
    if not lines and turns:
        lines.append(f"Earlier turns: {len(turns)} prior exchanges.")

    return " ".join(lines)


@dataclass
class ConversationContext:
    """Full conversational context bundle for agent prompt and RAG stages."""
    recent_turns: list[dict[str, str]]
    context_summary: str
    active_entities: ConversationEntities
    total_turns: int
    all_turns: list[dict[str, str]]


class RollingContextManager:
    """Manages rolling multi-turn context windows and active thread state."""

    DEFAULT_RECENT_LIMIT = 6  # Last 6 turns in full detail
    MAX_HISTORY_LOAD = 25     # Maximum historical turns loaded from storage

    def __init__(
        self,
        recent_limit: int = DEFAULT_RECENT_LIMIT,
        max_total_turns: int = MAX_HISTORY_LOAD,
    ) -> None:
        self.recent_limit = recent_limit
        self.max_total_turns = max_total_turns

    def build_context(
        self,
        raw_history: list[dict[str, Any]] | None,
        conversation_id: str = "",
    ) -> ConversationContext:
        """Construct a multi-turn context object with rolling summary and entity slots.

        Every turn is in its model-facing (English) form, see
        :func:`english_view`. ``recent_turns`` is the last ``recent_limit``
        turns, for the rewriter and the language decision; the summary covers
        every turn the prompt does not replay verbatim
        (:data:`PROMPT_VERBATIM_TURNS`).
        """
        normalized = english_view(normalize_history_turns(raw_history))
        if self.max_total_turns > 0 and len(normalized) > self.max_total_turns:
            normalized = normalized[-self.max_total_turns :]
        total = len(normalized)

        entities = extract_conversation_entities(normalized)

        recent = normalized[-self.recent_limit:] if self.recent_limit > 0 else normalized
        verbatim = max(1, min(PROMPT_VERBATIM_TURNS, self.recent_limit or PROMPT_VERBATIM_TURNS))
        summary = summarize_older_turns(normalized[:-verbatim]) if total > verbatim else ""

        return ConversationContext(
            recent_turns=recent,
            context_summary=summary,
            active_entities=entities,
            total_turns=total,
            all_turns=normalized,
        )

# Global helper instance
context_manager = RollingContextManager()
