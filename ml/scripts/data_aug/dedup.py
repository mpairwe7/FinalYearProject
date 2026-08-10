"""Deduplication stage.

Three layers:
    1. Exact hash on (user + assistant) normalised text.
    2. MinHash LSH (near-duplicate removal — Llama / FineWeb standard).
    3. Cross-split contamination scan (train ↔ val ↔ test).

Layer 2 uses ``datasketch`` (already in requirements.txt). When absent we
fall back to exact-only and log a warning — the pipeline still runs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ml.scripts.data_aug.schema import TrainingExample

log = logging.getLogger(__name__)

try:
    from datasketch import MinHash, MinHashLSH  # type: ignore

    _HAS_DATASKETCH = True
except ImportError:  # pragma: no cover - optional
    _HAS_DATASKETCH = False
    log.warning("datasketch not available — MinHash near-dup disabled")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class DedupStats:
    input_count: int = 0
    exact_removed: int = 0
    near_removed: int = 0
    contamination_removed: int = 0
    output_count: int = 0
    by_source: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "input_count": self.input_count,
            "exact_removed": self.exact_removed,
            "near_removed": self.near_removed,
            "contamination_removed": self.contamination_removed,
            "output_count": self.output_count,
            "by_source": self.by_source,
        }


# ---------------------------------------------------------------------------
# MinHash utilities
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _shingles(text: str, n: int = 5) -> set[bytes]:
    """Produce n-gram shingles (word-level). Empty set for short text."""
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < n:
        return {b" ".join(t.encode("utf-8") for t in tokens)} if tokens else set()
    return {
        b" ".join(t.encode("utf-8") for t in tokens[i : i + n]) for i in range(len(tokens) - n + 1)
    }


def _build_minhash(text: str, num_perm: int = 128, n: int = 5):
    """Build a MinHash signature for the given text. None if unavailable."""
    if not _HAS_DATASKETCH:
        return None
    mh = MinHash(num_perm=num_perm)
    for s in _shingles(text, n=n):
        mh.update(s)
    return mh


# ---------------------------------------------------------------------------
# Exact dedup
# ---------------------------------------------------------------------------


def exact_dedup(
    examples: Iterable[TrainingExample],
    stats: DedupStats,
) -> Iterator[TrainingExample]:
    """Yield only one example per unique content hash.

    Priority: first-seen wins. Upstream loaders are ordered so that
    higher-quality sources (CSV FAQs, teacher QA) appear first and survive
    deduplication against weaker sources.
    """
    seen: set[str] = set()
    for ex in examples:
        stats.input_count += 1
        h = ex.metadata.content_hash
        if h in seen:
            stats.exact_removed += 1
            continue
        seen.add(h)
        yield ex


# ---------------------------------------------------------------------------
# Near-duplicate dedup (MinHash LSH)
# ---------------------------------------------------------------------------


def near_dedup(
    examples: Iterable[TrainingExample],
    stats: DedupStats,
    *,
    threshold: float = 0.85,
    num_perm: int = 128,
    shingle_n: int = 5,
) -> Iterator[TrainingExample]:
    """Remove near-duplicates whose Jaccard similarity > ``threshold``.

    Runs after :func:`exact_dedup`. Disabled (pass-through) if datasketch
    is not installed.
    """
    if not _HAS_DATASKETCH:
        yield from examples
        return

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    kept = 0
    for ex in examples:
        payload = ex.user_text() + "\n" + ex.assistant_text()
        mh = _build_minhash(payload, num_perm=num_perm, n=shingle_n)
        if mh is None:
            yield ex
            kept += 1
            continue
        if lsh.query(mh):
            stats.near_removed += 1
            continue
        key = f"ex{kept}"
        lsh.insert(key, mh)
        kept += 1
        yield ex


# ---------------------------------------------------------------------------
# Contamination scan
# ---------------------------------------------------------------------------


def scan_contamination(
    train: list[TrainingExample],
    held_out: list[TrainingExample],
    *,
    threshold: float = 0.85,
    num_perm: int = 128,
    shingle_n: int = 5,
) -> set[int]:
    """Return indices in ``train`` that near-duplicate any held-out example.

    Builds an LSH over the held-out set (val + test) then queries each train
    example. The caller drops the returned indices from train.
    """
    if not _HAS_DATASKETCH:
        # Fall back to exact-hash comparison.
        holdout_hashes = {ex.metadata.content_hash for ex in held_out}
        return {i for i, ex in enumerate(train) if ex.metadata.content_hash in holdout_hashes}

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    for i, ex in enumerate(held_out):
        payload = ex.user_text() + "\n" + ex.assistant_text()
        mh = _build_minhash(payload, num_perm=num_perm, n=shingle_n)
        if mh is not None:
            lsh.insert(f"h{i}", mh)

    leaked: set[int] = set()
    for i, ex in enumerate(train):
        payload = ex.user_text() + "\n" + ex.assistant_text()
        mh = _build_minhash(payload, num_perm=num_perm, n=shingle_n)
        if mh is not None and lsh.query(mh):
            leaked.add(i)
    return leaked


# ---------------------------------------------------------------------------
# Convenience: full pipeline
# ---------------------------------------------------------------------------


def dedup_all(
    examples: Iterable[TrainingExample],
    *,
    near_threshold: float = 0.85,
) -> tuple[list[TrainingExample], DedupStats]:
    """Run exact then near dedup. Returns (kept, stats).

    Materialises the stream into a list because downstream stages need it;
    the loader pipeline is the only step that really benefits from
    streaming and it feeds into this function anyway.
    """
    stats = DedupStats()
    stage_exact = exact_dedup(examples, stats)
    stage_near = near_dedup(stage_exact, stats, threshold=near_threshold)
    kept = list(stage_near)
    stats.output_count = len(kept)
    for ex in kept:
        key = ex.metadata.source_type.value
        stats.by_source[key] = stats.by_source.get(key, 0) + 1
    return kept, stats
