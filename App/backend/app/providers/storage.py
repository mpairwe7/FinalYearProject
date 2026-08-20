"""Pluggable Object Storage Providers (Local, R2, Memory).

Implements the StorageProvider protocol with cloudless offline testability.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .config import is_r2_configured
from .interfaces import StorageProvider
from .r2 import get_object, object_exists, put_object

logger = logging.getLogger("ura.providers.storage")


class MemoryStorageProvider:
    """In-memory ephemeral storage provider for testing and cloudless development."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def get_bytes(self, key: str) -> bytes | None:
        return self._store.get(key)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> bool:
        self._store[key] = bytes(data)
        return True

    def exists(self, key: str) -> bool:
        return key in self._store

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def clear(self) -> None:
        self._store.clear()


class LocalStorageProvider:
    """Local filesystem storage provider for bare-metal / container volumes."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            raw = os.getenv("STORAGE_LOCAL_DIR", "/tmp/ura_storage")
            self.base_dir = Path(raw).resolve()
        else:
            self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Sanitize key to prevent path traversal
        clean_key = key.lstrip("/").replace("..", "_")
        target = (self.base_dir / clean_key).resolve()
        if not str(target).startswith(str(self.base_dir)):
            raise ValueError(f"Path traversal attempted: {key}")
        return target

    def get_bytes(self, key: str) -> bytes | None:
        try:
            path = self._path(key)
            if path.is_file():
                return path.read_bytes()
            return None
        except Exception as err:
            logger.debug("LocalStorage get_bytes failed for %s: %s", key, err)
            return None

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> bool:
        try:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return True
        except Exception as err:
            logger.warning("LocalStorage put_bytes failed for %s: %s", key, err)
            return False

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        try:
            path = self._path(key)
            if path.is_file():
                path.unlink()
                return True
            return False
        except Exception:
            return False


class R2StorageProvider:
    """Cloudflare R2 object storage provider (falls back gracefully if unconfigured)."""

    def get_bytes(self, key: str) -> bytes | None:
        return get_object(key)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> bool:
        return put_object(key, data, content_type=content_type)

    def exists(self, key: str) -> bool:
        return object_exists(key)

    def delete(self, key: str) -> bool:
        # R2 deletion stub
        return False


_GLOBAL_STORAGE_PROVIDER: StorageProvider | None = None


def get_storage_provider() -> StorageProvider:
    """Return the active configured StorageProvider based on STORAGE_PROVIDER env."""
    global _GLOBAL_STORAGE_PROVIDER
    if _GLOBAL_STORAGE_PROVIDER is not None:
        return _GLOBAL_STORAGE_PROVIDER

    provider_type = os.getenv("STORAGE_PROVIDER", "local").strip().lower()
    if provider_type in {"memory", "in_memory"}:
        _GLOBAL_STORAGE_PROVIDER = MemoryStorageProvider()
    elif provider_type in {"r2", "cloudflare", "s3"} and is_r2_configured():
        _GLOBAL_STORAGE_PROVIDER = R2StorageProvider()
    else:
        _GLOBAL_STORAGE_PROVIDER = LocalStorageProvider()

    return _GLOBAL_STORAGE_PROVIDER
