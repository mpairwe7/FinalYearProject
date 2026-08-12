"""JSONL-first indexing pipeline for the local Qdrant vector store.

Every source reaches the index as validated JSONL carrying source hashes and
stable record ids, so a partial or stale export is rejected rather than
silently indexed. Three corpora feed it:

* canonical FAQ JSONL exported from ``ura_*_faqs.csv``;
* normalised teacher-QA JSONL generated from the PDF corpus;
* hierarchical PDF chunk JSONL exported by :mod:`app.pdf_corpus`; and
* crawled-page chunk JSONL exported by :mod:`app.crawl_corpus`.

Evaluation and red-team JSONL are never indexed.

The two chunk corpora need an offline export first (they depend on ``ml/`` and,
for PDFs, ``pymupdf4llm`` — neither ships in the serving image). Without one
they are skipped, so a FAQ-only deployment is unaffected.

Usage:
    python -m app.indexer --export-faq-jsonl          # refresh canonical FAQ JSONL
    python -m app.indexer --export-pdf-jsonl          # chunk PDFs → canonical JSONL (offline)
    python -m app.indexer --export-crawl-jsonl        # chunk crawled pages → JSONL (offline)
    python -m app.indexer --recreate                  # index every exported corpus
    python -m app.indexer --faq-jsonl-only            # FAQ JSONL only
    python -m app.indexer --teacher-qa-only           # teacher-QA JSONL only
    python -m app.indexer --pdf-jsonl-only            # PDF chunk JSONL only
    python -m app.indexer --crawl-jsonl-only          # crawl chunk JSONL only
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "") or None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "ura_knowledge_base")
# 2026 default: BAAI/bge-m3 multilingual embeddings (1024-dim).
# Override DENSE_MODEL / DENSE_DIM when re-indexing legacy collections.
DENSE_MODEL_NAME = os.getenv("DENSE_MODEL", "BAAI/bge-m3")
DENSE_DIM = int(os.getenv("DENSE_DIM", "1024"))
BATCH_SIZE = int(os.getenv("INDEX_BATCH_SIZE", "64"))

from ._root import APP_DATA_ROOT as _APP_DATA_ROOT
from ._root import PROJECT_ROOT as _PROJECT_ROOT
from .faq_corpus import (
    CorpusValidationError,
    export_faq_csvs_to_jsonl,
    ingest_faq_jsonls,
    ingest_teacher_qa_jsonls,
)
from .crawl_corpus import (
    CRAWL_MANIFEST_NAME,
    export_crawl_pages_to_jsonl,
    ingest_crawl_jsonls,
)
from .pdf_corpus import (
    PDF_MANIFEST_NAME,
    export_pdf_chunks_to_jsonl,
    fiscal_year_from_name,
    ingest_pdf_jsonls,
)

DATA_DIR = Path(os.getenv("DATA_DIR", str(_APP_DATA_ROOT / "dataset")))
FAQ_JSONL_DIR = Path(os.getenv("FAQ_JSONL_DIR", str(_APP_DATA_ROOT / "faq_jsonl")))
TEACHER_QA_DIR = Path(os.getenv("TEACHER_QA_DIR", str(_APP_DATA_ROOT / "teacher_qa")))
PDF_DIR = Path(os.getenv("PDF_DIR", str(_APP_DATA_ROOT / "pdfs")))
PDF_JSONL_DIR = Path(os.getenv("PDF_JSONL_DIR", str(_APP_DATA_ROOT / "pdf_jsonl")))
# The crawl lives at the repository root (it is committed), not under App/Data.
CRAWL_PAGES_DIR = Path(os.getenv("CRAWL_PAGES_DIR", str(_PROJECT_ROOT / "Data" / "crawl" / "pages")))
CRAWL_JSONL_DIR = Path(os.getenv("CRAWL_JSONL_DIR", str(_APP_DATA_ROOT / "crawl_jsonl")))
BM25_STATE_PATH = Path(
    os.getenv("BM25_STATE_PATH", str(_APP_DATA_ROOT.parent / "Model" / "bm25_state.json"))
)


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------
def _embedding_text(doc: dict[str, Any]) -> str:
    """Return the text to embed and BM25-fit for *doc*.

    PDF chunks carry an ``embed_text`` that prepends the contextual prefix
    (document + heading trail) to the chunk body, so an isolated chunk stays
    retrievable without polluting the text that gets displayed and cited.
    Every other corpus embeds its ``text`` verbatim.
    """
    return doc.get("embed_text") or doc["text"]


def annotate_fiscal_year(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every vector document carries a ``fiscal_year``, in place.

    PDF chunks already record the edition their source PDF declares. FAQ and
    teacher-QA rows do not, so it is derived from the source filename here —
    the indexer is where the corpora are composed, which keeps
    :mod:`app.faq_corpus` free of a dependency on :mod:`app.pdf_corpus` and
    leaves the validated FAQ JSONL schema untouched.

    Idempotent: an existing value is never overwritten. An empty value means
    *unknown*, which :func:`app.retriever.fiscal_year_rank` treats as
    "do not rank", not as "old".
    """
    for doc in documents:
        if not doc.get("fiscal_year"):
            doc["fiscal_year"] = fiscal_year_from_name(Path(doc.get("source", "")).stem)
    return documents


