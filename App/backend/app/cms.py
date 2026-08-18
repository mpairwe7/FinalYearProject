"""Staff answer-override CMS (G31 remainder).

Exact match on a normalised query. Does not edit the FAQ corpus. The
chat path consults this only when ``FLAG_ANSWER_OVERRIDES`` is on.
"""

from __future__ import annotations

import re
from typing import Any

_WS = re.compile(r"\s+")


def normalize_query(text: str) -> str:
    return _WS.sub(" ", str(text or "").strip().lower())


def lookup(query: str) -> dict[str, Any] | None:
    from . import database as db

    key = normalize_query(query)
    if not key:
        return None
    return db.get_answer_override(key)


def upsert(
    query: str,
    reply: str,
    *,
    source_url: str = "",
    created_by: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    from . import database as db

    key = normalize_query(query)
    if not key:
        raise ValueError("query is required")
    body = str(reply or "").strip()
    if not body:
        raise ValueError("reply is required")
    return db.upsert_answer_override(
        key,
        body,
        source_url=str(source_url or "")[:500],
        created_by=str(created_by or "")[:120],
        enabled=enabled,
    )
