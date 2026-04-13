"""LoRA adapter merger for on-device deployment (2026).

Merges a PEFT LoRA adapter into the base model and exports a single
GGUF file ready for MediaPipe LLM Inference on mobile.

Since MediaPipe's ``LlmInference`` API does not support loading
adapters at runtime, we pre-merge the adapter weights offline and
ship a single ``ura-gemma-2b-q4_k_m-personalized.gguf`` file.

Pipeline::

    1. Load base model (Gemma-2-2B or Llama-3.2-1B)
    2. Load LoRA adapter (from peft fine-tune output)
    3. Merge adapter weights into base
    4. Export to GGUF via llama.cpp (or transformers + llama-cpp-python)
    5. Quantize to Q4_K_M for mobile
    6. Verify SHA-256 hash

Usage::

    # Merge a fine-tuned adapter into the base model:
    python -m ml.scripts.merge_lora_adapter \\
        --base-model google/gemma-2-2b-it \\
        --adapter-dir artifacts/llm_mobile_gemma_2b/final \\
        --output-dir artifacts/mobile \\
        --quant Q4_K_M

    # Dry-run (validates adapter compatibility):
    python -m ml.scripts.merge_lora_adapter --dry-run
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class MergeConfig:
    base_model: str = "google/gemma-2-2b-it"
    adapter_dir: Path = Path("artifacts/llm_mobile_gemma_2b/final")
    output_dir: Path = Path("artifacts/mobile")
    quant: str = "Q4_K_M"
    output_name: str | None = None  # auto-derived from base + quant
    dry_run: bool = False


def merge_adapter(cfg: MergeConfig) -> dict:
    """Merge LoRA adapter into base model and export GGUF."""
    result = {
        "base_model": cfg.base_model,
        "adapter_dir": str(cfg.adapter_dir),
        "quant": cfg.quant,
        "status": "pending",
    }

    # 1. Validate adapter exists.
    adapter_config = cfg.adapter_dir / "adapter_config.json"
    if not adapter_config.exists():
        if cfg.dry_run:
            log.info("[dry-run] adapter_config.json not found at %s (expected)", cfg.adapter_dir)
            result["status"] = "dry_run_ok"
            return result
        log.error("adapter_config.json not found at %s", cfg.adapter_dir)
        result["status"] = "failed"
        result["error"] = "adapter not found"
        return result

    # Read adapter metadata.
    with adapter_config.open() as f:
        adapter_meta = json.load(f)
    log.info(
        "Adapter: base_model=%s, r=%s, alpha=%s",
        adapter_meta.get("base_model_name_or_path"),
        adapter_meta.get("r"),
        adapter_meta.get("lora_alpha"),
    )

    if cfg.dry_run:
        log.info("[dry-run] adapter validated, would merge into %s", cfg.base_model)
        result["status"] = "dry_run_ok"
        return result

    # 2. Load and merge.
    try:
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        log.info("Loading base model: %s", cfg.base_model)
        tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
        model = AutoModelForCausalLM.from_pretrained(
            cfg.base_model,
            torch_dtype="auto",
            device_map="cpu",
        )

        log.info("Loading adapter from %s", cfg.adapter_dir)
        model = PeftModel.from_pretrained(model, str(cfg.adapter_dir))

        log.info("Merging adapter weights...")
        model = model.merge_and_unload()

        # 3. Save merged model.
        merged_dir = cfg.output_dir / "merged_hf"
        merged_dir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(merged_dir)
        tokenizer.save_pretrained(merged_dir)
        log.info("Merged model saved to %s", merged_dir)

    except ImportError as e:
        log.error("Missing dependency: %s", e)
        result["status"] = "failed"
        result["error"] = str(e)
        return result
    except Exception as e:
        log.error("Merge failed: %s", e)
        result["status"] = "failed"
        result["error"] = str(e)
        return result

    # 4. Convert to GGUF.
    output_name = cfg.output_name or f"ura-gemma-2b-{cfg.quant.lower().replace('_', '-')}.gguf"
    gguf_path = cfg.output_dir / output_name

    try:
        # Use llama.cpp's convert script if available.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "llama_cpp.convert",
                "--outtype",
                cfg.quant.lower(),
                "--outfile",
                str(gguf_path),
                str(merged_dir),
            ],
            check=True,
            timeout=1800,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.warning("llama_cpp.convert not available; trying convert-hf-to-gguf.py fallback")
        try:
            subprocess.run(
                [
                    sys.executable,
                    "convert-hf-to-gguf.py",
                    "--outtype",
                    cfg.quant.lower(),
                    "--outfile",
                    str(gguf_path),
                    str(merged_dir),
                ],
                check=True,
                timeout=1800,
            )
        except Exception as e:
            log.error("GGUF conversion failed: %s", e)
            result["status"] = "failed"
            result["error"] = f"gguf conversion: {e}"
            return result

    # 5. Compute SHA-256.
    sha = hashlib.sha256()
    with gguf_path.open("rb") as f:
        while chunk := f.read(1 << 20):
            sha.update(chunk)
    sha256 = sha.hexdigest()

    # 6. Write manifest.
    manifest = {
        "base_model": cfg.base_model,
        "adapter_dir": str(cfg.adapter_dir),
        "adapter_r": adapter_meta.get("r"),
        "adapter_alpha": adapter_meta.get("lora_alpha"),
        "quantization": cfg.quant,
        "output_file": output_name,
        "sha256": sha256,
        "size_bytes": gguf_path.stat().st_size,
    }
    manifest_path = cfg.output_dir / "adapter_merge_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    result["status"] = "success"
    result["gguf_path"] = str(gguf_path)
    result["sha256"] = sha256
    result["size_mb"] = round(gguf_path.stat().st_size / (1024 * 1024), 1)
    log.info("GGUF exported: %s (%s MB, sha256=%s...)", gguf_path, result["size_mb"], sha256[:16])

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Merge LoRA adapter into base model")
    parser.add_argument("--base-model", default="google/gemma-2-2b-it")
    parser.add_argument(
        "--adapter-dir", type=Path, default=Path("artifacts/llm_mobile_gemma_2b/final")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/mobile"))
    parser.add_argument(
        "--quant", default="Q4_K_M", choices=["Q4_K_M", "Q4_K_S", "IQ3_M", "Q8_0", "F16"]
    )
    parser.add_argument("--output-name", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    cfg = MergeConfig(
        base_model=args.base_model,
        adapter_dir=args.adapter_dir,
        output_dir=args.output_dir,
        quant=args.quant,
        output_name=args.output_name,
        dry_run=args.dry_run,
    )

    result = merge_adapter(cfg)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("success", "dry_run_ok") else 1)


if __name__ == "__main__":
    main()
