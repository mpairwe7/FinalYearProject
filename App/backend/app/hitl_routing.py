"""Human-In-The-Loop (HITL) & Document Uncertainty Routing (2026).

Maps document analysis uncertainty (unbordered fallback tables, low OCR confidence,
missing field evidence, suspicious TIN mismatches) directly into the staff
``ticket_queue`` for human officer verification without breaking taxpayer conversation flows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .flags import flags

logger = logging.getLogger(__name__)


@dataclass
class ReviewAssessment:
    """Assessment of whether a document or calculation requires human staff review."""

    requires_review: bool
    reasons: list[str] = field(default_factory=list)
    confidence_score: float = 1.0
    priority: str = "medium"  # "low", "medium", "high"
    suggested_action: str = "auto_approve"  # "auto_approve", "officer_review", "urgent_audit"


def assess_document_for_human_review(
    doc_record: Any,
    *,
    min_ocr_confidence: float = 0.70,
) -> ReviewAssessment:
    """Evaluate a DocumentRecord for extraction ambiguity and review triggers."""
    reasons: list[str] = []
    min_conf = 1.0

    # 1. Inspect extraction warnings
    warnings = getattr(doc_record, "warnings", []) or []
    for w in warnings:
        if "downscaled" not in w.lower():
            reasons.append(f"Extraction warning: {w}")

    # 2. Inspect OCR confidence & evidence
    meta = getattr(doc_record, "meta", {}) or {}
    ocr_evidence = meta.get("ocr_evidence", [])
    if ocr_evidence:
        scores = [float(ev.get("confidence", 1.0)) for ev in ocr_evidence if isinstance(ev, dict)]
        if scores:
            avg_conf = sum(scores) / len(scores)
            min_conf = min(scores)
            if avg_conf < min_ocr_confidence:
                reasons.append(f"Low average OCR confidence ({avg_conf:.2f} < {min_ocr_confidence:.2f})")

    # 3. Check for unverified TINs or disputed tax amounts
    fields = getattr(doc_record, "fields", {}) or {}
    tins = fields.get("tins", [])
    if not tins and getattr(doc_record, "doc_type", "") in {"assessment", "receipt", "return"}:
        reasons.append("Official tax document is missing detectable Taxpayer Identification Number (TIN)")

    # 4. Check table structuring review flags
    tables = getattr(doc_record, "tables", []) or []
    for t in tables:
        if isinstance(t, dict) and t.get("status") in {"needs_review", "rejected"}:
            reasons.append(f"Table {t.get('name', 'unnamed')} flagged with status: {t.get('status')}")

    requires_review = len(reasons) > 0
    priority = "high" if any("TIN" in r or "rejected" in r for r in reasons) else ("medium" if requires_review else "low")
    action = "officer_review" if requires_review else "auto_approve"

    return ReviewAssessment(
        requires_review=requires_review,
        reasons=reasons,
        confidence_score=round(min_conf, 2),
        priority=priority,
        suggested_action=action,
    )


def draft_staff_escalation_ticket(
    doc_record: Any,
    assessment: ReviewAssessment,
    *,
    taxpayer_user_id: str = "",
    session_id: str = "",
) -> dict[str, Any] | None:
    """Create a drafted human review ticket payload for the URA staff queue."""
    if not assessment.requires_review or not flags.is_enabled("ticket_queue"):
        return None

    filename = getattr(doc_record, "filename", "document.pdf")
    doc_id = getattr(doc_record, "doc_id", "")
    summary = getattr(doc_record, "summary", "")

    reasons_text = "; ".join(assessment.reasons)
    ticket_payload = {
        "title": f"Document Verification Required: {filename}",
        "reason": f"Automatic ingestion uncertainty: {reasons_text}",
        "priority": assessment.priority,
        "doc_id": doc_id,
        "filename": filename,
        "user_id": taxpayer_user_id,
        "session_id": session_id,
        "suggested_action": assessment.suggested_action,
        "summary": summary,
        "status": "pending_review",
    }
    return ticket_payload
