"""MT data loaders (2026).

Yield :class:`ml.scripts.data_aug.speech_schema.MTExample` from parallel
corpora. Supports **any** language pair — not limited to English↔Luganda.

Sources:

    * Tab-separated .txt (e.g. ``eng.lug.txt``, ``eng.sw.txt``)
    * CSV with flexible column detection (english, luganda, swahili, runyankole, acholi)
    * JSONL with ``source_text``/``target_text`` or language-name keys
    * JW300/OPUS downloads from ``dataset_downloader.py``

Commercial-safety: each loaded row inherits the license documented in
the corresponding data card under ``ml/docs/data_cards/``.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ml.scripts.data_aug.speech_schema import (
    LicenseClass,
    MTExample,
    MTSourceType,
    mt_content_hash,
)
from ml.scripts.data_aug.text_utils import clean_text

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger(__name__)

# Language name → ISO code mapping for flexible column detection.
_LANG_COLUMNS: dict[str, str] = {
    "english": "en",
    "en": "en",
    "english text": "en",
    "english_text": "en",
    "source": "en",
    "luganda": "lg",
    "lg": "lg",
    "luganda text": "lg",
    "luganda_text": "lg",
    "target": "lg",
    "swahili": "sw",
    "sw": "sw",
    "kiswahili": "sw",
    "runyankole": "nyn",
    "nyn": "nyn",
    "runyankore": "nyn",
    "acholi": "ach",
    "ach": "ach",
    "luo": "ach",
}

# Source type inference from language pair.
_PAIR_SOURCE_TYPE: dict[tuple[str, str], MTSourceType] = {
    ("en", "lg"): MTSourceType.LUGANDA_PARALLEL,
    ("en", "sw"): MTSourceType.SWAHILI_PARALLEL,
    ("en", "nyn"): MTSourceType.RUNYANKOLE_PARALLEL,
    ("en", "ach"): MTSourceType.ACHOLI_PARALLEL,
}


def _infer_source_type(src: str, tgt: str) -> MTSourceType:
    return _PAIR_SOURCE_TYPE.get(
        (src, tgt), _PAIR_SOURCE_TYPE.get((tgt, src), MTSourceType.LUGANDA_PARALLEL)
    )


# ---------------------------------------------------------------------------
# Tab-separated txt
# ---------------------------------------------------------------------------


def load_tab_txt(
    path: Path,
    *,
    source_lang: str = "en",
    target_lang: str = "lg",
    source_type: MTSourceType | None = None,
    license: LicenseClass = LicenseClass.PROPRIETARY,
) -> Iterator[MTExample]:
    """Yield MTExample rows from tab-separated parallel files."""
    if not path.exists():
        return

    st = source_type or _infer_source_type(source_lang, target_lang)

    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline().lower()
        # Skip header if it looks like column names.
        if not any(c in first for c in ("\t",)) or any(k in first for k in _LANG_COLUMNS):
            if "\t" not in first:
                f.seek(0)
        else:
            f.seek(0)

        for lineno, line in enumerate(f, 1):
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if len(parts) < 2:
                continue
            src, tgt = clean_text(parts[0]), clean_text(parts[1])
            if not src or not tgt:
                continue
            try:
                yield MTExample(
                    source_text=src,
                    target_text=tgt,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source=path.name,
                    source_type=st,
                    license=license,
                    content_hash=mt_content_hash(src, tgt),
                )
            except Exception as exc:
                log.debug("%s:%d skipped: %s", path.name, lineno, exc)


# ---------------------------------------------------------------------------
# CSV with flexible column detection
# ---------------------------------------------------------------------------


def load_csv(
    path: Path,
    *,
    source_lang: str = "en",
    target_lang: str = "lg",
    license: LicenseClass = LicenseClass.PROPRIETARY,
) -> Iterator[MTExample]:
    """Yield MTExample rows from CSV/TSV files with language columns."""
    if not path.exists():
        return

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
        dialect = csv.Sniffer().sniff(head, delimiters=",\t;|")
    except Exception:
        dialect = csv.excel

    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            return

        # Auto-detect source and target columns from headers.
        src_col, tgt_col = None, None
        detected_src, detected_tgt = source_lang, target_lang

        for col_name in reader.fieldnames:
            normed = col_name.lower().strip()
            lang = _LANG_COLUMNS.get(normed)
            if lang is None:
                continue
            if lang == source_lang or (src_col is None and lang == "en"):
                src_col = col_name
                detected_src = lang
            elif lang == target_lang or tgt_col is None:
                tgt_col = col_name
                detected_tgt = lang

        if not src_col or not tgt_col:
            log.debug(
                "csv: could not detect src/tgt columns in %s (fields: %s)",
                path.name,
                reader.fieldnames,
            )
            return

        st = _infer_source_type(detected_src, detected_tgt)

        for row in reader:
            src = clean_text(str(row.get(src_col, "")))
            tgt = clean_text(str(row.get(tgt_col, "")))
            if not src or not tgt:
                continue
            try:
                yield MTExample(
                    source_text=src,
                    target_text=tgt,
                    source_lang=detected_src,
                    target_lang=detected_tgt,
                    source=path.name,
                    source_type=st,
                    license=license,
                    content_hash=mt_content_hash(src, tgt),
                )
            except Exception as exc:
                log.debug("%s skipped row: %s", path.name, exc)


# ---------------------------------------------------------------------------
# JSONL (generic — works with any language pair)
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

            # Flexible key detection: canonical keys first, then language names.
            src = (
                rec.get("source_text")
                or rec.get("source")
                or rec.get("english")
                or rec.get("en")
                or ""
            )
            tgt = (
                rec.get("target_text")
                or rec.get("target")
                or rec.get("luganda")
                or rec.get("lg")
                or rec.get("swahili")
                or rec.get("sw")
                or rec.get("runyankole")
                or rec.get("nyn")
                or rec.get("acholi")
                or rec.get("ach")
                or ""
            )
            # Use per-row language if available.
            row_src = rec.get("source_lang", source_lang)
            row_tgt = rec.get("target_lang", target_lang)

            src = clean_text(str(src))
            tgt = clean_text(str(tgt))
            if not src or not tgt:
                continue
            try:
                yield MTExample(
                    source_text=src,
                    target_text=tgt,
                    source_lang=row_src,
                    target_lang=row_tgt,
                    source=path.name,
                    source_type=MTSourceType(
                        rec.get("source_type", _infer_source_type(row_src, row_tgt).value)
                    ),
                    license=license,
                    content_hash=mt_content_hash(src, tgt),
                    is_synthetic=bool(rec.get("is_synthetic", False)),
                )
            except Exception as exc:
                log.debug("%s:%d skipped: %s", path.name, lineno, exc)


# ---------------------------------------------------------------------------
# Directory dispatcher
# ---------------------------------------------------------------------------


def load_parallel_directory(
    data_dir: Path,
    *,
    source_lang: str = "en",
    target_lang: str = "lg",
) -> Iterator[MTExample]:
    """Scan a parallel corpus directory and emit MTExamples.

    Works for any language pair — pass source_lang/target_lang to override
    the defaults for tab-separated files where headers are absent.
    """
    if not data_dir.exists():
        log.info("mt: dir not found %s", data_dir)
        return

    total = 0
    for path in sorted(data_dir.glob("*.txt")):
        if path.stat().st_size == 0:
            continue
        for ex in load_tab_txt(path, source_lang=source_lang, target_lang=target_lang):
            yield ex
            total += 1
    for path in sorted(data_dir.glob("*.csv")):
        for ex in load_csv(path, source_lang=source_lang, target_lang=target_lang):
            yield ex
            total += 1
    for path in sorted(data_dir.glob("*.jsonl")):
        for ex in load_jsonl(path, source_lang=source_lang, target_lang=target_lang):
            yield ex
            total += 1
    log.info("mt: %d %s↔%s parallel pairs from %s", total, source_lang, target_lang, data_dir)


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

    buckets: dict[str, list[MTExample]] = {}
    for ex in examples:
        key = ex.doc_id or ex.source
        buckets.setdefault(key, []).append(ex)

    doc_ids = list(buckets.keys())
    rng.shuffle(doc_ids)
    n = len(doc_ids)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    train_docs = set(doc_ids[:n_train])
    val_docs = set(doc_ids[n_train : n_train + n_val])

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

    log.info("mt split: train=%d val=%d test=%d (from %d docs)", len(train), len(val), len(test), n)
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
# CLI entry-point
# ---------------------------------------------------------------------------


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="MT data loader (multi-language)")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="lg")
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/mt/data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")
    rows = list(
        load_parallel_directory(
            args.data_dir,
            source_lang=args.source_lang,
            target_lang=args.target_lang,
        )
    )
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
