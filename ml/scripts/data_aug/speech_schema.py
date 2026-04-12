"""Canonical schemas for speech, translation and TTS examples (2026).

This module is the speech-pipeline counterpart of ``ml/scripts/data_aug/schema.py``
which defines the chat-format :class:`TrainingExample` used by the existing LLM
pipeline. The types here are used by:

    * ``ml/scripts/data_aug/asr_loaders.py``  — Common Voice / SALT-ASR ingest
    * ``ml/scripts/data_aug/mt_loaders.py``   — Luganda parallel corpus ingest
    * ``ml/pipelines/evaluate_speech.py``     — ASR eval harness
    * ``ml/pipelines/evaluate_mt.py``         — MT eval harness
    * ``ml/pipelines/evaluate_tts.py``        — TTS eval harness

Design notes:

* Every example carries a ``license`` field so the dataset manifest can be
  audited for commercial use. CC-BY-NC rows are *allowed* in the corpus but
  are filtered out of the production mobile bundle; the filter is enforced by
  ``ml/scripts/speech/export_mobile_speech.py``.
* Pydantic v2, ``ConfigDict(extra="forbid")`` to catch schema drift early.
* No hashing of raw audio bytes here — that happens at the loader level after
  content-addressed storage decisions are made.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "2026.1"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AudioSourceType(str, Enum):
    """Provenance for audio clips. Used for per-source WER slicing + audit."""

    COMMON_VOICE = "common_voice"
    SALT_ASR = "salt_asr"
    FLEURS = "fleurs"
    PROJECT_RECORDING = "project_recording"   # URA-specific recordings
    SYNTHETIC_TTS = "synthetic_tts"           # TTS used for ASR test-time augmentation
    REDTEAM = "redteam"                       # adversarial voice probes


class MTSourceType(str, Enum):
    """Provenance for MT parallel pairs."""

    LUGANDA_PARALLEL = "luganda_parallel"     # existing Data/TTT corpus
    BACKTRANSLATION = "backtranslation"       # synthetic Lg->En from MT model
    CSV_FAQ_TRANSLATED = "csv_faq_translated" # URA FAQs translated
    EVAL_GOLD = "eval_gold"                   # human-reviewed gold eval set
    JW300 = "jw300"                           # JW300 parallel corpus (CC0)
    OPUS = "opus"                             # OPUS parallel corpora
    SWAHILI_PARALLEL = "swahili_parallel"     # en↔sw parallel data
    RUNYANKOLE_PARALLEL = "runyankole_parallel"  # en↔nyn parallel data
    ACHOLI_PARALLEL = "acholi_parallel"       # en↔ach parallel data
    MASAKHANE = "masakhane"                   # Masakhane MT benchmark (CC-BY-4.0)
    FLEURS = "fleurs"                        # Google FLEURS ASR eval set


class TTSSourceType(str, Enum):
    """Provenance for TTS training data (speaker recordings)."""

    PROJECT_VOICE = "project_voice"           # project-recorded speaker
    PUBLIC_CC0 = "public_cc0"                 # CC0 public-domain audio
    CROWDSOURCED = "crowdsourced"


class LicenseClass(str, Enum):
    """Commercial-safety classification used by the mobile bundle filter."""

    PUBLIC_DOMAIN = "public_domain"           # CC0, PDM
    PERMISSIVE = "permissive"                 # MIT, Apache-2.0, BSD, MPL-2.0
    CC_BY = "cc_by"                           # attribution required
    CC_BY_SA = "cc_by_sa"                     # share-alike copyleft
    CC_BY_NC = "cc_by_nc"                     # NON-COMMERCIAL — excluded from mobile bundle
    PROPRIETARY = "proprietary"               # project-internal
    GEMMA_TERMS = "gemma_terms"               # Google Gemma licence (commercial OK w/ restrictions)
    UNKNOWN = "unknown"                       # requires manual review

    @property
    def is_commercial_safe(self) -> bool:
        return self in {
            LicenseClass.PUBLIC_DOMAIN,
            LicenseClass.PERMISSIVE,
            LicenseClass.CC_BY,
            LicenseClass.PROPRIETARY,
            LicenseClass.GEMMA_TERMS,
        }


COMMERCIAL_SAFE_LICENSES = frozenset(lc for lc in LicenseClass if lc.is_commercial_safe)


# ---------------------------------------------------------------------------
# AudioExample — the unit of ASR training / evaluation
# ---------------------------------------------------------------------------


class AudioExample(BaseModel):
    """One audio clip + reference transcript.

    ``audio_path`` is a project-relative path (string) rather than a Path
    object so that manifests round-trip through JSON cleanly.
    """

    model_config = ConfigDict(extra="forbid")

    # Audio
    audio_path: str                    # project-relative
    sample_rate: int                   # Hz — must match speech_config.audio.sample_rate
    duration_s: float
    channels: int = 1

    # Transcript
    reference_text: str
    language: str = "en"               # ISO 639-1: en, lg, ...

    # Provenance + license (audit)
    source: str                        # filename / dataset subdir
    source_type: AudioSourceType
    license: LicenseClass = LicenseClass.UNKNOWN
    speaker_id: Optional[str] = None   # pseudonymised

    # Deduplication key (sha256[:16] of audio_path + reference_text).
    content_hash: Optional[str] = None

    # Free-form for debugging / lineage.
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reference_text")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reference_text must be non-empty")
        return v.strip()

    @field_validator("duration_s")
    @classmethod
    def _valid_duration(cls, v: float) -> float:
        if v <= 0 or v > 600:
            raise ValueError(f"duration_s must be in (0, 600], got {v}")
        return v

    @field_validator("sample_rate")
    @classmethod
    def _valid_sr(cls, v: int) -> int:
        if v not in {8000, 16000, 22050, 24000, 44100, 48000}:
            raise ValueError(f"unsupported sample_rate: {v}")
        return v

    def to_row(self) -> dict[str, Any]:
        return {
            "audio_path": self.audio_path,
            "sample_rate": self.sample_rate,
            "duration_s": self.duration_s,
            "channels": self.channels,
            "reference_text": self.reference_text,
            "language": self.language,
            "source": self.source,
            "source_type": self.source_type.value,
            "license": self.license.value,
            "speaker_id": self.speaker_id,
            "content_hash": self.content_hash,
            "extra": self.extra,
        }


def audio_content_hash(audio_path: str, reference_text: str) -> str:
    """Stable 16-char hash for AudioExample deduplication."""
    import re

    def _norm(t: str) -> str:
        return re.sub(r"\s+", " ", (t or "").lower()).strip()

    payload = (audio_path + "\x1f" + _norm(reference_text)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# MTExample — the unit of translation training / evaluation
# ---------------------------------------------------------------------------


class MTExample(BaseModel):
    """One parallel sentence pair for machine translation."""

    model_config = ConfigDict(extra="forbid")

    source_text: str
    target_text: str
    source_lang: str                   # ISO 639-1
    target_lang: str                   # ISO 639-1

    source: str                        # filename / dataset id
    source_type: MTSourceType
    license: LicenseClass = LicenseClass.UNKNOWN

    # Synthetic flag — True for rows produced by backtranslate.py.
    is_synthetic: bool = False

    # Per-row quality score (0..1) assigned by filters.
    quality_score: Optional[float] = None
    length_ratio: Optional[float] = None

    content_hash: Optional[str] = None
    doc_id: Optional[str] = None

    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_text", "target_text")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source/target text must be non-empty")
        return v.strip()

    @field_validator("source_lang", "target_lang")
    @classmethod
    def _iso_lang(cls, v: str) -> str:
        if len(v) not in (2, 3):
            raise ValueError(f"language must be ISO 639-1/2 code, got {v!r}")
        return v.lower()

    def to_row(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "target_text": self.target_text,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "source": self.source,
            "source_type": self.source_type.value,
            "license": self.license.value,
            "is_synthetic": self.is_synthetic,
            "quality_score": self.quality_score,
            "length_ratio": self.length_ratio,
            "content_hash": self.content_hash,
            "doc_id": self.doc_id,
            "extra": self.extra,
        }


def mt_content_hash(source_text: str, target_text: str) -> str:
    """Stable 16-char hash for MTExample deduplication."""
    import re

    def _norm(t: str) -> str:
        return re.sub(r"\s+", " ", (t or "").lower()).strip()

    payload = (_norm(source_text) + "\x1f" + _norm(target_text)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# TTSExample — the unit of TTS training (speaker recordings)
# ---------------------------------------------------------------------------


class TTSExample(BaseModel):
    """One speaker recording + aligned text for TTS training."""

    model_config = ConfigDict(extra="forbid")

    text: str
    audio_path: str
    speaker_id: str                    # pseudonymised
    language: str = "lg"
    sample_rate: int
    duration_s: float

    source: str
    source_type: TTSSourceType
    license: LicenseClass = LicenseClass.UNKNOWN

    # Consent tracking — MANDATORY for voice recordings.
    consent_version: Optional[str] = None
    consent_timestamp: Optional[str] = None   # ISO 8601

    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("consent_version")
    @classmethod
    def _require_consent_for_real_speakers(cls, v: Optional[str]) -> Optional[str]:
        # Pydantic v2 validates per-field; cross-field consent check is done
        # by the loader rather than here to keep this class side-effect-free.
        return v

    def to_row(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "audio_path": self.audio_path,
            "speaker_id": self.speaker_id,
            "language": self.language,
            "sample_rate": self.sample_rate,
            "duration_s": self.duration_s,
            "source": self.source,
            "source_type": self.source_type.value,
            "license": self.license.value,
            "consent_version": self.consent_version,
            "consent_timestamp": self.consent_timestamp,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# Convenience: JSONL helpers
# ---------------------------------------------------------------------------


def iter_audio_jsonl(path: Path):
    """Yield AudioExample rows from a JSONL file. Invalid rows logged + skipped."""
    import json
    import logging

    log = logging.getLogger(__name__)
    if not path.exists():
        log.warning("audio jsonl not found: %s", path)
        return
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                yield AudioExample(**rec)
            except Exception as exc:
                log.warning("skipping %s:%d: %s", path.name, i, exc)


def iter_mt_jsonl(path: Path):
    """Yield MTExample rows from a JSONL file."""
    import json
    import logging

    log = logging.getLogger(__name__)
    if not path.exists():
        log.warning("mt jsonl not found: %s", path)
        return
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                yield MTExample(**rec)
            except Exception as exc:
                log.warning("skipping %s:%d: %s", path.name, i, exc)
