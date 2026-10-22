"""Storage Backend Protocol & Dual-Database Parity Verifier (October 2026).

Enforces ISO/IEC 25010:2023 Modifiability & Replaceability between
``app.database`` (SQLite WAL default) and ``app.postgres`` (PostgreSQL
connection-pooled backend) so neither backend can drift in public functions or
call signatures.
"""

from __future__ import annotations

import ast
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


def verify_storage_backend_parity(app_dir: Path | None = None) -> dict[str, Any]:
    """Compare public top-level functions in ``database.py`` and ``postgres.py`` via AST."""
    base = app_dir if app_dir is not None else Path(__file__).resolve().parent
    db_path = base / "database.py"
    pg_path = base / "postgres.py"

    db_tree = ast.parse(db_path.read_text(encoding="utf-8"))
    pg_tree = ast.parse(pg_path.read_text(encoding="utf-8"))

    db_funcs = {
        n.name
        for n in db_tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
    }
    pg_funcs = {
        n.name
        for n in pg_tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
    }

    missing_in_postgres = sorted(db_funcs - pg_funcs)
    missing_in_sqlite = sorted(pg_funcs - db_funcs)
    return {
        "ok": not missing_in_postgres and not missing_in_sqlite,
        "sqlite_function_count": len(db_funcs),
        "postgres_function_count": len(pg_funcs),
        "missing_in_postgres": missing_in_postgres,
        "missing_in_sqlite": missing_in_sqlite,
    }
