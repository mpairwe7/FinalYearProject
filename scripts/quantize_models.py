#!/usr/bin/env python3
"""Automated model quantization pipeline for URA Chatbot.

Exports Qwen3-8B (or any HuggingFace model) into multiple quantized formats:

  - **GGUF**: Q4_K_M, Q5_K_M, Q8_0 via llama.cpp
  - **AWQ**: 4-bit via AutoAWQ
  - **GPTQ**: 4-bit 128-group via auto-gptq
  - **ONNX**: fp16 + int8 quantized via optimum

Each format produces an artifact under ``artifacts/quantized/<model>/<format>/``
with a ``manifest.json`` containing SHA-256 checksums and metadata.

Usage::

    # All formats (default: Qwen/Qwen3-8B)
    python scripts/quantize_models.py

    # Specific format + model
    python scripts/quantize_models.py --model Qwen/Qwen3-8B --formats gguf awq

    # GGUF only with specific quant types
    python scripts/quantize_models.py --formats gguf --gguf-types Q4_K_M Q5_K_M

    # Dry run (validate only)
    python scripts/quantize_models.py --dry-run

Environment:
    HF_TOKEN              — HuggingFace token for gated models
    QUANTIZE_OUTPUT_DIR   — Override output directory (default: artifacts/quantized)
    LLAMA_CPP_DIR         — Path to llama.cpp build directory
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("quantize_models")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "quantized"
CALIBRATION_DATA = PROJECT_ROOT / "Data" / "dataset"

# GGUF quant types: name -> (description, approx bits/weight for 8B model)
GGUF_QUANT_TYPES: dict[str, tuple[str, float]] = {
    "Q4_K_M": ("4-bit K-quant mixed precision (recommended)", 4.83),
    "Q5_K_M": ("5-bit K-quant mixed precision (high quality)", 5.69),
    "Q8_0": ("8-bit round-to-nearest (near-lossless)", 8.50),
    "Q4_K_S": ("4-bit K-quant small (smaller, slightly lower quality)", 4.58),
    "Q6_K": ("6-bit K-quant (quality-focused)", 6.56),
    "IQ4_NL": ("4-bit importance-matrix quant (2024+ best practice)", 4.50),
}


@dataclass
class QuantResult:
    """Result of a single quantization run."""

    model: str
    format: str
    quant_type: str
    output_path: str
    size_bytes: int
    size_mb: float
    sha256: str
    duration_s: float
    status: str  # success | failed | skipped
    error: str = ""
    created_at: str = ""
    metadata: dict = field(default_factory=dict)


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


def human_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"


def find_llama_cpp() -> Path | None:
    """Locate llama.cpp build directory."""
    # Check env var first
    env_dir = os.getenv("LLAMA_CPP_DIR", "")
    if env_dir:
        p = Path(env_dir)
        if (p / "llama-quantize").exists() or (p / "build" / "bin" / "llama-quantize").exists():
            return p

    # Check common locations
    for candidate in [
        Path.home() / "llama.cpp",
        Path.home() / "llama.cpp" / "build" / "bin",
        Path("/usr/local/bin"),
        Path("/opt/llama.cpp"),
    ]:
        if (candidate / "llama-quantize").exists():
            return candidate

    # Check PATH
    result = shutil.which("llama-quantize")
    if result:
        return Path(result).parent

    return None


# ---------------------------------------------------------------------------
# GGUF Quantization (via llama.cpp)
# ---------------------------------------------------------------------------
def quantize_gguf(
    model_id: str,
    output_dir: Path,
    quant_types: list[str],
    hf_token: str | None = None,
    use_imatrix: bool = False,
) -> list[QuantResult]:
    """Export HuggingFace model to GGUF format(s) via llama.cpp."""
    results: list[QuantResult] = []

    llama_dir = find_llama_cpp()
    if llama_dir is None:
        log.warning("llama.cpp not found — skipping GGUF quantization")
        for qt in quant_types:
            results.append(QuantResult(
                model=model_id, format="gguf", quant_type=qt,
                output_path="", size_bytes=0, size_mb=0, sha256="",
                duration_s=0, status="skipped",
                error="llama.cpp not found. Set LLAMA_CPP_DIR or install llama.cpp.",
            ))
        return results

    gguf_dir = output_dir / "gguf"
    gguf_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Convert HF model to GGUF F16
    f16_path = gguf_dir / "model-f16.gguf"
    if not f16_path.exists():
        log.info("Converting %s to GGUF F16...", model_id)
        t0 = time.time()

        convert_script = llama_dir / "convert_hf_to_gguf.py"
        if not convert_script.exists():
            # Try parent directory
            convert_script = llama_dir.parent / "convert_hf_to_gguf.py"

        if not convert_script.exists():
            log.error("convert_hf_to_gguf.py not found in llama.cpp directory")
            for qt in quant_types:
                results.append(QuantResult(
                    model=model_id, format="gguf", quant_type=qt,
                    output_path="", size_bytes=0, size_mb=0, sha256="",
                    duration_s=0, status="failed",
                    error="convert_hf_to_gguf.py not found",
                ))
            return results

        cmd = [
            sys.executable, str(convert_script),
            model_id,
            "--outfile", str(f16_path),
            "--outtype", "f16",
        ]
        if hf_token:
            cmd.extend(["--token", hf_token])

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3600)
            log.info("F16 conversion done in %.1fs, size=%s", time.time() - t0, human_size(f16_path.stat().st_size))
        except subprocess.CalledProcessError as e:
            log.error("F16 conversion failed: %s", e.stderr[:500])
            for qt in quant_types:
                results.append(QuantResult(
                    model=model_id, format="gguf", quant_type=qt,
                    output_path="", size_bytes=0, size_mb=0, sha256="",
                    duration_s=time.time() - t0, status="failed",
                    error=f"F16 conversion failed: {e.stderr[:200]}",
                ))
            return results

    # Step 2: Optional importance matrix calibration
    imatrix_path = gguf_dir / "imatrix.dat"
    if use_imatrix and not imatrix_path.exists():
        log.info("Computing importance matrix for better low-bit quantization...")
        calib_file = _prepare_calibration_data(gguf_dir)
        if calib_file:
            imatrix_cmd = [
                str(llama_dir / "llama-imatrix"),
                "-m", str(f16_path),
                "-f", str(calib_file),
                "-o", str(imatrix_path),
                "--chunks", "128",
            ]
            try:
                subprocess.run(imatrix_cmd, check=True, capture_output=True, timeout=7200)
                log.info("Importance matrix computed: %s", human_size(imatrix_path.stat().st_size))
            except (subprocess.CalledProcessError, FileNotFoundError):
                log.warning("Importance matrix computation failed — proceeding without imatrix")
                imatrix_path = None  # type: ignore[assignment]

    # Step 3: Quantize to each target type
    quantize_bin = llama_dir / "llama-quantize"
    if not quantize_bin.exists():
        quantize_bin = llama_dir / "build" / "bin" / "llama-quantize"

    for qt in quant_types:
        if qt not in GGUF_QUANT_TYPES:
            log.warning("Unknown GGUF quant type: %s — skipping", qt)
            results.append(QuantResult(
                model=model_id, format="gguf", quant_type=qt,
                output_path="", size_bytes=0, size_mb=0, sha256="",
                duration_s=0, status="skipped",
                error=f"Unknown quant type: {qt}",
            ))
            continue

        model_name = model_id.split("/")[-1]
        out_name = f"{model_name}-{qt.lower()}.gguf"
        out_path = gguf_dir / out_name

        if out_path.exists():
            log.info("GGUF %s already exists — computing checksum", qt)
            sz = out_path.stat().st_size
            results.append(QuantResult(
                model=model_id, format="gguf", quant_type=qt,
                output_path=str(out_path), size_bytes=sz,
                size_mb=round(sz / 1_048_576, 1),
                sha256=sha256_file(out_path), duration_s=0,
                status="success",
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ))
            continue

        log.info("Quantizing %s to %s...", model_id, qt)
        t0 = time.time()

        cmd = [str(quantize_bin), str(f16_path), str(out_path), qt]
        if use_imatrix and imatrix_path and imatrix_path.exists():
            cmd.extend(["--imatrix", str(imatrix_path)])

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3600)
            duration = time.time() - t0
            sz = out_path.stat().st_size
            checksum = sha256_file(out_path)

            log.info(
                "GGUF %s done: %s in %.1fs (sha256=%s...)",
                qt, human_size(sz), duration, checksum[:16],
            )
            results.append(QuantResult(
                model=model_id, format="gguf", quant_type=qt,
                output_path=str(out_path), size_bytes=sz,
                size_mb=round(sz / 1_048_576, 1),
                sha256=checksum, duration_s=round(duration, 1),
                status="success",
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ))
        except subprocess.CalledProcessError as e:
            duration = time.time() - t0
            log.error("GGUF %s failed: %s", qt, e.stderr[:300])
            results.append(QuantResult(
                model=model_id, format="gguf", quant_type=qt,
                output_path="", size_bytes=0, size_mb=0, sha256="",
                duration_s=round(duration, 1), status="failed",
                error=e.stderr[:300],
            ))

    return results


# ---------------------------------------------------------------------------
# AWQ Quantization
# ---------------------------------------------------------------------------
def quantize_awq(
    model_id: str,
    output_dir: Path,
    hf_token: str | None = None,
    group_size: int = 128,
    bits: int = 4,
) -> QuantResult:
    """Quantize model to AWQ 4-bit format."""
    awq_dir = output_dir / "awq"
    awq_dir.mkdir(parents=True, exist_ok=True)

    model_name = model_id.split("/")[-1]
    out_path = awq_dir / f"{model_name}-awq-w{bits}-g{group_size}"

    t0 = time.time()
    try:
        from awq import AutoAWQForCausalLM
        from transformers import AutoTokenizer

        log.info("Loading %s for AWQ quantization (w%d, g%d)...", model_id, bits, group_size)

        tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=False, token=hf_token,
        )
        model = AutoAWQForCausalLM.from_pretrained(
            model_id, trust_remote_code=False, token=hf_token,
        )

        # Prepare calibration data
        calib_data = _load_calibration_texts(max_samples=128)

        quant_config = {
            "zero_point": True,
            "q_group_size": group_size,
            "w_bit": bits,
            "version": "GEMM",
        }

        log.info("Running AWQ quantization...")
        model.quantize(tokenizer, quant_config=quant_config, calib_data=calib_data)

        model.save_quantized(str(out_path))
        tokenizer.save_pretrained(str(out_path))

        duration = time.time() - t0

        # Compute total size
        total_size = sum(f.stat().st_size for f in out_path.rglob("*") if f.is_file())
        # Hash the safetensors file
        st_files = list(out_path.glob("*.safetensors"))
        checksum = sha256_file(st_files[0]) if st_files else ""

        log.info("AWQ done: %s in %.1fs", human_size(total_size), duration)
        return QuantResult(
            model=model_id, format="awq",
            quant_type=f"w{bits}-g{group_size}",
            output_path=str(out_path), size_bytes=total_size,
            size_mb=round(total_size / 1_048_576, 1),
            sha256=checksum, duration_s=round(duration, 1),
            status="success",
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    except ImportError:
        log.warning("autoawq not installed — skipping AWQ quantization")
        return QuantResult(
            model=model_id, format="awq", quant_type=f"w{bits}-g{group_size}",
            output_path="", size_bytes=0, size_mb=0, sha256="",
            duration_s=0, status="skipped",
            error="autoawq not installed. pip install autoawq",
        )
    except Exception as e:
        duration = time.time() - t0
        log.error("AWQ quantization failed: %s", e)
        return QuantResult(
            model=model_id, format="awq", quant_type=f"w{bits}-g{group_size}",
            output_path="", size_bytes=0, size_mb=0, sha256="",
            duration_s=round(duration, 1), status="failed",
            error=str(e)[:300],
        )


# ---------------------------------------------------------------------------
# GPTQ Quantization
# ---------------------------------------------------------------------------
def quantize_gptq(
    model_id: str,
    output_dir: Path,
    hf_token: str | None = None,
    bits: int = 4,
    group_size: int = 128,
) -> QuantResult:
    """Quantize model to GPTQ 4-bit format."""
    gptq_dir = output_dir / "gptq"
    gptq_dir.mkdir(parents=True, exist_ok=True)

    model_name = model_id.split("/")[-1]
    out_path = gptq_dir / f"{model_name}-gptq-{bits}bit-g{group_size}"

    t0 = time.time()
    try:
        from auto_gptq import AutoGPTQForCausalLM, BaseQuantizeConfig
        from transformers import AutoTokenizer

        log.info("Loading %s for GPTQ quantization (%dbit, g%d)...", model_id, bits, group_size)

        tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=False, token=hf_token,
        )

        quantize_config = BaseQuantizeConfig(
            bits=bits,
            group_size=group_size,
            damp_percent=0.1,
            desc_act=False,
            static_groups=False,
            sym=True,
        )

        model = AutoGPTQForCausalLM.from_pretrained(
            model_id, quantize_config=quantize_config,
            trust_remote_code=False, token=hf_token,
        )

        # Calibration
        calib_data = _load_calibration_dataset(tokenizer, max_samples=128)

        log.info("Running GPTQ quantization...")
        model.quantize(calib_data)

        model.save_quantized(str(out_path))
        tokenizer.save_pretrained(str(out_path))

        duration = time.time() - t0
        total_size = sum(f.stat().st_size for f in out_path.rglob("*") if f.is_file())
        st_files = list(out_path.glob("*.safetensors"))
        checksum = sha256_file(st_files[0]) if st_files else ""

        log.info("GPTQ done: %s in %.1fs", human_size(total_size), duration)
        return QuantResult(
            model=model_id, format="gptq",
            quant_type=f"{bits}bit-g{group_size}",
            output_path=str(out_path), size_bytes=total_size,
            size_mb=round(total_size / 1_048_576, 1),
            sha256=checksum, duration_s=round(duration, 1),
            status="success",
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    except ImportError:
        log.warning("auto-gptq not installed — skipping GPTQ quantization")
        return QuantResult(
            model=model_id, format="gptq",
            quant_type=f"{bits}bit-g{group_size}",
            output_path="", size_bytes=0, size_mb=0, sha256="",
            duration_s=0, status="skipped",
            error="auto-gptq not installed. pip install auto-gptq",
        )
    except Exception as e:
        duration = time.time() - t0
        log.error("GPTQ quantization failed: %s", e)
        return QuantResult(
            model=model_id, format="gptq",
            quant_type=f"{bits}bit-g{group_size}",
            output_path="", size_bytes=0, size_mb=0, sha256="",
            duration_s=round(duration, 1), status="failed",
            error=str(e)[:300],
        )


# ---------------------------------------------------------------------------
# ONNX Export
# ---------------------------------------------------------------------------
def export_onnx(
    model_id: str,
    output_dir: Path,
    hf_token: str | None = None,
    quantize_int8: bool = True,
) -> QuantResult:
    """Export model to ONNX format (fp16 + optional int8 quantization)."""
    onnx_dir = output_dir / "onnx"
    onnx_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        from optimum.onnxruntime import ORTModelForCausalLM, ORTQuantizer
        from optimum.onnxruntime.configuration import AutoQuantizationConfig

        log.info("Exporting %s to ONNX...", model_id)

        model = ORTModelForCausalLM.from_pretrained(
            model_id, export=True, token=hf_token,
        )
        model.save_pretrained(str(onnx_dir / "fp16"))

        quant_type = "fp16"

        if quantize_int8:
            log.info("Applying ONNX int8 dynamic quantization...")
            quantizer = ORTQuantizer.from_pretrained(str(onnx_dir / "fp16"))
            dqconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
            quantizer.quantize(
                save_dir=str(onnx_dir / "int8"),
                quantization_config=dqconfig,
            )
            quant_type = "int8-dynamic"

        duration = time.time() - t0
        final_dir = onnx_dir / ("int8" if quantize_int8 else "fp16")
        total_size = sum(f.stat().st_size for f in final_dir.rglob("*") if f.is_file())
        onnx_files = list(final_dir.glob("*.onnx"))
        checksum = sha256_file(onnx_files[0]) if onnx_files else ""

        log.info("ONNX export done: %s in %.1fs", human_size(total_size), duration)
        return QuantResult(
            model=model_id, format="onnx", quant_type=quant_type,
            output_path=str(final_dir), size_bytes=total_size,
            size_mb=round(total_size / 1_048_576, 1),
            sha256=checksum, duration_s=round(duration, 1),
            status="success",
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    except ImportError:
        log.warning("optimum[onnxruntime] not installed — skipping ONNX export")
        return QuantResult(
            model=model_id, format="onnx", quant_type="fp16",
            output_path="", size_bytes=0, size_mb=0, sha256="",
            duration_s=0, status="skipped",
            error="optimum[onnxruntime] not installed",
        )
    except Exception as e:
        duration = time.time() - t0
        log.error("ONNX export failed: %s", e)
        return QuantResult(
            model=model_id, format="onnx", quant_type="fp16",
            output_path="", size_bytes=0, size_mb=0, sha256="",
            duration_s=round(duration, 1), status="failed",
            error=str(e)[:300],
        )


# ---------------------------------------------------------------------------
# Calibration data helpers
# ---------------------------------------------------------------------------
def _prepare_calibration_data(output_dir: Path) -> Path | None:
    """Prepare a plain-text calibration file from training data."""
    calib_path = output_dir / "calibration.txt"
    if calib_path.exists():
        return calib_path

    texts = _load_calibration_texts(max_samples=256)
    if not texts:
        return None

    with open(calib_path, "w", encoding="utf-8") as f:
        f.write("\n".join(texts))

    log.info("Calibration data: %d samples, %s", len(texts), human_size(calib_path.stat().st_size))
    return calib_path


def _load_calibration_texts(max_samples: int = 128) -> list[str]:
    """Load calibration texts from the training dataset."""
    texts: list[str] = []

    # Try JSONL files in Data/dataset
    for jsonl_path in sorted(CALIBRATION_DATA.glob("*.jsonl")):
        try:
            with open(jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    # Support multiple formats
                    text = data.get("text") or data.get("question") or data.get("input", "")
                    if text and len(text) > 20:
                        texts.append(text)
                    if len(texts) >= max_samples:
                        break
        except Exception:
            continue
        if len(texts) >= max_samples:
            break

    # Fallback: try CSV
    if len(texts) < max_samples:
        for csv_path in sorted(CALIBRATION_DATA.glob("*.csv")):
            try:
                import csv

                with open(csv_path, encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        text = row.get("text") or row.get("question") or row.get("input", "")
                        if text and len(text) > 20:
                            texts.append(text)
                        if len(texts) >= max_samples:
                            break
            except Exception:
                continue

    return texts[:max_samples]


def _load_calibration_dataset(tokenizer, max_samples: int = 128) -> list:
    """Load calibration dataset as tokenized examples for GPTQ."""
    texts = _load_calibration_texts(max_samples)
    if not texts:
        # Use a minimal fallback
        texts = [
            "What is the process for TIN registration in Uganda?",
            "How do I file my VAT return with URA?",
            "What are the deadlines for PAYE submissions?",
        ] * (max_samples // 3 + 1)
        texts = texts[:max_samples]

    return [
        tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        for text in texts
    ]


# ---------------------------------------------------------------------------
# Manifest generation
# ---------------------------------------------------------------------------
def write_manifest(output_dir: Path, results: list[QuantResult], model_id: str) -> Path:
    """Write a JSON manifest summarizing all quantization results."""
    manifest = {
        "pipeline_version": "2026.1.0",
        "model": model_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "results": [asdict(r) for r in results],
        "summary": {
            "total": len(results),
            "success": sum(1 for r in results if r.status == "success"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "skipped": sum(1 for r in results if r.status == "skipped"),
        },
    }

    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)

    log.info("Manifest written: %s", manifest_path)
    return manifest_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automated model quantization pipeline for URA Chatbot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--model", default="Qwen/Qwen3-8B",
        help="HuggingFace model ID (default: Qwen/Qwen3-8B)",
    )
    parser.add_argument(
        "--formats", nargs="+", default=["gguf", "awq", "gptq", "onnx"],
        choices=["gguf", "awq", "gptq", "onnx"],
        help="Quantization formats to produce",
    )
    parser.add_argument(
        "--gguf-types", nargs="+", default=["Q4_K_M", "Q5_K_M", "Q8_0"],
        help="GGUF quantization types",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(os.getenv("QUANTIZE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
        help="Output directory for quantized artifacts",
    )
    parser.add_argument("--imatrix", action="store_true", help="Use importance matrix for GGUF")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration only")
    parser.add_argument("--awq-bits", type=int, default=4, help="AWQ bit width")
    parser.add_argument("--gptq-bits", type=int, default=4, help="GPTQ bit width")
    parser.add_argument("--gptq-group-size", type=int, default=128, help="GPTQ group size")

    args = parser.parse_args()
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")

    model_name = args.model.split("/")[-1]
    output_dir = args.output_dir / model_name
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("URA Chatbot Model Quantization Pipeline")
    log.info("=" * 60)
    log.info("Model:   %s", args.model)
    log.info("Formats: %s", ", ".join(args.formats))
    log.info("Output:  %s", output_dir)

    if args.dry_run:
        log.info("DRY RUN — validating configuration only")
        log.info("llama.cpp found: %s", find_llama_cpp() is not None)
        log.info("Calibration data: %d samples available", len(_load_calibration_texts(10)))
        return 0

    all_results: list[QuantResult] = []

    if "gguf" in args.formats:
        log.info("\n--- GGUF Quantization ---")
        results = quantize_gguf(
            args.model, output_dir, args.gguf_types,
            hf_token=hf_token, use_imatrix=args.imatrix,
        )
        all_results.extend(results)

    if "awq" in args.formats:
        log.info("\n--- AWQ Quantization ---")
        result = quantize_awq(
            args.model, output_dir,
            hf_token=hf_token, bits=args.awq_bits,
        )
        all_results.append(result)

    if "gptq" in args.formats:
        log.info("\n--- GPTQ Quantization ---")
        result = quantize_gptq(
            args.model, output_dir,
            hf_token=hf_token, bits=args.gptq_bits,
            group_size=args.gptq_group_size,
        )
        all_results.append(result)

    if "onnx" in args.formats:
        log.info("\n--- ONNX Export ---")
        result = export_onnx(args.model, output_dir, hf_token=hf_token)
        all_results.append(result)

    # Write manifest
    write_manifest(output_dir, all_results, args.model)

    # Summary
    log.info("\n" + "=" * 60)
    log.info("QUANTIZATION SUMMARY")
    log.info("=" * 60)
    for r in all_results:
        status_icon = {"success": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(r.status, "?")
        log.info(
            "  [%4s] %-6s %-12s %8s  %s",
            status_icon, r.format, r.quant_type,
            f"{r.size_mb:.0f}MB" if r.size_mb > 0 else "---",
            r.output_path or r.error,
        )

    failed = sum(1 for r in all_results if r.status == "failed")
    if failed:
        log.error("%d quantization(s) failed", failed)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
