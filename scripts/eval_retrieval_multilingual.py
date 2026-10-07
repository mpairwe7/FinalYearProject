"""Trilingual retrieval measurement on the live index (English, Luganda, Swahili).

Scores *retrieval*, not replies: every query in
``evals/retrieval_multilingual/queries.jsonl`` names the Qdrant points that
answer it (built by ``scripts/build_retrieval_multilingual_set.py``), so each
retrieval leg can be measured on its own and the legs compared on the same
queries.

Systems, per query (``pivot`` is the English the production retriever searches
with for a Luganda or Swahili question — ``query.english_retrieval_query``):

  bm25 / dense / hybrid      one leg, or Qdrant's server-side RRF, on the raw query
  hybrid_rerank              + the cross-encoder, raw query only
  pivot_bm25 / pivot_dense   one leg on the pivot
  pivot_hybrid               server-side RRF on the pivot
  pivot_rrf60                client-side RRF (k=60, Cormack et al.) of the two legs
  pivot_dbsf                 Qdrant's distribution-based score fusion
  pivot_hybrid_rerank        + the cross-encoder on the pivot
  production                 ``HybridRetriever.search_planned`` with the true locale
                             (first pass, G18 translate leg, merge, prune) — what
                             the service hands on, at most ``--top-k`` passages
  service                    ``ChatModel.generate_retrieval_only`` with no locale
                             hint: language detection, routers, keyword fallback,
                             corrective RAG, FAQ blend, binding gates, abstention

The English rows have no pivot; their pivot_* rows repeat the raw ones.
Relevance is ``strict`` (the gold point, or a point with the same question) or
``lenient`` (also any passage containing at least 60% of the gold answer's
content words, which credits the PDF chunk a FAQ row was written from).

Runs inside the api container so it inherits the stack's exact environment —
``RERANK_ENABLED``, ``RETRIEVER_DENSE_DEVICE``, flags — without reading any
secret. The configuration under test is whatever that environment says;
override a variable with ``docker exec -e`` to measure another:

    docker exec ura-app-api mkdir -p /home/appuser/rm_eval
    docker cp scripts/eval_retrieval_multilingual.py ura-app-api:/home/appuser/rm_eval/
    docker cp evals/retrieval_multilingual/queries.jsonl ura-app-api:/home/appuser/rm_eval/
    docker cp evals/retrieval_multilingual/gold.jsonl ura-app-api:/home/appuser/rm_eval/
    docker exec -w /app ura-app-api python /home/appuser/rm_eval/eval_retrieval_multilingual.py \\
        run --config as_deployed
    docker exec -w /app -e RERANK_ENABLED=true -e RETRIEVER_DENSE_DEVICE=cuda:0 ura-app-api \\
        python /home/appuser/rm_eval/eval_retrieval_multilingual.py run --config rerank_gpu
    docker exec -w /app ura-app-api python /home/appuser/rm_eval/eval_retrieval_multilingual.py \\
        run --config sparse_only --sparse-only

``latency`` measures cold per-stage timings (caches cleared before every query)
and a concurrency sweep; ``judge`` grades a pool of coverage-bank candidates
with the local Sunflower (UMBRELA's 0-3 scale) into ``qrels_coverage.jsonl``;
``report`` turns the raw files into ``evals/reports/retrieval_multilingual_<date>.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# ``app`` lives in the working directory inside the container (/app) and in
# App/backend in a checkout; a script's own directory is all Python adds.
for _base in (Path.cwd(), HERE.parent / "App" / "backend"):
    if (_base / "app" / "__init__.py").exists() and str(_base) not in sys.path:
        sys.path.insert(0, str(_base))
# Copied into the container, the set sits next to this script; in a checkout
# it lives under evals/.
DATA_DIR = HERE if (HERE / "queries.jsonl").exists() else HERE.parent / "evals" / "retrieval_multilingual"
QUERIES = DATA_DIR / "queries.jsonl"
QRELS = DATA_DIR / "qrels_coverage.jsonl"
LANGS = ("en", "lg", "sw")
STAGES = ("mt", "dense", "qdrant", "rerank")
PAYLOAD = ["text", "question", "answer", "source", "doc_type", "chunk_id", "tag", "fiscal_year", "_meta"]
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the is are was were be been of in on at to for with by from as and or not no but if then than that this "
    "these those it its i me my we our you your what which who how when where why can do does did will would shall "
    "should may must have has had any all also such other per into under over about there their they them".split()
)


# --------------------------------------------------------------------------- helpers
def norm_question(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").casefold())).strip()


def content_tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall((text or "").lower()) if len(t) > 3 and t not in _STOP}


def token_f1(a: str, b: str) -> float:
    x, y = content_tokens(a), content_tokens(b)
    if not x or not y:
        return 0.0
    overlap = len(x & y)
    if not overlap:
        return 0.0
    p, r = overlap / len(y), overlap / len(x)
    return 2 * p * r / (p + r)


# --------------------------------------------------------------------------- perturbations
# Typo and transcript-style copies are derived when the set is loaded rather
# than stored: each is a pure function of the query and its id, so the same
# copies come back on every load.
SEED = "20261007"
PERTURBED_VARIANTS = ("paraphrase", "translation", "native")
_KEYBOARD_NEIGHBOURS = {
    "a": "sqwz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr", "f": "drtgvc",
    "g": "ftyhbv", "h": "gyujnb", "i": "ujko", "j": "huikmn", "k": "jiolm", "l": "kop",
    "m": "njk", "n": "bhjm", "o": "iklp", "p": "ol", "q": "wa", "r": "edft",
    "s": "awedxz", "t": "rfgy", "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc",
    "y": "tghu", "z": "asx",
}


def stable_rank(*parts: str) -> str:
    return hashlib.sha256("|".join((SEED, *parts)).encode()).hexdigest()


def typo(text: str, key: str) -> str:
    """Seeded keyboard slips: about one word in five of four or more letters."""
    rng = random.Random(stable_rank("typo", key))
    words = text.split(" ")
    for i, word in enumerate(words):
        letters = re.sub(r"[^A-Za-z]", "", word)
        if len(letters) < 4 or letters.isupper() or rng.random() >= 0.2:
            continue
        chars = list(word)
        idx = [j for j, ch in enumerate(chars) if ch.isalpha()]
        j = rng.choice(idx[1:-1] or idx)
        op = rng.choice(("swap", "delete", "double", "neighbour"))
        if op == "swap" and j + 1 < len(chars) and chars[j + 1].isalpha():
            chars[j], chars[j + 1] = chars[j + 1], chars[j]
        elif op == "delete":
            del chars[j]
        elif op == "double":
            chars.insert(j, chars[j])
        else:
            near = _KEYBOARD_NEIGHBOURS.get(chars[j].lower())
            if near:
                chars[j] = rng.choice(near)
        words[i] = "".join(chars)
    return " ".join(words)


def asr_style(text: str) -> str:
    """What a transcript or a hurried phone keyboard produces: no case, no
    punctuation, elision apostrophes dropped (Luganda ``ow'omusolo`` becomes
    ``owomusolo``), ``%`` spoken."""
    out = text.replace("%", " percent").replace("’", "'")
    out = re.sub(r"(\w)'(\w)", r"\1\2", out)
    out = re.sub(r"[^\w\s]", " ", out.lower())
    return re.sub(r"\s+", " ", out).strip()


def perturbed_copies(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The typo and ASR-style copies of a paraphrase, translation or native row."""
    if row["variant"] not in PERTURBED_VARIANTS:
        return []
    copies = []
    for variant, noisy in (("typo", typo(row["query"], row["id"])), ("asr", asr_style(row["query"]))):
        if noisy != row["query"]:
            copies.append({**row, "id": f"{row['id']}~{variant}", "parent": row["id"],
                           "variant": f"{row['variant']}+{variant}", "query": noisy})
    return copies


