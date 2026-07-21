"""Client for the Cloudflare relay (see the ``/internal/cf-relay/*`` endpoints
in ``main.py``).

Used when this deployment's own egress to Cloudflare is blocked (the HF Space
free-tier network path) — routes the calls the dense-retrieval fallback and
the cloud-primary LLM chain need (query embedding, Vectorize search, chat
completion) through another deployment with confirmed working Cloudflare
egress instead. Only used when ``cf_relay_base_url`` is configured;
direct-to-Cloudflare remains the default.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from .config import get_cloud_settings

logger = logging.getLogger("ura.providers.relay_client")

# _search_vectorize() calls embed then query *sequentially*, so a caller on a
# platform with a tight front-door gateway timeout (observed ~60s on the HF
# Space free tier) can stack two of these before generation even starts. Kept
# short and env-tunable so a slow/hanging relay call fails fast into the
# local keyword fallback instead of stalling the whole request past that
# ceiling — a hybrid answer that never arrives is worse than a fast keyword
# one. Cloudflare + one extra network hop through the relay host should
# comfortably finish well under this on a healthy path.
_HTTP_TIMEOUT = float(os.getenv("CF_RELAY_HTTP_TIMEOUT", "15"))
# Chat completion legitimately runs longer than an embed/vectorize call (it's
# generating up to max_tokens, not doing a lookup), and _llm_cloud_fallback
# tries this per model in its 3-model chain — so a touch more headroom per
# attempt than the retrieval timeout, but still short enough that even trying
# the whole chain stays well inside the ~60s ceiling that motivated
# _HTTP_TIMEOUT in the first place.
_CHAT_HTTP_TIMEOUT = float(os.getenv("CF_RELAY_CHAT_HTTP_TIMEOUT", "20"))
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


def relay_workers_ai_embed(texts: list[str]) -> list[list[float]]:
    """Embed via the relay's fixed retrieval model (see CFRelayEmbedRequest —
    the relay ignores any caller-supplied model string, so none is sent)."""
    s = get_cloud_settings()
    url = f"{s.cf_relay_base_url.rstrip('/')}/internal/cf-relay/workers-ai-embed"
    t0 = time.perf_counter()
    try:
        resp = _get_client().post(url, headers=_relay_headers(), json={"texts": texts})
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning(
            "relay_workers_ai_embed failed after %.2fs", time.perf_counter() - t0, exc_info=True
        )
        raise
    logger.info("relay_workers_ai_embed took %.2fs", time.perf_counter() - t0)
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
    t0 = time.perf_counter()
    try:
        resp = _get_client().post(url, headers=_relay_headers(), json=payload)
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning(
            "relay_vectorize_query failed after %.2fs", time.perf_counter() - t0, exc_info=True
        )
        raise
    logger.info("relay_vectorize_query took %.2fs", time.perf_counter() - t0)
    return resp.json().get("hits", [])


def relay_workers_ai_chat(
    messages: list[dict[str, str]], model: str, *, max_tokens: int, temperature: float
) -> str:
    """Chat-completion via the relay (see CFRelayChatRequest — ``model`` is
    checked server-side against the deployment's own configured chat chain)."""
    s = get_cloud_settings()
    url = f"{s.cf_relay_base_url.rstrip('/')}/internal/cf-relay/workers-ai-chat"
    payload = {
        "messages": messages,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    t0 = time.perf_counter()
    try:
        resp = _get_client().post(
            url, headers=_relay_headers(), json=payload, timeout=_CHAT_HTTP_TIMEOUT
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning(
            "relay_workers_ai_chat(%s) failed after %.2fs", model, time.perf_counter() - t0,
            exc_info=True,
        )
        raise
    logger.info("relay_workers_ai_chat(%s) took %.2fs", model, time.perf_counter() - t0)
    return (resp.json().get("text") or "").strip()
