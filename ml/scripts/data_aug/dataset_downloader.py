"""Online dataset downloader and integration (2026).

Downloads parallel corpora and ASR datasets from public sources,
normalises them into the project's canonical JSONL format, and provides
loaders that the augmentation pipeline can consume.

Supported sources:

    * **JW300** — CC0 parallel corpus via HuggingFace ``opus/JW300``
      Pairs: en↔lg, en↔sw, en↔nyn, en↔ach
    * **OPUS** — Various OPUS sub-corpora via HuggingFace ``datasets``
    * **Common Voice** — ASR clips (delegates to ``asr_loaders.py``)
    * **Google FLEURS** — ASR eval set for multilingual coverage

All downloaded data is written to JSONL in the format that
``mt_loaders.load_jsonl`` already consumes.

Usage::

    python -m ml.scripts.data_aug.dataset_downloader \\
        --lang-pairs en-sw en-nyn en-ach en-lg \\
        --output-dir Data/online_corpora

    # Or from pipeline config:
    cfg = PipelineConfig(online_corpus_dirs=[Path("Data/online_corpora/jw300")])
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator, Optional

from ml.scripts.data_aug.schema import (
    Message,
    Metadata,
    SourceType,
    TaskType,
    TrainingExample,
    URA_SYSTEM_PROMPT,
    content_hash,
)
from ml.scripts.data_aug.speech_schema import (
    LicenseClass,
    MTExample,
    MTSourceType,
    mt_content_hash,
)
from ml.scripts.data_aug.text_utils import clean_text

log = logging.getLogger(__name__)

# Language code → full name for prompt templates.
LANG_NAMES = {
    "en": "English",
    "lg": "Luganda",
    "sw": "Swahili",
    "nyn": "Runyankole",
    "ach": "Acholi",
    "rw": "Kinyarwanda",
}


# ---------------------------------------------------------------------------
# JW300 downloader
# ---------------------------------------------------------------------------


def download_jw300(
    src_lang: str,
    tgt_lang: str,
    output_dir: Path,
    max_pairs: int = 50_000,
) -> Path:
    """Download JW300 parallel pairs from HuggingFace OPUS.

    Writes to ``output_dir/jw300_{src}_{tgt}.jsonl``.
    Returns the output file path.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        log.error("datasets library required: pip install datasets")
        raise

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"jw300_{src_lang}_{tgt_lang}.jsonl"

    if out_path.exists():
        log.info("jw300: %s already exists, skipping download", out_path)
        return out_path

    log.info("jw300: downloading %s-%s (max %d pairs)", src_lang, tgt_lang, max_pairs)

    # OPUS datasets use ISO 639-1 codes for common languages.
    # For nyn/ach, HuggingFace may use 3-letter codes.
    lang1, lang2 = sorted([src_lang, tgt_lang])

    try:
        ds = load_dataset(
            "opus/JW300",
            lang1=lang1,
            lang2=lang2,
            split="train",
            trust_remote_code=True,
        )
    except Exception as e:
        log.warning("jw300: failed to load %s-%s: %s", lang1, lang2, e)
        # Write empty file so subsequent runs don't retry.
        out_path.write_text("")
        return out_path

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in ds:
            trans = row.get("translation", {})
            src_text = clean_text(trans.get(src_lang, "")).strip()
            tgt_text = clean_text(trans.get(tgt_lang, "")).strip()

            if not src_text or not tgt_text:
                continue
            if len(src_text) < 5 or len(tgt_text) < 5:
                continue

            f.write(json.dumps({
                "source_text": src_text,
                "target_text": tgt_text,
                "source_lang": src_lang,
                "target_lang": tgt_lang,
                "source_type": MTSourceType.JW300.value,
                "license": LicenseClass.PUBLIC_DOMAIN.value,
            }, ensure_ascii=False) + "\n")

            count += 1
            if count >= max_pairs:
                break

    log.info("jw300: wrote %d pairs to %s", count, out_path)
    return out_path


# ---------------------------------------------------------------------------
# OPUS generic downloader
# ---------------------------------------------------------------------------


