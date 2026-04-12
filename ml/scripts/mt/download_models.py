#!/usr/bin/env python3
"""Download commercial-safe MT backbones to the local artifacts cache.

Commercial-safe allowlist:

    * google/madlad400-3b-mt          Apache-2.0
    * google/madlad400-7b-mt          Apache-2.0 (large; gated to --full)
    * Helsinki-NLP/opus-mt-mul-en     CC-BY-4.0  (fallback, attribution only)

Explicitly NOT in the allowlist:

    * facebook/nllb-200-*             CC-BY-NC-4.0
    * facebook/mms-*                  CC-BY-NC-4.0

Usage::

    python -m ml.scripts.mt.download_models           # MADLAD-3B only
    python -m ml.scripts.mt.download_models --opus    # + Opus-MT fallback
    python -m ml.scripts.mt.download_models --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("mt.download_models")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MT_CACHE_DIR = PROJECT_ROOT / "artifacts" / "mt" / "models"


@dataclass
class MtModelSpec:
    repo_id: str
    license: str
    params: str
    role: str
    description: str
    allow_patterns: list[str] = field(default_factory=lambda: ["*"])


DEFAULT_MODELS: list[MtModelSpec] = [
    MtModelSpec(
        repo_id="google/madlad400-3b-mt",
        license="Apache-2.0",
        params="3B",
        role="primary",
        description="MADLAD-400 3B — multilingual MT covering Luganda (lg).",
    ),
]

OPTIONAL_MODELS: dict[str, MtModelSpec] = {
    "full": MtModelSpec(
        repo_id="google/madlad400-7b-mt",
        license="Apache-2.0",
        params="7B",
        role="alt",
        description="MADLAD-400 7B — higher quality, server-only.",
    ),
    "opus": MtModelSpec(
        repo_id="Helsinki-NLP/opus-mt-mul-en",
        license="CC-BY-4.0",
        params="84M",
        role="fallback",
        description="Opus-MT many-to-English — fallback for low-resource pairs.",
    ),
}


def _dest(spec: MtModelSpec) -> Path:
    return MT_CACHE_DIR / spec.repo_id.replace("/", "__")


def _already_cached(spec: MtModelSpec) -> bool:
    dest = _dest(spec)
    return dest.is_dir() and any(dest.rglob("*"))


def _download(spec: MtModelSpec, *, force: bool = False) -> Optional[Path]:
    dest = _dest(spec)
    if _already_cached(spec) and not force:
        log.info("already cached: %s", spec.repo_id)
        return dest
    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except ImportError:
        log.error("huggingface_hub missing")
        return None
    dest.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=spec.repo_id,
            local_dir=str(dest),
            local_dir_use_symlinks=False,
        )
    except Exception as exc:
        log.error("download failed for %s: %s", spec.repo_id, exc)
        return None
    return dest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--full", action="store_true", help="Also fetch MADLAD-400-7B")
    parser.add_argument("--opus", action="store_true", help="Also fetch Opus-MT fallback")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    specs = list(DEFAULT_MODELS)
    if args.full or args.all:
        specs.append(OPTIONAL_MODELS["full"])
    if args.opus or args.all:
        specs.append(OPTIONAL_MODELS["opus"])

    log.info("MT cache dir: %s", MT_CACHE_DIR)
    entries = []
    for spec in specs:
        cached = _already_cached(spec)
        entry = {
            "repo_id": spec.repo_id,
            "license": spec.license,
            "params": spec.params,
            "destination": str(_dest(spec)),
            "cached": cached,
        }
        if args.dry_run:
            log.info(
                "[dry-run] %s (%s, %s) %s",
                spec.repo_id, spec.license, spec.params,
                "(cached)" if cached else "(would download)",
            )
            entries.append(entry)
            continue
        result = _download(spec, force=args.force)
        entry["ok"] = result is not None
        entries.append(entry)

    if not args.dry_run:
        manifest_path = MT_CACHE_DIR / "download_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"entries": entries}, indent=2) + "\n", encoding="utf-8"
        )
        log.info("manifest: %s", manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
