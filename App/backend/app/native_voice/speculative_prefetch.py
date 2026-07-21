"""Speculative retrieval — start RAG search on partial ASR hypotheses (2026).

When the streaming ASR produces a *stable prefix* (tokens confirmed
across multiple consecutive windows), we launch a background retrieval
task against the :class:`HybridRetriever`.  If the final ASR transcript
starts with the same prefix, we reuse the cached hits — saving 100-300ms
off the critical path.

Feature flag: ``speculative_prefetch``
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIN_PREFETCH_TOKENS = int(os.getenv("SPECULATIVE_PREFETCH_MIN_TOKENS", "4"))
PREFETCH_STALE_MS = float(os.getenv("SPECULATIVE_PREFETCH_STALE_MS", "3000"))
PREFETCH_TIMEOUT_S = float(os.getenv("SPECULATIVE_PREFETCH_TIMEOUT_S", "0.5"))

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class PrefetchResult:
    """Cached result of a speculative retrieval."""

    query: str
    hits: list[dict]
    started_at: float
    completed_at: float
    was_used: bool = False

    @property
    def age_ms(self) -> float:
        return (time.perf_counter() - self.completed_at) * 1000

    @property
    def is_stale(self) -> bool:
        return self.age_ms > PREFETCH_STALE_MS


@dataclass
class PrefetchStats:
    """Cumulative statistics for monitoring."""

    attempts: int = 0
    hits: int = 0
    misses: int = 0
    errors: int = 0
    stale_discards: int = 0

    @property
    def hit_rate(self) -> float:
        return self.hits / max(self.attempts, 1)


# ---------------------------------------------------------------------------
# SpeculativePrefetcher
# ---------------------------------------------------------------------------


class SpeculativePrefetcher:
    """Background retrieval on partial ASR stable prefixes.

    Thread-safety: all state is accessed from the async event loop.
    The retrieval itself runs in the default executor (thread pool).

    Usage::

        prefetcher = SpeculativePrefetcher(retriever)

        # Called on each streaming ASR partial:
        await prefetcher.maybe_prefetch("how much VAT")

        # Called when ASR finalises:
        hits = await prefetcher.resolve("how much VAT do I pay on imports")
        if hits is not None:
            # use cached hits — saved 100-300ms
        else:
            # prefix diverged — do full retrieval
    """

    def __init__(
        self,
        retriever,
        top_k: int = 6,
        prefetch_limit: int = 20,
    ) -> None:
        self._retriever = retriever
        self._top_k = top_k
        self._prefetch_limit = prefetch_limit
        self._pending: asyncio.Task | None = None
        self._result: PrefetchResult | None = None
        self._last_prefix_hash: str = ""
        self.stats = PrefetchStats()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def maybe_prefetch(self, stable_prefix: str) -> None:
        """Start a background retrieval if *stable_prefix* is long enough and new.

        Called on every streaming ASR partial hypothesis.  De-duplicates
        by hashing the prefix — same prefix → same task, no re-fetch.
        """
        tokens = stable_prefix.strip().split()
        if len(tokens) < MIN_PREFETCH_TOKENS:
            return

        prefix_hash = hashlib.md5(stable_prefix.lower().encode()).hexdigest()[:12]
        if prefix_hash == self._last_prefix_hash:
            return  # identical prefix already in flight or completed
        self._last_prefix_hash = prefix_hash

        # Cancel previous in-flight prefetch
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()

        self.stats.attempts += 1
        self._pending = asyncio.create_task(
            self._do_prefetch(stable_prefix),
            name=f"prefetch-{prefix_hash}",
        )

    async def resolve(self, final_query: str) -> list[dict] | None:
        """Check if the cached prefetch is usable for *final_query*.

        Returns the cached hit list if the final query starts with the
        prefetched prefix (allowing suffix additions), or ``None`` if
        the result is stale, absent, or the query diverged.
        """
        # Wait briefly for in-flight prefetch to land
        if self._pending is not None and not self._pending.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._pending),
                    timeout=PREFETCH_TIMEOUT_S,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.stats.misses += 1
                return None

        if self._result is None:
            self.stats.misses += 1
            return None

        if self._result.is_stale:
            self.stats.stale_discards += 1
            logger.debug(
                "Speculative prefetch stale (age=%.0fms > %.0fms)",
                self._result.age_ms,
                PREFETCH_STALE_MS,
            )
            return None

        # Match: final query must start with the prefetched query text
        if final_query.lower().startswith(self._result.query.lower()):
            self._result.was_used = True
            self.stats.hits += 1
            logger.info(
                "Speculative prefetch HIT (prefix=%r → final=%r, age=%.0fms)",
                self._result.query[:40],
                final_query[:40],
                self._result.age_ms,
            )
            return self._result.hits

        self.stats.misses += 1
        logger.debug(
            "Speculative prefetch MISS (prefix=%r diverged from final=%r)",
            self._result.query[:40],
            final_query[:40],
        )
        return None

    def reset(self) -> None:
        """Cancel any in-flight prefetch and clear cached results."""
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None
        self._result = None
        self._last_prefix_hash = ""

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _do_prefetch(self, query: str) -> None:
        """Run retrieval in the executor and cache the result."""
        t0 = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            hits = await loop.run_in_executor(
                None,
                lambda: self._retriever.search(
                    query,
                    top_k=self._top_k,
                    prefetch_limit=self._prefetch_limit,
                ),
            )
            self._result = PrefetchResult(
                query=query,
                hits=hits,
                started_at=t0,
                completed_at=time.perf_counter(),
            )
            logger.debug(
                "Speculative prefetch completed: %r → %d hits in %.0fms",
                query[:40],
                len(hits),
                (time.perf_counter() - t0) * 1000,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.stats.errors += 1
            logger.warning("Speculative prefetch failed", exc_info=True)
            self._result = None
