"""Tests for the document attachment pipeline (``app/documents.py``).

Covers per-type extraction (text / CSV / XLSX / DOCX / PDF), URA
classification + field extraction, the TTL registry with session binding,
chat grounding-passage injection, the PDF analysis report, and the
``/v1/documents`` endpoints + chat ``attachment_ids`` wiring.

Optional extractors (python-docx, openpyxl, PyMuPDF, fpdf2) are skip-guarded
so the suite stays green on lean environments — matching the app's own
graceful degradation.
"""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

# Env must be set before importing app.* (read at import time).
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app import documents  # noqa: E402
from app.main import app  # noqa: E402


def setUpModule() -> None:
    db.init_db()


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


RECEIPT_TEXT = (
    "EFRIS Electronic Fiscal Receipt\n"
    "Uganda Revenue Authority\n"
    "TIN: 1001234567\n"
    "Date: 12/05/2026\n"
    "Amount Paid: UGX 1,250,000\n"
    "Reference: URA-20260512\n"
)


def _analyze_receipt(session_id: str = "") -> documents.DocumentRecord:
    return documents.analyze_document(
        RECEIPT_TEXT.encode(), "receipt.txt", "text/plain", session_id=session_id
    )


class _RegistryIsolation(unittest.TestCase):
    """Isolate the in-memory registry AND the shared file spool per test."""

    def setUp(self) -> None:
        self._spool_dir = tempfile.mkdtemp(prefix="ura-doc-test-")
        self._spool_patch = mock.patch.object(
            documents, "_STORE_DIR", Path(self._spool_dir)
        )
        self._spool_patch.start()
        with documents._registry_lock:
            documents._registry.clear()

    def tearDown(self) -> None:
        self._spool_patch.stop()
        shutil.rmtree(self._spool_dir, ignore_errors=True)
        with documents._registry_lock:
            documents._registry.clear()


# ---------------------------------------------------------------------------
# Extraction + analysis
# ---------------------------------------------------------------------------
class TextAnalysisTest(_RegistryIsolation):
    def test_receipt_classification_and_fields(self):
        record = _analyze_receipt()
        self.assertEqual(record.kind, "text")
        self.assertEqual(record.doc_type, "receipt")
        self.assertGreater(record.confidence, 0.5)
        self.assertIn("1001234567", record.fields["tins"])
        self.assertIn("12/05/2026", record.fields["dates"])
        self.assertTrue(any("1,250,000" in a for a in record.fields["amounts"]))
        self.assertTrue(record.fields["references"])
        self.assertIn("receipt", record.summary.lower())
        self.assertRegex(record.doc_id, r"^[a-f0-9]{32}$")

    def test_empty_and_oversized_rejected(self):
        with self.assertRaises(ValueError):
            documents.analyze_document(b"", "a.txt")
        with mock.patch.object(documents, "MAX_FILE_BYTES", 4):
            with self.assertRaises(ValueError):
                documents.analyze_document(b"12345", "a.txt")

    def test_unsupported_types_rejected(self):
        with self.assertRaises(documents.UnsupportedDocumentError):
            documents.analyze_document(b"MZ", "tool.exe", "application/octet-stream")
        with self.assertRaises(documents.UnsupportedDocumentError):
            documents.analyze_document(b"\xd0\xcf", "old.xls")

    def test_text_truncation_flagged(self):
        with mock.patch.object(documents, "_MAX_TEXT_CHARS", 50):
            record = documents.analyze_document(
                ("invoice " * 40).encode(), "big.txt"
            )
        self.assertTrue(record.truncated)
        self.assertLessEqual(len(record.text), 50)


class CsvAnalysisTest(_RegistryIsolation):
    def test_csv_table_summary_and_totals(self):
        data = b"item,amount\nstamp duty,15000\ntrading licence,25000\n"
        record = documents.analyze_document(data, "fees.csv", "text/csv")
        self.assertEqual(record.kind, "csv")
        self.assertEqual(len(record.tables), 1)
        table = record.tables[0]
        self.assertEqual(table.rows, 2)
        self.assertEqual(table.headers, ["item", "amount"])
        self.assertEqual(table.numeric_totals.get("amount"), 40000.0)
        self.assertIn("[Table: CSV data]", record.text)


