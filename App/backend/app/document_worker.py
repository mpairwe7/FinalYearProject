"""Isolated document parse worker (G13).

Default: in-process extractors (same as before). When
``DOCUMENT_PARSE_ISOLATED=true``, extractors run in a spawned subprocess
so a hostile PDF cannot share the API worker's address space. The child
sees only an allowlisted environment and is capped in CPU, file size and
address space (ADR-0031). Still not a dedicated pool or gVisor sandbox: it has
the parent's network and filesystem view.
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
#: Address-space cap per parse child (RLIMIT_AS). 1 GiB: every corpus PDF up to
#: 50 MB parsed identically under 512 MiB on 2026-11-07, and a 1.5 GiB flate bomb
#: was contained. Unset (0), one hostile upload could take the host's memory,
#: four at a time (ADR-0031, G479).
_DEFAULT_CHILD_MEM_BYTES = 1024 * 1024 * 1024


def parse_mem_cap(raw: str | None) -> int | None:
    """``DOCUMENT_PARSE_MAX_MEM_BYTES`` as the worker reads it: unset is the default,
    a whole number is that many bytes (zero or negative means no cap), anything
    else is ``None``. Production refuses no cap and ``None`` (CodeRabbit on #703)."""
    value = (raw or "").strip()
    if not value:
        return _DEFAULT_CHILD_MEM_BYTES
    try:
        return int(value)
    except ValueError:
        return None


_parsed_cap = parse_mem_cap(os.getenv("DOCUMENT_PARSE_MAX_MEM_BYTES"))
if _parsed_cap is None:
    logger.warning("DOCUMENT_PARSE_MAX_MEM_BYTES is not a whole number; using the 1 GiB default")
_MAX_CHILD_MEM_BYTES = _DEFAULT_CHILD_MEM_BYTES if _parsed_cap is None else _parsed_cap
_PARSE_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT_PARSES)

_SENSITIVE_ENV_RE = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PASS\b|PRIVATE|_KEY\b|KEY$|API_KEY|DSN|DATABASE_URL|CREDENTIAL|AUTH|SALT|HMAC|SIGNING|CERT)",
    re.IGNORECASE,
)
#: What a parse child may see of the environment: locale and runtime plumbing,
#: and the settings its extractors read. Everything else is removed, so a secret
#: or an internal service address no one thought to list stays in the parent
#: (CWE-526). A denylist kept AUDIT_TAXPAYER_HMAC_SALT, DEPLOY_KEY, webhook URLs
#: and the Qdrant/vLLM addresses (ADR-0031, G478).
_CHILD_ENV_ALLOW = re.compile(
    r"^(?:PATH|HOME|USER|LANG|LANGUAGE|LC_[A-Z]+|TZ|TMPDIR|TEMP|TMP|PWD|SHELL"
    r"|PYTHON[A-Z_]*|VIRTUAL_ENV|LD_LIBRARY_PATH|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE"
    r"|HTTP_PROXY|HTTPS_PROXY|NO_PROXY|http_proxy|https_proxy|no_proxy"
    r"|OMP_[A-Z_]+|OPENBLAS_[A-Z_]+|MKL_[A-Z_]+|MALLOC_[A-Z_]+|NUMEXPR_[A-Z_]+"
    r"|APP_ENV|FLAG_[A-Z0-9_]+"
    r"|DOCUMENT_[A-Z0-9_]+|OCR_[A-Z0-9_]+|VISION_[A-Z0-9_]+|DOCLING_[A-Z0-9_]+|TRITON_OCR_URL"
    r"|SANDBOX_[A-Z0-9_]+|MAX_SANDBOX_[A-Z0-9_]+|PDF_CORPUS_[A-Z0-9_]+|URA_WEB_SANDBOX_DIR"
    r"|HF_HOME|HF_HUB_OFFLINE|HF_HUB_CACHE|TRANSFORMERS_OFFLINE|TRANSFORMERS_CACHE|TORCH_HOME"
    r"|XDG_[A-Z_]+|TESSDATA_PREFIX)$"
)
#: Paths to CA bundles: certificates are public, and an OCR service behind a
#: private CA needs them. The ``CERT`` rule below is for keys and client certs.
_CHILD_ENV_PUBLIC = frozenset({"SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"})
#: A URL value carrying ``user:password@`` is a credential whatever its name.
_CREDENTIAL_URL_RE = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def child_environment(environ: dict[str, str] | os._Environ[str]) -> dict[str, str]:
    """The environment a parse child keeps: allowlisted names, no secret-shaped names, no credential URLs."""
    return {
        name: value
        for name, value in environ.items()
        if _CHILD_ENV_ALLOW.match(name)
        and (name in _CHILD_ENV_PUBLIC or not _SENSITIVE_ENV_RE.search(name))
        and not _CREDENTIAL_URL_RE.search(value or "")
    }


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
    kept = child_environment(os.environ)
    os.environ.clear()
    os.environ.update(kept)
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
