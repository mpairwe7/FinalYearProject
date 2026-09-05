#!/usr/bin/env python3
"""Re-index the URA corpus into Cloudflare Vectorize via Workers AI bge-m3.

Mirrors the Qdrant indexer (the SAME chunks + deterministic ids) but computes
dense vectors through the Cloudflare AI Gateway (Workers AI ``@cf/baai/bge-m3``)
— no local torch — then upserts them with ``wrangler vectorize upsert``
(idempotent: re-running this over an already-seeded index overwrites unchanged
content in place rather than erroring on it). This is what populates the index
``HybridRetriever``'s Vectorize fallback queries, restoring real dense
retrieval on Crane Cloud and the HF Space whenever their baked-in Qdrant
collection is sparse-only (the CPU-only images ship no torch to compute dense
vectors locally) — see ``docs/CLOUDFLARE_FALLBACKS.md``.

Prerequisites (load the gitignored .env into the process env first so both this
script *and* wrangler see the credentials, without printing them):

    set -a; . .env; set +a            # exports CLOUDFLARE_*, CF_AIG_*, VECTORIZE_INDEX
    wrangler --version                # v3 (Node 20) on PATH, authed via CLOUDFLARE_API_TOKEN

Usage:
    python scripts/reindex_vectorize.py --create     # create the index, embed, upsert
    python scripts/reindex_vectorize.py              # embed + upsert (index already created)
    python scripts/reindex_vectorize.py --dry-run    # build the NDJSON only; no upsert
    python scripts/reindex_vectorize.py --verify "What is the VAT rate?"   # query smoke-test
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # noqa: S404 — invokes the trusted `wrangler` CLI only
import sys
import tempfile
import time
from pathlib import Path

import httpx

# Make `app` importable when run from App/backend/scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.faq_corpus import CorpusValidationError  # noqa: E402
from app.crawl_corpus import CRAWL_MANIFEST_NAME, ingest_crawl_jsonls  # noqa: E402
from app.indexer import (  # noqa: E402
    CRAWL_JSONL_DIR,
    CRAWL_PAGES_DIR,
    DATA_DIR,
    FAQ_JSONL_DIR,
    PDF_DIR,
    PDF_JSONL_DIR,
    TEACHER_QA_DIR,
    _embedding_text,
    annotate_fiscal_year,
    ingest_faq_jsonls,
    ingest_pdf_jsonls,
    ingest_teacher_qa_jsonls,
)
from app.pdf_corpus import PDF_MANIFEST_NAME  # noqa: E402
from app.providers import config as cfg  # noqa: E402
from app.providers import gateway as gw  # noqa: E402
from app.retriever import deterministic_point_id  # noqa: E402

EMBED_BATCH = int(os.getenv("INDEX_BATCH_SIZE", "32"))
WRANGLER = os.getenv("WRANGLER_BIN", "wrangler")


def load_documents(*, allow_partial: bool = False) -> list[dict]:
    """Load every JSONL corpus the Qdrant indexer indexes.

    Vectorize and Qdrant must be built from one source of truth, otherwise the
    retriever's dense fallback answers from a corpus the primary index no longer
    contains.  PDFs, crawl pages and FAQ CSVs are deliberately not read directly
    — they reach the index only as validated FAQ / teacher-QA / PDF-chunk /
    crawl-chunk JSONL.

    Because Vectorize is what serves when Qdrant is down, seeding it from a
    subset is a silent capability regression: the same question answers from the
    full corpus normally and from the FAQ rows only during an outage. A missing
    export therefore aborts unless *allow_partial* is set.
    """
    optional = [
        ("PDF chunk", PDF_JSONL_DIR / PDF_MANIFEST_NAME, "--export-pdf-jsonl"),
        ("Crawl chunk", CRAWL_JSONL_DIR / CRAWL_MANIFEST_NAME, "--export-crawl-jsonl"),
    ]
    absent = [(label, flag) for label, manifest, flag in optional if not manifest.is_file()]
    if absent and not allow_partial:
        detail = "\n".join(
            f"  - {label} corpus not exported; run: python -m app.indexer {flag}"
            for label, flag in absent
        )
        sys.exit(
            "Refusing to seed Vectorize from an incomplete corpus — it would answer "
            "from fewer documents than Qdrant whenever it is serving:\n"
            f"{detail}\n"
            "Pass --allow-partial to seed anyway."
        )

    try:
        docs = ingest_faq_jsonls(DATA_DIR, FAQ_JSONL_DIR) + ingest_teacher_qa_jsonls(TEACHER_QA_DIR)
        if (PDF_JSONL_DIR / PDF_MANIFEST_NAME).is_file():
            docs += ingest_pdf_jsonls(PDF_DIR, PDF_JSONL_DIR)
        else:
            print(f"  PDF chunk corpus SKIPPED (no export at {PDF_JSONL_DIR})", flush=True)
        if (CRAWL_JSONL_DIR / CRAWL_MANIFEST_NAME).is_file():
            docs += ingest_crawl_jsonls(CRAWL_PAGES_DIR, CRAWL_JSONL_DIR)
        else:
            print(f"  Crawl chunk corpus SKIPPED (no export at {CRAWL_JSONL_DIR})", flush=True)
    except CorpusValidationError as exc:
        sys.exit(f"Corpus validation failed: {exc}")
    if not docs:
        sys.exit(f"No documents found under {FAQ_JSONL_DIR} / {TEACHER_QA_DIR}")
    # Same temporal metadata the Qdrant indexer stamps, so both indexes rank
    # editions identically.
    return annotate_fiscal_year(docs)


_EMBED_RETRIES = 5
_EMBED_BACKOFF_BASE_S = 1.0


def _embed_batch_with_retry(batch: list[str]) -> list[list[float]]:
    """One batch, retried with backoff on the free tier's occasional blip.

    Observed directly while seeding this index: the same batch, re-sent
    unchanged, alternates between a clean 200 and a 400 — including one run
    of 3 straight 400s, which is why this retries 5 times (exponential
    backoff: 1/2/4/8/16s) rather than the fewer attempts that first looked
    sufficient. Confirmed to be a genuine intermittent Workers AI / AI Gateway
    issue and not a malformed document: bisecting a consistently-failing
    range down to a single chunk stopped reproducing once the range got small
    enough to test repeatably, and that same chunk later embedded cleanly on
    its own. With ~250 batches needed for the full corpus, this fires more
    than once per run, and `embed_all` originally had no retry at all — one
    blip lost every vector embedded before it, since insertion only happens
    after the whole corpus is embedded.
    """
    last: Exception | None = None
    for attempt in range(1, _EMBED_RETRIES + 1):
        try:
            return gw.workers_ai_embed(batch)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last = exc
            if attempt == _EMBED_RETRIES:
                break
            delay = _EMBED_BACKOFF_BASE_S * (2 ** (attempt - 1))
            print(f"  batch failed ({exc}); retrying in {delay:.1f}s ({attempt}/{_EMBED_RETRIES})", flush=True)
            time.sleep(delay)
    assert last is not None  # noqa: S101 — loop always sets it before falling through
    raise last


def embed_all(texts: list[str]) -> tuple[list[list[float]], set[int]]:
    """Embed every text; a batch that exhausts retries is skipped, not fatal.

    Observed directly while seeding this index: a batch can outlast even 5
    retries with exponential backoff (up to ~30s), which looks less like a
    one-off blip and more like a rate/budget window on the Workers AI / AI
    Gateway free tier. ~250 batches are needed for the full corpus, so it is
    not safe to bet the whole run on that window always clearing in time —
    but insertion only happens after every batch is embedded, so one
    unlucky batch used to cost every vector embedded before it too.

    Returns the vectors for whatever embedded successfully, plus the set of
    *text-list indices* that were skipped, so the caller can drop the
    matching docs and keep both lists aligned. A skip is not silent: it
    prints a clear marker and the run ends with a count and a instruction to
    re-run — safe because `deterministic_point_id` + `upsert` make a re-run
    a no-op for everything that already succeeded and fill in the rest.
    """
    vectors: list[list[float]] = []
    skipped: set[int] = set()
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        try:
            vectors.extend(_embed_batch_with_retry(batch))
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            skipped.update(range(i, i + len(batch)))
            print(f"  SKIPPED [{i}:{i + len(batch)}] after exhausting retries: {exc}", flush=True)
            continue
        note = f" ({len(skipped)} skipped so far)" if skipped else ""
        print(f"  embedded {min(i + EMBED_BATCH, len(texts))}/{len(texts)}{note}", flush=True)
        time.sleep(0.2)  # gentle on the free tier
    if skipped:
        print(
            f"\nWARNING: {len(skipped)}/{len(texts)} chunks skipped after exhausting "
            "retries. Re-run this script (without --create) to fill the gap — "
            "upsert is idempotent for everything that already succeeded.",
            flush=True,
        )
    return vectors, skipped


def to_ndjson(docs: list[dict], vectors: list[list[float]], path: Path) -> int:
    """Write one ``{id, values, metadata}`` object per line (wrangler NDJSON)."""
    with open(path, "w", encoding="utf-8") as fh:
        for doc, vec in zip(docs, vectors, strict=True):
            obj = {
                "id": deterministic_point_id(doc),
                "values": vec,
                "metadata": {
                    "text": (doc.get("text") or "")[:2000],
                    "question": str(doc.get("question") or "")[:500],
                    "answer": str(doc.get("answer") or "")[:1500],
                    "source": doc.get("source", ""),
                    "page": str(doc.get("page", "")),
                    "section": doc.get("section", ""),
                    "tag": doc.get("tag", ""),
                    "chunk_id": doc.get("chunk_id", ""),
                    "doc_type": doc.get("doc_type", ""),
                    # Temporal metadata so the dense fallback can rank current
                    # guidance above superseded editions, as Qdrant does.
                    "fiscal_year": doc.get("fiscal_year", ""),
                },
            }
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return len(docs)


def _wrangler(args: list[str]) -> None:
    print("+ wrangler", " ".join(args[:2]), "...", flush=True)
    subprocess.run([WRANGLER, *args], check=True)  # noqa: S603 — fixed argv, no shell


def _verify(query: str) -> None:
    from app.providers import vectorize as vz

    qvec = gw.workers_ai_embed([query])[0]
    hits = vz.vectorize_query(qvec, top_k=4)
    print(f"Top {len(hits)} hits for {query!r}:")
    for h in hits:
        print(f"  {h['score']:.3f}  [{h.get('source', '')}] {h['text'][:80]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-index the URA corpus into Cloudflare Vectorize.")
    ap.add_argument("--create", action="store_true", help="create the Vectorize index first")
    ap.add_argument("--dry-run", action="store_true", help="build the NDJSON only; skip insert")
    ap.add_argument("--verify", metavar="QUERY", help="embed QUERY, query the index, print top hits")
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help="seed even when the PDF/crawl corpora have not been exported",
    )
    args = ap.parse_args()

    if not cfg.is_vectorize_configured():
        sys.exit(
            "Cloudflare/Vectorize not configured. Run `set -a; . .env; set +a` with "
            "CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, CF_AIG_GATEWAY, CF_AIG_TOKEN, "
            "VECTORIZE_INDEX first."
        )
    index = cfg.get_cloud_settings().vectorize_index

    if args.verify:
        _verify(args.verify)
        return

    docs = load_documents(allow_partial=args.allow_partial)
    by_type: dict[str, int] = {}
    for doc in docs:
        by_type[doc.get("doc_type", "")] = by_type.get(doc.get("doc_type", ""), 0) + 1
    print(f"Loaded {len(docs)} chunks {by_type}; embedding via Workers AI @cf/baai/bge-m3 ...")
    # Same embedding input as the Qdrant indexer: PDF chunks carry their
    # contextual prefix, every other corpus embeds its text verbatim.
    vectors, skipped = embed_all([_embedding_text(d) for d in docs])
    if skipped:
        docs = [d for i, d in enumerate(docs) if i not in skipped]
    dim = len(vectors[0]) if vectors else 0
    print(f"Embedded {len(vectors)} vectors (dim={dim}).")

    out = Path(tempfile.gettempdir()) / "ura_vectorize.ndjson"
    n = to_ndjson(docs, vectors, out)
    print(f"Wrote {n} vectors → {out}")

    if args.create:
        _wrangler(["vectorize", "create", index, "--dimensions", str(dim or 1024), "--metric", "cosine"])
        try:
            _wrangler(
                ["vectorize", "create-metadata-index", index, "--property-name", "tag", "--type", "string"]
            )
        except subprocess.CalledProcessError:
            print("  (metadata index already exists or not supported — continuing)")

    if args.dry_run:
        print("Dry run — NDJSON ready; skipping upsert.")
        return

    # upsert, not insert: `insert` errors on any id already present, and
    # deterministic_point_id() intentionally reuses the same id for an
    # unchanged chunk across runs. A re-run against an already-seeded index
    # (the normal case — corpus content changes far more often than the index
    # is rebuilt from scratch) needs every existing id overwritten in place,
    # which is exactly what `upsert` does and `insert` refuses to.
    _wrangler(["vectorize", "upsert", index, "--file", str(out)])
    print(f"\nUpserted {n} vectors into Vectorize index '{index}'.")
    print(f"Verify: python scripts/reindex_vectorize.py --verify 'What is the VAT rate?'")


if __name__ == "__main__":
    main()
