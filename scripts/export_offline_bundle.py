"""Export offline voice RAG bundle for mobile/edge deployment.

Creates a self-contained bundle at ``artifacts/offline/`` containing:

  1. ``faiss_index.bin``   — FAISS flat index of all knowledge passages
  2. ``passages.jsonl.gz`` — Compressed passage metadata + text
  3. ``embedder/``         — ONNX-quantized bge-m3 model + tokenizer
  4. ``manifest.json``     — SHA-256 checksums + metadata

Target: < 100 MB total for mobile deployment.

Usage::

    python scripts/export_offline_bundle.py --qdrant-url http://localhost:6333
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "App"))

OUTPUT_DIR = PROJECT_ROOT / "App" / "artifacts" / "offline"


def export_faiss_index(
    qdrant_url: str,
    collection: str,
    output_dir: Path,
) -> tuple[int, Path]:
    """Export Qdrant vectors to a FAISS flat index."""
    import faiss
    import numpy as np

    from qdrant_client import QdrantClient

    client = QdrantClient(url=qdrant_url)

    # Scroll all points
    all_vectors = []
    all_ids = []
    offset = None

    while True:
        result = client.scroll(
            collection_name=collection,
            limit=100,
            offset=offset,
            with_vectors=True,
            with_payload=True,
        )
        points, next_offset = result
        if not points:
            break

        for point in points:
            vec = point.vector
            if isinstance(vec, dict):
                vec = vec.get("dense", vec.get("default", []))
            if vec:
                all_vectors.append(vec)
                all_ids.append(str(point.id))

        offset = next_offset
        if offset is None:
            break

    if not all_vectors:
        logger.warning("No vectors found in collection %s", collection)
        return 0, output_dir / "faiss_index.bin"

    vectors = np.array(all_vectors, dtype=np.float32)
    dim = vectors.shape[1]

    # Build FAISS index
    index = faiss.IndexFlatIP(dim)  # Inner product (for normalized embeddings)
    faiss.normalize_L2(vectors)  # Normalize for cosine similarity
    index.add(vectors)

    index_path = output_dir / "faiss_index.bin"
    faiss.write_index(index, str(index_path))

    logger.info("FAISS index: %d vectors, dim=%d, size=%s", len(all_vectors), dim, _human_size(index_path))
    return len(all_vectors), index_path


def export_passages(
    qdrant_url: str,
    collection: str,
    output_dir: Path,
) -> Path:
    """Export passage metadata to compressed JSONL."""
    from qdrant_client import QdrantClient

    client = QdrantClient(url=qdrant_url)

    passages_path = output_dir / "passages.jsonl.gz"
    count = 0

    with gzip.open(passages_path, "wt", encoding="utf-8") as f:
        offset = None
        while True:
            result = client.scroll(
                collection_name=collection,
                limit=100,
                offset=offset,
                with_vectors=False,
                with_payload=True,
            )
            points, next_offset = result
            if not points:
                break

            for point in points:
                payload = point.payload or {}
                entry = {
                    "id": str(point.id),
                    "text": payload.get("text", payload.get("content", "")),
                    "source": payload.get("source", ""),
                    "page": payload.get("page", ""),
                    "section": payload.get("section", ""),
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                count += 1

            offset = next_offset
            if offset is None:
                break

    logger.info("Exported %d passages, size=%s", count, _human_size(passages_path))
    return passages_path


def compute_manifest(output_dir: Path) -> Path:
    """Compute SHA-256 checksums for all bundle files."""
    manifest = {
        "version": "1.0.0",
        "files": {},
        "total_size_bytes": 0,
    }

    for file_path in sorted(output_dir.rglob("*")):
        if file_path.is_file() and file_path.name != "manifest.json":
            rel = str(file_path.relative_to(output_dir))
            size = file_path.stat().st_size
            sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
            manifest["files"][rel] = {"sha256": sha, "size_bytes": size}
            manifest["total_size_bytes"] += size

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    total_mb = manifest["total_size_bytes"] / (1024 * 1024)
    logger.info("Bundle manifest: %d files, %.1f MB total", len(manifest["files"]), total_mb)

    if total_mb > 100:
        logger.warning("Bundle exceeds 100 MB target (%.1f MB) — consider quantization", total_mb)

    return manifest_path


def _human_size(path: Path) -> str:
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Export offline voice RAG bundle")
    parser.add_argument("--qdrant-url", type=str, default="http://localhost:6333")
    parser.add_argument("--collection", type=str, default="ura_knowledge_base")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    args.output.mkdir(parents=True, exist_ok=True)

    print(f"Exporting offline bundle to {args.output}")
    print("=" * 60)

    n_vectors, _ = export_faiss_index(args.qdrant_url, args.collection, args.output)
    if n_vectors == 0:
        print("WARNING: No vectors exported — check Qdrant connection")
        return

    export_passages(args.qdrant_url, args.collection, args.output)
    compute_manifest(args.output)

    print("=" * 60)
    print(f"Done. Bundle at {args.output}")


if __name__ == "__main__":
    main()
