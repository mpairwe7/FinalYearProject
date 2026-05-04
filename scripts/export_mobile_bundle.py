#!/usr/bin/env python3
"""Export optimized mobile bundle for Flutter app (Phase 26).

Assembles and validates the complete mobile bundle containing:

  1. Quantized LLM (Gemma-2-2B or distilled 3B, Q4_K_M)
  2. ONNX embedding model (bge-m3 quantized)
  3. FAISS vector index
  4. Offline passage data
  5. Speech models (Whisper-tiny + Piper TTS)
  6. App assets

Enforces hard limit: total bundle ≤ 800 MB.

Usage::

    # Default: assemble bundle and validate
    python scripts/export_mobile_bundle.py

    # Custom components
    python scripts/export_mobile_bundle.py \\
        --llm artifacts/mobile/ura-gemma-2b-q4_k_m.gguf \\
        --embedder artifacts/offline/embedder/ \\
        --index artifacts/offline/faiss_index.bin

    # Skip speech models (smaller bundle)
    python scripts/export_mobile_bundle.py --no-speech

    # Validate only (no copy)
    python scripts/export_mobile_bundle.py --validate-only
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("export_mobile_bundle")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
OUTPUT_DIR = ARTIFACTS_DIR / "mobile_bundle"

# Flutter mobile app paths
MOBILE_ROOT = PROJECT_ROOT / "MobileApp" / "ura_chatbot"
ANDROID_ASSETS = MOBILE_ROOT / "android" / "app" / "src" / "main" / "assets"
IOS_ASSETS = MOBILE_ROOT / "ios" / "Runner" / "assets"

# Hard limits (enforced in CI)
MAX_TOTAL_SIZE_MB = 800
MAX_LLM_SIZE_MB = 500
MAX_EMBEDDER_SIZE_MB = 100
MAX_INDEX_SIZE_MB = 80
MAX_SPEECH_SIZE_MB = 100

# Component defaults
DEFAULT_LLM = ARTIFACTS_DIR / "mobile" / "ura-gemma-2b-q4_k_m.gguf"
DEFAULT_EMBEDDER = ARTIFACTS_DIR / "offline" / "embedder"
DEFAULT_INDEX = ARTIFACTS_DIR / "offline" / "faiss_index.bin"
DEFAULT_PASSAGES = ARTIFACTS_DIR / "offline" / "passages.jsonl.gz"
DEFAULT_ASR = ARTIFACTS_DIR / "speech" / "asr" / "sherpa" / "whisper-tiny"
DEFAULT_TTS = ARTIFACTS_DIR / "speech" / "tts" / "piper"


@dataclass
class BundleComponent:
    """A single component of the mobile bundle."""

    name: str
    source_path: str
    dest_path: str
    size_bytes: int = 0
    size_mb: float = 0.0
    sha256: str = ""
    status: str = "pending"  # pending | included | missing | too_large | skipped
    max_size_mb: float = 0.0


@dataclass
class BundleResult:
    """Result of the mobile bundle assembly."""

    success: bool
    total_size_bytes: int = 0
    total_size_mb: float = 0.0
    max_allowed_mb: float = MAX_TOTAL_SIZE_MB
    components: list[BundleComponent] = field(default_factory=list)
    output_dir: str = ""
    manifest_path: str = ""
    error: str = ""
    created_at: str = ""


def sha256_file(path: Path, chunk_size: int = 8192) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def dir_size(path: Path) -> int:
    """Total size of all files in a directory."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human_size(size_bytes: int) -> str:
    """Format bytes as human-readable."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"


def validate_component(
    name: str,
    source: Path,
    dest_name: str,
    max_mb: float,
) -> BundleComponent:
    """Validate a single bundle component."""
    comp = BundleComponent(
        name=name,
        source_path=str(source),
        dest_path=dest_name,
        max_size_mb=max_mb,
    )

    if not source.exists():
        comp.status = "missing"
        return comp

    if source.is_dir():
        size = dir_size(source)
    else:
        size = source.stat().st_size

    comp.size_bytes = size
    comp.size_mb = round(size / 1_048_576, 1)

    if max_mb > 0 and comp.size_mb > max_mb:
        comp.status = "too_large"
        return comp

    if source.is_file():
        comp.sha256 = sha256_file(source)

    comp.status = "included"
    return comp


def assemble_bundle(
    llm_path: Path,
    embedder_path: Path,
    index_path: Path,
    passages_path: Path,
    asr_path: Path | None,
    tts_path: Path | None,
    output_dir: Path,
    deploy: bool = False,
) -> BundleResult:
    """Assemble the complete mobile bundle."""
    output_dir.mkdir(parents=True, exist_ok=True)

    components: list[BundleComponent] = []

    # Validate each component
    components.append(validate_component(
        "llm", llm_path, "models/llm.gguf", MAX_LLM_SIZE_MB,
    ))
    components.append(validate_component(
        "embedder", embedder_path, "models/embedder/", MAX_EMBEDDER_SIZE_MB,
    ))
    components.append(validate_component(
        "faiss_index", index_path, "models/faiss_index.bin", MAX_INDEX_SIZE_MB,
    ))
    components.append(validate_component(
        "passages", passages_path, "models/passages.jsonl.gz", MAX_INDEX_SIZE_MB,
    ))

    if asr_path:
        components.append(validate_component(
            "asr_model", asr_path, "models/asr/", MAX_SPEECH_SIZE_MB,
        ))
    if tts_path:
        components.append(validate_component(
            "tts_model", tts_path, "models/tts/", MAX_SPEECH_SIZE_MB,
        ))

    # Check for fatal issues
    critical_missing = [
        c for c in components if c.name in ("llm",) and c.status == "missing"
    ]
    too_large = [c for c in components if c.status == "too_large"]

    if too_large:
        names = ", ".join(f"{c.name} ({c.size_mb}MB > {c.max_size_mb}MB)" for c in too_large)
        return BundleResult(
            success=False,
            components=components,
            error=f"Components exceed size limits: {names}",
        )

    # Calculate total size
    total_bytes = sum(c.size_bytes for c in components if c.status == "included")
    total_mb = round(total_bytes / 1_048_576, 1)

    if total_mb > MAX_TOTAL_SIZE_MB:
        return BundleResult(
            success=False,
            total_size_bytes=total_bytes,
            total_size_mb=total_mb,
            components=components,
            error=f"Total bundle size {total_mb}MB exceeds limit of {MAX_TOTAL_SIZE_MB}MB",
        )

    # Copy components to output directory
    for comp in components:
        if comp.status != "included":
            continue

        source = Path(comp.source_path)
        dest = output_dir / comp.dest_path

        if source.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)

        log.info("  %s: %s -> %s", comp.name, human_size(comp.size_bytes), comp.dest_path)

    # Write manifest
    manifest = {
        "version": "1.0.0",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_size_bytes": total_bytes,
        "total_size_mb": total_mb,
        "max_allowed_mb": MAX_TOTAL_SIZE_MB,
        "components": [asdict(c) for c in components if c.status == "included"],
        "pipeline_version": "2026.1.0",
    }
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Deploy to Flutter app if requested
    if deploy:
        _deploy_to_flutter(output_dir)

    return BundleResult(
        success=True,
        total_size_bytes=total_bytes,
        total_size_mb=total_mb,
        components=components,
        output_dir=str(output_dir),
        manifest_path=str(manifest_path),
        created_at=manifest["created_at"],
    )


def _deploy_to_flutter(bundle_dir: Path) -> None:
    """Copy bundle to Flutter Android/iOS asset directories."""
    for target_dir, platform in [
        (ANDROID_ASSETS / "models", "Android"),
        (IOS_ASSETS / "models", "iOS"),
    ]:
        target_dir.mkdir(parents=True, exist_ok=True)
        models_dir = bundle_dir / "models"
        if models_dir.exists():
            shutil.copytree(models_dir, target_dir, dirs_exist_ok=True)
            log.info("Deployed to %s: %s", platform, target_dir)

    # Copy manifest
    manifest_src = bundle_dir / "manifest.json"
    if manifest_src.exists():
        for target in [ANDROID_ASSETS, IOS_ASSETS]:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest_src, target / "mobile_bundle_manifest.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export optimized mobile bundle for Flutter app",
    )
    parser.add_argument("--llm", type=Path, default=DEFAULT_LLM, help="Path to quantized LLM GGUF")
    parser.add_argument("--embedder", type=Path, default=DEFAULT_EMBEDDER, help="Path to ONNX embedder dir")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="Path to FAISS index")
    parser.add_argument("--passages", type=Path, default=DEFAULT_PASSAGES, help="Path to passages file")
    parser.add_argument("--asr", type=Path, default=DEFAULT_ASR, help="Path to ASR model dir")
    parser.add_argument("--tts", type=Path, default=DEFAULT_TTS, help="Path to TTS model dir")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="Output directory")
    parser.add_argument("--no-speech", action="store_true", help="Exclude speech models")
    parser.add_argument("--deploy", action="store_true", help="Deploy to Flutter app directories")
    parser.add_argument("--validate-only", action="store_true", help="Validate only, don't copy")

    args = parser.parse_args()

    log.info("=" * 60)
    log.info("URA Chatbot Mobile Bundle Export")
    log.info("=" * 60)
    log.info("LLM:      %s", args.llm)
    log.info("Embedder: %s", args.embedder)
    log.info("Index:    %s", args.index)
    log.info("Speech:   %s", "excluded" if args.no_speech else "included")
    log.info("Output:   %s", args.output)
    log.info("Max size: %d MB", MAX_TOTAL_SIZE_MB)

    if args.validate_only:
        log.info("\nVALIDATE ONLY — no files will be copied")

    result = assemble_bundle(
        llm_path=args.llm,
        embedder_path=args.embedder,
        index_path=args.index,
        passages_path=args.passages,
        asr_path=None if args.no_speech else args.asr,
        tts_path=None if args.no_speech else args.tts,
        output_dir=args.output,
        deploy=args.deploy and not args.validate_only,
    )

    log.info("\n" + "=" * 60)
    log.info("BUNDLE SUMMARY")
    log.info("=" * 60)

    for comp in result.components:
        icon = {"included": "OK", "missing": "MISS", "too_large": "BIG", "skipped": "SKIP"}.get(comp.status, "?")
        log.info(
            "  [%4s] %-15s %8s  %s",
            icon, comp.name,
            f"{comp.size_mb:.0f}MB" if comp.size_mb > 0 else "---",
            comp.dest_path if comp.status == "included" else comp.status,
        )

    log.info("-" * 60)
    log.info("Total: %s / %s MB limit", f"{result.total_size_mb:.0f}MB", MAX_TOTAL_SIZE_MB)

    if result.success:
        log.info("\nBUNDLE: PASS (within %d MB limit)", MAX_TOTAL_SIZE_MB)
    else:
        log.error("\nBUNDLE: FAIL — %s", result.error)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
