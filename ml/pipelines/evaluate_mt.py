"""MT evaluation pipeline (2026).

Computes translation quality signals on a held-out eval set:

    * **BLEU**           — corpus BLEU (via sacrebleu, already in requirements)
    * **chrF**           — character n-gram F1
    * **TER**            — translation edit rate
    * **length-ratio deviation** — mean |len(hyp)/len(ref) - 1|
    * **hallucination rate**     — fraction of outputs whose chrF < 0.10
    * **Per-direction slices** — en_lg vs lg_en

Optional: ``unbabel-comet`` for reference-free COMET-kiwi scoring when the
package is available.

Eval set format (JSONL)::

    {"source_text": "...", "target_text": "...", "source_lang": "en", "target_lang": "lg"}

Usage::

    python -m ml.pipelines.evaluate_mt
    python -m ml.pipelines.evaluate_mt --dry-run
    python -m ml.pipelines.evaluate_mt --eval-set Data/eval/mt_eval_lgen.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("evaluate_mt")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _compute_bleu(refs: list[str], hyps: list[str]) -> float:
    try:
        import sacrebleu  # type: ignore

        score = sacrebleu.corpus_bleu(hyps, [refs])
        return float(score.score)
    except ImportError:
        # Naive sentence-BLEU approximation: unigram overlap.
        if not refs:
            return 0.0
        total = 0
        for ref, hyp in zip(refs, hyps, strict=False):
            ref_tokens = set(ref.lower().split())
            hyp_tokens = set(hyp.lower().split())
            if not hyp_tokens:
                continue
            total += len(ref_tokens & hyp_tokens) / max(len(hyp_tokens), 1)
        return round(100.0 * total / max(len(refs), 1), 2)


def _compute_chrf(refs: list[str], hyps: list[str]) -> float:
    try:
        import sacrebleu  # type: ignore

        score = sacrebleu.corpus_chrf(hyps, [refs])
        return float(score.score) / 100.0
    except ImportError:
        # Naive char-trigram F1.
        def _trigrams(s: str) -> set[str]:
            s = " ".join(s.lower().split())
            return {s[i : i + 3] for i in range(len(s) - 2)}

        total = 0.0
        for ref, hyp in zip(refs, hyps, strict=False):
            r = _trigrams(ref)
            h = _trigrams(hyp)
            if not r or not h:
                continue
            common = len(r & h)
            prec = common / len(h)
            rec = common / len(r)
            if prec + rec:
                total += 2 * prec * rec / (prec + rec)
        return round(total / max(len(refs), 1), 4)


def _compute_ter(refs: list[str], hyps: list[str]) -> float:
    try:
        import sacrebleu  # type: ignore

        score = sacrebleu.corpus_ter(hyps, [refs])
        return float(score.score)
    except ImportError:
        return 0.0


def _length_ratio_deviation(refs: list[str], hyps: list[str]) -> float:
    if not refs:
        return 0.0
    deviations = []
    for ref, hyp in zip(refs, hyps, strict=False):
        rlen = max(len(ref), 1)
        hlen = len(hyp)
        deviations.append(abs(hlen / rlen - 1))
    return round(sum(deviations) / len(deviations), 4)


def _hallucination_rate(refs: list[str], hyps: list[str]) -> float:
    """Fraction of hypotheses with chrF < 0.10 — a proxy for unrelated output."""
    if not refs:
        return 0.0
    count = 0
    for ref, hyp in zip(refs, hyps, strict=False):
        if _compute_chrf([ref], [hyp]) < 0.10:
            count += 1
    return round(count / len(refs), 4)


def _compute_comet_kiwi(sources: list[str], hyps: list[str]) -> float | None:
    """Reference-free COMET-kiwi; None if unbabel-comet is unavailable."""
    try:
        from comet import download_model, load_from_checkpoint  # type: ignore

        model_path = download_model("Unbabel/wmt23-cometkiwi-da-xl")
        model = load_from_checkpoint(model_path)
        data = [{"src": s, "mt": h} for s, h in zip(sources, hyps, strict=False)]
        scores = model.predict(data, batch_size=8, gpus=0).scores
        return round(sum(scores) / max(len(scores), 1), 4)
    except Exception as exc:
        log.debug("COMET-kiwi unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


@dataclass
class MtEvalRow:
    source_text: str
    target_text: str
    source_lang: str
    target_lang: str


def _load_eval_set(path: Path) -> list[MtEvalRow]:
    if not path.exists():
        log.warning("MT eval set missing: %s", path)
        return []
    rows: list[MtEvalRow] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s:%d invalid json", path.name, lineno)
                continue
            src = rec.get("source_text") or rec.get("source") or rec.get("english")
            tgt = rec.get("target_text") or rec.get("target") or rec.get("luganda")
            sl = rec.get("source_lang") or "en"
            tl = rec.get("target_lang") or "lg"
            if not src or not tgt:
                continue
            rows.append(
                MtEvalRow(
                    source_text=str(src),
                    target_text=str(tgt),
                    source_lang=str(sl),
                    target_lang=str(tl),
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class DirectionMetrics:
    n: int
    bleu: float
    chrf: float
    ter: float


@dataclass
class MtMetrics:
    n: int
    bleu: float
    chrf: float
    ter: float
    length_ratio_deviation: float
    hallucination_rate: float
    comet_kiwi: float | None
    backend: str
    directions: dict[str, DirectionMetrics] = field(default_factory=dict)


def run_evaluation(
    *,
    eval_set: Path,
    backend: str = "auto",
    dry_run: bool = False,
) -> MtMetrics:
    rows = _load_eval_set(eval_set)
    if not rows:
        return MtMetrics(
            n=0,
            bleu=0.0,
            chrf=0.0,
            ter=0.0,
            length_ratio_deviation=0.0,
            hallucination_rate=0.0,
            comet_kiwi=None,
            backend=backend,
        )

    if dry_run:
        log.info("[dry-run] would evaluate %d MT pairs", len(rows))
        return MtMetrics(
            n=len(rows),
            bleu=0.0,
            chrf=0.0,
            ter=0.0,
            length_ratio_deviation=0.0,
            hallucination_rate=0.0,
            comet_kiwi=None,
            backend="dry-run",
        )

    from ml.scripts.mt.infer_mt import MtTranslator  # type: ignore

    translator = MtTranslator(backend=backend)
    hyps: list[str] = []
    refs: list[str] = []
    srcs: list[str] = []
    backend_used = "unknown"
    for row in rows:
        result = translator.translate(
            row.source_text, source_lang=row.source_lang, target_lang=row.target_lang
        )
        hyps.append(result.text)
        refs.append(row.target_text)
        srcs.append(row.source_text)
        backend_used = result.backend

    metrics = MtMetrics(
        n=len(rows),
        bleu=_compute_bleu(refs, hyps),
        chrf=_compute_chrf(refs, hyps),
        ter=_compute_ter(refs, hyps),
        length_ratio_deviation=_length_ratio_deviation(refs, hyps),
        hallucination_rate=_hallucination_rate(refs, hyps),
        comet_kiwi=_compute_comet_kiwi(srcs, hyps),
        backend=backend_used,
    )

    # Per-direction slices
    directions: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        key = f"{row.source_lang}_{row.target_lang}"
        directions.setdefault(key, []).append(i)
    for key, idxs in directions.items():
        slice_refs = [refs[i] for i in idxs]
        slice_hyps = [hyps[i] for i in idxs]
        metrics.directions[key] = DirectionMetrics(
            n=len(idxs),
            bleu=_compute_bleu(slice_refs, slice_hyps),
            chrf=_compute_chrf(slice_refs, slice_hyps),
            ter=_compute_ter(slice_refs, slice_hyps),
        )
    return metrics


def _to_dict(m: MtMetrics) -> dict[str, Any]:
    return {
        "n": m.n,
        "bleu": m.bleu,
        "chrf": m.chrf,
        "ter": m.ter,
        "length_ratio_deviation": m.length_ratio_deviation,
        "hallucination_rate": m.hallucination_rate,
        "comet_kiwi": m.comet_kiwi,
        "backend": m.backend,
        "directions": {k: asdict(v) for k, v in m.directions.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MT evaluation (BLEU / chrF / TER / COMET-kiwi)")
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=PROJECT_ROOT / "Data" / "eval" / "mt_eval_lgen.jsonl",
    )
    parser.add_argument(
        "--metrics-out",
        type=Path,
        default=PROJECT_ROOT / "Results" / "metrics" / "mt_metrics.json",
    )
    parser.add_argument(
        "--backend", choices=("auto", "onnx", "teacher", "base", "prompted"), default="auto"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    metrics = run_evaluation(
        eval_set=args.eval_set,
        backend=args.backend,
        dry_run=args.dry_run,
    )
    payload = _to_dict(metrics)

    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log.info("metrics -> %s", args.metrics_out)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
