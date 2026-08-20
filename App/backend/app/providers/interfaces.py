"""Cloud-agnostic provider interface contracts (Layer 2 - WHAT contracts are).

Follows the 4-tier cloud operating model:
- Defines abstract Protocol interfaces for Storage, Cache/Queue, and Compute.
- Implementations must never leak provider-specific symbols or require live cloud
  credentials during local / cloudless testing.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageProvider(Protocol):
    """Abstract object storage provider (Local, R2, S3, In-Memory)."""

    def get_bytes(self, key: str) -> bytes | None:
        """Fetch an object's raw bytes, or None if absent."""
        ...

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> bool:
        """Store raw bytes; return True on success, False otherwise."""
        ...

    def exists(self, key: str) -> bool:
        """Return True if the object exists in storage."""
        ...

    def delete(self, key: str) -> bool:
        """Delete an object; return True if deleted, False otherwise."""
        ...


@runtime_checkable
class QueueProvider(Protocol):
    """Abstract message / event queue provider (In-Memory, Redis)."""

    def enqueue(self, queue_name: str, payload: dict[str, Any]) -> str:
        """Enqueue a message payload; returns message ID."""
        ...

    def dequeue(self, queue_name: str, *, timeout: float = 0.0) -> dict[str, Any] | None:
        """Dequeue a message payload or None if empty."""
        ...

    def length(self, queue_name: str) -> int:
        """Return current queue depth."""
        ...


@runtime_checkable
class ComputeProvider(Protocol):
    """Abstract model / inference compute provider (Local, vLLM, Cloudflare, Gemini)."""

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate text completion from prompt."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate dense vector embeddings."""
        ...


@runtime_checkable
class CacheProvider(Protocol):
    """Abstract key-value caching provider (In-Memory, Redis)."""

    def get(self, key: str) -> str | None:
        """Get value by key or None."""
        ...

    def set(self, key: str, value: str, ttl_seconds: int = 300) -> bool:
        """Set value with TTL."""
        ...

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        ...


@runtime_checkable
class SecretsProvider(Protocol):
    """Abstract secret retrieval provider (Env, GCP Secret Manager, Vault)."""

    def get_secret(self, name: str, default: str = "") -> str:
        """Retrieve a secret by name."""
        ...
