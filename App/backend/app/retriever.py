"""Qdrant-backed hybrid retrieval with dense + BM25 sparse + cross-encoder reranking.

Implements the non-parametric retrieval component of the RAG architecture:
- Dense: sentence-transformers/all-MiniLM-L6-v2 (384-dim, HNSW)
- Sparse: BM25-weighted token vectors (inverted index)
- Fusion: Reciprocal Rank Fusion (RRF) via Qdrant query API
- Reranking: cross-encoder/ms-marco-MiniLM-L-6-v2
- Grounding: passage-level faithfulness scoring

References:
- Lewis et al. "Retrieval-Augmented Generation" (nlp.cs.ucl.ac.uk)
- Qdrant hybrid search: prefetch + RRF fusion
- RAGAS faithfulness metric (docs.ragas.io)
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit breaker (Phase 1 – production resilience)
# ---------------------------------------------------------------------------
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker with exponential back-off for Qdrant.

    - CLOSED: requests flow normally; consecutive failures tracked.
    - OPEN: requests rejected immediately; waits *reset_timeout* (doubles each trip).
    - HALF_OPEN: one test request allowed; success → CLOSED, failure → OPEN.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        reset_timeout: float = 10.0,
        max_timeout: float = 300.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self._base_timeout = reset_timeout
        self._max_timeout = max_timeout
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._current_timeout = reset_timeout
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self._current_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker → HALF_OPEN (testing)")
            return self._state

    def allow_request(self) -> bool:
        """Return True if the request should proceed."""
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """Call after a successful operation."""
        with self._lock:
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                logger.info("Circuit breaker → CLOSED (recovered)")
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._current_timeout = self._base_timeout

    def record_failure(self) -> None:
        """Call after a failed operation."""
        with self._lock:
            self._failures += 1
            was_half_open = self._state == CircuitState.HALF_OPEN
            if self._failures >= self.failure_threshold or was_half_open:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                # Double backoff only from HALF_OPEN→OPEN (not first trip)
                if was_half_open:
                    self._current_timeout = min(
                        self._current_timeout * 2, self._max_timeout
                    )
                logger.warning(
                    "Circuit breaker → OPEN (failures=%d, backoff=%.0fs)",
                    self._failures,
                    self._current_timeout,
                )

# ---------------------------------------------------------------------------
# Configuration via environment
# ---------------------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "ura_knowledge_base")
DENSE_MODEL_NAME = os.getenv("DENSE_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
DENSE_DIM = int(os.getenv("DENSE_DIM", "384"))
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() == "true"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
BM25_STATE_PATH = Path(
    os.getenv("BM25_STATE_PATH", str(_PROJECT_ROOT / "Model" / "bm25_state.json"))
)


# ---------------------------------------------------------------------------
# BM25 sparse encoder
# ---------------------------------------------------------------------------
class BM25SparseEncoder:
    """Compute BM25-weighted sparse vectors for Qdrant's inverted index.

    Vocabulary and IDF are built from the indexed corpus via ``fit()``,
    then serialised to JSON for the retriever to load at query time.
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._idf: dict[int, float] = {}
        self._next_id: int = 0
        self._k1: float = 1.2
        self._b: float = 0.75
        self._avg_dl: float = 0.0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def fit(self, documents: list[str]) -> "BM25SparseEncoder":
        """Build vocabulary and IDF weights from *documents*."""
        n_docs = len(documents)
        doc_freq: Counter[int] = Counter()
        total_len = 0

        for doc in documents:
            tokens = self._tokenize(doc)
            total_len += len(tokens)
            seen: set[int] = set()
            for tok in tokens:
                if tok not in self._vocab:
                    self._vocab[tok] = self._next_id
                    self._next_id += 1
                tid = self._vocab[tok]
                if tid not in seen:
                    doc_freq[tid] += 1
                    seen.add(tid)

        self._avg_dl = total_len / max(n_docs, 1)
        for tid, df in doc_freq.items():
            self._idf[tid] = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)

        logger.info("BM25 encoder fit: vocab=%d docs=%d", len(self._vocab), n_docs)
        return self

    def encode(self, text: str) -> tuple[list[int], list[float]]:
        """Return ``(indices, values)`` for a Qdrant ``SparseVector``."""
        tokens = self._tokenize(text)
        tf: Counter[str] = Counter(tokens)
        dl = len(tokens)

        indices: list[int] = []
        values: list[float] = []

        for tok, count in tf.items():
            tid = self._vocab.get(tok)
            if tid is None:
                continue
            idf = self._idf.get(tid, 0.0)
            num = count * (self._k1 + 1)
            denom = count + self._k1 * (1 - self._b + self._b * dl / max(self._avg_dl, 1))
            score = idf * num / denom
            if score > 0:
                indices.append(tid)
                values.append(round(score, 6))

        return indices, values

    # -- Serialisation -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "vocab": self._vocab,
            "idf": {str(k): v for k, v in self._idf.items()},
            "avg_dl": self._avg_dl,
            "next_id": self._next_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BM25SparseEncoder":
        enc = cls()
        enc._vocab = data["vocab"]
        enc._idf = {int(k): v for k, v in data["idf"].items()}
        enc._avg_dl = data.get("avg_dl", 0.0)
        enc._next_id = data.get("next_id", 0)
        return enc


