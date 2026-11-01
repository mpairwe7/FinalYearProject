"""Validated crawl-page corpus builder for the vector store.

The ``ura.go.ug`` crawl lands normalised pages under ``Data/crawl/pages``, and they
were feeding the training pipeline only. They can be fresher than the PDF corpus
— a rate change appears on the website before the next handbook edition — so
they belong in retrieval, provided they are read when they change.

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

Selection is also where a page can be refused (ADR-0023). OWASP ASI06 asks for
writes to shared context to be validated before commit, for authenticated
sources, and for unverified entries to expire. So the crawl ledger decides which
capture is current and whether the page still exists, nothing from outside the
publisher's live site qualifies, and a page holding an instruction aimed at a
model is quarantined rather than scrubbed and served. The check at prompt
assembly stays: this one means a poisoned page never enters the index at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .faq_corpus import CorpusValidationError
from .guardrails import injection_present, redact_pii_text
from .pdf_corpus import TRUST_MANIFEST, normalise_extracted_text, normalise_heading

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

CRAWL_JSONL_SCHEMA_VERSION = 2
CRAWL_MANIFEST_NAME = "crawl_corpus_manifest.json"
CRAWL_STATE_NAME = "crawl_state.json"

#: A page the live site has not confirmed for this long leaves the corpus
#: (ASI06: "expire unverified memory"). Measured from the last complete crawl,
#: never from the clock, so a crawler outage cannot silently empty the corpus,
#: and so the same inputs always select the same pages.
CRAWL_MAX_UNVERIFIED_DAYS = int(os.getenv("CRAWL_MAX_UNVERIFIED_DAYS", "45"))

#: Ledger statuses that take a URL out of the corpus.
_WITHDRAWN_STATUSES = frozenset({"gone", "moved", "disallowed", "noindex", "usage_denied"})

#: Written by the crawler before ADR-0023, when an archive redirect could be
#: stored under a URA address.
_FOREIGN_TITLES = frozenset({"wayback machine", "internet archive"})
_FOREIGN_BODY = re.compile(r"internet archive", re.IGNORECASE)

#: Identifiers that a taxpayer asking about tax never needs back. The helpline
#: has the ``ug_phone`` shape, so phones stay; ``kinds`` keeps them out of this.
_CORPUS_REDACTED_KINDS = frozenset({"ug_tin", "ug_nid"})
#: Official partner addresses (``sct@kra.go.ke``) stay; a personal mailbox does not.
_PERSONAL_WEBMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]+@(?:gmail|googlemail|yahoo|hotmail|outlook|live|icloud|proton|protonmail)\.[a-z]{2,}(?:\.[a-z]{2,})?\b",
    re.IGNORECASE,
)

#: Pages shorter than this are navigation furniture, not guidance.
CRAWL_MIN_PAGE_CHARS = int(os.getenv("CRAWL_MIN_PAGE_CHARS", "400"))

# Chunk sizing mirrors the PDF corpus so both are cut the same way.
CRAWL_CHUNK_TARGET_CHARS = int(os.getenv("CRAWL_CHUNK_TARGET_CHARS", "2000"))
CRAWL_CHUNK_HARD_MAX_CHARS = int(os.getenv("CRAWL_CHUNK_HARD_MAX_CHARS", "4000"))
CRAWL_CHUNK_MIN_CHARS = int(os.getenv("CRAWL_CHUNK_MIN_CHARS", "200"))

#: Marks the start of an answer on the "Ask URA Commissioner General" pages.
#: Those pages are digests: one heading, then a run of unrelated taxpayer
#: questions each answered under this salutation.
_QA_ANSWER_MARKER = re.compile(r"^\s*Dear\s+Reader\s*[,:]?\s*$", re.IGNORECASE)

#: A digest needs at least this many answers before it is cut question-by-question
#: rather than by size. Two is enough to prove the shape and is what makes the
#: cut safe: one salutation could be an ordinary page that happens to use it.
_QA_MIN_ANSWERS = 2


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
        "max_unverified_days": CRAWL_MAX_UNVERIFIED_DAYS,
    }


def _read_page(path: Path) -> dict[str, Any]:
    try:
        page = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusValidationError(f"{path}: invalid crawl page JSON ({exc.msg})") from exc
    if not isinstance(page, dict):
        raise CorpusValidationError(f"{path}: crawl page must be an object")
    return page


def load_crawl_state(pages_dir: Path) -> dict[str, Any]:
    """The crawl ledger beside *pages_dir*. Absent means no capture has a verdict yet."""
    path = pages_dir.parent / CRAWL_STATE_NAME
    if not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusValidationError(f"{path}: invalid crawl state ({exc.msg})") from exc
    if not isinstance(state, dict):
        raise CorpusValidationError(f"{path}: crawl state must be an object")
    return state


@dataclass
class CorpusSelection:
    """What a crawl may contribute to the corpus, and what it may not, with reasons."""

    selected: list[tuple[Path, dict[str, Any]]] = field(default_factory=list)
    excluded: Counter[str] = field(default_factory=Counter)
    quarantined: list[dict[str, str]] = field(default_factory=list)


def _parse_time(value: object) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _last_verified(entry: dict[str, Any], page: dict[str, Any]) -> str:
    """When the live site last confirmed this text. A capture time is a claim, not proof."""
    return _clean(entry.get("last_verified_at")) or _clean(page.get("timestamp"))


def _expired(entry: dict[str, Any], page: dict[str, Any], complete_run_at: datetime | None) -> bool:
    if complete_run_at is None:
        return False
    verified = _parse_time(_last_verified(entry, page))
    return verified is None or verified < complete_run_at - timedelta(days=CRAWL_MAX_UNVERIFIED_DAYS)


def _looks_foreign(page: dict[str, Any]) -> bool:
    if _clean(page.get("title")).lower() in _FOREIGN_TITLES:
        return True
    return bool(_FOREIGN_BODY.search(_clean(page.get("text"))[:400]) and "uganda" not in _clean(page.get("text"))[:400].lower())


def _quarantine_reason(page: dict[str, Any]) -> str:
    """Why a page must be held for a person to read, or ``""``.

    Text a reader cannot see is judged too. The crawler removes it, but an
    instruction hidden on a government page is evidence the site was tampered
    with, so the rest of that page is not to be trusted either.
    """
    if injection_present(_clean(page.get("hidden_excerpt"))):
        return "hidden_instruction"
    if injection_present(f"{_clean(page.get('title'))}\n{_clean(page.get('text'))}"):
        return "instruction_in_text"
    return ""


def _exclusion_reason(page: dict[str, Any], entry: dict[str, Any], complete_run_at: datetime | None) -> str:
    status = _clean(entry.get("status"))
    if status in _WITHDRAWN_STATUSES:
        return status
    if entry.get("kind") == "hub":
        return "listing"
    if _clean(page.get("source_kind")) not in ("", "live") or _looks_foreign(page):
        return "foreign"
    if len(_clean(page.get("text"))) < CRAWL_MIN_PAGE_CHARS:
        return "below_floor"
    if _expired(entry, page, complete_run_at):
        return "unverified"
    return ""


def review_pages(pages_dir: Path) -> CorpusSelection:
    """Decide every URL: which capture is current, and whether it may be served.

    The capture is chosen *before* the floor is applied. It used to be the other
    way round, so a page cut down to a short "withdrawn" stub fell back to its
    longer, older text. The ledger names the current capture when it has one,
    which also gets a page that reverted to earlier wording right, and a capture
    with no ledger entry is judged by its own timestamp.

    Sorted by URL so the export is deterministic regardless of filesystem order.
    """
    state = load_crawl_state(pages_dir)
    ledger: dict[str, Any] = state.get("visited") or {}
    complete_run_at = _parse_time(state.get("last_full_run_at"))

    selection = CorpusSelection()
    newest: dict[str, tuple[str, Path, dict[str, Any]]] = {}
    for path in sorted(pages_dir.glob("*.json")):
        page = _read_page(path)
        url = _clean(page.get("url"))
        if not url:
            continue
        current_hash = (ledger.get(url) or {}).get("content_hash")
        if current_hash is not None and _clean(current_hash) != _clean(page.get("content_hash")):
            continue
        timestamp = _clean(page.get("timestamp"))
        current = newest.get(url)
        # ISO-8601 timestamps sort lexicographically; the filename breaks ties
        # so two captures with the same timestamp still resolve deterministically.
        if current is None or (timestamp, path.name) > (current[0], current[1].name):
            newest[url] = (timestamp, path, page)

    for url, (_timestamp, path, page) in sorted(newest.items()):
        entry = ledger.get(url) or {}
        reason = _exclusion_reason(page, entry, complete_run_at)
        if reason:
            selection.excluded[reason] += 1
            continue
        held = _quarantine_reason(page)
        if held:
            selection.excluded["quarantined"] += 1
            selection.quarantined.append({"url": url, "source": path.name, "reason": held})
            logger.warning("crawl page quarantined (%s): %s", held, url)
            continue
        selection.selected.append((path, page))
    return selection


def select_pages(pages_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Return the pages worth indexing. See :func:`review_pages` for why not the rest."""
    return review_pages(pages_dir).selected


