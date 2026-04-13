"""Online dataset downloader and integration (2026).

Downloads parallel corpora, ASR, and TTS datasets from public sources,
normalises them into the project's canonical JSONL format, and provides
loaders that the augmentation pipeline can consume.

Supported sources:

    * **Sunbird SALT** — 25k parallel sentences for 10 Ugandan languages
      including Runyankole + Acholi + multispeaker ASR + TTS (CC-BY-SA-4.0)
    * **Google WAXAL** — 132k nyn ASR + 114k ach ASR + TTS samples
      collected by Makerere AI Lab (CC-BY-SA-4.0)
    * **JW300** — CC0 parallel corpus via HuggingFace ``opus/JW300``
    * **OPUS** — Various OPUS sub-corpora (Tatoeba, Mozilla-I10n)
    * **Masakhane LAFAND-MT** — African language MT benchmark (CC-BY-NC-4.0)
    * **FLORES-200** — MT evaluation benchmark for nyn/ach (CC-BY-SA-4.0)
    * **Google FLEURS** — ASR eval set for multilingual coverage
    * **Common Voice** — ASR clips (delegates to ``asr_loaders.py``)

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
# Sunbird SALT — 25k parallel sentences, 10 Ugandan languages (CC-BY-SA-4.0)
# ---------------------------------------------------------------------------


def download_salt(
    output_dir: Path,
    languages: Optional[list[str]] = None,
) -> list[Path]:
    """Download Sunbird SALT parallel text + speech data.

    SALT is the single highest-value dataset for Runyankole and Acholi.
    Contains 25k parallel sentences across en/lg/sw/nyn/ach + multispeaker
    ASR recordings + studio TTS recordings.

    HuggingFace: ``Sunbird/salt``
    License: CC-BY-SA-4.0
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    if languages is None:
        languages = ["ach", "nyn", "lug", "eng", "swa", "teo", "lgg", "lug"]

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    # 1. Text data (parallel sentences).
    text_path = output_dir / "salt_text_all.jsonl"
    if not text_path.exists():
        log.info("salt: downloading text-all split")
        try:
            ds = load_dataset("Sunbird/salt", "text-all", split="train",
                              trust_remote_code=True)
            count = 0
            with text_path.open("w", encoding="utf-8") as f:
                for row in ds:
                    # SALT has columns per language: eng, lug, ach, nyn, etc.
                    eng = str(row.get("eng", "")).strip()
                    if not eng:
                        continue
                    for lang_code, col_name in [
                        ("lg", "lug"), ("ach", "ach"), ("nyn", "nyn"),
                        ("sw", "swa"), ("en", "eng"),
                    ]:
                        text = str(row.get(col_name, "")).strip()
                        if text and lang_code != "en":
                            f.write(json.dumps({
                                "source_text": eng,
                                "target_text": text,
                                "source_lang": "en",
                                "target_lang": lang_code,
                                "source_type": "salt",
                                "license": "cc_by_sa",
                            }, ensure_ascii=False) + "\n")
                            count += 1
            log.info("salt: wrote %d parallel pairs to %s", count, text_path)
        except Exception as e:
            log.warning("salt: text download failed: %s", e)
            text_path.write_text("")
    paths.append(text_path)

    # 2. ASR data (multispeaker recordings) — write metadata only.
    for lang in ["ach", "nyn", "lug"]:
        asr_path = output_dir / f"salt_asr_{lang}.jsonl"
        if asr_path.exists():
            paths.append(asr_path)
            continue
        try:
            ds = load_dataset("Sunbird/salt", f"multispeaker-{lang}",
                              split="train", trust_remote_code=True)
            count = 0
            with asr_path.open("w", encoding="utf-8") as f:
                for row in ds:
                    text = str(row.get("text", "")).strip()
                    if not text:
                        continue
                    f.write(json.dumps({
                        "reference": text,
                        "locale": lang if lang != "lug" else "lg",
                        "source": "Sunbird/salt",
                        "source_type": "salt_asr",
                        "has_audio": True,
                    }, ensure_ascii=False) + "\n")
                    count += 1
            log.info("salt: wrote %d ASR samples for %s", count, lang)
            paths.append(asr_path)
        except Exception as e:
            log.warning("salt: ASR %s failed: %s", lang, e)

    return [p for p in paths if p.exists() and p.stat().st_size > 0]


