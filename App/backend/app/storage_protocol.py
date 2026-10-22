"""Storage Backend Protocol & Dual-Database Parity Verifier (October 2026).

Enforces ISO/IEC 25010:2023 Modifiability & Replaceability between
``app.database`` (SQLite WAL default) and ``app.postgres`` (PostgreSQL
connection-pooled backend) so neither backend can drift in public functions or
call signatures.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackendProtocol(Protocol):
    """Core runtime storage contract shared by ``app.database`` and ``app.postgres``."""

    def init_db(self) -> None: ...
    def close_db(self) -> None: ...
    def close_pool(self) -> None: ...
    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...
    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None: ...
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int: ...
    def execute_script(self, sql: str) -> None: ...
    def create_task(
        self,
        kind: str,
        args: dict[str, Any] | None = None,
        *,
        tenant_id: str = "default",
        user_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]: ...
    def get_task(self, task_id: str, *, tenant_id: str = "default") -> dict[str, Any] | None: ...
    def update_task(
        self,
        task_id: str,
        *,
        tenant_id: str = "default",
        status: str | None = None,
        progress: float | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None: ...
    def cancel_task(self, task_id: str, *, tenant_id: str = "default") -> dict[str, Any] | None: ...
    def list_tasks(
        self, *, tenant_id: str = "default", status: str = "", limit: int = 50
    ) -> list[dict[str, Any]]: ...
    def get_working_memory(self, user_id: str, tenant_id: str = "default") -> dict[str, Any] | None: ...
    def upsert_working_memory(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str = "default",
        ttl_seconds: float = 1800.0,
    ) -> None: ...
    def clear_working_memory(self, user_id: str, tenant_id: str = "default") -> int: ...
    def cleanup_expired_working_memory(self) -> int: ...


def _extract_fn_signatures(tree: ast.Module) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Return mapping of public function name -> (pos_arg_names, kwonly_arg_names)."""
    out: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            pos = tuple(a.arg for a in node.args.args)
            kwonly = tuple(sorted(a.arg for a in node.args.kwonlyargs))
            out[node.name] = (pos, kwonly)
    return out


@functools.lru_cache(maxsize=8)
def _cached_parity_check(
    db_path_str: str,
    db_mtime_ns: int,
    pg_path_str: str,
    pg_mtime_ns: int,
) -> dict[str, Any]:
    db_tree = ast.parse(Path(db_path_str).read_text(encoding="utf-8"))
    pg_tree = ast.parse(Path(pg_path_str).read_text(encoding="utf-8"))

    db_sigs = _extract_fn_signatures(db_tree)
    pg_sigs = _extract_fn_signatures(pg_tree)

    db_funcs = set(db_sigs)
    pg_funcs = set(pg_sigs)

    missing_in_postgres = sorted(db_funcs - pg_funcs)
    missing_in_sqlite = sorted(pg_funcs - db_funcs)
    signature_mismatches: list[str] = []
    for fn_name in sorted(db_funcs & pg_funcs):
        db_pos, db_kw = db_sigs[fn_name]
        pg_pos, pg_kw = pg_sigs[fn_name]
        if len(db_pos) != len(pg_pos) or db_kw != pg_kw:
            signature_mismatches.append(
                f"{fn_name}: sqlite=({db_pos}, kw={db_kw}) vs postgres=({pg_pos}, kw={pg_kw})"
            )

    return {
        "ok": not missing_in_postgres and not missing_in_sqlite and not signature_mismatches,
        "sqlite_function_count": len(db_funcs),
        "postgres_function_count": len(pg_funcs),
        "missing_in_postgres": missing_in_postgres,
        "missing_in_sqlite": missing_in_sqlite,
        "signature_mismatches": signature_mismatches,
    }


def verify_storage_backend_parity(app_dir: Path | None = None) -> dict[str, Any]:
    """Compare public top-level functions and call signatures in ``database.py`` and ``postgres.py`` via cached AST."""
    base = app_dir if app_dir is not None else Path(__file__).resolve().parent
    db_path = (base / "database.py").resolve()
    pg_path = (base / "postgres.py").resolve()
    return dict(
        _cached_parity_check(
            str(db_path),
            db_path.stat().st_mtime_ns,
            str(pg_path),
            pg_path.stat().st_mtime_ns,
        )
    )
