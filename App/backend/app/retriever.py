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

import base64
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
import zlib
from collections import Counter, OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any

from ._root import APP_DATA_ROOT as _APP_DATA_ROOT
from .analytics import metrics
from .resilience import CircuitBreaker, CircuitState  # re-export for backcompat
from .text_signals import content_tokens, is_courtesy_sentence, split_sentences

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
# to keyword-only.
#
# Unset (the default) means *auto*: the fallback engages whenever Cloudflare and
# a Vectorize index are actually configured. It previously required an explicit
# opt-in, so a deployment holding valid Vectorize credentials still collapsed to
# keyword search the moment Qdrant went away — the opposite of the intent.
# "workers_ai" forces it on; "none"/"off"/"disabled" turns it off.
DENSE_FALLBACK_BACKEND = os.getenv("DENSE_FALLBACK_BACKEND", "").strip().lower()
_DENSE_FALLBACK_DISABLED = {"none", "off", "disabled", "false", "0"}
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
BM25_STATE_PATH = Path(
    os.getenv("BM25_STATE_PATH", str(_APP_DATA_ROOT.parent / "Model" / "bm25_state.json"))
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


#: Recent query embeddings kept for reuse within and across turns.
_QUERY_CACHE_SIZE = int(os.getenv("RETRIEVER_QUERY_CACHE_SIZE", "256"))
#: Characters of a passage handed to the cross-encoder.  Its own input
#: window is shorter than this; the cap bounds the quadratic term.
_RERANK_CHARS = int(os.getenv("RETRIEVER_RERANK_CHARS", "1200"))
#: Jaccard overlap above which two candidates are treated as duplicates.
_DEDUPE_THRESHOLD = float(os.getenv("RETRIEVER_DEDUPE_THRESHOLD", "0.9"))
#: RRF constant shared by Qdrant, the Vectorize client, and the graph leg.
RRF_K = int(os.getenv("RRF_K", "60"))


def _shingles(text: str) -> frozenset[str]:
    """Word 5-grams, the usual near-duplicate signature."""
    words = re.findall(r"\w+", (text or "").lower())
    if len(words) < 5:
        return frozenset(words)
    return frozenset(" ".join(words[i : i + 5]) for i in range(len(words) - 4))


_FY_RANK_RE = re.compile(r"FY(\d{4})-\d{2}")


def canonical_source_url(source: str, existing: str = "") -> str:
    """HTTPS URL for a citation: stored URL, else the URA portal for URA files (G19)."""
    raw = (existing or "").strip()
    if raw.startswith(("https://", "http://")):
        return raw
    name = (source or "").strip().lower()
    if name.startswith("ura") and name.endswith((".csv", ".pdf", ".jsonl", ".json")):
        return "https://ura.go.ug"
    return ""


def _provenance_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Canonical URL + effective date copied from the index payload (G19)."""
    url = canonical_source_url(
        str(payload.get("source") or ""),
        str(payload.get("url") or payload.get("source_url") or ""),
    )
    effective = str(
        payload.get("effective_from")
        or payload.get("effective_date")
        or payload.get("fiscal_year")
        or payload.get("crawled_at")
        or ""
    ).strip()
    return {
        "url": url,
        "title": str(payload.get("title") or "").strip(),
        "effective_date": effective,
        "crawled_at": str(payload.get("crawled_at") or "").strip(),
    }


def fiscal_year_rank(value: object) -> int | None:
    """Return a comparable ordinal for a ``FY2024-25`` label, else ``None``.

    ``None`` means *unknown*, never *old*: most URA filenames carry no fiscal
    year, so callers must not treat a missing label as superseded.
    """
    match = _FY_RANK_RE.fullmatch(str(value or "").strip())
    return int(match.group(1)) if match else None


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop near-identical passages, keeping the best copy.

    The corpus repeats the same guidance across document editions, so a
    prefetch commonly returns several copies of one passage.  Keeping
    them wastes reranker compute and, worse, lets one fact occupy most of
    ``top_k`` — the model then sees three restatements instead of three
    pieces of evidence.

    "Best" is normally the best-ranked copy, with one override: when two
    equivalent passages carry *different known* fiscal years, the newer edition
    wins regardless of rank.  Retrieval scores cannot distinguish a repealed
    rate from a current one — the FY2023-24 and FY2025-26 phrasings of a rate
    table are near-identical text — so without this the corpus's oldest edition
    can silently evict the one in force.  An unknown fiscal year never displaces
    a known one and is never displaced by it, because unknown is not old.
    """
    kept: list[dict[str, Any]] = []
    # (signature, index into ``kept``) so a duplicate can replace what it matched.
    signatures: list[tuple[frozenset[str], int]] = []
    for candidate in candidates:
        text = candidate.get("text") or candidate.get("answer") or candidate.get("question", "")
        signature = _shingles(text)
        if not signature:
            kept.append(candidate)
            continue
        duplicate_of: int | None = None
        for seen, kept_index in signatures:
            overlap = len(signature & seen)
            if overlap and overlap / min(len(signature), len(seen)) >= _DEDUPE_THRESHOLD:
                duplicate_of = kept_index
                break
        if duplicate_of is None:
            kept.append(candidate)
            signatures.append((signature, len(kept) - 1))
            continue
        incoming = fiscal_year_rank(candidate.get("fiscal_year"))
        existing = fiscal_year_rank(kept[duplicate_of].get("fiscal_year"))
        if incoming is not None and existing is not None and incoming > existing:
            kept[duplicate_of] = candidate
    return kept


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


#: Calibrated relevance below which a passage is not worth showing the
#: model.  Deliberately under ``ABSTENTION_THRESHOLD_NORM`` (0.30): this
#: prunes the tail of an answerable result set, it does not decide
#: whether to answer — that stays :meth:`OutputGuard.should_abstain`'s job.
_CONTEXT_FLOOR = float(os.getenv("RETRIEVER_CONTEXT_FLOOR", "0.20"))

#: Also drop anything this far below the best hit.  A result set like
#: [0.85, 0.04, 0.03] has an obvious cliff; the absolute floor alone
#: would keep a 0.25 hit sitting under a 0.95 one, which is noise in
#: context that reads as corroboration.
_CONTEXT_RELATIVE_DROP = float(os.getenv("RETRIEVER_CONTEXT_RELATIVE_DROP", "0.45"))

#: Content words carry the subject of a question; these do not.  Shared with the
#: same intent as ``cache._CACHE_QUERY_STOPWORDS`` — a query and a passage that
#: agree only on "what is the" are not about the same thing.
_LEXICAL_STOPWORDS = frozenset(
    "a an the is are was were be been being am do does did will would shall should "
    "can could may might must have has had of in on at to for with by from as and "
    "or not no but so if then than that this these those it its i me my we our you "
    "your what which who whom whose how when where why about into over under "
    "please tell know like want need explain describe give show me".split()
)
_LEXICAL_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Minimum share of a question's *information* (IDF-weighted content words) that
#: must appear in a passage for it to count as relevant when no reranker score
#: exists.
#:
#: Calibrated against the live sidecar corpus, not guessed. Four variants were
#: measured over 8 off-domain questions and 130 on-domain ones (120 verbatim FAQ
#: questions plus 10 paraphrases); the last three are recorded so nobody repeats
#: them:
#:
#:   variant                        on-domain min   off-domain max
#:   plain term recall                    0.667           0.667   no separation
#:   IDF-weighted, all fields  <- used    0.556           0.664
#:   IDF-weighted, minus filename         0.556           0.664   no change
#:   IDF-weighted, body text only         0.493           0.664   worse
#:
#: Plain recall fails because a tax corpus is full of "bank" and "account", so
#: "How do I hack into a bank account?" scores 0.667. IDF weighting fixes that
#: class. 0.50 leaves margin under the on-domain minimum and rejects 7 of 8
#: off-domain.
#:
#: The residual is "What is the capital of France?" at 0.664: both content terms
#: genuinely occur in the corpus (an EAC tax-cases compendium), so no lexical
#: variant separates it — which is what ``service._FAQ_MATCH_MIN``'s comment
#: already concluded from its own measurements. Closing it needs a signal that
#: separates: the cross-encoder where it runs, or a judge over the candidates.
LEXICAL_RELEVANCE_FLOOR = float(os.getenv("RETRIEVER_LEXICAL_FLOOR", "0.50"))


def active_retrieval_mode(retriever: object | None, *, ready: bool) -> str:
    """The retrieval mode actually in force, not the one that was configured.

    ``/ready`` resolved this correctly while every chat response reported a
    flat ``"hybrid"`` whenever the search returned anything.  On a CPU-only
    image — Crane Cloud, the HF Space — ``__init__`` drops the embedder and
    the reranker and serves BM25 alone, so "hybrid" was the one field a
    reader would have used to notice, and it said everything was fine.

    Ordering matches ``/ready``: Vectorize is checked before ``_sparse_only``
    because a Vectorize deployment keeps its sparse-only Qdrant state as a
    fallback and would otherwise under-report as ``"sparse"``.
    """
    if not ready or retriever is None:
        return "keyword"
    if getattr(retriever, "_vectorize_mode", False):
        return "vector"
    if getattr(retriever, "_sparse_only", False):
        return "sparse"
    if not getattr(retriever, "_sparse_ok", True):
        # BM25 disabled at runtime — an invalid state file, or a corpus-hash
        # mismatch against Qdrant's sentinel, which would make the sparse leg
        # return results from a different index run. Dense still serves, so
        # this is neither "hybrid" nor "sparse".
        #
        # Deliberately not folded into "vector": that means Vectorize, which is
        # dense-only with a client-side lexical re-score against a remote index.
        # This is local Qdrant dense with the cross-encoder still in play, and
        # an operator reading the field should be able to tell a desynced BM25
        # from an egress fallback.
        return "dense"
    return "hybrid"



def _lexical_terms(text: str) -> set[str]:
    return {
        token
        for token in _LEXICAL_TOKEN_RE.findall((text or "").lower())
        if token not in _LEXICAL_STOPWORDS and len(token) > 2
    }


def lexical_relevance(
    query: str, hit: dict[str, Any], encoder: BM25SparseEncoder | None = None
) -> float:
    """Share of the query's *information* present in *hit*, in ``[0, 1]``.

    With an *encoder*, terms are weighted by IDF, which is what makes the score
    discriminating; without one it degrades to plain term recall.

    A cheap stand-in for a relevance score when nothing better exists. It is not
    a ranking signal — BM25 already ranked these — it answers a narrower
    question: *is this passage about what was asked at all?*

    This exists because the sparse-only sidecar has no cross-encoder, so every
    hit reaches :meth:`OutputGuard.should_abstain` carrying only an RRF score,
    ``hit_relevance`` returns ``None``, and the guard took its "cannot assess
    relevance" branch and answered anyway. That was safe while the fallback was
    keyword search over curated FAQ rows, which had their own question-F1 gate;
    over 7,000 raw document chunks BM25 finds *something* for any query, so
    "What is the capital of France?" was answered from a chunk about Thales Las
    France (Tanzania Branch) — one shared token out of two content words.

    Recall over precision by design: matching the terms is necessary for
    relevance, not sufficient. Downstream grounding and claim checks still run.
    """
    terms = _lexical_terms(query)
    if not terms:
        return 0.0
    haystack = " ".join(
        str(hit.get(field) or "")
        for field in ("text", "question", "answer", "section", "title", "source")
    )
    present = _lexical_terms(haystack)
    if not present:
        return 0.0
    if encoder is None:
        score = len(terms & present) / len(terms)
    else:
        weights = {term: encoder.term_idf(term) for term in terms}
        total = sum(weights.values())
        if total <= 0:
            score = 0.0
        else:
            score = sum(w for term, w in weights.items() if term in present) / total

    # Check question span if query contains a conditional or situational preamble
    from .query import extract_question_span
    q_span = extract_question_span(query)
    if q_span and q_span != query:
        span_terms = _lexical_terms(q_span)
        if span_terms:
            span_score = len(span_terms & present) / len(span_terms)
            score = max(score, span_score)

    matched_count = len(terms & present)
    if matched_count >= 3 and score < LEXICAL_RELEVANCE_FLOOR:
        score = max(score, LEXICAL_RELEVANCE_FLOOR)

    return score


def apply_preference_boost(
    hits: list[dict[str, Any]],
    prefer: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Soft-boost hits that match query-time metadata preferences (G17).

    Hard filters can empty the result set when the preferred edition is
    missing from the collection. A small additive boost keeps recall and
    still surfaces the current FY / mentioned tax type first.
    """
    if not hits or not prefer:
        return hits
    want_fy = str(prefer.get("fiscal_year") or "").strip()
    want_tax = str(prefer.get("tax_type") or "").strip().lower()
    if not want_fy and not want_tax:
        return hits

    for hit in hits:
        boost = 0.0
        if want_fy and str(hit.get("fiscal_year") or "") == want_fy:
            boost += 0.08
        if want_tax:
            blob = " ".join(
                str(hit.get(field) or "")
                for field in ("tax_type", "tag", "section", "source", "text", "question")
            ).lower()
            if want_tax in blob:
                boost += 0.05
        if not boost:
            continue
        if hit.get("score_norm") is not None:
            with suppress(TypeError, ValueError):
                hit["score_norm"] = min(1.0, float(hit["score_norm"]) + boost)
        try:
            hit["score_rrf"] = float(hit.get("score_rrf") or 0.0) + boost
        except (TypeError, ValueError):
            hit["score_rrf"] = boost

    hits.sort(
        key=lambda h: (
            1 if hit_relevance(h) is not None else 0,
            hit_relevance(h) or 0.0,
            float(h.get("score_rrf") or 0.0),
        ),
        reverse=True,
    )
    return hits


def hit_identity(hit: dict[str, Any]) -> str:
    """Stable id for fusing ranked lists from different retrieval legs."""
    return (
        str(hit.get("id") or "")
        or str(hit.get("chunk_id") or "")
        or (hit.get("text") or "")[:80]
    )


def rrf_fuse_ranked_lists(
    *lists: list[dict[str, Any]],
    k: int | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Reciprocal-rank fusion over independently ranked retrieval legs.

    Each list is already ordered best-first. A hit that appears in two
    lists accumulates ``1/(k+rank)`` from each; a graph-only or
    passage-only hit keeps the contribution from its own list. This is
    the same combinator Qdrant uses for dense+BM25, applied here so the
    statutory graph is a third leg rather than an unconditional prepend.
    """
    k = RRF_K if k is None else k
    scores: dict[str, float] = {}
    kept: dict[str, dict[str, Any]] = {}
    for ranked in lists:
        if not ranked:
            continue
        for rank, hit in enumerate(ranked):
            hid = hit_identity(hit)
            if not hid:
                continue
            scores[hid] = scores.get(hid, 0.0) + 1.0 / (k + rank)
            incoming = dict(hit)
            existing = kept.get(hid)
            if existing is None or incoming.get("doc_type") == "graph":
                kept[hid] = incoming
    fused: list[dict[str, Any]] = []
    for hid, hit in kept.items():
        hit["score_rrf"] = scores[hid]
        fused.append(hit)
    fused.sort(
        key=lambda h: (
            1 if hit_relevance(h) is not None else 0,
            hit_relevance(h) or 0.0,
            float(h.get("score_rrf") or 0.0),
        ),
        reverse=True,
    )
    if top_k is not None:
        fused = fused[:top_k]
    return fused


def merge_retrieval_hits(
    batches: list[list[dict[str, Any]]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """Dedupe and re-rank hits from parallel sub-query searches."""
    merged: list[dict[str, Any]] = []
    for batch in batches:
        merged.extend(batch)
    if not merged:
        return []
    merged = _dedupe_candidates(merged)
    merged.sort(
        key=lambda h: (
            1 if hit_relevance(h) is not None else 0,
            hit_relevance(h) or 0.0,
            float(h.get("score_rrf") or 0.0),
        ),
        reverse=True,
    )
    return prune_context(merged[:top_k])


def prune_context(
    hits: list[dict[str, Any]],
    *,
    floor: float | None = None,
    relative_drop: float | None = None,
) -> list[dict[str, Any]]:
    """Drop trailing hits the reranker already judged irrelevant.

    The system decided *whether* to answer from the best hit
    (``should_abstain`` uses ``max``), then handed the model **every**
    hit.  A set scored [0.85, 0.04, 0.03, 0.02] passes that gate and
    three passages at ~3% relevance still arrive as context.  Irrelevant
    passages next to a relevant one are not harmless padding: they are
    what lets a model combine two chunks that are about different things
    and produce a claim supported by neither.

    Two rules, both conservative:

    * an absolute floor, set *below* the abstention threshold so this
      only ever trims a result set that was already good enough to
      answer from;
    * a relative cut, because a hit can clear the floor and still be
      far below the best one.

    The top hit is always kept — starving the model of context is a
    worse failure than one weak passage, and abstention is decided
    elsewhere.  When no reranker signal is available (RRF-only or
    keyword fallback) this is a no-op: gating on an incomparable score
    is the exact mistake :func:`hit_relevance` exists to prevent.
    """
    if len(hits) <= 1:
        return hits
    floor = _CONTEXT_FLOOR if floor is None else floor
    relative_drop = _CONTEXT_RELATIVE_DROP if relative_drop is None else relative_drop

    scored = [(h, hit_relevance(h)) for h in hits]
    if any(rel is None for _, rel in scored):
        return hits  # degraded mode — no calibrated scale to threshold against

    best = max(rel for _, rel in scored if rel is not None)
    cutoff = max(floor, best - relative_drop)
    kept = [h for h, rel in scored if rel is not None and rel >= cutoff]
    if not kept:
        kept = [scored[0][0]]
    if len(kept) < len(hits):
        logger.info(
            "context pruned: %d/%d passages kept (best=%.2f cutoff=%.2f)",
            len(kept),
            len(hits),
            best,
            cutoff,
        )
    return kept


# ---------------------------------------------------------------------------
# BM25 sparse encoder
# ---------------------------------------------------------------------------
class BM25SparseEncoder:
    """Compute BM25-weighted sparse vectors for Qdrant's inverted index.

    Vocabulary and IDF are built from the indexed corpus via ``fit()``,
    then serialised to JSON for the retriever to load at query time.

    BM25 is **asymmetric**: the document side carries term-frequency
    saturation and length normalisation, the query side carries IDF, and
    their dot product is the score.  Encoding both sides the same way — as
    this class used to — applies IDF twice and normalises the query against
    the *document* average length, which is meaningless for a three-word
    question.  Measured over the full 7,970-document corpus with each of the
    509 indexed FAQ rows' own question as ground truth:

        suite                        Hit@1   Hit@3   Hit@5   MRR@10
        full question, symmetric     90.6%   97.2%   98.0%   0.940
        full question, asymmetric    93.3%   98.4%   99.0%   0.958
        short query, symmetric       49.3%   75.6%   83.9%   0.635
        short query, asymmetric      58.2%   82.3%   89.0%   0.711

    ("short query" is the scaffolding plus the question's highest-IDF term —
    "What is PAYE?" — which is how taxpayers actually ask and where the
    doubled IDF hurt most.)  This is also the split Qdrant's own sparse-BM25
    encoders use, so the stored vectors mean what the rest of the ecosystem
    assumes they mean.
    """

    #: Bumped when the meaning of the stored vectors changes.  ``1`` is the
    #: symmetric encoding described above; ``2`` is asymmetric BM25.  A state
    #: loaded from an older index keeps being queried its own way — see
    #: :meth:`encode_query` — so a collection built before this change keeps
    #: ranking exactly as it did instead of silently shifting.
    ENCODING_VERSION = 2

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._idf: dict[int, float] = {}
        self._next_id: int = 0
        self._k1: float = 1.2
        self._b: float = 0.75
        self._avg_dl: float = 0.0
        self._corpus_hash: str = ""
        self._encoding_version: int = self.ENCODING_VERSION

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

    def _saturation(self, count: int, dl: int) -> float:
        """BM25's ``tf`` component: saturating, length-normalised term weight."""
        num = count * (self._k1 + 1)
        denom = count + self._k1 * (1 - self._b + self._b * dl / max(self._avg_dl, 1))
        return num / denom

    def encode_document(self, text: str) -> tuple[list[int], list[float]]:
        """Return ``(indices, values)`` for a document's Qdrant ``SparseVector``.

        Document weights are pure term saturation.  IDF belongs to the query
        side, where it is applied once — see :meth:`encode_query`.
        """
        tokens = self._tokenize(text)
        dl = len(tokens)

        indices: list[int] = []
        values: list[float] = []
        for tok, count in Counter(tokens).items():
            tid = self._vocab.get(tok)
            if tid is None:
                continue
            if self._encoding_version < 2:
                # Only reachable through a legacy state; kept so re-encoding a
                # v1 corpus reproduces v1 vectors rather than mixing the two.
                weight = self._idf.get(tid, 0.0) * self._saturation(count, dl)
            else:
                weight = self._saturation(count, dl)
            if weight > 0:
                indices.append(tid)
                values.append(round(weight, 6))
        return indices, values

    def encode_query(self, text: str) -> tuple[list[int], list[float]]:
        """Return ``(indices, values)`` for a query's Qdrant ``SparseVector``.

        Query weights are IDF alone.  Dotted with a document vector from
        :meth:`encode_document` this is exactly ``sum(idf * tf_saturation)`` —
        the BM25 score.

        A state loaded from a v1 collection is encoded the v1 way: those
        document vectors already carry IDF, so pairing them with a v1 query
        vector keeps that index ranking as it always has.  Rebuilding the
        index is what moves a deployment to v2.
        """
        tokens = self._tokenize(text)
        dl = len(tokens)

        indices: list[int] = []
        values: list[float] = []
        for tok, count in Counter(tokens).items():
            tid = self._vocab.get(tok)
            if tid is None:
                continue
            idf = self._idf.get(tid, 0.0)
            weight = idf if self._encoding_version >= 2 else idf * self._saturation(count, dl)
            if weight > 0:
                indices.append(tid)
                values.append(round(weight, 6))
        return indices, values

    @property
    def corpus_hash(self) -> str:
        return self._corpus_hash

    @property
    def max_idf(self) -> float:
        """Highest IDF in the corpus; the weight given to unseen terms."""
        return max(self._idf.values()) if self._idf else 1.0

    def term_idf(self, token: str) -> float:
        """IDF of *token*, or :attr:`max_idf` when the corpus has never seen it.

        Treating an out-of-vocabulary term as maximally informative is the point:
        a question containing a word absent from the entire corpus is very likely
        not about this corpus, and weighting it heavily makes that unmatched term
        dominate the coverage score.
        """
        tid = self._vocab.get(token)
        if tid is None:
            return self.max_idf
        return self._idf.get(tid, 0.0)

    # -- Serialisation -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "vocab": self._vocab,
            "idf": {str(k): v for k, v in self._idf.items()},
            "avg_dl": self._avg_dl,
            "next_id": self._next_id,
            "corpus_hash": self._corpus_hash,
            "encoding_version": self._encoding_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BM25SparseEncoder:
        enc = cls()
        enc._vocab = data["vocab"]
        enc._idf = {int(k): v for k, v in data["idf"].items()}
        enc._avg_dl = data.get("avg_dl", 0.0)
        enc._next_id = data.get("next_id", 0)
        enc._corpus_hash = data.get("corpus_hash", "")
        # A state written before the asymmetric split carries no version, and
        # the vectors it describes have IDF baked into the document side.
        enc._encoding_version = int(data.get("encoding_version", 1))
        if enc._encoding_version < cls.ENCODING_VERSION:
            logger.warning(
                "BM25 state uses encoding v%d; reindex to get v%d asymmetric "
                "scoring (measured +8.9pp Hit@1 on short queries).",
                enc._encoding_version,
                cls.ENCODING_VERSION,
            )
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
        self._query_vec_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._ready = False
        # Disabled at init time if the loaded bm25_state is out of sync with
        # the live Qdrant vectors (P1-6) — search then runs dense-only.
        self._sparse_ok = True
        # Set when sentence-transformers is absent: Qdrant serves the corpus on
        # its sparse half alone, since a BM25 query vector needs no model.
        self._sparse_only = False
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
        """Bring up retrieval on the richest backend available.

        Priority, highest first:

        1. **Qdrant with a dense vector** — dense + BM25 sparse fused by RRF,
           then cross-encoder rerank. The richest backend, preferred whenever
           reachable with a real dense half.
        2. **Cloudflare Vectorize** — dense-only via Workers AI ``bge-m3``, with
           a client-side lexical re-score. No GPU or torch required. Also
           preferred over a *sparse-only* Qdrant collection: sparse-only Qdrant
           has no dense signal at all, so it is strictly poorer than a real
           (if unreranked) dense retriever — not richer, despite Qdrant being
           first in this list.
        3. **Qdrant sparse-only** — BM25 alone, served from Qdrant. The floor
           for the CPU-only deployments (Crane Cloud, HF Space) when Vectorize
           is not configured, rather than dropping straight to keyword search.
        4. **Keyword** — the caller's fallback when this returns ``False``.

        Every way Qdrant can be unavailable now falls through to Vectorize:
        previously only ``QDRANT_ENABLED=false`` did, so a missing collection, an
        unreachable host or an encoder mismatch skipped the dense fallback
        entirely and degraded straight to keyword search. A *sparse-only*
        collection is not "unavailable" in that sense — ``_init_qdrant`` returns
        ``True`` for it — so it needs its own check here too. Without it, a
        CPU-only image that bakes a sparse-only collection at build time (as
        Crane Cloud/HF Space's does) can never reach the dense fallback even
        when Vectorize is fully configured and seeded: this was a live
        regression, not a missing feature. Vectorize-backed hybrid retrieval
        was confirmed working end-to-end in prod before the sparse-only sidecar
        shipped, then silently stopped once it did, because a successful
        ``_init_qdrant`` was (wrongly) treated as always the richest case.
        """
        if QDRANT_ENABLED:
            if self._init_qdrant():
                if not self._sparse_only:
                    return True
                logger.info(
                    "Qdrant is sparse-only (no dense vector); trying Vectorize "
                    "before settling for BM25 alone"
                )
                if self._init_vectorize_mode():
                    return True
                logger.info(
                    "Vectorize unavailable; serving sparse-only Qdrant (BM25) instead"
                )
                return True
            logger.warning(
                "Qdrant unavailable at %s; trying the Cloudflare Vectorize dense fallback",
                QDRANT_URL,
            )
            self._reset_backend_state()
        else:
            logger.info("QDRANT_ENABLED=false; trying the Cloudflare Vectorize dense fallback")

        if self._init_vectorize_mode():
            return True
        logger.warning(
            "Neither Qdrant nor Vectorize is available; keyword fallback active. "
            "Retrieval is degraded — check QDRANT_URL/QDRANT_ENABLED, or configure "
            "CLOUDFLARE_*/VECTORIZE_INDEX and seed the index with "
            "scripts/reindex_vectorize.py."
        )
        return False

    def _reset_backend_state(self) -> None:
        """Drop half-initialised Qdrant state before trying another backend.

        A failure can happen after the client is constructed (missing
        collection, encoder mismatch); leaving it set would let ``search`` take
        the Qdrant path against a collection this process just rejected.
        """
        self._client = None
        self._dense_model = None
        self._reranker = None
        self._ready = False
        self._sparse_ok = True

    def _collection_has_dense_vector(self) -> bool:
        """Whether the live collection actually declares a named dense vector.

        A sparse-only collection (built with ``SPARSE_ONLY_INDEX=true`` so it can
        be created without torch) rejects a dense prefetch outright — Qdrant
        answers ``400 Not existing vector name error: dense`` and the whole search
        returns nothing. Asking the collection is therefore more reliable than
        inferring from whether sentence-transformers happens to be importable:
        the same sparse-only collection may well be queried by a process that
        does have torch.
        """
        try:
            params = self._client.get_collection(QDRANT_COLLECTION).config.params
            vectors = getattr(params, "vectors", None)
            if not vectors:
                return False
            if isinstance(vectors, dict):
                return "dense" in vectors
            # An unnamed single-vector collection still carries a dense space.
            return True
        except Exception:
            # Unknown shape — assume dense so behaviour is unchanged, and let the
            # search path surface any real error.
            logger.warning("Could not read the collection's vector config", exc_info=True)
            return True

    def _init_qdrant(self) -> bool:
        """Connect to Qdrant and load models.  Returns ``True`` if ready."""
        try:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10)
            collections = [c.name for c in self._client.get_collections().collections]
            aliases = {a.alias_name for a in self._client.get_aliases().aliases}
            if QDRANT_COLLECTION not in collections and QDRANT_COLLECTION not in aliases:
                logger.warning("Qdrant collection '%s' not found", QDRANT_COLLECTION)
                return False

            # Alias-backed staged builds embed their matching BM25 encoder in
            # the sentinel. This avoids pairing a newly-promoted sparse index
            # with a stale local state file. Older collections retain the file
            # fallback for backwards compatibility.
            binding = self._binding_payload()
            if not self._load_sparse_state_from_binding(binding) and BM25_STATE_PATH.exists():
                with open(BM25_STATE_PATH) as f:
                    self._sparse_encoder = BM25SparseEncoder.from_dict(json.load(f))
                logger.info("Loaded BM25 state from %s", BM25_STATE_PATH)
            self._verify_bm25_binding(binding)
            if not self._verify_embedder_binding():
                # Both halves of hybrid search are untrustworthy against this
                # collection. Give up on Qdrant so ``initialize`` can try the
                # Vectorize index, which is built by a known-correct encoder.
                self._ready = False
                return False

            # A sparse-only collection cannot serve a dense query at all, so
            # decide from the collection first and only then try to load models.
            collection_is_sparse_only = not self._collection_has_dense_vector()
            try:
                if collection_is_sparse_only:
                    raise ImportError("collection declares no dense vector")
                from sentence_transformers import CrossEncoder, SentenceTransformer
            except ImportError:
                # No torch in this image (Crane Cloud / HF Space). Dense search
                # is impossible because the *query* cannot be encoded — but BM25
                # sparse encoding is pure Python over the vocab+idf in
                # bm25_state.json, so Qdrant can still serve the whole corpus on
                # the sparse half alone. That is far better than the keyword
                # fallback, which only reads the FAQ CSVs and never sees a PDF or
                # crawl chunk.
                if not self._sparse_ok or not self._sparse_encoder.corpus_hash:
                    logger.warning(
                        "No dense retrieval available (%s) and no usable BM25 state; "
                        "cannot serve from Qdrant",
                        "sparse-only collection" if collection_is_sparse_only else "no sentence-transformers",
                    )
                    return False
                self._sparse_only = True
                self._dense_model = None
                self._reranker = None
                self._ready = True
                logger.info(
                    "HybridRetriever ready in SPARSE-ONLY mode (url=%s collection=%s) — "
                    "no sentence-transformers, so dense retrieval and reranking are off",
                    QDRANT_URL,
                    QDRANT_COLLECTION,
                )
                return True

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
            if self._dense_model is not None:
                try:
                    from .mcp.tool_rag import inject_dense_model

                    inject_dense_model(self._dense_model)
                except Exception:
                    logger.debug("Tool RAG dense inject skipped", exc_info=True)
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
            logger.warning("Qdrant init failed", exc_info=True)
            self._ready = False
            return False

    def _init_vectorize_mode(self) -> bool:
        """Dense retrieval via Workers AI bge-m3 + Vectorize (no GPU/torch).

        The second choice after Qdrant: dense-only, so it has no BM25 signal and
        no cross-encoder rerank. Returns ``True`` if the fallback is active.
        """
        if DENSE_FALLBACK_BACKEND in _DENSE_FALLBACK_DISABLED:
            logger.info("Vectorize dense fallback disabled by DENSE_FALLBACK_BACKEND=%s", DENSE_FALLBACK_BACKEND)
            return False
        if DENSE_FALLBACK_BACKEND not in ("", "workers_ai"):
            logger.warning(
                "Unrecognised DENSE_FALLBACK_BACKEND=%r; expected 'workers_ai', "
                "'none', or unset (auto)",
                DENSE_FALLBACK_BACKEND,
            )
            return False
        try:
            from .providers import config as _cfg

            if not _cfg.is_vectorize_configured():
                logger.info(
                    "Vectorize dense fallback unavailable: Cloudflare/VECTORIZE_INDEX "
                    "not configured (need CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, "
                    "CF_AIG_GATEWAY, CF_AIG_TOKEN, VECTORIZE_INDEX)"
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
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Dense retrieval via Workers AI bge-m3 -> Vectorize, fused client-side
        with a lexical (BM25-lite) re-score via RRF.  CPU-only hybrid."""
        from .providers import breakers, budget, routing
        from .providers import gateway as _gw
        from .providers import vectorize as _vz

        if not breakers.VECTORIZE_BREAKER.allow_request():
            logger.warning("Vectorize circuit OPEN — skipping dense fallback")
            return []
        if not budget.try_consume_neurons(1):
            logger.info("Workers AI neuron budget exhausted — skipping dense fallback")
            return []
        try:
            from .hyde import dense_query_text

            dense_vec = _gw.workers_ai_embed([dense_query_text(query, subject=subject)])[0]
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
        k = RRF_K
        rrf = [0.0] * len(hits)
        for dense_rank in range(len(hits)):  # Vectorize returns best-first
            rrf[dense_rank] += 1.0 / (k + dense_rank)
        for lex_rank, i in enumerate(lex_order):
            rrf[i] += 1.0 / (k + lex_rank)

        order = sorted(range(len(hits)), key=lambda i: rrf[i], reverse=True)
        candidates = []
        for i in order:
            hit = hits[i]
            text = hit.get("text", "")
            q = hit.get("question", "")
            a = hit.get("answer", "")
            if (not q or not a) and text.startswith("Question: ") and "\nAnswer: " in text:
                parts = text[len("Question: ") :].split("\nAnswer: ", 1)
                q = q or parts[0].strip()
                a = a or parts[1].strip()
            candidates.append(
                {
                    "id": str(hit.get("id", "")),
                    "text": text,
                    "question": q,
                    "answer": a,
                    "source": hit.get("source", ""),
                    "chunk_id": str(hit.get("id", "")),
                    "page": hit.get("page", ""),
                    "section": hit.get("section", ""),
                    "doc_type": hit.get("doc_type", ""),
                    "fiscal_year": hit.get("fiscal_year", ""),
                    "tax_type": hit.get("tax_type", ""),
                    "tag": hit.get("tag", ""),
                    **_provenance_fields(hit),
                    "score_rrf": float(rrf[i]),
                }
            )
        # Same near-duplicate collapse the Qdrant path applies: this fallback
        # serves the same multi-edition corpus, so without it one passage can
        # occupy most of top_k and a superseded edition can outrank the current
        # one on dense score alone.
        candidates = _dedupe_candidates(candidates)
        self._attach_lexical_relevance(query, candidates)
        if self._reranker and candidates:
            self._rerank(query, candidates)
        self._ready = True
        return prune_context(candidates[:top_k])

    def _attach_lexical_relevance(self, query: str, candidates: list[dict[str, Any]]) -> None:
        """Stamp each candidate with ``score_lexical``, in place.

        Computed here because this class owns the BM25 statistics the score needs.
        ``OutputGuard.should_abstain`` then reads the field without having to
        reach for an encoder, and both retrieval paths carry it.
        """
        for candidate in candidates:
            candidate["score_lexical"] = lexical_relevance(query, candidate, self._sparse_encoder)

    def _binding_payload(self) -> dict[str, Any]:
        """Return the Qdrant binding sentinel payload for this collection."""
        try:
            points = self._client.retrieve(
                collection_name=QDRANT_COLLECTION,
                ids=[bm25_binding_sentinel_id(QDRANT_COLLECTION)],
                with_payload=True,
                with_vectors=False,
            )
            return dict(points[0].payload or {}) if points else {}
        except Exception:
            logger.warning("Could not read Qdrant binding sentinel", exc_info=True)
            return {}

    def _load_sparse_state_from_binding(self, payload: dict[str, Any]) -> bool:
        """Load the compressed encoder state shipped with an alias-backed index."""
        packed = payload.get("bm25_state_zlib")
        if not isinstance(packed, str) or not packed:
            return False
        try:
            raw = zlib.decompress(base64.b64decode(packed.encode("ascii")))
            state = json.loads(raw)
            self._sparse_encoder = BM25SparseEncoder.from_dict(state)
            logger.info("Loaded BM25 state from Qdrant binding sentinel")
            return True
        except Exception:
            logger.error("Qdrant binding contains an invalid BM25 state", exc_info=True)
            self._sparse_ok = False
            return False

    def _verify_bm25_binding(self, payload: dict[str, Any] | None = None) -> None:
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
        payload = payload if payload is not None else self._binding_payload()
        remote = str(payload.get("corpus_hash", ""))
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

    def _verify_embedder_binding(self) -> bool:
        """Return ``False`` when the collection was built by a different encoder.

        Dense retrieval has no way to notice this on its own: querying a
        ``bge-m3`` collection with, say, a 384-dim MiniLM vector either raises
        deep inside the client or — worse, once dimensions happen to agree —
        returns confidently ranked nonsense. Degrading to keyword search is the
        safe outcome for a tax assistant.

        A missing stamp (collection built before this check) is treated as
        "cannot verify" and left enabled, matching :meth:`_verify_bm25_binding`.
        """
        try:
            points = self._client.retrieve(
                collection_name=QDRANT_COLLECTION,
                ids=[bm25_binding_sentinel_id(QDRANT_COLLECTION)],
                with_payload=True,
                with_vectors=False,
            )
            payload = (points[0].payload or {}) if points else {}
            remote_model = str(payload.get("dense_model", ""))
            if not remote_model:
                logger.warning(
                    "Collection '%s' carries no embedder stamp; cannot verify it was "
                    "built with %s — reindex to write it.",
                    QDRANT_COLLECTION,
                    DENSE_MODEL_NAME,
                )
                return True
            if remote_model != DENSE_MODEL_NAME:
                logger.error(
                    "Embedder MISMATCH: collection '%s' was indexed with %s but this "
                    "process queries with %s — disabling dense retrieval; reindex or set "
                    "DENSE_MODEL=%s.",
                    QDRANT_COLLECTION,
                    remote_model,
                    DENSE_MODEL_NAME,
                    remote_model,
                )
                return False
            remote_dim = payload.get("dense_dim")
            if isinstance(remote_dim, int) and remote_dim != DENSE_DIM:
                logger.error(
                    "Embedding dimension MISMATCH: collection '%s' is %d-dim, this "
                    "process is configured for %d — disabling dense retrieval.",
                    QDRANT_COLLECTION,
                    remote_dim,
                    DENSE_DIM,
                )
                return False
            return True
        except Exception:
            logger.warning("Embedder binding verification failed; leaving dense enabled", exc_info=True)
            return True

    @property
    def backend(self) -> str:
        """Which retrieval tier is serving: ``qdrant``, ``vectorize`` or ``keyword``.

        Reported so a silent demotion from hybrid to dense-only is visible in
        logs and health output rather than only showing up as worse answers.
        """
        if not self._ready:
            return "keyword"
        return "vectorize" if self._vectorize_mode else "qdrant"

    @property
    def is_ready(self) -> bool:
        """Check if retriever was initialised. Does NOT do a live call —
        the circuit breaker in ``search()`` handles transient failures."""
        return self._ready and (self._client is not None or self._vectorize_mode)

    def _encode_query(self, query: str) -> list[float]:
        """Embed *query*, reusing a recent identical embedding.

        A single turn embeds the same or near-identical text more than
        once: corrective RAG re-retrieves, and the rewritten query is
        frequently byte-identical to the original.  Query embedding is
        pure, so a small LRU removes that repeated forward pass without
        changing a single result.
        """
        cached = self._query_vec_cache.get(query)
        if cached is not None:
            self._query_vec_cache.move_to_end(query)
            return cached
        vector = self._dense_model.encode(query).tolist()
        self._query_vec_cache[query] = vector
        while len(self._query_vec_cache) > _QUERY_CACHE_SIZE:
            self._query_vec_cache.popitem(last=False)
        return vector

    def _rerank(self, query: str, candidates: list[dict[str, Any]]) -> None:
        """Score *candidates* with the cross-encoder, in place.

        Passages are truncated for scoring only.  A cross-encoder is
        quadratic in sequence length and its own input window is far
        shorter than a full chunk, so feeding untruncated text costs
        latency to produce a score the model derived from the head of the
        passage anyway.
        """
        pairs = [
            (query, (c.get("text") or c.get("answer") or c.get("question", ""))[:_RERANK_CHARS])
            for c in candidates
        ]
        scores = self._reranker.predict(pairs)
        for i, s in enumerate(scores):
            candidates[i]["score_rerank"] = float(s)
            candidates[i]["score_norm"] = normalize_rerank_score(float(s))
        candidates.sort(key=lambda x: x.get("score_rerank", 0.0), reverse=True)

    def search(
        self,
        query: str,
        top_k: int = 4,
        prefetch_limit: int = 20,
        filters: dict[str, Any] | None = None,
        *,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search with RRF fusion + optional cross-encoder rerank.

        *filters* accepts Qdrant payload filter keys, e.g.
        ``{"doc_type": "pdf", "tag": "vat"}``.
        *subject* buckets ``FLAG_HYDE_PERCENT``; omit it and HyDE stays
        at the registry default (off).
        """
        if self._vectorize_mode:
            hits = self._search_vectorize(query, top_k, prefetch_limit, filters, subject=subject)
            if hits or self._client is None:
                return hits
            # Vectorize produced nothing: an open circuit, an exhausted neuron
            # budget, a failed request, or an index that answers no query. Every
            # one of those used to return [] straight to the caller, which then
            # degraded to keyword search over the FAQ CSVs — while the
            # sparse-only Qdrant collection ``initialize`` deliberately keeps
            # alive for this case (see its docstring) sat healthy in the same
            # container holding the whole corpus. Both CPU deployments were
            # observed answering from 499 FAQ rows instead of 7,600+ documents
            # for exactly this reason: /ready reported "vector" because the
            # backend was *selected*, and nothing reported that it never served
            # a single query. Fall through to the Qdrant path below instead.
            logger.info(
                "Vectorize returned no results; serving from the local Qdrant "
                "collection %s instead of degrading to keyword search",
                QDRANT_COLLECTION,
            )

        if not self._ready or self._client is None:
            return []
        # Sparse-only mode has no dense model by design; every other mode needs one.
        if self._dense_model is None and not self._sparse_only:
            return []

        # Circuit breaker gate — reject immediately when OPEN
        if not self._circuit.allow_request():
            logger.warning("Circuit breaker OPEN — skipping Qdrant search")
            return []

        try:
            from qdrant_client import models
        except ImportError:
            # Stand-ins for the query-builder constructors so this path still
            # composes where qdrant_client is not installed. Nothing built from
            # them is ever sent: `self._client` is None in those environments
            # and the readiness guard above has already returned.
            #
            # This shim guards the import ONLY. Keep the search body in its own
            # `try` below — folding it in here left it reachable only when
            # qdrant_client was missing, so with the client installed (i.e. in
            # production) `search()` ran nothing and returned None.
            from types import SimpleNamespace

            models = SimpleNamespace(  # type: ignore[assignment]
                FieldCondition=lambda **kw: SimpleNamespace(**kw),
                MatchAny=lambda **kw: SimpleNamespace(**kw),
                MatchValue=lambda **kw: SimpleNamespace(**kw),
                Filter=lambda **kw: SimpleNamespace(**kw),
                Prefetch=lambda **kw: SimpleNamespace(**kw),
                SparseVector=lambda **kw: SimpleNamespace(**kw),
                FusionQuery=lambda **kw: SimpleNamespace(**kw),
                Fusion=SimpleNamespace(RRF="rrf"),
            )

        try:
            from .hyde import dense_query_text

            # HyDE (when flagged) rewrites only the dense embedding. BM25
            # and the cross-encoder stay on the taxpayer's own words so a
            # hallucinated hypothetical cannot change lexical matching.
            dense_vec = (
                None if self._sparse_only else self._encode_query(dense_query_text(query, subject=subject))
            )
            sparse_idx, sparse_val = self._sparse_encoder.encode_query(query)
            if self._sparse_only and not sparse_idx:
                # No query term is in the BM25 vocabulary, and there is no dense
                # half to fall back on.
                self._circuit.record_success()
                return []

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

            prefetch = []
            if dense_vec is not None:
                prefetch.append(
                    models.Prefetch(query=dense_vec, using="dense", limit=prefetch_limit)
                )
            if sparse_idx and self._sparse_ok:
                prefetch.append(
                    models.Prefetch(
                        query=models.SparseVector(indices=sparse_idx, values=sparse_val),
                        using="sparse",
                        limit=prefetch_limit,
                    )
                )

            query_started = time.perf_counter()
            results = self._client.query_points(
                collection_name=QDRANT_COLLECTION,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=query_filter,
                limit=prefetch_limit,
            )
            metrics.observe(
                "qdrant_query_duration_seconds",
                time.perf_counter() - query_started,
                labels={"mode": "sparse" if self._sparse_only else "hybrid"},
            )

            if not results.points:
                self._circuit.record_success()
                return []

            candidates: list[dict[str, Any]] = []
            for pt in results.points:
                p = pt.payload or {}
                if p.get("_meta") == "bm25_binding":
                    continue  # internal corpus-hash sentinel, not a document
                text = p.get("text", "")
                q = p.get("question", "")
                a = p.get("answer", "")
                if (not q or not a) and text.startswith("Question: ") and "\nAnswer: " in text:
                    parts = text[len("Question: ") :].split("\nAnswer: ", 1)
                    q = q or parts[0].strip()
                    a = a or parts[1].strip()
                candidates.append(
                    {
                        "id": str(pt.id),
                        "text": text,
                        "question": q,
                        "answer": a,
                        "source": p.get("source", ""),
                        "chunk_id": p.get("chunk_id", ""),
                        "page": p.get("page", ""),
                        "section": p.get("section", ""),
                        "doc_type": p.get("doc_type", ""),
                        # Edition of the source document, used to prefer current
                        # guidance over a superseded restatement of it.
                        "fiscal_year": p.get("fiscal_year", ""),
                        "tax_type": p.get("tax_type", ""),
                        "tag": p.get("tag", ""),
                        **_provenance_fields(p),
                        "score_rrf": float(pt.score) if pt.score else 0.0,
                    }
                )

            # Drop near-duplicate chunks before the expensive step.  The
            # corpus carries the same guidance across editions, so a
            # prefetch of 20 routinely contains several copies of one
            # passage — they cost reranker time and then crowd genuinely
            # different evidence out of top_k.
            candidates = _dedupe_candidates(candidates)
            self._attach_lexical_relevance(query, candidates)

            # Cross-encoder reranking
            if self._reranker and candidates:
                self._rerank(query, candidates)

            self._circuit.record_success()
            self._ready = True  # ensure readiness restored on success
            # top_k is a ceiling, not a quota: returning four passages
            # when only one is relevant pads the prompt with noise.
            return prune_context(candidates[:top_k])

        except Exception:
            metrics.inc("qdrant_query_errors_total")
            self._circuit.record_failure()
            logger.exception("Hybrid search failed; circuit breaker tracking failure")
            # Do NOT permanently disable _ready — the circuit breaker
            # controls availability via allow_request(). Setting _ready=False
            # here would prevent auto-recovery when Qdrant comes back.
            return []

    def search_planned(
        self,
        query: str,
        top_k: int = 4,
        prefetch_limit: int = 20,
        filters: dict[str, Any] | None = None,
        *,
        locale: str | None = None,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search with query-time filters, multi-intent split, and soft boosts.

        Shared by REST, streaming, the RAG tool, LangGraph, and voice prefetch
        so those paths cannot drift (agentic retrieve-as-tool + G17).
        *locale* triggers a merged English translation pass when the corpus
        is English and the question is not (G18).
        *subject* buckets HyDE percent rollout on the dense leg.
        """
        from .flags import flags
        from .query import plan_retrieval

        plan = plan_retrieval(query)
        merged_filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", "any")}
        merged_filters.update(plan["filters"])
        subqueries = plan["subqueries"]
        use_decompose = flags.is_enabled("query_decomposition") and len(subqueries) > 1

        if use_decompose:
            batches = [
                self.search(
                    sub,
                    top_k=top_k,
                    prefetch_limit=prefetch_limit,
                    filters=merged_filters or None,
                    subject=subject,
                )
                for sub in subqueries
            ]
            hits = merge_retrieval_hits(batches, top_k=top_k)
        else:
            hits = self.search(
                query,
                top_k=top_k,
                prefetch_limit=prefetch_limit,
                filters=merged_filters or None,
                subject=subject,
            )
        hits = apply_preference_boost(hits, plan["prefer"])
        return self._merge_translated_leg(
            query,
            hits,
            top_k=top_k,
            prefetch_limit=prefetch_limit,
            filters=merged_filters or None,
            prefer=plan["prefer"],
            locale=locale,
            subject=subject,
        )

    def _merge_translated_leg(
        self,
        query: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
        prefetch_limit: int,
        filters: dict[str, Any] | None,
        prefer: dict[str, Any],
        locale: str | None,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        """Second hybrid pass on an English translation (G18). Never displaces a first pass that already worked well — merge only."""
        from .flags import flags
        from .query import english_retrieval_query

        loc = (locale or "en").strip().lower().split("-")[0]
        if not flags.is_enabled("translate_retrieve") or loc in ("", "en"):
            return hits
        english = english_retrieval_query(query, locale)
        if not english or english.casefold() == (query or "").strip().casefold():
            # Worth logging at INFO: this is the branch where a non-English
            # question silently keeps whatever the untranslated first pass
            # found (usually nothing), and it is indistinguishable from "the
            # leg ran and found nothing" without a line here.
            logger.info(
                "G18 translate-leg skipped (%s): translation absent or unchanged, "
                "first pass kept %d hit(s)",
                locale, len(hits),
            )
            return hits
        en_hits = self.search(
            english,
            top_k=top_k,
            prefetch_limit=prefetch_limit,
            filters=filters,
            subject=subject,
        )
        if not en_hits:
            logger.info(
                "G18 translate-leg (%s -> en): %r -> %r, first_pass=%d en_leg=0",
                locale, (query or "")[:60], english[:60], len(hits),
            )
            return hits
        merged = merge_retrieval_hits([hits, en_hits], top_k=top_k)
        merged = apply_preference_boost(merged, prefer)

        def _best(rows: list[dict[str, Any]]) -> float:
            vals = [r for h in rows if (r := hit_relevance(h)) is not None]
            return max(vals) if vals else -1.0

        logger.info(
            "G18 translate-leg (%s -> en): %r -> %r, first_pass=%d(best=%.3f) "
            "en_leg=%d(best=%.3f) merged=%d(best=%.3f)",
            locale, (query or "")[:60], english[:60],
            len(hits), _best(hits), len(en_hits), _best(en_hits),
            len(merged), _best(merged),
        )
        return merged

    # -- Grounding helpers ---------------------------------------------------
    @staticmethod
    def compute_faithfulness(answer: str, contexts: list[str]) -> float:
        """Fraction of factual answer sentences grounded in the contexts.

        Lightweight runtime proxy for the RAGAS faithfulness metric: a
        sentence counts as grounded when >=50 % of its content tokens
        (stopwords removed) appear in the retrieved contexts. Courtesy
        sentences — greetings, empathy acknowledgments, contact footers,
        follow-up suggestions (see text_signals.is_courtesy_sentence) —
        carry no factual claims and are excluded from both sides of the
        ratio, so polite phrasing never reads as hallucination. An answer
        left with no factual sentences asserts nothing and scores 1.0;
        the courtesy filter never matches sentences carrying figures, so
        fabricated rates/amounts/deadlines still drive the score down.
        """
        if not answer or not contexts:
            return 0.0

        ctx_tokens = content_tokens(" ".join(contexts))
        grounded = 0
        scoreable = 0
        for sent in split_sentences(answer):
            if is_courtesy_sentence(sent):
                continue
            sent_tokens = content_tokens(sent)
            if len(sent_tokens) < 2:
                continue
            scoreable += 1
            if len(sent_tokens & ctx_tokens) / len(sent_tokens) >= 0.5:
                grounded += 1

        if not scoreable:
            return 1.0
        return round(grounded / scoreable, 4)

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
            url = canonical_source_url(str(hit.get("source") or ""), str(hit.get("url") or ""))
            if url:
                cit["url"] = url
            if hit.get("effective_date"):
                cit["effective_date"] = str(hit["effective_date"])
            if hit.get("title"):
                cit["title"] = str(hit["title"])
            passage = hit.get("text") or hit.get("answer") or ""
            cit["passage"] = passage[:500]
            citations.append(cit)
        return citations
