"""Tests for the validated PDF-chunk corpus (export, ingest, normalisation).

The export stage needs ``pymupdf4llm`` and the ``ml`` chunker; the ingest stage
deliberately needs neither. These tests therefore stub the chunker so the whole
contract is exercised without a real PDF or the heavy extraction dependency.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from app.faq_corpus import CorpusValidationError
from app.pdf_corpus import (
    PDF_MANIFEST_NAME,
    export_pdf_chunks_to_jsonl,
    fiscal_year_from_name,
    ingest_pdf_jsonls,
    normalise_extracted_text,
    normalise_heading,
)


@dataclass
class _FakeChunk:
    """Mirrors ``ml.scripts.data_aug.chunkers.Chunk``'s retrieval surface."""

    text: str
    doc_id: str = "doc"
    source: str = "doc.pdf"
    chunk_id: int = 0
    heading_trail: list[str] = field(default_factory=list)

    @property
    def contextual_prefix(self) -> str:
        parts = [p for p in self.heading_trail if p]
        if not parts:
            return f"[Document: {self.doc_id}]"
        return f"[Document: {self.doc_id} — {' > '.join(parts[-3:])}]"


def _body(marker: str, length: int = 260) -> str:
    """Return prose comfortably above the min-chars floor."""
    return (f"{marker} taxable income is assessed under the Income Tax Act. ") * (length // 55 + 1)


class FiscalYearParsingTests(unittest.TestCase):
    def test_parses_the_forms_that_appear_in_the_corpus(self) -> None:
        cases = {
            "ura.go.ug-Withholding-Tax-FY-2024-25-1": "FY2024-25",
            "TAXATION-HANDBOOK-FY-2025-26-1": "FY2025-26",
            "Taxation-handbook-FY2023-24": "FY2023-24",
            "A-guide-to-taxation-of-the-Hotel-Sector-2025-26": "FY2025-26",
            "Aguide-To-Taxation-Of-The-Wholesale-And-Retail-Sector22-23-1": "FY2022-23",
            "ura.go.ug-A-GUIDE-VOL-1-ISSUE-4-FY-2025-2026": "FY2025-26",
        }
        for stem, expected in cases.items():
            self.assertEqual(fiscal_year_from_name(stem), expected, stem)

    def test_rejects_non_fiscal_digit_runs(self) -> None:
        """Only consecutive year pairs count, so ids and leaflet numbers do not."""
        for stem in (
            "808977692-Taxation-Handbook",
            "ura.go.ug-10-Quick-Facts-about-Stamp-Duty",
            "TAXATION-OF-THE-EDUCATION-SECTOR",
            "Guidelines For Financial Clearance Final Jun2025",
        ):
            self.assertEqual(fiscal_year_from_name(stem), "", stem)


class ExtractionNormalisationTests(unittest.TestCase):
    def test_restores_decimal_points_encoded_as_replacement_chars(self) -> None:
        self.assertEqual(normalise_extracted_text("4�3 Certainty"), "4.3 Certainty")

    def test_collapses_table_of_contents_leader_runs(self) -> None:
        self.assertEqual(normalise_extracted_text("Foreword � � � � � � 6"), "Foreword")
        self.assertEqual(normalise_extracted_text("Foreword . . . . . . 6"), "Foreword")

    def test_preserves_newlines_so_markdown_tables_survive(self) -> None:
        table = "| Band | Rate |\n| --- | --- |\n| First 235,000 | 0% |"
        self.assertEqual(normalise_extracted_text(table), table)

    def test_leaves_ordinary_prose_and_decimals_untouched(self) -> None:
        prose = "Section 1.2.3 applies. See a. b. c. below."
        self.assertEqual(normalise_extracted_text(prose), prose)

    def test_strips_figure_placeholders(self) -> None:
        """pymupdf4llm replaces every figure with a literal placeholder line. 956 of
        7,035 chunks carried one, and a query with no real match could be answered
        with "picture [468 x 294] intentionally omitted" as its evidence — observed
        live for "write me a poem about cats"."""
        self.assertEqual(
            normalise_extracted_text("Intro text **==> picture [468 x 294] intentionally omitted more text"),
            "Intro text more text",
        )
        # A chunk that is nothing but placeholders collapses to empty, so the
        # min-chars floor drops it instead of indexing noise.
        self.assertEqual(normalise_extracted_text("picture [100 x 200] intentionally omitted"), "")
        self.assertEqual(normalise_extracted_text("table [10 x 20] INTENTIONALLY OMITTED"), "")

    def test_real_prose_and_tables_survive_placeholder_stripping(self) -> None:
        self.assertEqual(normalise_extracted_text("The rate is 18% on supplies."), "The rate is 18% on supplies.")
        table = "| Band | Rate |\n| --- | --- |"
        self.assertEqual(normalise_extracted_text(table), table)

    def test_heading_normalisation_strips_markdown_emphasis(self) -> None:
        self.assertEqual(normalise_heading("**4�3 Certainty**"), "4.3 Certainty")


class PdfCorpusExportIngestTests(unittest.TestCase):
    def _pdf(self, directory: Path, name: str, payload: bytes = b"%PDF-1.7 fixture") -> Path:
        path = directory / name
        path.write_bytes(payload)
        return path

    def _export(self, root: Path, chunks_by_pdf: dict[str, list[_FakeChunk]]):
        pdf_dir, jsonl_dir = root / "pdfs", root / "pdf_jsonl"
        pdf_dir.mkdir(exist_ok=True)
        for name in chunks_by_pdf:
            # Distinct bytes per fixture: identical payloads would be collapsed
            # by the byte-identical-duplicate check, which is exercised on its
            # own in test_byte_identical_pdfs_are_chunked_once.
            self._pdf(pdf_dir, name, f"%PDF-1.7 {name}".encode())

        def fake_chunk_pdf(pdf_path, **_kwargs):
            return iter(chunks_by_pdf[pdf_path.name])

        with mock.patch.dict(
            "sys.modules",
            {"ml.scripts.data_aug.chunkers": mock.Mock(chunk_pdf=fake_chunk_pdf)},
        ):
            stats = export_pdf_chunks_to_jsonl(pdf_dir, jsonl_dir)
        return pdf_dir, jsonl_dir, stats

    def test_export_then_ingest_carries_structure_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = [
                _FakeChunk(
                    text=_body("Employment"),
                    doc_id="ura-Withholding-Tax-FY-2024-25",
                    chunk_id=0,
                    heading_trail=["HANDBOOK", "**4�1 Equity**"],
                ),
                _FakeChunk(
                    text=_body("Rental"),
                    doc_id="ura-Withholding-Tax-FY-2024-25",
                    chunk_id=1,
                    heading_trail=["HANDBOOK", "4.2 Certainty"],
                ),
            ]
            pdf_dir, jsonl_dir, stats = self._export(
                root, {"ura-Withholding-Tax-FY-2024-25.pdf": chunks}
            )
            self.assertEqual(
                stats,
                {
                    "sources": 1,
                    "unique_sources": 1,
                    "duplicates_skipped": 0,
                    "records": 2,
                    "empty_sources": 0,
                    "unknown_fiscal_year": 0,
                },
            )

            documents = ingest_pdf_jsonls(pdf_dir, jsonl_dir)
            self.assertEqual(len(documents), 2)
            self.assertEqual({doc["doc_type"] for doc in documents}, {"pdf_chunk"})
            self.assertEqual({doc["fiscal_year"] for doc in documents}, {"FY2024-25"})
            # Heading emphasis and the replacement-char decimal are cleaned, and
            # the trail becomes the citation locator.
            self.assertEqual(documents[0]["section"], "HANDBOOK > 4.1 Equity")
            # Contextual prefix is embedded but not part of the cited text.
            self.assertTrue(documents[0]["embed_text"].startswith("[Document:"))
            self.assertNotIn("[Document:", documents[0]["text"])
            self.assertTrue(documents[0]["embed_text"].endswith(documents[0]["text"]))
            self.assertTrue(all(doc["source_sha256"] and doc["chunk_id"] for doc in documents))

    def test_ingest_rejects_a_pdf_changed_since_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))]}
            )
            (pdf_dir / "guide-FY-2024-25.pdf").write_bytes(b"%PDF-1.7 replaced")
            with self.assertRaisesRegex(CorpusValidationError, "source changed"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_ingest_rejects_a_pdf_added_after_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))]}
            )
            self._pdf(pdf_dir, "late-arrival.pdf")
            with self.assertRaisesRegex(CorpusValidationError, "coverage mismatch"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_ingest_rejects_hand_edited_chunk_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))]}
            )
            jsonl_path = jsonl_dir / "guide-FY-2024-25.jsonl"
            record = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
            record["text"] = record["text"].replace("taxable", "tax-free")
            jsonl_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "does not match its content"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_ingest_rejects_a_truncated_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root,
                {
                    "guide-FY-2024-25.pdf": [
                        _FakeChunk(text=_body("A"), chunk_id=0),
                        _FakeChunk(text=_body("B"), chunk_id=1),
                    ]
                },
            )
            jsonl_path = jsonl_dir / "guide-FY-2024-25.jsonl"
            first = jsonl_path.read_text(encoding="utf-8").splitlines()[0]
            jsonl_path.write_text(first + "\n", encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "expected 2 records, found 1"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_ingest_rejects_an_export_made_under_different_chunking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))]}
            )
            manifest_path = jsonl_dir / PDF_MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["chunker"]["target_chars"] = 99999
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "different chunker parameters"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_missing_manifest_names_the_command_that_creates_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir = root / "pdfs", root / "pdf_jsonl"
            pdf_dir.mkdir()
            jsonl_dir.mkdir()
            self._pdf(pdf_dir, "guide.pdf")
            with self.assertRaisesRegex(CorpusValidationError, "--export-pdf-jsonl"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_chunks_below_the_floor_are_dropped_and_indices_stay_dense(self) -> None:
        """A table-of-contents page cleans down to nothing and must not be
        indexed; the surviving chunks still need gap-free indices because
        ``chunk_id`` is re-derived from them on ingest."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = [
                _FakeChunk(text=_body("Real"), chunk_id=0, heading_trail=["H"]),
                _FakeChunk(text="Foreword � � � � � � 6", chunk_id=1, heading_trail=["H"]),
                _FakeChunk(text=_body("Also real"), chunk_id=2, heading_trail=["H"]),
            ]
            pdf_dir, jsonl_dir, stats = self._export(root, {"guide-FY-2024-25.pdf": chunks})
            self.assertEqual(stats["records"], 2)
            documents = ingest_pdf_jsonls(pdf_dir, jsonl_dir)
            self.assertEqual([doc["chunk_index"] for doc in documents], [0, 1])

    def test_a_pdf_yielding_no_chunks_is_reported_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, stats = self._export(
                root,
                {
                    "scanned-image-only.pdf": [],
                    "guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))],
                },
            )
            self.assertEqual(stats["empty_sources"], 1)
            self.assertEqual(stats["unknown_fiscal_year"], 1)
            manifest = json.loads((jsonl_dir / PDF_MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["empty_sources"], ["scanned-image-only.pdf"])
            # The empty source still ingests cleanly — it contributes no rows.
            self.assertEqual(len(ingest_pdf_jsonls(pdf_dir, jsonl_dir)), 1)

    def test_byte_identical_pdfs_are_chunked_once(self) -> None:
        """The corpus ships the same document with and without the crawl prefix.
        Chunking both would embed every passage twice and let one passage take
        several top_k slots."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir = root / "pdfs", root / "pdf_jsonl"
            pdf_dir.mkdir()
            same = b"%PDF-1.7 identical bytes"
            self._pdf(pdf_dir, "GUIDE-FY-2024-25.pdf", same)
            self._pdf(pdf_dir, "ura.go.ug-GUIDE-FY-2024-25.pdf", same)
            self._pdf(pdf_dir, "OTHER-FY-2024-25.pdf", b"%PDF-1.7 different")

            def fake_chunk_pdf(pdf_path, **_kwargs):
                return iter([_FakeChunk(text=_body(pdf_path.stem), heading_trail=["H"])])

            with mock.patch.dict(
                "sys.modules",
                {"ml.scripts.data_aug.chunkers": mock.Mock(chunk_pdf=fake_chunk_pdf)},
            ):
                stats = export_pdf_chunks_to_jsonl(pdf_dir, jsonl_dir)

            self.assertEqual(stats["sources"], 3)
            self.assertEqual(stats["unique_sources"], 2)
            self.assertEqual(stats["duplicates_skipped"], 1)
            self.assertEqual(stats["records"], 2)

            manifest = json.loads((jsonl_dir / PDF_MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["duplicate_sources"], ["ura.go.ug-GUIDE-FY-2024-25.pdf"])
            # The first name sorted wins, so the prefixed copy is the duplicate.
            dupe = next(s for s in manifest["sources"] if s.get("duplicate_of"))
            self.assertEqual(dupe["duplicate_of"], "GUIDE-FY-2024-25.pdf")
            self.assertEqual(dupe["records"], 0)

            # Ingest still validates full coverage and yields one copy.
            documents = ingest_pdf_jsonls(pdf_dir, jsonl_dir)
            self.assertEqual(len(documents), 2)
            self.assertNotIn("ura.go.ug-GUIDE-FY-2024-25.pdf", {d["source"] for d in documents})

    def test_a_duplicate_whose_bytes_change_is_still_caught(self) -> None:
        """The duplicate claim is verified, not trusted: its hash is checked even
        though it contributes no rows."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir = root / "pdfs", root / "pdf_jsonl"
            pdf_dir.mkdir()
            same = b"%PDF-1.7 identical bytes"
            self._pdf(pdf_dir, "GUIDE-FY-2024-25.pdf", same)
            self._pdf(pdf_dir, "ura.go.ug-GUIDE-FY-2024-25.pdf", same)

            def fake_chunk_pdf(pdf_path, **_kwargs):
                return iter([_FakeChunk(text=_body("A"), heading_trail=["H"])])

            with mock.patch.dict(
                "sys.modules",
                {"ml.scripts.data_aug.chunkers": mock.Mock(chunk_pdf=fake_chunk_pdf)},
            ):
                export_pdf_chunks_to_jsonl(pdf_dir, jsonl_dir)

            (pdf_dir / "ura.go.ug-GUIDE-FY-2024-25.pdf").write_bytes(b"%PDF-1.7 now different")
            with self.assertRaisesRegex(CorpusValidationError, "source changed"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_source_free_ingest_is_refused_by_default(self) -> None:
        """Losing the sources must not silently weaken validation."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))]}
            )
            (pdf_dir / "guide-FY-2024-25.pdf").unlink()
            with self.assertRaisesRegex(CorpusValidationError, "No PDF files found"):
                ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_trust_manifest_ingests_without_the_source_pdfs(self) -> None:
        """The serving image ships the derived JSONL but not 500 MB of PDFs."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"), heading_trail=["H"])]}
            )
            (pdf_dir / "guide-FY-2024-25.pdf").unlink()
            with mock.patch("app.pdf_corpus.TRUST_MANIFEST", True):
                documents = ingest_pdf_jsonls(pdf_dir, jsonl_dir)
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["fiscal_year"], "FY2024-25")

    def test_trust_manifest_still_rejects_a_source_that_is_present_and_changed(self) -> None:
        """Trust applies only where verification is impossible. A source that IS
        on disk is still hashed, so the relaxation cannot be used to skip a real
        staleness check."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))]}
            )
            (pdf_dir / "guide-FY-2024-25.pdf").write_bytes(b"%PDF-1.7 replaced")
            with mock.patch("app.pdf_corpus.TRUST_MANIFEST", True):
                with self.assertRaisesRegex(CorpusValidationError, "source changed"):
                    ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_trust_manifest_still_rejects_edited_jsonl(self) -> None:
        """Everything internal to the export stays verified under trust mode."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_dir, jsonl_dir, _ = self._export(
                root, {"guide-FY-2024-25.pdf": [_FakeChunk(text=_body("A"))]}
            )
            (pdf_dir / "guide-FY-2024-25.pdf").unlink()
            jsonl_path = jsonl_dir / "guide-FY-2024-25.jsonl"
            record = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
            record["text"] = record["text"].replace("taxable", "exempt")
            jsonl_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
            with mock.patch("app.pdf_corpus.TRUST_MANIFEST", True):
                with self.assertRaisesRegex(CorpusValidationError, "does not match its content"):
                    ingest_pdf_jsonls(pdf_dir, jsonl_dir)

    def test_export_requires_at_least_one_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pdfs").mkdir()
            with self.assertRaisesRegex(CorpusValidationError, "No PDF files found"):
                export_pdf_chunks_to_jsonl(root / "pdfs", root / "pdf_jsonl")


if __name__ == "__main__":
    unittest.main()
