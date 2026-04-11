"""Educational-quality classifier (FineWeb-Edu style) for the URA dataset.

The 2024 FineWeb-Edu approach (HuggingFace) trains a small classifier head
on top of frozen embeddings to produce a 0-5 educational-quality score,
then filters at a threshold. We follow the same architecture:

    sentence-transformers(MiniLM) embeddings (frozen)
        ↓
    sklearn LogisticRegression
        ↓
    P(high_quality)  →  threshold  →  keep / drop

Why this shape (and not a huge transformer like FineWeb's BERT-base):

    * The dataset is <100k rows — a large model would overfit and add
      disproportionate wall time per example.
    * ``sentence-transformers/all-MiniLM-L6-v2`` is already in
      ``requirements.txt`` (used by RAG) and fits a 22M-param SBERT in
      ~80 MB; no extra download needed for the classifier path.
    * Logistic regression gives calibrated probabilities out-of-the-box
      so threshold tuning is straightforward.

Training labels are *bootstrapped* rather than hand-annotated:

    POSITIVE   — gold CSV FAQ answers and teacher QA that have survived the
                  heuristic quality stage. These are human-curated and thus
                  high quality almost by definition.
    NEGATIVE   — synthetically constructed bad examples:
                  * first 15% of assistant text truncated
                  * shuffled word-order
                  * high-symbol PDF chunk tails
                  * repetition bombs (duplicated sentences)
                  The goal is not a perfect classifier, but a filter that
                  removes the 5-15% degenerate tail of the distribution.

The classifier is saved to disk as a single joblib pickle and loaded at
pipeline time. If it cannot load (missing file, missing deps), the
pipeline logs a warning and proceeds without the filter — exactly like
all other optional quality stages.

Public API:
    * :class:`QualityClassifier` — load / score / filter / save
    * :func:`build_bootstrap_dataset` — label a training set from pipeline
      output
    * :func:`train_classifier` — fit a classifier from labelled rows
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ml.scripts.data_aug.schema import (
    SourceType,
    TaskType,
    TrainingExample,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_THRESHOLD = 0.45  # tuned conservatively: keep > this score
DEFAULT_MODEL_PATH = Path("artifacts/models/quality_classifier.joblib")


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def _example_text(ex: TrainingExample) -> str:
    """Concatenate user + assistant text for scoring. System prompt is
    excluded so identical boilerplate doesn't dominate the embedding."""
    return f"Q: {ex.user_text()}\nA: {ex.assistant_text()}"


def _lexical_features(text: str) -> list[float]:
    """Cheap non-embedding features that complement the embedding signal.

    These are the same heuristic dimensions used by FineWeb-Edu's rule
    pre-filter, plus a couple of domain-specific ones for tax content.
    """
    if not text:
        return [0.0] * 7
    words = text.split()
    n_words = max(len(words), 1)
    n_chars = max(len(text), 1)
    avg_word_len = sum(len(w) for w in words) / n_words
    unique_ratio = len(set(words)) / n_words
    digit_ratio = sum(c.isdigit() for c in text) / n_chars
    upper_ratio = sum(c.isupper() for c in text) / n_chars
    # Tax-domain signal words — crude but effective on Ugandan tax text.
    domain_hits = sum(
        1 for kw in ("tax", "URA", "VAT", "income", "return", "file", "duty")
        if kw.lower() in text.lower()
    )
    return [
        float(n_words),
        float(n_chars),
        float(avg_word_len),
        float(unique_ratio),
        float(digit_ratio),
        float(upper_ratio),
        float(domain_hits),
    ]


# ---------------------------------------------------------------------------
# Classifier wrapper
# ---------------------------------------------------------------------------


@dataclass
class QualityScoreReport:
    total: int
    kept: int
    dropped: int
    score_mean: float
    score_p10: float
    score_p50: float
    score_p90: float

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "kept": self.kept,
            "dropped": self.dropped,
            "score_mean": round(self.score_mean, 4),
            "score_p10": round(self.score_p10, 4),
            "score_p50": round(self.score_p50, 4),
            "score_p90": round(self.score_p90, 4),
        }