class StageClock:
    """Time spent in each retrieval stage, accumulated per thread."""

    def __init__(self) -> None:
        self._local = threading.local()

    def start(self) -> None:
        self._local.ms = dict.fromkeys(STAGES, 0.0)
        self._local.calls = dict.fromkeys(STAGES, 0)

    def add(self, stage: str, seconds: float) -> None:
        ms = getattr(self._local, "ms", None)
        if ms is not None:
            ms[stage] += seconds * 1000.0
            self._local.calls[stage] += 1

    def take(self) -> dict[str, Any]:
        ms = getattr(self._local, "ms", None) or dict.fromkeys(STAGES, 0.0)
        calls = getattr(self._local, "calls", None) or dict.fromkeys(STAGES, 0)
        self._local.ms = None
        return {"ms": {k: round(v, 2) for k, v in ms.items()}, "calls": dict(calls)}


CLOCK = StageClock()


def timed(stage: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            CLOCK.add(stage, time.perf_counter() - started)

    return wrapper


def sampled(key: str, fraction: float) -> bool:
    """Deterministic Bernoulli(fraction) keyed on *key*."""
    if fraction >= 1.0:
        return True
    return int(hashlib.sha256(f"20261007|{key}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < fraction


def load_queries(path: Path, sets: set[str] | None, limit: int) -> list[dict[str, Any]]:
    """The query set, with ``gold.jsonl`` joined on ``parent`` and the typo/ASR copies derived."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_path = path.with_name("gold.jsonl")
    # Every row names a parent whose answer lives only in gold.jsonl; without
    # it the run "succeeds" with empty accuracy tables.
    if not gold_path.exists():
        raise SystemExit(f"{gold_path} is missing; copy it next to {path.name}")
    lines = [line for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {g["parent"]: g for g in map(json.loads, lines)}
    rows = [{**gold.get(r["parent"], {}), **r} for r in rows]
    if not any("+" in r["variant"] for r in rows):
        rows = [out for r in rows for out in (r, *perturbed_copies(r))]
    if sets:
        rows = [r for r in rows if r["set"] in sets]
    if limit:
        rng = random.Random(20261007)
        rng.shuffle(rows)
        rows = sorted(rows[:limit], key=lambda r: r["id"])
    return rows


def english_source(rows: list[dict[str, Any]]) -> dict[str, str]:
    """The English each Luganda/Swahili row was translated from, for pivot fidelity."""
    by_parent: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        if r["lang"] == "en" and "+" not in r["variant"]:
            by_parent[r["parent"]][r["variant"]] = r["query"]
    out: dict[str, str] = {}
    for r in rows:
        if r["lang"] == "en":
            continue
        en = by_parent.get(r["parent"], {})
        out[r["id"]] = en.get("paraphrase") or en.get("verbatim") or en.get("curated") \
            or next(iter(en.values()), "") or r.get("gold_question", "")
    return out


# --------------------------------------------------------------------------- relevance
#: UMBRELA grades read as relevant on the judged coverage pool. Sunflower is a
#: lenient judge (it grades 23% of unrelated passages 2 or more), so only its top
#: grade counts as strict relevance.
COVERAGE_STRICT_GRADE = 3
COVERAGE_LENIENT_GRADE = 2


class Judge:
    """Strict / lenient relevance of a passage to a row with gold points."""

    def __init__(self, row: dict[str, Any], qrels: dict[str, dict[str, int]] | None = None) -> None:
        self.gold = set(row.get("gold_ids") or [])
        self.gold_q = norm_question(row.get("gold_question", ""))
        answer_terms = content_tokens(row.get("gold_answer", ""))
        self.answer_terms = answer_terms if len(answer_terms) >= 5 else set()
        self.graded = (qrels or {}).get(row["parent"]) if row["set"] == "coverage" else None

    @property
    def has_gold(self) -> bool:
        return bool(self.gold) or bool(self.graded)

    def strict(self, hit: dict[str, Any]) -> int:
        if self.graded is not None:
            return int(self.graded.get(passage_key(hit), 0) >= COVERAGE_STRICT_GRADE)
        if str(hit.get("id", "")) in self.gold:
            return 1
        q = norm_question(hit.get("question", ""))
        return int(bool(q) and q == self.gold_q)

    def lenient(self, hit: dict[str, Any]) -> int:
        if self.strict(hit):
            return 1
        if self.graded is not None or not self.answer_terms:
            return 0
        text = " ".join(str(hit.get(k) or "") for k in ("text", "answer"))
        return int(len(self.answer_terms & content_tokens(text)) / len(self.answer_terms) >= 0.6)

    def grade(self, hit: dict[str, Any]) -> int:
        if self.graded is not None:
            return int(self.graded.get(passage_key(hit), 0))
        return 3 * self.strict(hit)


def passage_key(hit: dict[str, Any]) -> str:
    pid = str(hit.get("id") or "")
    if pid:
        return pid
    basis = f"{hit.get('source', '')}|{norm_question(hit.get('question', ''))}|{(hit.get('text') or '')[:200]}"
    # An identity for passages without a Qdrant id; only compared within one run.
    return "nid:" + hashlib.sha256(basis.encode()).hexdigest()[:16]


def rank_metrics(labels: list[int], k: int = 10) -> dict[str, float]:
    first = next((i for i, v in enumerate(labels[:k]) if v), None)
    return {
        "hit1": float(bool(labels[:1] and labels[0])),
        "hit3": float(any(labels[:3])),
        "hit5": float(any(labels[:5])),
        "hit10": float(any(labels[:10])),
        "mrr10": 0.0 if first is None else 1.0 / (first + 1),
        "ndcg10": 0.0 if first is None else 1.0 / math.log2(first + 2),
    }


def graded_ndcg(grades: list[int], ideal: list[int], k: int = 10) -> float:
    def dcg(gs: list[int]) -> float:
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gs[:k]))

    best = dcg(sorted(ideal, reverse=True))
    return dcg(grades) / best if best > 0 else 0.0


# --------------------------------------------------------------------------- retrieval
class Harness:
    def __init__(self, *, sparse_only: bool, service: bool) -> None:
        from app import query as query_module
        from app import service as service_module
        from app.retriever import QDRANT_COLLECTION

        self.collection = QDRANT_COLLECTION
        self.query_module = query_module
        if service:
            self.chat = service_module.ChatModel()
            self.r = self.chat._retriever
        else:
            from app.retriever import HybridRetriever

            self.chat = None
            self.r = HybridRetriever()
            if not self.r.initialize():
                raise SystemExit("retriever failed to initialise")
        if sparse_only:
            # What the CPU deployments run (Crane Cloud, the HF Space): the
            # ImportError branch in HybridRetriever drops embedder and reranker.
            self.r._sparse_only = True
            self.r._dense_model = None
            self.r._reranker = None
        if self.r._dense_model is not None:
            self.r._dense_model.encode = timed("dense", self.r._dense_model.encode)
        self.r._client.query_points = timed("qdrant", self.r._client.query_points)
        if self.r._reranker is not None:
            self.r._reranker.predict = timed("rerank", self.r._reranker.predict)
        translate = timed("mt", query_module.translate_query_for_retrieval)
        query_module.translate_query_for_retrieval = translate
        service_module.translate_query_for_retrieval = translate
        self.has_dense = self.r._dense_model is not None
        self.has_rerank = self.r._reranker is not None

    def clear_caches(self) -> None:
        from app import mt

        mt.cache.clear()
        self.r._query_vec_cache.clear()

    def pivot(self, text: str, lang: str) -> str:
        return self.query_module.english_retrieval_query(text, lang)

    def _points(self, query: str, mode: str, limit: int = 20) -> list[dict[str, Any]]:
        from qdrant_client import models

        dense_vec = None
        if mode in ("dense", "rrf", "dbsf") and self.has_dense:
            # The retriever's own LRU: the production call before this one
            # already paid for (and timed) the embedding.
            dense_vec = self.r._encode_query(query)
        sp_idx: list[int] = []
        sp_val: list[float] = []
        if mode in ("bm25", "rrf", "dbsf") and self.r._sparse_ok:
            sp_idx, sp_val = self.r._sparse_encoder.encode_query(query)
        sparse = models.SparseVector(indices=sp_idx, values=sp_val) if sp_idx else None
        kwargs: dict[str, Any] = {"collection_name": self.collection, "limit": limit, "with_payload": PAYLOAD}
        if dense_vec is not None and sparse is not None:
            fusion = models.Fusion.RRF if mode == "rrf" else models.Fusion.DBSF
            res = self.r._client.query_points(
                prefetch=[models.Prefetch(query=dense_vec, using="dense", limit=limit),
                          models.Prefetch(query=sparse, using="sparse", limit=limit)],
                query=models.FusionQuery(fusion=fusion), **kwargs)
        elif dense_vec is not None:
            res = self.r._client.query_points(query=dense_vec, using="dense", **kwargs)
        elif sparse is not None:
            res = self.r._client.query_points(query=sparse, using="sparse", **kwargs)
        else:
            return []
        out = []
        for pt in res.points:
            p = pt.payload or {}
            if p.get("_meta") == "bm25_binding":
                continue
            out.append({"id": str(pt.id), "score": float(pt.score or 0.0), **{k: p.get(k, "") for k in PAYLOAD if k != "_meta"}})
        return out

    def ranked(self, query: str, mode: str, *, rerank_with: tuple[str, str | None] | None = None) -> list[dict[str, Any]]:
        from app.retriever import _dedupe_candidates, _filter_tombstoned_candidates

        if mode == "rrf60":
            legs = [self._points(query, "dense"), self._points(query, "bm25")]
            scores: dict[str, float] = defaultdict(float)
            keep: dict[str, dict[str, Any]] = {}
            for leg in legs:
                for rank, hit in enumerate(leg, start=1):
                    scores[hit["id"]] += 1.0 / (60 + rank)
                    keep.setdefault(hit["id"], hit)
            cands = sorted(keep.values(), key=lambda h: scores[h["id"]], reverse=True)[:20]
        else:
            cands = self._points(query, mode)
        cands = _dedupe_candidates(_filter_tombstoned_candidates(cands))
        self.r._attach_lexical_relevance(query, cands)
        if rerank_with and self.has_rerank and cands:
            raw, english = rerank_with
            self.r._rerank(raw, cands, english_query=english)
        return cands

    def production(self, query: str, lang: str, top_k: int) -> list[dict[str, Any]]:
        return self.r.search_planned(query, top_k=top_k, locale=lang)

    def service(self, query: str) -> dict[str, Any]:
        assert self.chat is not None
        return self.chat.generate_retrieval_only(
            query, conversation_id=str(uuid.uuid4()), locale="", top_k=6, memory_write=False)


def abstains(hits: list[dict[str, Any]], lang: str) -> bool:
    from app.guardrails import OutputGuard

    return bool(OutputGuard.should_abstain(hits, locale=lang))


def best_signal(hits: list[dict[str, Any]]) -> tuple[str, float | None]:
    """The number ``should_abstain`` decides on, and which one it is."""
    from app.retriever import hit_relevance

    rel = [v for h in hits if (v := hit_relevance(h)) is not None]
    if rel:
        return "rerank", max(rel)
    lex = [float(h["score_lexical"]) for h in hits if h.get("score_lexical") is not None]
    return ("lexical", max(lex)) if lex else ("none", None)


def compact(hits: list[dict[str, Any]], judge: Judge) -> list[dict[str, Any]]:
    out = []
    for h in hits[:20]:
        out.append({
            "key": passage_key(h),
            "doc_type": h.get("doc_type", ""),
            "strict": judge.strict(h) if judge.has_gold else None,
            "lenient": judge.lenient(h) if judge.has_gold else None,
            "rerank": round(float(h["score_norm"]), 4) if h.get("score_norm") is not None else None,
            "lexical": round(float(h["score_lexical"]), 4) if h.get("score_lexical") is not None else None,
        })
    return out


# --------------------------------------------------------------------------- run
def isolate_side_effects(work: Path) -> None:
    """No cached answers from the live stack, no tickets or memory, a scratch analytics DB."""
    for key in ("FLAG_SEMANTIC_CACHE", "FLAG_TICKET_QUEUE", "FLAG_HANDOFF_SUMMARIES", "FLAG_MEMORY_ENABLED"):
        os.environ[key] = "false"
    (work / "db").mkdir(parents=True, exist_ok=True)
    os.environ["ANALYTICS_DB_DIR"] = str(work / "db")
    from app import database as db

    db.init_db()


def cmd_run(args: argparse.Namespace) -> int:
    work = Path(args.work)
    isolate_side_effects(work)
    rows = load_queries(Path(args.queries), set(args.sets.split(",")) if args.sets else None, args.limit)
    sources_en = english_source(load_queries(Path(args.queries), None, 0))
    # Stable subsamples, so two configurations sampled alike stay paired.
    rows = [r for r in rows if "+" not in r["variant"] or sampled(r["id"], args.sample_perturbed)]
    out_path = work / f"raw_{args.config}.jsonl"
    if args.resume and out_path.exists():
        done_ids = {json.loads(line)["id"] for line in out_path.read_text(encoding="utf-8").splitlines() if line}
        rows = [r for r in rows if r["id"] not in done_ids]
    h = Harness(sparse_only=args.sparse_only, service=not args.no_service)
    env = env_summary(h)
    print(json.dumps({"config": args.config, "rows": len(rows), **env}), file=sys.stderr)
    lock = threading.Lock()
    done = 0
    started = time.time()

    def one(row: dict[str, Any]) -> dict[str, Any]:
        judge = Judge(row)
        lang, q = row["lang"], row["query"]
        rec: dict[str, Any] = {k: row[k] for k in ("id", "set", "lang", "variant", "parent")}
        rec["config"] = args.config
        systems: dict[str, Any] = {}

        h.clear_caches()
        CLOCK.start()
        t0 = time.perf_counter()
        prod = h.production(q, lang, args.top_k)
        prod_ms = (time.perf_counter() - t0) * 1000
        stages = CLOCK.take()
        kind, signal = best_signal(prod)
        systems["production"] = {"hits": compact(prod, judge), "ms": round(prod_ms, 1), "stages": stages,
                                 "abstain": abstains(prod, lang), "signal": signal, "signal_kind": kind}

        pivot = h.pivot(q, lang) if lang != "en" else q
        rec["pivot"] = pivot
        if lang != "en":
            src = sources_en.get(row["id"], "")
            rec["pivot_f1"] = round(token_f1(src, pivot), 4) if src else None
            rec["pivot_unchanged"] = pivot.strip().casefold() == q.strip().casefold()

        modes = [("bm25", "bm25"), ("hybrid", "rrf")] + ([("dense", "dense")] if h.has_dense else [])
        for name, mode in modes:
            CLOCK.start()
            t0 = time.perf_counter()
            hits = h.ranked(q, mode)
            systems[name] = {"hits": compact(hits, judge), "ms": round((time.perf_counter() - t0) * 1000, 1)}
        if h.has_rerank:
            hits = h.ranked(q, "rrf", rerank_with=(q, None))
            systems["hybrid_rerank"] = {"hits": compact(hits, judge)}
        if lang == "en":
            for name in ("bm25", "dense", "hybrid"):
                if name in systems:
                    systems[f"pivot_{name}"] = systems[name]
        else:
            for name, mode in [("pivot_bm25", "bm25"), ("pivot_hybrid", "rrf")] + (
                    [("pivot_dense", "dense")] if h.has_dense else []):
                systems[name] = {"hits": compact(h.ranked(pivot, mode), judge)}
        if h.has_dense:
            systems["pivot_rrf60"] = {"hits": compact(h.ranked(pivot, "rrf60"), judge)}
            systems["pivot_dbsf"] = {"hits": compact(h.ranked(pivot, "dbsf"), judge)}
        if h.has_rerank:
            hits = h.ranked(pivot, "rrf", rerank_with=(pivot, None))
            systems["pivot_hybrid_rerank"] = {"hits": compact(hits, judge)}

        if (h.chat is not None and row["set"] in args.service_sets.split(",") and "+" not in row["variant"]
                and (row["set"] != "faq" or sampled(row["id"], args.service_sample))):
            t0 = time.perf_counter()
            try:
                res = h.service(q)
                err = ""
            except Exception as exc:  # noqa: BLE001 — recorded, not fatal to the run
                res, err = {}, f"{type(exc).__name__}: {exc}"[:200]
            hits = res.get("_hits") or []
            kind, signal = best_signal(hits)
            systems["service"] = {
                "hits": compact(hits, judge), "ms": round((time.perf_counter() - t0) * 1000, 1),
                "mode": res.get("retrieval_mode", ""), "locale": res.get("locale", ""),
                "abstain": res.get("retrieval_mode") == "abstained", "signal": signal, "signal_kind": kind,
                "error": err, "reply_head": (res.get("reply") or "")[:120],
            }
        rec["systems"] = systems
        return rec

    mode = "a" if args.resume else "w"
    with out_path.open(mode, encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rec in pool.map(one, rows):
            with lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done += 1
                if done % 100 == 0:
                    print(f"{done}/{len(rows)} in {time.time() - started:.0f}s", file=sys.stderr, flush=True)
    (work / f"env_{args.config}.json").write_text(json.dumps({"config": args.config, **env}, indent=1))
    print(f"wrote {out_path} ({done} rows, {time.time() - started:.0f}s)", file=sys.stderr)
    return 0


def env_summary(h: Harness) -> dict[str, Any]:
    from app.flags import flags
    from app.guardrails import ABSTENTION_THRESHOLD_NORM
    from app.retriever import (
        DENSE_MODEL_NAME,
        LEXICAL_RELEVANCE_FLOOR,
        RERANK_ENABLED,
        RERANKER_MODEL_NAME,
        RETRIEVER_DENSE_DEVICE,
        RRF_K,
    )

    return {
        "dense_model": DENSE_MODEL_NAME if h.has_dense else None,
        "dense_device": RETRIEVER_DENSE_DEVICE if h.has_dense else None,
        "reranker": RERANKER_MODEL_NAME if h.has_rerank else None,
        "rerank_enabled_env": RERANK_ENABLED,
        "sparse_only": bool(h.r._sparse_only),
        "rrf_k_env": RRF_K,
        "abstention_threshold_norm": ABSTENTION_THRESHOLD_NORM,
        "lexical_floor": LEXICAL_RELEVANCE_FLOOR,
        "collection": h.collection,
        "flags": {n: flags.is_enabled(n) for n in (
            "translate_retrieve", "corrective_rag", "query_rewrite", "query_decomposition", "hyde",
            "graph_fusion", "agentic_mode", "semantic_cache")},
    }


# --------------------------------------------------------------------------- latency
def cmd_latency(args: argparse.Namespace) -> int:
    isolate_side_effects(Path(args.work))
    rows = [r for r in load_queries(Path(args.queries), {"faq", "native"}, 0) if "+" not in r["variant"]]
    rng = random.Random(20261007)
    picked: list[dict[str, Any]] = []
    for lang in LANGS:
        pool = [r for r in rows if r["lang"] == lang and r["variant"] in ("paraphrase", "translation", "native")]
        rng.shuffle(pool)
        picked.extend(pool[: args.per_lang])
    h = Harness(sparse_only=args.sparse_only, service=args.service > 0)
    # Warm up: model kernels, Qdrant connection, vLLM prefix cache.
    for r in picked[:6]:
        h.production(r["query"], r["lang"], args.top_k)
    service_cold: list[dict[str, Any]] = []
    if args.service:
        # The whole retrieval half of a turn, one question at a time, every
        # cache cleared: routers, the English form, retrieval, gates.
        for lang in LANGS:
            for r in [x for x in picked if x["lang"] == lang][: args.service]:
                h.clear_caches()
                CLOCK.start()
                t0 = time.perf_counter()
                h.service(r["query"])
                service_cold.append({"lang": lang, "total_ms": (time.perf_counter() - t0) * 1000, "stages": CLOCK.take()})
    cold: list[dict[str, Any]] = []
    for r in picked:
        h.clear_caches()
        CLOCK.start()
        t0 = time.perf_counter()
        h.production(r["query"], r["lang"], args.top_k)
        total = (time.perf_counter() - t0) * 1000
        stages = CLOCK.take()
        # The same question again: MT and query-embedding caches now warm.
        t0 = time.perf_counter()
        h.production(r["query"], r["lang"], args.top_k)
        cold.append({"lang": r["lang"], "total_ms": total, "stages": stages,
                     "warm_ms": (time.perf_counter() - t0) * 1000})
    sweep = []
    for workers in [int(x) for x in args.concurrency.split(",")]:
        h.clear_caches()
        lat: list[float] = []

        def call(r: dict[str, Any], lat: list[float] = lat) -> None:
            t = time.perf_counter()
            h.production(r["query"], r["lang"], args.top_k)
            lat.append((time.perf_counter() - t) * 1000)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(call, picked))
        wall = time.perf_counter() - t0
        sweep.append({"workers": workers, "requests": len(picked), "qps": round(len(picked) / wall, 2),
                      "p50_ms": round(pct(lat, 50), 1), "p95_ms": round(pct(lat, 95), 1),
                      "max_ms": round(max(lat), 1)})
        print(json.dumps(sweep[-1]), file=sys.stderr, flush=True)
    precision = precision_check(h, picked[: args.precision_queries]) if args.precision_queries else {}
    out = Path(args.work) / f"latency_{args.config}.json"
    out.write_text(json.dumps({"config": args.config, **env_summary(h), "cold": cold, "sweep": sweep,
                               "precision": precision, "service_cold": service_cold}, indent=1))
    print(f"wrote {out}", file=sys.stderr)
    return 0


def precision_check(h: Harness, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The same models in float16: time per call, and whether the ranking changes.

    Both are loaded in float32 by the app (no ``model_kwargs``); this loads a
    float16 twin on the same device and scores identical inputs.
    """
    import torch
    from app.retriever import (
        DENSE_MODEL_NAME,
        RERANKER_DEVICE,
        RERANKER_MODEL_NAME,
        RETRIEVER_DENSE_DEVICE,
    )
    from sentence_transformers import CrossEncoder, SentenceTransformer

    out: dict[str, Any] = {}
    texts = [h.pivot(r["query"], r["lang"]) if r["lang"] != "en" else r["query"] for r in rows]
    if h.has_dense and str(RETRIEVER_DENSE_DEVICE).startswith("cuda"):
        half = SentenceTransformer(DENSE_MODEL_NAME, device=RETRIEVER_DENSE_DEVICE,
                                   model_kwargs={"torch_dtype": torch.float16})
        t32, t16, cos = [], [], []
        for text in texts:
            t = time.perf_counter()
            a = h.r._dense_model.encode(text, normalize_embeddings=True)
            t32.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            b = half.encode(text, normalize_embeddings=True)
            t16.append((time.perf_counter() - t) * 1000)
            cos.append(float((a * b).sum()))
        out["dense"] = {"fp32_p50_ms": round(pct(t32, 50), 1), "fp16_p50_ms": round(pct(t16, 50), 1),
                        "cosine_fp32_fp16_min": round(min(cos), 5)}
        del half
    if h.has_rerank:
        half = CrossEncoder(RERANKER_MODEL_NAME, device=RERANKER_DEVICE, model_kwargs={"torch_dtype": torch.float16})
        t32, t16, same_top = [], [], []
        for text in texts:
            cands = h.ranked(text, "rrf")
            pairs = [(text, (c.get("text") or "")[:1200]) for c in cands]
            if not pairs:
                continue
            t = time.perf_counter()
            s32 = h.r._reranker.predict(pairs)
            t32.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            s16 = half.predict(pairs)
            t16.append((time.perf_counter() - t) * 1000)
            same_top.append(float(int(max(range(len(pairs)), key=lambda i: s32[i]))
                                  == int(max(range(len(pairs)), key=lambda i: s16[i]))))
        out["rerank"] = {"pairs_per_call": 20, "fp32_p50_ms": round(pct(t32, 50), 1),
                         "fp16_p50_ms": round(pct(t16, 50), 1),
                         "top1_agreement": round(statistics.fmean(same_top), 4) if same_top else None}
        del half
    torch.cuda.empty_cache()
    print(json.dumps({"precision": out}), file=sys.stderr, flush=True)
    return out


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# --------------------------------------------------------------------------- mechanisms
# A verbatim copy of the language-boost block in service.py (generate() step
# 3b2 and its generate_retrieval_only twin), so its effect can be measured on
# its own. If service.py changes, this copy must follow it.
_LOCALE_BOOST_TERMS = {
    "lg": {"luganda", "oluganda", "lg"},
    "sw": {"swahili", "kiswahili", "sw"},
}


def service_language_boost(hits: list[dict[str, Any]], locale: str) -> tuple[list[dict[str, Any]], int]:
    boost_terms = _LOCALE_BOOST_TERMS.get(locale, set())
    boosted = 0
    hits = [dict(h) for h in hits]
    if locale != "en" and hits and boost_terms:
        for h in hits:
            source = (h.get("source") or "").lower()
            text_preview = (h.get("text") or "")[:200].lower()
            if any(t in source or t in text_preview for t in boost_terms):
                h["score_rrf"] = h.get("score_rrf", 0.5) + 0.3
                boosted += 1
        hits.sort(key=lambda x: x.get("score_rrf", 0), reverse=True)
    return hits, boosted


def cmd_mechanisms(args: argparse.Namespace) -> int:
    """Corrective RAG and the language boost, each measured in isolation.

    For each clean faq/native row: the production hits, then what
    ``corrective_retrieve`` does to them (trigger, time, top-1), then what the
    language boost does to its output (passages boosted, top-1 moved).
    """
    from app.corrective_rag import corrective_retrieve, should_correct

    rows = [r for r in load_queries(Path(args.queries), {"faq", "native"}, 0)
            if "+" not in r["variant"] and r["variant"] != "verbatim" and sampled(r["id"], args.sample)]
    h = Harness(sparse_only=False, service=False)
    out = []
    for row in rows:
        judge = Judge(row)
        q, lang = row["query"], row["lang"]
        prod = h.production(q, lang, args.top_k)
        trig = should_correct(prod)
        t0 = time.perf_counter()
        corrected, improved = corrective_retrieve(q, h.r, prod, top_k=args.top_k)
        corr_ms = (time.perf_counter() - t0) * 1000
        boosted_hits, n_boosted = service_language_boost(corrected, lang)
        out.append({
            "id": row["id"], "lang": lang, "set": row["set"],
            "prod_top1": judge.strict(prod[0]) if prod else 0,
            "corrective_triggered": bool(trig), "corrective_improved": bool(improved),
            "corrective_ms": round(corr_ms, 1),
            "after_corrective_top1": judge.strict(corrected[0]) if corrected else 0,
            "boosted": n_boosted,
            "boost_moved_top1": bool(corrected and boosted_hits and passage_key(corrected[0]) != passage_key(boosted_hits[0])),
            "after_boost_top1": judge.strict(boosted_hits[0]) if boosted_hits else 0,
        })
    summary: dict[str, Any] = {"config": args.config, **env_summary(h), "by_lang": {}}
    for lang in LANGS:
        rs = [r for r in out if r["lang"] == lang]
        if not rs:
            continue
        trig = [r for r in rs if r["corrective_triggered"]]
        summary["by_lang"][lang] = {
            "n": len(rs),
            "hit1_production": round(statistics.fmean(r["prod_top1"] for r in rs), 4),
            "corrective_trigger_rate": round(len(trig) / len(rs), 4),
            "corrective_improved_rate": round(statistics.fmean(float(r["corrective_improved"]) for r in rs), 4),
            "corrective_ms_p50_when_triggered": round(pct([r["corrective_ms"] for r in trig], 50), 1) if trig else 0.0,
            "hit1_after_corrective": round(statistics.fmean(r["after_corrective_top1"] for r in rs), 4),
            "mean_passages_boosted": round(statistics.fmean(r["boosted"] for r in rs), 3),
            "boost_moved_top1_rate": round(statistics.fmean(float(r["boost_moved_top1"]) for r in rs), 4),
            "hit1_after_boost": round(statistics.fmean(r["after_boost_top1"] for r in rs), 4),
        }
    path = Path(args.work) / f"mechanisms_{args.config}.json"
    path.write_text(json.dumps({"summary": summary, "rows": out}, indent=1))
    print(json.dumps(summary["by_lang"]), file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- embedders
# Query / passage formats each model was trained with. The E5 instruct family
# wants a task instruction on the query side only.
_E5_INSTRUCT = (
    "Instruct: Given a question from a taxpayer, retrieve passages that answer it\nQuery: {q}",
    "{d}",
)
EMBEDDER_FORMATS: dict[str, tuple[str, str]] = {
    "intfloat/multilingual-e5-large-instruct": _E5_INSTRUCT,
    "McGill-NLP/AfriE5-Large-instruct": _E5_INSTRUCT,
}


def cmd_embedders(args: argparse.Namespace) -> int:
    """Dense retrieval with candidate embedders over the whole corpus, in memory.

    Every model sees the same passages at the same truncation, so the numbers
    compare models, not index builds. ``raw`` is the question as asked;
    ``pivot`` is the English the production retriever searched with (read from
    a ``run`` file), so a model that retrieves Luganda directly can be set
    against translate-then-search.
    """
    import numpy as np
    import torch
    from qdrant_client import QdrantClient
    from sentence_transformers import SentenceTransformer

    client = QdrantClient(url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"))
    collection = os.getenv("QDRANT_COLLECTION", "ura_knowledge_base_jsonl_active")
    docs: list[dict[str, Any]] = []
    offset = None
    while True:
        points, offset = client.scroll(collection, limit=1000, offset=offset, with_payload=PAYLOAD, with_vectors=False)
        for pt in points:
            p = pt.payload or {}
            if p.get("_meta") == "bm25_binding" or not p.get("text"):
                continue
            docs.append({"id": str(pt.id), **{k: p.get(k, "") for k in PAYLOAD if k != "_meta"}})
        if offset is None:
            break
    rows = [r for r in load_queries(Path(args.queries), None, 0) if "+" not in r["variant"]]
    pivots: dict[str, str] = {}
    if args.pivots and Path(args.pivots).exists():
        for line in Path(args.pivots).read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            pivots[rec["id"]] = rec.get("pivot") or ""
    print(json.dumps({"docs": len(docs), "rows": len(rows), "pivots": len(pivots)}), file=sys.stderr)

    for name in args.models.split(","):
        short = name.split("/")[-1].lower()
        qfmt, dfmt = EMBEDDER_FORMATS.get(name, ("{q}", "{d}"))
        model = SentenceTransformer(name, device=args.device, model_kwargs={"torch_dtype": torch.float16})
        model.max_seq_length = args.max_len
        t0 = time.perf_counter()
        doc_vecs = model.encode([dfmt.format(d=d["text"]) for d in docs], batch_size=args.batch,
                                normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        index_s = time.perf_counter() - t0
        views = {"raw": [r["query"] for r in rows],
                 "pivot": [pivots.get(r["id"]) or r["query"] for r in rows]}
        hits_by_view: dict[str, Any] = {}
        for view, texts in views.items():
            q_vecs = model.encode([qfmt.format(q=t) for t in texts], batch_size=args.batch,
                                  normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            sims = q_vecs @ doc_vecs.T
            hits_by_view[view] = np.argsort(-sims, axis=1)[:, :20]
        single = []
        for text in views["raw"][:60]:
            t1 = time.perf_counter()
            model.encode([qfmt.format(q=text)], normalize_embeddings=True, show_progress_bar=False)
            single.append((time.perf_counter() - t1) * 1000)
        out = Path(args.work) / f"raw_emb_{short}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for i, row in enumerate(rows):
                judge = Judge(row)
                systems = {f"emb_{view}": {"hits": compact([docs[j] for j in idx[i]], judge)}
                           for view, idx in hits_by_view.items()}
                rec = {k: row[k] for k in ("id", "set", "lang", "variant", "parent")}
                fh.write(json.dumps({**rec, "config": f"emb_{short}", "systems": systems}, ensure_ascii=False) + "\n")
        meta = {"config": f"emb_{short}", "model": name, "device": args.device, "dtype": "float16",
                "max_seq_length": args.max_len, "index_seconds": round(index_s, 1), "docs": len(docs),
                "query_encode_p50_ms": round(pct(single, 50), 1), "query_encode_p95_ms": round(pct(single, 95), 1),
                "dim": int(doc_vecs.shape[1])}
        (Path(args.work) / f"env_emb_{short}.json").write_text(json.dumps(meta, indent=1))
        print(json.dumps(meta), file=sys.stderr, flush=True)
        del model, doc_vecs
        torch.cuda.empty_cache()
    return 0


# --------------------------------------------------------------------------- judge
UMBRELA = (
    "Given a query and a passage, you must provide a score on an integer scale of 0 to 3 with the following "
    "meanings:\n0 = represent that the passage has nothing to do with the query,\n1 = represents that the passage "
    "seems related to the query but does not answer it,\n2 = represents that the passage has some answer for the "
    "query, but the answer may be a bit unclear, or hidden amongst extraneous information and\n3 = represents that "
    "the passage is dedicated to the query and contains the exact answer.\n\nImportant Instruction: Assign category "
    "1 if the passage is somewhat related to the topic but not completely, category 2 if passage presents something "
    "very important related to the entire topic but also has some extra information and category 3 if the passage "
    "only and entirely refers to the topic. If none of the above satisfies give it category 0.\n\n"
    "Query: {query}\nPassage: {passage}\n\nSplit this problem into steps:\nConsider the underlying intent of the "
    "search.\nMeasure how well the content matches a likely intent of the query (M).\nMeasure how trustworthy the "
    "passage is (T).\nConsider the aspects above and the relative importance of each, and decide on a final score "
    "(O). Final score must be an integer value only.\nDo not provide any code in result. Provide each score in the "
    "format of: ##final score: score without providing any reasoning."
)


def cmd_judge(args: argparse.Namespace) -> int:
    """Grade the pooled top-``--depth`` of every coverage question (all configs, all systems)."""
    import httpx

    rows = {r["id"]: r for r in load_queries(Path(args.queries), {"coverage", "faq"}, 0)}
    pool: dict[str, set[str]] = defaultdict(set)
    texts: dict[str, dict[str, Any]] = {}
    for raw in sorted(Path(args.work).glob("raw_*.jsonl")):
        for line in raw.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["set"] != "coverage":
                continue
            for sysrec in rec["systems"].values():
                for hit in sysrec["hits"][: args.depth]:
                    pool[rec["parent"]].add(hit["key"])
    from app.retriever import QDRANT_COLLECTION, QDRANT_URL
    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT_URL)
    ids = sorted({k for keys in pool.values() for k in keys if not k.startswith("nid:")})
    for i in range(0, len(ids), 256):
        for pt in client.retrieve(QDRANT_COLLECTION, ids=ids[i:i + 256], with_payload=["text", "question", "answer"]):
            texts[str(pt.id)] = pt.payload or {}
    english = {r["parent"]: r["query"] for r in rows.values() if r["set"] == "coverage" and r["lang"] == "en"}
    tasks = [(parent, key) for parent, keys in pool.items() for key in sorted(keys) if key in texts]

    # Judge validation: gold FAQ passages (expect >= 2) and passages from an
    # unrelated tag (expect <= 1), drawn from the faq set.
    faq_rows = [r for r in rows.values() if r["set"] == "faq" and r["variant"] == "verbatim"]
    rng = random.Random(20261007)
    rng.shuffle(faq_rows)
    checks: list[tuple[str, str, str, int]] = []
    gold_payloads = {str(pt.id): pt.payload or {} for pt in client.retrieve(
        QDRANT_COLLECTION, ids=[r["gold_ids"][0] for r in faq_rows[: args.validate]], with_payload=["text"])}
    for r in faq_rows[: args.validate]:
        checks.append((r["query"], (gold_payloads.get(r["gold_ids"][0]) or {}).get("text", ""), "pos", 1))
        other = next(o for o in faq_rows if o["tag"] != r["tag"])
        faq_rows.append(faq_rows.pop(faq_rows.index(other)))
        checks.append((r["query"], other.get("gold_question", "") + "\n" + other.get("gold_answer", ""), "neg", 0))

    def grade(query: str, passage: str) -> int:
        body = {"model": args.model, "temperature": 0.0, "max_tokens": 64,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "user", "content": UMBRELA.format(query=query, passage=passage[: args.passage_chars])}]}
        for attempt in range(3):
            try:
                out = httpx.post(f"{args.llm}/chat/completions", json=body, timeout=120).json()
                text = out["choices"][0]["message"]["content"] or ""
                m = re.findall(r"final score\W*([0-3])", text, flags=re.I) or re.findall(r"\b([0-3])\b", text[-40:])
                return int(m[-1]) if m else 0
            except Exception:  # noqa: BLE001
                time.sleep(2 * (attempt + 1))
        return -1

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        grades = list(ex.map(lambda t: grade(english[t[0]], (texts[t[1]].get("text") or "")), tasks))
        validation = list(ex.map(lambda c: grade(c[0], c[1]), checks))
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    for (parent, key), g in zip(tasks, grades, strict=True):
        qrels[parent][key] = g
    out = Path(args.qrels)
    with out.open("w", encoding="utf-8") as fh:
        for parent in sorted(qrels):
            fh.write(json.dumps({"parent": parent, "query_en": english.get(parent, ""), "grades": qrels[parent]},
                                ensure_ascii=False) + "\n")
    pos = [g for (_, _, kind, _), g in zip(checks, validation, strict=True) if kind == "pos"]
    neg = [g for (_, _, kind, _), g in zip(checks, validation, strict=True) if kind == "neg"]
    summary = {
        "judged": len(tasks), "questions": len(qrels),
        "answerable": sum(1 for g in qrels.values() if max(g.values(), default=0) >= COVERAGE_STRICT_GRADE),
        "validation": {"gold_ge2": round(sum(g >= 2 for g in pos) / max(1, len(pos)), 3),
                       "unrelated_le1": round(sum(0 <= g <= 1 for g in neg) / max(1, len(neg)), 3),
                       "n": len(pos)},
    }
    (Path(args.work) / "judge_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary), file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- report
def boot_ci(values: list[float], n: int = 1000, seed: int = 7) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return (round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4))


def mcnemar_p(a: list[float], b: list[float]) -> float:
    """Exact two-sided McNemar test on paired binary outcomes."""
    b10 = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    b01 = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    n = b10 + b01
    if n == 0:
        return 1.0
    k = min(b10, b01)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return round(min(1.0, 2 * tail), 6)


def auroc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def cmd_report(args: argparse.Namespace) -> int:
    work = Path(args.work)
    qrels: dict[str, dict[str, int]] = {}
    qpath = Path(args.qrels)
    if qpath.exists():
        for line in qpath.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            qrels[item["parent"]] = {k: v for k, v in item["grades"].items() if v >= 0}
    rows = {r["id"]: r for r in load_queries(Path(args.queries), None, 0)}
    # The query set is pinned by the commit that holds it, not by a hash here.
    report: dict[str, Any] = {"generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                              "queries": len(rows), "configs": {}}
    for raw in sorted(work.glob("raw_*.jsonl")):
        config = raw.stem.removeprefix("raw_")
        env_path = work / f"env_{config}.json"
        recs = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
        report["configs"][config] = summarise(recs, rows, qrels,
                                              json.loads(env_path.read_text()) if env_path.exists() else {})
    for lat in sorted(work.glob("latency_*.json")):
        data = json.loads(lat.read_text())
        report["configs"].setdefault(data["config"], {})["latency"] = summarise_latency(data)
    for mech in sorted(work.glob("mechanisms_*.json")):
        summary = json.loads(mech.read_text())["summary"]
        report["configs"].setdefault(summary["config"], {})["mechanisms"] = summary["by_lang"]
    judge_summary = work / "judge_summary.json"
    if judge_summary.exists():
        report["judge"] = json.loads(judge_summary.read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Compact: the table has thousands of cells, and the markdown report is
    # what people read.
    out.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {out}", file=sys.stderr)
    return 0


def summarise(recs: list[dict[str, Any]], rows: dict[str, dict[str, Any]],
              qrels: dict[str, dict[str, int]], env: dict[str, Any]) -> dict[str, Any]:
    cells: dict[tuple[str, ...], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    paired: dict[tuple[str, ...], dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    abst: dict[tuple[str, ...], list[tuple[float | None, bool, bool, str]]] = defaultdict(list)
    pivot: dict[str, list[float]] = defaultdict(list)
    pivot_same: dict[str, list[float]] = defaultdict(list)
    service: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rec in recs:
        row = rows.get(rec["id"])
        if row is None:
            continue
        judge = Judge(row, qrels)
        family = rec["variant"].split("+")[1] if "+" in rec["variant"] else "clean"
        # Each row counts in its own variant ("paraphrase", "codeswitch", ...)
        # and in its coarse family (clean / typo / asr): English "clean" mixes
        # verbatim questions, which BM25 all but always wins, with paraphrases.
        groups = [family] if family == rec["variant"] else [family, rec["variant"]]
        if rec["lang"] != "en" and rec.get("pivot_f1") is not None:
            pivot[rec["lang"]].append(rec["pivot_f1"])
            pivot_same[rec["lang"]].append(float(bool(rec.get("pivot_unchanged"))))
        for name, sysrec in rec["systems"].items():
            hits = sysrec["hits"]
            # Lenient relevance only where served-context precision is read.
            for kind in ("strict", "lenient") if name in ("production", "service") else ("strict",):
                if not judge.has_gold:
                    continue
                if row["set"] == "coverage":
                    labels = [int((judge.graded or {}).get(h["key"], 0) >= COVERAGE_STRICT_GRADE) for h in hits]
                    if kind == "lenient":
                        labels = [int((judge.graded or {}).get(h["key"], 0) >= COVERAGE_LENIENT_GRADE) for h in hits]
                else:
                    labels = [int(h[kind] or 0) for h in hits]
                metrics = rank_metrics(labels)
                if row["set"] == "coverage" and kind == "strict":
                    ideal = list((judge.graded or {}).values())
                    metrics["ndcg10_graded"] = graded_ndcg([(judge.graded or {}).get(h["key"], 0) for h in hits], ideal)
                if row["set"] == "coverage" and not any(
                        v >= COVERAGE_STRICT_GRADE for v in (judge.graded or {}).values()):
                    continue
                for group in groups:
                    key = (row["set"], rec["lang"], group, name, kind)
                    for m, v in metrics.items():
                        cells[key][m].append(v)
                    if name in ("production", "service"):
                        cells[key]["served"].append(float(len(hits)))
                        cells[key]["precision_served"].append(sum(labels) / len(labels) if labels else 0.0)
                    paired[(row["set"], rec["lang"], group, kind)][name][rec["id"]] = metrics["hit1"]
            if name in ("production", "service") and "abstain" in sysrec:
                # "Answerable" = the top passage handed on is relevant; an OOD
                # question never is. Abstaining on the first is a lost answer,
                # answering the second is an answer from the wrong passage.
                top = hits[0] if hits else None
                if row["set"] == "ood" or top is None:
                    answerable = False
                elif row["set"] == "coverage":
                    answerable = (judge.graded or {}).get(top["key"], 0) >= COVERAGE_STRICT_GRADE
                else:
                    answerable = bool(top["strict"])
                abst[(name, rec["lang"], family)].append(
                    (sysrec.get("signal"), bool(sysrec["abstain"]), answerable, row["set"]))
            if name == "service":
                svc = service[(row["set"], rec["lang"])]
                svc["ms"].append(sysrec["ms"])
                svc["abstain"].append(float(sysrec["abstain"]))
                svc["locale_ok"].append(float(sysrec.get("locale") == rec["lang"]))
                svc["error"].append(float(bool(sysrec.get("error"))))
                svc["no_hits"].append(float(not hits))
                # Guard routes (greeting, jurisdiction, blocked) deflect without
                # abstaining; only passages handed to the answer step count.
                svc["answered_with_hits"].append(
                    float(bool(hits) and sysrec.get("mode") not in ("abstained", "clarification")))
                svc.setdefault("modes", []).append(sysrec.get("mode", ""))  # type: ignore[arg-type]

    table = []
    for key in sorted(cells):
        set_, lang, family, name, kind = key
        vals = cells[key]
        entry: dict[str, Any] = {"set": set_, "lang": lang, "family": family, "system": name, "relevance": kind,
                                 "n": len(vals["hit1"])}
        for m, v in vals.items():
            entry[m] = round(statistics.fmean(v), 4)
        entry["hit1_ci95"] = boot_ci(vals["hit1"])
        entry["ndcg10_ci95"] = boot_ci(vals["ndcg10"])
        table.append(entry)

    comparisons = []
    for key, by_system in paired.items():
        if key[3] != "strict":
            continue
        for a, b in (("production", "pivot_hybrid_rerank"), ("production", "pivot_hybrid"), ("hybrid", "pivot_hybrid"),
                     ("pivot_hybrid", "pivot_rrf60"), ("pivot_hybrid", "pivot_dbsf"), ("hybrid", "hybrid_rerank"),
                     ("pivot_hybrid", "pivot_hybrid_rerank"), ("production", "service"), ("bm25", "dense")):
            if a in by_system and b in by_system:
                ids = sorted(set(by_system[a]) & set(by_system[b]))
                if len(ids) < 10:
                    continue
                xa = [by_system[a][i] for i in ids]
                xb = [by_system[b][i] for i in ids]
                comparisons.append({"set": key[0], "lang": key[1], "family": key[2], "relevance": key[3], "a": a, "b": b,
                                    "n": len(ids), "hit1_a": round(statistics.fmean(xa), 4),
                                    "hit1_b": round(statistics.fmean(xb), 4), "mcnemar_p": mcnemar_p(xa, xb)})

    abstention = []
    for (name, lang, family), items in sorted(abst.items()):
        if family != "clean":
            continue
        ood = [x for x in items if x[3] == "ood"]
        good = [x for x in items if x[2]]
        abstention.append({
            "system": name, "lang": lang,
            "ood_n": len(ood), "ood_answered": round(statistics.fmean([0.0 if x[1] else 1.0 for x in ood]), 4) if ood else None,
            "answerable_n": len(good),
            "answerable_abstained": round(statistics.fmean([1.0 if x[1] else 0.0 for x in good]), 4) if good else None,
            "signal_auroc_answerable_vs_ood": auroc([x[0] for x in good if x[0] is not None],
                                                    [x[0] for x in ood if x[0] is not None]),
            "ood_signal_median": round(statistics.median([x[0] for x in ood if x[0] is not None]), 4)
            if any(x[0] is not None for x in ood) else None,
            "answerable_signal_median": round(statistics.median([x[0] for x in good if x[0] is not None]), 4)
            if any(x[0] is not None for x in good) else None,
        })

    service_table = []
    for (set_, lang), vals in sorted(service.items()):
        modes = vals.pop("modes", [])  # type: ignore[arg-type]
        mode_counts: dict[str, int] = defaultdict(int)
        for m in modes:
            mode_counts[str(m)] += 1
        service_table.append({"set": set_, "lang": lang, "n": len(vals["ms"]),
                              **{k: round(statistics.fmean(v), 4) for k, v in vals.items() if k != "ms"},
                              "p50_ms": round(pct(vals["ms"], 50), 1), "p95_ms": round(pct(vals["ms"], 95), 1),
                              "modes": dict(sorted(mode_counts.items(), key=lambda kv: -kv[1]))})
    return {
        "env": env, "table": table, "comparisons": comparisons, "abstention": abstention, "service": service_table,
        "pivot": {lang: {"n": len(v), "token_f1_mean": round(statistics.fmean(v), 4),
                         "unchanged_rate": round(statistics.fmean(pivot_same[lang]), 4)} for lang, v in pivot.items()},
    }


def summarise_latency(data: dict[str, Any]) -> dict[str, Any]:
    """Cold and warm totals per language, with each stage's median and call count."""
    out: dict[str, Any] = {"sweep": data["sweep"], "cold": {}, "warm": {}, "precision": data.get("precision", {})}
    for lang in LANGS:
        rows = [c for c in data["cold"] if c["lang"] == lang]
        if not rows:
            continue
        cold: dict[str, Any] = {"n": len(rows), "p50_ms": round(pct([r["total_ms"] for r in rows], 50), 1),
                                "p95_ms": round(pct([r["total_ms"] for r in rows], 95), 1)}
        for stage in STAGES:
            cold[f"{stage}_p50_ms"] = round(pct([r["stages"]["ms"][stage] for r in rows], 50), 1)
            cold[f"{stage}_p95_ms"] = round(pct([r["stages"]["ms"][stage] for r in rows], 95), 1)
            cold[f"{stage}_calls"] = round(statistics.fmean([r["stages"]["calls"][stage] for r in rows]), 2)
        out["cold"][lang] = cold
        out["warm"][lang] = {"p50_ms": round(pct([r["warm_ms"] for r in rows], 50), 1),
                             "p95_ms": round(pct([r["warm_ms"] for r in rows], 95), 1)}
        svc = [s for s in data.get("service_cold", []) if s["lang"] == lang]
        if svc:
            entry: dict[str, Any] = {"n": len(svc), "p50_ms": round(pct([s["total_ms"] for s in svc], 50), 1),
                                     "p95_ms": round(pct([s["total_ms"] for s in svc], 95), 1)}
            for stage in STAGES:
                entry[f"{stage}_p50_ms"] = round(pct([s["stages"]["ms"][stage] for s in svc], 50), 1)
                entry[f"{stage}_calls"] = round(statistics.fmean([s["stages"]["calls"][stage] for s in svc]), 2)
            out.setdefault("service_cold", {})[lang] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--queries", default=str(QUERIES))
    common.add_argument("--qrels", default=str(QRELS), help="judged coverage pool (written by judge, read by report)")
    common.add_argument("--work", default=str(HERE))
    common.add_argument("--config", default="as_deployed")
    common.add_argument("--top-k", type=int, default=6)
    common.add_argument("--sparse-only", action="store_true")
    run = sub.add_parser("run", parents=[common])
    run.add_argument("--sets", default="")
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--no-service", action="store_true")
    run.add_argument("--service-sets", default="faq,native,coverage,ood")
    run.add_argument("--service-sample", type=float, default=1.0, help="share of faq rows also run end to end")
    run.add_argument("--sample-perturbed", type=float, default=1.0, help="share of typo/asr rows kept")
    run.add_argument("--resume", action="store_true", help="append, skipping ids already in the raw file")
    lat = sub.add_parser("latency", parents=[common])
    lat.add_argument("--per-lang", type=int, default=40)
    lat.add_argument("--concurrency", default="1,4,8,16")
    lat.add_argument("--precision-queries", type=int, default=40, help="float16 comparison; 0 skips it")
    lat.add_argument("--service", type=int, default=0, help="also time the service path for N questions per language")
    jd = sub.add_parser("judge", parents=[common])
    jd.add_argument("--llm", default="http://vllm:8001/v1")
    jd.add_argument("--model", default="Sunbird/Sunflower-14B-FP8")
    jd.add_argument("--workers", type=int, default=24)
    jd.add_argument("--validate", type=int, default=60)
    jd.add_argument("--depth", type=int, default=5, help="pool depth per system (deeper ranks count as unjudged)")
    jd.add_argument("--passage-chars", type=int, default=1200, help="the reranker's own view of a passage")
    mech = sub.add_parser("mechanisms", parents=[common])
    mech.add_argument("--sample", type=float, default=0.5, help="share of clean faq/native rows")
    emb = sub.add_parser("embedders", parents=[common])
    emb.add_argument("--models", default="BAAI/bge-m3,intfloat/multilingual-e5-large-instruct,"
                                          "McGill-NLP/AfriE5-Large-instruct")
    emb.add_argument("--pivots", default="", help="a run file whose pivots to score as the pivot view")
    emb.add_argument("--device", default="cuda:0")
    emb.add_argument("--batch", type=int, default=64)
    emb.add_argument("--max-len", type=int, default=512)
    rep = sub.add_parser("report", parents=[common])
    rep.add_argument("--out", default=str(HERE.parent / "evals" / "reports"
                                          / f"retrieval_multilingual_{dt.date.today().isoformat()}.json"))
    args = ap.parse_args()
    commands = {"run": cmd_run, "latency": cmd_latency, "mechanisms": cmd_mechanisms, "embedders": cmd_embedders,
                "judge": cmd_judge, "report": cmd_report}
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
