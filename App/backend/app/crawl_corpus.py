"""Validated crawl-page corpus builder for the vector store.

The monthly ``ura.go.ug`` crawl already lands 1,000+ normalised pages under
``Data/crawl/pages``, and they were feeding the training pipeline only. They are
fresher than the PDF corpus — a rate change appears on the website before the
next handbook edition — so they belong in retrieval.

Same two-stage split as :mod:`app.pdf_corpus`: an offline export that needs the
``ml`` chunker, and an ingest that validates the result without re-chunking.

Two shaping decisions matter for retrieval quality, both measured against the
real corpus rather than assumed:

* **Most crawled pages are not content.** The median page body is ~156
  characters — category listings, author archives and pagination stubs. A floor
  of :data:`CRAWL_MIN_PAGE_CHARS` keeps ~30 % of the pages and ~92 % of the
  text, so the index gains the substance without 579 near-empty rows diluting
  every search.
* **The same URL is captured repeatedly.** Successive crawls store a new file
  per content hash, so one page can appear several times with slightly
  different bodies. Only the newest capture per URL is exported; the rest would
  be near-duplicates competing for the same ``top_k``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .faq_corpus import CorpusValidationError
from .pdf_corpus import TRUST_MANIFEST, normalise_extracted_text, normalise_heading

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

CRAWL_JSONL_SCHEMA_VERSION = 1
CRAWL_MANIFEST_NAME = "crawl_corpus_manifest.json"

#: Pages shorter than this are navigation furniture, not guidance.
CRAWL_MIN_PAGE_CHARS = int(os.getenv("CRAWL_MIN_PAGE_CHARS", "400"))

# Chunk sizing mirrors the PDF corpus so both are cut the same way.
CRAWL_CHUNK_TARGET_CHARS = int(os.getenv("CRAWL_CHUNK_TARGET_CHARS", "2000"))
CRAWL_CHUNK_HARD_MAX_CHARS = int(os.getenv("CRAWL_CHUNK_HARD_MAX_CHARS", "4000"))
CRAWL_CHUNK_MIN_CHARS = int(os.getenv("CRAWL_CHUNK_MIN_CHARS", "200"))


def _clean(value: object) -> str:
    return str(value or "").strip()


def _stable_id(*parts: object) -> str:
    raw = "\x1f".join(_clean(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def _chunker_params() -> dict[str, int]:
    return {
        "target_chars": CRAWL_CHUNK_TARGET_CHARS,
        "hard_max_chars": CRAWL_CHUNK_HARD_MAX_CHARS,
        "min_chars": CRAWL_CHUNK_MIN_CHARS,
        "min_page_chars": CRAWL_MIN_PAGE_CHARS,
    }


def _read_page(path: Path) -> dict[str, Any]:
    try:
        page = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusValidationError(f"{path}: invalid crawl page JSON ({exc.msg})") from exc
    if not isinstance(page, dict):
        raise CorpusValidationError(f"{path}: crawl page must be an object")
    return page


def select_pages(pages_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Return the pages worth indexing: newest capture per URL, above the floor.

    Sorted by URL so the export is deterministic regardless of filesystem order.
    """
    newest: dict[str, tuple[str, Path, dict[str, Any]]] = {}
    for path in sorted(pages_dir.glob("*.json")):
        page = _read_page(path)
        url = _clean(page.get("url"))
        if not url:
            continue
        if len(_clean(page.get("text"))) < CRAWL_MIN_PAGE_CHARS:
            continue
        timestamp = _clean(page.get("timestamp"))
        current = newest.get(url)
        # ISO-8601 timestamps sort lexicographically; the filename breaks ties
        # so two captures with the same timestamp still resolve deterministically.
        if current is None or (timestamp, path.name) > (current[0], current[1].name):
            newest[url] = (timestamp, path, page)
    return [(path, page) for _url, (_ts, path, page) in sorted(newest.items())]


