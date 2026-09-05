"""URA document type classifier — rule-based + embedding similarity (2026).

Classifies scanned documents into URA-specific types for contextual
prompting in the voice+vision pipeline.

Supported document types:
    - ``receipt``             — EFRIS receipts, payment slips
    - ``tin_card``            — TIN certificates / registration cards
    - ``assessment``          — Tax assessment notices, demand notes
    - ``customs_declaration`` — Bills of entry, import permits
    - ``filing_form``         — Tax returns, annual filings, VAT returns
    - ``invoice``             — Commercial invoices, proformas
    - ``generic``             — Unrecognised documents

Feature flag: ``voice_vision_v2``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Document types
# ---------------------------------------------------------------------------


class DocumentType(str, Enum):
    RECEIPT = "receipt"
    TIN_CARD = "tin_card"
    ASSESSMENT = "assessment"
    CUSTOMS_DECLARATION = "customs_declaration"
    FILING_FORM = "filing_form"
    INVOICE = "invoice"
    STATUTORY_ACT = "statutory_act"
    GENERIC = "generic"


@dataclass(frozen=True)
class ClassificationResult:
    doc_type: DocumentType
    confidence: float  # 0.0 – 1.0
    matched_keywords: list[str]


# ---------------------------------------------------------------------------
# Keyword patterns (priority-ordered)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[DocumentType, re.Pattern, float]] = [
    # Statutory Acts, Domestic Tax Laws, and Legal Compendiums
    (
        DocumentType.STATUTORY_ACT,
        re.compile(
            r"domestic\s+tax\s+laws|tax\s+laws\s+of\s+uganda|income\s+tax\s+act|"
            r"value\s+added\s+tax\s+act|tax\s+procedures?\s+code\s+act|"
            r"reprint\s+of\s+various\s+tax\s+laws|compendium.*tax\s+laws|"
            r"statutory\s+act|acts\s+of\s+parliament|tax\s+statute|"
            r"finance\s+acts?\s+20\d{2}",
            re.I,
        ),
        0.96,
    ),
    # EFRIS and electronic receipts are high-priority
    (
        DocumentType.RECEIPT,
        re.compile(
            r"efris|e-?receipt|electronic\s+fiscal|fiscal\s+receipt|"
            r"payment\s+(slip|receipt)|vat\s+receipt|pos\s+receipt",
            re.I,
        ),
        0.92,
    ),
    # TIN registration
    (
        DocumentType.TIN_CARD,
        re.compile(
            r"tin\s*(certificate|registration|card)|"
            r"taxpayer\s+identification\s+number|"
            r"certificate\s+of\s+registration.*ura",
            re.I,
        ),
        0.90,
    ),
    # Assessment notices
    (
        DocumentType.ASSESSMENT,
        re.compile(
            r"assessment\s+notice|demand\s+note|tax\s+assessment|"
            r"notice\s+of\s+assessment|additional\s+assessment",
            re.I,
        ),
        0.88,
    ),
    # Invoices (including Tax Invoices and E-Invoices)
    (
        DocumentType.INVOICE,
        re.compile(
            r"(commercial\s+|pro\s*forma\s+)?invoice|"
            r"tax\s+invoice|vat\s+invoice|e-?invoice|fiscal\s+invoice",
            re.I,
        ),
        0.88,
    ),
    # Customs declarations
    (
        DocumentType.CUSTOMS_DECLARATION,
        re.compile(
            r"customs?\s+declaration|bill\s+of\s+entry|"
            r"import\s+permit|export\s+permit|asycuda|"
            r"single\s+(customs\s+)?entry|customs?\s+duty",
            re.I,
        ),
        0.88,
    ),
    # Filing forms, returns, and payroll schedules
    (
        DocumentType.FILING_FORM,
        re.compile(
            r"(tax\s+)?return\s+(form|filing)|annual\s+return|"
            r"vat\s+return|income\s+tax\s+return|"
            r"paye\s+(return|schedule)|cit\s+return|excise\s+return|"
            r"payroll\s+schedule",
            re.I,
        ),
        0.86,
    ),
    # Generic receipt fallback (lower priority)
    (
        DocumentType.RECEIPT,
        re.compile(r"receipt|amount\s+paid|total\s+paid|change\s+due", re.I),
        0.70,
    ),
]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classify_document(text: str) -> ClassificationResult:
    """Classify a document from its text content (OCR or VLM output).

    Uses keyword pattern matching with priority ordering.  Returns
    the highest-confidence match, or ``GENERIC`` with confidence 0.0
    if no patterns match.

    Args:
        text: Combined OCR + VLM output text.

    Returns:
        ClassificationResult with document type, confidence, and
        matched keywords.
    """
    if not text or not text.strip():
        return ClassificationResult(
            doc_type=DocumentType.GENERIC,
            confidence=0.0,
            matched_keywords=[],
        )

    best: ClassificationResult | None = None

    for doc_type, pattern, base_confidence in _PATTERNS:
        matches = pattern.findall(text)
        if matches:
            # Boost confidence by number of distinct matches (capped)
            n_unique = len(set(m.lower() if isinstance(m, str) else m for m in matches))
            confidence = min(base_confidence + (n_unique - 1) * 0.02, 0.99)

            if best is None or confidence > best.confidence:
                keywords = [m if isinstance(m, str) else m[0] for m in matches[:5]]
                best = ClassificationResult(
                    doc_type=doc_type,
                    confidence=round(confidence, 3),
                    matched_keywords=keywords,
                )

    return best or ClassificationResult(
        doc_type=DocumentType.GENERIC,
        confidence=0.0,
        matched_keywords=[],
    )


def classify_with_context(
    ocr_text: str,
    vlm_output: str,
) -> ClassificationResult:
    """Classify using both OCR and VLM outputs, preferring VLM when available."""
    # VLM output is usually more structured and reliable
    if vlm_output.strip():
        vlm_result = classify_document(vlm_output)
        if vlm_result.confidence >= 0.80:
            return vlm_result

    # Fall back to combined text
    combined = f"{vlm_output} {ocr_text}"
    return classify_document(combined)
