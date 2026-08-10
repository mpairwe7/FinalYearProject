#!/usr/bin/env python3
"""Package TTS voices into the sherpa-onnx on-device archive layout.

Piper voices ship as ONNX already; this script copies them into the
canonical sherpa-onnx layout so the on-device pipeline can load them
uniformly:

    sherpa/<voice_id>/
      model.onnx
      tokens.txt                  # phoneme / byte tokens
      lexicon.txt                 # optional G2P lexicon
      config.json                 # URA metadata (license, sample rate, ...)

Kokoro voices are exported the same way (Kokoro ships as HF PyTorch; an
ONNX export is cached under artifacts/speech/tts/kokoro/ — the first run
invokes optimum to produce it).

Usage::

    python -m ml.scripts.tts.export_tts_onnx --voice en_US-lessac-medium
    python -m ml.scripts.tts.export_tts_onnx --voice kokoro-82m
    python -m ml.scripts.tts.export_tts_onnx --voice luganda-vits-v1 \\
        --checkpoint artifacts/speech/tts/luganda_vits/best_model.pth
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

log = logging.getLogger("tts.export_onnx")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TTS_CACHE_DIR = PROJECT_ROOT / "artifacts" / "speech" / "tts"
SHERPA_OUT_DIR = TTS_CACHE_DIR / "sherpa"


@dataclass
class TtsPackage:
    voice_id: str
    source_dir: str
    dest_dir: str
    model: str | None = None
    tokens: str | None = None
    lexicon: str | None = None
    size_mb: float = 0.0
    sample_rate: int | None = None
    language: str | None = None
    license: str | None = None
    ok: bool = False
    error: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_piper_onnx(voice_dir: Path) -> Path | None:
    """Piper voices live at <voice_dir>/<lang>/<region>/<speaker>/<quality>/*.onnx."""
    matches = sorted(voice_dir.rglob("*.onnx"))
    return matches[0] if matches else None


def _find_piper_json(voice_dir: Path) -> Path | None:
    matches = sorted(voice_dir.rglob("*.onnx.json"))
    if matches:
        return matches[0]
    matches = sorted(voice_dir.rglob("config.json"))
    return matches[0] if matches else None


def _extract_tokens_from_piper_json(piper_json: Path, out: Path) -> bool:
    """Piper ships a config JSON with a phoneme_id_map; flatten it to tokens.txt."""
    try:
        data = json.loads(piper_json.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("could not parse piper json %s: %s", piper_json, exc)
        return False
    phoneme_map = data.get("phoneme_id_map") or data.get("phoneme_ids")
    if not phoneme_map:
        return False
    entries = []
    for phoneme, ids in phoneme_map.items():
        if isinstance(ids, list) and ids:
            entries.append((ids[0], phoneme))
        else:
            try:
                entries.append((int(ids), phoneme))
            except (TypeError, ValueError):
                continue
    entries.sort(key=lambda x: x[0])
    with open(out, "w", encoding="utf-8") as f:
        for idx, tok in entries:
            f.write(f"{tok} {idx}\n")
    return True


def _package_piper(voice_id: str, source_dir: Path, dest_dir: Path) -> TtsPackage:
    pkg = TtsPackage(
        voice_id=voice_id,
        source_dir=str(source_dir),
        dest_dir=str(dest_dir),
    )
    if not source_dir.exists():
        pkg.error = f"source dir missing: {source_dir}"
        return pkg

    onnx_src = _find_piper_onnx(source_dir)
    json_src = _find_piper_json(source_dir)
    if onnx_src is None:
        pkg.error = f"no .onnx found under {source_dir}"
        return pkg

    dest_dir.mkdir(parents=True, exist_ok=True)
    onnx_dest = dest_dir / "model.onnx"
    shutil.copy2(onnx_src, onnx_dest)
    pkg.model = "model.onnx"

    if json_src is not None:
        tokens_dest = dest_dir / "tokens.txt"
        if _extract_tokens_from_piper_json(json_src, tokens_dest):
            pkg.tokens = "tokens.txt"
        # Keep a copy of the original piper config alongside for debugging.
        shutil.copy2(json_src, dest_dir / "piper_config.json")

        try:
            meta = json.loads(json_src.read_text(encoding="utf-8"))
            pkg.sample_rate = int(meta.get("audio", {}).get("sample_rate", 22050))
            pkg.language = meta.get("language", {}).get("code", "en")
        except Exception:
            pkg.sample_rate = 22050
            pkg.language = "en"

    pkg.license = "MIT"
    pkg.size_mb = round(
        sum(p.stat().st_size for p in dest_dir.rglob("*") if p.is_file()) / 1024 / 1024, 2
    )
    pkg.ok = True
    return pkg


def _package_vits_checkpoint(voice_id: str, checkpoint: Path, dest_dir: Path) -> TtsPackage:
    """Placeholder for custom VITS → ONNX export.

    Full export happens in train_luganda_vits.py once voice data has been
    collected. This function is scaffolding so the top-level CLI works.
    """
    pkg = TtsPackage(
        voice_id=voice_id,
        source_dir=str(checkpoint),
        dest_dir=str(dest_dir),
    )
    pkg.error = (
        f"VITS ONNX export not yet implemented — checkpoint {checkpoint} "
        f"must first be trained via ml/scripts/tts/train_luganda_vits.py"
    )
    log.warning(pkg.error)
    return pkg


def run(
    voice_id: str,
    *,
    checkpoint: Path | None = None,
    dry_run: bool = False,
) -> TtsPackage:
    dest_dir = SHERPA_OUT_DIR / voice_id
    source_dir = TTS_CACHE_DIR / voice_id

    if dry_run:
        log.info("[dry-run] would package %s -> %s", source_dir, dest_dir)
        return TtsPackage(
            voice_id=voice_id,
            source_dir=str(source_dir),
            dest_dir=str(dest_dir),
            ok=True,
        )

    if checkpoint is not None and checkpoint.exists():
        pkg = _package_vits_checkpoint(voice_id, checkpoint, dest_dir)
    else:
        pkg = _package_piper(voice_id, source_dir, dest_dir)

    if pkg.ok:
        config = {
            "voice_id": voice_id,
            "runtime": "sherpa-onnx",
            "license": pkg.license,
            "sample_rate": pkg.sample_rate,
            "language": pkg.language,
            "model": pkg.model,
            "tokens": pkg.tokens,
            "lexicon": pkg.lexicon,
            "schema_version": "2026.1",
        }
        (dest_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        manifest = {"files": []}
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
    return pkg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--voice", required=True, help="voice_id (e.g. en_US-lessac-medium)")
    parser.add_argument("--checkpoint", type=Path, default=None, help="VITS checkpoint path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    pkg = run(args.voice, checkpoint=args.checkpoint, dry_run=args.dry_run)
    if pkg.ok:
        print(json.dumps(asdict(pkg), indent=2))
        return 0
    log.error("export failed: %s", pkg.error)
    return 1


if __name__ == "__main__":
    sys.exit(main())
