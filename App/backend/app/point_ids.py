"""Qdrant point ids: content-derived, so re-indexing identical content is idempotent.

Its own module because more than the retriever depends on it: the local dense
leg (ADR-0034) records its vectors against these ids, and CI caches those
vectors on this file. A change here changes every id, and the cache key must
see it without re-embedding the corpus on every unrelated retriever edit.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

# Fixed namespace so re-indexing identical content yields identical point ids
# (idempotent upserts) and a stable per-collection binding sentinel.
_POINT_ID_NAMESPACE = uuid.UUID("a3f1c2b4-1e2d-4f5a-8b6c-9d0e1f2a3b4c")


def deterministic_point_id(doc: dict[str, Any]) -> str:
    """Content-derived, stable Qdrant point id for idempotent reindexing.

    Re-indexing the same chunk produces the same id, so a non-``--recreate``
    rebuild overwrites rather than appending a duplicate row.
    """
    text = doc.get("text") or doc.get("answer") or ""
    key = (
        "::".join(str(doc.get(k, "")) for k in ("source", "page", "section", "chunk_id"))
        + "::"
        + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    )
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, key))


def bm25_binding_sentinel_id(collection: str) -> str:
    """Deterministic id of the per-collection sentinel point holding the corpus
    hash, used to verify the loaded bm25_state matches the live vectors."""
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, f"{collection}::__bm25_binding__"))