def fiscal_year_of(*dates: str) -> str:
    """``FY2023-24`` for the latest of *dates*, on Uganda's July to June year, or ``""``.

    This puts a crawled page into the same edition logic as a handbook, so an
    older page cannot displace a newer statement: among near-identical passages
    the newer edition wins, an edition years out of date is demoted, and the
    model sees which edition it is reading. An undated page stays unknown, and
    unknown is never treated as old.
    """
    moments = [_parse_time(value) for value in dates]
    latest = max((moment for moment in moments if moment), default=None)
    if latest is None:
        return ""
    start = latest.year if latest.month >= 7 else latest.year - 1
    return f"FY{start}-{(start + 1) % 100:02d}"


def minimise_identifiers(text: str) -> str:
    """Take personal identifiers out of a public page before it is indexed.

    URA publishes these, but a corpus need not keep what no answer requires
    (ASI06: "minimize retention by data sensitivity"; DPPA 2019). The reply guard
    would hide them on the way out anyway. Deliberately not ``redact_pii_text``
    whole: that also removes ``0772140000``, URA's own WhatsApp helpline.
    """
    redacted = redact_pii_text(text, kinds=_CORPUS_REDACTED_KINDS)
    return _PERSONAL_WEBMAIL.sub("[REDACTED_EMAIL]", redacted)


