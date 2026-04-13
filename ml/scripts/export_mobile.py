#!/usr/bin/env python3
"""Export a fine-tuned Gemma-2-2B LoRA into a mobile-deployable GGUF.

2026 production pipeline:

    1. **Discover** the latest fine-tune output (or take ``--adapter <path>``)
    2. **Merge** the LoRA adapter into the base model weights
    3. **Convert** the merged HF model to GGUF F16 via llama.cpp's
       ``convert_hf_to_gguf.py``
    4. **(Optional) Imatrix calibration** — compute an importance matrix
       from a small sample of training data to improve low-bit quantisation
       quality (the 2024-2025 best practice for sub-3-bit quants)
    5. **Quantise** to a target precision (Q4_K_M default, IQ-series
       supported for sub-2GB mobile sizes)
    6. **Validate** the quantised GGUF (size, SHA-256, optional load test)
    7. **Generate model card + manifest** with full lineage from the
       fine_tune ``training_config.json`` and the data ``manifest.json``
    8. **Deploy to MobileApp** — atomically copy to:
         - ``MobileApp/ura_chatbot/android/app/src/main/assets/models/``
         - ``MobileApp/ura_chatbot/ios/Runner/models/`` (staging — must be
           added to the Xcode project once)
       and verify post-copy SHA-256

The exported GGUF runs on-device via the MediaPipe LLM Inference API
(Android ``com.google.mediapipe:tasks-genai`` / iOS ``MediaPipeTasksGenAI``)
which is the only inference engine the Flutter ``OnDeviceLlm`` class
talks to via platform channels (see ``lib/core/inference/on_device_llm.dart``).

Requirements:
    pip install transformers peft torch gguf
    # llama.cpp built locally or auto-discovered (looks for llama-quantize)

Usage:
    # Auto-discover latest fine-tune, deploy to MobileApp
    python ml/scripts/export_mobile.py

    # Explicit adapter, custom quant, no deploy
    python ml/scripts/export_mobile.py \\
        --adapter artifacts/ura-gemma-2-2b-it-20260411_193000/final \\
        --quant Q4_K_M \\
        --no-deploy

    # With imatrix calibration (better low-bit quality)
    python ml/scripts/export_mobile.py --quant IQ4_NL --imatrix

    # Dry run (validate adapter only)
    python ml/scripts/export_mobile.py --dry-run
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("export_mobile")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
OUTPUT_DIR = ARTIFACTS_DIR / "mobile"

# Mobile asset destinations (atomically copied at the end of the pipeline).
MOBILE_ROOT = PROJECT_ROOT / "MobileApp" / "ura_chatbot"
ANDROID_ASSETS = MOBILE_ROOT / "android" / "app" / "src" / "main" / "assets" / "models"
IOS_STAGING = MOBILE_ROOT / "ios" / "Runner" / "models"

# Pipeline + schema versions — bumped on backwards-incompatible changes.
PIPELINE_VERSION = "2026.1.0"
SCHEMA_VERSION = "2026.1"

# Filename the Flutter ``OnDeviceLlmConfig`` looks for. Hardcoded in
# ``lib/core/inference/on_device_llm.dart`` so must stay stable.
DEFAULT_MOBILE_FILENAME = "ura-gemma-2b-q4_k_m.gguf"


# ---------------------------------------------------------------------------
# Quantisation registry
# ---------------------------------------------------------------------------
# Maps llama.cpp quant names → human-readable description and approximate
# size for a 2.6B-param model. The IQ-series quants are 2024-2025 additions
# that give substantially better quality at < 3 bits/weight using a learned
# bookkeeping table — preferred for sub-2GB mobile builds.

QUANT_TYPES: dict[str, str] = {
    # Recommended mobile defaults
    "Q4_K_M": "4-bit K-quant medium — recommended mobile default (~1.6 GB)",
    "Q4_K_S": "4-bit K-quant small — (~1.4 GB)",
    "Q5_K_M": "5-bit K-quant medium — better quality (~1.9 GB)",
    "Q5_K_S": "5-bit K-quant small — (~1.7 GB)",
    "Q6_K": "6-bit K-quant — near-lossless (~2.2 GB)",
    "Q8_0": "8-bit — highest quality short of F16 (~2.8 GB)",
    # IQ-series (2024+) — best sub-3-bit options, benefit from imatrix
    "IQ4_NL": "4-bit non-linear — better than Q4_K_M with imatrix (~1.5 GB)",
    "IQ4_XS": "4-bit extra small — (~1.4 GB)",
    "IQ3_M": "3-bit medium — sub-1.5 GB mobile (~1.2 GB) ⚠ needs imatrix",
    "IQ3_S": "3-bit small — (~1.1 GB) ⚠ needs imatrix",
    "IQ2_M": "2-bit medium — extreme compression (~0.9 GB) ⚠ needs imatrix",
    # Pass-through
    "F16": "float16 — no quantisation (~5.2 GB, dev/eval only)",
}

# Quants that are essentially unusable without an imatrix calibration.
QUANTS_REQUIRING_IMATRIX = {"IQ3_M", "IQ3_S", "IQ2_M"}

DEFAULT_QUANT = "Q4_K_M"


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


@dataclass
class LlamaCppTools:
    convert_script: Path | None = None
    quantize_bin: Path | None = None
    imatrix_bin: Path | None = None
    cli_bin: Path | None = None
    repo_root: Path | None = None

    @property
    def has_convert(self) -> bool:
        return self.convert_script is not None

    @property
    def has_quantize(self) -> bool:
        return self.quantize_bin is not None

    @property
    def has_imatrix(self) -> bool:
        return self.imatrix_bin is not None


def _candidate_llama_cpp_dirs() -> list[Path]:
    """Locations where llama.cpp may be installed on dev/CI machines."""
    return [
        Path(os.environ.get("LLAMA_CPP_DIR", "")),
        PROJECT_ROOT / "vendor" / "llama.cpp",
        Path.home() / "llama.cpp",
        Path.home() / "dir_andrew" / "qa_data_analysis" / "llama.cpp",
        Path("/opt/llama.cpp"),
        Path("/usr/local/share/llama.cpp"),
    ]


def _find_cuda_runtime_dirs() -> list[str]:
    """Return directories containing CUDA runtime libs (cudart, cublas, etc).

    llama.cpp binaries are often built against a different CUDA version
    than the host system (e.g. CUDA 12 vs host CUDA 11) and dynamic-link
    against ``libcudart.so.<N>``, ``libcublas.so.<N>``, ``libcudnn.so``,
    and friends. We discover all of these from pip-installed
    ``nvidia-*`` wheels which drop their libs under
    ``site-packages/nvidia/<component>/lib/``. Every such dir that
    contains *any* ``lib*.so*`` file is added to the returned list.

    Also includes torch's bundled lib dir, which commonly ships
    ``libcudart`` and ``libcublas`` as a fallback.
    """
    import site

    dirs: set[str] = set()
    candidates: list[Path] = []

    for sp in set(site.getsitepackages() + [site.getusersitepackages()]):
        nvidia_root = Path(sp) / "nvidia"
        if nvidia_root.exists():
            # Every nvidia-*/lib subdirectory is a candidate. This
            # captures cuda_runtime, cublas, cudnn, cuda_cupti,
            # cusparse, cufft, curand, ...
            for component in nvidia_root.iterdir():
                lib_dir = component / "lib"
                if lib_dir.exists():
                    candidates.append(lib_dir)

    # Torch bundles some CUDA libs directly.
    try:
        import torch  # type: ignore

        torch_lib = Path(torch.__file__).parent / "lib"
        if torch_lib.exists():
            candidates.append(torch_lib)
    except Exception:
        pass

    for d in candidates:
        if d.exists() and any(d.glob("lib*.so*")):
            dirs.add(str(d))
    return sorted(dirs)


def _llama_cpp_env(tools: LlamaCppTools) -> dict[str, str]:
    """Build a subprocess env with ``LD_LIBRARY_PATH`` pointing at the
    llama.cpp build dir + discovered CUDA runtime dirs.

    The shipped ``llama-quantize`` and ``llama-imatrix`` binaries dynamic-link:
      * ``libllama.so`` and ``libggml*.so`` (lives in llama.cpp build/bin)
      * ``libcudart.so.<N>`` (CUDA version that llama.cpp was built against,
        which is frequently newer than the host CUDA — we source it from
        pip-installed ``nvidia-cuda-runtime`` wheels)

    Without these overrides the subprocess fails at startup with
    "cannot open shared object file".
    """
    env = os.environ.copy()
    lib_dirs: list[str] = []
    if tools.quantize_bin is not None:
        lib_dirs.append(str(tools.quantize_bin.parent))
    if tools.imatrix_bin is not None:
        parent = str(tools.imatrix_bin.parent)
        if parent not in lib_dirs:
            lib_dirs.append(parent)
    lib_dirs.extend(_find_cuda_runtime_dirs())
    if lib_dirs:
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(lib_dirs + ([existing] if existing else []))
    return env


def discover_llama_cpp() -> LlamaCppTools:
    """Locate llama.cpp's convert script + quantize/imatrix binaries.

    Returns a struct with whichever tools we found; the caller decides
    whether to error out (e.g. quantize is mandatory) or fall back
    (e.g. imatrix is optional).
    """
    tools = LlamaCppTools()

    for d in _candidate_llama_cpp_dirs():
        if not d or not d.exists():
            continue

        # Convert script
        if tools.convert_script is None:
            cs = d / "convert_hf_to_gguf.py"
            if cs.exists():
                tools.convert_script = cs
                tools.repo_root = d

        # Built binaries
        for bin_dir in (d / "build" / "bin", d / "build", d):
            if not bin_dir.exists():
                continue
            for name, attr in (
                ("llama-quantize", "quantize_bin"),
                ("llama-imatrix", "imatrix_bin"),
                ("llama-cli", "cli_bin"),
            ):
                p = bin_dir / name
                if p.exists() and os.access(p, os.X_OK) and getattr(tools, attr) is None:
                    setattr(tools, attr, p)

        if tools.has_convert and tools.has_quantize:
            break  # found a complete install

    # Fall back to PATH
    if tools.quantize_bin is None:
        path_q = shutil.which("llama-quantize") or shutil.which("quantize")
        if path_q:
            tools.quantize_bin = Path(path_q)
    if tools.imatrix_bin is None:
        path_i = shutil.which("llama-imatrix")
        if path_i:
            tools.imatrix_bin = Path(path_i)
    if tools.cli_bin is None:
        path_c = shutil.which("llama-cli")
        if path_c:
            tools.cli_bin = Path(path_c)

    log.info(
        "llama.cpp tools: convert=%s quantize=%s imatrix=%s cli=%s",
        bool(tools.convert_script),
        bool(tools.quantize_bin),
        bool(tools.imatrix_bin),
        bool(tools.cli_bin),
    )
    if tools.repo_root:
        log.info("  repo: %s", tools.repo_root)
    return tools


# ---------------------------------------------------------------------------
# Adapter discovery
# ---------------------------------------------------------------------------


def find_latest_adapter() -> Path | None:
    """Return the most recently modified ``*/final/`` directory under
    ``artifacts/`` that contains an ``adapter_config.json`` (LoRA) or
    ``config.json`` (full model)."""
    candidates: list[Path] = []
    for d in ARTIFACTS_DIR.glob("ura-*/final"):
        if (d / "adapter_config.json").exists() or (d / "config.json").exists():
            candidates.append(d)
    # Also accept artifacts/models/ura-*/final layout
    for d in (
        (ARTIFACTS_DIR / "models").glob("ura-*/final")
        if (ARTIFACTS_DIR / "models").exists()
        else []
    ):
        if (d / "adapter_config.json").exists() or (d / "config.json").exists():
            candidates.append(d)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def read_adapter_lineage(adapter_path: Path) -> dict[str, Any]:
    """Read ``training_config.json`` from the adapter's parent dir to
    surface the dataset SHA, git commit, LoRA config, and seed in the
    mobile MODEL_CARD."""
    lineage: dict[str, Any] = {}

    # training_config.json is written one level up from /final by fine_tune_gemma
    for candidate in (
        adapter_path / "training_config.json",
        adapter_path.parent / "training_config.json",
    ):
        if candidate.exists():
            try:
                lineage = json.loads(candidate.read_text())
                lineage["_source"] = str(candidate)
                break
            except Exception as exc:
                log.debug("could not parse %s: %s", candidate, exc)

    # adapter_config.json gives us the base model id
    ac = adapter_path / "adapter_config.json"
    if ac.exists():
        with contextlib.suppress(Exception):
            lineage.setdefault(
                "base_model_id",
                json.loads(ac.read_text()).get("base_model_name_or_path"),
            )

    return lineage


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    """Streaming SHA-256 of a file (full hex digest)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head() -> dict[str, Any]:
    """Repo HEAD info for the manifest. Empty dict outside a git repo."""

    def _run(cmd: list[str]) -> str:
        return subprocess.check_output(
            cmd, cwd=PROJECT_ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()

    try:
        return {
            "commit": _run(["git", "rev-parse", "HEAD"]),
            "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
            "dirty": bool(_run(["git", "status", "--porcelain"])),
        }
    except Exception:
        return {"commit": None, "branch": None, "dirty": None}


# ---------------------------------------------------------------------------
# Step 1 — merge LoRA adapter
# ---------------------------------------------------------------------------


def merge_lora_adapter(adapter_path: Path, output_path: Path) -> tuple[Path, str]:
    """Merge LoRA adapter weights back into the base model.

    Returns ``(merged_path, base_model_id)``. ``base_model_id`` is the HF
    repo name (e.g. ``google/gemma-2-2b-it``) read from
    ``adapter_config.json``.
    """
    log.info("STAGE 1: merging LoRA adapter into base model")

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adapter_config_path = adapter_path / "adapter_config.json"
    is_lora = adapter_config_path.exists()

    if is_lora:
        adapter_config = json.loads(adapter_config_path.read_text())
        base_model_id = adapter_config.get("base_model_name_or_path", "")
        if not base_model_id:
            raise RuntimeError(
                f"adapter_config.json at {adapter_config_path} has no " "base_model_name_or_path"
            )
        log.info("  Base model: %s", base_model_id)
    else:
        # Adapter dir IS the model (someone passed a merged checkpoint)
        config_path = adapter_path / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(
                f"Neither adapter_config.json nor config.json found in {adapter_path}"
            )
        base_model_id = str(adapter_path)
        log.info("  Loading as full model from: %s", adapter_path)

    log.info("  Loading base model...")
    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_id,
        torch_dtype=torch.float16,
        device_map="cpu",  # CPU export — model is fp16 on disk anyway
        low_cpu_mem_usage=True,
        trust_remote_code=False,
    )
    tokenizer = AutoTokenizer.from_pretrained(base_model_id, trust_remote_code=False)

    if is_lora:
        log.info("  Applying LoRA adapter...")
        model = PeftModel.from_pretrained(base_model, str(adapter_path))
        log.info("  Merging adapter into base weights...")
        model = model.merge_and_unload()
    else:
        model = base_model

    merged_path = output_path / "merged_model"
    merged_path.mkdir(parents=True, exist_ok=True)
    log.info("  Saving merged model to %s ...", merged_path)
    model.save_pretrained(
        str(merged_path),
        safe_serialization=True,
        max_shard_size="4GB",
    )
    tokenizer.save_pretrained(str(merged_path))

    param_count = sum(p.numel() for p in model.parameters()) / 1e9
    log.info("  Merged model saved (%.2fB params)", param_count)
    return merged_path, base_model_id


