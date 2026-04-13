#!/usr/bin/env python3
"""Download commercial-safe TTS voices to the local artifacts cache.

Voices fetched (all MIT / Apache-2.0):

    * rhasspy/piper-voices                — MIT; the 2026 default English stack
        - en_US-lessac-medium             — general, high quality
        - en_US-libritts_r-medium         — alternative
    * hexgrad/Kokoro-82M                  — Apache-2.0; small mobile English TTS

**Luganda is not downloaded here** because no commercial-safe pretrained
Luganda TTS exists in 2026. The Luganda voice is trained in-project via
:mod:`ml.scripts.tts.train_luganda_vits` once voice data has been recorded.

Usage::

    python -m ml.scripts.tts.download_models             # Piper lessac only
    python -m ml.scripts.tts.download_models --all       # Piper + Kokoro
    python -m ml.scripts.tts.download_models --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("tts.download_models")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TTS_CACHE_DIR = PROJECT_ROOT / "artifacts" / "speech" / "tts"


@dataclass
class VoiceSpec:
    voice_id: str
    repo_id: str
    license: str
    size_mb: int
    language: str
    description: str
    allow_patterns: list[str] = field(default_factory=lambda: ["*"])
    role: str = "primary"


# Canonical commercial-safe allowlist. Extending this list requires a
# matching model card in ml/docs/model_cards/.
DEFAULT_VOICES: list[VoiceSpec] = [
    VoiceSpec(
        voice_id="en_US-lessac-medium",
        repo_id="rhasspy/piper-voices",
        license="MIT",
        size_mb=63,
        language="en",
        description="Piper lessac medium-quality English. Default mobile voice.",
        allow_patterns=[
            "en/en_US/lessac/medium/*.onnx",
            "en/en_US/lessac/medium/*.json",
            "en/en_US/lessac/medium/MODEL_CARD",
        ],
    ),
]

OPTIONAL_VOICES: dict[str, VoiceSpec] = {
    "libritts": VoiceSpec(
        voice_id="en_US-libritts_r-medium",
        repo_id="rhasspy/piper-voices",
        license="MIT",
        size_mb=63,
        language="en",
        description="Piper LibriTTS-R medium-quality alternative English voice.",
        role="alt",
        allow_patterns=[
            "en/en_US/libritts_r/medium/*.onnx",
            "en/en_US/libritts_r/medium/*.json",
            "en/en_US/libritts_r/medium/MODEL_CARD",
        ],
    ),
    "kokoro": VoiceSpec(
        voice_id="kokoro-82m",
        repo_id="hexgrad/Kokoro-82M",
        license="Apache-2.0",
        size_mb=82,
        language="en",
        description="Kokoro 82M — Apache-2.0 small mobile TTS with multiple voices.",
        role="alt",
    ),
}


def _voice_dest(spec: VoiceSpec) -> Path:
    return TTS_CACHE_DIR / spec.voice_id


def _already_cached(spec: VoiceSpec) -> bool:
    dest = _voice_dest(spec)
    return dest.is_dir() and any(dest.rglob("*"))


def _download_voice(spec: VoiceSpec, *, force: bool = False) -> Path | None:
    dest = _voice_dest(spec)
    if _already_cached(spec) and not force:
        log.info("already cached: %s", spec.voice_id)
        return dest
    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except ImportError:
        log.error("huggingface_hub missing — pip install huggingface-hub")
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
        log.error("download failed for %s: %s", spec.voice_id, exc)
        return None
    log.info("downloaded %s -> %s", spec.voice_id, dest)
    return dest


def _write_manifest(entries: list[dict]) -> Path:
    manifest_path = TTS_CACHE_DIR / "download_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"entries": entries}, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--all", action="store_true", help="Fetch every optional voice")
    parser.add_argument("--libritts", action="store_true")
    parser.add_argument("--kokoro", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    specs = list(DEFAULT_VOICES)
    if args.libritts or args.all:
        specs.append(OPTIONAL_VOICES["libritts"])
    if args.kokoro or args.all:
        specs.append(OPTIONAL_VOICES["kokoro"])

    log.info("TTS cache dir: %s", TTS_CACHE_DIR)
    log.info("target: %d voice(s) (dry_run=%s)", len(specs), args.dry_run)

    entries: list[dict] = []
    for spec in specs:
        cached = _already_cached(spec)
        entry = {
            "voice_id": spec.voice_id,
            "repo_id": spec.repo_id,
            "license": spec.license,
            "size_mb": spec.size_mb,
            "language": spec.language,
            "description": spec.description,
            "destination": str(_voice_dest(spec)),
            "cached": cached,
        }
        if args.dry_run:
            log.info(
                "[dry-run] %s (%s, %d MB) %s",
                spec.voice_id,
                spec.license,
                spec.size_mb,
                "(cached)" if cached else "(would download)",
            )
            entries.append(entry)
            continue
        result = _download_voice(spec, force=args.force)
        entry["ok"] = result is not None
        entries.append(entry)

    if not args.dry_run:
        manifest = _write_manifest(entries)
        log.info("manifest: %s", manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
