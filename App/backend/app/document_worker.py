"""Isolated document parse worker (G13).

Default: in-process extractors (same as before). When
``DOCUMENT_PARSE_ISOLATED=true``, extractors run in a spawned subprocess
so a hostile PDF cannot share the API worker's address space. Still not
a dedicated pool or gVisor sandbox.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import os
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT_S = int(os.getenv("DOCUMENT_PARSE_TIMEOUT_S", "30"))
_MAX_CONCURRENT_PARSES = max(1, int(os.getenv("DOCUMENT_PARSE_MAX_CONCURRENT", "4")))
_MAX_CHILD_FSIZE_BYTES = int(os.getenv("DOCUMENT_PARSE_MAX_FSIZE_BYTES", str(64 * 1024 * 1024)))
_MAX_CHILD_MEM_BYTES = int(os.getenv("DOCUMENT_PARSE_MAX_MEM_BYTES", "0"))
_PARSE_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT_PARSES)

_SENSITIVE_ENV_RE = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|DSN|DATABASE_URL|CREDENTIAL|AUTH)",
    re.IGNORECASE,
)


def isolated_enabled() -> bool:
    return (os.getenv("DOCUMENT_PARSE_ISOLATED") or "").lower() in ("1", "true", "yes", "on")


def _apply_child_sandbox_limits(*, apply_rlimits: bool | None = None) -> None:
    """Scrub inherited secrets, bound thread arenas, and enforce POSIX RLIMIT caps inside the child worker (CS-2).

    ``resource.setrlimit`` is only applied when running inside a spawned child
    process (``mp.current_process().name != "MainProcess"``) unless
    *apply_rlimits* is explicitly ``True``, preventing an in-process unit test
    call from lowering ``RLIMIT_CPU`` or ``RLIMIT_FSIZE`` on the host/pytest
    runner process.
    """
    for key in list(os.environ.keys()):
        if _SENSITIVE_ENV_RE.search(key) and key != "DOCUMENT_PARSE_ISOLATED":
            os.environ.pop(key, None)
    # Prevent OpenBLAS / OpenMP / glibc from pre-allocating multi-GB virtual arenas in short-lived parse workers
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MALLOC_ARENA_MAX"] = "2"
    should_set_rlimits = (
        apply_rlimits
        if apply_rlimits is not None
        else (mp.current_process().name != "MainProcess")
    )
    if not should_set_rlimits:
        return
    try:
        import resource

        cpu_soft = max(5, _TIMEOUT_S)
        for limit_name, bounds in (
            ("RLIMIT_CORE", (0, 0)),
            ("RLIMIT_FSIZE", (_MAX_CHILD_FSIZE_BYTES, _MAX_CHILD_FSIZE_BYTES)),
            ("RLIMIT_CPU", (cpu_soft, cpu_soft + 5)),
        ):
            rlimit = getattr(resource, limit_name, None)
            if rlimit is not None:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(rlimit, bounds)
        if _MAX_CHILD_MEM_BYTES > 0 and hasattr(resource, "RLIMIT_AS"):
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(resource.RLIMIT_AS, (_MAX_CHILD_MEM_BYTES, _MAX_CHILD_MEM_BYTES))
    except ImportError:
        pass


def _child(kind: str, data: bytes, conn: Any) -> None:
    try:
        _apply_child_sandbox_limits(apply_rlimits=True)
        if kind == "sandbox_pdf":
            from .ephemeral_pdf_sandbox import _extract_pdf_pages

            extraction = _extract_pdf_pages(data)
        elif kind == "sandbox_xlsx":
            from .ephemeral_pdf_sandbox import _extract_xlsx_pages

            extraction = _extract_xlsx_pages(data)
        else:
            from .documents import _EXTRACTORS, UnsupportedDocumentError
            from .pdf_guards import QUERY_PDF_LIMITS, PdfRejected, inspect_pdf_bytes

            if kind == "pdf":
                try:
                    inspection = inspect_pdf_bytes(data, QUERY_PDF_LIMITS)
                except PdfRejected as err:
                    raise UnsupportedDocumentError(str(err)) from err
                extraction = _EXTRACTORS[kind](data)
                for warning in inspection.warnings:
                    if warning not in extraction.warnings:
                        extraction.warnings.append(warning)
            else:
                extraction = _EXTRACTORS[kind](data)
        conn.send(("ok", extraction))
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in ("UnsupportedDocumentError", "PdfRejected"):
            conn.send(("unsupported", str(exc)))
        else:
            conn.send(("err", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def try_isolated(kind: str, data: bytes) -> Any | None:
    """Return an Extraction from a child process, or None if isolation is off.

    Drains the pipe via ``parent.poll(_TIMEOUT_S)`` + ``parent.recv()`` *before*
    ``proc.join()`` so payloads exceeding the 64 KiB OS pipe buffer cannot deadlock
    the child process waiting to flush ``conn.send()``.
    """
    if not isolated_enabled():
        return None
    if not _PARSE_SEMAPHORE.acquire(timeout=_TIMEOUT_S):
        raise TimeoutError("isolated document parse concurrency queue timed out")
    try:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_child, args=(kind, data, child))
        try:
            proc.start()
            child.close()
            if not parent.poll(_TIMEOUT_S):
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=2)
                    if proc.is_alive():
                        proc.kill()
                        proc.join(timeout=1)
                    raise TimeoutError("isolated document parse timed out")
                raise RuntimeError("isolated document parse produced no result")
            try:
                status, payload = parent.recv()
            except EOFError as err:
                proc.join(timeout=2)
                raise RuntimeError("isolated document parse produced no result") from err
            proc.join(timeout=2)
        finally:
            with contextlib.suppress(Exception):
                parent.close()
            if proc.is_alive():
                with contextlib.suppress(Exception):
                    proc.kill()
                    proc.join(timeout=1)
    finally:
        _PARSE_SEMAPHORE.release()
    if status == "unsupported":
        from .documents import UnsupportedDocumentError

        raise UnsupportedDocumentError(str(payload))
    if status != "ok":
        raise RuntimeError(f"isolated document parse failed: {payload}")
    return payload
