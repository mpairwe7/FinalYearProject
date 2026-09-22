"""Simulated AI phone receptionist package (Pipecat demo)."""

from __future__ import annotations

import importlib.util


def is_available() -> bool:
    """Return True if Pipecat runtime dependencies are installed."""
    return importlib.util.find_spec("pipecat") is not None