def split_qa_digest(body: str) -> list[tuple[str, str]]:
    """Cut an "Ask URA Commissioner General" page into its individual Q&As.

    These pages carry a single markdown heading and then six to nine unrelated
    taxpayer questions under it, each answered after a "Dear Reader," line. Size
    chunking cannot see those boundaries, so it packs several unrelated Q&As into
    one chunk. That chunk then retrieves correctly — the answer really is in it —
    while the passage that gets read back is whichever Q&A happened to land at the
    top. "What taxes apply to private schools?" returned an answer about funeral
    service companies for exactly this reason: both sat in chunk 0, funeral first.

    Mixing topics also inflates lexical scoring, because one chunk accumulates the
    vocabulary of every question in it and so looks relevant to all of them.

    Returns ``(question, answer)`` pairs, or an empty list when the page is not a
    digest — the caller then falls back to ordinary size chunking.
    """
    lines = body.splitlines()
    markers = [i for i, line in enumerate(lines) if _QA_ANSWER_MARKER.match(line)]
    if len(markers) < _QA_MIN_ANSWERS:
        return []

    units: list[tuple[str, str]] = []
    for position, marker in enumerate(markers):
        # The question is the last non-blank line before the salutation. On these
        # pages it is a single line; the page heading is never one, because the
        # first salutation always has its question between it and the heading.
        question_at = next((j for j in range(marker - 1, -1, -1) if lines[j].strip()), None)
        if question_at is None:
            continue

        # The answer runs to the question that introduces the next salutation,
        # not to the salutation itself, or to the end of the page for the last one.
        answer_end = len(lines)
        if position + 1 < len(markers):
            answer_end = next(
                (j for j in range(markers[position + 1] - 1, marker, -1) if lines[j].strip()),
                markers[position + 1],
            )

        question = lines[question_at].strip()
        answer = "\n".join(lines[marker + 1 : answer_end]).strip()
        if question and answer:
            units.append((question, answer))

    return units


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
    # The crawler stores the page body as markdown, so the heading hierarchy is
    # already there to be recovered.
    body = minimise_identifiers(normalise_extracted_text(_clean(payload.get("text"))))
    # Every chunk of a page shares its evidence.
    evidence = {
        "content_hash": content_hash,
        "url": url,
        "title": title,
        "crawled_at": _clean(payload.get("_verified_at")) or _clean(payload.get("timestamp")),
        "published_at": _clean(payload.get("published_at")),
        "modified_at": _clean(payload.get("modified_at")),
        "fiscal_year": fiscal_year_of(_clean(payload.get("published_at")), _clean(payload.get("modified_at"))),
        "source_kind": _clean(payload.get("source_kind")) or "unverified",
    }

    kept = 0

    # A Q&A digest is cut question-by-question so the retrieval unit and the
    # answer unit are the same thing. Every unit on the 18 digest pages in the
    # corpus lands between the min and hard-max above, so nothing is dropped and
    # nothing needs re-splitting; the size chunker below still handles every
    # other page.
    for question, answer in split_qa_digest(body):
        text = normalise_extracted_text(f"{question}\n{answer}")
        if len(text) < CRAWL_CHUNK_MIN_CHARS:
            continue
        heading = normalise_heading(question)
        trail = [part for part in (title, heading) if part]
        chunk_index = kept
        kept += 1
        yield {
            "schema_version": CRAWL_JSONL_SCHEMA_VERSION,
            "record_type": "crawl_chunk",
            "chunk_id": _stable_id(page.name, chunk_index, text),
            "text": text,
            "contextual_prefix": f"[Document: {title or url} — {heading}]" if heading else "",
            "heading_trail": trail,
            "source": page.name,
            **evidence,
            "chunk_index": chunk_index,
            "char_count": len(text),
        }
    if kept:
        return

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
            **evidence,
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
    review = review_pages(pages_dir)
    selected = review.selected
    if not selected:
        raise CorpusValidationError(
            f"No crawl pages in {pages_dir} reach {CRAWL_MIN_PAGE_CHARS} characters"
        )
    state = load_crawl_state(pages_dir)
    ledger: dict[str, Any] = state.get("visited") or {}

    jsonl_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for path, payload in selected:
        entry = ledger.get(_clean(payload.get("url"))) or {}
        evidence = {**payload, "_verified_at": _last_verified(entry, payload)}
        page_records = list(_chunk_page_records(path, evidence))
        records.extend(page_records)
        sources.append(
            {
                "source": path.name,
                "url": _clean(payload.get("url")),
                "content_hash": _clean(payload.get("content_hash")),
                "crawled_at": evidence["_verified_at"],
                "source_kind": _clean(payload.get("source_kind")) or "unverified",
                "published_at": _clean(payload.get("published_at")),
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
        "crawl": _crawl_summary(state, sources, review),
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


def _crawl_summary(
    state: dict[str, Any],
    sources: list[dict[str, Any]],
    review: CorpusSelection,
) -> dict[str, Any]:
    """How old and how well-evidenced this corpus is, read by /v1/index/freshness.

    The index freshness check proves the index matches the shipped corpus, which
    is true by construction in a baked image. Nothing said how the corpus
    compared with the site, which is the question a taxpayer's answer rests on.
    """
    verified = sorted(entry["crawled_at"] for entry in sources if entry["crawled_at"])
    return {
        "last_full_run_at": _clean(state.get("last_full_run_at")),
        "last_changes_run_at": _clean(state.get("last_changes_run_at")),
        "oldest_verified_at": verified[0] if verified else "",
        "pages_live": sum(1 for entry in sources if entry["source_kind"] == "live"),
        "pages_unverified": sum(1 for entry in sources if entry["source_kind"] != "live"),
        "pages_undated": sum(1 for entry in sources if not entry["published_at"]),
        "excluded": dict(sorted(review.excluded.items())),
        "quarantined": review.quarantined,
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
                    "published_at": _clean(record.get("published_at")),
                    "modified_at": _clean(record.get("modified_at")),
                    "fiscal_year": _clean(record.get("fiscal_year")),
                    "source_kind": _clean(record.get("source_kind")),
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
