"""Conversation topic persistence (G6).

A thread such as "I'm importing a car" followed by "what documents do I
need?" must keep the *task*, not just the last five turns. Classification
is deterministic and catalog-only: the label that reaches the system
prompt is never the user's raw words (LLM01).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Catalog labels are the only strings allowed into the prompt.
_TOPICS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("import_vehicle", "importing a vehicle", "customs", (
        r"\bimport(?:ing)?\s+(?:a\s+)?(?:car|vehicle|motor\s*vehicle)\b",
        r"\b(?:bring|bringing)\s+(?:in\s+)?(?:a\s+)?(?:car|vehicle)\b",
        r"\bused\s+car\s+import",
    )),
    ("import_goods", "importing goods", "customs", (
        r"\bimport(?:ing)?\s+(?:goods|cargo|consignment|freight)\b",
        r"\bport\s+of\s+entry\b",
        r"\bbill\s+of\s+lading\b",
        r"\bimport\s+clearance\b",
    )),
    ("tin_registration", "TIN registration", "tin", (
        r"\b(?:get|getting|apply|applying|register|registering)\s+(?:for\s+)?(?:a\s+)?tin\b",
        r"\btin\s+registration\b",
        r"\bregister\s+(?:as\s+)?(?:a\s+)?taxpayer\b",
    )),
    ("vat_registration", "VAT registration", "vat", (
        r"\bvat\s+registration\b",
        r"\bregister\s+(?:for\s+)?vat\b",
    )),
    ("vat_filing", "VAT filing", "vat", (
        r"\bvat\s+(?:return|filing|file)\b",
        r"\bfile\s+(?:my\s+)?vat\b",
    )),
    ("paye", "PAYE / employment tax", "paye", (
        r"\bpaye\b",
        r"\btake[\s-]?home\s+pay\b",
        r"\bemployment\s+tax\b",
    )),
    ("cit", "corporation tax", "cit", (
        r"\bcorporation\s+tax\b",
        r"\bcorporate\s+(?:income\s+)?tax\b",
        r"\bcit\b",
    )),
    ("wht", "withholding tax", "wht", (
        r"\bwithholding\s+tax\b",
        r"\bwht\b",
    )),
    ("cgt", "capital gains tax", "cgt", (
        r"\bcapital\s+gains?\b",
        r"\bcgt\b",
    )),
    ("efris", "EFRIS", "efris", (
        r"\befris\b",
        r"\belectronic\s+fiscal\b",
    )),
    ("payment", "making a tax payment", "", (
        r"\b(?:pay|paying|payment)\s+(?:my\s+)?(?:tax|vat|paye|ura)\b",
        r"\bhow\s+(?:do\s+i|to)\s+pay\b",
    )),
    ("refund", "a tax refund", "", (
        r"\b(?:tax\s+)?refund\b",
        r"\bvat\s+refund\b",
    )),
    ("rental_tax", "rental income tax", "rental_tax", (
        r"\brental\s+(?:income\s+)?tax\b",
        r"\brental\s+income\b",
        r"\blandlords?\b",
        r"\brenting\s+out\b",
    )),
    ("stamp_duty", "stamp duty", "stamp_duty", (
        r"\bstamp\s+duty\b",
        r"\bproperty\s+transfer\b",
        r"\bland\s+transfer\b",
    )),
    ("motor_vehicle", "motor vehicle registration", "motor_vehicle", (
        r"\bmotor\s*vehicle\b",
        r"\blogbook\b",
        r"\bnumber\s*plate\b",
        r"\bvehicle\s+transfer\b",
    )),
    ("excise_duty", "excise duty and DTS", "excise_duty", (
        r"\bexcise\s+duty\b",
        r"\bdts\b",
        r"\bdigital\s+tax\s+stamps?\b",
    )),
    ("tcc", "tax clearance certificate (TCC)", "tcc", (
        r"\btax\s+clearance\s+certificate\b",
        r"\btcc\b",
    )),
    ("objection", "tax dispute and objection", "objection", (
        r"\btax\s+objection\b",
        r"\bdispute\s+assessment\b",
        r"\btax\s+appeals\s+tribunal\b",
        r"\btat\b",
    )),
)

_COMPILED: tuple[tuple[str, str, str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (tid, label, tax, tuple(re.compile(p, re.I) for p in pats))
    for tid, label, tax, pats in _TOPICS
)

_RESET_RE = re.compile(
    r"\b("
    r"new\s+(?:question|topic|chat|conversation)|"
    r"something\s+else|"
    r"different\s+(?:topic|question)|"
    r"start\s+over|"
    r"never\s+mind|"
    r"goodbye|bye\b|see\s+you"
    r")\b",
    re.I,
)

_FOLLOWUP_RE = re.compile(
    r"^(?:what|how|when|where|which|who|and|also|then|ok|okay|yes|no|"
    r"that|those|this|it|the(?:m|se)?)\b",
    re.I,
)

_LABELS = {tid: label for tid, label, _tax, _pats in _TOPICS}


@dataclass(frozen=True)
class TopicRecord:
    topic_id: str
    label: str
    tax_type: str
    confidence: float
    updated_at: float = 0.0

    def prompt_fragment(self) -> str:
        """Catalog label only — never user text."""
        label = _LABELS.get(self.topic_id, "")
        if not label:
            return ""
        return (
            f"The user is currently working on {label}. "
            "Treat short follow-ups as continuing that task unless they "
            "clearly change subject."
        )


def classify_topic(message: str) -> TopicRecord | None:
    """Return a catalog topic when the message names a task, else None."""
    text = (message or "").strip()
    if not text or _RESET_RE.search(text):
        return None
    for topic_id, label, tax_type, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            return TopicRecord(topic_id, label, tax_type, 0.9, time.time())
    return None


def is_topic_reset(message: str) -> bool:
    return bool(_RESET_RE.search(message or ""))


def is_followup(message: str) -> bool:
    """True when the utterance is likely continuing the current task."""
    text = (message or "").strip()
    if not text or classify_topic(text) is not None:
        return False
    words = text.split()
    if len(words) <= 8:
        return True
    return bool(_FOLLOWUP_RE.match(text))


def topic_retrieval_query(topic: TopicRecord | None, query: str) -> str:
    """Prefix an anaphoric query with the catalog label so retrieval sees the task."""
    q = (query or "").strip()
    if not topic or not q or not is_followup(q):
        return q
    label = _LABELS.get(topic.topic_id, "")
    if not label:
        return q
    return f"{label}: {q}"


def resolve_topic(conversation_id: str, message: str) -> TopicRecord | None:
    """Load, update, or clear the persisted topic for *conversation_id*."""
    from . import database as db

    cid = (conversation_id or "").strip()
    if not cid:
        return classify_topic(message)

    try:
        if is_topic_reset(message):
            db.clear_conversation_topic(cid)
            return None
        detected = classify_topic(message)
        if detected:
            db.upsert_conversation_topic(
                cid,
                topic_id=detected.topic_id,
                label=detected.label,
                tax_type=detected.tax_type,
                confidence=detected.confidence,
            )
            return detected
        stored = db.get_conversation_topic(cid)
        if not stored:
            return None
        return TopicRecord(
            topic_id=str(stored.get("topic_id") or ""),
            label=str(stored.get("label") or ""),
            tax_type=str(stored.get("tax_type") or ""),
            confidence=float(stored.get("confidence") or 0.0),
            updated_at=float(stored.get("updated_at") or 0.0),
        )
    except Exception:
        logger.debug("conversation topic store failed", exc_info=True)
        return classify_topic(message)
