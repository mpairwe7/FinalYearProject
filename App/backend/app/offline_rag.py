"""Production Offline RAG pipeline (2026 — Phase 25).

On-device retrieval when the main Qdrant instance or network is unavailable.
Uses a pre-exported FAISS index + ONNX-quantized bge-m3 embedding model for
sub-100ms retrieval on low-end hardware (4 GB RAM Android).

Architecture::

    query  ──▶  ONNX bge-m3 embed  ──▶  FAISS search  ──▶  passages
                                                               │
                                              (optional) GGUF LLM ──▶ answer

The offline bundle is exported via ``scripts/export_offline_bundle.py``
and contains:

* ``faiss_index.bin``       — FAISS flat or IVF index (~30-80 MB)
* ``passages.jsonl.gz``     — compressed passage metadata + text
* ``embedder/``             — ONNX-quantized bge-m3 model + tokenizer
* ``manifest.json``         — SHA-256 checksums, version, metadata
* ``chunk_hashes.json``     — per-chunk hashes for delta sync

Feature-flagged behind ``offline_rag`` and gated by the existence of the
offline bundle on disk.  Delta sync is additionally gated by ``offline_sync``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from ._root import PROJECT_ROOT as _PROJECT_ROOT
OFFLINE_BUNDLE_DIR = Path(
    os.getenv(
        "OFFLINE_BUNDLE_DIR",
        str(_PROJECT_ROOT / "artifacts" / "offline"),
    )
)
OFFLINE_MAX_PASSAGES = int(os.getenv("OFFLINE_MAX_PASSAGES", "5000"))
OFFLINE_TOP_K_DEFAULT = int(os.getenv("OFFLINE_TOP_K", "4"))
OFFLINE_SCORE_THRESHOLD = float(os.getenv("OFFLINE_SCORE_THRESHOLD", "0.25"))


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OfflineRetrievalResult:
    """Result from the offline retrieval pipeline."""

    passages: list[dict[str, Any]]
    latency_ms: float
    index_size: int
    bundle_version: str = ""
    error: str | None = None


@dataclass
class BundleManifest:
    """Metadata from the offline bundle manifest.json."""

    version: str = "0.0.0"
    created_at: str = ""
    passage_count: int = 0
    index_dim: int = 0
    size_bytes: int = 0
    sha256_index: str = ""
    sha256_passages: str = ""
    sha256_embedder: str = ""
    min_app_version: str = ""
    chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class OfflineRAGPipeline:
    """Production on-device retrieval using FAISS + ONNX embedding model.

    Initialised lazily — does nothing if the offline bundle doesn't
    exist on disk.  Supports versioned bundles and integrity verification.
    """

    def __init__(self, bundle_dir: Path | None = None) -> None:
        self._bundle_dir = bundle_dir or OFFLINE_BUNDLE_DIR
        self._index = None  # FAISS index
        self._passages: list[dict] = []
        self._embedder = None  # ONNX InferenceSession
        self._tokenizer = None
        self._ready = False
        self._manifest: BundleManifest = BundleManifest()
        self._chunk_hashes: dict[str, str] = {}

    # --- Properties ---

    @property
    def is_available(self) -> bool:
        """True if the offline bundle exists and can be loaded."""
        return self._bundle_dir.exists() and (self._bundle_dir / "faiss_index.bin").exists()

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def manifest(self) -> BundleManifest:
        return self._manifest

    @property
    def bundle_version(self) -> str:
        return self._manifest.version

    @property
    def passage_count(self) -> int:
        return len(self._passages)

    @property
    def chunk_hashes(self) -> dict[str, str]:
        return self._chunk_hashes.copy()

    # --- Lifecycle ---

    def initialize(self) -> bool:
        """Load the offline bundle from disk.

        Returns True on success, False on failure (logs the error).
        """
        if self._ready:
            return True

        if not self.is_available:
            logger.info(
                "Offline bundle not found at %s — offline RAG disabled",
                self._bundle_dir,
            )
            return False

        try:
            self._load_manifest()
            if not self._verify_integrity():
                logger.error("Offline bundle integrity check failed — refusing to load")
                return False
            self._load_index()
            self._load_passages()
            self._load_embedder()
            self._load_chunk_hashes()
            self._ready = True
            logger.info(
                "Offline RAG ready: version=%s, %d passages, index dim=%s",
                self._manifest.version,
                len(self._passages),
                self._index.d if self._index else "?",
            )
            return True
        except Exception:
            logger.exception("Failed to initialize offline RAG pipeline")
            return False

    def reload(self) -> bool:
        """Reload the bundle from disk (e.g. after a delta sync update)."""
        self.close()
        return self.initialize()

    def close(self) -> None:
        """Release resources."""
        self._index = None
        self._passages.clear()
        self._embedder = None
        self._tokenizer = None
        self._chunk_hashes.clear()
        self._ready = False

    # --- Retrieval ---

    def retrieve(
        self,
        query: str,
        top_k: int = OFFLINE_TOP_K_DEFAULT,
        score_threshold: float = OFFLINE_SCORE_THRESHOLD,
    ) -> OfflineRetrievalResult:
        """Search the offline FAISS index.

        Args:
            query: User query text.
            top_k: Number of passages to return.
            score_threshold: Minimum similarity score to include a passage.

        Returns:
            OfflineRetrievalResult with matching passages.
        """
        if not self._ready:
            return OfflineRetrievalResult(
                passages=[],
                latency_ms=0,
                index_size=0,
                bundle_version=self._manifest.version,
                error="Offline RAG not initialized",
            )

        t0 = time.perf_counter()

        try:
            import numpy as np

            # Embed query
            embedding = self._embed(query)
            if embedding is None:
                return OfflineRetrievalResult(
                    passages=[],
                    latency_ms=0,
                    index_size=len(self._passages),
                    bundle_version=self._manifest.version,
                    error="Embedding failed — ONNX model unavailable",
                )

            # Search FAISS
            query_vec = np.array([embedding], dtype=np.float32)
            k = min(top_k, len(self._passages))
            distances, indices = self._index.search(query_vec, k)

            results = []
            for i, idx in enumerate(indices[0]):
                if idx < 0 or idx >= len(self._passages):
                    continue
                # Convert distance to similarity score
                score = float(1.0 / (1.0 + distances[0][i]))
                if score < score_threshold:
                    continue
                passage = self._passages[idx].copy()
                passage["score"] = round(score, 4)
                passage["retrieval_mode"] = "offline"
                results.append(passage)

            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            return OfflineRetrievalResult(
                passages=results,
                latency_ms=latency_ms,
                index_size=len(self._passages),
                bundle_version=self._manifest.version,
            )

        except Exception as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.error("Offline retrieval failed: %s", exc)
            return OfflineRetrievalResult(
                passages=[],
                latency_ms=latency_ms,
                index_size=len(self._passages),
                bundle_version=self._manifest.version,
                error=str(exc),
            )

    # --- Health ---

    def health(self) -> dict[str, Any]:
        """Return health status for monitoring."""
        return {
            "available": self.is_available,
            "ready": self._ready,
            "bundle_version": self._manifest.version,
            "passage_count": len(self._passages),
            "index_dim": self._index.d if self._index else 0,
            "bundle_dir": str(self._bundle_dir),
            "has_embedder": self._embedder is not None,
        }

    # ------------------------------------------------------------------
    # Private loading methods
    # ------------------------------------------------------------------

    def _load_manifest(self) -> None:
        """Load bundle manifest for version and integrity info."""
        manifest_path = self._bundle_dir / "manifest.json"
        if not manifest_path.exists():
            logger.warning("No manifest.json in offline bundle — using defaults")
            return

        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)

        self._manifest = BundleManifest(
            version=data.get("version", "0.0.0"),
            created_at=data.get("created_at", ""),
            passage_count=data.get("passage_count", 0),
            index_dim=data.get("index_dim", 0),
            size_bytes=data.get("size_bytes", 0),
            sha256_index=data.get("sha256", {}).get("faiss_index", ""),
            sha256_passages=data.get("sha256", {}).get("passages", ""),
            sha256_embedder=data.get("sha256", {}).get("embedder", ""),
            min_app_version=data.get("min_app_version", ""),
            chunk_count=data.get("chunk_count", 0),
            metadata=data.get("metadata", {}),
        )
        logger.info(
            "Bundle manifest: version=%s, passages=%d, created=%s",
            self._manifest.version,
            self._manifest.passage_count,
            self._manifest.created_at,
        )

    def _verify_integrity(self) -> bool:
        """Verify SHA-256 checksums of bundle artifacts."""
        if not self._manifest.sha256_index:
            logger.debug("No SHA-256 in manifest — skipping integrity check")
            return True

        index_path = self._bundle_dir / "faiss_index.bin"
        if index_path.exists() and self._manifest.sha256_index:
            actual = _sha256_file(index_path)
            if actual != self._manifest.sha256_index:
                logger.error(
                    "FAISS index integrity check FAILED: expected=%s actual=%s",
                    self._manifest.sha256_index[:16],
                    actual[:16],
                )
                return False

        logger.debug("Bundle integrity check passed")
        return True

    def _load_index(self) -> None:
        """Load the FAISS index from disk."""
        import faiss

        index_path = str(self._bundle_dir / "faiss_index.bin")
        self._index = faiss.read_index(index_path)
        logger.info(
            "FAISS index loaded: %d vectors, dim=%d",
            self._index.ntotal,
            self._index.d,
        )

    def _load_passages(self) -> None:
        """Load passage metadata from compressed JSONL."""
        passages_path = self._bundle_dir / "passages.jsonl.gz"
        if not passages_path.exists():
            passages_path = self._bundle_dir / "passages.jsonl"

        self._passages = []
        opener = gzip.open if str(passages_path).endswith(".gz") else open
        with opener(passages_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self._passages.append(json.loads(line))
                if len(self._passages) >= OFFLINE_MAX_PASSAGES:
                    break

        logger.info("Loaded %d passages for offline retrieval", len(self._passages))

    def _load_embedder(self) -> None:
        """Load the ONNX embedding model."""
        embedder_dir = self._bundle_dir / "embedder"
        model_path = embedder_dir / "model.onnx"

        if not model_path.exists():
            logger.warning(
                "ONNX embedder not found at %s — offline RAG will have reduced quality",
                model_path,
            )
            return

        try:
            import onnxruntime as ort

            sess_opts = ort.SessionOptions()
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_opts.intra_op_num_threads = 2  # Low-end device friendly
            sess_opts.inter_op_num_threads = 1

            self._embedder = ort.InferenceSession(
                str(model_path),
                sess_options=sess_opts,
                providers=["CPUExecutionProvider"],
            )

            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(str(embedder_dir))
            logger.info("ONNX embedder loaded from %s", embedder_dir)
        except Exception:
            logger.warning(
                "ONNX embedder loading failed — offline RAG may have reduced quality",
                exc_info=True,
            )

    def _load_chunk_hashes(self) -> None:
        """Load per-chunk hashes for delta sync support."""
        hash_path = self._bundle_dir / "chunk_hashes.json"
        if hash_path.exists():
            with open(hash_path, encoding="utf-8") as f:
                self._chunk_hashes = json.load(f)
            logger.info("Loaded %d chunk hashes for delta sync", len(self._chunk_hashes))

    def _embed(self, text: str) -> list[float] | None:
        """Embed a query using the ONNX model or fallback."""
        if self._embedder is not None and self._tokenizer is not None:
            try:
                import numpy as np

                inputs = self._tokenizer(
                    text,
                    return_tensors="np",
                    padding=True,
                    truncation=True,
                    max_length=512,
                )
                outputs = self._embedder.run(
                    None,
                    {
                        "input_ids": inputs["input_ids"].astype(np.int64),
                        "attention_mask": inputs["attention_mask"].astype(np.int64),
                    },
                )
                # Mean pooling over last hidden state
                embeddings = outputs[0]
                mask = inputs["attention_mask"][..., None].astype(np.float32)
                pooled = (embeddings * mask).sum(axis=1) / mask.sum(axis=1)
                # L2 normalize
                norm = np.linalg.norm(pooled, axis=1, keepdims=True)
                pooled = pooled / np.maximum(norm, 1e-8)
                return pooled[0].tolist()
            except Exception:
                logger.debug("ONNX embedding failed, no fallback available", exc_info=True)

        return None


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
