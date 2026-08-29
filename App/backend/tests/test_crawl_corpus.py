"""Tests for the validated crawl-page corpus.

The export needs the ``ml`` chunker; the ingest deliberately does not. The
chunker is stubbed so the selection and validation contracts are exercised
without depending on it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from app.crawl_corpus import (
    CRAWL_MANIFEST_NAME,
    split_qa_digest,
    CRAWL_MIN_PAGE_CHARS,
    export_crawl_pages_to_jsonl,
    ingest_crawl_jsonls,
    select_pages,
)
from app.faq_corpus import CorpusValidationError


@dataclass
class _FakeChunk:
    text: str
    doc_id: str = "doc"
    source: str = "page.json"
    chunk_id: int = 0
    heading_trail: list[str] = field(default_factory=list)

    @property
    def contextual_prefix(self) -> str:
        parts = [p for p in self.heading_trail if p]
        if not parts:
            return f"[Document: {self.doc_id}]"
        return f"[Document: {self.doc_id} — {' > '.join(parts[-3:])}]"


def _body(marker: str = "Guidance") -> str:
    return (f"{marker}: taxable supplies are rated under the VAT Act. ") * 12


class PageSelectionTests(unittest.TestCase):
    """Most crawled pages are navigation furniture, and the same URL is captured
    repeatedly — both would otherwise reach the index."""

    def _pages(self, root: Path, pages: list[dict]) -> Path:
        pages_dir = root / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        for i, page in enumerate(pages):
            (pages_dir / f"page{i:03d}.json").write_text(json.dumps(page), encoding="utf-8")
        return pages_dir

    def test_pages_below_the_floor_are_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir = self._pages(
                Path(temporary),
                [
                    {"url": "https://ura.go.ug/a", "text": "x" * (CRAWL_MIN_PAGE_CHARS - 1)},
                    {"url": "https://ura.go.ug/b", "text": "y" * (CRAWL_MIN_PAGE_CHARS + 1)},
                ],
            )
            selected = [page["url"] for _path, page in select_pages(pages_dir)]
            self.assertEqual(selected, ["https://ura.go.ug/b"])

    def test_only_the_newest_capture_of_a_url_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            long = "z" * (CRAWL_MIN_PAGE_CHARS + 10)
            pages_dir = self._pages(
                Path(temporary),
                [
                    {"url": "https://ura.go.ug/vat", "text": long, "timestamp": "2026-01-01T00:00:00Z", "content_hash": "old"},
                    {"url": "https://ura.go.ug/vat", "text": long, "timestamp": "2026-07-01T00:00:00Z", "content_hash": "new"},
                    {"url": "https://ura.go.ug/vat", "text": long, "timestamp": "2026-03-01T00:00:00Z", "content_hash": "mid"},
                ],
            )
            selected = select_pages(pages_dir)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0][1]["content_hash"], "new")

    def test_a_page_without_a_url_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir = self._pages(
                Path(temporary), [{"text": "q" * (CRAWL_MIN_PAGE_CHARS + 5)}]
            )
            self.assertEqual(select_pages(pages_dir), [])

    def test_malformed_page_json_is_rejected_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir = Path(temporary) / "pages"
            pages_dir.mkdir()
            (pages_dir / "broken.json").write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "invalid crawl page JSON"):
                select_pages(pages_dir)


class CrawlCorpusExportIngestTests(unittest.TestCase):
    def _export(self, root: Path, pages: list[dict], chunks: list[_FakeChunk] | None = None):
        pages_dir = root / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        for i, page in enumerate(pages):
            (pages_dir / f"page{i:03d}.json").write_text(json.dumps(page), encoding="utf-8")
        jsonl_dir = root / "crawl_jsonl"

        def fake_chunk_markdown(markdown, **kwargs):
            return iter(
                chunks
                if chunks is not None
                else [_FakeChunk(text=_body(), doc_id=kwargs.get("doc_id", "d"), heading_trail=["VAT"])]
            )

        with mock.patch.dict(
            "sys.modules",
            {"ml.scripts.data_aug.chunkers": mock.Mock(chunk_markdown=fake_chunk_markdown)},
        ):
            stats = export_crawl_pages_to_jsonl(pages_dir, jsonl_dir)
        return pages_dir, jsonl_dir, stats

    def _page(self, url: str = "https://ura.go.ug/vat", **overrides) -> dict:
        page = {
            "url": url,
            "title": "VAT - Uganda Revenue Authority",
            "text": "# VAT\n\n" + "s" * (CRAWL_MIN_PAGE_CHARS + 50),
            "content_hash": "hash-1",
            "timestamp": "2026-07-01T00:00:00Z",
        }
        page.update(overrides)
        return page

    def test_export_then_ingest_carries_url_title_and_crawl_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir, jsonl_dir, stats = self._export(Path(temporary), [self._page()])
            self.assertEqual(stats["pages_selected"], 1)
            self.assertEqual(stats["records"], 1)

            documents = ingest_crawl_jsonls(pages_dir, jsonl_dir)
            self.assertEqual(len(documents), 1)
            doc = documents[0]
            self.assertEqual(doc["doc_type"], "crawl_chunk")
            self.assertEqual(doc["url"], "https://ura.go.ug/vat")
            self.assertEqual(doc["crawled_at"], "2026-07-01T00:00:00Z")
            self.assertEqual(doc["section"], "VAT")
            self.assertTrue(doc["embed_text"].startswith("[Document:"))
            self.assertNotIn("[Document:", doc["text"])

    def test_stats_report_what_the_floor_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages = [self._page(), {"url": "https://ura.go.ug/stub", "text": "tiny"}]
            _pages_dir, _jsonl_dir, stats = self._export(Path(temporary), pages)
            self.assertEqual(stats["pages_available"], 2)
            self.assertEqual(stats["pages_selected"], 1)
            self.assertEqual(stats["dropped_below_floor"], 1)

    def test_ingest_rejects_a_page_changed_since_export(self) -> None:
        """A re-crawl changes content_hash, so withdrawn guidance cannot keep
        being served from a stale export."""
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir, jsonl_dir, _ = self._export(Path(temporary), [self._page()])
            page_path = pages_dir / "page000.json"
            page = json.loads(page_path.read_text(encoding="utf-8"))
            page["content_hash"] = "hash-2"
            page_path.write_text(json.dumps(page), encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "changed since export"):
                ingest_crawl_jsonls(pages_dir, jsonl_dir)

    def test_ingest_rejects_a_newly_crawled_page_missing_from_the_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir, jsonl_dir, _ = self._export(Path(temporary), [self._page()])
            (pages_dir / "page999.json").write_text(
                json.dumps(self._page(url="https://ura.go.ug/new", content_hash="h9")),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CorpusValidationError, "coverage mismatch"):
                ingest_crawl_jsonls(pages_dir, jsonl_dir)

    def test_ingest_rejects_hand_edited_chunk_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir, jsonl_dir, _ = self._export(Path(temporary), [self._page()])
            jsonl_path = jsonl_dir / "crawl_chunks.jsonl"
            record = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
            record["text"] = record["text"].replace("taxable", "exempt")
            jsonl_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "does not match its content"):
                ingest_crawl_jsonls(pages_dir, jsonl_dir)

    def test_ingest_rejects_an_export_made_under_different_chunking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages_dir, jsonl_dir, _ = self._export(Path(temporary), [self._page()])
            manifest_path = jsonl_dir / CRAWL_MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["chunker"]["min_page_chars"] = 1
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "different chunker parameters"):
                ingest_crawl_jsonls(pages_dir, jsonl_dir)

    def test_missing_manifest_names_the_command_that_creates_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pages").mkdir()
            (root / "crawl_jsonl").mkdir()
            with self.assertRaisesRegex(CorpusValidationError, "--export-crawl-jsonl"):
                ingest_crawl_jsonls(root / "pages", root / "crawl_jsonl")

    def test_export_fails_when_no_page_reaches_the_floor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "stub.json").write_text(
                json.dumps({"url": "https://ura.go.ug/x", "text": "tiny"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(CorpusValidationError, "reach"):
                export_crawl_pages_to_jsonl(pages_dir, root / "crawl_jsonl")

    def test_chunk_indices_stay_dense_when_a_chunk_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            chunks = [
                _FakeChunk(text=_body("A"), chunk_id=0, heading_trail=["H"]),
                _FakeChunk(text="too short", chunk_id=1, heading_trail=["H"]),
                _FakeChunk(text=_body("B"), chunk_id=2, heading_trail=["H"]),
            ]
            pages_dir, jsonl_dir, stats = self._export(
                Path(temporary), [self._page()], chunks=chunks
            )
            self.assertEqual(stats["records"], 2)
            documents = ingest_crawl_jsonls(pages_dir, jsonl_dir)
            self.assertEqual([d["chunk_index"] for d in documents], [0, 1])


if __name__ == "__main__":
    unittest.main()


class QaDigestSplitTests(unittest.TestCase):
    """The "Ask URA Commissioner General" pages are cut per question.

    Size chunking cannot see a Q&A boundary, so it packed six to nine unrelated
    taxpayer questions into one chunk. The chunk still retrieved correctly — the
    answer really was inside it — but the passage read back was whichever Q&A
    landed at the top, which is why "What taxes apply to private schools?" was
    answered with guidance about funeral service companies.
    """

    DIGEST = "\n".join(
        [
            "# Ask URA Commissioner General 5",
            "Why don’t you collect taxes from funeral service companies?",
            "Dear Reader,",
            "Any person offering funeral services must account for tax on that income.",
            "Why does URA collect taxes from private schools?",
            "Dear Reader,",
            "Private schools are in business like any other service provider and are",
            "required to account for tax on the profits earned.",
        ]
    )

    def test_each_question_becomes_its_own_unit(self) -> None:
        units = split_qa_digest(self.DIGEST)
        self.assertEqual(len(units), 2)
        self.assertEqual(
            [question for question, _ in units],
            [
                "Why don’t you collect taxes from funeral service companies?",
                "Why does URA collect taxes from private schools?",
            ],
        )

    def test_an_answer_stays_with_its_own_question(self) -> None:
        """The regression itself: the schools answer must not carry funeral text."""
        units = dict(split_qa_digest(self.DIGEST))
        schools = units["Why does URA collect taxes from private schools?"]
        self.assertIn("Private schools are in business", schools)
        self.assertNotIn("funeral", schools.lower())

    def test_the_page_heading_is_never_taken_as_a_question(self) -> None:
        questions = [question for question, _ in split_qa_digest(self.DIGEST)]
        self.assertNotIn("# Ask URA Commissioner General 5", questions)

    def test_an_ordinary_page_is_left_to_the_size_chunker(self) -> None:
        """One salutation is not a digest; returning [] keeps the old path."""
        self.assertEqual(split_qa_digest("# A page\nSome guidance.\nDear Reader,\nHello."), [])
        self.assertEqual(split_qa_digest("# A page\nNo salutation anywhere in this body."), [])

