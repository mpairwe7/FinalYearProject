"""Tests for validated FAQ JSONL export and teacher-QA normalisation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.faq_corpus import (
    CorpusValidationError,
    export_faq_csvs_to_jsonl,
    ingest_faq_jsonls,
    ingest_teacher_qa_jsonls,
)


class FaqJsonlCorpusTests(unittest.TestCase):
    def _write_csv(self, directory: Path, name: str, rows: list[tuple[str, str]]) -> Path:
        path = directory / name
        path.write_text(
            "question,answer\n" + "".join(f'"{question}","{answer}"\n' for question, answer in rows),
            encoding="utf-8",
        )
        return path

    def test_export_and_ingest_requires_full_fresh_csv_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_dir, jsonl_dir = root / "csv", root / "faq_jsonl"
            csv_dir.mkdir()
            self._write_csv(csv_dir, "ura_vat_faqs.csv", [("What is VAT?", "A consumption tax.")])
            self._write_csv(csv_dir, "ura_tin_faqs.csv", [("What is a TIN?", "A taxpayer number.")])

            self.assertEqual(
                export_faq_csvs_to_jsonl(csv_dir, jsonl_dir),
                {"sources": 2, "source_rows": 2, "records": 2, "duplicates_removed": 0},
            )
            documents = ingest_faq_jsonls(csv_dir, jsonl_dir)
            self.assertEqual(len(documents), 2)
            self.assertEqual({doc["doc_type"] for doc in documents}, {"faq_jsonl"})
            self.assertEqual({doc["source"] for doc in documents}, {"ura_vat_faqs.csv", "ura_tin_faqs.csv"})
            self.assertTrue(all(doc["source_sha256"] and doc["chunk_id"] for doc in documents))

            self._write_csv(csv_dir, "ura_vat_faqs.csv", [("What is VAT?", "Changed answer.")])
            with self.assertRaisesRegex(CorpusValidationError, "regenerate"):
                ingest_faq_jsonls(csv_dir, jsonl_dir)

    def test_export_deduplicates_normalized_questions_with_manifest_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_dir, jsonl_dir = root / "csv", root / "faq_jsonl"
            csv_dir.mkdir()
            self._write_csv(
                csv_dir,
                "ura_edition_faqs.csv",
                [("What is VAT?", "A short VAT definition.")],
            )
            self._write_csv(
                csv_dir,
                "ura_vat_faqs.csv",
                [("  what   is VAT?  ", "A longer, more complete VAT definition.")],
            )

            stats = export_faq_csvs_to_jsonl(csv_dir, jsonl_dir)
            self.assertEqual(
                stats,
                {"sources": 2, "source_rows": 2, "records": 1, "duplicates_removed": 1},
            )
            documents = ingest_faq_jsonls(csv_dir, jsonl_dir)
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["source"], "ura_vat_faqs.csv")
            self.assertEqual(documents[0]["duplicate_question_count"], 2)

            manifest = json.loads((jsonl_dir / "faq_corpus_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["duplicates_removed"], 1)
            self.assertEqual(manifest["duplicates"][0]["retained"]["source"], "ura_vat_faqs.csv")
            self.assertEqual(manifest["duplicates"][0]["removed"][0]["source"], "ura_edition_faqs.csv")

    def test_normalises_and_deduplicates_supported_teacher_qa_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            question, answer, source = "What is VAT?", "VAT is a consumption tax.", "guide.pdf"
            records = {
                "teacher_qa.jsonl": {
                    "question": question,
                    "answer": answer,
                    "source_pdf": source,
                    "chunk_id": 4,
                    "chunk_text": "Longer supporting evidence.",
                    "question_type": "factual",
                },
                "teacher_qa_instruction.jsonl": {
                    "instruction": question,
                    "input": "",
                    "output": answer,
                    "context": "Short evidence.",
                    "source": source,
                    "type": "factual",
                },
                "teacher_qa_qwen.jsonl": {
                    "messages": [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ],
                    "source": source,
                    "type": "factual",
                },
                "teacher_qa_gemma.jsonl": {
                    "text": f"<start_of_turn>user\n{question}<end_of_turn>\n"
                    f"<start_of_turn>model\n{answer}<end_of_turn>",
                    "source": source,
                    "type": "factual",
                },
            }
            for filename, record in records.items():
                (directory / filename).write_text(json.dumps(record) + "\n", encoding="utf-8")

            documents = ingest_teacher_qa_jsonls(directory)
            self.assertEqual(len(documents), 1)
            document = documents[0]
            self.assertEqual(document["doc_type"], "teacher_qa_jsonl")
            self.assertEqual(document["source"], source)
            self.assertEqual(
                document["source_formats"],
                ["chat_messages", "gemma_turns", "instruction_output", "question_answer"],
            )
            self.assertIn("Longer supporting evidence", document["text"])

    def test_rejects_malformed_teacher_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "teacher_qa.jsonl"
            path.write_text('{"question": "bad"\n', encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "invalid JSONL"):
                ingest_teacher_qa_jsonls(path.parent)


if __name__ == "__main__":
    unittest.main()
