"""MT data loaders (2026).

Yield :class:`ml.scripts.data_aug.speech_schema.MTExample` from the project's
Luganda parallel corpora. The existing ``loaders.load_luganda_data`` in
``ml/scripts/data_aug/loaders.py`` wraps each pair as an *instruction*
example ("Translate to Luganda: ...") so the LLM pipeline consumes it as
chat data.  The dedicated MT training path wants *bare parallel pairs*
instead — this module produces those.

Sources reused from existing project layout (``Data/TTT/``):

    * ``eng.lug.txt``           — tab-separated english\\tluganda (mojibake-repaired)
    * ``Luganda.csv``           — english / luganda columns
    * ``Luganda_Agriculture-specific_dataset-1.csv``
    * ``WordProject_ Luganda_English_Corpus - verses.txt``
    * ``Multilingual Parallel Corpus.xlsx`` (loaded via openpyxl if present)

Commercial-safety: each loaded row inherits the license documented in
``ml/docs/data_cards/mt_luganda_parallel.md``.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Iterator, Optional

from ml.scripts.data_aug.speech_schema import (
    LicenseClass,
    MTExample,
    MTSourceType,
    mt_content_hash,
)
from ml.scripts.data_aug.text_utils import clean_text

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tab-separated txt (eng.lug.txt, WordProject corpus)
# ---------------------------------------------------------------------------


def load_tab_txt(
    path: Path,
    *,
    source_lang: str = "en",
    target_lang: str = "lg",
    source_type: MTSourceType = MTSourceType.LUGANDA_PARALLEL,
    license: LicenseClass = LicenseClass.PROPRIETARY,
) -> Iterator[MTExample]:
    """Yield MTExample rows from tab-separated english/luganda files."""
    if not path.exists():
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline()
        if "english" not in first.lower() and "luganda" not in first.lower():
            f.seek(0)
        for lineno, line in enumerate(f, 1):
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if len(parts) < 2:
                continue
            en, lg = parts[0], parts[1]
            en, lg = clean_text(en), clean_text(lg)
            if not en or not lg:
                continue
            try:
                yield MTExample(
                    source_text=en,
                    target_text=lg,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source=path.name,
                    source_type=source_type,
                    license=license,
                    content_hash=mt_content_hash(en, lg),
                )
            except Exception as exc:
                log.debug("%s:%d skipped: %s", path.name, lineno, exc)


# ---------------------------------------------------------------------------
# CSV (Luganda.csv, Luganda_Agriculture-specific_dataset-1.csv)
# ---------------------------------------------------------------------------


def load_csv(
    path: Path,
    *,
    source_lang: str = "en",
    target_lang: str = "lg",
    license: LicenseClass = LicenseClass.PROPRIETARY,
) -> Iterator[MTExample]:
    """Yield MTExample rows from CSV/TSV files with english/luganda columns."""
    if not path.exists():
        return

    # Sniff dialect (tab vs comma).
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
        dialect = csv.Sniffer().sniff(head, delimiters=",\t;|")
    except Exception:
        dialect = csv.excel

    en_keys = ("english", "en", "English", "English Text", "english_text", "source")
    lg_keys = ("luganda", "lg", "Luganda", "Luganda Text", "luganda_text", "target")

    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, dialect=dialect)
        for row in reader:
            en = next((row.get(k) for k in en_keys if row.get(k)), None)
            lg = next((row.get(k) for k in lg_keys if row.get(k)), None)
            if not en or not lg:
                continue
            en = clean_text(str(en))
            lg = clean_text(str(lg))
            if not en or not lg:
                continue
            try:
                yield MTExample(
                    source_text=en,
                    target_text=lg,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source=path.name,
                    source_type=MTSourceType.LUGANDA_PARALLEL,
                    license=license,
                    content_hash=mt_content_hash(en, lg),
                )
            except Exception as exc:
                log.debug("%s skipped row: %s", path.name, exc)


# ---------------------------------------------------------------------------
# JSONL (existing or hand-curated)
# ---------------------------------------------------------------------------


def load_jsonl(
    path: Path,
    *,
    source_lang: str = "en",
    target_lang: str = "lg",
    license: LicenseClass = LicenseClass.PROPRIETARY,
) -> Iterator[MTExample]:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            en = rec.get("english") or rec.get("en") or rec.get("source_text") or rec.get("source")
            lg = rec.get("luganda") or rec.get("lg") or rec.get("target_text") or rec.get("target")
            if not en or not lg:
                continue
            en = clean_text(str(en))
            lg = clean_text(str(lg))
            if not en or not lg:
                continue
            try:
                yield MTExample(
                    source_text=en,
                    target_text=lg,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source=path.name,
                    source_type=MTSourceType(rec.get("source_type") or MTSourceType.LUGANDA_PARALLEL.value),
                    license=license,
                    content_hash=mt_content_hash(en, lg),
                    is_synthetic=bool(rec.get("is_synthetic", False)),
                )
            except Exception as exc:
                log.debug("%s:%d skipped: %s", path.name, lineno, exc)


# ---------------------------------------------------------------------------
# Directory dispatcher
# ---------------------------------------------------------------------------


def load_parallel_directory(data_dir: Path) -> Iterator[MTExample]:
    """Scan a Luganda parallel directory (e.g. Data/TTT/) and emit MTExamples.

    This is the MT counterpart of loaders.load_luganda_data. It walks every
    known file type and delegates to the right loader.
    """
    if not data_dir.exists():
        log.info("mt: dir not found %s", data_dir)
        return

    total = 0
    for path in sorted(data_dir.glob("*.txt")):
        if path.stat().st_size == 0:
            continue
        for ex in load_tab_txt(path):
            yield ex
            total += 1
    for path in sorted(data_dir.glob("*.csv")):
        for ex in load_csv(path):
            yield ex
            total += 1
    for path in sorted(data_dir.glob("*.jsonl")):
        for ex in load_jsonl(path):
            yield ex
            total += 1
    log.info("mt: %d parallel pairs from %s", total, data_dir)


# ---------------------------------------------------------------------------
# Stratified train/val/test split (by doc_id when present)
# ---------------------------------------------------------------------------


def stratified_split(
    examples: list[MTExample],
    *,
    train_ratio: float = 0.88,
    val_ratio: float = 0.08,
    seed: int = 42,
) -> tuple[list[MTExample], list[MTExample], list[MTExample]]:
    """Deterministic 88/8/4 split. Ensures doc_id groups do not straddle splits."""
    import random

    rng = random.Random(seed)

    # Group by doc_id so the same document cannot leak between train/test.
    buckets: dict[str, list[MTExample]] = {}
    for ex in examples:
        key = ex.doc_id or ex.source  # fall back to filename when doc_id missing
        buckets.setdefault(key, []).append(ex)

    doc_ids = list(buckets.keys())
    rng.shuffle(doc_ids)
    n = len(doc_ids)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    train_docs = set(doc_ids[:n_train])
    val_docs = set(doc_ids[n_train : n_train + n_val])
    test_docs = set(doc_ids[n_train + n_val :])

    train: list[MTExample] = []
    val: list[MTExample] = []
    test: list[MTExample] = []
    for doc, rows in buckets.items():
        if doc in train_docs:
            train.extend(rows)
        elif doc in val_docs:
            val.extend(rows)
        else:
            test.extend(rows)

    log.info(
        "mt split: train=%d val=%d test=%d (from %d docs)",
        len(train),
        len(val),
        len(test),
        n,
    )
    return train, val, test


def write_mt_jsonl(rows: list[MTExample], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.to_row(), ensure_ascii=False) + "\n")
            count += 1
    return count


# ---------------------------------------------------------------------------
# CLI entry-point for manual triage
# ---------------------------------------------------------------------------


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="MT data loader")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/mt/data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")
    rows = list(load_parallel_directory(args.data_dir))
    if not rows:
        log.warning("no rows loaded")
        return 1
    train, val, test = stratified_split(rows)
    if args.dry_run:
        log.info("[dry-run] would write %d train, %d val, %d test", len(train), len(val), len(test))
        return 0
    write_mt_jsonl(train, args.out_dir / "train.jsonl")
    write_mt_jsonl(val, args.out_dir / "val.jsonl")
    write_mt_jsonl(test, args.out_dir / "test.jsonl")
    log.info("wrote splits to %s", args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
