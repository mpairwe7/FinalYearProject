#!/usr/bin/env python3
"""Download pretrained ASR weights to the local artifacts cache.

Fetches commercial-safe (MIT) ASR backbones used by the URA speech pipeline:

    * openai/whisper-small                      — English + multilingual baseline
    * distil-whisper/distil-small.en            — 2x faster English-only
    * openai/whisper-tiny                       — fallback for very-low-RAM devices
    * silero-vad                                — voice activity detection (MIT)

All models are cached under ``artifacts/speech/asr/`` and reused by every
downstream script. Idempotent — a second invocation is a no-op if every
snapshot already exists.

Usage::

    python -m ml.scripts.asr.download_models             # default set
    python -m ml.scripts.asr.download_models --tiny      # include whisper-tiny
    python -m ml.scripts.asr.download_models --dry-run   # report only
    python -m ml.scripts.asr.download_models --model openai/whisper-small

License policy: all model IDs listed here MUST be in the commercial-safe
allowlist (MIT, Apache-2.0, BSD, CC0). New additions require an entry in
``ml/docs/model_cards/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("asr.download_models")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASR_CACHE_DIR = PROJECT_ROOT / "artifacts" / "speech" / "asr"


@dataclass
class ModelSpec:
    """A commercial-safe ASR snapshot."""

    repo_id: str
    license: str
    params: str
    role: str              # primary | alt | tiny | vad
    description: str
    allow_patterns: list[str] = field(default_factory=lambda: ["*"])


# ---------------------------------------------------------------------------
# Commercial-safe allowlist
# ---------------------------------------------------------------------------
# IMPORTANT: every entry here must be MIT / Apache-2.0 / CC0 / BSD.
# NO CC-BY-NC models (e.g. MMS, SeamlessM4T) may be added.

DEFAULT_MODELS: list[ModelSpec] = [
    ModelSpec(
        repo_id="openai/whisper-small",
        license="MIT",
        params="244M",
        role="primary",
        description="Multilingual ASR backbone. Used for both English and Luganda fine-tune.",
    ),
    ModelSpec(
        repo_id="distil-whisper/distil-small.en",
        license="MIT",
        params="166M",
        role="alt",
        description="English-only distilled Whisper. ~2x faster, similar quality on en.",
    ),
]

OPTIONAL_MODELS: dict[str, ModelSpec] = {
    "tiny": ModelSpec(
        repo_id="openai/whisper-tiny",
        license="MIT",
        params="39M",
        role="tiny",
        description="Smallest Whisper variant for very-low-RAM devices.",
    ),
    "vad": ModelSpec(
        repo_id="snakers4/silero-vad",
        license="MIT",
        params="1.8M",
        role="vad",
        description="Streaming voice activity detection (ONNX-ready).",
        allow_patterns=["*.onnx", "*.json", "*.md"],
    ),
}


def _spec_dest(spec: ModelSpec) -> Path:
    """Subdirectory under ASR_CACHE_DIR where this snapshot lives."""
    return ASR_CACHE_DIR / spec.repo_id.replace("/", "__")


def _already_cached(spec: ModelSpec) -> bool:
    """Heuristic: directory exists + contains at least one file."""
    dest = _spec_dest(spec)
    if not dest.is_dir():
        return False
    return any(dest.rglob("*"))


def _download_spec(spec: ModelSpec, *, force: bool = False) -> Optional[Path]:
    """Download one snapshot to the local cache.

    Returns the destination Path on success, None on failure.
    """
    dest = _spec_dest(spec)
    if _already_cached(spec) and not force:
        log.info("already cached: %s -> %s", spec.repo_id, dest)
        return dest

    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except ImportError:
        log.error("huggingface_hub missing. pip install huggingface-hub")
        return None

    dest.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=spec.repo_id,
            local_dir=str(dest),
            local_dir_use_symlinks=False,
            allow_patterns=spec.allow_patterns,
        )
    except Exception as exc:
        log.error("download failed for %s: %s", spec.repo_id, exc)
        return None

    log.info("downloaded %s -> %s", spec.repo_id, dest)
    return dest


def _write_manifest(entries: list[dict]) -> Path:
    """Write a SHA-free manifest so downstream scripts can discover the cache."""
    manifest_path = ASR_CACHE_DIR / "download_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"entries": entries}, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="Add an additional repo_id. Must be in the commercial-safe allowlist.",
    )
    parser.add_argument("--tiny", action="store_true", help="Also fetch whisper-tiny")
    parser.add_argument("--vad", action="store_true", help="Also fetch Silero VAD")
    parser.add_argument("--all", action="store_true", help="Fetch every optional model")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached")
    parser.add_argument("--dry-run", action="store_true", help="Report without downloading")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    specs: list[ModelSpec] = list(DEFAULT_MODELS)
    if args.tiny or args.all:
        specs.append(OPTIONAL_MODELS["tiny"])
    if args.vad or args.all:
        specs.append(OPTIONAL_MODELS["vad"])
    for repo in args.model:
        specs.append(
            ModelSpec(
                repo_id=repo,
                license="user-supplied",
                params="?",
                role="user",
                description="User-supplied repo — verify license manually.",
            )
        )

    log.info("ASR cache dir: %s", ASR_CACHE_DIR)
    log.info("target: %d model(s) (dry_run=%s, force=%s)", len(specs), args.dry_run, args.force)

    entries: list[dict] = []
    for spec in specs:
        cached = _already_cached(spec)
        dest = _spec_dest(spec)
        entry = {
            "repo_id": spec.repo_id,
            "license": spec.license,
            "params": spec.params,
            "role": spec.role,
            "description": spec.description,
            "destination": str(dest),
            "cached": cached,
        }
        if args.dry_run:
            log.info(
                "[dry-run] %s (%s, %s) -> %s %s",
                spec.repo_id,
                spec.license,
                spec.params,
                dest,
                "(cached)" if cached else "(would download)",
            )
            entries.append(entry)
            continue

        result = _download_spec(spec, force=args.force)
        entry["ok"] = result is not None
        entries.append(entry)

    if not args.dry_run:
        manifest = _write_manifest(entries)
        log.info("manifest: %s", manifest)

    ok = all(e.get("ok", True) for e in entries)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
