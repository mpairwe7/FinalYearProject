"""Run certified metrics, read-only and bounded (ADR-0033, phase 3)."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from .catalogue import Metric, MetricError, certified
from .guard import check

logger = logging.getLogger(__name__)

MAX_ROWS = 500
TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 5
_NAMED_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


class Executor(Protocol):
    def run(self, sql: str, params: dict[str, Any], *, max_rows: int, timeout_s: float) -> tuple[list[str], list[tuple]]: ...


#: String literals and double-quoted identifiers: neither holds a parameter.
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def to_pyformat(sql: str) -> str:
    """Named ``:params`` to psycopg's ``%(name)s``, leaving string literals alone.

    Every ``%`` is doubled first, so a literal such as ``'VAT%'`` is not read as a
    placeholder, and a ``:word`` inside a literal (``'urn:sector'``) is not a parameter.
    """
    out, last = [], 0
    for literal in _LITERAL_RE.finditer(sql):
        out.append(_NAMED_RE.sub(r"%(\1)s", sql[last:literal.start()].replace("%", "%%")))
        out.append(literal.group(0).replace("%", "%%"))
        last = literal.end()
    out.append(_NAMED_RE.sub(r"%(\1)s", sql[last:].replace("%", "%%")))
    return "".join(out)


def _jsonable(value: Any) -> Any:
    """A database value as JSON can carry it (NUMERIC arrives as Decimal, DATE as date)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, _dt.date | _dt.datetime):
        return value.isoformat()
    return value


def _capped(sql: str, max_rows: int) -> str:
    # max_rows is an int clamped by the caller; nothing else is formatted in.
    # A certified template that passed the guard, and an int: no caller text is formatted in.
    return f"SELECT * FROM ({sql.strip().removesuffix(';')}) AS certified_metric LIMIT {int(max_rows) + 1}"  # noqa: S608


class SqliteExecutor:
    """Development and tests: a read-only SQLite file (``mode=ro``)."""

    def __init__(self, path: str) -> None:
        self._path = path

    def run(self, sql: str, params: dict[str, Any], *, max_rows: int, timeout_s: float) -> tuple[list[str], list[tuple]]:
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        deadline = time.monotonic() + timeout_s
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            cur = conn.execute(_capped(sql, max_rows), params)
            columns = [c[0] for c in cur.description or ()]
            return columns, cur.fetchmany(max_rows + 1)
        finally:
            conn.close()


class PostgresExecutor:
    """Production: a read-only transaction with a statement timeout, credentials from Vault."""

    def __init__(self, connect: Any) -> None:
        self._connect = connect

    def run(self, sql: str, params: dict[str, Any], *, max_rows: int, timeout_s: float) -> tuple[list[str], list[tuple]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
            cur.execute(to_pyformat(_capped(sql, max_rows)), params)
            columns = [c[0] for c in cur.description or ()]
            rows = cur.fetchmany(max_rows + 1)
            conn.rollback()
            return columns, rows
        finally:
            conn.close()


def vault_secret(path: str) -> dict[str, Any]:
    """A KV v2 secret from Vault (``VAULT_ADDR``, ``VAULT_TOKEN`` or ``VAULT_TOKEN_FILE``)."""
    import httpx

    addr = os.environ["VAULT_ADDR"].rstrip("/")
    token = os.getenv("VAULT_TOKEN") or Path(os.environ["VAULT_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    response = httpx.get(f"{addr}/v1/{path.lstrip('/')}", headers={"X-Vault-Token": token}, timeout=5.0)
    response.raise_for_status()
    return dict(response.json()["data"]["data"])


@dataclass
class SemanticLayer:
    executor: Executor
    metrics: dict[str, Metric]
    max_rows: int = MAX_ROWS
    timeout_s: float = TIMEOUT_S

    def catalogue(self) -> list[dict[str, Any]]:
        return [m.describe() for m in sorted(self.metrics.values(), key=lambda m: m.id)]

    def query(self, metric_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        metric = self.metrics.get(metric_id)
        if metric is None or not metric.certified:
            raise MetricError(f"{metric_id!r} is not a certified metric")
        bound = metric.bind(parameters or {})
        check(metric.sql, sources=metric.sources)  # re-checked at run time, not trusted from load
        started = time.monotonic()
        columns, rows = self.executor.run(metric.sql, bound, max_rows=self.max_rows, timeout_s=self.timeout_s)
        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        if "group_size" not in columns:
            raise MetricError(f"{metric_id} returned no group_size column")
        at = columns.index("group_size")
        kept = [{c: _jsonable(v) for c, v in zip(columns, r, strict=True)} for r in rows
                if int(r[at] or 0) >= metric.min_group_size]
        logger.info("semantic metric %s: %d rows, %d suppressed", metric_id, len(kept), len(rows) - len(kept))
        return {"ok": True, "metric": metric_id, "columns": columns, "rows": kept, "suppressed_rows": len(rows) - len(kept),
                "truncated": truncated, "min_group_size": metric.min_group_size,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1), "certified_by": metric.certified_by,
                "certified_at": metric.certified_at}


_LAYER: SemanticLayer | None = None


def set_layer(layer: SemanticLayer | None) -> None:
    """Install a layer (tests, or a deployment's own wiring); ``None`` resets."""
    global _LAYER
    _LAYER = layer


def get_layer() -> SemanticLayer | None:
    """The configured layer, or ``None`` when nothing is connected."""
    global _LAYER
    if _LAYER is not None:
        return _LAYER
    dsn = os.getenv("SEMANTIC_LAYER_DSN", "")
    vault_path = os.getenv("SEMANTIC_LAYER_VAULT_PATH", "")
    if dsn.startswith("sqlite:///"):
        executor: Executor = SqliteExecutor(dsn.removeprefix("sqlite:///"))
    elif vault_path:
        def connect() -> Any:
            import psycopg  # optional: only a connected deployment needs it

            secret = vault_secret(vault_path)
            # A stalled connect must not hold a worker: bounded, and well inside the tool's time.
            return psycopg.connect(secret["dsn"], connect_timeout=CONNECT_TIMEOUT_S)

        executor = PostgresExecutor(connect)
    else:
        return None
    _LAYER = SemanticLayer(executor, certified())
    return _LAYER