@unittest.skipUnless(_has("openpyxl"), "openpyxl not installed")
class XlsxAnalysisTest(_RegistryIsolation):
    def test_xlsx_sheet_summary_and_totals(self):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Taxes"
        ws.append(["tax head", "amount"])
        ws.append(["PAYE", 50000])
        ws.append(["VAT", 30000])
        buf = io.BytesIO()
        wb.save(buf)

        record = documents.analyze_document(buf.getvalue(), "taxes.xlsx")
        self.assertEqual(record.kind, "xlsx")
        self.assertEqual(record.meta.get("sheet_count"), 1)
        table = record.tables[0]
        self.assertEqual(table.name, "Taxes")
        self.assertEqual(table.rows, 2)
        self.assertEqual(table.numeric_totals.get("amount"), 80000.0)


@unittest.skipUnless(_has("docx"), "python-docx not installed")
class DocxAnalysisTest(_RegistryIsolation):
    def test_docx_paragraphs_and_classification(self):
        from docx import Document

        doc = Document()
        doc.add_paragraph("Tax Invoice No. INV-2026-001")
        doc.add_paragraph("Supplier TIN: 1009876543")
        buf = io.BytesIO()
        doc.save(buf)

        record = documents.analyze_document(buf.getvalue(), "invoice.docx")
        self.assertEqual(record.kind, "docx")
        self.assertEqual(record.doc_type, "invoice")
        self.assertIn("1009876543", record.fields["tins"])
        self.assertGreaterEqual(record.meta.get("paragraph_count", 0), 2)


@unittest.skipUnless(_has("fitz"), "PyMuPDF not installed")
class PdfAnalysisTest(_RegistryIsolation):
    def test_pdf_text_layer_extraction(self):
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(
            (72, 72), "Notice of Assessment — Uganda Revenue Authority"
        )
        page.insert_text((72, 96), "Taxpayer TIN: 1005556667")
        data = doc.tobytes()
        doc.close()

        record = documents.analyze_document(data, "assessment.pdf", "application/pdf")
        self.assertEqual(record.kind, "pdf")
        self.assertEqual(record.doc_type, "assessment")
        self.assertEqual(record.meta.get("page_count"), 1)
        self.assertIn("1005556667", record.fields["tins"])


