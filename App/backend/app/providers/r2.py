"""Cloudflare R2 (S3-compatible) object storage.

Stores artifacts that are ephemeral on Crane Cloud's free tier: ``bm25_state.json``
(so BM25/hybrid survives restarts without a mounted volume), offline bundles, and
a TTS audio cache. ``boto3`` is imported lazily so the slim image stays slim and
the module imports fine when R2 is unconfigured.
"""

from __future__ import annotations

import functools
import logging

from .config import get_cloud_settings, is_r2_configured

logger = logging.getLogger("ura.providers.r2")


@functools.lru_cache(maxsize=1)
def _client():
    import boto3  # noqa: PLC0415 — lazy; only needed when R2 is used

    s = get_cloud_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=s.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=s.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    )


def get_object(key: str) -> bytes | None:
    if not is_r2_configured():
        return None
    try:
        s = get_cloud_settings()
        return _client().get_object(Bucket=s.r2_bucket, Key=key)["Body"].read()
    except Exception as exc:
        logger.info("r2 get_object %s failed: %s", key, exc)
        return None


def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    if not is_r2_configured():
        return False
    try:
        s = get_cloud_settings()
        _client().put_object(Bucket=s.r2_bucket, Key=key, Body=data, ContentType=content_type)
        return True
    except Exception as exc:
        logger.warning("r2 put_object %s failed: %s", key, exc)
        return False


def object_exists(key: str) -> bool:
    if not is_r2_configured():
        return False
    try:
        s = get_cloud_settings()
        _client().head_object(Bucket=s.r2_bucket, Key=key)
        return True
    except Exception:
        return False
