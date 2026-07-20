"""Client for the Cloudflare relay (see the ``/internal/cf-relay/*`` endpoints
in ``main.py``).

Used when this deployment's own egress to Cloudflare is blocked (the HF Space
free-tier network path) — routes the two calls the dense-retrieval fallback
needs (query embedding + Vectorize search) through another deployment with
confirmed working Cloudflare egress instead. Only used when
``cf_relay_base_url`` is configured; direct-to-Cloudflare remains the default.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_cloud_settings

_HTTP_TIMEOUT = 30.0
_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=_HTTP_TIMEOUT)
    return _client


def _relay_headers() -> dict[str, str]:
    s = get_cloud_settings()
    return {
        "Authorization": f"Bearer {s.cf_relay_secret.get_secret_value()}",
        "Content-Type": "application/json",
    }


def relay_workers_ai_embed(texts: list[str], model: str) -> list[list[float]]:
    s = get_cloud_settings()
    url = f"{s.cf_relay_base_url.rstrip('/')}/internal/cf-relay/workers-ai-embed"
    resp = _get_client().post(url, headers=_relay_headers(), json={"texts": texts, "model": model})
    resp.raise_for_status()
    vectors = resp.json().get("vectors")
    if not vectors:
        raise RuntimeError("relay_workers_ai_embed: empty embedding response")
    return vectors


def relay_vectorize_query(
    vector: list[float], top_k: int, vector_filter: dict[str, Any] | None
) -> list[dict[str, Any]]:
    s = get_cloud_settings()
    url = f"{s.cf_relay_base_url.rstrip('/')}/internal/cf-relay/vectorize-query"
    payload: dict[str, Any] = {"vector": vector, "top_k": top_k}
    if vector_filter:
        payload["vector_filter"] = vector_filter
    resp = _get_client().post(url, headers=_relay_headers(), json=payload)
    resp.raise_for_status()
    return resp.json().get("hits", [])
