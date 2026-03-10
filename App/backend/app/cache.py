"""Semantic cache — avoid redundant LLM calls for similar queries.

Embeds each query with the same dense model used for retrieval. If a
cached query exists within cosine similarity >= threshold, the cached
response is returned directly, saving LLM latency and cost.

Environment variables:
    CACHE_ENABLED           – enable/disable (default: true)
    CACHE_THRESHOLD         – cosine similarity threshold (default: 0.92)
    CACHE_TTL_SECONDS       – entry expiry (default: 3600 = 1 hour)
    CACHE_MAX_SIZE          – max entries (default: 1000)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_THRESHOLD = float(os.getenv("CACHE_THRESHOLD", "0.92"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "1000"))


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
        self._entries = [
            e for e in self._entries
            if (now - e.created_at) < CACHE_TTL_SECONDS
        ]
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
                    best_sim, query[:50], best_entry.query[:50],
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

            self._entries.append(CacheEntry(
                query=query,
                embedding=embedding,
                response=response,
            ))

    @property
    def stats(self) -> dict[str, int]:
        return {**self._stats, "size": len(self._entries)}
