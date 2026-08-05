"""Validated local corpus builders for FAQ and teacher-QA vector documents.

The vector corpus deliberately contains only:

* canonical FAQ JSONL exported from ``ura_*_faqs.csv``; and
* teacher-QA JSONL generated from the PDF source material.

PDFs and evaluation/red-team JSONL files are not vector inputs.  The exporter
writes source hashes and stable record ids, allowing the indexer to reject a
partial or stale JSONL export instead of silently indexing an incomplete FAQ
corpus.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


FAQ_JSONL_SCHEMA_VERSION = 2
FAQ_MANIFEST_NAME = "faq_corpus_manifest.json"
FAQ_DEDUPLICATION_POLICY = "normalized_question_longest_answer_then_source_then_row"
_GEMMA_TURN_RE = re.compile(
    r"<start_of_turn>user\s*(.*?)<end_of_turn>\s*"
    r"<start_of_turn>model\s*(.*?)<end_of_turn>",
    re.DOTALL,
)


class CorpusValidationError(ValueError):
    """Raised when a local vector-corpus input is incomplete or invalid."""


def _clean(value: object) -> str:
    return str(value or "").strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(*parts: object) -> str:
    raw = "\x1f".join(_clean(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def _faq_tag(csv_path: Path) -> str:
    return csv_path.stem.removeprefix("ura_").removesuffix("_faqs")


def _csv_rows(csv_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        if not ({"question", "answer"} <= fields or {"Question", "Answer"} <= fields):
            raise CorpusValidationError(f"{csv_path}: expected question/answer columns")
        for row_number, row in enumerate(reader, 2):
            question = _clean(row.get("question") or row.get("Question"))
            answer = _clean(row.get("answer") or row.get("Answer"))
            if not question or not answer:
                raise CorpusValidationError(f"{csv_path}:{row_number}: question and answer are required")
            rows.append({"question": question, "answer": answer, "row_number": row_number})
    if not rows:
        raise CorpusValidationError(f"{csv_path}: contains no FAQ rows")
    return rows


def _question_key(question: str) -> str:
    """Return the stable comparison key used to deduplicate FAQ questions."""
    return " ".join(_clean(question).casefold().split())


def _canonical_faq_records(
    csv_paths: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Build canonical per-source records and a provenance audit for duplicates.

    Raw CSV files remain intact.  The vector corpus has one record per
    normalized question, retaining the longest answer and resolving equal
    answers deterministically by source name and source row.  The manifest
    records every discarded row so that deduplication is auditable.
    """
    sources: list[dict[str, Any]] = []
    candidates: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    records_by_source: dict[str, list[dict[str, Any]]] = {}

    for csv_path in csv_paths:
        source_sha256 = _sha256_file(csv_path)
        source_rows = _csv_rows(csv_path)
        source = csv_path.name
        records_by_source[source] = []
        sources.append(
            {
                "path": csv_path,
                "source": source,
                "sha256": source_sha256,
                "tag": _faq_tag(csv_path),
                "source_rows": len(source_rows),
            }
        )
        for row in source_rows:
            question, answer, row_number = row["question"], row["answer"], row["row_number"]
            candidates[_question_key(question)].append(
                {
                    "schema_version": FAQ_JSONL_SCHEMA_VERSION,
                    "record_type": "faq",
                    "chunk_id": _stable_id(source, row_number, question, answer),
                    "question": question,
                    "answer": answer,
                    "source": source,
                    "source_sha256": source_sha256,
                    "tag": _faq_tag(csv_path),
                    "row_number": row_number,
                }
            )

    duplicate_audit: list[dict[str, Any]] = []
    for question_key in sorted(candidates):
        ranked = sorted(
            candidates[question_key],
            key=lambda record: (
                -len(record["answer"]),
                record["source"].casefold(),
                int(record["row_number"]),
            ),
        )
        retained = dict(ranked[0])
        retained["duplicate_question_count"] = len(ranked)
        records_by_source[retained["source"]].append(retained)
        if len(ranked) > 1:
            duplicate_audit.append(
                {
                    "question": retained["question"],
                    "question_key": question_key,
                    "retained": {
                        "source": retained["source"],
                        "row_number": retained["row_number"],
                    },
                    "removed": [
                        {"source": record["source"], "row_number": record["row_number"]}
                        for record in ranked[1:]
                    ],
                }
            )

    for records in records_by_source.values():
        records.sort(key=lambda record: int(record["row_number"]))
    return sources, records_by_source, duplicate_audit


