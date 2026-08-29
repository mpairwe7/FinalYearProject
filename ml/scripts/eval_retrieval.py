"""Measure retrieval quality: BM25 alone vs static dense alone vs RRF fusion.

The corpus is its own ground truth — each FAQ entry is the single correct
answer for a query derived from it. Two query sets, because they exercise
different things:

  verbatim  the entry's own question. BM25 should be near-perfect here; the
            point is to confirm fusion does not damage the case it already
            wins.

  jargon-dropped
            the question with its highest-IDF terms removed. This is the user
            who does not know that the form is called "presumptive" or that
            receipting is "EFRIS" — exactly where exact-term matching fails
            and a semantic signal should earn its place. Dropping the rarest
            terms is a mechanical, reproducible way to build that set without
            hand-writing paraphrases.

Reported: Hit@1/3/5 and MRR@10.

Usage:
    python -m ml.scripts.eval_retrieval
    python -m ml.scripts.eval_retrieval --drop 2 --k 60
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "App" / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _load_entries() -> list[dict]:
    from app.service import _DATA_DIR, _load_faq_data

    faq_index, _ = _load_faq_data(_DATA_DIR)
    out = []
    for tag, rows in sorted(faq_index.items()):
        for i, row in enumerate(rows):
            out.append(
                {
                    "id": f"{tag}:{i}",
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                }
            )
    return out


class BM25:
    """BM25 over the eval entries, scored by the **production** encoder.

    This used to be a separate implementation with its own ``k1`` (1.5 against
    production's 1.2) reading IDF out of a committed ``Model/bm25_state.json``
    whose provenance nothing checked.  It was therefore blind to the ranker it
    was meant to gate: while ``BM25SparseEncoder`` encoded the document and the
    query the same way — applying IDF twice — this script scored a textbook
    formula instead and reported a healthy number.

    Fitting the real encoder on the entries and scoring the same sparse dot
    product Qdrant computes gives the gate teeth.  Measured on the 516-row FAQ
    corpus: 93.0% verbatim Hit@1 with the corrected asymmetric encoding, 91.1%
    with the old symmetric one.
    """

    def __init__(self, entries: list[dict]):
        from app.retriever import BM25SparseEncoder

        texts = [f"{e['question']} {e['answer']}" for e in entries]
        self.encoder = BM25SparseEncoder().fit(texts)
        self.docs = [
            (e["id"], dict(zip(*self.encoder.encode_document(text))))
            for e, text in zip(entries, texts)
        ]

    def term_idf(self, token: str) -> float:
        """IDF of *token* in the eval corpus, 0.0 when it is unseen."""
        tid = self.encoder._vocab.get(token)
        return self.encoder._idf.get(tid, 0.0) if tid is not None else 0.0

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        indices, values = self.encoder.encode_query(query)
        scored = []
        for doc_id, vector in self.docs:
            s = 0.0
            for tid, weight in zip(indices, values):
                doc_weight = vector.get(tid)
                if doc_weight:
                    s += weight * doc_weight
            if s > 0:
                scored.append((doc_id, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


def metrics(ranked: list[str], target: str) -> tuple[int, int, int, float]:
    hit1 = int(bool(ranked) and ranked[0] == target)
    hit3 = int(target in ranked[:3])
    hit5 = int(target in ranked[:5])
    rr = 0.0
    for i, d in enumerate(ranked[:10], start=1):
        if d == target:
            rr = 1.0 / i
            break
    return hit1, hit3, hit5, rr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", type=int, default=2, help="rarest terms to remove")
    ap.add_argument("--k", type=int, default=60, help="RRF constant")
    ap.add_argument(
        "--min-hit1",
        type=float,
        default=None,
        help="fail if verbatim BM25 Hit@1 falls below this (0-1); CI regression gate",
    )
    args = ap.parse_args()

    from ml.scripts import static_dense as sd

    entries = _load_entries()
    bm25 = BM25(entries)
    # Building the static index needs sentence-transformers, which the CI image
    # and the deployed image both lack. Without it, report BM25 alone rather than
    # refusing to measure anything — the corpus-derived ground truth still makes
    # Hit@k and MRR meaningful for the lexical path, which is what actually
    # serves when no dense backend is reachable.
    dense_available = sd.is_available()
    if not dense_available:
        print("static dense index unavailable — reporting BM25 only")

    def jargon_dropped(question: str) -> str:
        toks = _tokenize(question)
        if len(toks) <= 3:
            return question
        ranked = sorted(toks, key=lambda t: -bm25.term_idf(t))
        drop = set(ranked[: args.drop])
        kept = [t for t in toks if t not in drop]
        return " ".join(kept) if len(kept) >= 2 else question

    suites = {
        "verbatim question": lambda e: e["question"],
        f"jargon-dropped (-{args.drop} rarest)": lambda e: jargon_dropped(e["question"]),
    }

    methods = ("bm25", "dense", "rrf") if dense_available else ("bm25",)
    verbatim_bm25_hit1 = None

    for suite_name, make_query in suites.items():
        agg = {m: [0, 0, 0, 0.0] for m in methods}
        n = 0
        for e in entries:
            q = make_query(e)
            if not q.strip():
                continue
            n += 1
            b_rank = [d for d, _ in bm25.search(q, top_k=20)]
            ranked_by_method = {"bm25": b_rank}
            if dense_available:
                d_rank = [d for d, _ in sd.search(q, top_k=20)]
                fused = sd.rrf_fuse([b_rank, d_rank], k=args.k)
                ranked_by_method["dense"] = d_rank
                ranked_by_method["rrf"] = [
                    d for d, _ in sorted(fused.items(), key=lambda x: -x[1])
                ]
            for name in methods:
                h1, h3, h5, rr = metrics(ranked_by_method[name], e["id"])
                agg[name][0] += h1
                agg[name][1] += h3
                agg[name][2] += h5
                agg[name][3] += rr

        if not n:
            print(f"\n{suite_name}: no queries produced — nothing measured")
            continue

        print(f"\n{suite_name}  (n={n})")
        print(f"  {'method':8}{'Hit@1':>8}{'Hit@3':>8}{'Hit@5':>8}{'MRR@10':>9}")
        for name in methods:
            h1, h3, h5, rr = agg[name]
            print(f"  {name:8}{h1/n:>8.1%}{h3/n:>8.1%}{h5/n:>8.1%}{rr/n:>9.3f}")
        if suite_name.startswith("verbatim"):
            verbatim_bm25_hit1 = agg["bm25"][0] / n

    if args.min_hit1 is not None:
        if verbatim_bm25_hit1 is None:
            print("\nFAIL: --min-hit1 given but the verbatim suite produced no queries")
            return 1
        if verbatim_bm25_hit1 < args.min_hit1:
            print(
                f"\nFAIL: verbatim BM25 Hit@1 {verbatim_bm25_hit1:.1%} "
                f"< required {args.min_hit1:.1%}"
            )
            return 1
        print(
            f"\nOK: verbatim BM25 Hit@1 {verbatim_bm25_hit1:.1%} >= {args.min_hit1:.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
