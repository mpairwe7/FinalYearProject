#!/usr/bin/env python3
"""Export a fine-tuned / distilled MT model to INT8 ONNX for mobile.

Pipeline:

    1. Load the student (from ``distill_mt.py`` output or any HF seq2seq).
    2. ``optimum.exporters.onnx`` → fp32 encoder/decoder graphs.
    3. ``optimum.onnxruntime.ORTQuantizer`` → INT8 dynamic quant.
    4. Write a sherpa-onnx-compatible layout under ``artifacts/mt/onnx/``.
    5. Emit a SHA-256 manifest.

Usage::

    python -m ml.scripts.mt.export_mt_onnx --dry-run
    python -m ml.scripts.mt.export_mt_onnx \\
        --checkpoint artifacts/mt/student/final \\
        --out artifacts/mt/onnx/student_int8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("mt.export_onnx")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts" / "mt"
DEFAULT_OUT = ARTIFACTS_DIR / "onnx" / "student_int8"


@dataclass
class ExportResult:
    checkpoint: str
    output_dir: str
    onnx_files: list[str]
    total_size_mb: float
    ok: bool = False
    error: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(*, checkpoint: Path, output_dir: Path, quantize: bool, dry_run: bool) -> ExportResult:
    result = ExportResult(
        checkpoint=str(checkpoint),
        output_dir=str(output_dir),
        onnx_files=[],
        total_size_mb=0.0,
    )

    if dry_run:
        log.info("[dry-run] would export %s -> %s", checkpoint, output_dir)
        result.ok = True
        return result

    if not checkpoint.exists():
        result.error = f"checkpoint missing: {checkpoint} — run distill_mt.py first"
        log.error(result.error)
        return result

    try:
        from optimum.exporters.onnx import main_export  # type: ignore
    except ImportError:
        result.error = "optimum[onnxruntime] missing"
        return result

    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("exporting %s -> %s", checkpoint, output_dir)
    try:
        main_export(
            model_name_or_path=str(checkpoint),
            output=str(output_dir),
            task="text2text-generation-with-past",
            opset=17,
            optimize="O2",
        )
    except Exception as exc:
        log.exception("export failed")
        result.error = str(exc)
        return result

    produced = sorted(output_dir.glob("*.onnx"))
    if quantize and produced:
        try:
            from optimum.onnxruntime import ORTQuantizer  # type: ignore
            from optimum.onnxruntime.configuration import AutoQuantizationConfig  # type: ignore

            qconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
            for onnx_path in list(produced):
                q_dir = output_dir / f"{onnx_path.stem}_int8"
                quantizer = ORTQuantizer.from_pretrained(output_dir, file_name=onnx_path.name)
                quantizer.quantize(save_dir=q_dir, quantization_config=qconfig)
            produced = sorted(output_dir.glob("*_int8/*.onnx"))
        except Exception as exc:
            log.warning("quantise failed, keeping fp32: %s", exc)

    result.onnx_files = [str(p.relative_to(output_dir)) for p in produced]
    result.total_size_mb = round(sum(p.stat().st_size for p in produced) / 1024 / 1024, 2)

    manifest = {
        "onnx_files": result.onnx_files,
        "total_size_mb": result.total_size_mb,
        "sha256": {p.name: _sha256(p) for p in produced},
    }
    (output_dir / "export_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    result.ok = True
    log.info("export OK: %d files, %.2f MB", len(produced), result.total_size_mb)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=ARTIFACTS_DIR / "student" / "final",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-quantize", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    result = run(
        checkpoint=args.checkpoint,
        output_dir=args.out,
        quantize=not args.no_quantize,
        dry_run=args.dry_run,
    )
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
