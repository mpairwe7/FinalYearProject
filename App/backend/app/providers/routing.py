"""Central model-routing policy + observability for the cloud fallbacks.

Single source of truth for which models each task should try (Best -> Fallbacks)
and the place that records what actually served a request. Model IDs are
env-overridable so the strategy can be tuned (e.g. swap to a different
Cloudflare model) without code changes. The call wiring lives in service.py /
speech_service.py / retriever.py; those modules import the IDs below and call
``log_model_use()`` / ``log_fallback()`` at each routing decision, which surface
on the existing Prometheus ``/metrics`` (``analytics.MetricsStore``).

Catalog note (account c30f297...): the model strategy named some models that are
NOT in the Workers AI catalog (Llama 405B, Command R+, Qwen2.5-72B, nomic-embed);
the defaults below use the best available substitutes. Gemini uses
``CloudSettings.gemini_model`` (2.5 Flash). A Vectorize index is tied to ONE
embedding model's vector space, so embedding resilience is "retry bge-m3 ->
degrade to BM25 keyword", not a different embed model.
"""
from __future__ import annotations

import os

from ..analytics import metrics

# ── Cloudflare Workers AI text models (env-overridable) ──────────────────────
# Reasoning / RAG / summarization primary CF model (best available; not 405B).
CF_LLM_MODEL = os.getenv("CF_LLM_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
# Deeper reasoning fallback (no Command R+ / Qwen2.5-72B in catalog).
CF_LLM_FALLBACK_MODEL = os.getenv("CF_LLM_FALLBACK_MODEL", "@cf/qwen/qwq-32b")
# Fast / high-volume / classification model.
CF_LLM_FAST_MODEL = os.getenv("CF_LLM_FAST_MODEL", "@cf/meta/llama-3.1-8b-instruct-fp8")
# (STT/TTS/MT model IDs live in speech_service.py: STT_FALLBACK_MODEL, etc.)


def log_model_use(task: str, model: str) -> None:
    """Record that *model* served *task* (Prometheus ``model_usage_total``)."""
    try:
        metrics.inc("model_usage_total", labels={"task": task, "model": model})
    except Exception:
        pass


def log_fallback(task: str, frm: str, to: str, reason: str = "error") -> None:
    """Record a fallback hop for *task* (Prometheus ``model_fallback_total``)."""
    try:
        metrics.inc("model_fallback_total", labels={"task": task, "from": frm, "to": to, "reason": reason})
    except Exception:
        pass
