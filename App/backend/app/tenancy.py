"""Tenant scope helpers (G30 slice).

``FLAG_MULTI_TENANT`` does not turn on Postgres RLS. This module is the
application-level predicate: when the flag is on, every read must carry
the request tenant and must not return another tenant's row. When the
flag is off, the default tenant is used and no extra filter is applied.
"""

from __future__ import annotations

from typing import Any

from .flags import flags


DEFAULT_TENANT = "default"


def tenant_enabled() -> bool:
    return flags.is_enabled("multi_tenant")


def active_tenant_id(ctx: Any | None = None) -> str:
    if ctx is not None:
        tid = getattr(ctx, "tenant_id", "") or ""
        if tid:
            return str(tid)
        user = getattr(ctx, "user", None)
        if user is not None:
            tid = getattr(user, "tenant_id", "") or ""
            if tid:
                return str(tid)
    return DEFAULT_TENANT


def same_tenant(row_tenant: str | None, ctx: Any | None = None) -> bool:
    if not tenant_enabled():
        return True
    return (row_tenant or DEFAULT_TENANT) == active_tenant_id(ctx)


def qdrant_payload_filter(tenant_id: str | None = None) -> dict[str, Any] | None:
    """Mandatory payload predicate when multi-tenant is on. Not a collection split."""
    if not tenant_enabled():
        return None
    tid = tenant_id or DEFAULT_TENANT
    return {"must": [{"key": "tenant_id", "match": {"value": tid}}]}


def rls_set_local_sql(tenant_id: str | None = None) -> str:
    """Session variable for ``infra/postgres/rls.sql``. Caller must apply it."""
    tid = (tenant_id or DEFAULT_TENANT).replace("'", "")
    return f"SET LOCAL app.current_tenant = '{tid}'"
