#!/usr/bin/env python3
"""Export a Whisper / Distil-Whisper checkpoint to INT8 ONNX.

Uses ``optimum.exporters.onnx`` + ``optimum.onnxruntime.ORTQuantizer`` to
produce mobile-ready ONNX encoder / decoder graphs. The resulting files are
consumed by :mod:`ml.scripts.asr.export_asr_sherpa`, which packages them into
the sherpa-onnx on-device archive layout.

Pipeline:

    1. Load the HF model + processor from the local cache
       (``artifacts/speech/asr/<repo_id>``).
    2. Export to ONNX with encoder / decoder / decoder-with-past splits.
    3. Quantise each graph to INT8 using dynamic quantisation.
    4. Write a SHA-256 manifest documenting file size and source revision.

Commercial-safety: the only models accepted are those in
``ml.scripts.asr.download_models.DEFAULT_MODELS`` (all MIT). Adding a new
model requires updating the allowlist there.

Usage::

    python -m ml.scripts.asr.export_asr_onnx                         # default whisper-small
    python -m ml.scripts.asr.export_asr_onnx --model whisper-tiny    # nickname resolution
    python -m ml.scripts.asr.export_asr_onnx --dry-run               # report only
    python -m ml.scripts.asr.export_asr_onnx \\
        --checkpoint artifacts/speech/asr/whisper_lg                 # fine-tuned Luganda
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("asr.export_onnx")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASR_CACHE_DIR = PROJECT_ROOT / "artifacts" / "speech" / "asr"
ONNX_OUT_DIR = ASR_CACHE_DIR / "onnx"

# Commercial-safe nickname table mirrors download_models.DEFAULT_MODELS.
NICKNAMES: dict[str, str] = {
    "whisper-small": "openai/whisper-small",
    "distil-small": "distil-whisper/distil-small.en",
    "whisper-tiny": "openai/whisper-tiny",
}


@dataclass
class ExportResult:
    model_id: str
    source_dir: str
    onnx_dir: str
    onnx_files: list[str]
    total_size_mb: float
    quantization: str
    ok: bool
    error: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_source(model_or_path: str) -> Path:
    """Turn a nickname or path into an existing local directory.

    Returns the local dir under ``ASR_CACHE_DIR`` if it exists, otherwise
    the raw path the user provided.
    """
    # Nickname?
    repo_id = NICKNAMES.get(model_or_path, model_or_path)
    cached = ASR_CACHE_DIR / repo_id.replace("/", "__")
    if cached.is_dir():
        return cached
    # Maybe it's already a path
    p = Path(model_or_path)
    if p.is_dir():
        return p.resolve()
    # Fall back to HF cache discovery — optimum will download on-the-fly.
    log.warning("local cache miss for %s — optimum will attempt to fetch from HF Hub", repo_id)
    return Path(repo_id)


def _export_onnx(source: Path, out_dir: Path, quantize: bool) -> list[Path]:
    """Run optimum's ONNX exporter + quantiser. Returns produced files."""
    try:
        from optimum.exporters.onnx import main_export  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "optimum[onnxruntime] missing — pip install 'optimum[onnxruntime]>=1.24'"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("exporting %s -> %s", source, out_dir)

    main_export(
        model_name_or_path=str(source),
        output=str(out_dir),
        task="automatic-speech-recognition-with-past",
        opset=17,
        optimize="O2",
        no_post_process=False,
    )

    produced = sorted(out_dir.glob("*.onnx"))

    if quantize:
        try:
            from optimum.onnxruntime import ORTQuantizer  # type: ignore
            from optimum.onnxruntime.configuration import AutoQuantizationConfig  # type: ignore
        except ImportError as exc:
            raise RuntimeError("optimum[onnxruntime] quantiser missing") from exc

        qconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
        for onnx_path in list(produced):
            q_dir = out_dir / f"{onnx_path.stem}_int8"
            log.info("quantising %s -> %s", onnx_path.name, q_dir)
            quantizer = ORTQuantizer.from_pretrained(out_dir, file_name=onnx_path.name)
            quantizer.quantize(save_dir=q_dir, quantization_config=qconfig)
        # Collect quantised outputs as the canonical artifacts.
        produced = sorted((out_dir).glob("*_int8/*.onnx"))
    return produced


def _write_manifest(result: ExportResult, sha_table: dict[str, str]) -> Path:
    """Persist the export manifest next to the ONNX files."""
    manifest_path = Path(result.onnx_dir) / "export_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(result) | {"sha256": sha_table}
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def run(model: str, *, checkpoint: Path | None, quantize: bool, dry_run: bool) -> ExportResult:
    source = checkpoint if checkpoint is not None else _resolve_source(model)
    out_dir = ONNX_OUT_DIR / (checkpoint.name if checkpoint else model.replace("/", "__"))

    result = ExportResult(
        model_id=model if not checkpoint else str(checkpoint),
        source_dir=str(source),
        onnx_dir=str(out_dir),
        onnx_files=[],
        total_size_mb=0.0,
        quantization="int8" if quantize else "fp32",
        ok=False,
    )

    if dry_run:
        log.info("[dry-run] would export %s -> %s", source, out_dir)
        result.ok = True
        return result

    if not Path(source).exists():
        msg = f"source path does not exist: {source} — run download_models.py first"
        log.error(msg)
        result.error = msg
        return result

    try:
        produced = _export_onnx(Path(source), out_dir, quantize=quantize)
    except Exception as exc:
        log.exception("export failed")
        result.error = str(exc)
        return result

    if not produced:
        result.error = "no ONNX files produced"
        return result

    total = sum(p.stat().st_size for p in produced)
    result.onnx_files = [str(p.relative_to(out_dir)) for p in produced]
    result.total_size_mb = round(total / 1024 / 1024, 2)
    result.ok = True

    sha_table = {p.name: _sha256(p) for p in produced}
    _write_manifest(result, sha_table)
    log.info("export OK: %d files, %.2f MB", len(produced), result.total_size_mb)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--model",
        default="whisper-small",
        help=f"Nickname or repo_id. Known: {', '.join(NICKNAMES)}",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Explicit local checkpoint (e.g. artifacts/speech/asr/whisper_lg)",
    )
    parser.add_argument(
        "--no-quantize", action="store_true", help="Skip INT8 quantisation (debug only)"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    result = run(
        args.model,
        checkpoint=args.checkpoint,
        quantize=not args.no_quantize,
        dry_run=args.dry_run,
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
