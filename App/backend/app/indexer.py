"""Document indexing pipeline for the Qdrant vector store.

Ingests PDFs (via pymupdf4llm) and FAQ CSVs, chunks them, computes
dense + BM25 sparse embeddings, and upserts into a Qdrant collection.
Designed to run as a CLI or be triggered via the ``POST /v1/index`` API.

Usage:
    python -m App.backend.app.indexer                # full reindex
    python -m App.backend.app.indexer --csvs-only    # FAQ CSVs only
    python -m App.backend.app.indexer --pdfs-only    # PDFs only
    python -m App.backend.app.indexer --recreate     # drop + rebuild collection
"""

from __future__ import annotations

import csv
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "ura_knowledge_base")
# 2026 default: BAAI/bge-m3 multilingual embeddings (1024-dim).
# Override DENSE_MODEL / DENSE_DIM when re-indexing legacy collections.
DENSE_MODEL_NAME = os.getenv("DENSE_MODEL", "BAAI/bge-m3")
DENSE_DIM = int(os.getenv("DENSE_DIM", "1024"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))
BATCH_SIZE = int(os.getenv("INDEX_BATCH_SIZE", "64"))

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.getenv("DATA_DIR", str(_PROJECT_ROOT / "Data" / "dataset")))
PDF_DIR = Path(os.getenv("PDF_DIR", str(_PROJECT_ROOT / "Data" / "pdfs")))
BM25_STATE_PATH = Path(
    os.getenv("BM25_STATE_PATH", str(_PROJECT_ROOT / "Model" / "bm25_state.json"))
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
        start = end - overlap

    return [c for c in chunks if len(c) > 50]


# ---------------------------------------------------------------------------
# Ingestors
# ---------------------------------------------------------------------------
def ingest_pdfs(pdf_dir: Path) -> list[dict[str, Any]]:
    """Extract and chunk text from PDF files using pymupdf4llm."""
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
                page_text = page_data.get("text", "") if isinstance(page_data, dict) else str(page_data)
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


def ingest_csvs(csv_dir: Path) -> list[dict[str, Any]]:
    """Load FAQ CSVs as individual Q&A documents."""
    documents: list[dict[str, Any]] = []
    if not csv_dir.is_dir():
        logger.warning("CSV directory not found: %s", csv_dir)
        return documents

    for csv_path in sorted(csv_dir.glob("ura_*_faqs.csv")):
        count = 0
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row_idx, row in enumerate(reader):
                    q = (row.get("question") or row.get("Question") or "").strip()
                    a = (row.get("answer") or row.get("Answer") or "").strip()
                    if q and a:
                        tag = csv_path.stem.replace("ura_", "").replace("_faqs", "")
                        documents.append(
                            {
                                "text": f"Question: {q}\nAnswer: {a}",
                                "source": csv_path.name,
                                "chunk_id": f"{csv_path.stem}_row_{row_idx}",
                                "page": "",
                                "section": tag.replace("_", " ").title(),
                                "doc_type": "csv",
                                "question": q,
                                "answer": a,
                                "tag": tag,
                            }
                        )
                        count += 1
            logger.info("Ingested %s: %d entries", csv_path.name, count)
        except Exception:
            logger.exception("Failed to ingest %s", csv_path.name)

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

    from .retriever import BM25SparseEncoder

    client = QdrantClient(url=QDRANT_URL, timeout=30)
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
                vectors["sparse"] = models.SparseVector(
                    indices=sparse_idx, values=sparse_val
                )

            payload = {k: v for k, v in doc.items()}
            points.append(
                models.PointStruct(
                    id=str(uuid.uuid4()),
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

    stats = {
        "collection": QDRANT_COLLECTION,
        "total_documents": len(documents),
        "total_upserted": total_upserted,
        "pdf_documents": sum(1 for d in documents if d["doc_type"] == "pdf"),
        "csv_documents": sum(1 for d in documents if d["doc_type"] == "csv"),
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

    parser = argparse.ArgumentParser(description="Index documents into Qdrant")
    parser.add_argument("--pdfs-only", action="store_true")
    parser.add_argument("--csvs-only", action="store_true")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    parser.add_argument("--csv-dir", type=str, default=str(DATA_DIR))
    parser.add_argument("--pdf-dir", type=str, default=str(PDF_DIR))
    args = parser.parse_args()

    documents: list[dict[str, Any]] = []

    if not args.pdfs_only:
        documents.extend(ingest_csvs(Path(args.csv_dir)))
    if not args.csvs_only:
        documents.extend(ingest_pdfs(Path(args.pdf_dir)))

    if not documents:
        logger.error("No documents to index")
        return

    stats = build_index(documents, recreate=args.recreate)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
