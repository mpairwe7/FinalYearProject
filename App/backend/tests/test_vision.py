"""Tests for the Phase 28 vision module.

Covers:
  - DocumentClassifier: keyword-based document type classification
  - OCR utilities: TIN extraction, amount extraction, date extraction
  - VisionEncoder: graceful degradation when models unavailable
  - Field extraction from combined OCR + VLM text
"""

from __future__ import annotations

import unittest


# ===========================================================================
# DocumentClassifier tests
# ===========================================================================


class TestDocumentClassifier(unittest.TestCase):
    """URA document type classification tests."""

    def test_efris_receipt(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("EFRIS Electronic Fiscal Receipt No. 123456")
        self.assertEqual(result.doc_type, DocumentType.RECEIPT)
        self.assertGreater(result.confidence, 0.85)

    def test_payment_receipt(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("Payment Receipt: Amount Paid UGX 500,000")
        self.assertEqual(result.doc_type, DocumentType.RECEIPT)

    def test_tin_certificate(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("TIN Certificate - Taxpayer Identification Number 1000123456")
        self.assertEqual(result.doc_type, DocumentType.TIN_CARD)
        self.assertGreater(result.confidence, 0.85)

    def test_assessment_notice(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("Notice of Assessment - Tax Period 2025/2026")
        self.assertEqual(result.doc_type, DocumentType.ASSESSMENT)

    def test_customs_declaration(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("Customs Declaration - Bill of Entry C12345")
        self.assertEqual(result.doc_type, DocumentType.CUSTOMS_DECLARATION)

    def test_filing_form(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("VAT Return Filing Form - Period Jan 2026")
        self.assertEqual(result.doc_type, DocumentType.FILING_FORM)

    def test_invoice(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("Tax Invoice No. INV-2026-001")
        self.assertEqual(result.doc_type, DocumentType.INVOICE)

    def test_generic_document(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("Some random text with no keywords")
        self.assertEqual(result.doc_type, DocumentType.GENERIC)
        self.assertEqual(result.confidence, 0.0)

    def test_empty_text(self):
        from app.vision.document_classifier import classify_document, DocumentType

        result = classify_document("")
        self.assertEqual(result.doc_type, DocumentType.GENERIC)
        self.assertEqual(result.confidence, 0.0)

    def test_classify_with_context_prefers_vlm(self):
        from app.vision.document_classifier import classify_with_context, DocumentType

        result = classify_with_context(
            ocr_text="some numbers 123456",
            vlm_output="TYPE: TIN Certificate\nFIELDS: TIN=1000123456",
        )
        self.assertEqual(result.doc_type, DocumentType.TIN_CARD)

    def test_classify_with_context_falls_back_to_combined(self):
        from app.vision.document_classifier import classify_with_context, DocumentType

        result = classify_with_context(
            ocr_text="EFRIS receipt total UGX 50000",
            vlm_output="",  # VLM unavailable
        )
        self.assertEqual(result.doc_type, DocumentType.RECEIPT)

    def test_multiple_matches_picks_highest_confidence(self):
        from app.vision.document_classifier import classify_document, DocumentType

        # Text mentions both receipt and invoice
        result = classify_document(
            "EFRIS Electronic Fiscal Receipt Tax Invoice Total UGX 500,000"
        )
        # EFRIS receipt pattern has higher base confidence (0.92) than invoice (0.82)
        self.assertEqual(result.doc_type, DocumentType.RECEIPT)


# ===========================================================================
# OCR utility tests
# ===========================================================================


class TestOCRUtilities(unittest.TestCase):
    """OCR post-processing utility tests."""

    def test_extract_tin_numbers(self):
        from app.vision.ocr import extract_tin_numbers

        tins = extract_tin_numbers("TIN: 1000123456 and 1999876543")
        self.assertEqual(len(tins), 2)
        self.assertIn("1000123456", tins)
        self.assertIn("1999876543", tins)

    def test_extract_tin_no_false_positives(self):
        from app.vision.ocr import extract_tin_numbers

        tins = extract_tin_numbers("Phone: 0700123456")  # starts with 0, not 1
        self.assertEqual(len(tins), 0)

    def test_extract_ugx_amounts(self):
        from app.vision.ocr import extract_ugx_amounts

        amounts = extract_ugx_amounts("Total: UGX 1,500,000.00 Tax: UGX 270,000")
        self.assertEqual(len(amounts), 2)

    def test_extract_dates(self):
        from app.vision.ocr import extract_dates

        dates = extract_dates("Date: 15/06/2026 Due: 30-06-2026")
        self.assertEqual(len(dates), 2)

    def test_extract_reference_numbers(self):
        from app.vision.ocr import extract_reference_numbers

        refs = extract_reference_numbers("Ref: ACK-123456789 Assessment: ASM/000123456")
        self.assertTrue(len(refs) >= 1)

    def test_clean_ocr_text(self):
        from app.vision.ocr import clean_ocr_text

        cleaned = clean_ocr_text("Amount:  UGX  l,5OO,OOO")
        self.assertNotIn("  ", cleaned)  # double spaces removed


# ===========================================================================
# VisionEncoder tests
# ===========================================================================


class TestVisionEncoder(unittest.TestCase):
    """VisionEncoder graceful degradation tests."""

    def test_encoder_returns_empty_on_invalid_image(self):
        from app.vision.encoder import VisionEncoder

        encoder = VisionEncoder()
        result = encoder.encode(b"not a valid image")
        self.assertEqual(result["doc_type"], "generic")
        self.assertEqual(result["confidence"], 0.0)

    def test_field_extraction_from_text(self):
        from app.vision.encoder import _extract_fields

        fields = _extract_fields(
            "TIN: 1000123456 Amount: UGX 2,500,000 Date: 15/03/2026",
            "REF: ACK-123456789",
        )
        self.assertIn("tin_numbers", fields)
        self.assertIn("amounts", fields)
        self.assertIn("dates", fields)
        self.assertEqual(fields["tin_numbers"], ["1000123456"])

    def test_doc_type_classification_from_text(self):
        from app.vision.encoder import _classify_doc_type

        self.assertEqual(
            _classify_doc_type("TYPE: receipt", "EFRIS receipt"),
            "receipt",
        )
        self.assertEqual(
            _classify_doc_type("", "TIN Certificate 1000123456"),
            "tin_card",
        )
        self.assertEqual(
            _classify_doc_type("", "random text"),
            "generic",
        )


# ===========================================================================
# Integration: Encoder → Classifier → Fields
# ===========================================================================


class TestVisionIntegration(unittest.TestCase):
    """End-to-end vision pipeline (model-free — tests classification + extraction)."""

    def test_receipt_pipeline(self):
        from app.vision.document_classifier import classify_document, DocumentType
        from app.vision.ocr import extract_ugx_amounts, extract_dates
        from app.vision.encoder import _extract_fields

        ocr_text = (
            "EFRIS Electronic Fiscal Receipt "
            "Date: 15/03/2026 "
            "Item: Office Supplies "
            "Amount: UGX 1,200,000 "
            "VAT (18%): UGX 216,000 "
            "Total: UGX 1,416,000"
        )

        classification = classify_document(ocr_text)
        self.assertEqual(classification.doc_type, DocumentType.RECEIPT)

        amounts = extract_ugx_amounts(ocr_text)
        self.assertTrue(len(amounts) >= 2)

        dates = extract_dates(ocr_text)
        self.assertEqual(len(dates), 1)

        fields = _extract_fields("", ocr_text)
        self.assertIn("amounts", fields)
        self.assertIn("dates", fields)

    def test_tin_pipeline(self):
        from app.vision.document_classifier import classify_document, DocumentType
        from app.vision.ocr import extract_tin_numbers

        ocr_text = (
            "Uganda Revenue Authority "
            "TIN Certificate "
            "Taxpayer Identification Number: 1000567890 "
            "Name: John Mukasa "
            "Date of Registration: 01/02/2020"
        )

        classification = classify_document(ocr_text)
        self.assertEqual(classification.doc_type, DocumentType.TIN_CARD)

        tins = extract_tin_numbers(ocr_text)
        self.assertEqual(tins, ["1000567890"])


if __name__ == "__main__":
    unittest.main()
