"""Regression tests for the PDF chunking pipeline (backend/app/indexer.py)."""

from __future__ import annotations

import unittest

from app.indexer import _chunk_text


class ChunkTextWordBoundaryTests(unittest.TestCase):
    def test_chunks_never_start_mid_word(self) -> None:
        """Reproduces a live bug: `end` is snapped to a sentence/word
        boundary, but `start = end - overlap` for the next chunk was not,
        so a chunk could begin inside the word straddling that offset (the
        production reply for "What services does URA provide?" started
        with "omes and wealth..." instead of "incomes and wealth...")."""
        paragraph = (
            "To achieve equity, the government may impose a progressive "
            "tax on the incomes and wealth of the rich. The revenue raised "
            "is then used to provide social services for the benefit of "
            "the society. "
        )
        text = paragraph * 20
        chunks = _chunk_text(text, chunk_size=800, overlap=120)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            idx = text.find(chunk[:30])
            self.assertGreaterEqual(idx, 0, chunk[:30])
            if idx > 0:
                self.assertTrue(
                    text[idx - 1].isspace(),
                    f"chunk starts mid-word: {chunk[:40]!r}",
                )

    def test_short_text_is_returned_unchanged(self) -> None:
        short = "A short passage that fits in one chunk."
        self.assertEqual(_chunk_text(short, chunk_size=800, overlap=120), [short])


if __name__ == "__main__":
    unittest.main()
