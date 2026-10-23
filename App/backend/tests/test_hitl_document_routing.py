"""Unit tests for Human-In-The-Loop (HITL) document uncertainty assessment and ticket drafting."""

from __future__ import annotations

import time
from unittest import mock

from app.documents import DocumentRecord
from app.hitl_routing import assess_document_for_human_review, draft_staff_escalation_ticket


def test_clear_document_requires_no_human_review() -> None:
    doc = DocumentRecord(
        doc_id="doc_clean",
        filename="assessment.pdf",
        kind="pdf",
        size_bytes=1024,
        doc_type="assessment",
        confidence=0.95,
        matched_keywords=["assessment", "ura"],
        text="Clean Assessment Notice",
        truncated=False,
        fields={"tins": ["1001234567"]},
        tables=[],
        meta={"ocr_evidence": [{"confidence": 0.98}]},
        summary="Everything clear",
        warnings=[],
        created_at=time.time(),
    )
    assessment = assess_document_for_human_review(doc)
    assert assessment.requires_review is False
    assert assessment.suggested_action == "auto_approve"

    # Ticket is not drafted when no review is needed
    with mock.patch("app.hitl_routing.flags.is_enabled", return_value=True):
        ticket = draft_staff_escalation_ticket(doc, assessment)
        assert ticket is None


def test_uncertain_document_triggers_staff_review_draft() -> None:
    doc = DocumentRecord(
        doc_id="doc_uncertain",
        filename="scanned_receipt.png",
        kind="image",
        size_bytes=4096,
        doc_type="receipt",
        confidence=0.50,
        matched_keywords=["receipt"],
        text="Blurry receipt text",
        truncated=False,
        fields={"tins": []},  # Missing TIN on tax receipt
        tables=[],
        meta={"ocr_evidence": [{"confidence": 0.45}]},
        summary="Low quality scan",
        warnings=["Low resolution input scan"],
        created_at=time.time(),
    )
    assessment = assess_document_for_human_review(doc, min_ocr_confidence=0.70)
    assert assessment.requires_review is True
    assert assessment.suggested_action == "officer_review"
    assert len(assessment.reasons) >= 2

    with mock.patch("app.hitl_routing.flags.is_enabled", return_value=True):
        ticket = draft_staff_escalation_ticket(
            doc,
            assessment,
            taxpayer_user_id="usr-test-1",
            session_id="sess-test-1",
        )
        assert ticket is not None
        assert ticket["doc_id"] == "doc_uncertain"
        assert ticket["status"] == "pending_review"
        assert ticket["user_id"] == "usr-test-1"
        assert "Automatic ingestion uncertainty" in ticket["reason"]


def test_table_summary_objects_and_tax_discrepancies_trigger_hitl_review() -> None:
    from app.documents import TableSummary

    doc = DocumentRecord(
        doc_id="doc_recon_fail",
        filename="disputed_invoice.pdf",
        kind="pdf",
        size_bytes=2048,
        doc_type="invoice",
        confidence=0.92,
        matched_keywords=["invoice"],
        text="Subtotal: UGX 1,000,000 VAT: UGX 180,000 Total: UGX 1,500,000",
        truncated=False,
        fields={"tins": ["1001234567"]},
        tables=[
            TableSummary(
                name="Page 1 table_fallback",
                rows=3,
                cols=2,
                headers=["Item", "Amount"],
                source="vlm",
                status="ok",
                downstream_action="use_with_warning",
                warnings=["missing_line_candidate", "vlm_fallback"],
            )
        ],
        meta={},
        summary="Invoice with discrepancy",
        warnings=[],
        created_at=time.time(),
        tax_reconciliation={
            "status": "discrepancy_detected",
            "notes": ["Arithmetic discrepancy detected: variance UGX 320,000"],
        },
    )
    assessment = assess_document_for_human_review(doc)
    assert assessment.requires_review is True
    assert assessment.priority == "high"
    assert assessment.suggested_action == "urgent_audit"
    assert any("unbordered fallback" in r for r in assessment.reasons)
    assert any("Tax reconciliation discrepancy" in r for r in assessment.reasons)