# ---------------------------------------------------------------------------
# Step 2 — convert to GGUF F16
# ---------------------------------------------------------------------------


def convert_to_gguf(
    merged_path: Path,
    output_path: Path,
    tools: LlamaCppTools,
) -> Path:
    """Convert HF model → GGUF F16 via llama.cpp's convert script."""
    log.info("STAGE 2: converting merged HF model → GGUF F16")

    if not tools.has_convert:
        raise RuntimeError(
            "llama.cpp's convert_hf_to_gguf.py was not found. "
            "Either set LLAMA_CPP_DIR=/path/to/llama.cpp or clone llama.cpp "
            "to ~/llama.cpp / vendor/llama.cpp."
        )

    gguf_path = output_path / "model-f16.gguf"

    cmd = [
        sys.executable,
        str(tools.convert_script),
        str(merged_path),
        "--outfile",
        str(gguf_path),
        "--outtype",
        "f16",
    ]
    log.info("  $ %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("  convert_hf_to_gguf failed:\n%s", result.stderr)
        raise RuntimeError("GGUF conversion failed (see log)")

    if not gguf_path.exists():
        raise FileNotFoundError(f"GGUF output not produced: {gguf_path}")

    size_gb = gguf_path.stat().st_size / (1024**3)
    log.info("  GGUF F16: %.2f GB", size_gb)
    return gguf_path


# ---------------------------------------------------------------------------
# Step 2.5 — (optional) Imatrix calibration
# ---------------------------------------------------------------------------


def compute_imatrix(
    gguf_f16: Path,
    output_path: Path,
    tools: LlamaCppTools,
    *,
    calibration_jsonl: Path | None = None,
    sample_size: int = 256,
) -> Path | None:
    """Compute an importance matrix for low-bit quantisation.

    The imatrix is a per-tensor weighting derived from running ~hundreds of
    real prompts through the FP16 model and recording activation statistics.
    Quantisation tools then use it to allocate more bits to tensors that
    matter most. For sub-3-bit quants this is the difference between a
    usable model and gibberish (Llama.cpp 2024-2025 best practice).

    Calibration data: prefer the project's training data so the imatrix
    is matched to the deployment domain. Falls back to a small built-in
    English/Luganda prompt mix if no JSONL is given.
    """
    if not tools.has_imatrix:
        log.warning("  llama-imatrix binary missing — skipping calibration")
        return None

    log.info("STAGE 2b: computing imatrix calibration")

    # Build a calibration text file from the training data
    calib_txt = output_path / "calibration.txt"
    if calibration_jsonl is None:
        candidates = [
            ARTIFACTS_DIR / "training_data" / "train.messages.jsonl",
            ARTIFACTS_DIR / "training_data" / "training_data.messages.jsonl",
        ]
        calibration_jsonl = next((c for c in candidates if c.exists()), None)

    lines: list[str] = []
    if calibration_jsonl and calibration_jsonl.exists():
        log.info("  calibration source: %s", calibration_jsonl)
        with open(calibration_jsonl, encoding="utf-8") as f:
            for i, raw in enumerate(f):
                if i >= sample_size:
                    break
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                msgs = row.get("messages", [])
                # Concatenate user + assistant for the calibration prompt
                parts = [
                    m.get("content", "") for m in msgs if m.get("role") in {"user", "assistant"}
                ]
                if parts:
                    lines.append("\n".join(parts).strip())
    else:
        log.warning("  no calibration JSONL found — falling back to a tiny English prompt mix")
        lines = [
            "What is the standard VAT rate in Uganda?",
            "How do I file a tax return with URA?",
            "Explain the difference between PAYE and rental income tax.",
            "What is a TIN and why do I need one?",
        ] * 16

    if not lines:
        log.warning("  no calibration text produced — skipping imatrix")
        return None

    calib_txt.write_text("\n\n".join(lines), encoding="utf-8")
    imatrix_path = output_path / "imatrix.dat"

    cmd = [
        str(tools.imatrix_bin),
        "-m",
        str(gguf_f16),
        "-f",
        str(calib_txt),
        "-o",
        str(imatrix_path),
        "--chunks",
        str(min(len(lines), 100)),
    ]
    log.info("  $ %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_llama_cpp_env(tools),
    )
    if result.returncode != 0 or not imatrix_path.exists():
        log.warning("  imatrix calibration failed:\n%s", result.stderr or result.stdout)
        return None

    log.info("  imatrix written: %s (%d bytes)", imatrix_path, imatrix_path.stat().st_size)
    return imatrix_path


# ---------------------------------------------------------------------------
# Step 3 — Quantise
# ---------------------------------------------------------------------------


def quantize_gguf(
    gguf_f16_path: Path,
    output_path: Path,
    tools: LlamaCppTools,
    quant_type: str = DEFAULT_QUANT,
    imatrix_path: Path | None = None,
) -> Path:
    """Quantise GGUF model to target precision via ``llama-quantize``."""
    log.info("STAGE 3: quantising → %s", quant_type)

    if quant_type == "F16":
        log.info("  F16 requested — passing through")
        return gguf_f16_path

    if not tools.has_quantize:
        raise RuntimeError(
            "llama-quantize binary not found. Build llama.cpp:\n"
            "  cmake -B build && cmake --build build --target llama-quantize"
        )

    if quant_type in QUANTS_REQUIRING_IMATRIX and imatrix_path is None:
        log.warning(
            "  %s strongly recommends an imatrix; quality will be poor without --imatrix",
            quant_type,
        )

    quant_path = output_path / f"ura-gemma-2b-{quant_type.lower()}.gguf"

    cmd = [str(tools.quantize_bin)]
    if imatrix_path is not None:
        cmd.extend(["--imatrix", str(imatrix_path)])
    cmd.extend([str(gguf_f16_path), str(quant_path), quant_type])

    log.info("  $ %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_llama_cpp_env(tools),
    )
    if result.returncode != 0:
        log.error("  quantize failed:\n%s", result.stderr)
        raise RuntimeError("Quantisation failed (see log)")

    if not quant_path.exists():
        raise FileNotFoundError(f"Quantised model not produced: {quant_path}")

    size_mb = quant_path.stat().st_size / (1024**2)
    log.info("  Quantised: %.1f MB (%s)", size_mb, quant_type)
    return quant_path


# ---------------------------------------------------------------------------
# Step 4 — Validate
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    can_load: bool = False
    test_prompt: str | None = None
    test_output: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_quantized(
    quant_path: Path,
    *,
    skip_load_test: bool = False,
) -> ValidationResult:
    """Run a tiny inference probe via ``llama-cpp-python`` if available.

    Validation is best-effort: if neither ``llama-cpp-python`` nor a CLI
    binary is available we still ship the model and record the failure
    in the manifest. This keeps the export pipeline runnable on hosts
    without the heavy inference deps installed.
    """
    log.info("STAGE 4: validating quantised model")
    result = ValidationResult()

    if skip_load_test:
        log.info("  skipping load test (--no-validate)")
        return result

    test_prompt = (
        "<start_of_turn>user\n"
        "What is the standard VAT rate in Uganda?<end_of_turn>\n"
        "<start_of_turn>model\n"
    )
    result.test_prompt = test_prompt.strip()

    try:
        from llama_cpp import Llama  # type: ignore

        log.info("  test load via llama-cpp-python (n_ctx=512)...")
        llm = Llama(
            model_path=str(quant_path),
            n_ctx=512,
            n_gpu_layers=0,
            verbose=False,
        )
        out = llm(test_prompt, max_tokens=32, temperature=0.1, stop=["<end_of_turn>"])
        text = out["choices"][0]["text"].strip() if out.get("choices") else ""
        result.can_load = True
        result.test_output = text[:200]
        log.info("  ✓ model loaded; test output: %s", text[:60])
        del llm
    except ImportError:
        log.info("  llama-cpp-python not installed — skipping load test")
        result.error = "llama-cpp-python not installed"
    except Exception as exc:
        log.warning("  load test failed: %s", exc)
        result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# Step 5 — Manifest + model card
# ---------------------------------------------------------------------------


@dataclass
class ExportManifest:
    pipeline_version: str
    schema_version: str
    created_at_utc: str
    git: dict[str, Any]
    model_name: str
    base_model: str
    adapter_path: str
    export_format: str
    quantization: str
    file_name: str
    file_path: str
    size_bytes: int
    size_mb: float
    sha256: str
    validation: dict[str, Any]
    lineage: dict[str, Any]  # from training_config.json
    deployment: dict[str, Any]
    runtime: dict[str, Any]


def build_manifest(
    quant_path: Path,
    output_path: Path,
    adapter_path: Path,
    quant_type: str,
    base_model: str,
    lineage: dict[str, Any],
    validation: ValidationResult,
) -> ExportManifest:
    log.info("STAGE 5: building manifest + model card")

    sha = file_sha256(quant_path)
    size_bytes = quant_path.stat().st_size
    size_mb = size_bytes / (1024**2)

    manifest = ExportManifest(
        pipeline_version=PIPELINE_VERSION,
        schema_version=SCHEMA_VERSION,
        created_at_utc=datetime.datetime.now(datetime.UTC).isoformat(),
        git=_git_head(),
        model_name="ura-gemma-2b",
        base_model=base_model,
        adapter_path=str(adapter_path),
        export_format="gguf",
        quantization=quant_type,
        file_name=quant_path.name,
        file_path=str(quant_path),
        size_bytes=size_bytes,
        size_mb=round(size_mb, 1),
        sha256=sha,
        validation=validation.as_dict(),
        lineage=lineage,
        deployment={
            "android": {
                "inference_engine": "MediaPipe LLM Inference API",
                "min_sdk": 24,
                "asset_path": "models/ura-gemma-2b-q4_k_m.gguf",
                "no_compress": True,
            },
            "ios": {
                "inference_engine": "MediaPipeTasksGenAI",
                "min_version": "16.0",
                "bundle_resource": "ura-gemma-2b-q4_k_m",
            },
            "flutter": {
                "channel": "com.ura_chatbot/llm_inference",
                "config_class": "OnDeviceLlmConfig",
            },
        },
        runtime={
            "min_ram_mb": int(round(size_mb * 1.4)),  # ~40% mmap + KV cache headroom
            "context_length": 1024,
            "recommended_devices": "Android 12+ (≥6 GB RAM) / iPhone 12+ (iOS 16+)",
        },
    )

    manifest_path = output_path / "mobile_manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")
    log.info("  manifest: %s", manifest_path)
    return manifest


