"""Resolve the project root directory safely for both local dev and Docker."""

import os
from pathlib import Path

_parents = Path(__file__).resolve().parents


def _resolve() -> Path:
    env = os.getenv("PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    # Local dev: file is at <project>/App/backend/app/_root.py → parents[3]
    return _parents[3] if len(_parents) > 3 else _parents[-1]


PROJECT_ROOT = _resolve()


def _resolve_app_data_root() -> Path:
    """Resolve the repository-local corpus location for FAQ retrieval.

    A source checkout has an ``App`` directory beneath the project root,
    whereas the container runtime places its application assets directly in
    ``/app``. This preserves a single local-data convention in both layouts.
    """
    configured = os.getenv("APP_DATA_ROOT")
    if configured:
        return Path(configured).resolve()
    if (PROJECT_ROOT / "App").is_dir():
        return (PROJECT_ROOT / "App" / "Data").resolve()
    return (PROJECT_ROOT / "Data").resolve()


APP_DATA_ROOT = _resolve_app_data_root()