# ---------------------------------------------------------------------------
# Google WAXAL — 132k nyn ASR + 114k ach ASR + TTS (CC-BY-SA-4.0)
# ---------------------------------------------------------------------------


def download_waxal(
    output_dir: Path,
    languages: Optional[list[str]] = None,
    max_samples: int = 150_000,
) -> list[Path]:
    """Download Google WAXAL NLP dataset (Makerere AI Lab, 2026).

    21 African languages, 11k+ hours. For URA Chatbot, we need:
    - nyn_asr (132k samples) + nyn_tts (1,990 samples)
    - ach_asr (114k samples) + ach_tts (2,030 samples)
    - lug_asr (98.5k samples) + lug_tts (2,020 samples)

    HuggingFace: ``google/WaxalNLP``
    License: CC-BY-SA-4.0 / CC-BY-4.0
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    if languages is None:
        languages = ["nyn", "ach", "lug"]

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for lang in languages:
        # ASR data.
        for split_type in ["asr", "tts"]:
            config_name = f"{lang}_{split_type}"
            out_path = output_dir / f"waxal_{config_name}.jsonl"

            if out_path.exists():
                paths.append(out_path)
                continue

            log.info("waxal: downloading %s", config_name)
            try:
                ds = load_dataset("google/WaxalNLP", config_name,
                                  split="train", trust_remote_code=True)
                count = 0
                with out_path.open("w", encoding="utf-8") as f:
                    for row in ds:
                        text = str(row.get("transcription", row.get("text", ""))).strip()
                        if not text:
                            continue
                        # Map 3-letter codes to our 2/3-letter codes.
                        locale = {"lug": "lg", "nyn": "nyn", "ach": "ach"}.get(lang, lang)
                        f.write(json.dumps({
                            "reference": text,
                            "locale": locale,
                            "source": "google/WaxalNLP",
                            "source_type": f"waxal_{split_type}",
                            "has_audio": True,
                        }, ensure_ascii=False) + "\n")
                        count += 1
                        if count >= max_samples:
                            break
                log.info("waxal: wrote %d %s samples for %s", count, split_type, lang)
                paths.append(out_path)
            except Exception as e:
                log.warning("waxal: %s failed: %s", config_name, e)

    return [p for p in paths if p.exists() and p.stat().st_size > 0]


# ---------------------------------------------------------------------------
# FLORES-200 — MT evaluation benchmark (CC-BY-SA-4.0)
# ---------------------------------------------------------------------------


def download_flores(
    output_dir: Path,
    languages: Optional[list[str]] = None,
) -> list[Path]:
    """Download FLORES-200 devtest sets for MT evaluation.

    ~1,012 high-quality sentences per language. Used as eval benchmark
    for MT quality gates, not for training.

    HuggingFace: ``facebook/flores`` or ``openlanguagedata/flores_plus``
    License: CC-BY-SA-4.0
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    if languages is None:
        languages = ["nyn_Latn", "ach_Latn", "lug_Latn", "swh_Latn", "eng_Latn"]

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for lang_code in languages:
        out_path = output_dir / f"flores_{lang_code}.jsonl"
        if out_path.exists():
            paths.append(out_path)
            continue

        log.info("flores: downloading %s", lang_code)
        try:
            ds = load_dataset("facebook/flores", lang_code, split="devtest",
                              trust_remote_code=True)
            count = 0
            with out_path.open("w", encoding="utf-8") as f:
                for row in ds:
                    text = str(row.get("sentence", "")).strip()
                    if not text:
                        continue
                    # Shorten code: nyn_Latn → nyn
                    short = lang_code.split("_")[0]
                    locale = {"lug": "lg", "swh": "sw", "eng": "en"}.get(short, short)
                    f.write(json.dumps({
                        "reference": text,
                        "locale": locale,
                        "id": f"flores-{locale}-{count:04d}",
                        "source": "facebook/flores",
                    }, ensure_ascii=False) + "\n")
                    count += 1
            log.info("flores: wrote %d sentences for %s", count, lang_code)
            paths.append(out_path)
        except Exception as e:
            log.warning("flores: %s failed: %s", lang_code, e)

    return [p for p in paths if p.exists() and p.stat().st_size > 0]


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