class QualityClassifier:
    """Thin wrapper around sentence-transformers + sklearn.

    The wrapper hides the embedding model so callers don't need to think
    about it. Both the embedding model and the classifier pickle are
    lazy-loaded so that the pipeline can run in environments that lack
    ``sentence-transformers`` (falling back to *no* quality scoring).
    """

    def __init__(
        self,
        embed_model: str = DEFAULT_EMBED_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        self.embed_model_name = embed_model
        self.threshold = threshold
        self._embedder = None
        self._classifier = None
        self._fitted = False

    # ------------------------------------------------------------------
    # Lazy loaders
    # ------------------------------------------------------------------

    def _ensure_embedder(self):
        if self._embedder is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # lazy
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required for QualityClassifier. "
                "Install with: pip install sentence-transformers"
            ) from exc
        log.info("quality_classifier: loading embedder %s", self.embed_model_name)
        self._embedder = SentenceTransformer(self.embed_model_name)

    def _featurise(self, examples: Sequence[TrainingExample]):
        import numpy as np  # lazy

        self._ensure_embedder()
        texts = [_example_text(ex) for ex in examples]
        embeddings = self._embedder.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        lex = np.asarray([_lexical_features(t) for t in texts], dtype="float32")
        return np.concatenate([embeddings.astype("float32"), lex], axis=1)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(
        self,
        positives: Sequence[TrainingExample],
        negatives: Sequence[TrainingExample],
    ) -> dict:
        """Fit on bootstrapped (positive, negative) lists.

        Returns a metrics dict so the training CLI can print / persist it.
        """
        from sklearn.linear_model import LogisticRegression  # lazy
        from sklearn.metrics import (
            accuracy_score,
            f1_score,
            precision_score,
            recall_score,
        )
        from sklearn.model_selection import train_test_split
        import numpy as np

        if not positives or not negatives:
            raise ValueError(
                f"Need both positive and negative examples (got "
                f"{len(positives)} positives, {len(negatives)} negatives)"
            )

        log.info(
            "quality_classifier: fitting on %d positive / %d negative examples",
            len(positives),
            len(negatives),
        )

        X_pos = self._featurise(positives)
        X_neg = self._featurise(negatives)
        X = np.concatenate([X_pos, X_neg], axis=0)
        y = np.concatenate([
            np.ones(len(positives), dtype="int64"),
            np.zeros(len(negatives), dtype="int64"),
        ])

        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
        clf.fit(X_train, y_train)
        self._classifier = clf
        self._fitted = True

        preds = clf.predict(X_val)
        metrics = {
            "accuracy": float(accuracy_score(y_val, preds)),
            "f1": float(f1_score(y_val, preds)),
            "precision": float(precision_score(y_val, preds, zero_division=0)),
            "recall": float(recall_score(y_val, preds, zero_division=0)),
            "n_train": int(len(y_train)),
            "n_val": int(len(y_val)),
            "feature_dim": int(X.shape[1]),
            "threshold": float(self.threshold),
            "embed_model": self.embed_model_name,
        }
        log.info("quality_classifier: %s", metrics)
        return metrics

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def score(self, examples: Sequence[TrainingExample]) -> "list[float]":
        """Return P(high_quality) for each example."""
        if not self._fitted or self._classifier is None:
            raise RuntimeError("QualityClassifier is not fitted / loaded")
        if not examples:
            return []
        X = self._featurise(examples)
        probs = self._classifier.predict_proba(X)[:, 1]
        return [float(p) for p in probs]

    def score_and_filter(
        self,
        examples: Sequence[TrainingExample],
        *,
        threshold: Optional[float] = None,
    ) -> tuple[list[TrainingExample], QualityScoreReport]:
        """Score, attach scores to metadata, keep only rows above threshold."""
        if not examples:
            return [], QualityScoreReport(0, 0, 0, 0.0, 0.0, 0.0, 0.0)

        t = threshold if threshold is not None else self.threshold
        scores = self.score(examples)
        kept: list[TrainingExample] = []
        for ex, s in zip(examples, scores):
            ex.metadata.quality_score = round(s, 4)
            if s >= t:
                kept.append(ex)

        import statistics

        ss = sorted(scores)
        report = QualityScoreReport(
            total=len(examples),
            kept=len(kept),
            dropped=len(examples) - len(kept),
            score_mean=float(sum(scores) / len(scores)),
            score_p10=float(ss[max(0, int(0.1 * len(ss)) - 1)]),
            score_p50=float(statistics.median(ss)),
            score_p90=float(ss[min(len(ss) - 1, int(0.9 * len(ss)))]),
        )
        log.info("quality_classifier: %s", report.as_dict())
        return kept, report

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        if not self._fitted or self._classifier is None:
            raise RuntimeError("Cannot save an unfitted classifier")
        import joblib  # lazy

        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "embed_model": self.embed_model_name,
                "threshold": self.threshold,
                "classifier": self._classifier,
                "format_version": 1,
            },
            path,
        )
        log.info("quality_classifier: saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> "QualityClassifier":
        import joblib  # lazy

        if not path.exists():
            raise FileNotFoundError(path)
        payload: dict[str, Any] = joblib.load(path)
        if payload.get("format_version") != 1:
            raise RuntimeError(
                f"unexpected quality classifier format: {payload.get('format_version')}"
            )
        obj = cls(
            embed_model=payload["embed_model"],
            threshold=float(payload.get("threshold", DEFAULT_THRESHOLD)),
        )
        obj._classifier = payload["classifier"]
        obj._fitted = True
        log.info("quality_classifier: loaded from %s", path)
        return obj


# ---------------------------------------------------------------------------
# Bootstrap label generation
# ---------------------------------------------------------------------------


_DEGRADERS = [
    "truncate_15",      # keep only first 15% of assistant text
    "shuffle_words",    # random word shuffle
    "repeat_bomb",      # repeat the assistant 4× to trigger repetition
    "strip_answer",     # assistant becomes empty-ish
    "symbol_spam",      # inject symbol-heavy tail
]


def _degrade(example: TrainingExample, mode: str, rng: random.Random) -> Optional[TrainingExample]:
    """Return a degraded copy of an example for use as a NEGATIVE sample.

    ``None`` if the degradation produces something Pydantic rejects (e.g.
    empty assistant turn) — caller just skips.
    """
    import copy

    ex = copy.deepcopy(example)
    # Mutate the last assistant message only; keep user / system intact.
    for i in range(len(ex.messages) - 1, -1, -1):
        if ex.messages[i].role == "assistant":
            content = ex.messages[i].content
            if mode == "truncate_15":
                new = content[: max(5, int(len(content) * 0.15))]
            elif mode == "shuffle_words":
                words = content.split()
                rng.shuffle(words)
                new = " ".join(words)
            elif mode == "repeat_bomb":
                new = (content + " ") * 4
            elif mode == "strip_answer":
                new = content[:8]
            elif mode == "symbol_spam":
                new = content + " ...... ########## !!! !!! !!! !!!"
            else:
                new = content
            # Swap by rebuilding the message (Pydantic models are frozen by
            # default — deepcopy gets a fresh instance which we can replace).
            from ml.scripts.data_aug.schema import Message

            try:
                ex.messages[i] = Message(role="assistant", content=new or "x")
            except Exception:
                return None
            break
    ex.metadata.source = f"synthetic_negative:{mode}"
    return ex


def build_bootstrap_dataset(
    examples: Iterable[TrainingExample],
    *,
    max_per_class: int = 5000,
    seed: int = 42,
) -> tuple[list[TrainingExample], list[TrainingExample]]:
    """Convert a pipeline output into (positives, negatives).

    Positives are drawn from high-confidence source types (CSV FAQs and
    teacher QA); anything else is ignored for labelling. Negatives are
    generated by degrading a shuffled copy of the positives.
    """
    rng = random.Random(seed)
    positives: list[TrainingExample] = []
    for ex in examples:
        if ex.metadata.source_type in {SourceType.CSV_FAQ, SourceType.TEACHER_QA}:
            if ex.metadata.task == TaskType.QA:
                positives.append(ex)
    rng.shuffle(positives)
    positives = positives[:max_per_class]

    if not positives:
        return [], []

    # Produce negatives by degrading positives. We use several modes so the
    # classifier learns more than one degenerate pattern.
    negatives: list[TrainingExample] = []
    for i, ex in enumerate(positives):
        mode = _DEGRADERS[i % len(_DEGRADERS)]
        deg = _degrade(ex, mode, rng)
        if deg is not None:
            negatives.append(deg)

    # Cap negatives at positives count to keep class balance reasonable.
    negatives = negatives[:max_per_class]
    return positives, negatives


def train_classifier(
    positives: Sequence[TrainingExample],
    negatives: Sequence[TrainingExample],
    *,
    embed_model: str = DEFAULT_EMBED_MODEL,
    threshold: float = DEFAULT_THRESHOLD,
    save_path: Optional[Path] = None,
) -> tuple[QualityClassifier, dict]:
    """Train + optionally save. Returns (clf, metrics)."""
    clf = QualityClassifier(embed_model=embed_model, threshold=threshold)
    metrics = clf.fit(positives, negatives)
    if save_path is not None:
        clf.save(save_path)
        # Dump metrics next to the pickle for visibility.
        (save_path.with_suffix(".metrics.json")).write_text(
            json.dumps(metrics, indent=2)
        )
    return clf, metrics