# ---------------------------------------------------------------------------
# Hybrid retriever
# ---------------------------------------------------------------------------
class HybridRetriever:
    """Production hybrid retriever: dense + sparse RRF + cross-encoder rerank.

    Gracefully degrades to unavailable when Qdrant is not reachable or
    the collection does not exist.  Callers check ``is_ready`` first.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._dense_model: Any = None
        self._reranker: Any = None
        self._sparse_encoder = BM25SparseEncoder()
        self._ready = False
        self._circuit = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=10.0,
            max_timeout=300.0,
        )

    def initialize(self) -> bool:
        """Connect to Qdrant and load models.  Returns ``True`` if ready."""
        try:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=QDRANT_URL, timeout=10)
            collections = [c.name for c in self._client.get_collections().collections]
            if QDRANT_COLLECTION not in collections:
                logger.warning("Qdrant collection '%s' not found", QDRANT_COLLECTION)
                return False

            # Load BM25 state persisted by the indexer
            if BM25_STATE_PATH.exists():
                with open(BM25_STATE_PATH) as f:
                    self._sparse_encoder = BM25SparseEncoder.from_dict(json.load(f))
                logger.info("Loaded BM25 state from %s", BM25_STATE_PATH)

            from sentence_transformers import SentenceTransformer, CrossEncoder

            self._dense_model = SentenceTransformer(DENSE_MODEL_NAME)
            if RERANK_ENABLED:
                self._reranker = CrossEncoder(RERANKER_MODEL_NAME)

            self._ready = True
            logger.info(
                "HybridRetriever ready (url=%s collection=%s rerank=%s)",
                QDRANT_URL,
                QDRANT_COLLECTION,
                RERANK_ENABLED,
            )
            return True
        except Exception:
            logger.warning("HybridRetriever init failed; keyword fallback active", exc_info=True)
            self._ready = False
            return False

    @property
    def is_ready(self) -> bool:
        """Live health check — verifies Qdrant is actually reachable."""
        if not self._ready or self._client is None:
            return False
        try:
            self._client.get_collections()
            return True
        except Exception:
            logger.warning("Qdrant health check failed; marking retriever as unavailable")
            self._ready = False
            return False

    def search(
        self,
        query: str,
        top_k: int = 4,
        prefetch_limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search with RRF fusion + optional cross-encoder rerank.

        *filters* accepts Qdrant payload filter keys, e.g.
        ``{"doc_type": "pdf", "tag": "vat"}``.
        """
        if not self._ready or self._client is None or self._dense_model is None:
            return []

        # Circuit breaker gate — reject immediately when OPEN
        if not self._circuit.allow_request():
            logger.warning("Circuit breaker OPEN — skipping Qdrant search")
            return []

        try:
            from qdrant_client import models

            dense_vec = self._dense_model.encode(query).tolist()
            sparse_idx, sparse_val = self._sparse_encoder.encode(query)

            # Build optional payload filter
            query_filter = None
            if filters:
                must_conditions = []
                for field, value in filters.items():
                    if isinstance(value, list):
                        must_conditions.append(
                            models.FieldCondition(key=field, match=models.MatchAny(any=value))
                        )
                    else:
                        must_conditions.append(
                            models.FieldCondition(key=field, match=models.MatchValue(value=value))
                        )
                if must_conditions:
                    query_filter = models.Filter(must=must_conditions)

            prefetch = [
                models.Prefetch(query=dense_vec, using="dense", limit=prefetch_limit),
            ]
            if sparse_idx:
                prefetch.append(
                    models.Prefetch(
                        query=models.SparseVector(indices=sparse_idx, values=sparse_val),
                        using="sparse",
                        limit=prefetch_limit,
                    )
                )

            results = self._client.query_points(
                collection_name=QDRANT_COLLECTION,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=query_filter,
                limit=prefetch_limit,
            )

            if not results.points:
                self._circuit.record_success()
                return []

            candidates: list[dict[str, Any]] = []
            for pt in results.points:
                p = pt.payload or {}
                candidates.append(
                    {
                        "id": str(pt.id),
                        "text": p.get("text", ""),
                        "question": p.get("question", ""),
                        "answer": p.get("answer", ""),
                        "source": p.get("source", ""),
                        "chunk_id": p.get("chunk_id", ""),
                        "page": p.get("page", ""),
                        "section": p.get("section", ""),
                        "doc_type": p.get("doc_type", ""),
                        "score_rrf": float(pt.score) if pt.score else 0.0,
                    }
                )

            # Cross-encoder reranking
            if self._reranker and candidates:
                pairs = [
                    (query, c.get("text") or c.get("answer") or c.get("question", ""))
                    for c in candidates
                ]
                scores = self._reranker.predict(pairs)
                for i, s in enumerate(scores):
                    candidates[i]["score_rerank"] = float(s)
                candidates.sort(key=lambda x: x.get("score_rerank", 0.0), reverse=True)

            self._circuit.record_success()
            self._ready = True  # ensure readiness restored on success
            return candidates[:top_k]

        except Exception:
            self._circuit.record_failure()
            logger.exception("Hybrid search failed; circuit breaker tracking failure")
            # Do NOT permanently disable _ready — the circuit breaker
            # controls availability via allow_request(). Setting _ready=False
            # here would prevent auto-recovery when Qdrant comes back.
            return []

    # -- Grounding helpers ---------------------------------------------------
    @staticmethod
    def compute_faithfulness(answer: str, contexts: list[str]) -> float:
        """Fraction of answer sentences whose tokens are >=50 % covered by contexts.

        Lightweight runtime proxy for the RAGAS faithfulness metric.
        """
        if not answer or not contexts:
            return 0.0

        sentences = [s.strip() for s in re.split(r"[.!?]+", answer) if len(s.strip()) > 5]
        if not sentences:
            return 1.0

        ctx_tokens = set(re.findall(r"\w+", " ".join(contexts).lower()))
        grounded = 0
        for sent in sentences:
            sent_tokens = set(re.findall(r"\w+", sent.lower()))
            if not sent_tokens:
                continue
            if len(sent_tokens & ctx_tokens) / len(sent_tokens) >= 0.5:
                grounded += 1

        return round(grounded / len(sentences), 4)

    @staticmethod
    def build_citations(hits: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Build passage-level citations with section spans from retrieval hits."""
        citations: list[dict[str, str]] = []
        for i, hit in enumerate(hits, 1):
            cit: dict[str, str] = {"ref": f"[{i}]", "source": hit.get("source", "unknown")}
            if hit.get("page"):
                cit["page"] = str(hit["page"])
            if hit.get("section"):
                cit["section"] = str(hit["section"])
            passage = hit.get("text") or hit.get("answer") or ""
            cit["passage"] = passage[:500]
            citations.append(cit)
        return citations
