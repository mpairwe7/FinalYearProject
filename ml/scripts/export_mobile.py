#!/usr/bin/env python3
"""Export fine-tuned Gemma-2-2B for on-device mobile inference.

Conversion pipeline (2026 production standard):
  1. Merge LoRA adapters into base model weights
  2. Convert merged model to GGUF format (llama.cpp compatible)
  3. Quantize to INT4 (Q4_K_M) for mobile — ~1.5 GB on disk
  4. Optionally export to MediaPipe-compatible format for Android/iOS

The exported GGUF model runs on-device via:
  - Android: MediaPipe LLM Inference API or llama.cpp JNI
  - iOS: MediaPipe LLM Inference API or llama.cpp Metal
  - Flutter: Platform channels → native MediaPipe/llama.cpp

Requirements:
    pip install transformers peft torch llama-cpp-python

Usage:
    # Export from fine-tuned LoRA adapter
    python ml/scripts/export_mobile.py --adapter artifacts/models/ura-gemma-2-2b-it-*/final

    # Export with custom quantization
    python ml/scripts/export_mobile.py --adapter path/to/adapter --quant Q4_K_M

    # Dry run (validate without converting)
    python ml/scripts/export_mobile.py --adapter path/to/adapter --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "mobile"

# Supported quantization types (llama.cpp naming)
QUANT_TYPES = {
    "Q4_K_M": "4-bit (recommended for mobile, ~1.5 GB)",
    "Q4_K_S": "4-bit smaller (lower quality, ~1.3 GB)",
    "Q5_K_M": "5-bit (better quality, ~1.8 GB)",
    "Q8_0": "8-bit (highest quality, ~2.5 GB)",
    "F16": "float16 (no quantization, ~4 GB)",
}

DEFAULT_QUANT = "Q4_K_M"


# ---------------------------------------------------------------------------
# Step 1: Merge LoRA adapters
# ---------------------------------------------------------------------------
def merge_lora_adapter(adapter_path: Path, output_path: Path) -> Path:
    """Merge LoRA adapter weights back into the base model."""
    logger.info("Step 1: Merging LoRA adapter into base model...")

    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    # Load adapter config to find base model
    adapter_config_path = adapter_path / "adapter_config.json"
    if adapter_config_path.exists():
        with open(adapter_config_path) as f:
            adapter_config = json.load(f)
        base_model_id = adapter_config.get("base_model_name_or_path", "")
        logger.info("  Base model: %s", base_model_id)
    else:
        # Try loading from the adapter directory directly (full model save)
        base_model_id = str(adapter_path)
        logger.info("  No adapter_config.json found, loading as full model from: %s", adapter_path)

    # Load base model + tokenizer
    logger.info("  Loading base model...")
    import torch

    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_id,
        torch_dtype=torch.float16,
        device_map="cpu",  # CPU for export
        trust_remote_code=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        base_model_id,
        trust_remote_code=True,
    )

    # Merge LoRA if adapter config exists
    if adapter_config_path.exists():
        logger.info("  Applying LoRA adapter...")
        model = PeftModel.from_pretrained(base_model, str(adapter_path))
        logger.info("  Merging adapter weights...")
        model = model.merge_and_unload()
    else:
        model = base_model

    # Save merged model
    merged_path = output_path / "merged_model"
    merged_path.mkdir(parents=True, exist_ok=True)
    logger.info("  Saving merged model to %s...", merged_path)
    model.save_pretrained(str(merged_path), safe_serialization=True)
    tokenizer.save_pretrained(str(merged_path))

    param_count = sum(p.numel() for p in model.parameters()) / 1e9
    logger.info("  Merged model saved (%.2fB params)", param_count)

    return merged_path


# ---------------------------------------------------------------------------
# Step 2: Convert to GGUF
# ---------------------------------------------------------------------------
def convert_to_gguf(merged_path: Path, output_path: Path) -> Path:
    """Convert HuggingFace model to GGUF format using llama.cpp's convert script."""
    logger.info("Step 2: Converting to GGUF format...")

    gguf_path = output_path / "model-f16.gguf"

    # Try llama.cpp's convert_hf_to_gguf.py
    convert_script = _find_convert_script()
    if convert_script is None:
        # Fallback: use the transformers-to-gguf approach
        logger.info("  llama.cpp convert script not found, trying gguf Python package...")
        _convert_with_gguf_package(merged_path, gguf_path)
    else:
        cmd = [
            sys.executable,
            str(convert_script),
            str(merged_path),
            "--outfile",
            str(gguf_path),
            "--outtype",
            "f16",
        ]
        logger.info("  Running: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("  Convert failed: %s", result.stderr)
            raise RuntimeError(f"GGUF conversion failed: {result.stderr}")

    if not gguf_path.exists():
        raise FileNotFoundError(f"GGUF output not found: {gguf_path}")

    size_gb = gguf_path.stat().st_size / (1024**3)
    logger.info("  GGUF F16 model: %.2f GB", size_gb)
    return gguf_path


def _find_convert_script() -> Path | None:
    """Find llama.cpp's convert_hf_to_gguf.py script."""
    # Check common locations
    search_paths = [
        Path.home() / "llama.cpp" / "convert_hf_to_gguf.py",
        Path("/usr/local/share/llama.cpp/convert_hf_to_gguf.py"),
        PROJECT_ROOT / "vendor" / "llama.cpp" / "convert_hf_to_gguf.py",
    ]

    # Also check PATH
    for p in search_paths:
        if p.exists():
            return p

    # Try to find via pip install location
    try:
        import llama_cpp

        pkg_dir = Path(llama_cpp.__file__).parent
        script = pkg_dir / "convert_hf_to_gguf.py"
        if script.exists():
            return script
    except ImportError:
        pass

    return None


def _convert_with_gguf_package(merged_path: Path, gguf_path: Path) -> None:
    """Convert using the gguf Python package (pip install gguf)."""
    try:
        cmd = [
            sys.executable,
            "-m",
            "gguf",
            "convert",
            str(merged_path),
            "--output",
            str(gguf_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
    except Exception as e:
        logger.error(
            "  GGUF conversion failed. Install llama.cpp or the gguf package:\n"
            "    pip install gguf\n"
            "    # or clone llama.cpp: git clone https://github.com/ggerganov/llama.cpp"
        )
        raise RuntimeError(f"No GGUF converter available: {e}") from e


# ---------------------------------------------------------------------------
# Step 3: Quantize
# ---------------------------------------------------------------------------
def quantize_gguf(
    gguf_f16_path: Path,
    output_path: Path,
    quant_type: str = DEFAULT_QUANT,
) -> Path:
    """Quantize GGUF model to target precision."""
    logger.info("Step 3: Quantizing to %s...", quant_type)

    if quant_type == "F16":
        logger.info("  Skipping quantization (F16 requested)")
        return gguf_f16_path

    quant_path = output_path / f"ura-gemma-2b-{quant_type.lower()}.gguf"

    # Find llama-quantize binary
    quantize_bin = _find_quantize_binary()
    if quantize_bin is None:
        logger.warning(
            "  llama-quantize not found. Install llama.cpp:\n"
            "    brew install llama.cpp  # macOS\n"
            "    # or build from source: https://github.com/ggerganov/llama.cpp"
        )
        logger.info("  Falling back to llama-cpp-python quantization...")
        _quantize_with_python(gguf_f16_path, quant_path, quant_type)
    else:
        cmd = [str(quantize_bin), str(gguf_f16_path), str(quant_path), quant_type]
        logger.info("  Running: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Quantization failed: {result.stderr}")

    if not quant_path.exists():
        raise FileNotFoundError(f"Quantized model not found: {quant_path}")

    size_gb = quant_path.stat().st_size / (1024**3)
    logger.info("  Quantized model: %.2f GB (%s)", size_gb, quant_type)
    return quant_path


def _find_quantize_binary() -> Path | None:
    """Find llama-quantize binary."""
    binary_names = ["llama-quantize", "quantize"]
    search_dirs = [
        Path.home() / "llama.cpp" / "build" / "bin",
        Path.home() / "llama.cpp",
        Path("/usr/local/bin"),
        PROJECT_ROOT / "vendor" / "llama.cpp" / "build" / "bin",
    ]

    for d in search_dirs:
        for name in binary_names:
            p = d / name
            if p.exists() and os.access(p, os.X_OK):
                return p

    # Check PATH
    result = shutil.which("llama-quantize") or shutil.which("quantize")
    return Path(result) if result else None


def _quantize_with_python(
    gguf_f16_path: Path, quant_path: Path, quant_type: str
) -> None:
    """Fallback quantization using llama-cpp-python."""
    try:
        from llama_cpp import llama_model_quantize

        # Map our type names to llama.cpp enum values
        type_map = {
            "Q4_K_M": 15,
            "Q4_K_S": 14,
            "Q5_K_M": 17,
            "Q8_0": 7,
        }
        ftype = type_map.get(quant_type)
        if ftype is None:
            raise ValueError(f"Unsupported quantization type: {quant_type}")

        llama_model_quantize(
            str(gguf_f16_path).encode(),
            str(quant_path).encode(),
            ftype,
        )
    except ImportError:
        raise RuntimeError(
            "Neither llama-quantize binary nor llama-cpp-python found.\n"
            "Install one of:\n"
            "  pip install llama-cpp-python\n"
            "  brew install llama.cpp"
        )


# ---------------------------------------------------------------------------
# Step 4: Validate + Generate manifest
# ---------------------------------------------------------------------------
def validate_and_manifest(
    quant_path: Path,
    output_path: Path,
    adapter_path: Path,
    quant_type: str,
) -> dict[str, Any]:
    """Validate the exported model and generate a deployment manifest."""
    logger.info("Step 4: Validating exported model...")

    # SHA-256 checksum
    sha256 = hashlib.sha256()
    with open(quant_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    checksum = sha256.hexdigest()

    size_bytes = quant_path.stat().st_size
    size_mb = size_bytes / (1024**2)

    # Try loading with llama-cpp-python for validation
    can_load = False
    try:
        from llama_cpp import Llama

        logger.info("  Testing model load (llama-cpp-python)...")
        llm = Llama(model_path=str(quant_path), n_ctx=1024, n_gpu_layers=0, verbose=False)
        # Quick inference test
        output = llm(
            "What is VAT?",
            max_tokens=20,
            temperature=0.1,
        )
        test_text = output["choices"][0]["text"].strip() if output.get("choices") else ""
        can_load = True
        logger.info("  Model loads OK, test output: %s", test_text[:60])
        del llm
    except ImportError:
        logger.info("  llama-cpp-python not installed, skipping load test")
    except Exception as e:
        logger.warning("  Model load test failed: %s", e)

    manifest = {
        "model_name": "ura-gemma-2b",
        "base_model": "google/gemma-2-2b-it",
        "adapter_path": str(adapter_path),
        "export_format": "gguf",
        "quantization": quant_type,
        "file_name": quant_path.name,
        "size_bytes": size_bytes,
        "size_mb": round(size_mb, 1),
        "sha256": checksum,
        "validated": can_load,
        "deployment_targets": [
            "Android (MediaPipe LLM Inference API)",
            "iOS (MediaPipe LLM Inference API)",
            "Flutter (platform channels → MediaPipe/llama.cpp)",
        ],
        "min_ram_mb": round(size_mb * 1.3),  # ~30% overhead for inference
        "recommended_devices": "Android 12+ (6+ GB RAM) / iOS 16+ (iPhone 12+)",
    }

    manifest_path = output_path / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info("  Manifest: %s", manifest_path)

    return manifest


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export fine-tuned Gemma-2B for on-device mobile inference",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quantization types:
"""
        + "\n".join(f"  {k:10s} — {v}" for k, v in QUANT_TYPES.items())
        + """

Examples:
  %(prog)s --adapter artifacts/models/ura-gemma-2-2b-it-*/final
  %(prog)s --adapter path/to/adapter --quant Q4_K_M
  %(prog)s --adapter path/to/adapter --dry-run
""",
    )

    parser.add_argument(
        "--adapter",
        type=Path,
        required=True,
        help="Path to fine-tuned LoRA adapter (or merged model) directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--quant",
        type=str,
        default=DEFAULT_QUANT,
        choices=list(QUANT_TYPES.keys()),
        help=f"Quantization type (default: {DEFAULT_QUANT})",
    )
    parser.add_argument(
        "--keep-merged",
        action="store_true",
        help="Keep the intermediate merged FP16 model (large, ~4 GB)",
    )
    parser.add_argument(
        "--keep-gguf-f16",
        action="store_true",
        help="Keep the intermediate GGUF F16 file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate adapter without converting",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("GEMMA-2B MOBILE EXPORT PIPELINE")
    print("=" * 70)
    print(f"  Adapter:      {args.adapter}")
    print(f"  Output:       {args.output}")
    print(f"  Quantization: {args.quant} — {QUANT_TYPES[args.quant]}")
    print()

    # Validate adapter path
    if not args.adapter.exists():
        logger.error("Adapter path not found: %s", args.adapter)
        sys.exit(1)

    if args.dry_run:
        logger.info("Dry run — validating adapter only")
        # Check if it's a valid HF model/adapter directory
        has_config = (args.adapter / "config.json").exists()
        has_adapter = (args.adapter / "adapter_config.json").exists()
        has_safetensors = list(args.adapter.glob("*.safetensors"))
        has_bin = list(args.adapter.glob("*.bin"))

        logger.info("  config.json:        %s", "found" if has_config else "MISSING")
        logger.info("  adapter_config.json: %s", "found" if has_adapter else "not found (full model)")
        logger.info("  Model weights:      %d files", len(has_safetensors) + len(has_bin))

        if not has_config and not has_adapter:
            logger.error("  No valid model found at %s", args.adapter)
            sys.exit(1)

        logger.info("\nDry run complete. Adapter looks valid.")
        return

    # Create output directory
    args.output.mkdir(parents=True, exist_ok=True)

    # Step 1: Merge LoRA
    merged_path = merge_lora_adapter(args.adapter, args.output)

    # Step 2: Convert to GGUF
    gguf_f16_path = convert_to_gguf(merged_path, args.output)

    # Step 3: Quantize
    quant_path = quantize_gguf(gguf_f16_path, args.output, args.quant)

    # Step 4: Validate + manifest
    manifest = validate_and_manifest(quant_path, args.output, args.adapter, args.quant)

    # Cleanup intermediate files
    if not args.keep_merged and merged_path.exists():
        logger.info("Cleaning up merged model (use --keep-merged to keep)...")
        shutil.rmtree(merged_path, ignore_errors=True)
    if not args.keep_gguf_f16 and gguf_f16_path != quant_path and gguf_f16_path.exists():
        logger.info("Cleaning up GGUF F16 (use --keep-gguf-f16 to keep)...")
        gguf_f16_path.unlink(missing_ok=True)

    # Summary
    print()
    print("=" * 70)
    print("EXPORT COMPLETE")
    print("=" * 70)
    print(f"  Model:        {quant_path}")
    print(f"  Size:         {manifest['size_mb']} MB")
    print(f"  Quantization: {args.quant}")
    print(f"  SHA-256:      {manifest['sha256'][:16]}...")
    print(f"  Validated:    {manifest['validated']}")
    print(f"  Min RAM:      {manifest['min_ram_mb']} MB")
    print()
    print("Deploy to mobile:")
    print("  1. Copy GGUF file to Android assets/ or iOS bundle")
    print("  2. Use MediaPipe LLM Inference API (Android/iOS)")
    print("  3. Or use llama.cpp native bindings via Flutter platform channels")
    print()
    print(f"Manifest: {args.output / 'manifest.json'}")


if __name__ == "__main__":
    main()
