"""Rolling context & multi-turn memory management (2026).

Provides:
1. ``normalize_history_turns``: Robust schema normalizer converting any
   message list or turn dict sequence into standard ``[{"user_message": "...", "bot_reply": "..."}]``.
2. ``extract_conversation_entities``: Slot-filler extracting active tax domains,
   taxpayer status, figures, reference numbers, and core subject entities across turns.
3. ``RollingContextManager``: Hierarchical context manager maintaining:
   - Verbatim recent turns (last 4-6 turns) for high-fidelity prompt generation.
   - Compact abstractive rolling summary of older turns (1..N-K) to prevent
     memory loss in extended multi-turn sessions (>5 turns).
   - In-session working memory caching keyed on conversation_id.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .memory.working import WorkingMemory

logger = logging.getLogger(__name__)

# Global session working memory singleton (30 min TTL)
_SESSION_WORKING_MEMORY = WorkingMemory(ttl_seconds=30 * 60)

# ---------------------------------------------------------------------------
# Known Tax Domain Patterns & Entities
# ---------------------------------------------------------------------------
_TAX_TOPIC_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(rental\s+(?:income\s+)?tax|rental\s+income|tenants?|landlords?)\b", re.I), "Rental Income Tax", "rental_tax"),
    (re.compile(r"\b(paye|pay\s+as\s+you\s+earn|salary|gross\s+pay|net\s+pay|employment\s+income)\b", re.I), "PAYE (Pay As You Earn)", "paye"),
    (re.compile(r"\b(efris|electronic\s+fiscal\s+(?:receipting|invoicing|device|system)?)\b", re.I), "EFRIS", "efris"),
    (re.compile(r"\b(value\s+added\s+tax|vat\b)\b", re.I), "Value Added Tax (VAT)", "vat"),
    (re.compile(r"\b(withholding\s+tax|wht\b)\b", re.I), "Withholding Tax (WHT)", "wht"),
    (re.compile(r"\b(corporat(?:e|ion)\s+(?:income\s+)?tax|company\s+tax|cit\b)\b", re.I), "Corporation Tax (CIT)", "cit"),
    (re.compile(r"\b(register(?:ing|ation)?\s+for\s+(?:a\s+)?tin|get\s+(?:a\s+)?tin|apply\s+for\s+(?:a\s+)?tin|tin\s+registration|obtain\s+(?:a\s+)?tin)\b", re.I), "TIN Registration", "tin_registration"),
    (re.compile(r"\b(tin\b|tax\s+identification\s+number)\b", re.I), "TIN", "tin"),
    (re.compile(r"\b(customs|import\s+duty|export\s+duty|tariffs?|clearance|asycuda|single\s+customs)\b", re.I), "Customs & Import/Export Duty", "customs"),
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


def normalize_history_turns(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Normalize any history format into standard turn pairs.

    Handles:
    - Standard turn dicts: ``{"user_message": "...", "bot_reply": "..."}``
    - Role-based message lists: ``[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]``
    - Mixed or legacy keys: ``{"query": ..., "reply": ...}`` or ``{"user": ..., "assistant": ...}``
    """
    if not history:
        return []

    # Case 1: Already paired turn dicts
    if all(isinstance(h, dict) and ("user_message" in h or "bot_reply" in h) for h in history):
        return [
            {
                "user_message": str(h.get("user_message") or "").strip(),
                "bot_reply": str(h.get("bot_reply") or "").strip(),
            }
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
            current_user = content
        elif role in ("assistant", "system", "bot", "model"):
            turns.append({"user_message": current_user, "bot_reply": content})
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
        r"\b(?:ugx|ug\s?shs?|shs)?\s*(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:m|k|bn|million|thousand|billion))\b",
        re.I,
    )
    ref_re = re.compile(
        r"\b(?:TIN|PRN|ARN|REF|ASSESSMENT)(?:[-\s#:]|(?:is|no\.?|number)\s*)+([A-Z0-9]{5,18})\b",
        re.I,
    )

    for turn in turns:
        user_msg = turn.get("user_message", "")
        bot_reply = turn.get("bot_reply", "")

        # Tax Topics: match user message first for active topic
        user_matched_topic = ""
        for pat, label, key in _TAX_TOPIC_PATTERNS:
            if pat.search(user_msg):
                if label not in topics:
                    topics.append(label)
                    topic_keys.append(key)
                user_matched_topic = label

        if user_matched_topic:
            active_subject = user_matched_topic
        else:
            for pat, label, key in _TAX_TOPIC_PATTERNS:
                if pat.search(bot_reply):
                    if label not in topics:
                        topics.append(label)
                        topic_keys.append(key)
                    active_subject = label

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
        lines.append(f"Reference IDs: {', '.join(entities.reference_numbers)}.")

    # Extract key user intent queries from the older turns
    key_queries: list[str] = []
    for turn in turns:
        u = turn.get("user_message", "").strip()
        if len(u) > 5 and u not in key_queries:
            # Clean punctuation and keep short
            key_queries.append(u[:80] + ("..." if len(u) > 80 else ""))

    if key_queries:
        sampled = key_queries if len(key_queries) <= 5 else key_queries[:2] + key_queries[-3:]
        lines.append(f"Previous inquiries: {'; '.join(sampled)}.")

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
        """Construct a multi-turn context object with rolling summary and entity slots."""
        normalized = normalize_history_turns(raw_history)
        total = len(normalized)

        entities = extract_conversation_entities(normalized)

        if total <= self.recent_limit:
            recent = normalized
            summary = ""
        else:
            older_turns = normalized[:-self.recent_limit]
            recent = normalized[-self.recent_limit:]
            summary = summarize_older_turns(older_turns)

        # Cache in-session working state
        if conversation_id:
            try:
                _SESSION_WORKING_MEMORY.update(
                    conversation_id,
                    active_subject=entities.active_subject,
                    tax_topics=entities.tax_topics,
                    taxpayer_types=entities.taxpayer_types,
                    amounts=entities.amounts[-3:],
                    reference_numbers=entities.reference_numbers[-2:],
                    summary=summary,
                    turn_count=total,
                )
            except Exception:
                logger.debug("Failed to update session working memory", exc_info=True)

        return ConversationContext(
            recent_turns=recent,
            context_summary=summary,
            active_entities=entities,
            total_turns=total,
            all_turns=normalized,
        )

    def get_session_state(self, session_key: str) -> dict[str, Any] | None:
        """Retrieve active working session state."""
        if not session_key:
            return None
        return _SESSION_WORKING_MEMORY.get(session_key)


# Global helper instance
context_manager = RollingContextManager()