# ---------------------------------------------------------------------------
# Registry + chat grounding passages
# ---------------------------------------------------------------------------
class RegistryTest(_RegistryIsolation):
    def test_get_document_and_session_binding(self):
        record = _analyze_receipt(session_id="sess-1")
        self.assertIs(documents.get_document(record.doc_id, session_id="sess-1"), record)
        self.assertIsNone(documents.get_document(record.doc_id, session_id="sess-2"))
        self.assertIsNone(documents.get_document(record.doc_id))
        # Unbound records (no session at upload) are fetchable by anyone
        # holding the unguessable id.
        loose = _analyze_receipt()
        self.assertIs(documents.get_document(loose.doc_id, session_id="sess-9"), loose)

    def test_ttl_expiry(self):
        record = _analyze_receipt()
        record.created_at -= documents.DOCUMENT_TTL_SECONDS + 5
        # Age the spool mirror too (both copies must expire together).
        documents._spool_write(record)
        self.assertIsNone(documents.get_document(record.doc_id))

    def test_cross_worker_lookup_via_spool(self):
        """A record stored by one worker is visible from a fresh process.

        The deployed image runs UVICORN_WORKERS=2; simulate the second
        worker by clearing this process's in-memory dict and fetching
        through the shared spool.
        """
        record = _analyze_receipt(session_id="sess-w")
        with documents._registry_lock:
            documents._registry.clear()
        fetched = documents.get_document(record.doc_id, session_id="sess-w")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.doc_id, record.doc_id)
        self.assertEqual(fetched.text, record.text)
        self.assertEqual(fetched.fields, record.fields)
        self.assertEqual(
            [t.name for t in fetched.tables], [t.name for t in record.tables]
        )
        # Session binding survives the spool round-trip.
        with documents._registry_lock:
            documents._registry.clear()
        self.assertIsNone(documents.get_document(record.doc_id, session_id="other"))

    def test_registry_size_cap_evicts_oldest(self):
        with mock.patch.object(documents, "DOCUMENT_REGISTRY_MAX", 2):
            first = _analyze_receipt()
            _analyze_receipt()
            _analyze_receipt()
        self.assertIsNone(documents.get_document(first.doc_id))

    def test_resolve_attachments_drops_missing_dedupes_and_caps(self):
        records = [_analyze_receipt() for _ in range(4)]
        ids = [r.doc_id for r in records]
        resolved = documents.resolve_attachments(
            [ids[0], ids[0], "f" * 32, *ids[1:]]
        )
        self.assertEqual(len(resolved), documents.MAX_ATTACHMENTS_PER_TURN)
        self.assertEqual(resolved[0].doc_id, ids[0])

    def test_attachment_passages_shape(self):
        record = _analyze_receipt()
        passages = documents.attachment_passages([record])
        self.assertEqual(len(passages), 1)
        hit = passages[0]
        self.assertEqual(hit["doc_type"], "attachment")
        self.assertEqual(hit["source"], "attached:receipt.txt")
        self.assertEqual(hit["chunk_id"], record.doc_id)
        self.assertEqual(hit["score_rrf"], 1.0)
        self.assertIn("EFRIS", hit["text"])
        self.assertIn("receipt.txt", hit["text"])


class ServiceInjectionTest(_RegistryIsolation):
    """Attachment passages must reach the retrieval result un-abstained."""

    def test_retrieval_only_grounds_on_attachment(self):
        from app.service import ChatModel

        model = ChatModel()
        model._cache = mock.MagicMock(name="cache")
        record = _analyze_receipt()

        result = model.generate_retrieval_only(
            message="Please summarise the attached receipt for me",
            attachments=[record],
        )
        self.assertNotIn(
            result["retrieval_mode"], ("abstained", "clarification", "blocked")
        )
        hits = result.get("_hits") or []
        self.assertTrue(hits and hits[0]["doc_type"] == "attachment")
        self.assertIn("attached:receipt.txt", result["sources"])
        self.assertTrue(result["reply"])
        # Attachment turns bypass the semantic cache entirely.
        model._cache.get.assert_not_called()
        model._cache.put.assert_not_called()

    def test_contexts_json_redacts_attachment_text(self):
        from app.service import ChatModel

        record = _analyze_receipt()
        hits = documents.attachment_passages([record])
        hits.append({"text": "VAT is 18%.", "source": "vat.pdf", "doc_type": "pdf"})
        serialized = ChatModel.contexts_json({"_hits": hits})
        self.assertNotIn("1001234567", serialized)
        self.assertIn("[user attachment: attached:receipt.txt]", serialized)
        self.assertIn("VAT is 18%.", serialized)


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------
@unittest.skipUnless(_has("fpdf"), "fpdf2 not installed")
class ReportPdfTest(_RegistryIsolation):
    def test_report_pdf_bytes(self):
        from app.pdf_export import generate_document_report_pdf

        record = _analyze_receipt()
        pdf_bytes = generate_document_report_pdf(record.to_report_payload())
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 800)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def _stub_chat_model():
    m = mock.MagicMock(name="stub_chat_model")
    m.generate.return_value = {
        "reply": "Grounded on your document.",
        "sources": ["attached:receipt.txt"],
        "citations": [],
        "faithfulness_score": 0.9,
        "retrieval_mode": "hybrid",
        "model": "stub-model",
        "conversation_id": "conv-doc-1",
        "locale": "en",
        "escalation_required": False,
        "escalation_reason": "",
        "agent_role": "rag_answerer",
        "ticket_id": "",
        "next_actions": [],
    }
    m.classify.return_value = {"predictions": [], "processing_time_ms": 1.0}
    return m


