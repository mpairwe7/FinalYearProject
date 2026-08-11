"""Static dense retrieval — MEASURED AND NOT SHIPPED. Kept as the experiment.

This is not imported by the application. It was built as a candidate for the
semantic half of hybrid retrieval and rejected on its own numbers; see
ml/scripts/eval_retrieval.py for the measurements and the module docstring
there for the verdict. It stays so the experiment can be re-run rather than
re-argued.

Original rationale follows.

Both of the retriever's dense paths are unavailable in the deployed image:
Qdrant + sentence-transformers needs torch (not installed), and the Workers AI
+ Vectorize fallback needs Cloudflare (the Space cannot open a TLS connection
to any Cloudflare host). Every query therefore falls through to BM25 alone,
and `_simple_search` drops entries scoring zero — so a question phrased in
words the corpus does not use returns nothing rather than something close.

This module restores the semantic signal with no runtime dependency beyond
numpy, by moving all the model work offline. ``ml/scripts/build_static_index``
encodes each vocabulary term ONCE with a real sentence transformer and stores
a word -> vector table; here a query is embedded by looking its terms up and
averaging. That is the static-embedding approach (Model2Vec,
sentence-transformers' StaticEmbedding) applied to the constraint at hand.

What it is and is not: averaged term vectors carry synonymy and topical
similarity, which is what BM25 lacks. They do not carry word order or
composition, so this complements the sparse side rather than replacing it —
which is exactly how it is wired, via reciprocal rank fusion.

The index is memory-mapped and loaded once, lazily, so a deployment without
the artefact simply reports unavailable and the caller keeps its old
behaviour.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger("ura.static_dense")

# Model/static_index.npz, alongside bm25_state.json.
_INDEX_PATH = Path(__file__).resolve().parents[2] / "Model" / "static_index.npz"

_lock = threading.Lock()
_index: dict[str, Any] | None = None
_load_attempted = False


def _tokenize(text: str) -> list[str]:
    """Identical to BM25SparseEncoder._tokenize. The two sides share a
    vocabulary, so they must share a tokenizer."""
    return re.findall(r"\w+", text.lower())


def _load() -> dict[str, Any] | None:
    global _index, _load_attempted
    if _index is not None:
        return _index
    with _lock:
        if _index is not None:
            return _index
        if _load_attempted:
            return None
        _load_attempted = True
        if not _INDEX_PATH.exists():
            logger.info("Static dense index %s not found; sparse-only retrieval", _INDEX_PATH)
            return None
        try:
            import numpy as np

            data = np.load(_INDEX_PATH, allow_pickle=True)
            terms = [str(t) for t in data["terms"]]
            # float16 on disk, float32 in memory: the table is ~8 MB either
            # way, and float32 avoids a per-query upcast in the matmul.
            _index = {
                "term_ids": {t: i for i, t in enumerate(terms)},
                "word_vecs": data["word_vecs"].astype(np.float32),
                "idf": data["idf"].astype(np.float32),
                "doc_vecs": data["doc_vecs"].astype(np.float32),
                "doc_ids": [str(x) for x in data["doc_ids"]],
                "doc_tags": [str(x) for x in data["doc_tags"]],
                "dim": int(data["dim"]),
                "np": np,
            }
            logger.info(
                "Static dense index ready (%d terms, %d docs, dim=%d, model=%s)",
                len(terms), len(_index["doc_ids"]), _index["dim"], str(data["model"]),
            )
            return _index
        except Exception:
            logger.warning("Failed to load static dense index; sparse-only", exc_info=True)
            return None


def is_available() -> bool:
    return _load() is not None


def doc_ids() -> list[str]:
    idx = _load()
    return list(idx["doc_ids"]) if idx else []


def encode(text: str):
    """IDF-weighted mean of term vectors, L2-normalised. None when unusable.

    Must stay identical to the embedding step in build_static_index, or queries
    and documents stop sharing a space.
    """
    idx = _load()
    if idx is None:
        return None
    np = idx["np"]
    ids: list[int] = []
    weights: list[float] = []
    for token, count in Counter(_tokenize(text)).items():
        i = idx["term_ids"].get(token)
        if i is None:
            continue
        # Sub-linear term frequency, same shape BM25 uses, so one repeated
        # word cannot dominate the direction of the vector.
        w = float(idx["idf"][i]) * (1.0 + float(np.log(count)))
        if w > 0:
            ids.append(i)
            weights.append(w)
    if not ids:
        return None
    w = np.asarray(weights, dtype=np.float32)
    vec = (idx["word_vecs"][ids] * w[:, None]).sum(0)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else None


def search(query: str, top_k: int = 20) -> list[tuple[str, float]]:
    """Top-*k* ``(doc_id, cosine)`` for *query*, best first.

    One matmul over a 506x384 matrix — the corpus is small enough that an
    approximate index (HNSW/IVF) would add a dependency and a recall cliff to
    save microseconds.
    """
    idx = _load()
    if idx is None:
        return []
    vec = encode(query)
    if vec is None:
        return []
    np = idx["np"]
    scores = idx["doc_vecs"] @ vec  # both sides L2-normalised -> cosine
    if top_k >= len(scores):
        order = np.argsort(-scores)
    else:
        part = np.argpartition(-scores, top_k)[:top_k]
        order = part[np.argsort(-scores[part])]
    return [(idx["doc_ids"][i], float(scores[i])) for i in order if scores[i] > 0]


def rrf_fuse(
    rankings: list[list[str]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> dict[str, float]:
    """Reciprocal rank fusion — ``sum(w / (k + rank))`` over each ranking.

    RRF is the default fusion in Elasticsearch, OpenSearch, Weaviate and
    Vespa because it needs no score calibration: BM25 scores are unbounded
    and cosines sit in [-1, 1], so any weighted sum of the raw values would
    be dominated by whichever side happened to be on a larger scale, and the
    balance would drift as the corpus grew. Ranks are comparable by
    construction.

    k=60 is the constant from the original paper and the one those engines
    default to; it damps the difference between the top few positions so a
    single confident-but-wrong first hit cannot carry the fused result.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    fused: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + weight / (k + rank)
    return fused