def download_opus(
    corpus_name: str,
    src_lang: str,
    tgt_lang: str,
    output_dir: Path,
    max_pairs: int = 30_000,
) -> Path:
    """Download any OPUS sub-corpus via HuggingFace."""
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"opus_{corpus_name}_{src_lang}_{tgt_lang}.jsonl"

    if out_path.exists():
        log.info("opus: %s already exists, skipping", out_path)
        return out_path

    lang1, lang2 = sorted([src_lang, tgt_lang])
    log.info("opus: downloading %s %s-%s", corpus_name, lang1, lang2)

    try:
        ds = load_dataset(
            f"opus/{corpus_name}",
            lang1=lang1,
            lang2=lang2,
            split="train",
            trust_remote_code=True,
        )
    except Exception as e:
        log.warning("opus: failed %s %s-%s: %s", corpus_name, lang1, lang2, e)
        out_path.write_text("")
        return out_path

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in ds:
            trans = row.get("translation", {})
            src_text = clean_text(trans.get(src_lang, "")).strip()
            tgt_text = clean_text(trans.get(tgt_lang, "")).strip()
            if not src_text or not tgt_text or len(src_text) < 5:
                continue

            f.write(json.dumps({
                "source_text": src_text,
                "target_text": tgt_text,
                "source_lang": src_lang,
                "target_lang": tgt_lang,
                "source_type": MTSourceType.OPUS.value,
                "license": LicenseClass.CC_BY.value,
            }, ensure_ascii=False) + "\n")

            count += 1
            if count >= max_pairs:
                break

    log.info("opus: wrote %d pairs to %s", count, out_path)
    return out_path


# ---------------------------------------------------------------------------
# Batch download all language pairs
# ---------------------------------------------------------------------------


def download_masakhane(
    src_lang: str,
    tgt_lang: str,
    output_dir: Path,
    max_pairs: int = 50_000,
) -> Path:
    """Download Masakhane MT benchmark data from HuggingFace.

    Masakhane is the largest community-driven NLP project for African
    languages. Their MT benchmarks cover en↔lg, en↔sw, en↔nyn, en↔ach
    with human-curated parallel pairs (CC-BY-4.0).
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"masakhane_{src_lang}_{tgt_lang}.jsonl"

    if out_path.exists():
        log.info("masakhane: %s already exists, skipping", out_path)
        return out_path

    # Masakhane uses ISO 639-3 codes with script tags.
    _code_map = {
        "en": "eng_Latn", "lg": "lug_Latn", "sw": "swh_Latn",
        "nyn": "nyn_Latn", "ach": "ach_Latn",
    }
    src_code = _code_map.get(src_lang, f"{src_lang}_Latn")
    tgt_code = _code_map.get(tgt_lang, f"{tgt_lang}_Latn")
    pair = f"{src_code}-{tgt_code}"

    log.info("masakhane: downloading %s (pair: %s)", pair, pair)

    try:
        ds = load_dataset(
            "masakhane/lafand-mt",
            pair,
            split="train",
            trust_remote_code=True,
        )
    except Exception as e:
        log.warning("masakhane: %s not available: %s", pair, e)
        # Try reversed pair.
        try:
            pair_rev = f"{tgt_code}-{src_code}"
            ds = load_dataset(
                "masakhane/lafand-mt",
                pair_rev,
                split="train",
                trust_remote_code=True,
            )
        except Exception as e2:
            log.warning("masakhane: reversed pair also failed: %s", e2)
            out_path.write_text("")
            return out_path

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in ds:
            src_text = clean_text(str(row.get(src_lang, row.get("source", "")))).strip()
            tgt_text = clean_text(str(row.get(tgt_lang, row.get("target", "")))).strip()

            if not src_text or not tgt_text or len(src_text) < 5:
                continue

            f.write(json.dumps({
                "source_text": src_text,
                "target_text": tgt_text,
                "source_lang": src_lang,
                "target_lang": tgt_lang,
                "source_type": "masakhane",
                "license": LicenseClass.CC_BY.value,
            }, ensure_ascii=False) + "\n")

            count += 1
            if count >= max_pairs:
                break

    log.info("masakhane: wrote %d pairs to %s", count, out_path)
    return out_path


def download_fleurs(
    language: str,
    output_dir: Path,
    max_samples: int = 5000,
) -> Path:
    """Download Google FLEURS ASR eval set for a language.

    FLEURS has ~12 hours per language covering 102 languages including
    Luganda, Swahili. Used for ASR evaluation, not training.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    _fleurs_codes = {
        "en": "en_us", "lg": "lg_ug", "sw": "sw_ke",
    }
    fleurs_code = _fleurs_codes.get(language)
    if not fleurs_code:
        log.warning("fleurs: language %s not in FLEURS", language)
        return Path()

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"fleurs_{language}.jsonl"

    if out_path.exists():
        log.info("fleurs: %s already exists", out_path)
        return out_path

    log.info("fleurs: downloading %s", fleurs_code)
    try:
        ds = load_dataset("google/fleurs", fleurs_code, split="test",
                          trust_remote_code=True)
    except Exception as e:
        log.warning("fleurs: failed for %s: %s", language, e)
        return Path()

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in ds:
            text = row.get("transcription", "").strip()
            if not text:
                continue
            f.write(json.dumps({
                "reference": text,
                "locale": language,
                "id": f"fleurs-{language}-{count:04d}",
                "source": "google/fleurs",
            }, ensure_ascii=False) + "\n")
            count += 1
            if count >= max_samples:
                break

    log.info("fleurs: wrote %d eval samples to %s", count, out_path)
    return out_path


