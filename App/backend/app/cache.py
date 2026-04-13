"""Semantic cache — avoid redundant LLM calls for similar queries.

Embeds each query with the same dense model used for retrieval. If a
cached query exists within cosine similarity >= threshold, the cached
response is returned directly, saving LLM latency and cost.

Two backends are available:

- **in-process** (default) — ``SemanticCache``, a thread-safe list with
  cosine-similarity lookup.  Zero extra deps; perfect for single-worker
  dev and single-node deploys.
- **redis** — ``RedisSemanticCache`` stores embeddings as base64-encoded
  numpy bytes in a Redis key, so hits are shared across workers / replicas.
  Enable by setting ``CACHE_BACKEND=redis`` and ``REDIS_URL``.

Environment variables:
    CACHE_BACKEND           – "memory" (default) or "redis"
    CACHE_ENABLED           – enable/disable (default: true)
    CACHE_THRESHOLD         – cosine similarity threshold (default: 0.92)
    CACHE_TTL_SECONDS       – entry expiry (default: 3600 = 1 hour)
    CACHE_MAX_SIZE          – max entries (default: 1000)
    REDIS_URL               – Redis URI (default: redis://localhost:6379/0)
    CACHE_REDIS_PREFIX      – key prefix (default: ura:cache:)
"""

from __future__ import annotations

import base64
import contextlib
import json as _json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

