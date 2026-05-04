"""Offline bundle builder and manager (2026 — Phase 25).

Builds, versions, and serves self-contained offline RAG bundles for
mobile/edge deployment.  Each bundle is:

  - Semantically versioned (MAJOR.MINOR.PATCH)
  - Integrity-verified (SHA-256 per artifact)
  - Compressed (< 150 MB target)
  - Backward-compatible (min_app_version field)

Architecture::

    Qdrant vectors  ──┐
    Passages JSONL  ──┤──▶  BundleBuilder  ──▶  artifacts/offline/
    ONNX embedder   ──┘         │                    ├── faiss_index.bin
                                │                    ├── passages.jsonl.gz
                                │                    ├── embedder/
                                │                    ├── chunk_hashes.json
                                │                    ├── manifest.json
                                │                    └── bundle.tar.gz
                                │
                                ▼
                         BundleManager  ──▶  /v1/offline/bundle (download)

Feature-flagged behind ``offline_bundle_api``.
"""

from __future__ import annotations

import datetime
import gzip
import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ._root import PROJECT_ROOT as _PROJECT_ROOT
OFFLINE_BUNDLE_DIR = Path(
    os.getenv(
        "OFFLINE_BUNDLE_DIR",
        str(_PROJECT_ROOT / "artifacts" / "offline"),
    )
)

# Bundle constraints
MAX_BUNDLE_SIZE_BYTES = int(os.getenv("MAX_OFFLINE_BUNDLE_BYTES", "157286400"))  # 150 MB
MAX_PASSAGE_COUNT = int(os.getenv("MAX_OFFLINE_PASSAGES", "5000"))
TARGET_BUNDLE_VERSION = os.getenv("OFFLINE_BUNDLE_VERSION", "")


@dataclass
class BuildResult:
    """Result of a bundle build operation."""

    success: bool
    version: str
    output_dir: str
    bundle_path: str = ""
    size_bytes: int = 0
    size_mb: float = 0.0
    passage_count: int = 0
    index_dim: int = 0
    duration_s: float = 0.0
    sha256: str = ""
    error: str = ""


@dataclass
class BundleInfo:
    """Metadata about a built bundle for serving."""

    version: str = "0.0.0"
    size_bytes: int = 0
    size_mb: float = 0.0
    passage_count: int = 0
    index_dim: int = 0
    sha256: str = ""
    created_at: str = ""
    min_app_version: str = ""
    bundle_path: str = ""
    available: bool = False


