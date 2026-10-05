"""Offline RAG bundle and delta sync routes."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..analytics import metrics
from ..auth import AuthContext, current_user
from ..flags import flags
from ..models import (
    OfflineBundleInfo,
    OfflineStatusResponse,
    OfflineSyncRequest,
    OfflineSyncResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["offline"])


@router.get("/v1/offline/status", response_model=OfflineStatusResponse)
def offline_status(
    request: Request,
    _ctx: AuthContext = Depends(current_user),
) -> OfflineStatusResponse:
    """Get offline bundle availability and sync status."""
    if not flags.is_enabled("offline_rag"):
        return OfflineStatusResponse(available=False)

    from ..offline_bundle import BundleManager

    manager = BundleManager()
    info = manager.get_info()

    bundle_info = None
    if info.available:
        bundle_info = OfflineBundleInfo(
            version=info.version,
            size_bytes=info.size_bytes,
            size_mb=info.size_mb,
            passage_count=info.passage_count,
            index_dim=info.index_dim,
            sha256=info.sha256,
            created_at=info.created_at,
            min_app_version=info.min_app_version,
        )

    return OfflineStatusResponse(
        available=info.available,
        bundle=bundle_info,
        sync_enabled=flags.is_enabled("offline_sync"),
    )


@router.post("/v1/offline/sync", response_model=OfflineSyncResponse)
def offline_sync(
    body: OfflineSyncRequest,
    request: Request,
    _ctx: AuthContext = Depends(current_user),
) -> OfflineSyncResponse:
    """Compute delta sync for a client's offline bundle."""
    if not flags.is_enabled("offline_sync"):
        raise HTTPException(
            status_code=404,
            detail="Offline sync is disabled (FLAG_OFFLINE_SYNC=false)",
        )

    from ..offline_sync import OfflineSyncEngine, SyncEvent

    engine = OfflineSyncEngine()
    if not engine.initialize():
        raise HTTPException(status_code=503, detail="Sync engine not available")

    t0 = time.perf_counter()
    delta = engine.compute_delta(
        client_version=body.client_version,
        client_chunk_hashes=body.client_chunk_hashes,
        max_download_bytes=body.max_download_bytes,
    )
    duration = time.perf_counter() - t0

    engine.record_sync(
        SyncEvent(
            device_id=body.device_id,
            client_version=body.client_version,
            server_version=delta.server_version,
            sync_type="full" if delta.needs_full_sync else "delta",
            chunks_sent=len(delta.changed_chunks),
            bytes_sent=delta.total_download_bytes,
            duration_s=round(duration, 3),
            timestamp=time.time(),
        )
    )

    return OfflineSyncResponse(
        server_version=delta.server_version,
        needs_full_sync=delta.needs_full_sync,
        changed_chunks=delta.changed_chunks,
        deleted_chunk_ids=delta.deleted_chunk_ids,
        total_download_bytes=delta.total_download_bytes,
        estimated_sync_seconds=delta.estimated_sync_seconds,
    )


@router.get("/v1/offline/bundle")
def download_offline_bundle(
    request: Request,
    _ctx: AuthContext = Depends(current_user),
):
    """Download the latest offline RAG bundle."""
    if not flags.is_enabled("offline_bundle_api"):
        raise HTTPException(
            status_code=404,
            detail="Offline bundle API is disabled (FLAG_OFFLINE_BUNDLE_API=false)",
        )

    from ..offline_bundle import BundleManager

    manager = BundleManager()
    bundle_path = manager.get_bundle_path()

    if bundle_path is None or not bundle_path.exists():
        raise HTTPException(status_code=404, detail="No offline bundle available")

    size = bundle_path.stat().st_size
    manager.record_download(size)
    metrics.inc("offline_bundle_downloads_total")

    return FileResponse(
        path=str(bundle_path),
        media_type="application/gzip",
        filename=bundle_path.name,
        headers={
            "Content-Length": str(size),
            "X-Bundle-Version": manager.get_info().version,
        },
    )
