"""Cloudflare Vectorize query client (dense-retrieval fallback when Qdrant is off).

Queries the Vectorize REST API directly (the AI Gateway proxies inference
providers only) and reshapes matches into the SAME hit-dict the retriever emits,
so downstream RRF fusion / citation building is untouched.
"""

from __future__ import annotations

import logging
from typing import Any

from . import gateway
from .config import get_cloud_settings

logger = logging.getLogger("ura.providers.vectorize")

_API_BASE = "https://api.cloudflare.com/client/v4"


def vectorize_query(
    vector: list[float], top_k: int = 10, *, vector_filter: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return up to *top_k* nearest chunks as retriever-shaped hit dicts."""
    s = get_cloud_settings()
    if s.cf_relay_base_url.strip():
        from . import relay_client

        return relay_client.relay_vectorize_query(vector, top_k, vector_filter)
    url = (
        f"{_API_BASE}/accounts/{s.cloudflare_account_id}"
        f"/vectorize/v2/indexes/{s.vectorize_index}/query"
    )
    payload: dict[str, Any] = {"vector": vector, "topK": top_k, "returnMetadata": "all"}
    if vector_filter:
        payload["filter"] = vector_filter
    resp = gateway._get_client().post(url, headers=gateway.cf_api_headers(), json=payload)
    resp.raise_for_status()
    matches = resp.json().get("result", {}).get("matches", [])
    hits: list[dict[str, Any]] = []
    for m in matches:
        meta = m.get("metadata") or {}
        hits.append(
            {
                "id": m.get("id"),
                "text": meta.get("text", ""),
                "source": meta.get("source", ""),
                "page": meta.get("page"),
                "section": meta.get("section", ""),
                "tag": meta.get("tag", ""),
                "doc_type": meta.get("doc_type", ""),
                # Edition of the source document. The retriever prefers the
                # newest known fiscal year among near-identical passages, so a
                # superseded rate table cannot evict the one in force.
                "fiscal_year": meta.get("fiscal_year", ""),
                "score": float(m.get("score", 0.0)),
            }
        )
    return hits
