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
import json
import re
import sys
from collections import Counter
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
    """Standalone BM25 over the same entries, using the committed idf/avg_dl."""

    def __init__(self, entries: list[dict], k1: float = 1.5, b: float = 0.75):
        state_path = ROOT / "Model" / "bm25_state.json"
        self.k1, self.b = k1, b
        self.idf: dict[str, float] = {}
        if state_path.exists():
            state = json.loads(state_path.read_text())
            raw = state.get("idf", {})
            for term, tid in state.get("vocab", {}).items():
                v = raw.get(str(tid), raw.get(tid))
                if v is not None:
                    self.idf[term] = float(v)
        self.docs = []
        for e in entries:
            toks = _tokenize(f"{e['question']} {e['answer']}")
            self.docs.append((e["id"], Counter(toks), len(toks)))
        self.avg_dl = sum(d[2] for d in self.docs) / max(len(self.docs), 1)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        q = set(_tokenize(query))
        scored = []
        for doc_id, tf, dl in self.docs:
            s = 0.0
            norm = 1 - self.b + self.b * dl / max(self.avg_dl, 1.0)
            for term in q:
                f = tf.get(term)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                if idf <= 0:
                    continue
                s += idf * (f * (self.k1 + 1)) / (f + self.k1 * norm)
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
    args = ap.parse_args()

    from ml.scripts import static_dense as sd

    entries = _load_entries()
    bm25 = BM25(entries)
    if not sd.is_available():
        print("static dense index unavailable — build it first")
        return 1

    def jargon_dropped(question: str) -> str:
        toks = _tokenize(question)
        if len(toks) <= 3:
            return question
        ranked = sorted(toks, key=lambda t: -bm25.idf.get(t, 0.0))
        drop = set(ranked[: args.drop])
        kept = [t for t in toks if t not in drop]
        return " ".join(kept) if len(kept) >= 2 else question

    suites = {
        "verbatim question": lambda e: e["question"],
        f"jargon-dropped (-{args.drop} rarest)": lambda e: jargon_dropped(e["question"]),
    }

    for suite_name, make_query in suites.items():
        agg = {m: [0, 0, 0, 0.0] for m in ("bm25", "dense", "rrf")}
        n = 0
        for e in entries:
            q = make_query(e)
            if not q.strip():
                continue
            n += 1
            b_hits = bm25.search(q, top_k=20)
            d_hits = sd.search(q, top_k=20)
            b_rank = [d for d, _ in b_hits]
            d_rank = [d for d, _ in d_hits]
            fused = sd.rrf_fuse([b_rank, d_rank], k=args.k)
            r_rank = [d for d, _ in sorted(fused.items(), key=lambda x: -x[1])]
            for name, ranked in (("bm25", b_rank), ("dense", d_rank), ("rrf", r_rank)):
                h1, h3, h5, rr = metrics(ranked, e["id"])
                agg[name][0] += h1
                agg[name][1] += h3
                agg[name][2] += h5
                agg[name][3] += rr

        print(f"\n{suite_name}  (n={n})")
        print(f"  {'method':8}{'Hit@1':>8}{'Hit@3':>8}{'Hit@5':>8}{'MRR@10':>9}")
        for name in ("bm25", "dense", "rrf"):
            h1, h3, h5, rr = agg[name]
            print(f"  {name:8}{h1/n:>8.1%}{h3/n:>8.1%}{h5/n:>8.1%}{rr/n:>9.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