def _vector_payload(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the Qdrant payload for *doc*.

    ``embed_text`` is an input to embedding rather than retrievable content, so
    keeping it would store every chunk body twice per point.
    """
    return {k: v for k, v in doc.items() if k != "embed_text"}
def build_index(
    documents: list[dict[str, Any]],
    recreate: bool = False,
) -> dict[str, Any]:
    """Embed *documents* and upsert into Qdrant with dense + sparse vectors.

    Returns indexing statistics.
    """
    from qdrant_client import QdrantClient, models
    from sentence_transformers import SentenceTransformer

    from .retriever import (
        BM25SparseEncoder,
        bm25_binding_sentinel_id,
        deterministic_point_id,
    )

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
    dense_model = SentenceTransformer(DENSE_MODEL_NAME)

    # -- Collection management -----------------------------------------------
    existing = [c.name for c in client.get_collections().collections]
    if recreate and QDRANT_COLLECTION in existing:
        client.delete_collection(QDRANT_COLLECTION)
        logger.info("Deleted existing collection '%s'", QDRANT_COLLECTION)

    if QDRANT_COLLECTION not in existing or recreate:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config={
                "dense": models.VectorParams(
                    size=DENSE_DIM,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False),
                ),
            },
        )
        logger.info("Created Qdrant collection '%s'", QDRANT_COLLECTION)

    # -- BM25 sparse encoder -------------------------------------------------
    # Fitted on the same text that gets embedded, so dense and sparse retrieval
    # see an identical view of the corpus.
    texts = [_embedding_text(d) for d in documents]
    sparse_encoder = BM25SparseEncoder().fit(texts)

    BM25_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BM25_STATE_PATH, "w") as f:
        json.dump(sparse_encoder.to_dict(), f)
    logger.info("Saved BM25 state to %s", BM25_STATE_PATH)

    # -- Batch embed + upsert ------------------------------------------------
    total_upserted = 0
    for i in range(0, len(documents), BATCH_SIZE):
        batch = documents[i : i + BATCH_SIZE]
        batch_texts = [_embedding_text(d) for d in batch]

        dense_embeddings = dense_model.encode(batch_texts, show_progress_bar=False)

        points: list[models.PointStruct] = []
        for j, doc in enumerate(batch):
            sparse_idx, sparse_val = sparse_encoder.encode(_embedding_text(doc))
            vectors: dict[str, Any] = {"dense": dense_embeddings[j].tolist()}
            if sparse_idx:
                vectors["sparse"] = models.SparseVector(indices=sparse_idx, values=sparse_val)

            payload = _vector_payload(doc)
            points.append(
                models.PointStruct(
                    id=deterministic_point_id(doc),
                    vector=vectors,
                    payload=payload,
                )
            )

        client.upsert(collection_name=QDRANT_COLLECTION, points=points)
        total_upserted += len(points)
        logger.info(
            "Upserted batch %d–%d (%d/%d)",
            i,
            i + len(batch),
            total_upserted,
            len(documents),
        )

    # Stamp the corpus hash into Qdrant so the retriever can detect a
    # bm25_state.json that is out of sync with these vectors (P1-6).
    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[
            models.PointStruct(
                id=bm25_binding_sentinel_id(QDRANT_COLLECTION),
                vector={"dense": [0.0] * DENSE_DIM},
                payload={
                    "_meta": "bm25_binding",
                    "corpus_hash": sparse_encoder.corpus_hash,
                    # Dense-side binding: querying a bge-m3 collection with a
                    # different encoder returns confident nonsense rather than
                    # an error, so the retriever verifies this at init.
                    "dense_model": DENSE_MODEL_NAME,
                    "dense_dim": DENSE_DIM,
                },
            )
        ],
    )

    stats = {
        "collection": QDRANT_COLLECTION,
        "total_documents": len(documents),
        "total_upserted": total_upserted,
        "faq_jsonl_documents": sum(1 for d in documents if d["doc_type"] == "faq_jsonl"),
        "teacher_qa_jsonl_documents": sum(
            1 for d in documents if d["doc_type"] == "teacher_qa_jsonl"
        ),
        "pdf_chunk_documents": sum(1 for d in documents if d["doc_type"] == "pdf_chunk"),
        "crawl_chunk_documents": sum(1 for d in documents if d["doc_type"] == "crawl_chunk"),
    }
    logger.info("Indexing complete: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Index validated FAQ, teacher-QA and PDF-chunk JSONL into Qdrant"
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--faq-jsonl-only", action="store_true")
    source_group.add_argument("--teacher-qa-only", action="store_true")
    source_group.add_argument("--pdf-jsonl-only", action="store_true")
    source_group.add_argument("--crawl-jsonl-only", action="store_true")
    parser.add_argument("--export-faq-jsonl", action="store_true")
    parser.add_argument(
        "--export-pdf-jsonl",
        action="store_true",
        help="Chunk every PDF into canonical JSONL (offline; needs ml/ + pymupdf4llm)",
    )
    parser.add_argument(
        "--export-crawl-jsonl",
        action="store_true",
        help="Chunk the newest capture of every substantive crawled page (offline; needs ml/)",
    )
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    parser.add_argument("--csv-dir", type=str, default=str(DATA_DIR))
    parser.add_argument("--faq-jsonl-dir", type=str, default=str(FAQ_JSONL_DIR))
    parser.add_argument("--teacher-qa-dir", type=str, default=str(TEACHER_QA_DIR))
    parser.add_argument("--pdf-dir", type=str, default=str(PDF_DIR))
    parser.add_argument("--pdf-jsonl-dir", type=str, default=str(PDF_JSONL_DIR))
    parser.add_argument("--crawl-pages-dir", type=str, default=str(CRAWL_PAGES_DIR))
    parser.add_argument("--crawl-jsonl-dir", type=str, default=str(CRAWL_JSONL_DIR))
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    faq_jsonl_dir = Path(args.faq_jsonl_dir)
    teacher_qa_dir = Path(args.teacher_qa_dir)
    pdf_dir = Path(args.pdf_dir)
    pdf_jsonl_dir = Path(args.pdf_jsonl_dir)
    crawl_pages_dir = Path(args.crawl_pages_dir)
    crawl_jsonl_dir = Path(args.crawl_jsonl_dir)

    if args.export_faq_jsonl or args.export_pdf_jsonl or args.export_crawl_jsonl:
        try:
            if args.export_faq_jsonl:
                logger.info("FAQ JSONL export complete: %s", export_faq_csvs_to_jsonl(csv_dir, faq_jsonl_dir))
            if args.export_pdf_jsonl:
                logger.info(
                    "PDF chunk JSONL export complete: %s",
                    export_pdf_chunks_to_jsonl(pdf_dir, pdf_jsonl_dir),
                )
            if args.export_crawl_jsonl:
                logger.info(
                    "Crawl chunk JSONL export complete: %s",
                    export_crawl_pages_to_jsonl(crawl_pages_dir, crawl_jsonl_dir),
                )
        except CorpusValidationError as exc:
            logger.error("Corpus export failed: %s", exc)
            raise SystemExit(2) from exc
        return

    # At most one --*-only flag is accepted (mutually exclusive group); it pins
    # the run to a single corpus. Otherwise every corpus is indexed, except that
    # PDF and crawl chunks require an export first — a missing export is "not
    # configured" rather than an error, so FAQ-only deployments keep working
    # unchanged, while an explicit --pdf-jsonl-only/--crawl-jsonl-only still
    # fails loudly with the command to run.
    only = next(
        (
            name
            for name, selected in (
                ("faq", args.faq_jsonl_only),
                ("teacher_qa", args.teacher_qa_only),
                ("pdf", args.pdf_jsonl_only),
                ("crawl", args.crawl_jsonl_only),
            )
            if selected
        ),
        None,
    )
    corpora: list[tuple[str, Path | None, Any]] = [
        ("faq", None, lambda: ingest_faq_jsonls(csv_dir, faq_jsonl_dir)),
        ("teacher_qa", None, lambda: ingest_teacher_qa_jsonls(teacher_qa_dir)),
        ("pdf", pdf_jsonl_dir / PDF_MANIFEST_NAME, lambda: ingest_pdf_jsonls(pdf_dir, pdf_jsonl_dir)),
        (
            "crawl",
            crawl_jsonl_dir / CRAWL_MANIFEST_NAME,
            lambda: ingest_crawl_jsonls(crawl_pages_dir, crawl_jsonl_dir),
        ),
    ]

    try:
        documents: list[dict[str, Any]] = []
        for name, manifest_path, ingest in corpora:
            if only is not None and only != name:
                continue
            if only is None and manifest_path is not None and not manifest_path.is_file():
                logger.info("%s corpus skipped (no export at %s)", name, manifest_path.parent)
                continue
            rows = ingest()
            logger.info("%s corpus: %d documents", name, len(rows))
            documents.extend(rows)
    except CorpusValidationError as exc:
        logger.error("Corpus validation failed: %s", exc)
        raise SystemExit(2) from exc

    if not documents:
        logger.error("No FAQ, teacher-QA, PDF-chunk or crawl-chunk JSONL documents to index")
        raise SystemExit(2)

    annotate_fiscal_year(documents)

    build_index(documents, recreate=args.recreate)


if __name__ == "__main__":
    main()
