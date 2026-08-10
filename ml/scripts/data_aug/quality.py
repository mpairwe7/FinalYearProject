"""Quality filtering stage.

Filters (each independently toggleable via :class:`QualityConfig`):
    * tokenizer-aware length bounds
    * non-ASCII / mojibake ratio (symbol_ratio threshold)
    * n-gram repetition ratio
    * language ID (expects ``en`` or ``lg`` depending on source_type)
    * per-source caps (prevent any one source from dominating)

A tokenizer is loaded once and re-used across the stage to avoid
reinitialising. If transformers is unavailable we fall back to a
whitespace count as a conservative proxy — the pipeline still runs.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ml.scripts.data_aug.schema import SourceType, TaskType, TrainingExample
from ml.scripts.data_aug.text_utils import repetition_ratio, symbol_ratio

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)


@dataclass
class QualityConfig:
    min_tokens: int = 8
    max_tokens: int = 2048  # hard cap matches web_high_accuracy max_seq_length
    max_symbol_ratio: float = 0.35
    max_repetition_ratio: float = 0.25
    tokenizer_model: str | None = "google/gemma-2-2b-it"
    source_caps: dict[str, int] = field(default_factory=dict)
    drop_short_responses_below: int = 4  # words
    min_user_chars: int = 3

    # Optional FineWeb-Edu style learned quality classifier. When set, we
    # load the classifier from disk and drop rows below ``quality_threshold``.
    # See ``ml/scripts/train_quality_classifier.py`` for training.
    quality_classifier_path: Path | None = None
    quality_threshold: float | None = None  # overrides the model's own default


@dataclass
class QualityStats:
    input_count: int = 0
    dropped_length: int = 0
    dropped_symbol: int = 0
    dropped_repetition: int = 0
    dropped_capped: int = 0
    dropped_short_response: int = 0
    dropped_classifier: int = 0
    classifier_report: dict[str, Any] | None = None
    output_count: int = 0

    def as_dict(self) -> dict:
        out = {
            "input_count": self.input_count,
            "dropped_length": self.dropped_length,
            "dropped_symbol": self.dropped_symbol,
            "dropped_repetition": self.dropped_repetition,
            "dropped_capped": self.dropped_capped,
            "dropped_short_response": self.dropped_short_response,
            "dropped_classifier": self.dropped_classifier,
            "output_count": self.output_count,
        }
        if self.classifier_report is not None:
            out["classifier"] = self.classifier_report
        return out


# ---------------------------------------------------------------------------
# Tokenizer loader (cached)
# ---------------------------------------------------------------------------


class _TokenCounter:
    """Lazy tokenizer wrapper. Counts tokens for the full (system+user+assistant)
    sequence as it will appear at training time — this is what the fine-tuner's
    ``max_seq_length`` applies to."""

    def __init__(self, model_id: str | None):
        self._model_id = model_id
        self._tokenizer = None
        self._fallback = False

    def _load(self):
        if self._tokenizer is not None or self._fallback:
            return
        if not self._model_id:
            self._fallback = True
            return
        try:
            from transformers import AutoTokenizer  # lazy

            self._tokenizer = AutoTokenizer.from_pretrained(
                self._model_id, use_fast=True, trust_remote_code=False
            )
            log.info("quality: loaded tokenizer %s", self._model_id)
        except Exception as exc:
            log.warning(
                "quality: could not load tokenizer %s (%s); using whitespace fallback",
                self._model_id,
                exc,
            )
            self._fallback = True

    def count(self, example: TrainingExample) -> int:
        self._load()
        text = "\n".join(m.content for m in example.messages)
        if self._fallback or self._tokenizer is None:
            # whitespace approx: ~1.3 tokens per whitespace-word
            return int(len(text.split()) * 1.3) + 2
        try:
            return len(self._tokenizer(text, add_special_tokens=False)["input_ids"])
        except Exception:
            return int(len(text.split()) * 1.3) + 2


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _is_short_response(ex: TrainingExample, min_words: int) -> bool:
    return len(ex.assistant_text().split()) < min_words


def _passes_symbol_filter(ex: TrainingExample, max_ratio: float) -> bool:
    # Apply to assistant text only — user text can contain raw PDF tables
    # which are legitimately symbol-heavy.
    return symbol_ratio(ex.assistant_text()) <= max_ratio


def _passes_repetition_filter(ex: TrainingExample, max_ratio: float) -> bool:
    text = ex.user_text() + " " + ex.assistant_text()
    return repetition_ratio(text, n=4) <= max_ratio


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _maybe_load_classifier(config: QualityConfig):
    """Load the learned quality classifier if configured, else return None.

    Failure to load is non-fatal: the pipeline logs a warning and continues
    without the classifier filter. This matches how the rest of the
    pipeline handles optional deps and is important for CI where
    sentence-transformers may be unavailable.
    """
    path = config.quality_classifier_path
    if path is None:
        return None
    if not Path(path).exists():
        log.warning("quality: classifier path does not exist: %s", path)
        return None
    try:
        from ml.scripts.data_aug.quality_classifier import QualityClassifier

        clf = QualityClassifier.load(Path(path))
        if config.quality_threshold is not None:
            clf.threshold = config.quality_threshold
        return clf
    except Exception as exc:
        log.warning("quality: could not load classifier %s: %s", path, exc)
        return None


def filter_quality(
    examples: Iterable[TrainingExample],
    config: QualityConfig,
) -> tuple[list[TrainingExample], QualityStats]:
    """Apply all quality filters and return (kept, stats).

    Stages (each optional, in order):
        1. Heuristic pre-filter (length / symbol / repetition / caps)
        2. Learned quality classifier (FineWeb-Edu style) — optional

    Heuristic stage runs first because it's cheap; it drops the 10-30% of
    rows that no classifier could rescue anyway, which keeps the embedding
    cost of stage 2 proportional to useful data.

    Token counts are written back into ``metadata.token_count`` so the
    provenance manifest and downstream diagnostics get them for free.
    """
    counter = _TokenCounter(config.tokenizer_model)
    stats = QualityStats()
    per_source: Counter[str] = Counter()
    kept: list[TrainingExample] = []

    for ex in examples:
        stats.input_count += 1

        if len(ex.user_text()) < config.min_user_chars:
            stats.dropped_length += 1
            continue

        # Skip short-response filter for retrieval/corpus where assistant
        # text is intentionally brief citations.
        if ex.metadata.task not in {TaskType.RETRIEVAL_QA, TaskType.CORPUS} and _is_short_response(
            ex, config.drop_short_responses_below
        ):
            stats.dropped_short_response += 1
            continue

        tok = counter.count(ex)
        ex.metadata.token_count = tok
        if tok < config.min_tokens or tok > config.max_tokens:
            stats.dropped_length += 1
            continue

        # Symbol ratio — skip for corpus / retrieval where tables are
        # legitimately symbol-heavy.
        if ex.metadata.source_type not in {
            SourceType.PDF_CORPUS,
            SourceType.RETRIEVAL,
        } and not _passes_symbol_filter(ex, config.max_symbol_ratio):
            stats.dropped_symbol += 1
            continue

        if not _passes_repetition_filter(ex, config.max_repetition_ratio):
            stats.dropped_repetition += 1
            continue

        # Per-source cap.
        cap_key = ex.metadata.source_type.value
        cap = config.source_caps.get(cap_key)
        if cap is not None and per_source[cap_key] >= cap:
            stats.dropped_capped += 1
            continue
        per_source[cap_key] += 1

        kept.append(ex)

    # Stage 2: learned classifier (opt-in).
    clf = _maybe_load_classifier(config)
    if clf is not None and kept:
        # Rows that the classifier does not want to score (wrong language,
        # corpus-only) are passed through untouched. We only score examples
        # where the assistant is natural language QA-like content.
        scorable_idx: list[int] = []
        scorable_ex: list[TrainingExample] = []
        for i, ex in enumerate(kept):
            if ex.metadata.source_type in {
                SourceType.PDF_CORPUS,
                SourceType.RETRIEVAL,
                SourceType.LUGANDA_PARALLEL,
            }:
                continue
            scorable_idx.append(i)
            scorable_ex.append(ex)

        if scorable_ex:
            filtered_scored, report = clf.score_and_filter(scorable_ex)
            kept_hashes = {ex.metadata.content_hash for ex in filtered_scored}
            new_kept: list[TrainingExample] = []
            for i, ex in enumerate(kept):
                if i in scorable_idx:
                    if ex.metadata.content_hash in kept_hashes:
                        new_kept.append(ex)
                    else:
                        stats.dropped_classifier += 1
                else:
                    new_kept.append(ex)
            kept = new_kept
            stats.classifier_report = report.as_dict()
        else:
            stats.classifier_report = {
                "total": 0,
                "kept": 0,
                "dropped": 0,
                "note": "no rows passed source filter for scoring",
            }

    stats.output_count = len(kept)
    return kept, stats