def _client() -> TestClient:
    app.state.model = _stub_chat_model()
    app.state.speech = None
    return TestClient(app)


@unittest.skipUnless(
    _has("python_multipart") or _has("multipart"), "python-multipart not installed"
)
class DocumentEndpointsTest(_RegistryIsolation):
    def _upload(self, client: TestClient, name="receipt.txt", data=None, headers=None):
        return client.post(
            "/v1/documents/analyze",
            files={"file": (name, data or RECEIPT_TEXT.encode(), "text/plain")},
            headers=headers or {},
        )

    def test_analyze_happy_path(self):
        r = self._upload(_client())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertRegex(body["document_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(body["doc_type"], "receipt")
        self.assertIn("1001234567", body["fields"]["tins"])
        self.assertTrue(body["summary"])
        self.assertGreater(body["expires_in_seconds"], 0)

    def test_analyze_unsupported_415(self):
        r = self._upload(_client(), name="tool.exe")
        self.assertEqual(r.status_code, 415)

    def test_analyze_oversize_413(self):
        with mock.patch.object(documents, "MAX_FILE_BYTES", 10):
            r = self._upload(_client())
        self.assertEqual(r.status_code, 413)

    def test_analyze_missing_file_422(self):
        r = _client().post("/v1/documents/analyze", data={"note": "no file"})
        self.assertEqual(r.status_code, 422)

    @unittest.skipUnless(_has("fpdf"), "fpdf2 not installed")
    def test_report_download_and_session_binding(self):
        client = _client()
        up = self._upload(client, headers={"X-Session-ID": "sess-a"})
        doc_id = up.json()["document_id"]

        ok = client.get(
            f"/v1/documents/{doc_id}/report", headers={"X-Session-ID": "sess-a"}
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers["content-type"], "application/pdf")
        self.assertIn("ura_document_report_", ok.headers["content-disposition"])
        self.assertTrue(ok.content.startswith(b"%PDF"))

        stranger = client.get(
            f"/v1/documents/{doc_id}/report", headers={"X-Session-ID": "sess-b"}
        )
        self.assertEqual(stranger.status_code, 404)

    def test_report_unknown_and_malformed_ids(self):
        client = _client()
        self.assertEqual(
            client.get(f"/v1/documents/{'a' * 32}/report").status_code, 404
        )
        self.assertEqual(
            client.get("/v1/documents/not-a-doc-id/report").status_code, 422
        )

    def test_chat_threads_resolved_attachments(self):
        client = _client()
        up = self._upload(client, headers={"X-Session-ID": "sess-chat"})
        doc_id = up.json()["document_id"]

        r = client.post(
            "/v1/chat",
            json={"message": "What is this document?", "attachment_ids": [doc_id]},
            headers={"X-Session-ID": "sess-chat"},
        )
        self.assertEqual(r.status_code, 200)
        attachments = app.state.model.generate.call_args.kwargs["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].doc_id, doc_id)

    def test_chat_session_mismatch_drops_attachment(self):
        client = _client()
        up = self._upload(client, headers={"X-Session-ID": "sess-owner"})
        doc_id = up.json()["document_id"]

        r = client.post(
            "/v1/chat",
            json={"message": "What is this document?", "attachment_ids": [doc_id]},
            headers={"X-Session-ID": "sess-intruder"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(app.state.model.generate.call_args.kwargs["attachments"])

    def test_chat_rejects_malformed_attachment_ids(self):
        r = _client().post(
            "/v1/chat",
            json={"message": "hi there", "attachment_ids": ["../../etc/passwd"]},
        )
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
