"""Regression tests for the JSONL-first indexing pipeline (backend/app/indexer.py).

The chunking that used to live here moved to ``ml.scripts.data_aug.chunkers``
(structure-preserving, exercised by ``tests/test_data_augmentation.py``) and
reaches the index through ``app.pdf_corpus``. What remains indexer-owned is how
a vector document is turned into embedding input and a Qdrant payload.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from app import indexer
from app.indexer import _embedding_text, _vector_payload, annotate_fiscal_year


class EmbeddingTextTests(unittest.TestCase):
    def test_pdf_chunks_embed_their_contextual_prefix(self) -> None:
        """Contextual retrieval: the prefix naming the document and section is
        embedded so an isolated chunk stays findable, while ``text`` remains the
        verbatim content that gets displayed and cited."""
        doc = {
            "text": "The standard rate is 18%.",
            "embed_text": "[Document: VAT-GUIDE — VAT > 3.1 Rates]\n\nThe standard rate is 18%.",
        }
        self.assertEqual(_embedding_text(doc), doc["embed_text"])

    def test_other_corpora_embed_their_text_verbatim(self) -> None:
        doc = {"text": "Question: What is VAT?\nAnswer: A consumption tax."}
        self.assertEqual(_embedding_text(doc), doc["text"])

    def test_blank_embed_text_falls_back_to_text(self) -> None:
        """An empty prefix must not produce an empty embedding input."""
        doc = {"text": "Rental income is taxed at 12%.", "embed_text": ""}
        self.assertEqual(_embedding_text(doc), doc["text"])


class PayloadShapeTests(unittest.TestCase):
    def test_embed_text_is_excluded_from_the_stored_payload(self) -> None:
        """``embed_text`` is an input to embedding, not retrievable content —
        storing it would duplicate every chunk body inside Qdrant."""
        doc = {
            "text": "body",
            "embed_text": "[Document: D]\n\nbody",
            "source": "d.pdf",
            "doc_type": "pdf_chunk",
        }
        payload = _vector_payload(doc)
        self.assertNotIn("embed_text", payload)
        self.assertEqual(payload["text"], "body")
        self.assertEqual(payload["doc_type"], "pdf_chunk")

    def test_payload_keeps_every_other_field_including_temporal_metadata(self) -> None:
        doc = {
            "text": "body",
            "source": "guide-FY-2024-25.pdf",
            "fiscal_year": "FY2024-25",
            "section": "Handbook > 4.3 Certainty",
            "heading_trail": ["Handbook", "4.3 Certainty"],
        }
        self.assertEqual(_vector_payload(doc), doc)


class FiscalYearAnnotationTests(unittest.TestCase):
    """Every corpus must reach the index with the same temporal field, so the
    retriever's edition preference applies uniformly rather than only to PDFs."""

    def test_derives_the_edition_from_a_faq_or_teacher_qa_source_name(self) -> None:
        documents = [
            {"source": "ura_taxation_handbook_fy2025_26_faqs.csv", "text": "q/a"},
            {"source": "808977692-Taxation-Handbook-FY-2024-25-1.pdf", "text": "q/a"},
        ]
        annotate_fiscal_year(documents)
        self.assertEqual([d["fiscal_year"] for d in documents], ["FY2025-26", "FY2024-25"])

    def test_a_source_without_an_edition_stays_empty(self) -> None:
        """Empty means unknown; the retriever must not read it as superseded."""
        documents = [{"source": "ura_vat_faqs.csv", "text": "q/a"}]
        annotate_fiscal_year(documents)
        self.assertEqual(documents[0]["fiscal_year"], "")

    def test_never_overwrites_an_edition_the_corpus_already_declared(self) -> None:
        """PDF chunks carry the value from their own manifest — a filename must
        not override it."""
        documents = [{"source": "guide-FY-2020-21.pdf", "fiscal_year": "FY2025-26", "text": "x"}]
        annotate_fiscal_year(documents)
        self.assertEqual(documents[0]["fiscal_year"], "FY2025-26")

    def test_is_idempotent_and_returns_the_same_list(self) -> None:
        documents = [{"source": "guide-FY-2024-25.pdf", "text": "x"}]
        self.assertIs(annotate_fiscal_year(documents), documents)
        annotate_fiscal_year(documents)
        self.assertEqual(documents[0]["fiscal_year"], "FY2024-25")

    def test_tolerates_a_document_with_no_source(self) -> None:
        documents = [{"text": "x"}]
        annotate_fiscal_year(documents)
        self.assertEqual(documents[0]["fiscal_year"], "")


if __name__ == "__main__":
    unittest.main()


class DenseDeviceSelectionTests(unittest.TestCase):
    """The bulk index build must honour the same device knob as the query path.

    ``SentenceTransformer`` defaults to ``cuda:0`` whenever any CUDA device is
    visible. On a shared multi-GPU host that meant a rebuild always seized GPU 0
    — the busiest card — no matter what ``RETRIEVER_DENSE_DEVICE`` said, and the
    only way to move it was ``CUDA_VISIBLE_DEVICES``. ``retriever.py`` has always
    passed the setting through; the indexer had not.
    """

    def _fake_sentence_transformers(self, calls, unavailable=()):
        class FakeST:
            def __init__(self, name, device=None):
                calls.append(device)
                if device in unavailable:
                    raise RuntimeError(f"device {device!r} unavailable")
                self.device = device

        module = types.ModuleType("sentence_transformers")
        module.SentenceTransformer = FakeST
        return module

    def _load(self, configured_device, unavailable=()):
        """Call the real ``indexer.load_dense_model`` with a stub library."""
        calls: list[object] = []
        module = self._fake_sentence_transformers(calls, unavailable)
        with mock.patch.dict(sys.modules, {"sentence_transformers": module}), \
                mock.patch.object(indexer, "DENSE_DEVICE", configured_device):
            model = indexer.load_dense_model()
        return calls, model

    def test_the_configured_device_reaches_sentence_transformers(self) -> None:
        calls, model = self._load("cuda:6")
        self.assertEqual(calls, ["cuda:6"])
        self.assertEqual(model.device, "cuda:6")

    def test_an_unset_device_preserves_the_library_default(self) -> None:
        """Deployments that never set the variable must not change behaviour:
        ``device=None`` is exactly what the call site passed before."""
        calls, _ = self._load(None)
        self.assertEqual(calls, [None])

    def test_an_unavailable_accelerator_degrades_to_cpu(self) -> None:
        calls, model = self._load("cuda:6", unavailable=("cuda:6",))
        self.assertEqual(calls, ["cuda:6", "cpu"])
        self.assertEqual(model.device, "cpu")

    def test_an_explicit_cpu_request_that_fails_stays_fatal(self) -> None:
        """There is no lower rung to fall back to, so masking this would hide a
        broken environment behind a build that silently produced no vectors."""
        with self.assertRaises(RuntimeError):
            self._load("cpu", unavailable=("cpu",))