CACHE_BACKEND = os.getenv("CACHE_BACKEND", "memory").lower()
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_THRESHOLD = float(os.getenv("CACHE_THRESHOLD", "0.92"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "1000"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_REDIS_PREFIX = os.getenv("CACHE_REDIS_PREFIX", "ura:cache:")


@dataclass
class CacheEntry:
    query: str
    embedding: np.ndarray
    response: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    hits: int = 0


class SemanticCache:
    """In-memory semantic cache with cosine similarity matching."""

    def __init__(self, dense_model: Any = None) -> None:
        self._entries: list[CacheEntry] = []
        self._lock = threading.Lock()
        self._dense_model = dense_model
        self._stats = {"hits": 0, "misses": 0, "evictions": 0}

    def set_model(self, model: Any) -> None:
        """Set or update the embedding model (shared with retriever)."""
        self._dense_model = model

    def _embed(self, text: str) -> np.ndarray | None:
        if self._dense_model is None:
            return None
        try:
            return self._dense_model.encode(text, normalize_embeddings=True)
        except Exception:
            logger.debug("Cache embedding failed", exc_info=True)
            return None

    def _cosine_sim(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    def _evict_expired(self) -> None:
        """Remove expired entries."""
        now = time.time()
        before = len(self._entries)
        self._entries = [e for e in self._entries if (now - e.created_at) < CACHE_TTL_SECONDS]
        evicted = before - len(self._entries)
        if evicted:
            self._stats["evictions"] += evicted

    def get(self, query: str, locale: str = "en") -> dict[str, Any] | None:
        """Look up a semantically similar cached response.

        Returns the cached response dict or None on miss.
        """
        if not CACHE_ENABLED or not self._dense_model:
            return None

        with self._lock:
            # Embed inside lock to prevent model swap race with set_model()
            embedding = self._embed(query)
            if embedding is None:
                return None

            self._evict_expired()

            best_sim = 0.0
            best_entry: CacheEntry | None = None

            for entry in self._entries:
                # Must match locale
                if entry.response.get("locale") != locale:
                    continue
                sim = self._cosine_sim(embedding, entry.embedding)
                if sim > best_sim:
                    best_sim = sim
                    best_entry = entry

            if best_entry and best_sim >= CACHE_THRESHOLD:
                best_entry.hits += 1
                self._stats["hits"] += 1
                logger.debug(
                    "Cache HIT: sim=%.4f query=%s → cached=%s",
                    best_sim,
                    query[:50],
                    best_entry.query[:50],
                )
                return best_entry.response

            self._stats["misses"] += 1
            return None

    def put(self, query: str, response: dict[str, Any]) -> None:
        """Store a query-response pair in the cache."""
        if not CACHE_ENABLED or not self._dense_model:
            return

        with self._lock:
            embedding = self._embed(query)
            if embedding is None:
                return
            # Enforce max size (LRU-style: remove oldest)
            if len(self._entries) >= CACHE_MAX_SIZE:
                self._entries.sort(key=lambda e: e.created_at)
                removed = len(self._entries) - CACHE_MAX_SIZE + 1
                self._entries = self._entries[removed:]
                self._stats["evictions"] += removed

            self._entries.append(
                CacheEntry(
                    query=query,
                    embedding=embedding,
                    response=response,
                )
            )

    @property
    def stats(self) -> dict[str, int]:
        return {**self._stats, "size": len(self._entries)}


# ---------------------------------------------------------------------------
# Redis-backed semantic cache (CACHE_BACKEND=redis)
# ---------------------------------------------------------------------------
class RedisSemanticCache:
    """Shared-memory semantic cache backed by Redis.

    Keys are prefixed with ``CACHE_REDIS_PREFIX`` so multiple apps can share
    a Redis instance safely.  Each entry stores the query, a base64 numpy
    embedding, and the JSON-serialised response.  TTL is enforced per-key
    via Redis ``EXPIRE``.  Similarity search is still a linear scan over
    keys — upgrade to RediSearch / Qdrant-as-cache when >10k entries.
    """

    def __init__(self, dense_model: Any = None) -> None:
        self._dense_model = dense_model
        self._lock = threading.Lock()
        self._client: Any = None
        self._stats = {"hits": 0, "misses": 0, "evictions": 0}
        try:
            import redis

            self._client = redis.Redis.from_url(REDIS_URL, decode_responses=False)
            self._client.ping()
            logger.info("RedisSemanticCache connected to %s", REDIS_URL.split("@")[-1])
        except Exception:
            logger.warning("Redis cache backend unavailable; falling back to memory", exc_info=True)
            self._client = None

    def set_model(self, model: Any) -> None:
        self._dense_model = model

    def _embed(self, text: str) -> np.ndarray | None:
        if self._dense_model is None:
            return None
        try:
            return self._dense_model.encode(text, normalize_embeddings=True)
        except Exception:
            logger.debug("Cache embedding failed", exc_info=True)
            return None

    @staticmethod
    def _encode_emb(emb: np.ndarray) -> str:
        return base64.b64encode(emb.astype(np.float32).tobytes()).decode("ascii")

    @staticmethod
    def _decode_emb(blob: bytes) -> np.ndarray:
        return np.frombuffer(base64.b64decode(blob), dtype=np.float32)

    def get(self, query: str, locale: str = "en") -> dict[str, Any] | None:
        if not CACHE_ENABLED or self._client is None or self._dense_model is None:
            return None

        embedding = self._embed(query)
        if embedding is None:
            return None

        try:
            # Linear scan — fine for the hundreds/low-thousands range typical
            # for a domain chatbot.  For larger cache footprints, migrate to
            # RediSearch or a dedicated vector index.
            pattern = f"{CACHE_REDIS_PREFIX}*"
            keys = list(self._client.scan_iter(match=pattern, count=200))
            best_sim = 0.0
            best_response: dict[str, Any] | None = None
            for key in keys:
                data = self._client.hgetall(key)
                if not data:
                    continue
                entry_locale = data.get(b"locale", b"en").decode("utf-8", "ignore")
                if entry_locale != locale:
                    continue
                emb_b64 = data.get(b"embedding")
                if not emb_b64:
                    continue
                entry_emb = self._decode_emb(emb_b64)
                if entry_emb.shape != embedding.shape:
                    continue
                sim = float(np.dot(embedding, entry_emb))
                if sim > best_sim:
                    best_sim = sim
                    if sim >= CACHE_THRESHOLD:
                        resp_json = data.get(b"response", b"{}").decode("utf-8", "ignore")
                        try:
                            best_response = _json.loads(resp_json)
                        except Exception:
                            best_response = None

            if best_response:
                self._stats["hits"] += 1
                return best_response
            self._stats["misses"] += 1
            return None
        except Exception:
            logger.debug("Redis cache get failed", exc_info=True)
            return None

    def put(self, query: str, response: dict[str, Any]) -> None:
        if not CACHE_ENABLED or self._client is None or self._dense_model is None:
            return
        embedding = self._embed(query)
        if embedding is None:
            return
        try:
            # Key includes a monotonic timestamp suffix so collisions are
            # avoided when the same query is inserted twice.
            key = f"{CACHE_REDIS_PREFIX}{int(time.time() * 1000)}"
            locale = response.get("locale", "en")
            self._client.hset(
                key,
                mapping={
                    "query": query.encode("utf-8"),
                    "locale": locale.encode("utf-8"),
                    "embedding": self._encode_emb(embedding),
                    "response": _json.dumps(response).encode("utf-8"),
                },
            )
            self._client.expire(key, CACHE_TTL_SECONDS)
        except Exception:
            logger.debug("Redis cache put failed", exc_info=True)

    @property
    def stats(self) -> dict[str, int]:
        size = 0
        if self._client is not None:
            with contextlib.suppress(Exception):
                size = sum(
                    1 for _ in self._client.scan_iter(match=f"{CACHE_REDIS_PREFIX}*", count=200)
                )
        return {**self._stats, "size": size}


def create_cache(dense_model: Any = None) -> Any:
    """Factory that returns the configured cache backend.

    Falls back to the in-process ``SemanticCache`` if Redis is requested
    but unavailable.
    """
    if CACHE_BACKEND == "redis":
        cache = RedisSemanticCache(dense_model=dense_model)
        if cache._client is not None:
            return cache
        logger.warning("CACHE_BACKEND=redis but Redis not reachable; using memory cache")
    return SemanticCache(dense_model=dense_model)
