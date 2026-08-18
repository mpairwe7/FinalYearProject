"""Isolated document parse worker (G13).

Default: in-process extractors (same as before). When
``DOCUMENT_PARSE_ISOLATED=true``, extractors run in a spawned subprocess
so a hostile PDF cannot share the API worker's address space. Still not
a dedicated pool or gVisor sandbox.
"""

from __future__ import annotations

import logging
import os
import multiprocessing as mp
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT_S = int(os.getenv("DOCUMENT_PARSE_TIMEOUT_S", "30"))


def isolated_enabled() -> bool:
    return (os.getenv("DOCUMENT_PARSE_ISOLATED") or "").lower() in ("1", "true", "yes", "on")


def _child(kind: str, data: bytes, conn: Any) -> None:
    try:
        from .documents import _EXTRACTORS

        extraction = _EXTRACTORS[kind](data)
        conn.send(("ok", extraction))
    except Exception as exc:  # noqa: BLE001
        conn.send(("err", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def try_isolated(kind: str, data: bytes) -> Any | None:
    """Return an Extraction from a child process, or None if isolation is off."""
    if not isolated_enabled():
        return None
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(kind, data, child))
    proc.start()
    child.close()
    proc.join(timeout=_TIMEOUT_S)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        raise TimeoutError("isolated document parse timed out")
    if not parent.poll():
        raise RuntimeError("isolated document parse produced no result")
    status, payload = parent.recv()
    if status != "ok":
        raise RuntimeError(f"isolated document parse failed: {payload}")
    return payload
