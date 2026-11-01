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
import html as _html
import json
import logging
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urljoin, urlparse

from .faq_corpus import CorpusValidationError
from .malware_scan import MalwareRejected, scan_bytes
from .pdf_guards import (
    CORPUS_PDF_LIMITS,
    PdfRejected,
    inspect_pdf_bytes,
    inspect_pdf_path,
    validate_pdf_header,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

logger = logging.getLogger(__name__)

PDF_JSONL_SCHEMA_VERSION = 1
PDF_MANIFEST_NAME = "pdf_corpus_manifest.json"

#: Official Ugandan statutory PDF portal sources (October 2026 standard):
#: 1. Parliament of Uganda 2026 Tax Bills portal
#: 2. Uganda Revenue Authority Laws, Acts & Regulations download archive
DEFAULT_STATUTORY_PDF_SOURCE_URLS: tuple[str, ...] = (
    "https://www.parliament.go.ug/page/tax-bills-2026",
    "https://ura.go.ug/download-category/laws-and-acts/",
)

#: Top 10 Ugandan Government Bodies (MDAs) with high URA NTR/PRN, TIN, TCC,
#: Stamp Duty, and ASYCUDA/UESW dependency driving URA CXP inquiry volume (October 2026):
MDA_STATUTORY_PDF_REGISTRY: tuple[dict[str, str], ...] = (
    {
        "mda_code": "MDA1_MoWT",
        "mda_name": "Ministry of Works and Transport (UDLS & Motor Vehicle Registry)",
        "filename": "MoWT-Traffic-and-Road-Safety-Act-Cap-361.pdf",
        "title": "Traffic and Road Safety Act, 1998 (Cap. 361) — Driving Permits & Vehicle Registration",
        "url": "https://media.ulii.org/media/legislation/18258/source_file/5b5c2f1ce136c1a3/1998-15.pdf",
    },
    {
        "mda_code": "MDA2_DCIC",
        "mda_name": "Ministry of Internal Affairs — Directorate of Citizenship and Immigration Control (DCIC)",
        "filename": "DCIC-Uganda-Citizenship-and-Immigration-Control-Act-Cap-313.pdf",
        "title": "Uganda Citizenship and Immigration Control Act (Cap. 313) — Passports, Work Permits & Passes",
        "url": "https://media.ulii.org/media/legislation/18262/source_file/8e4f495a61b25d69/1999-3.pdf",
    },
    {
        "mda_code": "MDA2_DCIC",
        "mda_name": "Ministry of Internal Affairs — Directorate of Citizenship and Immigration Control (DCIC)",
        "filename": "DCIC-Uganda-Citizenship-and-Immigration-Control-Fees-Regulations-2021.pdf",
        "title": "The Uganda Citizenship and Immigration Control (Fees) Regulations, 2021",
        "url": "https://media.ulii.org/media/legislation/107108/source_file/uganda-citizenship-and-immigration-control-fees-regulations-2021.pdf",
    },
    {
        "mda_code": "MDA3_NIRA",
        "mda_name": "National Identification and Registration Authority (NIRA)",
        "filename": "NIRA-Summary-of-Registration-of-Persons-Fees-2024-FY2024-25.pdf",
        "title": "NIRA Summary of the Registration of Persons (Fees) Regulations (National ID & Certificates)",
        "url": "https://www.nira.go.ug/media/2024/10/SUMMARY-OF-THE-REGISTRATION-OF-PERSONS-FEES.pdf",
    },
    {
        "mda_code": "MDA3_NIRA",
        "mda_name": "National Identification and Registration Authority (NIRA)",
        "filename": "NIRA-Registration-of-Persons-Act-2015.pdf",
        "title": "Registration of Persons Act, 2015 (Cap. 332) — NIN Issuance & TIN Verification",
        "url": "https://media.ulii.org/media/legislation/17871/source_file/9715a09ebcdf6785/2015-4.pdf",
    },
    {
        "mda_code": "MDA4_PPDA_MoFPED",
        "mda_name": "PPDA (e-GP) & Ministry of Finance, Planning and Economic Development (IFMS)",
        "filename": "PPDA-Public-Procurement-and-Disposal-of-Public-Assets-Act.pdf",
        "title": "Public Procurement and Disposal of Public Assets Act — Tax Clearance & Supplier Compliance",
        "url": "https://media.ulii.org/media/legislation/18276/source_file/40a9202d41ec69ad/2003-1.pdf",
    },
    {
        "mda_code": "MDA5_UNBS",
        "mda_name": "Uganda National Bureau of Standards (UNBS — E-Portal & PVoC Import Clearance)",
        "filename": "UNBS-Uganda-National-Bureau-of-Standards-Act.pdf",
        "title": "Uganda National Bureau of Standards Act — Compulsory Standards & Import Clearance",
        "url": "https://media.ulii.org/media/legislation/73988/source_file/259e194e1d73cc25/1983-1.pdf",
    },
    {
        "mda_code": "MDA6_MLHUD",
        "mda_name": "Ministry of Lands, Housing and Urban Development (MLHUD / UgNLIS)",
        "filename": "MLHUD-Registration-of-Titles-Act-Cap-230.pdf",
        "title": "Registration of Titles Act (Cap. 230) — Land Transfer, Stamp Duty & Registration Fees",
        "url": "https://mlhud.go.ug/wp-content/uploads/2019/03/Registration-of-Titles-Act-Chapter_230.pdf",
    },
    {
        "mda_code": "MDA7_KCCA_MOLG",
        "mda_name": "Kampala Capital City Authority (KCCA e-Citie) & Local Governments (IRAS)",
        "filename": "KCCA-Trade-Licensing-Act-Cap-79.pdf",
        "title": "Trade (Licensing) Act (Cap. 79) — Trading Licenses & Mandatory TIN Requirement",
        "url": "https://media.ulii.org/media/legislation/18578/source_file/177d6b9459ffc6af/1969-14.pdf",
    },
    {
        "mda_code": "MDA7_KCCA_MOLG",
        "mda_name": "Kampala Capital City Authority (KCCA e-Citie) & Local Governments (IRAS)",
        "filename": "MOLG-Local-Governments-Act-Cap-243.pdf",
        "title": "Local Governments Act (Cap. 243) — Local Revenue, Rates & Agency Withholding",
        "url": "https://media.ulii.org/media/legislation/18245/source_file/5461811597227ca8/1997-5.pdf",
    },
    {
        "mda_code": "MDA8_UPF",
        "mda_name": "Uganda Police Force (UPF — Express Penalty Scheme EPS & Traffic Regulation)",
        "filename": "UPF-Traffic-and-Road-Safety-Speed-of-Motor-Vehicles-Order-2026-FY2026-27.pdf",
        "title": "Traffic and Road Safety (Speed of Motor Vehicles) Order, 2026 (SI 50 of 2026)",
        "url": "https://media.ulii.org/media/legislation/131751/source_file/f969ab7100677e30/ug-act-si-2026-50-publication-document.pdf",
    },
    {
        "mda_code": "MDA8_UPF",
        "mda_name": "Uganda Police Force (UPF) & MoWT — Express Penalty & Overloading Fines",
        "filename": "MoWT-UPF-Roads-Act-2019-Express-Penalty.pdf",
        "title": "Roads Act, 2019 — Express Penalty & Overloading Fines Paid via Revenue Collection Bank",
        "url": "https://media.ulii.org/media/legislation/18052/source_file/2019-16.pdf",
    },
    {
        "mda_code": "MDA9_URSB",
        "mda_name": "Uganda Registration Services Bureau (URSB — OBRS Companies & Business Registry)",
        "filename": "URSB-Companies-Act-2012.pdf",
        "title": "Companies Act, 2012 — Company Incorporation, Returns & Statutory Registration Fees",
        "url": "https://media.ulii.org/media/legislation/17888/source_file/7e53dcc2c056a04d/2012-1.pdf",
    },
    {
        "mda_code": "MDA10_NDA_NEMA_UESW",
        "mda_name": "National Drug Authority (NDA) & NEMA — Uganda Electronic Single Window (UESW)",
        "filename": "NDA-National-Drug-Policy-and-Authority-Act.pdf",
        "title": "National Drug Policy and Authority Act (Cap. 206) — Import Verification & Licensing",
        "url": "https://media.ulii.org/media/legislation/121926/source_file/3d9e9752a5995226/1993-13.pdf",
    },
    {
        "mda_code": "MDA10_NDA_NEMA_UESW",
        "mda_name": "National Environment Management Authority (NEMA) — URA Portal PRN Integration",
        "filename": "NEMA-Technical-Guide-URA-PRN-EIA-Fees.pdf",
        "title": "NEMA Technical Guide on How to Generate PRN for 30% EIA Submission & 70% Approval Fees on URA Portal",
        "url": "https://nema.go.ug/sites/all/themes/nema/docs/TECHNICAL%20GUIDE.pdf",
    },
)

MDA_DIRECT_PDF_BY_URL: dict[str, dict[str, str]] = {
    entry["url"]: entry for entry in MDA_STATUTORY_PDF_REGISTRY
}
DEFAULT_MDA_PDF_SOURCE_URLS: tuple[str, ...] = tuple(
    entry["url"] for entry in MDA_STATUTORY_PDF_REGISTRY
)
DEFAULT_ALL_STATUTORY_AND_MDA_SOURCE_URLS: tuple[str, ...] = (
    DEFAULT_STATUTORY_PDF_SOURCE_URLS + DEFAULT_MDA_PDF_SOURCE_URLS
)
DEFAULT_STATUTORY_PDF_SOURCES_CSV = ",".join(DEFAULT_STATUTORY_PDF_SOURCE_URLS)

ALLOWED_STATUTORY_PDF_HOSTS: frozenset[str] = frozenset(
    {
        "ura.go.ug",
        "www.ura.go.ug",
        "uramedia.ura.go.ug",
        "parliament.go.ug",
        "www.parliament.go.ug",
        "bills.parliament.ug",
        "ulii.org",
        "www.ulii.org",
        "media.ulii.org",
        "works.go.ug",
        "www.works.go.ug",
        "immigration.go.ug",
        "www.immigration.go.ug",
        "mia.go.ug",
        "www.mia.go.ug",
        "nira.go.ug",
        "www.nira.go.ug",
        "ppda.go.ug",
        "www.ppda.go.ug",
        "finance.go.ug",
        "www.finance.go.ug",
        "budget.finance.go.ug",
        "unbs.go.ug",
        "www.unbs.go.ug",
        "mlhud.go.ug",
        "www.mlhud.go.ug",
        "kcca.go.ug",
        "www.kcca.go.ug",
        "iras.go.ug",
        "www.iras.go.ug",
        "molg.go.ug",
        "www.molg.go.ug",
        "upf.go.ug",
        "www.upf.go.ug",
        "ursb.go.ug",
        "www.ursb.go.ug",
        "nda.or.ug",
        "www.nda.or.ug",
        "nema.go.ug",
        "www.nema.go.ug",
        "agriculture.go.ug",
        "www.agriculture.go.ug",
        "ugandacoffee.go.ug",
        "www.ugandacoffee.go.ug",
        "singlewindow.go.ug",
        "www.singlewindow.go.ug",
    }
)

# Ugandan MDA hosts whose government CA intermediate chain is omitted from standard OS bundles
_UG_GOV_INTERMEDIATE_TLS_HOSTS: frozenset[str] = frozenset(
    {
        "nira.go.ug",
        "www.nira.go.ug",
        "nema.go.ug",
        "www.nema.go.ug",
    }
)

#: Ingest without the source PDFs present, trusting the manifest's own hashes.
#:
#: Normally ingest re-hashes every PDF on disk, which is what makes a stale
#: export impossible to index. The serving image cannot do that: it ships the
#: derived JSONL but not 500 MB of PDFs. Everything internal to the export is
#: still verified — schema, per-record ``chunk_id`` against its own content,
#: per-source record counts, id uniqueness — so a truncated or edited JSONL is
#: still rejected. Only the source-hash comparison is skipped, and skipping it
#: is logged.
TRUST_MANIFEST = os.getenv("CORPUS_TRUST_MANIFEST", "false").lower() in ("1", "true", "yes", "on")

#: One honest identity for every fetch that feeds the corpus, equal to
#: ``ml.scripts.data_aug.crawl_policy.USER_AGENT`` (the runtime may not import it,
#: so tests/test_crawl_policy.py holds the two together). A browser string here
#: would be a public service impersonating a person (ADR-0023).
CORPUS_FETCH_USER_AGENT = (
    "URA-Taxpayer-Assistant-Crawler/2.0 "
    "(+https://github.com/mpairwe7/FinalYearProject/blob/dev/docs/runbooks/crawl-operations.md)"
)

# Chunk sizing. The defaults mirror ``ml.scripts.data_aug.chunkers.chunk_pdf``
# so the retrieval corpus and the training corpus are cut the same way.
# ``BAAI/bge-m3`` accepts 8192 tokens, so there is headroom to grow these — but
# larger chunks trade retrieval precision for recall, so the defaults are left
# where the training pipeline already has them and changing them is an explicit,
# measurable decision (see the Hit@K / nDCG gate in CI).
PDF_CHUNK_TARGET_CHARS = int(os.getenv("PDF_CHUNK_TARGET_CHARS", "2000"))
PDF_CHUNK_HARD_MAX_CHARS = int(os.getenv("PDF_CHUNK_HARD_MAX_CHARS", "4000"))
PDF_CHUNK_MIN_CHARS = int(os.getenv("PDF_CHUNK_MIN_CHARS", "200"))
PDF_CORPUS_MAX_BYTES = int(os.getenv("PDF_CORPUS_MAX_BYTES", str(100 * 1024 * 1024)))

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
# pymupdf4llm replaces every figure with a literal placeholder line. 956 of 7,035
# chunks carried one (2,491 occurrences), and a query with no real match could be
# answered with "picture [468 x 294] intentionally omitted" as the evidence —
# observed live for "write me a poem about cats". The text is noise for retrieval
# either way: it matches nothing a taxpayer would ask and dilutes the chunk.
_FIGURE_PLACEHOLDER_RE = re.compile(
    # The leading class also eats the markdown run-in the extractor emits around
    # these lines ("**==>"), which would otherwise be left stranded as noise.
    r"[*_#>=\s]*(?:picture|image|figure|table)\s*\[[^\]]*\]\s*intentionally\s+omitted[*_\s]*",
    re.I,
)
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MD_LIST_ITALIC_RE = re.compile(r"\(\s*_([a-zA-Z0-9]+)_\s*\)")
_PDF_BLOCKQUOTE_MARGINAL_RE = re.compile(r"(?m)^\s*>.*$")
_PDF_REVISION_TAG_RE = re.compile(r"\[Rev\.\s*\d{4}\]?", re.IGNORECASE)
_PDF_LEGAL_HEADER_RE = re.compile(
    r"(?i)_?(?:East\s+African\s+Community(?:\s+Customs\s+Management)?(?:\s+Act)?|"
    r"Taxation\s+handbook|Anti-Money\s+Laundering\s+Act|Tax\s+Procedures\s+Code\s+Act|"
    r"Income\s+Tax\s+Act|Value\s+Added\s+Tax\s+Act|EAC-CET)_?",
)
_PDF_STANDALONE_PAGE_RE = re.compile(r"(?m)^\s*\d{1,4}\s*$")

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
    text = _MD_LIST_ITALIC_RE.sub(r"(\1)", text)
    text = _PDF_BLOCKQUOTE_MARGINAL_RE.sub(" ", text)
    text = _PDF_REVISION_TAG_RE.sub(" ", text)
    text = _PDF_LEGAL_HEADER_RE.sub(" ", text)
    text = _PDF_STANDALONE_PAGE_RE.sub(" ", text)
    text = _DIGIT_DOT_RE.sub(".", text)
    text = _LEADER_RUN_RE.sub(" ", text)
    text = _FIGURE_PLACEHOLDER_RE.sub(" ", text)
    text = text.replace("\ufffd", "")
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


def _has_pdf_header(path: Path) -> bool:
    with path.open("rb") as handle:
        head = handle.read(64)
    try:
        validate_pdf_header(head)
    except PdfRejected:
        return False
    return True


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

    try:
        inspect_pdf_path(pdf_path, CORPUS_PDF_LIMITS)
    except PdfRejected as err:
        logger.warning("skipped %s — PDF guard rejected it: %s", pdf_path.name, err)
        return

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


def export_pdf_chunks_to_jsonl(
    pdf_dir: Path,
    jsonl_dir: Path,
    *,
    force_rechunk: bool = False,
) -> dict[str, Any]:
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
    existing_by_pdf: dict[str, dict[str, Any]] = {}
    existing_manifest_path = jsonl_dir / PDF_MANIFEST_NAME
    if not force_rechunk and existing_manifest_path.is_file():
        try:
            prev_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            if (
                isinstance(prev_manifest, dict)
                and prev_manifest.get("schema_version") == PDF_JSONL_SCHEMA_VERSION
                and prev_manifest.get("chunker") == _chunker_params()
                and isinstance(prev_manifest.get("sources"), list)
            ):
                for item in prev_manifest["sources"]:
                    if isinstance(item, dict) and item.get("pdf") and not item.get("duplicate_of"):
                        existing_by_pdf[str(item["pdf"])] = item
        except Exception:
            existing_by_pdf = {}

    manifest_sources: list[dict[str, Any]] = []
    empty_sources: list[str] = []
    # The corpus holds the same document under more than one name — usually with
    # and without the ``ura.go.ug-`` crawl prefix. Chunking both would embed
    # every passage twice and hand one passage several ``top_k`` slots, so the
    # first name wins and the rest are recorded as duplicates of it.
    canonical_by_hash: dict[str, str] = {}
    duplicate_count = 0
    total_records = 0

    for pdf_path in pdf_paths:
        source_sha256 = _sha256_file(pdf_path)
        canonical = canonical_by_hash.get(source_sha256)
        if canonical is not None:
            duplicate_count += 1
            manifest_sources.append(
                {
                    "pdf": pdf_path.name,
                    "jsonl": "",
                    "sha256": source_sha256,
                    "records": 0,
                    "fiscal_year": fiscal_year_from_name(pdf_path.stem),
                    "chars": 0,
                    "duplicate_of": canonical,
                }
            )
            logger.info("skipped %s — byte-identical to %s", pdf_path.name, canonical)
            continue
        canonical_by_hash[source_sha256] = pdf_path.name

        # PyMuPDF opens HTML happily, so a saved error page under a .pdf name
        # became "the employment income guide": two Wayback pages yielded 10,000
        # characters of Internet Archive fundraising text this way (ADR-0023).
        if not _has_pdf_header(pdf_path):
            logger.warning("refused %s: not a PDF", pdf_path.name)
            manifest_sources.append(
                {
                    "pdf": pdf_path.name,
                    "jsonl": "",
                    "sha256": source_sha256,
                    "records": 0,
                    "fiscal_year": "",
                    "chars": 0,
                    "refused": "not a PDF",
                }
            )
            continue

        output_path = jsonl_dir / f"{pdf_path.stem}.jsonl"
        cached_entry = existing_by_pdf.get(pdf_path.name)
        if (
            cached_entry is not None
            and cached_entry.get("sha256") == source_sha256
            and cached_entry.get("jsonl") == output_path.name
            and output_path.is_file()
        ):
            rec_count = int(cached_entry.get("records") or 0)
            char_count = int(cached_entry.get("chars") or 0)
            if rec_count == 0:
                empty_sources.append(pdf_path.name)
            total_records += rec_count
            manifest_sources.append(
                {
                    "pdf": pdf_path.name,
                    "jsonl": output_path.name,
                    "sha256": source_sha256,
                    "records": rec_count,
                    "fiscal_year": fiscal_year_from_name(pdf_path.stem),
                    "chars": char_count,
                }
            )
            continue

        records = list(_chunk_pdf_records(pdf_path, source_sha256))

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
        "duplicate_sources": sorted(
            entry["pdf"] for entry in manifest_sources if entry.get("duplicate_of")
        ),
        "unique_sources": len(canonical_by_hash),
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
        "unique_sources": len(canonical_by_hash),
        "duplicates_skipped": duplicate_count,
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
    manifest = _load_manifest(jsonl_dir)
    by_pdf = {str(entry.get("pdf", "")): entry for entry in manifest["sources"] if isinstance(entry, dict)}
    if len(by_pdf) != len(manifest["sources"]):
        raise CorpusValidationError(f"{jsonl_dir}: duplicate source entries in the manifest")

    pdf_paths = sorted(pdf_dir.glob("*.pdf"))
    if TRUST_MANIFEST and not pdf_paths:
        # Source-free ingest: walk the manifest instead of the directory.
        logger.warning(
            "CORPUS_TRUST_MANIFEST=true and no PDFs at %s — indexing %d sources on the "
            "manifest's own hashes; a source replaced since export cannot be detected here.",
            pdf_dir,
            len(by_pdf),
        )
        pdf_paths = [pdf_dir / name for name in sorted(by_pdf)]
    elif not pdf_paths:
        raise CorpusValidationError(f"No PDF files found in {pdf_dir}")
    else:
        expected_names = {path.name for path in pdf_paths}
        if set(by_pdf) != expected_names:
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
        if pdf_path.is_file():
            source_sha256 = _sha256_file(pdf_path)
            if entry.get("sha256") != source_sha256:
                raise CorpusValidationError(
                    f"{pdf_path}: source changed since PDF JSONL export; regenerate the corpus"
                )
        elif TRUST_MANIFEST:
            source_sha256 = _clean(entry.get("sha256"))
            if not source_sha256:
                raise CorpusValidationError(f"{pdf_path}: manifest entry has no sha256 to trust")
        else:
            raise CorpusValidationError(f"{pdf_path}: source PDF is missing")

        # A byte-identical duplicate contributes no rows: its content is already
        # indexed under the canonical name, so there is no JSONL to read. The
        # hash check above still ran, so the claim is verified rather than
        # trusted.
        if _clean(entry.get("duplicate_of")) or _clean(entry.get("refused")):
            continue

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


# ---------------------------------------------------------------------------
# Statutory Portal PDF Harvester & Qdrant / JSONL Sync (October 2026 Standard)
# ---------------------------------------------------------------------------


def parse_pdf_source_urls(raw: str | Iterable[str] | None = None) -> list[str]:
    """Parse and validate a comma-separated string or iterable of HTTPS statutory source URLs."""
    if raw is None:
        raw = os.getenv("PDF_SOURCE_URLS") or DEFAULT_STATUTORY_PDF_SOURCES_CSV
    if isinstance(raw, str):
        items = [part.strip() for part in re.split(r"[,;\r\n]+", raw) if part.strip()]
    else:
        items = []
        for item in raw:
            for sub in re.split(r"[,;\r\n]+", str(item or "")):
                if sub.strip():
                    items.append(sub.strip())

    if not items:
        raise CorpusValidationError("No statutory PDF source URLs configured.")

    validated: list[str] = []
    seen: set[str] = set()
    for url in items:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if parsed.scheme != "https" or not host:
            raise CorpusValidationError(f"Statutory PDF source URL must use https: {url!r}")
        if parsed.username or parsed.password:
            raise CorpusValidationError(f"Statutory PDF source URL must not contain credentials: {url!r}")
        if host not in ALLOWED_STATUTORY_PDF_HOSTS:
            raise CorpusValidationError(
                f"Host {host!r} is not in ALLOWED_STATUTORY_PDF_HOSTS ({sorted(ALLOWED_STATUTORY_PDF_HOSTS)})."
            )
        if url not in seen:
            seen.add(url)
            validated.append(url)
    return validated


def _normalise_statutory_link(raw_href: str, base_url: str) -> str:
    """Resolve *raw_href* against *base_url*, keeping WPDM `wpdmdl` and pagination `cp` query params."""
    href = _html.unescape(str(raw_href or "").strip())
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    joined = urljoin(base_url, href).split("#")[0]
    parsed = urlparse(joined)
    if parsed.scheme in ("http", "https"):
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if host in ALLOWED_STATUTORY_PDF_HOSTS and parsed.scheme == "http":
            joined = "https://" + joined[len("http://") :]
            parsed = urlparse(joined)
    if parsed.scheme != "https":
        return ""
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if host not in ALLOWED_STATUTORY_PDF_HOSTS:
        return ""
    if "?" in joined:
        path_part, query_part = joined.split("?", 1)
        kept = [
            kv
            for kv in query_part.split("&")
            if kv.lower().startswith(("wpdmdl=", "cp="))
        ]
        return f"{path_part}?{'&'.join(kept)}" if kept else path_part
    return joined


def discover_statutory_pdf_links(
    html_body: str,
    base_url: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """Extract ``(pdf_links, pagination_urls)`` from a Parliament or URA statutory portal page.

    Applies October 2026 DOM content-scope isolation:
    * On Parliament of Uganda Drupal pages (``parliament.go.ug/page/tax-bills-2026``),
      scopes extraction to ``div.node__content`` so navbar/sidebar unrelated PDFs
      (e.g. ``MEMBERS.pdf`` or sidebar acts) are never harvested.
    * On URA WordPress Download Manager pages (``ura.go.ug/download-category/laws-and-acts/``),
      scopes extraction to ``#content_wpdm_package_1``, resolves ``data-downloadurl``
      (preserving ``?wpdmdl=<id>`` while stripping ephemeral ``&refresh=...`` tokens),
      and extracts ``?cp=<page>`` pagination links.
    """
    if not html_body:
        return [], []

    pdf_items: list[dict[str, str]] = []
    next_pages: list[str] = []
    seen_pdfs: set[str] = set()
    seen_pages: set[str] = set()
    base_parsed = urlparse(base_url)
    base_path = (base_parsed.path or "").rstrip("/")

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_body, "html.parser")
        # 1. Discover pagination links before decomposing chrome
        for pg_a in soup.select("ul.wpdm-pagination a[href], ul.pagination a[href], a.page-numbers[href]"):
            pg_url = _normalise_statutory_link(str(pg_a.get("href") or ""), base_url)
            if not pg_url or pg_url == base_url or pg_url in seen_pages:
                continue
            pg_parsed = urlparse(pg_url)
            if (pg_parsed.path or "").rstrip("/").startswith(base_path) and "cp=" in (pg_parsed.query or ""):
                seen_pages.add(pg_url)
                next_pages.append(pg_url)

        # 2. Isolate primary content container
        scope = (
            soup.find("div", class_="node__content")
            or soup.find(id="content_wpdm_package_1")
        )
        if scope is None:
            for chrome in soup.find_all(["nav", "header", "footer", "aside", "script", "style"]):
                chrome.decompose()
            for sidebar in soup.select(".region-sidebar-second, .site-nav, .site-nav-large, .aside"):
                sidebar.decompose()
            scope = soup.find("article") or soup.find("main") or soup

        # 3. Extract WordPress Download Manager (WPDM) cards first
        for card in scope.select("div.card, div.link-template-default"):
            dl_a = card.find("a", attrs={"data-downloadurl": True})
            if not dl_a:
                continue
            dl_url = _normalise_statutory_link(str(dl_a.get("data-downloadurl") or ""), base_url)
            if not dl_url or dl_url in seen_pdfs:
                continue
            title_el = card.find(["h3", "h4", "h2"], class_="package-title") or card.find(["h3", "h4", "h2"])
            title = title_el.get_text(" ", strip=True) if title_el else dl_a.get_text(" ", strip=True)
            seen_pdfs.add(dl_url)
            pdf_items.append(
                {
                    "url": dl_url,
                    "title": _clean(title),
                    "source_page": base_url,
                }
            )

        # 4. Extract direct .pdf links and any remaining data-downloadurl anchors in scope
        for a in scope.find_all("a"):
            raw_dl = a.get("data-downloadurl")
            raw_href = a.get("href")
            for candidate in (raw_dl, raw_href):
                if not candidate:
                    continue
                norm = _normalise_statutory_link(str(candidate), base_url)
                if not norm or norm in seen_pdfs:
                    continue
                low = norm.lower()
                if low.endswith(".pdf") or "wpdmdl=" in low:
                    seen_pdfs.add(norm)
                    pdf_items.append(
                        {
                            "url": norm,
                            "title": _clean(a.get_text(" ", strip=True)),
                            "source_page": base_url,
                        }
                    )
        return pdf_items, next_pages
    except ImportError:
        pass

    # Pure-stdlib regex fallback if BeautifulSoup is unavailable
    for m in re.finditer(r'data-downloadurl=["\']([^"\']+)["\']', html_body, re.IGNORECASE):
        norm = _normalise_statutory_link(m.group(1), base_url)
        if norm and norm not in seen_pdfs:
            seen_pdfs.add(norm)
            pdf_items.append({"url": norm, "title": "", "source_page": base_url})
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+\.pdf)["\'][^>]*>(.*?)</a>', html_body, re.IGNORECASE | re.DOTALL):
        norm = _normalise_statutory_link(m.group(1), base_url)
        if norm and norm not in seen_pdfs:
            seen_pdfs.add(norm)
            title = re.sub(r"<[^>]+>", " ", _html.unescape(m.group(2))).strip()
            pdf_items.append({"url": norm, "title": title, "source_page": base_url})
    return pdf_items, next_pages


def canonical_statutory_pdf_filename(
    url: str,
    *,
    title: str = "",
    content_disposition: str = "",
    source_page: str = "",
) -> str:
    """Derive a clean, deterministic `.pdf` filename with fiscal-year normalization for Tax Bills."""
    # 0. Check curated 10-MDA statutory PDF registry first
    reg_entry = MDA_DIRECT_PDF_BY_URL.get(url)
    if reg_entry and reg_entry.get("filename"):
        return reg_entry["filename"]

    # 1. Honour authoritative server Content-Disposition filename when present
    if content_disposition:
        cd_match = re.search(
            r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)',
            content_disposition,
            re.IGNORECASE,
        )
        if cd_match:
            cd_name = unquote(cd_match.group(1).strip())
            clean_cd = re.sub(r"[^A-Za-z0-9._-]+", "-", cd_name).strip("-_")
            if clean_cd.lower().endswith(".pdf"):
                return clean_cd[:120]

    # 2. Derive from title or URL path
    parsed = urlparse(url)
    path_name = unquote(Path(parsed.path or "").name).strip()
    if path_name.lower().endswith(".pdf"):
        raw_stem = path_name[:-4]
        raw_stem = re.sub(r"_\d+$", "", raw_stem)
    elif title:
        raw_stem = title
    else:
        parts = [p for p in (parsed.path or "").split("/") if p and p not in ("en", "download")]
        raw_stem = parts[-1] if parts else f"statutory-{_stable_id(url)[:10]}"

    # Normalize punctuation -> hyphenated tokens
    normalized = raw_stem.replace("&", " and ")
    normalized = re.sub(r"[(),:;'\"]+", " ", normalized)
    tokens = [tok for tok in re.split(r"[^A-Za-z0-9.]+", normalized) if tok]
    stem = "-".join(tokens).strip("-.")
    if not stem:
        stem = f"statutory-{_stable_id(url)[:10]}"

    # 3. Append canonical FY20XX-YY suffix for Parliamentary Tax Bills (e.g. Bill 2026 -> FY2026-27)
    combined_context = f"{stem} {title} {source_page}"
    if not fiscal_year_from_name(stem) and re.search(r"\bBill\b", combined_context, re.IGNORECASE):
        yr_match = re.search(r"\b(202\d)\b", combined_context)
        if yr_match:
            yr = int(yr_match.group(1))
            stem = f"{stem}-FY{yr}-{(yr + 1) % 100:02d}"

    return f"{stem[:110]}.pdf"


