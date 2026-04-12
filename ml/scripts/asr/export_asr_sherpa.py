#!/usr/bin/env python3
"""Package ONNX Whisper files into the sherpa-onnx on-device archive layout.

sherpa-onnx expects a directory like::

    whisper-small.int8/
      encoder.int8.onnx
      decoder.int8.onnx
      tokens.txt
      config.json                 # custom metadata — not required by sherpa, useful for us

This script consumes the output of :mod:`ml.scripts.asr.export_asr_onnx` and
emits the above structure under ``artifacts/speech/asr/sherpa/<name>/`` ready
for :mod:`ml.scripts.speech.export_mobile_speech` to copy into the mobile
asset bundle.

Usage::

    python -m ml.scripts.asr.export_asr_sherpa --name whisper-small
    python -m ml.scripts.asr.export_asr_sherpa --name whisper_lg --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("asr.export_sherpa")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASR_CACHE_DIR = PROJECT_ROOT / "artifacts" / "speech" / "asr"
ONNX_OUT_DIR = ASR_CACHE_DIR / "onnx"
SHERPA_OUT_DIR = ASR_CACHE_DIR / "sherpa"


@dataclass
class SherpaPackage:
    name: str
    source_onnx_dir: str
    dest_dir: str
    encoder: Optional[str] = None
    decoder: Optional[str] = None
    tokens: Optional[str] = None
    size_mb: float = 0.0
    ok: bool = False
    error: Optional[str] = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_onnx(dir_: Path, keyword: str) -> Optional[Path]:
    """Locate an ONNX file whose filename contains ``keyword``."""
    if not dir_.exists():
        return None
    for p in sorted(dir_.rglob("*.onnx")):
        if keyword in p.name.lower():
            return p
    return None


def _copy_tokens(source_model_dir: Path, dest: Path) -> bool:
    """Copy tokens.txt / tokenizer.json into the sherpa layout.

    sherpa-onnx wants a flat ``tokens.txt`` mapping id -> token. For Whisper
    we rely on the HF tokenizer files shipped with the snapshot and convert
    them on-the-fly if tokens.txt is missing.
    """
    for cand in ("tokens.txt",):
        src = source_model_dir / cand
        if src.exists():
            shutil.copy2(src, dest / "tokens.txt")
            return True

    vocab = source_model_dir / "vocab.json"
    if vocab.exists():
        try:
            data = json.loads(vocab.read_text(encoding="utf-8"))
        except Exception:
            return False
        inverted = sorted(((idx, tok) for tok, idx in data.items()), key=lambda x: x[0])
        with open(dest / "tokens.txt", "w", encoding="utf-8") as f:
            for idx, tok in inverted:
                f.write(f"{tok} {idx}\n")
        return True
    return False


def run(name: str, *, dry_run: bool) -> SherpaPackage:
    source_dir = ONNX_OUT_DIR / name
    dest_dir = SHERPA_OUT_DIR / name

    pkg = SherpaPackage(
        name=name,
        source_onnx_dir=str(source_dir),
        dest_dir=str(dest_dir),
    )

    if dry_run:
        log.info("[dry-run] would package %s -> %s", source_dir, dest_dir)
        pkg.ok = True
        return pkg

    if not source_dir.exists():
        pkg.error = (
            f"source dir missing: {source_dir}. "
            f"Run `python -m ml.scripts.asr.export_asr_onnx --model {name}` first."
        )
        log.error(pkg.error)
        return pkg

    dest_dir.mkdir(parents=True, exist_ok=True)

    encoder_src = _find_onnx(source_dir, "encoder")
    decoder_src = _find_onnx(source_dir, "decoder_model_merged") or _find_onnx(
        source_dir, "decoder"
    )
    if encoder_src is None or decoder_src is None:
        pkg.error = (
            f"could not find encoder/decoder ONNX under {source_dir}; "
            f"has the export run completed?"
        )
        log.error(pkg.error)
        return pkg

    encoder_dest = dest_dir / "encoder.int8.onnx"
    decoder_dest = dest_dir / "decoder.int8.onnx"
    shutil.copy2(encoder_src, encoder_dest)
    shutil.copy2(decoder_src, decoder_dest)
    pkg.encoder = str(encoder_dest.relative_to(dest_dir))
    pkg.decoder = str(decoder_dest.relative_to(dest_dir))

    # Best-effort tokens.txt — sherpa-onnx needs this for the Whisper recipe.
    # Probe the original HF snapshot for tokeniser files.
    hf_cache = ASR_CACHE_DIR / name
    if not hf_cache.exists() and "__" in name:
        hf_cache = ASR_CACHE_DIR / name
    tokens_ok = _copy_tokens(hf_cache, dest_dir)
    pkg.tokens = "tokens.txt" if tokens_ok else None

    config = {
        "name": name,
        "runtime": "sherpa-onnx",
        "license": "MIT",
        "encoder": pkg.encoder,
        "decoder": pkg.decoder,
        "tokens": pkg.tokens,
        "schema_version": "2026.1",
    }
    (dest_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "files": [],
    }
    for p in sorted(dest_dir.rglob("*")):
        if p.is_file():
            manifest["files"].append(
                {
                    "name": str(p.relative_to(dest_dir)),
                    "size_bytes": p.stat().st_size,
                    "sha256": _sha256(p),
                }
            )
    (dest_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    pkg.size_mb = round(
        sum(p.stat().st_size for p in dest_dir.rglob("*") if p.is_file()) / 1024 / 1024,
        2,
    )
    pkg.ok = True
    log.info("sherpa package OK: %s (%.2f MB)", dest_dir, pkg.size_mb)
    return pkg


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--name", required=True, help="ONNX export name (from export_asr_onnx)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    pkg = run(args.name, dry_run=args.dry_run)
    if pkg.ok and not args.dry_run:
        print(json.dumps(asdict(pkg), indent=2))
    return 0 if pkg.ok else 1


if __name__ == "__main__":
    sys.exit(main())
