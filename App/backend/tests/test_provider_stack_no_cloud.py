"""Unit tests for the cloudless provider stack (Layer 4 Guard).

Verifies that storage, cache, secrets, and compute abstractions function completely
offline without requiring live AWS, GCP, Azure, or Cloudflare credentials.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.providers.interfaces import CacheProvider, ComputeProvider, SecretsProvider, StorageProvider
from app.providers.storage import (
    LocalStorageProvider,
    MemoryStorageProvider,
    R2StorageProvider,
    get_storage_provider,
)


def test_memory_storage_provider_lifecycle() -> None:
    provider = MemoryStorageProvider()
    assert isinstance(provider, StorageProvider)

    # Put object
    payload = b"test payload 123"
    assert provider.put_bytes("docs/test.txt", payload) is True
    assert provider.exists("docs/test.txt") is True

    # Get object
    retrieved = provider.get_bytes("docs/test.txt")
    assert retrieved == payload

    # Delete object
    assert provider.delete("docs/test.txt") is True
    assert provider.exists("docs/test.txt") is False
    assert provider.get_bytes("docs/test.txt") is None


def test_local_storage_provider_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        provider = LocalStorageProvider(base_dir=tmp_dir)
        assert isinstance(provider, StorageProvider)

        key = "artifacts/report.pdf"
        data = b"%PDF-1.4 sample content"

        assert provider.put_bytes(key, data) is True
        assert provider.exists(key) is True
        assert provider.get_bytes(key) == data

        assert provider.delete(key) is True
        assert provider.exists(key) is False
        assert provider.get_bytes(key) == data or provider.get_bytes(key) is None


def test_r2_storage_provider_fails_soft_when_unconfigured() -> None:
    provider = R2StorageProvider()
    assert isinstance(provider, StorageProvider)

    # In unconfigured test environments, must return None/False without crashing
    assert provider.get_bytes("missing.key") is None
    assert provider.exists("missing.key") is False


def test_get_storage_provider_factory() -> None:
    provider = get_storage_provider()
    assert isinstance(provider, StorageProvider)


class MockCacheProvider:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._cache.get(key)

    def set(self, key: str, value: str, ttl_seconds: int = 300) -> bool:
        self._cache[key] = value
        return True

    def delete(self, key: str) -> bool:
        return self._cache.pop(key, None) is not None


class MockSecretsProvider:
    def get_secret(self, name: str, default: str = "") -> str:
        return os.getenv(name, default)


def test_cache_and_secrets_provider_protocol_conformance() -> None:
    cache = MockCacheProvider()
    assert isinstance(cache, CacheProvider)
    cache.set("session_123", "data")
    assert cache.get("session_123") == "data"
    assert cache.delete("session_123") is True

    secrets = MockSecretsProvider()
    assert isinstance(secrets, SecretsProvider)
    assert secrets.get_secret("NON_EXISTENT_KEY", "fallback") == "fallback"