MODEL_CARD_TEMPLATE = """\
# URA Tax Assistant — Mobile Model Card

*Auto-generated by `ml/scripts/export_mobile.py` — do not edit by hand.*

- **Pipeline version:** `{pipeline_version}`
- **Schema version:** `{schema_version}`
- **Generated at:** `{created_at_utc}`
- **Git commit:** `{git_commit}` (dirty: `{git_dirty}`) on `{git_branch}`

## Model

| Field | Value |
|-------|-------|
| Name | `ura-gemma-2b` |
| Base | `{base_model}` |
| Format | GGUF |
| Quantization | `{quantization}` |
| File | `{file_name}` |
| Size | {size_mb} MB |
| SHA-256 | `{sha256}` |

## Training lineage

- **Adapter:** `{adapter_path}`
- **Training data:** `{train_path}` (sha256 `{train_sha256}`)
- **Manifest:** `{manifest_sha256}`
- **Pipeline:** `{lineage_pipeline_version}` / schema `{lineage_schema_version}`
- **Git commit (training):** `{lineage_git_commit}`
- **LoRA:** r={lora_r}, α={lora_alpha}, dropout={lora_dropout}, RSLoRA={use_rslora}, DoRA={use_dora}
- **Effective batch:** {effective_batch_size}, lr={learning_rate}, epochs={num_epochs}, seed={seed}

## Validation

- **Test prompt:** `{test_prompt}`
- **Loaded:** {can_load}
- **Test output:** `{test_output}`

## Deployment

### Android (MediaPipe LLM Inference API)

```yaml
asset_path: assets/models/{file_name}
inference_engine: com.google.mediapipe:tasks-genai:0.10.22
min_sdk: 24
build.gradle:
  androidResources:
    noCompress += listOf("gguf")  # required for mmap loading
```

### iOS (MediaPipeTasksGenAI)

```yaml
bundle_resource: ura-gemma-2b-q4_k_m
pod: 'MediaPipeTasksGenAI', '~> 0.10.22'
min_version: '16.0'
```

The GGUF file is staged at `MobileApp/ura_chatbot/ios/Runner/models/`.
**Manual step:** open `Runner.xcworkspace`, drag the file into the
project navigator, and ensure "Copy items if needed" + "Add to target:
Runner" are checked.

### Flutter

```dart
final llm = OnDeviceLlm(config: const OnDeviceLlmConfig(
  modelPath: 'models/{file_name}',
  contextLength: 1024,
));
await llm.initialize();
final result = await llm.generate('What is VAT?');
```

## Runtime requirements

- **Min RAM:** {min_ram_mb} MB
- **Context length:** 1024 tokens
- **Devices:** Android 12+ (≥6 GB RAM) / iPhone 12+ (iOS 16+)

## Reproducibility

```bash
git checkout {git_commit}
python ml/scripts/export_mobile.py \\
    --adapter {adapter_path} \\
    --quant {quantization}
```
"""


