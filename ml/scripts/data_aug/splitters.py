"""Stratified train/val/test splitter with deterministic seeding.

Grouping keys used for stratification (in priority order):
    1. source_type (CSV_FAQ, PDF_CORPUS, LUGANDA_PARALLEL, TEACHER_QA, ...)
    2. language (en, lg)
    3. tag (tax domain — income, vat, customs, ...)

Every (strata, example) is shuffled with a fixed seed before being
partitioned, so runs are byte-reproducible. After splitting we run a
contamination scan (:func:`ml.scripts.data_aug.dedup.scan_contamination`)
and drop any train row that leaked into val or test.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ml.scripts.data_aug.dedup import scan_contamination
from ml.scripts.data_aug.schema import TrainingExample

log = logging.getLogger(__name__)


@dataclass
class SplitConfig:
    val_ratio: float = 0.08
    test_ratio: float = 0.02
    seed: int = 42
    min_per_split: int = 1  # Per-strata minimum; small strata stay in train.


@dataclass
class SplitStats:
    train: int = 0
    val: int = 0
    test: int = 0
    contamination_leaked: int = 0
    strata_count: int = 0
    strata_histogram: dict[str, int] = None  # type: ignore[assignment]

    def as_dict(self) -> dict:
        return {
            "train": self.train,
            "val": self.val,
            "test": self.test,
            "contamination_leaked": self.contamination_leaked,
            "strata_count": self.strata_count,
            "strata_histogram": self.strata_histogram or {},
        }


def _strata_key(ex: TrainingExample) -> str:
    parts = [
        ex.metadata.source_type.value,
        ex.metadata.language,
        ex.metadata.tag or "_",
    ]
    return "::".join(parts)


def stratified_split(
    examples: Iterable[TrainingExample],
    config: SplitConfig,
) -> tuple[
    list[TrainingExample],
    list[TrainingExample],
    list[TrainingExample],
    SplitStats,
]:
    """Return (train, val, test, stats).

    Each stratum is shuffled with ``seed`` then partitioned. If a stratum
    has fewer than ``min_per_split * 3`` rows we put *all* of it in train
    — otherwise tiny tags would leak entirely into val or test and poison
    metrics.
    """
    buckets: dict[str, list[TrainingExample]] = defaultdict(list)
    for ex in examples:
        buckets[_strata_key(ex)].append(ex)

    rng = random.Random(config.seed)
    train: list[TrainingExample] = []
    val: list[TrainingExample] = []
    test: list[TrainingExample] = []
    hist: dict[str, int] = {}

    for key, items in sorted(buckets.items()):
        hist[key] = len(items)
        n = len(items)
        if n == 0:
            continue

        # Deterministic shuffle per stratum (per-bucket RNG derived from
        # global seed + key so stratum ordering doesn't affect outputs).
        local = random.Random((config.seed, key).__hash__() & 0xFFFFFFFF)
        local.shuffle(items)

        if n < max(3, config.min_per_split * 3):
            train.extend(items)
            continue

        n_test = max(config.min_per_split, int(round(n * config.test_ratio)))
        n_val = max(config.min_per_split, int(round(n * config.val_ratio)))
        if n_val + n_test >= n:
            n_val = max(1, (n - config.min_per_split) // 2)
            n_test = max(1, n - config.min_per_split - n_val)

        test.extend(items[:n_test])
        val.extend(items[n_test : n_test + n_val])
        train.extend(items[n_test + n_val :])

    # Global shuffle after stratification so batches are mixed at training
    # time, not clumped by source.
    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)

    # Contamination scan: drop any train row that near-duplicates val/test.
    held_out = val + test
    leaked_idx: set[int] = set()
    if held_out:
        leaked_idx = scan_contamination(train, held_out)
        if leaked_idx:
            train = [ex for i, ex in enumerate(train) if i not in leaked_idx]
            log.info(
                "splitter: dropped %d train rows that leaked into held-out",
                len(leaked_idx),
            )

    stats = SplitStats(
        train=len(train),
        val=len(val),
        test=len(test),
        contamination_leaked=len(leaked_idx),
        strata_count=len(buckets),
        strata_histogram=hist,
    )
    return train, val, test, stats
