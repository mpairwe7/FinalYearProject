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