def write_model_card(manifest: ExportManifest, out_path: Path) -> Path:
    lineage = manifest.lineage or {}
    dataset_md = lineage.get("dataset_metadata") or {}
    lora_md = lineage.get("lora_config") or {}
    rendered = MODEL_CARD_TEMPLATE.format(
        pipeline_version=manifest.pipeline_version,
        schema_version=manifest.schema_version,
        created_at_utc=manifest.created_at_utc,
        git_commit=(manifest.git or {}).get("commit") or "unknown",
        git_dirty=(manifest.git or {}).get("dirty"),
        git_branch=(manifest.git or {}).get("branch") or "unknown",
        base_model=manifest.base_model,
        quantization=manifest.quantization,
        file_name=manifest.file_name,
        size_mb=manifest.size_mb,
        sha256=manifest.sha256,
        adapter_path=manifest.adapter_path,
        train_path=dataset_md.get("train_path", "unknown"),
        train_sha256=dataset_md.get("train_sha256", "unknown"),
        manifest_sha256=dataset_md.get("manifest_sha256", "unknown"),
        lineage_pipeline_version=dataset_md.get("pipeline_version", "unknown"),
        lineage_schema_version=dataset_md.get("schema_version", "unknown"),
        lineage_git_commit=dataset_md.get("git_commit", "unknown"),
        lora_r=lora_md.get("r", "?"),
        lora_alpha=lora_md.get("lora_alpha", "?"),
        lora_dropout=lora_md.get("lora_dropout", "?"),
        use_rslora=lora_md.get("use_rslora", False),
        use_dora=lora_md.get("use_dora", False),
        effective_batch_size=lineage.get("effective_batch_size", "?"),
        learning_rate=lineage.get("learning_rate", "?"),
        num_epochs=lineage.get("num_epochs", "?"),
        seed=lineage.get("seed", "?"),
        test_prompt=manifest.validation.get("test_prompt") or "(skipped)",
        can_load=manifest.validation.get("can_load"),
        test_output=manifest.validation.get("test_output") or "(skipped)",
        min_ram_mb=manifest.runtime.get("min_ram_mb"),
    )
    out_path.write_text(rendered, encoding="utf-8")
    log.info("  model card: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Step 6 — Deploy to MobileApp
# ---------------------------------------------------------------------------


def _rel_to_project(path: Path) -> str:
    """Return ``path`` relative to ``PROJECT_ROOT`` if possible, else absolute.

    Used in the manifest so deployment paths render as
    ``MobileApp/ura_chatbot/...`` for the canonical case while still
    handling test fixtures or out-of-tree deployments without crashing.
    """
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _atomic_copy(src: Path, dst: Path) -> str:
    """Copy ``src`` → ``dst`` atomically (write to ``.tmp`` then rename),
    verify post-copy SHA-256, and return the digest."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    src_sha = file_sha256(src)
    dst_sha = file_sha256(tmp)
    if src_sha != dst_sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"copy integrity failure: {src} → {dst} (sha mismatch)")
    tmp.replace(dst)
    log.info("  → %s (%.1f MB) sha256=%s", dst, dst.stat().st_size / (1024**2), dst_sha[:12])
    return dst_sha


def deploy_to_mobile_app(
    quant_path: Path,
    manifest: ExportManifest,
    *,
    deploy_android: bool = True,
    deploy_ios: bool = True,
) -> dict[str, Any]:
    """Atomically copy the GGUF into the MobileApp asset directories.

    Uses the canonical filename ``ura-gemma-2b-q4_k_m.gguf`` regardless of
    the source quantisation, because the Flutter ``OnDeviceLlmConfig``
    hardcodes that path. (Override with the ``--mobile-filename`` flag.)

    Returns a dict describing what was copied where, with post-copy
    digests, for inclusion in the manifest.
    """
    log.info("STAGE 6: deploying to MobileApp")
    deployed: dict[str, Any] = {"android": None, "ios": None}

    target_name = manifest.file_name
    # The Flutter app expects ``ura-gemma-2b-q4_k_m.gguf`` regardless of
    # the actual quant type — keep the canonical name to avoid touching
    # Dart code on every quant change.
    canonical = DEFAULT_MOBILE_FILENAME

    if deploy_android:
        if not ANDROID_ASSETS.parent.parent.exists():
            log.warning(
                "  Android assets parent (%s) does not exist; skipping Android deploy",
                ANDROID_ASSETS.parent.parent,
            )
        else:
            dst = ANDROID_ASSETS / canonical
            sha = _atomic_copy(quant_path, dst)
            deployed["android"] = {
                "path": _rel_to_project(dst),
                "sha256": sha,
                "filename": canonical,
                "source_filename": target_name,
            }
            # Quick sanity-check that build.gradle.kts has noCompress for gguf.
            gradle = MOBILE_ROOT / "android" / "app" / "build.gradle.kts"
            if gradle.exists():
                content = gradle.read_text()
                if "noCompress" not in content or "gguf" not in content:
                    log.warning(
                        "  ⚠ %s does not appear to declare noCompress for gguf — "
                        "the GGUF file will be APK-compressed and mmap will fail "
                        'at runtime. Add: androidResources { noCompress += listOf("gguf") }',
                        gradle,
                    )

    if deploy_ios:
        if not IOS_STAGING.parent.exists():
            log.warning(
                "  iOS Runner directory (%s) does not exist; skipping iOS deploy",
                IOS_STAGING.parent,
            )
        else:
            dst = IOS_STAGING / canonical
            sha = _atomic_copy(quant_path, dst)
            deployed["ios"] = {
                "path": _rel_to_project(dst),
                "sha256": sha,
                "filename": canonical,
                "source_filename": target_name,
                "manual_step": (
                    "Open Runner.xcworkspace, drag the GGUF file into the "
                    "project navigator, ensure 'Copy items if needed' is "
                    "OFF (we already wrote it in place) and 'Add to "
                    "target: Runner' is ON. Do this once per file rename."
                ),
            }

    return deployed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export fine-tuned Gemma-2B for on-device mobile inference (2026)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quantization types:
"""
        + "\n".join(f"  {k:8s} — {v}" for k, v in QUANT_TYPES.items())
        + """

Examples:
  %(prog)s                                  # auto-discover, deploy to MobileApp
  %(prog)s --adapter path/to/final
  %(prog)s --quant IQ4_NL --imatrix
  %(prog)s --quant Q5_K_M --no-deploy
  %(prog)s --dry-run
""",
    )

    parser.add_argument(
        "--adapter",
        type=Path,
        default=None,
        help="Path to fine-tuned LoRA adapter dir (auto-discovered if omitted)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR.relative_to(PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--quant",
        type=str,
        default=DEFAULT_QUANT,
        choices=list(QUANT_TYPES.keys()),
        help=f"Quantization (default: {DEFAULT_QUANT})",
    )

    parser.add_argument(
        "--imatrix",
        action="store_true",
        help="Compute imatrix calibration before quantising "
        "(strongly recommended for IQ-series sub-3-bit quants)",
    )
    parser.add_argument(
        "--imatrix-source",
        type=Path,
        default=None,
        help="JSONL file for imatrix calibration "
        "(default: artifacts/training_data/train.messages.jsonl)",
    )
    parser.add_argument(
        "--imatrix-samples",
        type=int,
        default=256,
        help="Number of calibration prompts (default: 256)",
    )

    parser.add_argument(
        "--no-validate", action="store_true", help="Skip the llama-cpp-python load test"
    )
    parser.add_argument("--no-deploy", action="store_true", help="Skip copying to MobileApp/")
    parser.add_argument(
        "--no-android", action="store_true", help="Skip Android asset copy (still does iOS)"
    )
    parser.add_argument(
        "--no-ios", action="store_true", help="Skip iOS staging copy (still does Android)"
    )

    parser.add_argument(
        "--keep-merged", action="store_true", help="Keep the intermediate merged FP16 model (~5 GB)"
    )
    parser.add_argument(
        "--keep-gguf-f16", action="store_true", help="Keep the intermediate GGUF F16 file"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate adapter + tools without converting"
    )

    args = parser.parse_args()

    print("=" * 70)
    print("URA TAX ASSISTANT — MOBILE EXPORT PIPELINE (2026)")
    print("=" * 70)

    # Resolve adapter
    if args.adapter is None:
        args.adapter = find_latest_adapter()
        if args.adapter is None:
            log.error(
                "No fine-tune adapter found under artifacts/. Run "
                "fine_tune_gemma.py first or pass --adapter <path>."
            )
            sys.exit(2)
        log.info("Auto-discovered adapter: %s", args.adapter)

    if not args.adapter.exists():
        log.error("Adapter path not found: %s", args.adapter)
        sys.exit(2)

    print(f"  Adapter:      {args.adapter}")
    print(f"  Output:       {args.output}")
    print(f"  Quantization: {args.quant} — {QUANT_TYPES[args.quant]}")
    print(
        f"  Deploy:       Android={not args.no_android and not args.no_deploy} "
        f"iOS={not args.no_ios and not args.no_deploy}"
    )
    print()

    # Tool discovery
    tools = discover_llama_cpp()

    # Read training lineage from the adapter dir
    lineage = read_adapter_lineage(args.adapter)
    if lineage:
        log.info(
            "Lineage: pipeline=%s git=%s",
            (lineage.get("dataset_metadata") or {}).get("pipeline_version", "?"),
            (lineage.get("dataset_metadata") or {}).get("git_commit", "?")[:12],
        )

    if args.dry_run:
        log.info("DRY RUN — checking adapter + tools only")
        has_config = (args.adapter / "config.json").exists()
        has_adapter = (args.adapter / "adapter_config.json").exists()
        has_safetensors = list(args.adapter.glob("*.safetensors"))
        log.info("  config.json:         %s", "found" if has_config else "missing")
        log.info("  adapter_config.json: %s", "found" if has_adapter else "missing (full model)")
        log.info("  weight files:        %d", len(has_safetensors))
        log.info("  llama.cpp convert:   %s", bool(tools.has_convert))
        log.info("  llama-quantize:      %s", bool(tools.has_quantize))
        log.info("  llama-imatrix:       %s", bool(tools.has_imatrix))
        log.info(
            "  Android assets dir:  %s (exists=%s)",
            ANDROID_ASSETS,
            ANDROID_ASSETS.parent.parent.exists(),
        )
        log.info("  iOS staging dir:     %s (exists=%s)", IOS_STAGING, IOS_STAGING.parent.exists())
        if args.quant in QUANTS_REQUIRING_IMATRIX and not args.imatrix:
            log.warning(
                "  ⚠ %s strongly recommends --imatrix; quality will be poor without it",
                args.quant,
            )
        if not has_config and not has_adapter:
            log.error("  No valid HF model found at %s", args.adapter)
            sys.exit(3)
        if not tools.has_convert or not tools.has_quantize:
            log.warning(
                "  Some llama.cpp tools missing — full export will fail. "
                "Install via: git clone https://github.com/ggerganov/llama.cpp "
                "&& cmake -B build && cmake --build build --target llama-quantize llama-imatrix"
            )
        log.info("Dry run complete.")
        return

    args.output.mkdir(parents=True, exist_ok=True)

    # 1. Merge
    merged_path, base_model = merge_lora_adapter(args.adapter, args.output)

    # 2. Convert to GGUF F16
    gguf_f16 = convert_to_gguf(merged_path, args.output, tools)

    # 2b. (optional) imatrix
    imatrix_path = None
    if args.imatrix:
        imatrix_path = compute_imatrix(
            gguf_f16,
            args.output,
            tools,
            calibration_jsonl=args.imatrix_source,
            sample_size=args.imatrix_samples,
        )

    # 3. Quantise
    quant_path = quantize_gguf(gguf_f16, args.output, tools, args.quant, imatrix_path=imatrix_path)

    # 4. Validate
    validation = validate_quantized(quant_path, skip_load_test=args.no_validate)

    # 5. Manifest + model card
    manifest = build_manifest(
        quant_path,
        args.output,
        args.adapter,
        args.quant,
        base_model=base_model,
        lineage=lineage,
        validation=validation,
    )
    write_model_card(manifest, args.output / "MODEL_CARD.md")

    # 6. Deploy to MobileApp (atomic + verified)
    deployment_record: dict[str, Any] = {}
    if not args.no_deploy:
        deployment_record = deploy_to_mobile_app(
            quant_path,
            manifest,
            deploy_android=not args.no_android,
            deploy_ios=not args.no_ios,
        )
        # Update manifest with the actual deployed paths + sha256s
        existing = json.loads((args.output / "mobile_manifest.json").read_text())
        existing["deployed"] = deployment_record
        (args.output / "mobile_manifest.json").write_text(
            json.dumps(existing, indent=2) + "\n", encoding="utf-8"
        )

    # Cleanup intermediates
    if not args.keep_merged and merged_path.exists():
        log.info("Cleanup: removing merged model dir (%s)", merged_path)
        shutil.rmtree(merged_path, ignore_errors=True)
    if not args.keep_gguf_f16 and gguf_f16 != quant_path and gguf_f16.exists():
        log.info("Cleanup: removing GGUF F16 (%s)", gguf_f16)
        gguf_f16.unlink(missing_ok=True)

    # Summary
    print()
    print("=" * 70)
    print("✓ EXPORT COMPLETE")
    print("=" * 70)
    print(f"  Quantised model: {quant_path}")
    print(f"  Size:            {manifest.size_mb} MB")
    print(f"  Quantisation:    {args.quant}")
    print(f"  SHA-256:         {manifest.sha256[:16]}...")
    print(f"  Validated:       {validation.can_load}")
    print(f"  Min RAM:         {manifest.runtime['min_ram_mb']} MB")
    if deployment_record:
        for platform_name, info in deployment_record.items():
            if info:
                print(f"  Deployed → {platform_name}: {info['path']}")
    print()
    print(f"  Model card:      {args.output / 'MODEL_CARD.md'}")
    print(f"  Manifest:        {args.output / 'mobile_manifest.json'}")
    print()
    if deployment_record.get("ios"):
        print("⚠ iOS one-time setup: open Runner.xcworkspace, drag the GGUF file")
        print("  from MobileApp/ura_chatbot/ios/Runner/models/ into the project")
        print("  navigator (target: Runner). Subsequent re-exports replace the")
        print("  file in place — no Xcode action needed.")
        print()


if __name__ == "__main__":
    main()