# ---------------------------------------------------------------------------
# SALT (Sunbird African Language Technology)
# ---------------------------------------------------------------------------
#
# Active replacement for JW300 / OPUS-Tatoeba / lafand-mt, whose HF loaders
# are deprecated (opus/JW300 returns 404; opus/Tatoeba unavailable; lafand-mt
# ships none of the en-{lg,sw,nyn,ach} pairs we need).
#
# `Sunbird/salt` hosts a single `text-all` config where every row carries
# parallel translations for the five Ugandan languages we care about
# (eng / swa / lug / nyn / ach) plus teo, lgg, xog, ttj, ibo. One download
# covers all four language pairs we target.

# SALT uses ISO-639-3 column names; project uses 2-letter or short codes.
_SALT_COL_FOR_LANG = {
    "en": "eng_source_text",
    "sw": "swa_text",
    "lg": "lug_text",
    "nyn": "nyn_text",
    "ach": "ach_text",
}


def download_salt(
    src_lang: str,
    tgt_lang: str,
    output_dir: Path,
    max_pairs: int = 50_000,
) -> Path:
    """Download parallel pairs from the SALT multi-way corpus.

    One download of the `text-all` config yields rows with all five languages
    filled in; this function projects each row into a single src→tgt pair.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"salt_{src_lang}_{tgt_lang}.jsonl"

    if out_path.exists() and out_path.stat().st_size > 0:
        log.info("salt: %s already exists, skipping", out_path)
        return out_path

    src_col = _SALT_COL_FOR_LANG.get(src_lang)
    tgt_col = _SALT_COL_FOR_LANG.get(tgt_lang)
    if not src_col or not tgt_col:
        log.warning("salt: %s-%s not covered by SALT schema", src_lang, tgt_lang)
        out_path.write_text("")
        return out_path

    log.info("salt: downloading text-all for %s-%s", src_lang, tgt_lang)
    try:
        ds = load_dataset("Sunbird/salt", "text-all", split="train")
    except Exception as e:
        log.warning("salt: load failed: %s", e)
        out_path.write_text("")
        return out_path

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in ds:
            src_text = clean_text(str(row.get(src_col, ""))).strip()
            tgt_text = clean_text(str(row.get(tgt_col, ""))).strip()
            if not src_text or not tgt_text or len(src_text) < 5:
                continue
            f.write(json.dumps({
                "source_text": src_text,
                "target_text": tgt_text,
                "source_lang": src_lang,
                "target_lang": tgt_lang,
                "source_type": "salt",
                "license": LicenseClass.CC_BY.value,
            }, ensure_ascii=False) + "\n")
            count += 1
            if count >= max_pairs:
                break

    log.info("salt: wrote %d pairs to %s", count, out_path)
    return out_path


# ---------------------------------------------------------------------------
# Common Voice (Luganda) — via `fsicoli/common_voice_19_0` mirror
# ---------------------------------------------------------------------------
#
# `mozilla-foundation/common_voice_17_0` now only hosts README.md on HF;
# `fsicoli/common_voice_19_0` mirrors the audio tarballs for every locale
# and is the current working path via `datasets.load_dataset`.


def download_common_voice(
    language: str,
    output_dir: Path,
    repo: str = "fsicoli/common_voice_19_0",
    split: str = "train+validation+test",
) -> Path:
    """Download Common Voice audio + transcripts for a language."""
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("datasets library required")

    output_dir.mkdir(parents=True, exist_ok=True)
    target_dir = output_dir / f"common_voice_{language}"

    if target_dir.exists() and any(target_dir.iterdir()):
        log.info("common_voice: %s already exists, skipping", target_dir)
        return target_dir

    log.info("common_voice: downloading %s / %s (split=%s)", repo, language, split)
    try:
        ds = load_dataset(repo, language, split=split, trust_remote_code=True)
    except Exception as e:
        log.warning("common_voice: %s failed: %s", repo, e)
        return target_dir

    ds.save_to_disk(str(target_dir))
    log.info("common_voice: wrote %d clips to %s", len(ds), target_dir)
    return target_dir


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
    include_salt: bool = True,
    include_fleurs: bool = True,
    include_legacy: bool = False,
) -> list[Path]:
    """Download SALT + FLEURS (+ legacy JW300/OPUS/Masakhane if requested).

    Default pairs: en↔lg, en↔sw, en↔nyn, en↔ach.

    `include_legacy=False` by default because the HF loaders for
    `opus/JW300`, `opus/Tatoeba`, and `masakhane/lafand-mt` are deprecated
    for our language pairs; SALT covers all four with richer parallel data.
    """
    if lang_pairs is None:
        lang_pairs = [
            ("en", "lg"),
            ("en", "sw"),
            ("en", "nyn"),
            ("en", "ach"),
        ]

    paths = []

    # --- Priority 1: Sunbird SALT (highest quality for nyn/ach) ---
    if include_salt:
        try:
            paths.extend(download_salt(output_dir / "salt"))
        except Exception as e:
            log.warning("SALT unavailable: %s", e)

    # --- Priority 2: Google WAXAL (largest ASR/TTS for nyn/ach) ---
    if include_waxal:
        try:
            paths.extend(download_waxal(output_dir / "waxal"))
        except Exception as e:
            log.warning("WAXAL unavailable: %s", e)

    # --- Priority 3-5: JW300 + OPUS + Masakhane per language pair ---
    for src, tgt in lang_pairs:
        if include_salt:
            try:
                paths.append(download_salt(src, tgt, output_dir / "salt"))
            except Exception as e:
                log.warning("SALT %s-%s unavailable: %s", src, tgt, e)

        if include_legacy:
            # JW300 / OPUS Tatoeba / lafand-mt — kept as sentinels only.
            paths.append(download_jw300(src, tgt, output_dir / "jw300"))
            try:
                paths.append(download_opus("Tatoeba", src, tgt, output_dir / "opus"))
            except Exception as e:
                log.warning("Tatoeba %s-%s unavailable: %s", src, tgt, e)
            try:
                paths.append(download_masakhane(src, tgt, output_dir / "masakhane"))
            except Exception as e:
                log.warning("Masakhane %s-%s unavailable: %s", src, tgt, e)

    # --- Priority 6: FLORES-200 (MT eval benchmark) ---
    if include_flores_eval:
        try:
            paths.extend(download_flores(output_dir / "flores"))
        except Exception as e:
            log.warning("FLORES unavailable: %s", e)

    # --- Priority 7: FLEURS (ASR eval) ---
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
    parser.add_argument("--no-salt", action="store_true", help="Skip SALT download")
    parser.add_argument("--no-fleurs", action="store_true", help="Skip FLEURS download")
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Also try JW300/OPUS Tatoeba/lafand-mt (deprecated; produces sentinels)",
    )
    parser.add_argument(
        "--common-voice",
        nargs="*",
        default=[],
        help="Languages to fetch via fsicoli/common_voice_19_0 (e.g. lg sw)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    pairs = [tuple(p.split("-", 1)) for p in args.lang_pairs]
    paths = download_all_corpora(
        args.output_dir,
        lang_pairs=pairs,
        include_salt=not args.no_salt,
        include_fleurs=not args.no_fleurs,
        include_legacy=args.include_legacy,
    )
    log.info("Downloaded %d corpus files", len(paths))

    for cv_lang in args.common_voice:
        download_common_voice(cv_lang, args.output_dir.parent)


if __name__ == "__main__":
    main()