def _chunk_page_records(page: Path, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield canonical JSONL records for one crawled page."""
    try:
        from ml.scripts.data_aug.chunkers import chunk_markdown
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise CorpusValidationError(
            "Crawl export needs ml.scripts.data_aug.chunkers. Run the exporter from a "
            "source checkout with the ml/ package importable."
        ) from exc

    url = _clean(payload.get("url"))
    title = normalise_heading(_clean(payload.get("title")))
    content_hash = _clean(payload.get("content_hash"))
    crawled_at = _clean(payload.get("timestamp"))
    # The crawler stores the page body as markdown, so the heading hierarchy is
    # already there to be recovered.
    body = normalise_extracted_text(_clean(payload.get("text")))

    kept = 0
    for chunk in chunk_markdown(
        body,
        doc_id=title or url,
        source=page.name,
        target_chars=CRAWL_CHUNK_TARGET_CHARS,
        hard_max_chars=CRAWL_CHUNK_HARD_MAX_CHARS,
        min_chars=CRAWL_CHUNK_MIN_CHARS,
    ):
        text = normalise_extracted_text(_clean(chunk.text))
        if len(text) < CRAWL_CHUNK_MIN_CHARS:
            continue
        chunk.heading_trail = [
            cleaned for cleaned in (normalise_heading(part) for part in chunk.heading_trail) if cleaned
        ]
        chunk.chunk_id = kept
        kept += 1
        yield {
            "schema_version": CRAWL_JSONL_SCHEMA_VERSION,
            "record_type": "crawl_chunk",
            "chunk_id": _stable_id(page.name, kept - 1, text),
            "text": text,
            "contextual_prefix": chunk.contextual_prefix,
            "heading_trail": list(chunk.heading_trail),
            "source": page.name,
            "content_hash": content_hash,
            "url": url,
            "title": title,
            "crawled_at": crawled_at,
            "chunk_index": kept - 1,
            "char_count": len(text),
        }


def export_crawl_pages_to_jsonl(pages_dir: Path, jsonl_dir: Path) -> dict[str, Any]:
    """Chunk the selected crawl pages into one canonical JSONL file.

    Unlike the FAQ and PDF corpora this writes a single output file: crawl page
    filenames are content hashes, so per-source files would churn on every
    crawl. Provenance stays per-record via ``source`` and ``content_hash``.
    """
    if not pages_dir.is_dir():
        raise CorpusValidationError(f"No crawl pages directory at {pages_dir}")
    selected = select_pages(pages_dir)
    if not selected:
        raise CorpusValidationError(
            f"No crawl pages in {pages_dir} reach {CRAWL_MIN_PAGE_CHARS} characters"
        )

    jsonl_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for path, payload in selected:
        page_records = list(_chunk_page_records(path, payload))
        records.extend(page_records)
        sources.append(
            {
                "source": path.name,
                "url": _clean(payload.get("url")),
                "content_hash": _clean(payload.get("content_hash")),
                "crawled_at": _clean(payload.get("timestamp")),
                "records": len(page_records),
            }
        )

    output_path = jsonl_dir / "crawl_chunks.jsonl"
    temporary_path = output_path.with_suffix(".jsonl.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    temporary_path.replace(output_path)

    total_pages = len(list(pages_dir.glob("*.json")))
    manifest = {
        "schema_version": CRAWL_JSONL_SCHEMA_VERSION,
        "chunker": _chunker_params(),
        "jsonl": output_path.name,
        "pages_available": total_pages,
        "pages_selected": len(selected),
        "record_count": len(records),
        "empty_sources": sorted(s["source"] for s in sources if not s["records"]),
        "sources": sources,
    }
    manifest_path = jsonl_dir / CRAWL_MANIFEST_NAME
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    with temporary_manifest.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    temporary_manifest.replace(manifest_path)

    logger.info(
        "crawl export: %d/%d pages → %d records", len(selected), total_pages, len(records)
    )
    return {
        "pages_available": total_pages,
        "pages_selected": len(selected),
        "records": len(records),
        "dropped_below_floor": total_pages - len(selected),
    }


def _load_manifest(jsonl_dir: Path) -> dict[str, Any]:
    manifest_path = jsonl_dir / CRAWL_MANIFEST_NAME
    if not manifest_path.is_file():
        raise CorpusValidationError(
            f"Missing {manifest_path}. Run `python -m app.indexer --export-crawl-jsonl` first."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusValidationError(f"{manifest_path}: invalid JSON ({exc.msg})") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != CRAWL_JSONL_SCHEMA_VERSION
        or not isinstance(manifest.get("sources"), list)
    ):
        raise CorpusValidationError(f"{manifest_path}: unsupported crawl corpus manifest")
    if manifest.get("chunker") != _chunker_params():
        raise CorpusValidationError(
            f"{manifest_path}: exported under different chunker parameters "
            f"({manifest.get('chunker')} != {_chunker_params()}); regenerate the corpus"
        )
    return manifest


def ingest_crawl_jsonls(pages_dir: Path, jsonl_dir: Path) -> list[dict[str, Any]]:
    """Validate the exported crawl JSONL and convert it to vector documents.

    The export is bound to the crawl by ``content_hash`` per page: a re-crawl
    that changes a page changes its hash, so a stale export is rejected instead
    of quietly serving withdrawn guidance.
    """
    manifest = _load_manifest(jsonl_dir)
    jsonl_path = jsonl_dir / _clean(manifest.get("jsonl"))
    if not jsonl_path.is_file():
        raise CorpusValidationError(f"Missing generated crawl JSONL {jsonl_path}")

    expected_by_source = {
        _clean(entry.get("source")): entry
        for entry in manifest["sources"]
        if isinstance(entry, dict)
    }
    # Re-select from the live crawl and require the export to match it. The
    # serving image ships the derived JSONL without the crawl pages, so that
    # comparison is skipped there — see pdf_corpus.TRUST_MANIFEST for what is
    # still verified.
    if TRUST_MANIFEST and not pages_dir.is_dir():
        logger.warning(
            "CORPUS_TRUST_MANIFEST=true and no crawl pages at %s — indexing %d sources on "
            "the manifest's own hashes; a page changed since export cannot be detected here.",
            pages_dir,
            len(expected_by_source),
        )
    else:
        current = {path.name: payload for path, payload in select_pages(pages_dir)}
        if set(current) != set(expected_by_source):
            missing = sorted(set(current) - set(expected_by_source))
            unexpected = sorted(set(expected_by_source) - set(current))
            raise CorpusValidationError(
                f"Crawl JSONL coverage mismatch (missing={missing[:5] or 'none'}, "
                f"unexpected={unexpected[:5] or 'none'}); regenerate the corpus"
            )
        for source, payload in current.items():
            if _clean(payload.get("content_hash")) != _clean(expected_by_source[source].get("content_hash")):
                raise CorpusValidationError(
                    f"{source}: crawl page changed since export; regenerate the corpus"
                )

    documents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    counts: Counter[str] = Counter()
    with jsonl_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: invalid JSONL ({exc.msg})") from exc
            if not isinstance(record, dict):
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: record must be an object")

            text = _clean(record.get("text"))
            source = _clean(record.get("source"))
            missing = [k for k in ("chunk_id", "text", "source", "url") if not _clean(record.get(k))]
            if (
                record.get("schema_version") != CRAWL_JSONL_SCHEMA_VERSION
                or record.get("record_type") != "crawl_chunk"
                or not isinstance(record.get("chunk_index"), int)
                or not isinstance(record.get("heading_trail"), list)
                or missing
            ):
                raise CorpusValidationError(
                    f"{jsonl_path}:{line_number}: invalid crawl chunk record "
                    f"({', '.join(missing) or 'schema'})"
                )
            if source not in expected_by_source:
                raise CorpusValidationError(
                    f"{jsonl_path}:{line_number}: record from unselected page {source}"
                )
            chunk_id = _clean(record["chunk_id"])
            if chunk_id != _stable_id(source, record["chunk_index"], text):
                raise CorpusValidationError(
                    f"{jsonl_path}:{line_number}: chunk_id does not match its content; JSONL was edited"
                )
            if chunk_id in seen_ids:
                raise CorpusValidationError(f"{jsonl_path}:{line_number}: duplicate chunk_id {chunk_id}")
            seen_ids.add(chunk_id)
            counts[source] += 1

            heading_trail = [_clean(p) for p in record["heading_trail"] if _clean(p)]
            prefix = _clean(record.get("contextual_prefix"))
            documents.append(
                {
                    "text": text,
                    "embed_text": f"{prefix}\n\n{text}" if prefix else text,
                    "source": source,
                    "chunk_id": chunk_id,
                    "page": "",
                    "section": " > ".join(heading_trail),
                    "doc_type": "crawl_chunk",
                    "question": "",
                    "answer": "",
                    "heading_trail": heading_trail,
                    "contextual_prefix": prefix,
                    # The live URL is the citation for crawled guidance.
                    "url": _clean(record.get("url")),
                    "title": _clean(record.get("title")),
                    "crawled_at": _clean(record.get("crawled_at")),
                    "content_hash": _clean(record.get("content_hash")),
                    "chunk_index": int(record["chunk_index"]),
                    "corpus_file": jsonl_path.name,
                }
            )

    for source, entry in expected_by_source.items():
        if counts[source] != entry.get("records"):
            raise CorpusValidationError(
                f"{jsonl_path}: {source} has {counts[source]} records, manifest says {entry.get('records')}"
            )
    if len(documents) != manifest.get("record_count"):
        raise CorpusValidationError(
            f"Crawl corpus manifest record_count ({manifest.get('record_count')}) "
            f"does not match loaded documents ({len(documents)})"
        )
    return sorted(documents, key=lambda item: (item["source"], item["chunk_index"]))
