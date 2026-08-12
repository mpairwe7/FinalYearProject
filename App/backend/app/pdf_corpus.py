"""Validated PDF-chunk corpus builder for the vector store.

Until now the 300+ MB of upstream URA guidance under ``Data/pdfs`` reached
retrieval only as teacher-QA JSONL, and that bridge covered a single
document — so the prose knowledge base was effectively the FAQ CSVs alone.
This module gives PDFs a first-class, auditable path into the index while
keeping the JSONL-first contract the FAQ corpus established.

Two stages, deliberately split by cost:

* :func:`export_pdf_chunks_to_jsonl` runs **offline** from a source
  checkout. It is the only stage that needs ``pymupdf4llm`` and the
  hierarchical chunker in ``ml.scripts.data_aug.chunkers`` (neither ships
  in the serving image), so the import is lazy and failure is loud.
* :func:`ingest_pdf_jsonls` runs wherever the index is built. It validates
  the export against the PDFs on disk *without* re-extracting them.

Why the validation differs from :mod:`app.faq_corpus`: the FAQ ingester can
cheaply re-derive every record from the CSVs and demand byte equality. Doing
that for PDFs would mean re-running markdown extraction over hundreds of
megabytes on every index build. Instead the contract is:

* every PDF on disk appears in the manifest, and its SHA-256 still matches
  (catches a source document replaced after export);
* the manifest's chunker parameters match the ones requested (catches an
  export produced under different chunking);
* per-source record counts match, and every ``chunk_id`` re-derives from the
  record's own ``source``/``chunk_index``/``text`` (catches truncated,
  reordered, or hand-edited JSONL).

Chunks keep the structure the chunker recovers: the heading trail becomes the
citation locator, markdown tables stay atomic so rate tables survive, and the
contextual prefix is embedded with the chunk (contextual retrieval) while the
raw text is what gets displayed and cited.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .faq_corpus import CorpusValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

PDF_JSONL_SCHEMA_VERSION = 1
PDF_MANIFEST_NAME = "pdf_corpus_manifest.json"

# Chunk sizing. The defaults mirror ``ml.scripts.data_aug.chunkers.chunk_pdf``
# so the retrieval corpus and the training corpus are cut the same way.
# ``BAAI/bge-m3`` accepts 8192 tokens, so there is headroom to grow these — but
# larger chunks trade retrieval precision for recall, so the defaults are left
# where the training pipeline already has them and changing them is an explicit,
# measurable decision (see the Hit@K / nDCG gate in CI).
PDF_CHUNK_TARGET_CHARS = int(os.getenv("PDF_CHUNK_TARGET_CHARS", "2000"))
PDF_CHUNK_HARD_MAX_CHARS = int(os.getenv("PDF_CHUNK_HARD_MAX_CHARS", "4000"))
PDF_CHUNK_MIN_CHARS = int(os.getenv("PDF_CHUNK_MIN_CHARS", "200"))

# URA's PDFs embed the period as a non-standard glyph that decodes to U+FFFD,
# so extracted text shows "1�2" for "1.2" and renders table-of-contents
# leaders as "� � �" runs. ``text_utils._DOT_LEADER_RE`` only
# matches literal "..." and therefore misses both. Normalising here keeps the
# fix scoped to the retrieval corpus; the training pipeline shares the same
# latent gap in ``ml/scripts/data_aug/text_utils.py``.
#
# A leader run is 3+ dot-ish glyphs separated only by whitespace, optionally
# trailed by a page number. Legitimate prose ("1.2.3", "a. b. c.") never
# matches because a non-dot character breaks the run.
_LEADER_RUN_RE = re.compile(r"(?:[.�]\s*){3,}\d*")
# The same glyph between two digits is a decimal point: "1�2" → "1.2".
_DIGIT_DOT_RE = re.compile(r"(?<=\d)�(?=\d)")
# Markdown emphasis leaks out of pymupdf4llm headings ("**4.3 Certainty**").
_EMPHASIS_RE = re.compile(r"[*_]{1,3}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# Fiscal year in a filename, e.g. "...-FY-2024-25-1.pdf", "...FY2023-24.pdf",
# "...Sector-2025-26.pdf". A candidate is only accepted when the two years are
# consecutive, which rejects document numbers and unrelated digit runs.
_FY_FULL_RE = re.compile(r"(?:FY[-_ ]?)?((?:19|20)\d{2})\s*[-_/]\s*((?:19|20)?\d{2})", re.I)
# Two-digit form, e.g. "...Retail-Sector22-23-1.pdf".
_FY_SHORT_RE = re.compile(r"(?<!\d)([12]\d)\s*[-_/]\s*([12]\d)(?!\d)")


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


def fiscal_year_from_name(name: str) -> str:
    """Return the normalised fiscal year encoded in *name*, or ``""``.

    ``"ura.go.ug-Withholding-Tax-FY-2024-25-1"`` → ``"FY2024-25"``.

    An empty result means *unknown*, not *current*: roughly two thirds of the
    URA filenames carry no fiscal year at all. Callers must not treat unknown
    as stale — see :func:`app.retriever.fiscal_year_rank`.
    """
    stem = _clean(name)
    for match in _FY_FULL_RE.finditer(stem):
        first = int(match.group(1))
        raw_second = match.group(2)
        second = int(raw_second) if len(raw_second) == 4 else (first // 100) * 100 + int(raw_second)
        if second == first + 1:
            return f"FY{first}-{second % 100:02d}"
    for match in _FY_SHORT_RE.finditer(stem):
        first, second = int(match.group(1)), int(match.group(2))
        if second == first + 1 and 10 <= first <= 30:
            return f"FY20{first}-{second:02d}"
    return ""


def normalise_extracted_text(text: str) -> str:
    """Repair the extraction artefacts URA's PDFs introduce.

    Decimal points are restored, table-of-contents leader runs collapse to a
    single space, and any remaining undecodable glyph is dropped. Newlines are
    preserved because markdown tables depend on them.
    """
    if not text:
        return ""
    text = _DIGIT_DOT_RE.sub(".", text)
    text = _LEADER_RUN_RE.sub(" ", text)
    text = text.replace("�", "")
    lines = [_MULTI_SPACE_RE.sub(" ", line).rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def normalise_heading(heading: str) -> str:
    """Clean one heading-trail level for use as a citation locator."""
    heading = _EMPHASIS_RE.sub("", normalise_extracted_text(heading))
    return _MULTI_SPACE_RE.sub(" ", heading.replace("\n", " ")).strip()


def _chunker_params() -> dict[str, int]:
    return {
        "target_chars": PDF_CHUNK_TARGET_CHARS,
        "hard_max_chars": PDF_CHUNK_HARD_MAX_CHARS,
        "min_chars": PDF_CHUNK_MIN_CHARS,
    }


def _chunk_pdf_records(pdf_path: Path, source_sha256: str) -> Iterator[dict[str, Any]]:
    """Yield canonical JSONL records for one PDF.

    The hierarchical chunker lives in ``ml/``, which is deliberately absent
    from the serving image — this is an offline-only code path, so the import
    is lazy and a missing dependency raises rather than silently yielding an
    empty corpus.
    """
    try:
        from ml.scripts.data_aug.chunkers import chunk_pdf
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise CorpusValidationError(
            "PDF export needs ml.scripts.data_aug.chunkers (and pymupdf4llm). "
            "Run the exporter from a source checkout with the ml/ package importable."
        ) from exc

    source = pdf_path.name
    fiscal_year = fiscal_year_from_name(pdf_path.stem)
    kept = 0
    for chunk in chunk_pdf(
        pdf_path,
        target_chars=PDF_CHUNK_TARGET_CHARS,
        hard_max_chars=PDF_CHUNK_HARD_MAX_CHARS,
        min_chars=PDF_CHUNK_MIN_CHARS,
    ):
        text = normalise_extracted_text(_clean(chunk.text))
        # Cleaning collapses table-of-contents pages to almost nothing, which is
        # exactly what should not be indexed — re-apply the floor afterwards.
        if len(text) < PDF_CHUNK_MIN_CHARS:
            continue
        # Assigning the cleaned trail back lets ``contextual_prefix`` recompute
        # from clean headings instead of duplicating its formatting here.
        chunk.heading_trail = [
            cleaned for cleaned in (normalise_heading(part) for part in chunk.heading_trail) if cleaned
        ]
        # Chunk indices must stay dense after the filter above, because
        # ``chunk_id`` and the ingest-side re-derivation both key on them.
        chunk.chunk_id = kept
        kept += 1
        yield {
            "schema_version": PDF_JSONL_SCHEMA_VERSION,
            "record_type": "pdf_chunk",
            "chunk_id": _stable_id(source, chunk.chunk_id, text),
            "text": text,
            "contextual_prefix": chunk.contextual_prefix,
            "heading_trail": list(chunk.heading_trail),
            "source": source,
            "source_sha256": source_sha256,
            "fiscal_year": fiscal_year,
            "chunk_index": int(chunk.chunk_id),
            "char_count": len(text),
        }


def export_pdf_chunks_to_jsonl(pdf_dir: Path, jsonl_dir: Path) -> dict[str, Any]:
    """Chunk every PDF in *pdf_dir* into one canonical JSONL file each.

    Deterministic: unchanged PDF bytes and unchanged chunker parameters
    produce unchanged records. Files are replaced atomically so a failed
    export cannot leave a partial corpus for the indexer.

    A PDF that yields no chunks (image-only scan, or extraction failure) is
    reported in the manifest with ``records: 0`` rather than skipped, so the
    gap is visible instead of silent.
    """
    pdf_paths = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_paths:
        raise CorpusValidationError(f"No PDF files found in {pdf_dir}")

    jsonl_dir.mkdir(parents=True, exist_ok=True)
    manifest_sources: list[dict[str, Any]] = []
    empty_sources: list[str] = []
    total_records = 0

    for pdf_path in pdf_paths:
        source_sha256 = _sha256_file(pdf_path)
        records = list(_chunk_pdf_records(pdf_path, source_sha256))

        output_path = jsonl_dir / f"{pdf_path.stem}.jsonl"
        temporary_path = output_path.with_suffix(".jsonl.tmp")
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        temporary_path.replace(output_path)

        if not records:
            empty_sources.append(pdf_path.name)
        total_records += len(records)
        manifest_sources.append(
            {
                "pdf": pdf_path.name,
                "jsonl": output_path.name,
                "sha256": source_sha256,
                "records": len(records),
                "fiscal_year": fiscal_year_from_name(pdf_path.stem),
                "chars": sum(record["char_count"] for record in records),
            }
        )
        logger.info("chunked %s → %d records", pdf_path.name, len(records))

    fiscal_years = Counter(entry["fiscal_year"] for entry in manifest_sources)
    manifest = {
        "schema_version": PDF_JSONL_SCHEMA_VERSION,
        "chunker": _chunker_params(),
        "source_count": len(manifest_sources),
        "record_count": total_records,
        "empty_sources": sorted(empty_sources),
        "fiscal_year_counts": dict(sorted(fiscal_years.items())),
        "sources": manifest_sources,
    }
    manifest_path = jsonl_dir / PDF_MANIFEST_NAME
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    with temporary_manifest.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    temporary_manifest.replace(manifest_path)

    return {
        "sources": len(manifest_sources),
        "records": total_records,
        "empty_sources": len(empty_sources),
        "unknown_fiscal_year": fiscal_years.get("", 0),
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
    manifest_path = jsonl_dir / PDF_MANIFEST_NAME
    if not manifest_path.is_file():
        raise CorpusValidationError(
            f"Missing {manifest_path}. Run `python -m app.indexer --export-pdf-jsonl` first."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusValidationError(f"{manifest_path}: invalid JSON ({exc.msg})") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PDF_JSONL_SCHEMA_VERSION:
        raise CorpusValidationError(f"{manifest_path}: unsupported PDF corpus manifest")
    if not isinstance(manifest.get("sources"), list):
        raise CorpusValidationError(f"{manifest_path}: sources must be a list")
    if manifest.get("chunker") != _chunker_params():
        raise CorpusValidationError(
            f"{manifest_path}: exported under different chunker parameters "
            f"({manifest.get('chunker')} != {_chunker_params()}); regenerate the corpus"
        )
    return manifest


def ingest_pdf_jsonls(pdf_dir: Path, jsonl_dir: Path) -> list[dict[str, Any]]:
    """Validate the exported PDF JSONL and convert it to vector documents.

    Validation deliberately avoids re-extracting the PDFs — see the module
    docstring for the contract this enforces instead.
    """
    pdf_paths = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_paths:
        raise CorpusValidationError(f"No PDF files found in {pdf_dir}")

    manifest = _load_manifest(jsonl_dir)
    by_pdf = {str(entry.get("pdf", "")): entry for entry in manifest["sources"] if isinstance(entry, dict)}
    expected_names = {path.name for path in pdf_paths}
    if len(by_pdf) != len(manifest["sources"]) or set(by_pdf) != expected_names:
        missing = sorted(expected_names - set(by_pdf))
        unexpected = sorted(set(by_pdf) - expected_names)
        raise CorpusValidationError(
            f"PDF JSONL coverage mismatch (missing={missing or 'none'}, unexpected={unexpected or 'none'})"
        )

    documents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    total_records = 0

    for pdf_path in pdf_paths:
        entry = by_pdf[pdf_path.name]
        source_sha256 = _sha256_file(pdf_path)
        if entry.get("sha256") != source_sha256:
            raise CorpusValidationError(
                f"{pdf_path}: source changed since PDF JSONL export; regenerate the corpus"
            )

        jsonl_name = _clean(entry.get("jsonl"))
        jsonl_path = jsonl_dir / jsonl_name
        if not jsonl_name or not jsonl_path.is_file():
            raise CorpusValidationError(f"{pdf_path}: missing generated JSONL {jsonl_name or '<unset>'}")

        actual_records = _read_json_lines(jsonl_path)
        expected_count = entry.get("records")
        if not isinstance(expected_count, int) or len(actual_records) != expected_count:
            raise CorpusValidationError(
                f"{jsonl_path}: expected {expected_count} records, found {len(actual_records)}"
            )
        total_records += len(actual_records)

        expected_fiscal_year = _clean(entry.get("fiscal_year"))
        for line_number, record in actual_records:
            text = _clean(record.get("text"))
            required = ("chunk_id", "text", "source", "source_sha256")
            missing = [key for key in required if not _clean(record.get(key))]
            if (
                record.get("schema_version") != PDF_JSONL_SCHEMA_VERSION
                or record.get("record_type") != "pdf_chunk"
                or not isinstance(record.get("chunk_index"), int)
                or not isinstance(record.get("heading_trail"), list)
                or missing
            ):
                raise CorpusValidationError(
                    f"{jsonl_path}:{line_number}: invalid PDF chunk record ({', '.join(missing) or 'schema'})"
                )
            if record["source"] != pdf_path.name or record["source_sha256"] != source_sha256:
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: source provenance mismatch")
            if _clean(record.get("fiscal_year")) != expected_fiscal_year:
                raise CorpusValidationError(
                    f"{jsonl_path}:{line_number}: fiscal_year does not match the manifest"
                )

            chunk_id = _clean(record["chunk_id"])
            if chunk_id != _stable_id(pdf_path.name, record["chunk_index"], text):
                raise CorpusValidationError(
                    f"{jsonl_path}:{line_number}: chunk_id does not match its content; JSONL was edited"
                )
            if chunk_id in seen_ids:
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: duplicate chunk_id {chunk_id}")
            seen_ids.add(chunk_id)

            heading_trail = [_clean(part) for part in record["heading_trail"] if _clean(part)]
            prefix = _clean(record.get("contextual_prefix"))
            documents.append(
                {
                    # Displayed and cited verbatim.
                    "text": text,
                    # Embedded and BM25-fitted: the contextual prefix names the
                    # document and section the chunk came from, which is what
                    # makes an isolated chunk retrievable.
                    "embed_text": f"{prefix}\n\n{text}" if prefix else text,
                    "source": pdf_path.name,
                    "chunk_id": chunk_id,
                    "page": "",
                    # Section-level citation locator. PDF page numbers are lost
                    # by whole-document markdown extraction, so the heading
                    # trail is the addressable location.
                    "section": " > ".join(heading_trail),
                    "doc_type": "pdf_chunk",
                    "question": "",
                    "answer": "",
                    "heading_trail": heading_trail,
                    "contextual_prefix": prefix,
                    "fiscal_year": expected_fiscal_year,
                    "chunk_index": int(record["chunk_index"]),
                    "source_sha256": source_sha256,
                    "corpus_file": jsonl_path.name,
                }
            )

    if total_records != manifest.get("record_count"):
        raise CorpusValidationError(
            f"PDF corpus manifest record_count ({manifest.get('record_count')}) "
            f"does not match loaded documents ({total_records})"
        )
    return sorted(documents, key=lambda item: (item["source"], item["chunk_index"]))