#: Document dates a PDF states about itself. ``crawl_policy.pdf_document_date``
#: is the same reading for the crawler, which may not import this module.
_PDF_INFO_DATE = re.compile(rb"/(?:ModDate|CreationDate)\s*\(D:(\d{4})(\d{2})(\d{2})")
_PDF_XMP_DATE = re.compile(rb"xmp:(?:ModifyDate|CreateDate)(?:>|=[\"'])\s*(\d{4})-(\d{2})-(\d{2})")


def pdf_document_date(data: bytes) -> str:
    """The latest valid non-future calendar date a PDF states about itself, ``YYYY-MM-DD``, or ``""``.

    Invalid calendar dates (such as February 30) and dates later than the fetch
    time are rejected so a malformed or future-stamped file cannot block
    legitimate replacements.
    """
    from datetime import date, datetime, timezone

    today = datetime.now(timezone.utc).date()
    found: list[str] = []
    for pattern in (_PDF_INFO_DATE, _PDF_XMP_DATE):
        for match in pattern.finditer(data):
            try:
                candidate = date(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
            except ValueError:
                continue
            if candidate <= today:
                found.append(candidate.isoformat())
    return max(found, default="")


def _validated_redirect(resp: Any, current: str) -> str:
    """The next hop of a redirect, or a refusal if it leaves the allowlist.

    Checked hop by hop, because ``follow_redirects=True`` would let a page on an
    allowed host hand the harvester to any other host.
    """
    location = resp.headers.get("Location", "").strip()
    if not location:
        raise CorpusValidationError(f"Redirect missing Location header for {current}")
    target = str(resp.url.join(location))
    parsed = urlparse(target)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme != "https" or host not in ALLOWED_STATUTORY_PDF_HOSTS:
        raise CorpusValidationError(f"Disallowed redirect target: {target}")
    return target


def _default_page_fetcher(url: str, timeout: float = 30.0) -> str:
    """Fetch HTML from a validated HTTPS statutory portal page."""
    import httpx

    headers = {
        "User-Agent": CORPUS_FETCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _hop in range(20):
            resp = client.get(current)
            if resp.is_redirect:
                current = _validated_redirect(resp, current)
                continue
            resp.raise_for_status()
            return resp.text
    raise CorpusValidationError(f"Too many redirects fetching {url}")


def _stream_pdf_with_tls_fallback(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str],
    existing_by_name: dict[str, tuple[str, int, str]] | None = None,
    title: str = "",
    source_page: str = "",
) -> dict[str, Any]:
    """Stream *url* over HTTPS with strict TLS verification and redirect allowlist validation.

    A file already held is marked existing only when the downloaded body's
    SHA-256 matches the held digest (a matching name and Content-Length alone
    cannot prove byte identity).
    """
    import httpx

    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _hop in range(20):
            with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    current = _validated_redirect(resp, current)
                    continue
                resp.raise_for_status()
                resp_headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
                buf = bytearray()
                for chunk in resp.iter_bytes(chunk_size=65536):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > PDF_CORPUS_MAX_BYTES:
                        raise CorpusValidationError(
                            f"Remote PDF {url} exceeded corpus max_bytes ({PDF_CORPUS_MAX_BYTES})."
                        )
                raw_bytes = bytes(buf)
                if existing_by_name is not None:
                    cd_header = resp_headers.get("content-disposition", "")
                    cand_name = canonical_statutory_pdf_filename(
                        current,
                        title=title,
                        content_disposition=cd_header,
                        source_page=source_page,
                    )
                    held = existing_by_name.get(cand_name.lower())
                    if (
                        held is not None
                        and len(raw_bytes) == held[1]
                        and hashlib.sha256(raw_bytes).hexdigest() == held[2]
                    ):
                        return {
                            "status": "existing",
                            "pdf": held[0],
                            "url": url,
                            "title": title,
                            "sha256": held[2],
                            "bytes": held[1],
                        }
                return {
                    "status": "fetched",
                    "url": url,
                    "title": title,
                    "source_page": source_page,
                    "raw_bytes": raw_bytes,
                    "headers": resp_headers,
                }
        raise CorpusValidationError(f"Too many redirects fetching PDF {url}")


def _default_pdf_fetcher(url: str, timeout: float = 45.0) -> tuple[bytes, dict[str, str]]:
    """Stream a statutory PDF over HTTPS while enforcing host allowlists and ``PDF_CORPUS_MAX_BYTES``."""
    headers = {"User-Agent": CORPUS_FETCH_USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"}
    res = _stream_pdf_with_tls_fallback(url, timeout=timeout, headers=headers)
    return res["raw_bytes"], res["headers"]


def harvest_remote_pdfs(
    pdf_dir: Path,
    *,
    source_urls: str | Iterable[str] | None = None,
    include_mda_sources: bool = False,
    mirror_dirs: Iterable[Path] = (),
    max_pages_per_source: int = 5,
    timeout: float = 30.0,
    page_fetcher: Callable[[str], str] | None = None,
    pdf_fetcher: Callable[[str], tuple[bytes, dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """Retrieve and download statutory & MDA PDFs from *source_urls* into *pdf_dir* with SHA-256 deduplication.

    Supports:
    * ``https://www.parliament.go.ug/page/tax-bills-2026``
    * ``https://ura.go.ug/download-category/laws-and-acts/`` (including WPDM ``?wpdmdl=`` & ``?cp=`` pagination)
    * Direct PDF URLs across the 10 high-dependency Ugandan MDAs (:data:`MDA_STATUTORY_PDF_REGISTRY`).
    """
    urls = parse_pdf_source_urls(source_urls)
    if include_mda_sources:
        for mda_url in DEFAULT_MDA_PDF_SOURCE_URLS:
            if mda_url not in urls:
                urls.append(mda_url)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    mirrors = [Path(m) for m in mirror_dirs if Path(m).resolve() != pdf_dir.resolve()]
    for m_dir in mirrors:
        m_dir.mkdir(parents=True, exist_ok=True)

    fetch_page = page_fetcher or (lambda u: _default_page_fetcher(u, timeout=timeout))
    fetch_pdf = pdf_fetcher or (lambda u: _default_pdf_fetcher(u, timeout=max(timeout, 45.0)))

    # Existing PDFs by SHA-256 (the only proof two files are one document) and by name.
    existing_by_sha: dict[str, str] = {}
    existing_by_name: dict[str, tuple[str, int, str]] = {}
    for existing_pdf in sorted(pdf_dir.glob("*.pdf")):
        try:
            sha = _sha256_file(existing_pdf)
            sz = existing_pdf.stat().st_size
            existing_by_sha.setdefault(sha, existing_pdf.name)
            existing_by_name[existing_pdf.name.lower()] = (existing_pdf.name, sz, sha)
        except Exception:
            logger.debug("Failed hashing existing PDF %s", existing_pdf, exc_info=True)

    discovered_items: list[dict[str, str]] = []
    seen_pdf_urls: set[str] = set()
    visited_pages: list[str] = []
    page_errors: list[dict[str, str]] = []

    for seed_url in urls:
        # Direct PDF URLs (e.g. 10-MDA statutory/fee PDFs) do not need HTML page parsing
        if seed_url in MDA_DIRECT_PDF_BY_URL or (urlparse(seed_url).path or "").lower().endswith(".pdf"):
            if seed_url not in seen_pdf_urls:
                seen_pdf_urls.add(seed_url)
                reg = MDA_DIRECT_PDF_BY_URL.get(seed_url, {})
                discovered_items.append(
                    {
                        "url": seed_url,
                        "title": reg.get("title", ""),
                        "source_page": seed_url,
                    }
                )
            continue

        queue = [seed_url]
        pages_for_seed = 0
        while queue and pages_for_seed < max_pages_per_source:
            page_url = queue.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.append(page_url)
            pages_for_seed += 1
            try:
                html_body = fetch_page(page_url)
            except Exception as exc:
                logger.warning("Failed fetching statutory source page %s: %s", page_url, exc)
                page_errors.append({"url": page_url, "error": str(exc)})
                continue

            found_pdfs, next_pages = discover_statutory_pdf_links(html_body, page_url)
            for item in found_pdfs:
                if item["url"] not in seen_pdf_urls:
                    seen_pdf_urls.add(item["url"])
                    discovered_items.append(item)
            for nxt in next_pages:
                if nxt not in visited_pages and nxt not in queue:
                    queue.append(nxt)

    downloaded: list[dict[str, Any]] = []
    verified_existing: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    def _probe_or_fetch_item(item: dict[str, str]) -> dict[str, Any]:
        pdf_url = item["url"]
        title = item.get("title", "")
        source_page = item.get("source_page", "")
        if pdf_fetcher is None:
            headers_req = {"User-Agent": CORPUS_FETCH_USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"}
            return _stream_pdf_with_tls_fallback(
                pdf_url,
                timeout=max(timeout, 45.0),
                headers=headers_req,
                existing_by_name=existing_by_name,
                title=title,
                source_page=source_page,
            )

        raw_bytes, headers = fetch_pdf(pdf_url)
        return {
            "status": "fetched",
            "url": pdf_url,
            "title": title,
            "source_page": source_page,
            "raw_bytes": raw_bytes,
            "headers": headers,
        }

    from concurrent.futures import ThreadPoolExecutor

    max_workers = min(6, max(1, len(discovered_items)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [(item, pool.submit(_probe_or_fetch_item, item)) for item in discovered_items]
        for item, fut in futures:
            pdf_url = item["url"]
            title = item.get("title", "")
            source_page = item.get("source_page", "")
            try:
                outcome = fut.result()
            except Exception as exc:
                logger.warning("Failed downloading statutory PDF %s: %s", pdf_url, exc)
                rejected.append({"url": pdf_url, "title": title, "reason": f"download_failed: {exc}"})
                continue

            if outcome.get("status") == "existing":
                verified_existing.append(
                    {
                        "pdf": outcome["pdf"],
                        "url": outcome["url"],
                        "title": outcome["title"],
                        "sha256": outcome["sha256"],
                        "bytes": outcome["bytes"],
                    }
                )
                continue

            raw_bytes = outcome["raw_bytes"]
            headers = outcome["headers"]
            sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
            existing_name = existing_by_sha.get(sha256_hex)
            if existing_name is not None:
                verified_existing.append(
                    {
                        "pdf": existing_name,
                        "url": pdf_url,
                        "title": title,
                        "sha256": sha256_hex,
                        "bytes": len(raw_bytes),
                    }
                )
                continue

            cd_header = headers.get("content-disposition", "")
            filename = canonical_statutory_pdf_filename(
                pdf_url,
                title=title,
                content_disposition=cd_header,
                source_page=source_page,
            )
            try:
                validate_pdf_header(raw_bytes)
                inspect_pdf_bytes(raw_bytes, limits=CORPUS_PDF_LIMITS)
                scan_bytes(raw_bytes, filename=filename)
            except (PdfRejected, MalwareRejected) as exc:
                logger.warning("Rejected downloaded PDF %s (%s): %s", filename, pdf_url, exc)
                rejected.append({"url": pdf_url, "pdf": filename, "reason": str(exc)})
                continue

            target_path = pdf_dir / filename
            replaced = target_path.is_file()
            if replaced:
                # Older never replaces newer: a re-upload of last year's edition
                # at the same address is reported, not installed. An oversized
                # held file is not read into memory (treated as undated so a
                # valid replacement within PDF_CORPUS_MAX_BYTES may overwrite it).
                held_size = target_path.stat().st_size
                held_date = (
                    pdf_document_date(target_path.read_bytes())
                    if held_size <= PDF_CORPUS_MAX_BYTES
                    else ""
                )
                new_date = pdf_document_date(raw_bytes)
                if held_date and new_date and new_date < held_date:
                    logger.warning("Kept %s: the remote file dated %s is older than the %s copy", filename, new_date, held_date)
                    rejected.append({"url": pdf_url, "pdf": filename, "reason": f"older_than_held: {new_date} < {held_date}"})
                    continue
            tmp_path = target_path.with_suffix(".pdf.tmp")
            tmp_path.write_bytes(raw_bytes)
            tmp_path.replace(target_path)
            existing_by_sha[sha256_hex] = filename
            existing_by_name[filename.lower()] = (filename, len(raw_bytes), sha256_hex)

            downloaded.append(
                {
                    "pdf": filename,
                    "url": pdf_url,
                    "title": title,
                    "sha256": sha256_hex,
                    "bytes": len(raw_bytes),
                    "fiscal_year": fiscal_year_from_name(Path(filename).stem),
                    "replaced_earlier_edition": replaced,
                }
            )
            logger.info("Downloaded statutory PDF %s (%d bytes) from %s", filename, len(raw_bytes), pdf_url)

    # Keep any configured mirror_dirs (e.g. App/Data/pdfs) in sync with pdf_dir
    mirrored_count = 0
    if mirrors:
        for src_pdf in sorted(pdf_dir.glob("*.pdf")):
            for m_dir in mirrors:
                dest_pdf = m_dir / src_pdf.name
                if not dest_pdf.is_file() or _sha256_file(dest_pdf) != _sha256_file(src_pdf):
                    shutil.copy2(src_pdf, dest_pdf)
                    mirrored_count += 1

    return {
        "source_urls": urls,
        "pages_scanned": visited_pages,
        "discovered_count": len(discovered_items),
        "downloaded_count": len(downloaded),
        "verified_existing_count": len(verified_existing),
        "rejected_count": len(rejected),
        "mirrored_files": mirrored_count,
        "downloaded": downloaded,
        "verified_existing": verified_existing,
        "rejected": rejected,
        "page_errors": page_errors,
    }

