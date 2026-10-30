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
import hashlib
import json as _json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .analytics import metrics

logger = logging.getLogger(__name__)

CACHE_BACKEND = os.getenv("CACHE_BACKEND", "memory").lower()
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_THRESHOLD = float(os.getenv("CACHE_THRESHOLD", "0.92"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "1000"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_REDIS_PREFIX = os.getenv("CACHE_REDIS_PREFIX", "ura:cache:")

_CACHE_QUERY_STOPWORDS = frozenset(
    "a an the is are was were be been am do does did will would can could may "
    "might must have has had of in on at to for with by from and or not no "
    "but so if then than that this these those it its i me my we our you your "
    "what which who whom how when where why about current details information "
    "please tell know like want".split()
)
_CACHE_TERM_ALIASES = {
    "applications": "application",
    "deadlines": "deadline",
    "documents": "document",
    "filing": "file",
    "filings": "file",
    "imports": "import",
    "penalties": "penalty",
    "payments": "payment",
    "rates": "rate",
    "returns": "return",
    "taxes": "tax",
    "thresholds": "threshold",
    "vehicles": "vehicle",
}


#: Words that change how a question reads but not what it asks for. A cached
#: answer may be reused across these ("What are the steps to register for a
#: TIN?" / "How do I register for a TIN?"), never across a content word that
#: one question has and the other lacks. "standard" and "normal" name the
#: default reading of a rate, which is what an unqualified question asks for;
#: "reduced" or "zero" do not, so they stay content words.
_CACHE_FILLER_TERMS = frozenset(
    "uganda ugandan ura revenue authority kindly exactly actually really just "
    "simply briefly quick quickly also steps step process procedure procedures "
    "way ways guide guidance help explain describe show give standard normal".split()
)

#: Negation flips the answer and is invisible to the term sets, because "not"
#: and "no" are stopwords there.
_CACHE_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|without|cannot|neither|nor|none|bila|hapana|tewali|nedda|ngaaha|pe)\b|n['’]t\b",
    re.I,
)
_COMMA_GROUPED_DIGIT_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")


def _query_terms(text: str) -> set[str]:
    cleaned = _COMMA_GROUPED_DIGIT_RE.sub("", (text or "").lower())
    return {
        _CACHE_TERM_ALIASES.get(token, token)
        for token in re.findall(r"[a-z0-9]+", cleaned)
        if token not in _CACHE_QUERY_STOPWORDS
    }


def _cache_queries_compatible(query: str, cached_query: str) -> bool:
    """Whether an answer cached for *cached_query* may be served for *query*.

    Embedding similarity is useful for paraphrases but can be dangerously high
    for neighbouring FAQ intents. The check used to ask for two shared content
    terms, which let every narrowing or contrasting word through:

    * "What is the VAT rate?" took the answer cached for "What is the VAT rate
      on exports?" — the zero rate for the standard one, sharing {vat, rate};
    * "What is the PAYE registration threshold?" took the VAT threshold,
      sharing {registration, threshold};
    * "VAT rate FY2023-24" took "VAT rate FY2025-26", sharing {vat, rate};
    * "Is a TIN not required for this?" took the answer to the same question
      without "not", since negations are stopwords.

    Every one of those is a confident wrong answer with a citation. So the two
    questions must now ask for the *same* content terms once filler is set
    aside, and agree on negation; the cosine threshold then only decides
    between phrasings of one question. A miss costs a retrieval; a false hit
    costs a taxpayer the right answer, so this fails closed.
    """
    requested = _query_terms(query) - _CACHE_FILLER_TERMS
    cached = _query_terms(cached_query) - _CACHE_FILLER_TERMS
    if not requested or requested != cached:
        return False
    return bool(_CACHE_NEGATION_RE.search(query or "")) == bool(_CACHE_NEGATION_RE.search(cached_query or ""))


#: Placeholder embedding for exact-tier entries, which carry no vector.
_EMPTY_EMBEDDING = np.zeros(0, dtype=np.float32)
_EXACT_KEY_STRIP_RE = re.compile(r"[^\w\s]+")
_EXACT_KEY_WS_RE = re.compile(r"\s+")


#: How long a resolved index stamp is trusted before it is read again. The index
#: is normally baked at build time and the process restarts on deploy, but it can
#: also be rebuilt in place through the index API, and a minute is short enough
#: that nobody chases a stale answer for long.
_INDEX_STAMP_TTL_SECONDS = 60.0

_index_stamp_value: str | None = None
_index_stamp_read_at: float = 0.0
_index_stamp_lock = threading.Lock()


#: Corpus hash of the collection the retriever last bound to ("" before any).
_bound_index_hash: str = ""


def register_index_binding(corpus_hash: str) -> None:
    """Record the corpus the retriever is now serving; a change retires the cache.

    The freshness status file is only rewritten by the freshness check and the
    index lifecycle. ``python -m app.indexer``, ``POST /v1/index`` and a
    retriever re-initialised onto a promoted alias rebuilt or swapped the index
    without touching it, so the stamp — and every cached answer — survived the
    change it exists to detect.
    """
    global _bound_index_hash
    value = str(corpus_hash or "")[:12]
    if value != _bound_index_hash:
        _bound_index_hash = value
        reset_index_stamp()


def _read_index_stamp() -> str:
    """Short id of the corpus the live index was built from, or "" if unknown."""
    try:
        from .freshness import load_status

        status = load_status() or {}
        stamp = str(status.get("index_corpus_hash") or status.get("corpus_hash") or "")[:12]
    except Exception:  # pragma: no cover - a missing snapshot must not break lookups
        logger.debug("Could not read the index stamp for the cache key", exc_info=True)
        stamp = ""
    return f"{stamp}+{_bound_index_hash}" if _bound_index_hash else stamp


def index_stamp() -> str:
    """Cached :func:`_read_index_stamp`, refreshed at most once a minute."""
    global _index_stamp_value, _index_stamp_read_at

    now = time.time()
    if _index_stamp_value is not None and (now - _index_stamp_read_at) < _INDEX_STAMP_TTL_SECONDS:
        return _index_stamp_value
    with _index_stamp_lock:
        if _index_stamp_value is None or (time.time() - _index_stamp_read_at) >= _INDEX_STAMP_TTL_SECONDS:
            _index_stamp_value = _read_index_stamp()
            _index_stamp_read_at = time.time()
    return _index_stamp_value or ""


def reset_index_stamp() -> None:
    """Drop the memoised stamps. For tests and for a reindex that wants immediacy."""
    global _index_stamp_value, _index_stamp_read_at, _generation_tail, _generation_read_at
    with _index_stamp_lock:
        _index_stamp_value = None
        _index_stamp_read_at = 0.0
    with _generation_lock:
        _generation_tail = None
        _generation_read_at = 0.0


_generation_tail: str | None = None
_generation_read_at: float = 0.0
_generation_lock = threading.Lock()


def _read_tombstone_stamp() -> str:
    """Digest of the corpus tombstones retrieval applies, or "" when none do.

    A tombstone withdraws a passage that staff found wrong or superseded, and
    retrieval drops it from that moment. An answer cached from it does not: it
    stays servable for the rest of its TTL, so the correction looks like it
    never landed — the failure the index stamp fixed for reindexes. Read from
    the shared store rather than from what retrieval last saw, because a
    replica answering everything from the cache never runs retrieval at all.
    """
    try:
        from .flags import flags

        if not flags.is_enabled("knowledge_discrepancy_reporting"):
            return ""
        # The same read, and the same outage rule, retrieval applies.
        from .retriever import active_tombstones_snapshot

        rows, known = active_tombstones_snapshot()
    except Exception:
        logger.debug("Could not read corpus tombstones for the cache key", exc_info=True)
        rows, known = [], False
    if not known:
        # Unknown is not "none": a fixed value here would let entries written
        # during one outage be served in the next. A fresh value per read
        # confines them to the window they were written in.
        return f"unread-{uuid.uuid4().hex[:8]}"
    ids = sorted(str(row.get("id") or "") for row in rows or [] if row.get("id"))
    if not ids:
        return ""
    return hashlib.sha256("\x1f".join(ids).encode("utf-8")).hexdigest()[:12]


def _read_fiscal_year() -> str:
    """Fiscal year in force, so "the current rate" is not reused across 1 July."""
    try:
        from .query import current_fiscal_year

        return current_fiscal_year()
    except Exception:  # pragma: no cover - defensive
        return ""


def cache_generation() -> str:
    """Everything a cached answer depends on besides the question itself.

    The corpus the index was built from, the tombstones retrieval applies and
    the fiscal year in force. Both tiers key on it: an entry from another
    generation is never served, whichever tier holds it.
    """
    global _generation_tail, _generation_read_at

    stamp = index_stamp()
    now = time.time()
    with _generation_lock:
        if _generation_tail is None or (now - _generation_read_at) >= _INDEX_STAMP_TTL_SECONDS:
            tomb_stamp = _read_tombstone_stamp()
            tail = f"{tomb_stamp}.{_read_fiscal_year()}"
            if not tomb_stamp.startswith("unread-"):
                _generation_tail = tail
                _generation_read_at = time.time()
        else:
            tail = _generation_tail
    return f"{stamp}.{tail}"


def exact_cache_key(
    query: str,
    locale: str = "en",
    tenant_id: str | None = None,
    *,
    generation: str | None = None,
) -> str:
    """Return a model-free lookup key: casefolded, punctuation-stripped query.

    The similarity tiers below need an embedding, so they return ``None`` the
    moment no dense model is available — which is every deployment that ships
    without torch (Crane Cloud, the HF Space). The cache was therefore inert
    there: `set_model` is only called when the retriever has a dense model, so
    both backends missed on every request and nothing was ever reused.

    A repeated question is the common case for an FAQ assistant, and matching one
    needs no model at all. "What is the VAT rate?" and "what is the vat rate"
    collapse to the same key, so the second asker gets the first asker's answer.

    An answer is only valid for the corpus it was retrieved from, so the key
    carries the index stamp too. Without it a reindex leaves every entry from the
    previous corpus readable for the rest of its TTL: the fix ships, the index is
    rebuilt, and the assistant keeps serving the answer the old index produced —
    which is how a corrected passage can look like it never landed. The stamp
    is the whole :func:`cache_generation`, so a tombstone or a new fiscal year
    retires entries the same way.
    """
    normalised = _EXACT_KEY_WS_RE.sub(" ", _EXACT_KEY_STRIP_RE.sub(" ", (query or "").casefold())).strip()
    prefix = tenant_id or "default"
    gen = generation if generation is not None else cache_generation()
    return f"{prefix}\x1f{gen}\x1f{locale}\x1f{normalised}"


def _record_lookup(backend: str, outcome: str) -> None:
    """One count per lookup: ``hit_exact``, ``hit_semantic``, ``guard_rejected`` or ``miss``.

    ``guard_rejected`` is a lookup whose nearest entry cleared the cosine
    threshold and was refused by :func:`_cache_queries_compatible` — the
    false hits the threshold alone would have served.
    """
    metrics.inc("semantic_cache_lookups_total", labels={"backend": backend, "outcome": outcome})


@dataclass
class CacheEntry:
    query: str
    embedding: np.ndarray
    response: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    hits: int = 0
    tenant_id: str = "default"
    generation: str = ""


class SemanticCache:
    """In-memory semantic cache with cosine similarity matching."""

    def __init__(self, dense_model: Any = None) -> None:
        self._entries: list[CacheEntry] = []
        #: Exact (normalised-query) tier, usable with no embedding model.
        self._exact: dict[str, CacheEntry] = {}
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
        stale = [k for k, e in self._exact.items() if (now - e.created_at) >= CACHE_TTL_SECONDS]
        for key in stale:
            del self._exact[key]
        evicted += len(stale)
        if evicted:
            self._stats["evictions"] += evicted

    def get(self, query: str, locale: str = "en", tenant_id: str | None = None) -> dict[str, Any] | None:
        """Look up a semantically similar cached response.

        Tries an exact (normalised) key first, then semantic similarity. The
        exact tier is what makes the cache work at all without an embedder.

        Returns the cached response dict or None on miss.
        """
        if not CACHE_ENABLED:
            return None

        t_id = tenant_id or "default"
        generation = cache_generation()
        key = exact_cache_key(query, locale, tenant_id=t_id, generation=generation)
        with self._lock:
            self._evict_expired()
            exact = self._exact.get(key)
            if exact is not None and (time.time() - exact.created_at) < CACHE_TTL_SECONDS:
                exact.hits += 1
                self._stats["hits"] += 1
                logger.debug("Cache HIT (exact, query_length=%d)", len(query))
                _record_lookup("memory", "hit_exact")
                return exact.response

        if not self._dense_model:
            with self._lock:
                self._stats["misses"] += 1
            _record_lookup("memory", "miss")
            return None

        with self._lock:
            # Embed inside lock to prevent model swap race with set_model()
            embedding = self._embed(query)
            if embedding is None:
                self._stats["misses"] += 1
                _record_lookup("memory", "miss")
                return None

            self._evict_expired()

            best_sim = 0.0
            best_entry: CacheEntry | None = None
            guard_rejected = False

            for entry in self._entries:
                # Must match locale, tenant and the corpus generation
                if entry.response.get("locale") != locale:
                    continue
                if getattr(entry, "tenant_id", "default") != t_id:
                    continue
                if entry.generation != generation:
                    continue
                sim = self._cosine_sim(embedding, entry.embedding)
                if sim < CACHE_THRESHOLD or sim <= best_sim:
                    continue
                if not _cache_queries_compatible(query, entry.query):
                    guard_rejected = True
                    continue
                best_sim = sim
                best_entry = entry

            if best_entry is not None:
                best_entry.hits += 1
                self._stats["hits"] += 1
                logger.debug(
                    "Cache HIT: sim=%.4f query_length=%d cached_query_length=%d",
                    best_sim,
                    len(query),
                    len(best_entry.query),
                )
                _record_lookup("memory", "hit_semantic")
                return best_entry.response

            self._stats["misses"] += 1
            _record_lookup("memory", "guard_rejected" if guard_rejected else "miss")
            return None

    def put(self, query: str, response: dict[str, Any], tenant_id: str | None = None) -> None:
        """Store a query-response pair in the cache."""
        if not CACHE_ENABLED:
            return

        t_id = tenant_id or response.get("tenant_id") or "default"
        generation = cache_generation()
        # The exact tier is always written, so a deployment with no embedder
        # still accumulates a usable cache.
        key = exact_cache_key(query, response.get("locale") or "en", tenant_id=t_id, generation=generation)
        with self._lock:
            if len(self._exact) >= CACHE_MAX_SIZE:
                oldest = sorted(self._exact.items(), key=lambda kv: kv[1].created_at)
                for stale_key, _entry in oldest[: len(self._exact) - CACHE_MAX_SIZE + 1]:
                    del self._exact[stale_key]
                    self._stats["evictions"] += 1
            self._exact[key] = CacheEntry(
                query=query,
                embedding=_EMPTY_EMBEDDING,
                response=response,
                tenant_id=t_id,
                generation=generation,
            )

        if not self._dense_model:
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
                    tenant_id=t_id,
                    generation=generation,
                )
            )

    @property
    def stats(self) -> dict[str, int]:
        return {**self._stats, "size": len(self._entries) + len(self._exact)}


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

    @staticmethod
    def _exact_redis_key(
        query: str,
        locale: str,
        tenant_id: str = "default",
        *,
        generation: str | None = None,
    ) -> str:
        digest = hashlib.sha256(
            exact_cache_key(query, locale, tenant_id=tenant_id, generation=generation).encode("utf-8")
        ).hexdigest()[:32]
        return f"{CACHE_REDIS_PREFIX}{tenant_id}:exact:{digest}"

    @staticmethod
    def _semantic_scope(tenant_id: str, locale: str, generation: str) -> str:
        """Key namespace of one tenant's similarity entries for one locale and generation.

        The scan used to match ``CACHE_REDIS_PREFIX*``, which includes the
        exact tier's *string* keys. ``HGETALL`` on one of those raises WRONGTYPE,
        the handler logged it at debug and reported a miss — so once the first
        exact entry was written, which every ``put`` does, no similarity lookup
        on Redis ever succeeded. Scoping the scan also stops one lookup reading
        every tenant's, every locale's and every retired generation's entries.
        """
        scope = hashlib.sha256(f"{tenant_id}\x1f{locale}\x1f{generation}".encode()).hexdigest()[:16]
        return f"{CACHE_REDIS_PREFIX}sem:{scope}:"

    def get(self, query: str, locale: str = "en", tenant_id: str | None = None) -> dict[str, Any] | None:
        if not CACHE_ENABLED or self._client is None:
            return None

        t_id = tenant_id or "default"
        generation = cache_generation()
        # Exact tier: a single GET, and the only tier that works without an
        # embedder — which is every deployment shipping without torch.
        try:
            raw = self._client.get(self._exact_redis_key(query, locale, tenant_id=t_id, generation=generation))
            if raw:
                self._stats["hits"] += 1
                logger.debug("Redis cache HIT (exact, query_length=%d)", len(query))
                _record_lookup("redis", "hit_exact")
                return _json.loads(raw.decode("utf-8"))
        except Exception:
            logger.debug("Redis exact cache get failed", exc_info=True)

        if self._dense_model is None:
            self._stats["misses"] += 1
            _record_lookup("redis", "miss")
            return None

        embedding = self._embed(query)
        if embedding is None:
            self._stats["misses"] += 1
            _record_lookup("redis", "miss")
            return None

        try:
            # Linear scan over one tenant/locale/generation scope — fine for the
            # hundreds/low-thousands range typical for a domain chatbot. For
            # larger cache footprints, migrate to RediSearch or a vector index.
            pattern = f"{self._semantic_scope(t_id, locale, generation)}*"
            keys = list(self._client.scan_iter(match=pattern, count=200))
            rows: list[Any] = []
            if keys:
                pipe = self._client.pipeline(transaction=False)
                for key in keys:
                    pipe.hgetall(key)
                rows = pipe.execute(raise_on_error=False)
            best_sim = 0.0
            best_response: dict[str, Any] | None = None
            guard_rejected = False
            for data in rows:
                if not isinstance(data, dict) or not data:
                    continue  # expired between SCAN and HGETALL, or not a hash
                # The scope already selects these; re-checked so a digest
                # collision can never cross a tenant, locale or generation.
                if data.get(b"tenant_id", b"default").decode("utf-8", "ignore") != t_id:
                    continue
                if data.get(b"locale", b"en").decode("utf-8", "ignore") != locale:
                    continue
                if data.get(b"generation", b"").decode("utf-8", "ignore") != generation:
                    continue
                emb_b64 = data.get(b"embedding")
                if not emb_b64:
                    continue
                entry_emb = self._decode_emb(emb_b64)
                if entry_emb.shape != embedding.shape:
                    continue
                sim = float(np.dot(embedding, entry_emb))
                if sim < CACHE_THRESHOLD or sim <= best_sim:
                    continue
                entry_query = data.get(b"query", b"").decode("utf-8", "ignore")
                if not _cache_queries_compatible(query, entry_query):
                    guard_rejected = True
                    continue
                try:
                    candidate = _json.loads(data.get(b"response", b"{}").decode("utf-8", "ignore"))
                except ValueError:
                    logger.debug("Skipping a Redis cache entry with an unreadable response")
                    continue
                if candidate:
                    best_sim = sim
                    best_response = candidate

            if best_response:
                self._stats["hits"] += 1
                _record_lookup("redis", "hit_semantic")
                return best_response
            self._stats["misses"] += 1
            _record_lookup("redis", "guard_rejected" if guard_rejected else "miss")
            return None
        except Exception:
            logger.debug("Redis cache get failed", exc_info=True)
            self._stats["misses"] += 1
            _record_lookup("redis", "miss")
            return None

    def put(self, query: str, response: dict[str, Any], tenant_id: str | None = None) -> None:
        if not CACHE_ENABLED or self._client is None:
            return

        t_id = tenant_id or response.get("tenant_id") or "default"
        locale = response.get("locale") or "en"
        generation = cache_generation()
        try:
            self._client.set(
                self._exact_redis_key(query, locale, tenant_id=t_id, generation=generation),
                _json.dumps(response).encode("utf-8"),
                ex=CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.debug("Redis exact cache put failed", exc_info=True)

        if self._dense_model is None:
            return
        embedding = self._embed(query)
        if embedding is None:
            return
        try:
            # A random suffix: two replicas inserting in the same millisecond
            # must not overwrite each other's entry.
            key = f"{self._semantic_scope(t_id, locale, generation)}{uuid.uuid4().hex}"
            pipe = self._client.pipeline(transaction=True)
            pipe.hset(
                key,
                mapping={
                    "query": query.encode("utf-8"),
                    "locale": locale.encode("utf-8"),
                    "tenant_id": t_id.encode("utf-8"),
                    "generation": generation.encode("utf-8"),
                    "embedding": self._encode_emb(embedding),
                    "response": _json.dumps(response).encode("utf-8"),
                },
            )
            pipe.expire(key, CACHE_TTL_SECONDS)
            pipe.execute()
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
