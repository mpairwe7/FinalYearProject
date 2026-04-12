"""Reproducibility + environment capture for the 2026 ML pipeline.

The fine-tune script (``fine_tune_gemma.py``) already has deep deterministic
seed handling for torch/cuDNN. This module provides the *lighter-weight*
counterpart used by the peripheral pipelines (safety eval, calibration,
tokenizer audit, benchmarking, model-card generation) that do not load
torch but still need:

- a single callable that seeds ``random`` / ``numpy`` / ``PYTHONHASHSEED``
- an ``env_snapshot()`` that captures enough context to reproduce a run
  *after the fact* — git SHA, git dirty flag, python, OS, hostname, and
  the versions of every library that materially affects numerical
  outputs.

The snapshot is written to a JSON file (default
``artifacts/env_snapshots/<script>-<ts>.json``) and also returned so
callers can embed it in their own output artefacts (model card,
benchmark report, safety eval report).

Usage:

    from ml.scripts.repro import set_global_seed, env_snapshot, write_snapshot

    set_global_seed(42)
    snap = env_snapshot(script="evaluate_safety")
    write_snapshot(snap, Path("artifacts/env_snapshots"))
"""

from __future__ import annotations

import datetime as _dt
import importlib.metadata
import json
import logging
import os
import platform
import random
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Libraries whose version we record if present. We *do not* import them;
# we just look up their installed metadata so the snapshot works even in
# minimal environments (e.g. the CI job that only runs the safety eval).
_TRACKED_PACKAGES: tuple[str, ...] = (
    "torch",
    "transformers",
    "peft",
    "trl",
    "accelerate",
    "bitsandbytes",
    "datasets",
    "sentence-transformers",
    "scikit-learn",
    "numpy",
    "pandas",
    "pyarrow",
    "datasketch",
    "gguf",
    "llama-cpp-python",
    "huggingface-hub",
    "safetensors",
    "pydantic",
)


def set_global_seed(seed: int = 42) -> dict[str, Any]:
    """Seed Python + numpy + ``PYTHONHASHSEED`` deterministically.

    This is the *light* seed helper — it does **not** touch torch/cuDNN.
    ``fine_tune_gemma.set_deterministic`` remains the authoritative path
    for training runs. Peripheral scripts (safety eval, benchmarking,
    tokenizer audit) should call this instead to avoid a torch import.
    """
    flags: dict[str, Any] = {"seed": seed}

    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    flags["PYTHONHASHSEED"] = os.environ["PYTHONHASHSEED"]

    random.seed(seed)
    flags["python_random"] = True

    try:
        import numpy as np  # type: ignore

        np.random.seed(seed)
        flags["numpy_random"] = True
    except ImportError:
        flags["numpy_random"] = False

    return flags


def _git(*args: str) -> str | None:
    """Run ``git <args>`` and return stdout, or ``None`` on failure."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return None


def _pkg_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def env_snapshot(script: str = "unknown") -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of the current run environment.

    Fields:

      * ``script`` — caller name (free-form; used as default filename prefix)
      * ``timestamp_utc`` — ISO-8601 UTC timestamp
      * ``git`` — commit SHA, branch, dirty flag, last-commit timestamp
      * ``platform`` — python + OS + hostname + arch
      * ``packages`` — installed versions of tracked libraries
      * ``cuda`` — best-effort CUDA availability + device names (only if
        torch is already importable; we never trigger a fresh import)
    """
    now = _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")

    git = {
        "sha": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "last_commit_iso": _git("log", "-1", "--format=%cI"),
    }

    plat = {
        "python": sys.version.split()[0],
        "python_impl": platform.python_implementation(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
    }

    packages = {name: _pkg_version(name) for name in _TRACKED_PACKAGES}

    cuda: dict[str, Any] = {"available": False}
    # Only probe torch if it's already in sys.modules — never force an import.
    if "torch" in sys.modules:
        try:  # pragma: no cover - torch-dependent branch
            import torch  # type: ignore

            cuda = {
                "available": bool(torch.cuda.is_available()),
                "version": getattr(torch.version, "cuda", None),
                "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
                "devices": (
                    [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
                    if torch.cuda.is_available()
                    else []
                ),
            }
        except Exception as exc:  # pragma: no cover
            cuda = {"available": False, "error": str(exc)}

    return {
        "script": script,
        "timestamp_utc": now,
        "git": git,
        "platform": plat,
        "packages": packages,
        "cuda": cuda,
    }


def write_snapshot(
    snapshot: dict[str, Any],
    output_dir: Path | str = PROJECT_ROOT / "artifacts" / "env_snapshots",
) -> Path:
    """Persist a snapshot to ``<output_dir>/<script>-<timestamp>.json``."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = snapshot.get("timestamp_utc", "").replace(":", "-")
    name = f"{snapshot.get('script', 'run')}-{ts}.json"
    path = output_dir / name
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=False))
    return path


def main() -> None:  # small CLI so the snapshot is runnable standalone
    import argparse

    parser = argparse.ArgumentParser(description="Emit an environment snapshot")
    parser.add_argument("--script", default="manual", help="Caller label")
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "artifacts" / "env_snapshots"),
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--print", action="store_true", help="Echo JSON to stdout")
    args = parser.parse_args()

    if args.seed is not None:
        set_global_seed(args.seed)

    snap = env_snapshot(script=args.script)
    path = write_snapshot(snap, args.output_dir)
    print(f"[repro] snapshot: {path}")
    if args.print:
        print(json.dumps(snap, indent=2))


if __name__ == "__main__":
    main()
