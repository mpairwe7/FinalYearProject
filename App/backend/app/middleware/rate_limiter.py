"""Rate limiting: the shared slowapi limiter and the per-route ceilings."""

from __future__ import annotations

import logging
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

RATE_LIMIT = os.getenv("RATE_LIMIT", "30/minute")
EXPORT_RATE_LIMIT = os.getenv("EXPORT_RATE_LIMIT", "10/minute")
DOCUMENT_RATE_LIMIT = os.getenv("DOCUMENT_RATE_LIMIT", "10/minute")

#: The taxpayer's chat polls its officer thread every few seconds (more often
#: than a person types), so the poll has its own, higher ceiling.
CHAT_POLL_RATE_LIMIT = os.getenv("CHAT_POLL_RATE_LIMIT", "120/minute")

# Set SLOWAPI_STORAGE_URI=redis://redis:6379 for multi-worker / multi-replica
# deploys.  When unset, the limiter uses an in-process memory bucket — this
# is fine for a single-worker dev server but will NOT correctly enforce a
# shared limit across workers or Kubernetes replicas.
_SLOWAPI_STORAGE_URI = os.getenv("SLOWAPI_STORAGE_URI", "")
_limiter_kwargs: dict = {"key_func": get_remote_address, "default_limits": []}
if _SLOWAPI_STORAGE_URI:
    _limiter_kwargs["storage_uri"] = _SLOWAPI_STORAGE_URI
    logger.info("Rate limiter using Redis storage: %s", _SLOWAPI_STORAGE_URI.split("@")[-1])

limiter = Limiter(**_limiter_kwargs)