class BundleBuilder:
    """Builds versioned offline RAG bundles from Qdrant vectors + passages."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or OFFLINE_BUNDLE_DIR

    def build(
        self,
        qdrant_url: str = "http://localhost:6333",
        collection: str = "ura_knowledge",
        version: str | None = None,
        min_app_version: str = "1.0.0",
    ) -> BuildResult:
        """Build a complete offline bundle.

        Args:
            qdrant_url: Qdrant server URL.
            collection: Qdrant collection name.
            version: Bundle version (auto-incremented if None).
            min_app_version: Minimum app version required.

        Returns:
            BuildResult with build status and metadata.
        """
        t0 = time.time()
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if version is None:
            version = self._next_version()

        logger.info("Building offline bundle v%s...", version)

        try:
            # Step 1: Export FAISS index
            num_vectors, index_path, index_dim = self._export_faiss_index(
                qdrant_url, collection
            )
            if num_vectors == 0:
                return BuildResult(
                    success=False,
                    version=version,
                    output_dir=str(self._output_dir),
                    error="No vectors found in Qdrant collection",
                )

            # Step 2: Export passages
            passage_count = self._export_passages(qdrant_url, collection)

            # Step 3: Compute chunk hashes for delta sync
            self._compute_chunk_hashes()

            # Step 4: Compute SHA-256 checksums
            checksums = self._compute_checksums()

            # Step 5: Write manifest
            self._write_manifest(
                version=version,
                passage_count=passage_count,
                index_dim=index_dim,
                checksums=checksums,
                min_app_version=min_app_version,
            )

            # Step 6: Create compressed bundle archive
            bundle_path = self._create_archive(version)
            bundle_size = bundle_path.stat().st_size if bundle_path.exists() else 0
            bundle_sha = _sha256_file(bundle_path) if bundle_path.exists() else ""

            duration = time.time() - t0

            # Step 7: Validate size constraint
            if bundle_size > MAX_BUNDLE_SIZE_BYTES:
                logger.warning(
                    "Bundle exceeds size limit: %s > %s",
                    _human_size(bundle_size),
                    _human_size(MAX_BUNDLE_SIZE_BYTES),
                )

            logger.info(
                "Bundle v%s built: %d passages, %s, %.1fs",
                version,
                passage_count,
                _human_size(bundle_size),
                duration,
            )

            return BuildResult(
                success=True,
                version=version,
                output_dir=str(self._output_dir),
                bundle_path=str(bundle_path),
                size_bytes=bundle_size,
                size_mb=round(bundle_size / 1_048_576, 1),
                passage_count=passage_count,
                index_dim=index_dim,
                duration_s=round(duration, 1),
                sha256=bundle_sha,
            )

        except Exception as e:
            duration = time.time() - t0
            logger.exception("Bundle build failed")
            return BuildResult(
                success=False,
                version=version,
                output_dir=str(self._output_dir),
                duration_s=round(duration, 1),
                error=str(e)[:500],
            )

    def _export_faiss_index(
        self,
        qdrant_url: str,
        collection: str,
    ) -> tuple[int, Path, int]:
        """Export Qdrant vectors to FAISS flat index."""
        import faiss
        import numpy as np
        from qdrant_client import QdrantClient

        client = QdrantClient(url=qdrant_url)

        all_vectors: list[list[float]] = []
        offset = None

        while True:
            result = client.scroll(
                collection_name=collection,
                limit=100,
                offset=offset,
                with_vectors=True,
                with_payload=False,
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

            offset = next_offset
            if offset is None:
                break

        if not all_vectors:
            return 0, self._output_dir / "faiss_index.bin", 0

        vectors = np.array(all_vectors, dtype=np.float32)
        dim = vectors.shape[1]

        # Build FAISS index (normalized for cosine similarity)
        index = faiss.IndexFlatIP(dim)
        faiss.normalize_L2(vectors)
        index.add(vectors)

        index_path = self._output_dir / "faiss_index.bin"
        faiss.write_index(index, str(index_path))

        logger.info(
            "FAISS index: %d vectors, dim=%d, size=%s",
            len(all_vectors),
            dim,
            _human_size(index_path.stat().st_size),
        )
        return len(all_vectors), index_path, dim

    def _export_passages(self, qdrant_url: str, collection: str) -> int:
        """Export passage payloads to compressed JSONL."""
        from qdrant_client import QdrantClient

        client = QdrantClient(url=qdrant_url)
        passages_path = self._output_dir / "passages.jsonl.gz"
        count = 0
        offset = None

        with gzip.open(passages_path, "wt", encoding="utf-8") as f:
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
                    passage = {
                        "id": str(point.id),
                        "text": payload.get("text", ""),
                        "source": payload.get("source", ""),
                        "page": payload.get("page", ""),
                        "section": payload.get("section", ""),
                    }
                    f.write(json.dumps(passage, ensure_ascii=False) + "\n")
                    count += 1

                    if count >= MAX_PASSAGE_COUNT:
                        break

                offset = next_offset
                if offset is None or count >= MAX_PASSAGE_COUNT:
                    break

        logger.info(
            "Passages exported: %d, size=%s",
            count,
            _human_size(passages_path.stat().st_size),
        )
        return count

    def _compute_chunk_hashes(self) -> dict[str, str]:
        """Compute per-chunk SHA-256 hashes for delta sync."""
        passages_path = self._output_dir / "passages.jsonl.gz"
        if not passages_path.exists():
            return {}

        hashes: dict[str, str] = {}
        with gzip.open(passages_path, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if line:
                    chunk_id = f"chunk_{i:06d}"
                    hashes[chunk_id] = hashlib.sha256(line.encode("utf-8")).hexdigest()

        hash_path = self._output_dir / "chunk_hashes.json"
        with open(hash_path, "w", encoding="utf-8") as f:
            json.dump(hashes, f)

        return hashes

    def _compute_checksums(self) -> dict[str, str]:
        """Compute SHA-256 checksums for all bundle artifacts."""
        checksums: dict[str, str] = {}
        for name in ("faiss_index.bin", "passages.jsonl.gz"):
            path = self._output_dir / name
            if path.exists():
                checksums[name.replace(".", "_").replace("-", "_")] = _sha256_file(path)

        embedder_dir = self._output_dir / "embedder"
        if embedder_dir.exists():
            model_onnx = embedder_dir / "model.onnx"
            if model_onnx.exists():
                checksums["embedder"] = _sha256_file(model_onnx)

        return checksums

    def _write_manifest(
        self,
        version: str,
        passage_count: int,
        index_dim: int,
        checksums: dict[str, str],
        min_app_version: str,
    ) -> None:
        """Write bundle manifest.json."""
        # Compute total size
        total_size = 0
        for path in self._output_dir.rglob("*"):
            if path.is_file() and path.name != "manifest.json":
                total_size += path.stat().st_size

        manifest = {
            "version": version,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "passage_count": passage_count,
            "index_dim": index_dim,
            "size_bytes": total_size,
            "sha256": checksums,
            "min_app_version": min_app_version,
            "chunk_count": passage_count,
            "max_bundle_size_bytes": MAX_BUNDLE_SIZE_BYTES,
            "metadata": {
                "builder": "offline_bundle.py",
                "pipeline_version": "2026.1.0",
            },
        }

        manifest_path = self._output_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    def _create_archive(self, version: str) -> Path:
        """Create a compressed tar.gz archive of the bundle."""
        archive_path = self._output_dir / f"bundle-v{version}.tar.gz"

        with tarfile.open(archive_path, "w:gz", compresslevel=6) as tar:
            for item in self._output_dir.iterdir():
                if item.name.startswith("bundle-") and item.suffix == ".gz":
                    continue  # Don't include other archives
                tar.add(item, arcname=item.name)

        logger.info("Bundle archive: %s", _human_size(archive_path.stat().st_size))
        return archive_path

    def _next_version(self) -> str:
        """Auto-increment bundle version from manifest."""
        manifest_path = self._output_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            parts = manifest.get("version", "0.0.0").split(".")
            if len(parts) == 3:
                return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
        return "1.0.0"


class BundleManager:
    """Serves offline bundles to clients and tracks download statistics."""

    def __init__(self, bundle_dir: Path | None = None) -> None:
        self._bundle_dir = bundle_dir or OFFLINE_BUNDLE_DIR
        self._download_count = 0
        self._total_bytes_served = 0

    def get_info(self) -> BundleInfo:
        """Get information about the current bundle."""
        manifest_path = self._bundle_dir / "manifest.json"
        if not manifest_path.exists():
            return BundleInfo()

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        # Find the latest archive
        archives = sorted(self._bundle_dir.glob("bundle-v*.tar.gz"), reverse=True)
        bundle_path = str(archives[0]) if archives else ""
        bundle_size = archives[0].stat().st_size if archives else 0

        return BundleInfo(
            version=manifest.get("version", "0.0.0"),
            size_bytes=manifest.get("size_bytes", 0),
            size_mb=round(manifest.get("size_bytes", 0) / 1_048_576, 1),
            passage_count=manifest.get("passage_count", 0),
            index_dim=manifest.get("index_dim", 0),
            sha256=manifest.get("sha256", {}).get("faiss_index_bin", ""),
            created_at=manifest.get("created_at", ""),
            min_app_version=manifest.get("min_app_version", ""),
            bundle_path=bundle_path,
            available=bool(bundle_path),
        )

    def get_bundle_path(self) -> Path | None:
        """Get the path to the latest bundle archive for download."""
        archives = sorted(self._bundle_dir.glob("bundle-v*.tar.gz"), reverse=True)
        if archives:
            return archives[0]
        return None

    def record_download(self, size_bytes: int) -> None:
        """Record a bundle download for statistics."""
        self._download_count += 1
        self._total_bytes_served += size_bytes

    def get_stats(self) -> dict[str, Any]:
        """Return download statistics."""
        info = self.get_info()
        return {
            "total_bundles_served": self._download_count,
            "total_bytes_served": self._total_bytes_served,
            "bundle_version": info.version,
            "bundle_size_mb": info.size_mb,
            "passage_count": info.passage_count,
            "bundle_available": info.available,
            "last_built_at": info.created_at,
        }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _sha256_file(path: Path, chunk_size: int = 8192) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _human_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"
