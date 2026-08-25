#!/usr/bin/env python3
"""Generate synthetic Lg->En backtranslation pairs from monolingual Luganda text.

Iterative backtranslation is the standard technique for low-resource MT:

    1. Train a forward En->Lg model on the seed parallel corpus.
    2. Use it to translate monolingual En text to synthetic Lg (or the
       reverse for Lg->En).
    3. Treat the (synthetic-source, real-target) pairs as additional
       training data with ``is_synthetic=True``.

This script consumes monolingual Luganda text from ``Data/TTT/`` (e.g.
``luganda_wiki_corpus.txt``) and emits MTExample rows flagged as
synthetic, which downstream trainers can weight down vs gold pairs.

Usage::

    python -m ml.scripts.mt.backtranslate --direction lg2en --dry-run
    python -m ml.scripts.mt.backtranslate \\
        --direction lg2en \\
        --mono-file Data/TTT/luganda_wiki_corpus.txt \\
        --out artifacts/mt/backtranslated_lg2en.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger("mt.backtranslate")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PROJECT_ROOT / "Data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts" / "mt"


def _iter_mono(path: Path, *, min_chars: int = 15, max_chars: int = 400) -> Iterator[str]:
    if not path.exists():
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not (min_chars <= len(s) <= max_chars):
                continue
            yield s


def _translate_batch(translator, texts: list[str], direction: str) -> list[str]:
    src, tgt = direction.split("2")
    return [translator.translate(t, source_lang=src, target_lang=tgt).text for t in texts]


def run(
    *,
    direction: str,
    mono_file: Path,
    out_path: Path,
    max_rows: int | None,
    dry_run: bool,
) -> int:
    from ml.scripts.data_aug.speech_schema import (  # type: ignore
        LicenseClass,
        MTExample,
        MTSourceType,
        mt_content_hash,
    )

    if direction not in ("en2lg", "lg2en"):
        log.error("direction must be en2lg or lg2en")
        return 1

    rows = list(_iter_mono(mono_file))
    if max_rows is not None:
        rows = rows[:max_rows]
    log.info("monolingual rows: %d (from %s)", len(rows), mono_file)

    if dry_run:
        log.info("[dry-run] would backtranslate %d rows -> %s", len(rows), out_path)
        return 0

    if not rows:
        log.warning("no monolingual text — nothing to backtranslate")
        return 0

    try:
        from ml.scripts.mt.infer_mt import MtTranslator  # type: ignore

        translator = MtTranslator(backend="auto")
    except Exception as exc:
        log.error("translator init failed: %s", exc)
        return 1

    src_lang, tgt_lang = direction.split("2")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        batch_size = 16
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            try:
                translations = _translate_batch(translator, batch, direction)
            except Exception as exc:
                log.warning("batch %d failed: %s", i, exc)
                continue
            for src_text, tgt_text in zip(batch, translations, strict=False):
                if not tgt_text or not tgt_text.strip():
                    continue
                try:
                    ex = MTExample(
                        source_text=src_text,
                        target_text=tgt_text,
                        source_lang=src_lang,
                        target_lang=tgt_lang,
                        source=mono_file.name,
                        source_type=MTSourceType.BACKTRANSLATION,
                        license=LicenseClass.PROPRIETARY,
                        is_synthetic=True,
                        content_hash=mt_content_hash(src_text, tgt_text),
                    )
                    f.write(json.dumps(ex.to_row(), ensure_ascii=False) + "\n")
                    written += 1
                except Exception as exc:
                    log.debug("skipped row: %s", exc)
    log.info("wrote %d backtranslated pairs -> %s", written, out_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--direction", choices=("en2lg", "lg2en"), default="lg2en")
    parser.add_argument(
        "--mono-file",
        type=Path,
        default=DATA_ROOT / "TTT" / "luganda_wiki_corpus.txt",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ARTIFACTS_DIR / "backtranslated.jsonl",
    )
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    return run(
        direction=args.direction,
        mono_file=args.mono_file,
        out_path=args.out,
        max_rows=args.max_rows,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