def export_faq_csvs_to_jsonl(csv_dir: Path, jsonl_dir: Path) -> dict[str, int]:
    """Create one canonical JSONL file per FAQ CSV plus a coverage manifest.

    The output is deterministic: unchanged CSV bytes produce unchanged JSONL
    records and manifest entries.  Files are replaced atomically so a failed
    export cannot leave a partially-written corpus for the indexer.
    """
    csv_paths = sorted(csv_dir.glob("ura_*_faqs.csv"))
    if not csv_paths:
        raise CorpusValidationError(f"No ura_*_faqs.csv files found in {csv_dir}")

    jsonl_dir.mkdir(parents=True, exist_ok=True)
    source_data, records_by_source, duplicate_audit = _canonical_faq_records(csv_paths)
    manifest_sources: list[dict[str, Any]] = []

    for source in source_data:
        csv_path = source["path"]
        records = records_by_source[source["source"]]
        output_path = jsonl_dir / f"{csv_path.stem}.jsonl"
        temporary_path = output_path.with_suffix(".jsonl.tmp")
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        temporary_path.replace(output_path)

        manifest_sources.append(
            {
                "csv": source["source"],
                "jsonl": output_path.name,
                "sha256": source["sha256"],
                "source_rows": source["source_rows"],
                "records": len(records),
                "duplicates_removed": source["source_rows"] - len(records),
            }
        )

    source_row_count = sum(source["source_rows"] for source in source_data)
    total_records = sum(len(records) for records in records_by_source.values())

    manifest = {
        "schema_version": FAQ_JSONL_SCHEMA_VERSION,
        "deduplication_policy": FAQ_DEDUPLICATION_POLICY,
        "source_count": len(manifest_sources),
        "source_row_count": source_row_count,
        "record_count": total_records,
        "duplicates_removed": source_row_count - total_records,
        "sources": manifest_sources,
        "duplicates": duplicate_audit,
    }
    manifest_path = jsonl_dir / FAQ_MANIFEST_NAME
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    with temporary_manifest.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    temporary_manifest.replace(manifest_path)

    return {
        "sources": len(manifest_sources),
        "source_rows": source_row_count,
        "records": total_records,
        "duplicates_removed": source_row_count - total_records,
    }


