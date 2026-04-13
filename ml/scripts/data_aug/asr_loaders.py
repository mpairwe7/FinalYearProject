"""ASR data loaders (2026).

Yield :class:`ml.scripts.data_aug.speech_schema.AudioExample` from common
public ASR corpora. Mirrors the pattern of :mod:`ml.scripts.data_aug.loaders`
so that downstream code can consume either text or audio rows with the same
lazy-iterator interface.

Supported sources (commercial-safe only):

    * **Common Voice** (``mozilla-foundation/common_voice_17_0`` on HF)
      License: **CC0-1.0** (public-domain declaration).
    * **SALT-ASR** (Makerere SALT corpus)
      License: **CC-BY-4.0** — commercial use with attribution.
    * **Local wav+tsv** — a project directory with ``validated.tsv`` mapping
      file -> reference.

Consumed by:
    * ``ml/scripts/asr/train_luganda.py``         — Whisper fine-tune
    * ``ml/pipelines/evaluate_speech.py``          — eval harness
    * ``ml/scripts/data_aug/pipeline.py``          — combined ingest stage
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ml.scripts.data_aug.speech_schema import (
    AudioExample,
    AudioSourceType,
    LicenseClass,
    audio_content_hash,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _probe_audio_duration(path: Path) -> tuple[float | None, int | None]:
    """Best-effort probe for duration + sample rate. Returns (None, None) if
    soundfile/librosa are unavailable. No hard dependency so dry-runs work."""
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(str(path))
        return float(info.duration), int(info.samplerate)
    except Exception:
        pass
    try:
        import librosa  # type: ignore

        duration = librosa.get_duration(path=str(path))
        # librosa can't tell us sr without loading; return 16000 as a neutral default.
        return float(duration), 16000
    except Exception:
        return None, None


def _looks_like_audio(path: Path) -> bool:
    return path.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


# ---------------------------------------------------------------------------
# Common Voice (Luganda + English)
# ---------------------------------------------------------------------------


def load_common_voice(
    data_dir: Path,
    *,
    language: str = "lg",
    split_tsv: str = "validated.tsv",
    max_examples: int | None = None,
) -> Iterator[AudioExample]:
    """Yield rows from a local Common Voice snapshot.

    Common Voice ships a directory like::

        common_voice_lg/
          clips/
            common_voice_lg_23830019.mp3
            ...
          validated.tsv           # client_id, path, sentence, ...
          other.tsv, invalidated.tsv, ...

    This loader uses ``validated.tsv`` by default (those are clips that passed
    human review), falling back to any other TSV when validated is missing.
    If there is no TSV at all (e.g. the shipped ``Data/lgaudio/`` directory)
    we still yield bare AudioExamples with empty ``reference_text`` so the
    pipeline can surface them as training candidates awaiting transcription.
    """
    # Common Voice client_id fields can be very long UUIDs; raise the CSV field limit.
    csv.field_size_limit(1 << 20)  # 1 MB

    if not data_dir.exists():
        log.info("common_voice: dir not found %s", data_dir)
        return

    clips_dir = data_dir / "clips"
    if not clips_dir.exists():
        # Some dataset layouts put clips at the root (e.g. Data/lgaudio).
        clips_dir = data_dir

    tsv_paths = [data_dir / split_tsv]
    tsv_paths += sorted(data_dir.glob("*.tsv"))
    tsv_paths = [p for p in tsv_paths if p.exists()]
    if not tsv_paths:
        log.warning(
            "common_voice: %s has audio but no TSV transcripts — emitting un-transcribed rows",
            data_dir,
        )
        yield from _emit_untranscribed(
            clips_dir, language=language, source="common_voice_untranscribed"
        )
        return

    tsv_path = tsv_paths[0]
    log.info("common_voice: loading %s", tsv_path)
    count = 0
    with open(tsv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            rel = row.get("path") or row.get("filename") or row.get("clip") or ""
            sentence = row.get("sentence") or row.get("text") or ""
            if not rel or not sentence:
                continue
            audio_path = clips_dir / rel
            if not audio_path.exists():
                log.debug("common_voice: missing clip %s", audio_path)
                continue

            duration, sample_rate = _probe_audio_duration(audio_path)
            if duration is None or sample_rate is None:
                # Use neutral defaults — the training loader validates again.
                duration = 0.0
                sample_rate = 16000

            try:
                yield AudioExample(
                    audio_path=str(audio_path.relative_to(Path.cwd()))
                    if audio_path.is_relative_to(Path.cwd())
                    else str(audio_path),
                    sample_rate=sample_rate
                    if sample_rate in {8000, 16000, 22050, 24000, 44100, 48000}
                    else 16000,
                    duration_s=duration if duration > 0 else 1.0,
                    reference_text=sentence.strip(),
                    language=language,
                    source=tsv_path.name,
                    source_type=AudioSourceType.COMMON_VOICE,
                    license=LicenseClass.PUBLIC_DOMAIN,
                    speaker_id=row.get("client_id") or None,
                    content_hash=audio_content_hash(str(audio_path), sentence),
                )
                count += 1
                if max_examples is not None and count >= max_examples:
                    break
            except Exception as exc:
                log.warning("common_voice: skipping row: %s", exc)
    log.info("common_voice: %d examples", count)


def _emit_untranscribed(clips_dir: Path, *, language: str, source: str) -> Iterator[AudioExample]:
    """Yield AudioExample rows with empty reference for clips awaiting transcription."""
    count = 0
    for audio_path in sorted(clips_dir.rglob("*")):
        if not _looks_like_audio(audio_path):
            continue
        duration, sample_rate = _probe_audio_duration(audio_path)
        if duration is None:
            duration = 1.0
        if sample_rate not in {8000, 16000, 22050, 24000, 44100, 48000}:
            sample_rate = 16000
        try:
            yield AudioExample(
                audio_path=str(audio_path),
                sample_rate=sample_rate,
                duration_s=max(duration, 0.1),
                # Empty ref is disallowed by the schema; use a placeholder that
                # downstream filters recognise and drop before training.
                reference_text="[UNTRANSCRIBED]",
                language=language,
                source=source,
                source_type=AudioSourceType.COMMON_VOICE,
                license=LicenseClass.PUBLIC_DOMAIN,
                content_hash=audio_content_hash(str(audio_path), audio_path.name),
            )
            count += 1
        except Exception as exc:
            log.debug("skipping %s: %s", audio_path, exc)
    log.info("common_voice: %d untranscribed clips", count)


def load_common_voice_lg(data_dir: Path, **kwargs) -> Iterator[AudioExample]:
    """Luganda-specific wrapper around load_common_voice."""
    return load_common_voice(data_dir, language="lg", **kwargs)


# ---------------------------------------------------------------------------
# SALT-ASR (Makerere)
# ---------------------------------------------------------------------------


def load_salt_asr(data_dir: Path, *, language: str = "lg") -> Iterator[AudioExample]:
    """Yield rows from a local SALT-ASR snapshot.

    SALT ships as ``train.tsv`` / ``dev.tsv`` / ``test.tsv`` with
    tab-separated ``audio_path\treference\tspeaker_id`` lines. If your
    copy differs, pass ``split_tsv=<filename>`` explicitly.

    License: CC-BY-4.0 (attribution required in model card).
    """
    if not data_dir.exists():
        log.info("salt_asr: dir not found %s", data_dir)
        return

    for split in ("train.tsv", "dev.tsv", "test.tsv", "validated.tsv"):
        tsv_path = data_dir / split
        if not tsv_path.exists():
            continue
        log.info("salt_asr: loading %s", tsv_path)
        count = 0
        with open(tsv_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                audio_rel = row.get("audio") or row.get("path") or row.get("file")
                reference = row.get("text") or row.get("reference") or row.get("sentence")
                if not audio_rel or not reference:
                    continue
                audio_path = data_dir / audio_rel
                if not audio_path.exists():
                    log.debug("salt_asr: missing clip %s", audio_path)
                    continue
                duration, sample_rate = _probe_audio_duration(audio_path)
                try:
                    yield AudioExample(
                        audio_path=str(audio_path),
                        sample_rate=sample_rate or 16000,
                        duration_s=duration or 1.0,
                        reference_text=reference.strip(),
                        language=language,
                        source=f"salt_asr:{split}",
                        source_type=AudioSourceType.SALT_ASR,
                        license=LicenseClass.CC_BY,
                        speaker_id=row.get("speaker_id") or None,
                        content_hash=audio_content_hash(str(audio_path), reference),
                    )
                    count += 1
                except Exception as exc:
                    log.warning("salt_asr: skipping row: %s", exc)
        log.info("salt_asr: %s -> %d examples", split, count)


# ---------------------------------------------------------------------------
# Generic JSONL loader (already-curated eval sets)
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> Iterator[AudioExample]:
    """Yield AudioExample rows from an already-curated JSONL file."""
    if not path.exists():
        log.info("asr jsonl not found %s", path)
        return
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("asr jsonl %s:%d invalid: %s", path.name, lineno, exc)
                continue
            # Accept both our canonical schema and the loose eval format.
            if "reference_text" not in rec and "reference" in rec:
                rec["reference_text"] = rec.pop("reference")
            if not rec.get("reference_text"):
                continue
            try:
                yield AudioExample(**{k: v for k, v in rec.items() if not k.startswith("_")})
            except Exception as exc:
                log.warning("asr jsonl %s:%d schema: %s", path.name, lineno, exc)


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


def load_asr_corpus(
    *,
    common_voice_dir: Path | None = None,
    salt_dir: Path | None = None,
    jsonl_files: list[Path] | None = None,
) -> Iterator[AudioExample]:
    """Compose every available ASR source into a single iterator."""
    if common_voice_dir is not None:
        yield from load_common_voice(common_voice_dir)
    if salt_dir is not None:
        yield from load_salt_asr(salt_dir)
    for path in jsonl_files or []:
        yield from load_jsonl(path)
