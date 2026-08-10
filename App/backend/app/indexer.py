"""JSONL-first indexing pipeline for the local Qdrant vector store.

Exports every curated FAQ CSV to canonical JSONL, normalises teacher-QA JSONL
generated from PDFs, computes dense + BM25 sparse embeddings, and upserts the
resulting vector documents. PDFs and evaluation/red-team JSONL are excluded.

Usage:
    python -m app.indexer --export-faq-jsonl         # refresh canonical FAQ JSONL
    python -m app.indexer --recreate                 # FAQ JSONL + teacher-QA JSONL
    python -m app.indexer --faq-jsonl-only            # FAQ JSONL only
    python -m app.indexer --teacher-qa-only           # teacher-QA JSONL only
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
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))
BATCH_SIZE = int(os.getenv("INDEX_BATCH_SIZE", "64"))

from ._root import APP_DATA_ROOT as _APP_DATA_ROOT
from .faq_corpus import (
    CorpusValidationError,
    export_faq_csvs_to_jsonl,
    ingest_faq_jsonls,
    ingest_teacher_qa_jsonls,
)

DATA_DIR = Path(os.getenv("DATA_DIR", str(_APP_DATA_ROOT / "dataset")))
FAQ_JSONL_DIR = Path(os.getenv("FAQ_JSONL_DIR", str(_APP_DATA_ROOT / "faq_jsonl")))
TEACHER_QA_DIR = Path(os.getenv("TEACHER_QA_DIR", str(_APP_DATA_ROOT / "teacher_qa")))
BM25_STATE_PATH = Path(
    os.getenv("BM25_STATE_PATH", str(_APP_DATA_ROOT.parent / "Model" / "bm25_state.json"))
)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def _chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split *text* into overlapping chunks, breaking at sentence boundaries."""
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in [". ", ".\n", "\n\n", "\n", " "]:
                idx = text.rfind(sep, start + chunk_size // 2, end)
                if idx > start:
                    end = idx + len(sep)
                    break
        chunks.append(text[start:end].strip())
        next_start = end - overlap
        if 0 < next_start < len(text) and not text[next_start - 1].isspace():
            # `end` was snapped to a sentence/word boundary above, but
            # subtracting a fixed overlap from it is not: the next chunk can
            # start inside the word straddling that offset (observed live —
            # "...for the benefit of the society. incomes and wealth..."
            # started its next chunk at "omes and wealth..."). Snap forward
            # to the next whitespace so every chunk begins on a word boundary.
            ws = text.find(" ", next_start, end)
            if ws != -1:
                next_start = ws + 1
        start = next_start

    return [c for c in chunks if len(c) > 50]


# ---------------------------------------------------------------------------
# Legacy PDF source helper
# ---------------------------------------------------------------------------
def ingest_pdfs(pdf_dir: Path) -> list[dict[str, Any]]:
    """Extract PDF chunks for offline teacher-QA generation only.

    This helper is intentionally not called by the Qdrant indexing CLI or
    ``POST /v1/index``.  Teacher-QA JSONL is the only PDF-derived vector input.
    """
    documents: list[dict[str, Any]] = []
    if not pdf_dir.is_dir():
        logger.warning("PDF directory not found: %s", pdf_dir)
        return documents

    try:
        import pymupdf4llm
    except ImportError:
        logger.error("pymupdf4llm not installed; skipping PDF ingestion")
        return documents

    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        try:
            pages = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
            chunk_idx = 0
            for page_data in pages:
                page_text = (
                    page_data.get("text", "") if isinstance(page_data, dict) else str(page_data)
                )
                page_meta = page_data.get("metadata", {}) if isinstance(page_data, dict) else {}
                page_num = str(page_meta.get("page", ""))

                # Extract section heading from first markdown heading in chunk
                section = ""
                for line in page_text.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        section = stripped.lstrip("#").strip()
                        break

                page_chunks = _chunk_text(page_text)
                for chunk in page_chunks:
                    documents.append(
                        {
                            "text": chunk,
                            "source": pdf_path.name,
                            "chunk_id": f"{pdf_path.stem}_chunk_{chunk_idx}",
                            "page": page_num,
                            "section": section,
                            "doc_type": "pdf",
                            "question": "",
                            "answer": "",
                        }
                    )
                    chunk_idx += 1
            logger.info("Ingested %s: %d chunks", pdf_path.name, chunk_idx)
        except Exception:
            logger.exception("Failed to ingest %s", pdf_path.name)

    return documents


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------
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
    texts = [d["text"] for d in documents]
    sparse_encoder = BM25SparseEncoder().fit(texts)

    BM25_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BM25_STATE_PATH, "w") as f:
        json.dump(sparse_encoder.to_dict(), f)
    logger.info("Saved BM25 state to %s", BM25_STATE_PATH)

    # -- Batch embed + upsert ------------------------------------------------
    total_upserted = 0
    for i in range(0, len(documents), BATCH_SIZE):
        batch = documents[i : i + BATCH_SIZE]
        batch_texts = [d["text"] for d in batch]

        dense_embeddings = dense_model.encode(batch_texts, show_progress_bar=False)

        points: list[models.PointStruct] = []
        for j, doc in enumerate(batch):
            sparse_idx, sparse_val = sparse_encoder.encode(doc["text"])
            vectors: dict[str, Any] = {"dense": dense_embeddings[j].tolist()}
            if sparse_idx:
                vectors["sparse"] = models.SparseVector(indices=sparse_idx, values=sparse_val)

            payload = dict(doc.items())
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
                payload={"_meta": "bm25_binding", "corpus_hash": sparse_encoder.corpus_hash},
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

    parser = argparse.ArgumentParser(description="Index validated FAQ and teacher-QA JSONL into Qdrant")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--faq-jsonl-only", action="store_true")
    source_group.add_argument("--teacher-qa-only", action="store_true")
    parser.add_argument("--export-faq-jsonl", action="store_true")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    parser.add_argument("--csv-dir", type=str, default=str(DATA_DIR))
    parser.add_argument("--faq-jsonl-dir", type=str, default=str(FAQ_JSONL_DIR))
    parser.add_argument("--teacher-qa-dir", type=str, default=str(TEACHER_QA_DIR))
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    faq_jsonl_dir = Path(args.faq_jsonl_dir)
    teacher_qa_dir = Path(args.teacher_qa_dir)
    if args.export_faq_jsonl:
        stats = export_faq_csvs_to_jsonl(csv_dir, faq_jsonl_dir)
        logger.info("FAQ JSONL export complete: %s", stats)
        return

    try:
        documents: list[dict[str, Any]] = []
        if not args.teacher_qa_only:
            documents.extend(ingest_faq_jsonls(csv_dir, faq_jsonl_dir))
        if not args.faq_jsonl_only:
            documents.extend(ingest_teacher_qa_jsonls(teacher_qa_dir))
    except CorpusValidationError as exc:
        logger.error("Corpus validation failed: %s", exc)
        raise SystemExit(2) from exc

    if not documents:
        logger.error("No FAQ or teacher-QA JSONL documents to index")
        raise SystemExit(2)

    build_index(documents, recreate=args.recreate)


if __name__ == "__main__":
    main()
