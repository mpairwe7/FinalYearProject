"""Build the static dense index the runtime uses for hybrid retrieval.

Why static embeddings rather than a model
-----------------------------------------
The deployed image has numpy and nothing else: no torch, no onnxruntime, no
sentence-transformers. Qdrant is not reachable there and neither is Cloudflare
(the Space cannot open a TLS connection to any Cloudflare host), so both of the
retriever's existing dense paths are dead in production and every query falls
through to BM25 alone.

BM25 alone has a specific, visible failure: `_simple_search` drops any entry
scoring zero, so a question phrased in words the corpus does not use returns
nothing at all rather than something approximate.

This script precomputes the one artefact that restores a semantic signal without
adding a runtime dependency — a word -> vector table, following the static
embedding approach (Model2Vec and sentence-transformers' StaticEmbedding): each
vocabulary term is encoded ONCE, offline, by a real sentence transformer, and
the runtime just looks vectors up and averages them. Inference becomes a dict
lookup plus a matmul.

Consistency rules that make this work
-------------------------------------
* The vocabulary and tokenizer are BM25's own (``\\w+`` lowercased), so the
  dense and sparse sides always see the same terms. No second tokenizer to
  drift.
* Queries and documents are embedded the SAME way — IDF-weighted mean of word
  vectors. Encoding documents with the full model while queries use averaged
  word vectors would put them in different spaces and quietly degrade ranking.
* IDF weighting comes from the same bm25_state.json the sparse side uses, so
  rare domain terms (presumptive, EFRIS, withholding) dominate the vector the
  way they dominate the BM25 score.

Output is float16 — the table is ~8 MB at 10.7k terms x 384 dims, which is
small enough to commit and load at import.

Usage:
    python -m ml.scripts.build_static_index
    python -m ml.scripts.build_static_index --model <st-model> --out <path>
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_static_index")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Multilingual: the picker offers English and Swahili among others, and this
# model covers both. No small open model covers Luganda/Runyankole/Acholi;
# those queries are translated upstream before they reach retrieval.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_OUT = ROOT / "Model" / "static_index.npz"
BM25_STATE = ROOT / "Model" / "bm25_state.json"


def _tokenize(text: str) -> list[str]:
    """Identical to BM25SparseEncoder._tokenize — do not let these diverge."""
    return re.findall(r"\w+", text.lower())


def _load_corpus() -> list[dict]:
    """FAQ entries via the app's own loader, so the index matches what serves."""
    sys.path.insert(0, str(ROOT / "App" / "backend"))
    from app.service import _DATA_DIR, _load_faq_data  # noqa: E402

    faq_index, _ = _load_faq_data(_DATA_DIR)
    entries: list[dict] = []
    for tag, rows in sorted(faq_index.items()):
        for i, row in enumerate(rows):
            entries.append(
                {
                    "id": f"{tag}:{i}",
                    "tag": tag,
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                }
            )
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--batch-size", type=int, default=256)
    args = ap.parse_args()

    entries = _load_corpus()
    logger.info("Corpus: %d FAQ entries", len(entries))
    if not entries:
        logger.error("No FAQ entries found — refusing to write an empty index")
        return 1

    # ── vocabulary + IDF, taken from the sparse side ────────────────────────
    idf: dict[str, float] = {}
    if BM25_STATE.exists():
        state = json.loads(BM25_STATE.read_text())
        vocab: dict[str, int] = state.get("vocab", {})
        raw_idf: dict = state.get("idf", {})
        for term, tid in vocab.items():
            v = raw_idf.get(str(tid), raw_idf.get(tid))
            if v is not None:
                idf[term] = float(v)
        logger.info("Loaded %d terms with IDF from %s", len(idf), BM25_STATE.name)
    else:
        logger.warning("%s missing — deriving IDF from the corpus", BM25_STATE)

    # Any term the corpus uses but BM25 never saw still needs a vector, or the
    # document side would silently drop it.
    df = Counter()
    for e in entries:
        df.update(set(_tokenize(f"{e['question']} {e['answer']}")))
    n_docs = len(entries)
    terms = sorted(set(idf) | set(df))
    for t in terms:
        if t not in idf:
            idf[t] = float(np.log((n_docs - df[t] + 0.5) / (df[t] + 0.5) + 1.0))
    logger.info("Vocabulary: %d terms", len(terms))

    # ── encode each term once, in isolation ─────────────────────────────────
    from sentence_transformers import SentenceTransformer

    logger.info("Loading %s", args.model)
    model = SentenceTransformer(args.model)
    dim = model.get_sentence_embedding_dimension()
    logger.info("Encoding %d terms (dim=%d)", len(terms), dim)
    word_vecs = model.encode(
        terms,
        batch_size=args.batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype(np.float32)

    # ── remove the common component ─────────────────────────────────────────
    # Encoding single words produces vectors that nearly all point the same
    # way: a sentence transformer trained on sentences puts isolated tokens in
    # a tight cone, so averaged vectors land on a shared centroid and every
    # cosine comes out ~0.94. Measured on this corpus before the fix, an
    # unrelated query returned three near-identical scores and the wrong
    # documents.
    #
    # Centre, then strip the top principal directions (Mu & Viswanath,
    # "All-but-the-Top"; the same post-processing Model2Vec applies via PCA).
    # What is left is the part of each vector that actually distinguishes one
    # term from another.
    mean = word_vecs.mean(axis=0, keepdims=True)
    centred = word_vecs - mean
    n_components = max(1, dim // 100)  # the paper's D ~= d/100
    # Randomised SVD on the top components only — a full SVD of 10.7k x 384 is
    # wasted work when we need the first few directions.
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    top = vt[:n_components]                       # (n_components, dim)
    centred -= (centred @ top.T) @ top
    norms = np.linalg.norm(centred, axis=1, keepdims=True)
    word_vecs = np.divide(centred, norms, out=np.zeros_like(centred), where=norms > 0)
    logger.info("Removed %d principal component(s) from the term vectors", n_components)

    index = {t: i for i, t in enumerate(terms)}
    idf_arr = np.array([max(idf.get(t, 0.0), 0.0) for t in terms], dtype=np.float32)

    def embed(text: str) -> np.ndarray:
        """IDF-weighted mean of term vectors — the runtime does exactly this."""
        ids, weights = [], []
        for tok, count in Counter(_tokenize(text)).items():
            i = index.get(tok)
            if i is None:
                continue
            w = idf_arr[i] * (1.0 + np.log(count))
            if w > 0:
                ids.append(i)
                weights.append(w)
        if not ids:
            return np.zeros(dim, dtype=np.float32)
        w = np.asarray(weights, dtype=np.float32)
        v = (word_vecs[ids] * w[:, None]).sum(0)
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    # ── document vectors, in the same space as queries ──────────────────────
    doc_vecs = np.stack([embed(f"{e['question']} {e['answer']}") for e in entries])
    empties = int((np.linalg.norm(doc_vecs, axis=1) == 0).sum())
    if empties:
        logger.warning("%d documents produced an empty vector", empties)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out,
        terms=np.array(terms, dtype=object),
        word_vecs=word_vecs.astype(np.float16),
        idf=idf_arr,
        doc_vecs=doc_vecs.astype(np.float16),
        doc_ids=np.array([e["id"] for e in entries], dtype=object),
        doc_tags=np.array([e["tag"] for e in entries], dtype=object),
        model=np.array(args.model),
        dim=np.array(dim),
    )
    size_mb = args.out.stat().st_size / 1e6
    logger.info(
        "Wrote %s — %d terms, %d docs, dim=%d, %.1f MB",
        args.out, len(terms), len(entries), dim, size_mb,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
