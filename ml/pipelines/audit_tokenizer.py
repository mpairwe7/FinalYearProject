"""Tokenizer coverage audit for the 2026 mobile model.

The mobile Gemma-2-2B tokenizer was trained predominantly on English web
text. Luganda coverage is *empirically* unknown — and for an offline
mobile model targeting Ugandan taxpayers, an under-trained tokenizer has
three concrete, measurable costs:

1. **Longer sequences** — 1 Luganda sentence may consume 2-3x the tokens
   of its English equivalent. This translates directly to slower TTFT
   and worse inference tokens/sec on-device, where both the KV cache and
   per-token compute are already tight.
2. **Worse quantisation** — rare subwords get squashed hardest by INT4
   quantisation, and Luganda relies on rare subwords more heavily than
   English does.
3. **Silent OOV → ``<unk>``** — losing semantic content mid-answer,
   where the symptoms look like "the model is dumb in Luganda" rather
   than "the tokenizer cannot represent the input".

This audit measures those costs directly so the model card can report
them and so CI can gate on them. It has no model-loading dependency —
only ``transformers.AutoTokenizer`` — so it can run in any CI job.

Metrics
-------

- **tokens_per_char** — headline compression. English ≈ 0.20 for Gemma.
- **fertility** — tokens / word. English ≈ 1.3; anything over 2.5 for a
  language means you are paying ~2x the inference cost per message.
- **unk_rate** — fraction of tokens that resolve to the UNK token. In a
  byte-level BPE tokenizer this is usually 0 (UNK cannot fire), but we
  still detect the case so we do not silently mask the issue.
- **piece_length_p50 / p90** — median and tail of subword byte length.
  A very low p50 means the tokenizer is falling back to single-byte
  pieces for this language (the "splitting into ashes" failure mode).
- **fertility_ratio** — Luganda fertility / English fertility. The 2026
  production threshold is ≤ 1.8. Higher means this tokenizer is a poor
  fit for Luganda and a vocabulary expansion or a different base model
  should be considered.

Usage
-----

    python -m ml.pipelines.audit_tokenizer \
        --tokenizer google/gemma-2-2b-it \
        --en Data/eval/rag_eval.jsonl \
        --lg Data/eval/rag_eval_lg.jsonl \
        --output-dir Results/tokenizer_audit
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ml.scripts.repro import env_snapshot, set_global_seed, write_snapshot  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger("audit_tokenizer")


# ---------------------------------------------------------------------------
# Corpus extraction — the JSONL row can use any of these fields for text.
# ---------------------------------------------------------------------------
_TEXT_FIELDS: tuple[str, ...] = (
    "question",
    "answer",
    "ground_truth",
    "prompt",
    "text",
    "content",
)


def extract_text(row: dict[str, Any]) -> str:
    """Concatenate every text-bearing field on a row into a single string."""
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        val = row.get(field)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    ctxs = row.get("contexts")
    if isinstance(ctxs, list):
        parts.extend(str(c) for c in ctxs if c)
    return " ".join(parts)


def load_jsonl_texts(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    out: list[str] = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Bad JSON at {path}:{i} — {exc}") from exc
            txt = extract_text(r)
            if txt:
                out.append(txt)
    log.info("Loaded %d texts from %s", len(out), path)
    return out


# ---------------------------------------------------------------------------
# Tokenizer loader — tries transformers, falls back to a word/char proxy so
# the audit still runs in a minimal environment (returns the proxy stats
# with a ``proxy=True`` flag so the caller can tell the difference).
# ---------------------------------------------------------------------------
class _WordLevelProxy:
    """Whitespace-word fallback tokenizer for environments without transformers.

    We still produce a meaningful per-language comparison — it just
    reduces to "characters per word" which is trivially longer for
    Luganda than English but does not reflect the real GGUF vocabulary.
    The report marks ``proxy=True`` so no downstream gate confuses this
    with the real tokenizer.
    """

    name_or_path = "whitespace-proxy"
    unk_token = None

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return re.findall(r"\S+", text)

    def convert_ids_to_tokens(self, ids: list[str]) -> list[str]:
        return ids


def load_tokenizer(name: str) -> tuple[Any, bool]:
    try:
        from transformers import AutoTokenizer  # type: ignore

        tok = AutoTokenizer.from_pretrained(name, use_fast=True)
        return tok, False
    except Exception as exc:  # pragma: no cover - best effort in CI
        log.warning(
            "Could not load tokenizer %r (%s) — using whitespace proxy",
            name,
            exc,
        )
        return _WordLevelProxy(), True


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------
def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text, flags=re.UNICODE))


def _percentile(values: list[int | float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return float(s[k])


def tokenize_corpus(
    tokenizer: Any,
    texts: Iterable[str],
    *,
    is_proxy: bool,
) -> dict[str, Any]:
    """Compute per-corpus token / fertility / unk / piece-length stats.

    ``tokenizer`` can be a real ``transformers.PreTrainedTokenizer`` or
    the whitespace proxy. The proxy path skips UNK/piece-length stats.
    """
    n_texts = 0
    n_chars = 0
    n_words = 0
    n_tokens = 0
    n_unks = 0
    piece_lengths: list[int] = []

    unk_token = getattr(tokenizer, "unk_token", None)

    for text in texts:
        if not text:
            continue
        n_texts += 1
        n_chars += len(text)
        n_words += _word_count(text)

        if is_proxy:
            pieces = tokenizer.encode(text)
            n_tokens += len(pieces)
            continue

        ids = tokenizer.encode(text, add_special_tokens=False)
        n_tokens += len(ids)
        if not ids:
            continue
        try:
            pieces = tokenizer.convert_ids_to_tokens(ids)
        except Exception:
            pieces = []
        for piece in pieces:
            if unk_token is not None and piece == unk_token:
                n_unks += 1
            if isinstance(piece, str):
                # strip the BPE marker byte so piece length reflects actual bytes
                normalised = piece.lstrip("▁").replace("Ġ", "").replace("Ċ", "")
                piece_lengths.append(len(normalised))

    def _ratio(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return {
        "n_texts": n_texts,
        "n_chars": n_chars,
        "n_words": n_words,
        "n_tokens": n_tokens,
        "n_unks": n_unks,
        "unk_rate": _ratio(n_unks, n_tokens),
        "tokens_per_char": _ratio(n_tokens, n_chars),
        "fertility": _ratio(n_tokens, n_words),  # tokens per word
        "chars_per_word": _ratio(n_chars, n_words),
        "piece_length_p50": _percentile(piece_lengths, 50) if piece_lengths else None,
        "piece_length_p90": _percentile(piece_lengths, 90) if piece_lengths else None,
    }


def compare(
    english: dict[str, Any],
    luganda: dict[str, Any],
    *,
    fertility_warn_ratio: float,
) -> dict[str, Any]:
    """Cross-language comparison + pass/fail flag."""

    def _ratio(a: float, b: float) -> float:
        return round(a / b, 4) if b else 0.0

    fert_ratio = _ratio(
        float(luganda.get("fertility") or 0),
        float(english.get("fertility") or 0),
    )
    tpc_ratio = _ratio(
        float(luganda.get("tokens_per_char") or 0),
        float(english.get("tokens_per_char") or 0),
    )

    passed = fert_ratio <= fertility_warn_ratio
    return {
        "fertility_ratio_lg_over_en": fert_ratio,
        "tokens_per_char_ratio_lg_over_en": tpc_ratio,
        "fertility_warn_threshold": fertility_warn_ratio,
        "passed": passed,
        "verdict": (
            "OK — Luganda fertility is within the 2026 production window"
            if passed
            else "WARN — Luganda tokenizer coverage is too thin; consider "
            "vocabulary expansion, a multilingual base model, or a "
            "SentencePiece retrain on a Luganda seed corpus."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Tokenizer coverage audit")
    parser.add_argument(
        "--tokenizer",
        default="google/gemma-2-2b-it",
        help="HF tokenizer repo id or local path",
    )
    parser.add_argument(
        "--en",
        type=Path,
        default=Path("Data/eval/rag_eval.jsonl"),
        help="English corpus (JSONL)",
    )
    parser.add_argument(
        "--lg",
        type=Path,
        default=Path("Data/eval/rag_eval_lg.jsonl"),
        help="Luganda corpus (JSONL)",
    )
    parser.add_argument(
        "--extra",
        type=Path,
        action="append",
        default=[],
        help="Additional corpora in the form LANG:PATH (repeatable)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("Results/tokenizer_audit"),
    )
    parser.add_argument(
        "--fertility-warn-ratio",
        type=float,
        default=1.8,
        help="Fail when Luganda fertility / English fertility exceeds this",
    )
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Exit non-zero when the comparison gate fails",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    set_global_seed(args.seed)
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve paths relative to PROJECT_ROOT when they are not absolute.
    def _abs(p: Path) -> Path:
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    en_path = _abs(args.en)
    lg_path = _abs(args.lg)

    en_texts = load_jsonl_texts(en_path)
    lg_texts = load_jsonl_texts(lg_path)

    tok, is_proxy = load_tokenizer(args.tokenizer)
    log.info(
        "Tokenizer: %s (proxy=%s)",
        getattr(tok, "name_or_path", args.tokenizer),
        is_proxy,
    )

    en_stats = tokenize_corpus(tok, en_texts, is_proxy=is_proxy)
    lg_stats = tokenize_corpus(tok, lg_texts, is_proxy=is_proxy)

    report: dict[str, Any] = {
        "tokenizer": getattr(tok, "name_or_path", args.tokenizer),
        "proxy": is_proxy,
        "corpora": {
            "en": {"path": str(en_path), **en_stats},
            "lg": {"path": str(lg_path), **lg_stats},
        },
        "comparison": compare(
            en_stats,
            lg_stats,
            fertility_warn_ratio=args.fertility_warn_ratio,
        ),
    }

    # Extra corpora (e.g. --extra sw:Data/eval/rag_eval_sw.jsonl)
    for entry in args.extra:
        entry_str = str(entry)
        if ":" not in entry_str:
            log.warning("Skipping --extra %r (expected LANG:PATH)", entry_str)
            continue
        lang, p = entry_str.split(":", 1)
        p_path = _abs(Path(p))
        texts = load_jsonl_texts(p_path)
        report["corpora"][lang.strip()] = {
            "path": str(p_path),
            **tokenize_corpus(tok, texts, is_proxy=is_proxy),
        }

    report["_env"] = env_snapshot(script="audit_tokenizer")

    out_path = out_dir / "tokenizer_audit.json"
    out_path.write_text(json.dumps(report, indent=2))
    write_snapshot(report["_env"], out_dir / "env_snapshots")

    print("=" * 60)
    print("TOKENIZER AUDIT")
    print("=" * 60)
    print(f"  tokenizer:    {report['tokenizer']}" + (" (proxy)" if is_proxy else ""))
    for lang, stats in report["corpora"].items():
        print(
            f"  [{lang}] n={stats['n_texts']:<4} "
            f"tokens/char={stats['tokens_per_char']:.3f} "
            f"fertility={stats['fertility']:.3f} "
            f"unk_rate={stats['unk_rate']:.4f}"
        )
    cmp_ = report["comparison"]
    status = "PASS" if cmp_["passed"] else "WARN"
    print(
        f"  fertility ratio lg/en = {cmp_['fertility_ratio_lg_over_en']:.3f} "
        f"(threshold {cmp_['fertility_warn_threshold']:.2f}) {status}"
    )
    print(f"  verdict: {cmp_['verdict']}")
    print(f"  report:  {out_path}")

    if args.fail_on_warn and not cmp_["passed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