def download_all_corpora(
    output_dir: Path,
    lang_pairs: Optional[list[tuple[str, str]]] = None,
    include_masakhane: bool = True,
    include_fleurs: bool = True,
) -> list[Path]:
    """Download JW300 + OPUS + Masakhane + FLEURS for all language pairs.

    Default pairs: en↔lg, en↔sw, en↔nyn, en↔ach.
    """
    if lang_pairs is None:
        lang_pairs = [
            ("en", "lg"),
            ("en", "sw"),
            ("en", "nyn"),
            ("en", "ach"),
        ]

    paths = []
    for src, tgt in lang_pairs:
        # JW300 — largest freely available corpus.
        paths.append(download_jw300(src, tgt, output_dir / "jw300"))

        # OPUS Tatoeba — high-quality short sentences.
        try:
            paths.append(download_opus("Tatoeba", src, tgt, output_dir / "opus"))
        except Exception as e:
            log.warning("Tatoeba %s-%s unavailable: %s", src, tgt, e)

        # Masakhane — community-curated African language MT.
        if include_masakhane:
            try:
                paths.append(download_masakhane(src, tgt, output_dir / "masakhane"))
            except Exception as e:
                log.warning("Masakhane %s-%s unavailable: %s", src, tgt, e)

    # FLEURS — ASR eval sets.
    if include_fleurs:
        for lang in {l for pair in lang_pairs for l in pair}:
            try:
                p = download_fleurs(lang, output_dir / "fleurs")
                if p and p.exists():
                    paths.append(p)
            except Exception as e:
                log.warning("FLEURS %s unavailable: %s", lang, e)

    return [p for p in paths if p.exists() and p.stat().st_size > 0]


# ---------------------------------------------------------------------------
# Pipeline integration — load downloaded corpora as TrainingExamples
# ---------------------------------------------------------------------------


def load_online_corpus(corpus_dir: Path) -> Iterator[TrainingExample]:
    """Read JSONL files produced by download_* functions and yield
    TrainingExamples formatted as translation instruction pairs."""

    jsonl_files = sorted(corpus_dir.rglob("*.jsonl"))
    log.info("online corpus loader: found %d JSONL files in %s", len(jsonl_files), corpus_dir)

    for jf in jsonl_files:
        if jf.stat().st_size == 0:
            continue

        try:
            for line in jf.open(encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue

                row = json.loads(line)
                src_text = row.get("source_text", "").strip()
                tgt_text = row.get("target_text", "").strip()
                src_lang = row.get("source_lang", "en")
                tgt_lang = row.get("target_lang", "lg")

                if not src_text or not tgt_text:
                    continue

                tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang.upper())
                src_name = LANG_NAMES.get(src_lang, src_lang.upper())

                yield TrainingExample(
                    messages=[
                        Message(role="system", content=URA_SYSTEM_PROMPT),
                        Message(
                            role="user",
                            content=f"Translate the following from {src_name} to {tgt_name}:\n\n{src_text}",
                        ),
                        Message(role="assistant", content=tgt_text),
                    ],
                    metadata=Metadata(
                        source=jf.name,
                        source_type=SourceType.ONLINE_CORPUS,
                        task=TaskType.TRANSLATION,
                        language=tgt_lang,
                        content_hash=content_hash(src_text, tgt_text),
                        doc_id=jf.stem,
                        extra={
                            "source_lang": src_lang,
                            "target_lang": tgt_lang,
                            "corpus": row.get("source_type", "unknown"),
                        },
                    ),
                )
        except Exception as e:
            log.warning("online corpus loader: error in %s: %s", jf.name, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Download parallel corpora")
    parser.add_argument("--output-dir", type=Path, default=Path("Data/online_corpora"))
    parser.add_argument(
        "--lang-pairs",
        nargs="+",
        default=["en-lg", "en-sw", "en-nyn", "en-ach"],
        help="Language pairs as src-tgt (e.g. en-sw en-nyn)",
    )
    parser.add_argument("--max-pairs", type=int, default=50_000)
    parser.add_argument("--no-masakhane", action="store_true", help="Skip Masakhane download")
    parser.add_argument("--no-fleurs", action="store_true", help="Skip FLEURS download")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    pairs = [tuple(p.split("-", 1)) for p in args.lang_pairs]
    paths = download_all_corpora(
        args.output_dir,
        lang_pairs=pairs,
        include_masakhane=not args.no_masakhane,
        include_fleurs=not args.no_fleurs,
    )
    log.info("Downloaded %d corpus files", len(paths))


if __name__ == "__main__":
    main()
