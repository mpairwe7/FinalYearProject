"""Qdrant-backed hybrid retrieval with dense + BM25 sparse + cross-encoder reranking.

Implements the non-parametric retrieval component of the RAG architecture:
- Dense: BAAI/bge-m3 (1024-dim, multilingual, MTEB 63.0) — default
- Sparse: BM25-weighted token vectors (inverted index)
- Fusion: Reciprocal Rank Fusion (RRF) via Qdrant query API
- Reranking: mxbai-rerank-base-v2 (BEIR 55.6, 500M, Apache-2.0)
- Grounding: passage-level faithfulness scoring

Model swap guide: see docs/MODEL_SWAP_GUIDE.md for tested alternatives.

References:
- Lewis et al. "Retrieval-Augmented Generation" (nlp.cs.ucl.ac.uk)
- Qdrant hybrid search: prefetch + RRF fusion
- RAGAS faithfulness metric (docs.ragas.io)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .resilience import CircuitBreaker, CircuitState  # re-export for backcompat

logger = logging.getLogger(__name__)

__all__ = ["CircuitBreaker", "CircuitState", "BM25SparseEncoder", "HybridRetriever"]

# ---------------------------------------------------------------------------
# Configuration via environment
# ---------------------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "") or None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "ura_knowledge_base")
QDRANT_ENABLED = os.getenv("QDRANT_ENABLED", "true").lower() not in ("0", "false", "no", "off")
# When Qdrant is unavailable (e.g. CPU-only Crane Cloud), restore dense
# retrieval via Cloudflare Workers AI bge-m3 + Vectorize instead of degrading
# to keyword-only.  "" disables; "workers_ai" enables the cloud dense fallback.
DENSE_FALLBACK_BACKEND = os.getenv("DENSE_FALLBACK_BACKEND", "").strip().lower()
# 2026 default embedding: BAAI/bge-m3 — multilingual (100+ langs incl.
# Bantu-family languages relevant to Luganda), 1024-dim, current MTEB
# state-of-art for free models.  Set DENSE_MODEL=sentence-transformers/
# all-MiniLM-L6-v2 to keep the 384-dim legacy index without re-indexing.
DENSE_MODEL_NAME = os.getenv("DENSE_MODEL", "BAAI/bge-m3")
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL", "mixedbread-ai/mxbai-rerank-base-v2")
DENSE_DIM = int(os.getenv("DENSE_DIM", "1024"))
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() == "true"
RETRIEVER_DENSE_DEVICE = os.getenv("RETRIEVER_DENSE_DEVICE", "cpu")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", RETRIEVER_DENSE_DEVICE)
from ._root import PROJECT_ROOT as _PROJECT_ROOT
BM25_STATE_PATH = Path(
    os.getenv("BM25_STATE_PATH", str(_PROJECT_ROOT / "Model" / "bm25_state.json"))
)

# Fixed namespace so re-indexing identical content yields identical point ids
# (idempotent upserts) and a stable per-collection binding sentinel.
_POINT_ID_NAMESPACE = uuid.UUID("a3f1c2b4-1e2d-4f5a-8b6c-9d0e1f2a3b4c")


def compute_corpus_hash(texts: list[str]) -> str:
    """Order-sensitive content hash of the corpus used to fit BM25.

    The BM25 token ids are assigned by first-seen order, so the sparse vectors
    stored in Qdrant are only consistent with a ``bm25_state.json`` produced by
    the *same* fit.  Stamping both artifacts with this hash lets the retriever
    detect a stale state file paired with a freshly-rebuilt collection instead
    of silently querying a desynced inverted index (P1-6).
    """
    h = hashlib.sha256()
    for t in texts:
        chunk = t or ""
        h.update(str(len(chunk)).encode())
        h.update(b"\x00")
        h.update(chunk.encode("utf-8", "replace"))
        h.update(b"\x01")
    return h.hexdigest()


def deterministic_point_id(doc: dict[str, Any]) -> str:
    """Content-derived, stable Qdrant point id for idempotent reindexing.

    Re-indexing the same chunk produces the same id, so a non-``--recreate``
    rebuild overwrites rather than appending a duplicate row.
    """
    text = doc.get("text") or doc.get("answer") or ""
    key = (
        "::".join(str(doc.get(k, "")) for k in ("source", "page", "section", "chunk_id"))
        + "::"
        + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    )
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, key))


def bm25_binding_sentinel_id(collection: str) -> str:
    """Deterministic id of the per-collection sentinel point holding the corpus
    hash, used to verify the loaded bm25_state matches the live vectors."""
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, f"{collection}::__bm25_binding__"))


def normalize_rerank_score(logit: float) -> float:
    """Squash an unbounded cross-encoder rerank logit to a [0,1] relevance (P1-5).

    Abstention/corrective thresholds previously compared raw reranker logits
    (unbounded, often negative) against the same numbers as RRF scores
    (~1/(k+rank) ≈ 0.016) — incomparable scales. A logistic squash gives one
    calibrated scale to threshold against.
    """
    x = max(-30.0, min(30.0, float(logit)))
    return 1.0 / (1.0 + math.exp(-x))


def hit_relevance(hit: dict[str, Any]) -> float | None:
    """Best-effort calibrated [0,1] relevance for a hit, or ``None`` (P1-5).

    Prefers the normalized reranker score (``score_norm``), then squashes a raw
    ``score_rerank``. Returns ``None`` when only an RRF score is available
    (reranker absent/disabled): RRF magnitudes are not comparable to the
    reranker scale, so callers treat ``None`` as a *degraded* signal and avoid
    score-based gating instead of abstaining on an incomparable number.
    """
    norm = hit.get("score_norm")
    if norm is not None:
        try:
            return float(norm)
        except (TypeError, ValueError):
            return None
    rr = hit.get("score_rerank")
    if rr is not None:
        try:
            return normalize_rerank_score(float(rr))
        except (TypeError, ValueError):
            return None
    return None


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
        self._corpus_hash: str = ""

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def fit(self, documents: list[str]) -> BM25SparseEncoder:
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

        self._corpus_hash = compute_corpus_hash(documents)
        logger.info(
            "BM25 encoder fit: vocab=%d docs=%d corpus=%s",
            len(self._vocab),
            n_docs,
            self._corpus_hash[:12],
        )
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

    @property
    def corpus_hash(self) -> str:
        return self._corpus_hash

    # -- Serialisation -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "vocab": self._vocab,
            "idf": {str(k): v for k, v in self._idf.items()},
            "avg_dl": self._avg_dl,
            "next_id": self._next_id,
            "corpus_hash": self._corpus_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BM25SparseEncoder:
        enc = cls()
        enc._vocab = data["vocab"]
        enc._idf = {int(k): v for k, v in data["idf"].items()}
        enc._avg_dl = data.get("avg_dl", 0.0)
        enc._next_id = data.get("next_id", 0)
        enc._corpus_hash = data.get("corpus_hash", "")
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
        # Disabled at init time if the loaded bm25_state is out of sync with
        # the live Qdrant vectors (P1-6) — search then runs dense-only.
        self._sparse_ok = True
        # Set when Qdrant is off but the Workers AI + Vectorize dense fallback
        # is configured — search() then routes to _search_vectorize().
        self._vectorize_mode = False
        self._circuit = CircuitBreaker(
            name="qdrant",
            failure_threshold=3,
            reset_timeout=10.0,
            max_timeout=300.0,
        )

    def initialize(self) -> bool:
        """Connect to Qdrant and load models.  Returns ``True`` if ready."""
        if not QDRANT_ENABLED:
            if self._init_vectorize_mode():
                return True
            logger.info("HybridRetriever disabled by QDRANT_ENABLED=false; keyword fallback active")
            return False
        try:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10)
            collections = [c.name for c in self._client.get_collections().collections]
            if QDRANT_COLLECTION not in collections:
                logger.warning("Qdrant collection '%s' not found", QDRANT_COLLECTION)
                return False

            # Load BM25 state persisted by the indexer
            if BM25_STATE_PATH.exists():
                with open(BM25_STATE_PATH) as f:
                    self._sparse_encoder = BM25SparseEncoder.from_dict(json.load(f))
                logger.info("Loaded BM25 state from %s", BM25_STATE_PATH)
            self._verify_bm25_binding()

            from sentence_transformers import CrossEncoder, SentenceTransformer

            try:
                self._dense_model = SentenceTransformer(DENSE_MODEL_NAME, device=RETRIEVER_DENSE_DEVICE)
            except Exception:
                if RETRIEVER_DENSE_DEVICE == "cpu":
                    raise
                logger.warning(
                    "Dense retriever device %s unavailable; falling back to CPU",
                    RETRIEVER_DENSE_DEVICE,
                    exc_info=True,
                )
                self._dense_model = SentenceTransformer(DENSE_MODEL_NAME, device="cpu")
            if RERANK_ENABLED:
                try:
                    self._reranker = CrossEncoder(RERANKER_MODEL_NAME, device=RERANKER_DEVICE)
                except Exception:
                    if RERANKER_DEVICE == "cpu":
                        raise
                    logger.warning(
                        "Reranker device %s unavailable; falling back to CPU",
                        RERANKER_DEVICE,
                        exc_info=True,
                    )
                    self._reranker = CrossEncoder(RERANKER_MODEL_NAME, device="cpu")

            self._ready = True
            logger.info(
                "HybridRetriever ready (url=%s collection=%s dense_device=%s rerank=%s reranker_device=%s)",
                QDRANT_URL,
                QDRANT_COLLECTION,
                RETRIEVER_DENSE_DEVICE,
                RERANK_ENABLED,
                RERANKER_DEVICE if RERANK_ENABLED else "disabled",
            )
            return True
        except Exception:
            logger.warning("HybridRetriever init failed; keyword fallback active", exc_info=True)
            self._ready = False
            return False

    def _init_vectorize_mode(self) -> bool:
        """Restore dense retrieval via Workers AI bge-m3 + Vectorize when Qdrant
        is off (no GPU/torch needed). Returns True if the fallback is active."""
        if DENSE_FALLBACK_BACKEND != "workers_ai":
            return False
        try:
            from .providers import config as _cfg

            if not _cfg.is_vectorize_configured():
                logger.info(
                    "DENSE_FALLBACK_BACKEND=workers_ai but Vectorize/Cloudflare not configured"
                )
                return False
            self._vectorize_mode = True
            self._ready = True
            logger.info(
                "HybridRetriever ready in Vectorize fallback mode "
                "(Cloudflare Workers AI bge-m3 + Vectorize, no GPU)"
            )
            return True
        except Exception:
            logger.warning("Vectorize fallback init failed", exc_info=True)
            return False

    def _search_vectorize(
        self,
        query: str,
        top_k: int,
        prefetch_limit: int,
        filters: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Dense retrieval via Workers AI bge-m3 -> Vectorize, fused client-side
        with a lexical (BM25-lite) re-score via RRF.  CPU-only hybrid."""
        from .providers import breakers, budget
        from .providers import gateway as _gw
        from .providers import routing
        from .providers import vectorize as _vz

        if not breakers.VECTORIZE_BREAKER.allow_request():
            logger.warning("Vectorize circuit OPEN — skipping dense fallback")
            return []
        if not budget.try_consume_neurons(1):
            logger.info("Workers AI neuron budget exhausted — skipping dense fallback")
            return []
        try:
            dense_vec = _gw.workers_ai_embed([query])[0]
            vfilter = None
            if filters:
                eqs = {k: {"$eq": v} for k, v in filters.items() if not isinstance(v, list)}
                vfilter = eqs or None
            hits = _vz.vectorize_query(dense_vec, top_k=prefetch_limit, vector_filter=vfilter)
            breakers.VECTORIZE_BREAKER.record_success()
            routing.log_model_use("embed", "bge-m3")  # index is bge-m3 space; keyword is the resilience fallback
        except Exception:
            breakers.VECTORIZE_BREAKER.record_failure()
            logger.exception("Vectorize dense fallback failed")
            return []

        if not hits:
            return []

        # Client-side RRF: dense rank (Vectorize order) + lexical rank (query-term
        # overlap), restoring a hybrid signal without Qdrant/torch.
        q_terms = set(re.findall(r"\w+", query.lower()))

        def _lexical(text: str) -> float:
            toks = re.findall(r"\w+", (text or "").lower())
            if not toks:
                return 0.0
            return sum(1 for t in toks if t in q_terms) / math.sqrt(len(toks))

        lex_order = sorted(
            range(len(hits)), key=lambda i: _lexical(hits[i].get("text", "")), reverse=True
        )
        k = 60
        rrf = [0.0] * len(hits)
        for dense_rank in range(len(hits)):  # Vectorize returns best-first
            rrf[dense_rank] += 1.0 / (k + dense_rank)
        for lex_rank, i in enumerate(lex_order):
            rrf[i] += 1.0 / (k + lex_rank)

        order = sorted(range(len(hits)), key=lambda i: rrf[i], reverse=True)
        candidates = [
            {
                "id": str(hits[i].get("id", "")),
                "text": hits[i].get("text", ""),
                "question": "",
                "answer": "",
                "source": hits[i].get("source", ""),
                "chunk_id": str(hits[i].get("id", "")),
                "page": hits[i].get("page", ""),
                "section": hits[i].get("section", ""),
                "doc_type": "",
                "score_rrf": float(rrf[i]),
            }
            for i in order
        ]
        self._ready = True
        return candidates[:top_k]

    def _verify_bm25_binding(self) -> None:
        """Disable sparse retrieval if the loaded bm25_state's corpus hash does
        not match the one stamped into Qdrant at index time (P1-6).

        A mismatch means the inverted index and the BM25 vocab/idf came from
        different index runs, so the sparse half would return garbage.  A
        missing sentinel (pre-P1-6 collection) is treated as "can't verify" and
        leaves sparse enabled for backward compatibility.
        """
        local = self._sparse_encoder.corpus_hash
        if not local:
            return  # old state file without a hash — nothing to compare
        try:
            points = self._client.retrieve(
                collection_name=QDRANT_COLLECTION,
                ids=[bm25_binding_sentinel_id(QDRANT_COLLECTION)],
                with_payload=True,
                with_vectors=False,
            )
            remote = str((points[0].payload or {}).get("corpus_hash", "")) if points else ""
            if not remote:
                logger.warning(
                    "BM25 binding sentinel missing in Qdrant; cannot verify "
                    "sparse/state consistency — reindex to write it."
                )
                return
            if remote != local:
                self._sparse_ok = False
                logger.error(
                    "BM25 state/Qdrant corpus hash MISMATCH (state=%s qdrant=%s) — "
                    "disabling sparse retrieval to avoid desynced results; reindex.",
                    local[:12],
                    remote[:12],
                )
        except Exception:
            logger.warning("BM25 binding verification failed; leaving sparse enabled", exc_info=True)

    @property
    def is_ready(self) -> bool:
        """Check if retriever was initialised. Does NOT do a live call —
        the circuit breaker in ``search()`` handles transient failures."""
        return self._ready and (self._client is not None or self._vectorize_mode)

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
        if self._vectorize_mode:
            return self._search_vectorize(query, top_k, prefetch_limit, filters)

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
            if sparse_idx and self._sparse_ok:
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
                if p.get("_meta") == "bm25_binding":
                    continue  # internal corpus-hash sentinel, not a document
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
                    candidates[i]["score_norm"] = normalize_rerank_score(float(s))
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
