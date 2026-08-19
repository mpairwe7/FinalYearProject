"""Idempotent retention job for all application-owned personal-data stores.

The job is run once during application startup and at a bounded interval by
the FastAPI lifespan.  It has no user supplied inputs and every underlying
delete is TTL-based, so running it from more than one replica is safe.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from . import database, documents

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


def run_retention_cleanup() -> dict[str, Any]:
    """Delete expired data and return non-sensitive per-store evidence."""
    from .memory.service import get_memory_service
    from .voice_consent import cleanup_expired_voice_data

    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "database": database.cleanup_expired_data,
        "documents": documents.purge_expired_documents,
        "memory": get_memory_service().cleanup_expired,
        "voice": cleanup_expired_voice_data,
    }
    result: dict[str, Any] = {}
    for store, job in jobs.items():
        try:
            result[store] = job()
        except Exception:
            # One unavailable optional subsystem must not stop deletion from
            # other stores; the exception text could itself expose a provider
            # response, so log only the store name and traceback.
            logger.exception("Retention cleanup failed for store=%s", store)
            result[store] = {"error": "cleanup_failed"}
    logger.info("Retention cleanup completed (stores=%s)", ",".join(sorted(result)))
    return result
