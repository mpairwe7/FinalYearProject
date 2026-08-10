"""Delta sync engine for offline RAG bundles (2026 — Phase 25).

Implements hash-based delta synchronization so mobile/edge clients only
download chunks that have changed since their last sync.

Architecture::

    Client sends:  {client_version, chunk_hashes: {id: sha256}}
    Server computes diff → returns only changed/new chunks
    Client applies diff → verifies integrity → updates local version

Target: delta sync completes in < 12 seconds for typical daily changes
on a 3G connection (~200 KB/day of changes).

Feature-flagged behind ``offline_sync`` (requires ``offline_rag`` = true).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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

# Sync configuration
SYNC_MAX_DOWNLOAD_BYTES = int(os.getenv("SYNC_MAX_DOWNLOAD_BYTES", "50000000"))  # 50 MB
SYNC_CHUNK_SIZE_BYTES = int(os.getenv("SYNC_CHUNK_SIZE_BYTES", "65536"))  # 64 KB


@dataclass
class SyncDelta:
    """Computed delta between client and server bundle state."""

    server_version: str
    needs_full_sync: bool = False
    changed_chunks: list[dict[str, Any]] = field(default_factory=list)
    deleted_chunk_ids: list[str] = field(default_factory=list)
    total_download_bytes: int = 0
    estimated_sync_seconds: float = 0.0
    error: str | None = None


@dataclass
class SyncEvent:
    """Audit record for a sync operation."""

    device_id: str
    client_version: str
    server_version: str
    sync_type: str  # full | delta | none
    chunks_sent: int
    bytes_sent: int
    duration_s: float
    timestamp: float
    error: str | None = None


class OfflineSyncEngine:
    """Hash-based delta sync engine for offline RAG bundles.

    Compares client-side chunk hashes against the server's current bundle
    to determine the minimal set of changes needed.
    """

    def __init__(self, bundle_dir: Path | None = None) -> None:
        self._bundle_dir = bundle_dir or OFFLINE_BUNDLE_DIR
        self._server_hashes: dict[str, str] = {}
        self._server_version: str = "0.0.0"
        self._chunk_data_dir: Path = self._bundle_dir / "chunks"
        self._sync_log: list[SyncEvent] = []
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def server_version(self) -> str:
        return self._server_version

    def initialize(self) -> bool:
        """Load server-side chunk hashes and version info."""
        try:
            # Load manifest for version
            manifest_path = self._bundle_dir / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
                self._server_version = manifest.get("version", "0.0.0")

            # Load chunk hashes
            hash_path = self._bundle_dir / "chunk_hashes.json"
            if hash_path.exists():
                with open(hash_path, encoding="utf-8") as f:
                    self._server_hashes = json.load(f)

            # If no pre-computed chunk hashes, compute from passages
            if not self._server_hashes:
                self._compute_chunk_hashes()

            self._ready = bool(self._server_hashes)
            if self._ready:
                logger.info(
                    "Sync engine ready: version=%s, %d chunks",
                    self._server_version,
                    len(self._server_hashes),
                )
            return self._ready

        except Exception:
            logger.exception("Sync engine initialization failed")
            return False

    def compute_delta(
        self,
        client_version: str,
        client_chunk_hashes: dict[str, str],
        max_download_bytes: int = SYNC_MAX_DOWNLOAD_BYTES,
    ) -> SyncDelta:
        """Compute the delta between client and server state.

        Args:
            client_version: Client's current bundle version.
            client_chunk_hashes: Map of chunk_id -> sha256 from client.
            max_download_bytes: Maximum bytes the client is willing to download.

        Returns:
            SyncDelta with changed chunks and deleted IDs.
        """
        if not self._ready:
            return SyncDelta(
                server_version=self._server_version,
                error="Sync engine not initialized",
            )

        t0 = time.perf_counter()

        # If client has no hashes, they need a full sync
        if not client_chunk_hashes:
            return SyncDelta(
                server_version=self._server_version,
                needs_full_sync=True,
                estimated_sync_seconds=self._estimate_full_sync_time(),
            )

        # Compute diff
        changed: list[dict[str, Any]] = []
        deleted: list[str] = []
        total_bytes = 0

        # Find changed or new chunks
        for chunk_id, server_hash in self._server_hashes.items():
            client_hash = client_chunk_hashes.get(chunk_id)
            if client_hash != server_hash:
                chunk_size = self._get_chunk_size(chunk_id)
                if total_bytes + chunk_size > max_download_bytes:
                    # Would exceed budget — trigger partial or full sync
                    break
                changed.append({
                    "chunk_id": chunk_id,
                    "sha256": server_hash,
                    "size_bytes": chunk_size,
                    "download_url": f"/v1/offline/chunks/{chunk_id}",
                })
                total_bytes += chunk_size

        # Find deleted chunks (client has them, server doesn't)
        for chunk_id in client_chunk_hashes:
            if chunk_id not in self._server_hashes:
                deleted.append(chunk_id)

        # If too many changes, recommend full sync
        change_ratio = len(changed) / max(len(self._server_hashes), 1)
        needs_full = change_ratio > 0.5  # More than 50% changed → full sync

        duration = time.perf_counter() - t0
        estimated_sync = self._estimate_delta_sync_time(total_bytes)

        logger.info(
            "Delta computed: %d changed, %d deleted, %s download (%.1fms)",
            len(changed),
            len(deleted),
            _human_size(total_bytes),
            duration * 1000,
        )

        return SyncDelta(
            server_version=self._server_version,
            needs_full_sync=needs_full,
            changed_chunks=changed,
            deleted_chunk_ids=deleted,
            total_download_bytes=total_bytes,
            estimated_sync_seconds=estimated_sync,
        )

    def get_chunk_data(self, chunk_id: str) -> bytes | None:
        """Retrieve raw chunk data for download."""
        # Try chunked storage first
        chunk_path = self._chunk_data_dir / f"{chunk_id}.gz"
        if chunk_path.exists():
            import gzip

            with gzip.open(chunk_path, "rb") as f:
                return f.read()

        # Fallback: extract from passages file
        return self._extract_chunk_from_passages(chunk_id)

    def record_sync(self, event: SyncEvent) -> None:
        """Record a sync event for analytics."""
        self._sync_log.append(event)
        # Keep only last 1000 events in memory
        if len(self._sync_log) > 1000:
            self._sync_log = self._sync_log[-500:]

    def get_stats(self) -> dict[str, Any]:
        """Return sync statistics for admin dashboard."""
        total_syncs = len(self._sync_log)
        if not total_syncs:
            return {
                "total_syncs": 0,
                "total_bytes_sent": 0,
                "avg_duration_s": 0.0,
                "unique_devices": 0,
                "server_version": self._server_version,
                "chunk_count": len(self._server_hashes),
            }

        total_bytes = sum(e.bytes_sent for e in self._sync_log)
        avg_duration = sum(e.duration_s for e in self._sync_log) / total_syncs
        unique_devices = len({e.device_id for e in self._sync_log if e.device_id})

        return {
            "total_syncs": total_syncs,
            "total_bytes_sent": total_bytes,
            "avg_duration_s": round(avg_duration, 2),
            "unique_devices": unique_devices,
            "server_version": self._server_version,
            "chunk_count": len(self._server_hashes),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_chunk_hashes(self) -> None:
        """Compute chunk hashes from the passages file."""
        import gzip

        passages_path = self._bundle_dir / "passages.jsonl.gz"
        if not passages_path.exists():
            passages_path = self._bundle_dir / "passages.jsonl"
        if not passages_path.exists():
            return

        opener = gzip.open if str(passages_path).endswith(".gz") else open
        with opener(passages_path, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                chunk_id = f"chunk_{i:06d}"
                chunk_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
                self._server_hashes[chunk_id] = chunk_hash

        # Persist for future use
        hash_path = self._bundle_dir / "chunk_hashes.json"
        try:
            with open(hash_path, "w", encoding="utf-8") as f:
                json.dump(self._server_hashes, f)
        except OSError:
            logger.debug("Could not persist chunk hashes (read-only filesystem?)")

    def _get_chunk_size(self, chunk_id: str) -> int:
        """Get the size of a chunk in bytes."""
        chunk_path = self._chunk_data_dir / f"{chunk_id}.gz"
        if chunk_path.exists():
            return chunk_path.stat().st_size
        # Estimate ~500 bytes per passage chunk
        return 500

    def _extract_chunk_from_passages(self, chunk_id: str) -> bytes | None:
        """Extract a single chunk from the passages file by index."""
        import gzip as _gzip

        try:
            idx = int(chunk_id.split("_")[-1])
        except (ValueError, IndexError):
            return None

        passages_path = self._bundle_dir / "passages.jsonl.gz"
        if not passages_path.exists():
            passages_path = self._bundle_dir / "passages.jsonl"
        if not passages_path.exists():
            return None

        opener = _gzip.open if str(passages_path).endswith(".gz") else open
        with opener(passages_path, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == idx:
                    return line.strip().encode("utf-8")

        return None

    def _estimate_full_sync_time(self) -> float:
        """Estimate full sync duration in seconds (assuming 3G: ~300 KB/s)."""
        manifest_path = self._bundle_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            size_bytes = manifest.get("size_bytes", 80_000_000)
        else:
            size_bytes = 80_000_000  # 80 MB default estimate
        return size_bytes / 300_000  # 300 KB/s on 3G

    def _estimate_delta_sync_time(self, download_bytes: int) -> float:
        """Estimate delta sync duration in seconds."""
        return max(1.0, download_bytes / 300_000)  # 300 KB/s on 3G


def _human_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"