def _read_json_lines(path: Path) -> list[tuple[int, dict[str, Any]]]:
    records: list[tuple[int, dict[str, Any]]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorpusValidationError(f"{path}:{line_number}: invalid JSONL ({exc.msg})") from exc
            if not isinstance(record, dict):
                raise CorpusValidationError(f"{path}:{line_number}: record must be an object")
            records.append((line_number, record))
    return records


def _load_manifest(jsonl_dir: Path) -> dict[str, Any]:
    manifest_path = jsonl_dir / FAQ_MANIFEST_NAME
    if not manifest_path.is_file():
        raise CorpusValidationError(
            f"Missing {manifest_path}. Run `python -m app.indexer --export-faq-jsonl` first."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusValidationError(f"{manifest_path}: invalid JSON ({exc.msg})") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != FAQ_JSONL_SCHEMA_VERSION:
        raise CorpusValidationError(f"{manifest_path}: unsupported FAQ corpus manifest")
    if not isinstance(manifest.get("sources"), list):
        raise CorpusValidationError(f"{manifest_path}: sources must be a list")
    return manifest


def ingest_faq_jsonls(csv_dir: Path, jsonl_dir: Path) -> list[dict[str, Any]]:
    """Validate canonical FAQ JSONL coverage and convert it to vector documents."""
    csv_paths = sorted(csv_dir.glob("ura_*_faqs.csv"))
    if not csv_paths:
        raise CorpusValidationError(f"No ura_*_faqs.csv files found in {csv_dir}")
    source_data, expected_by_source, expected_duplicates = _canonical_faq_records(csv_paths)
    manifest = _load_manifest(jsonl_dir)
    by_csv = {str(entry.get("csv", "")): entry for entry in manifest["sources"] if isinstance(entry, dict)}
    expected_names = {source["source"] for source in source_data}
    if len(by_csv) != len(manifest["sources"]) or set(by_csv) != expected_names:
        missing = sorted(expected_names - set(by_csv))
        unexpected = sorted(set(by_csv) - expected_names)
        raise CorpusValidationError(
            f"FAQ JSONL coverage mismatch (missing={missing or 'none'}, unexpected={unexpected or 'none'})"
        )

    expected_source_rows = sum(source["source_rows"] for source in source_data)
    expected_records = sum(len(records) for records in expected_by_source.values())
    expected_removed = expected_source_rows - expected_records
    if (
        manifest.get("deduplication_policy") != FAQ_DEDUPLICATION_POLICY
        or manifest.get("source_count") != len(source_data)
        or manifest.get("source_row_count") != expected_source_rows
        or manifest.get("record_count") != expected_records
        or manifest.get("duplicates_removed") != expected_removed
        or manifest.get("duplicates") != expected_duplicates
    ):
        raise CorpusValidationError(
            "FAQ corpus manifest does not match the current canonical CSV export; regenerate the corpus"
        )

    documents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    for source in source_data:
        csv_path = source["path"]
        source_name = source["source"]
        manifest_entry = by_csv[source_name]
        source_sha256 = source["sha256"]
        if manifest_entry.get("sha256") != source_sha256:
            raise CorpusValidationError(
                f"{csv_path}: source changed since FAQ JSONL export; regenerate the corpus"
            )
        expected_source_records = expected_by_source[source_name]
        if (
            manifest_entry.get("source_rows") != source["source_rows"]
            or manifest_entry.get("records") != len(expected_source_records)
            or manifest_entry.get("duplicates_removed")
            != source["source_rows"] - len(expected_source_records)
        ):
            raise CorpusValidationError(
                f"{csv_path}: manifest source counts do not match the current canonical CSV export"
            )
        jsonl_name = _clean(manifest_entry.get("jsonl"))
        jsonl_path = jsonl_dir / jsonl_name
        if not jsonl_name or not jsonl_path.is_file():
            raise CorpusValidationError(f"{csv_path}: missing generated JSONL {jsonl_name or '<unset>'}")

        expected_records_by_id = {record["chunk_id"]: record for record in expected_source_records}
        actual_records = _read_json_lines(jsonl_path)
        if len(actual_records) != len(expected_source_records):
            raise CorpusValidationError(
                f"{jsonl_path}: expected {len(expected_source_records)} records, found {len(actual_records)}"
            )
        for line_number, record in actual_records:
            required = ("chunk_id", "question", "answer", "source", "source_sha256", "tag", "row_number")
            missing = [key for key in required if not _clean(record.get(key))]
            if (
                record.get("schema_version") != FAQ_JSONL_SCHEMA_VERSION
                or record.get("record_type") != "faq"
                or not isinstance(record.get("duplicate_question_count"), int)
                or record["duplicate_question_count"] < 1
                or missing
            ):
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: invalid FAQ record ({', '.join(missing)})")
            if record["source"] != source_name or record["source_sha256"] != source_sha256:
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: source provenance mismatch")
            chunk_id = _clean(record["chunk_id"])
            if chunk_id in seen_ids:
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: duplicate chunk_id {chunk_id}")
            seen_ids.add(chunk_id)
            expected_record = expected_records_by_id.get(chunk_id)
            if expected_record is None or record != expected_record:
                raise CorpusValidationError(
                    f"{jsonl_path}:{line_number}: record does not match the current canonical CSV export"
                )
            question, answer = _clean(record["question"]), _clean(record["answer"])
            question_key = _question_key(question)
            if question_key in seen_questions:
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: duplicate canonical question {question}")
            seen_questions.add(question_key)
            documents.append(
                {
                    "text": f"Question: {question}\nAnswer: {answer}",
                    "source": csv_path.name,
                    "chunk_id": chunk_id,
                    "page": "",
                    "section": _clean(record["tag"]).replace("_", " ").title(),
                    "doc_type": "faq_jsonl",
                    "question": question,
                    "answer": answer,
                    "tag": _clean(record["tag"]),
                    "row_number": int(record["row_number"]),
                    "source_sha256": source_sha256,
                    "corpus_file": jsonl_path.name,
                    "duplicate_question_count": record["duplicate_question_count"],
                }
            )

    if len(documents) != expected_records:
        raise CorpusValidationError("FAQ corpus manifest record_count does not match loaded documents")
    return documents


def _messages_question_answer(messages: object) -> tuple[str, str]:
    if not isinstance(messages, list):
        return "", ""
    question = answer = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role, content = _clean(message.get("role")), _clean(message.get("content"))
        if role == "user":
            question = content
        elif role == "assistant":
            answer = content
    return question, answer


def _teacher_record(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Return question, answer, source, evidence, and source format."""
    question, answer = _clean(record.get("question")), _clean(record.get("answer"))
    source = _clean(record.get("source_pdf") or record.get("source"))
    evidence = _clean(record.get("chunk_text") or record.get("context"))
    if question and answer:
        return question, answer, source, evidence, "question_answer"

    instruction, output = _clean(record.get("instruction")), _clean(record.get("output"))
    if instruction and output:
        return instruction, output, source, evidence, "instruction_output"

    question, answer = _messages_question_answer(record.get("messages"))
    if question and answer:
        return question, answer, source, evidence, "chat_messages"

    text = _clean(record.get("text"))
    match = _GEMMA_TURN_RE.search(text)
    if match:
        return _clean(match.group(1)), _clean(match.group(2)), source, evidence, "gemma_turns"
    return "", "", source, evidence, ""


def ingest_teacher_qa_jsonls(teacher_qa_dir: Path) -> list[dict[str, Any]]:
    """Normalise the supported teacher-QA JSONL schemas into vector documents.

    Duplicate model-format exports of the same QA pair are collapsed while
    retaining the richest available evidence payload and all source formats.
    """
    paths = sorted(teacher_qa_dir.glob("*.jsonl"))
    if not paths:
        raise CorpusValidationError(f"No teacher-QA JSONL files found in {teacher_qa_dir}")

    canonical: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in paths:
        for line_number, record in _read_json_lines(path):
            question, answer, source, evidence, source_format = _teacher_record(record)
            if not question or not answer or not source:
                raise CorpusValidationError(f"{path}:{line_number}: unsupported teacher-QA record")
            key = (question.casefold(), answer.casefold(), source.casefold())
            current = canonical.get(key)
            if current is None:
                canonical[key] = {
                    "question": question,
                    "answer": answer,
                    "source": source,
                    "evidence": evidence,
                    "chunk_id": _clean(record.get("chunk_id")) or _stable_id(path.name, line_number, question, answer),
                    "question_type": _clean(record.get("question_type") or record.get("type")),
                    "formats": [source_format],
                    "corpus_files": [path.name],
                }
                continue
            if source_format not in current["formats"]:
                current["formats"].append(source_format)
            if path.name not in current["corpus_files"]:
                current["corpus_files"].append(path.name)
            if len(evidence) > len(current["evidence"]):
                current["evidence"] = evidence
                current["chunk_id"] = _clean(record.get("chunk_id")) or current["chunk_id"]
                current["question_type"] = _clean(record.get("question_type") or record.get("type"))

    documents = []
    for record in canonical.values():
        evidence = record["evidence"]
        text = f"Question: {record['question']}\nAnswer: {record['answer']}"
        if evidence:
            text += f"\nEvidence: {evidence[:4000]}"
        documents.append(
            {
                "text": text,
                "source": record["source"],
                "chunk_id": record["chunk_id"],
                "page": "",
                "section": record["question_type"],
                "doc_type": "teacher_qa_jsonl",
                "question": record["question"],
                "answer": record["answer"],
                "evidence": evidence,
                "source_formats": sorted(record["formats"]),
                "corpus_files": sorted(record["corpus_files"]),
            }
        )
    return sorted(documents, key=lambda item: (item["source"], item["chunk_id"]))
