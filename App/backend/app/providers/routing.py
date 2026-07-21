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
# Ordered best-quality → cheaper/faster; all GA on Workers AI as of 2026-06.
# When LLM_PRIMARY_BACKEND=workers_ai this chain is the PRIMARY generator for
# high-resource locales (English, Swahili, …); the local Qwen3-8B stays primary
# for Ugandan languages and is the universal fallback (see
# service._prefer_cloud_primary).  Reasoning models (QwQ-32B, DeepSeek-R1) are
# deliberately excluded — their <think> traces add latency/cost a citation-
# grounded chatbot does not want.
# 1) Llama 3.3 70B — best instruction-following / citation discipline (~92 IFEval).
CF_LLM_MODEL = os.getenv("CF_LLM_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
# 2) Mistral Small 3.1 24B — ~70B-class quality, ~4× cheaper output, lower latency.
CF_LLM_FALLBACK_MODEL = os.getenv(
    "CF_LLM_FALLBACK_MODEL", "@cf/mistralai/mistral-small-3.1-24b-instruct"
)
# 3) Llama 4 Scout 17B (16-expert MoE) — cheap, 131k context, fast; final cloud hop.
CF_LLM_FALLBACK_MODEL_2 = os.getenv(
    "CF_LLM_FALLBACK_MODEL_2", "@cf/meta/llama-4-scout-17b-16e-instruct"
)
# Fast / high-volume / classification model.
CF_LLM_FAST_MODEL = os.getenv("CF_LLM_FAST_MODEL", "@cf/meta/llama-3.1-8b-instruct-fp8")
# (STT/TTS/MT model IDs live in speech_service.py: STT_FALLBACK_MODEL, etc.)

# Closed set of chat-completion models the relay's cf-relay/workers-ai-chat
# endpoint (main.py) will forward to Cloudflare on a caller's behalf — the
# actual configured chain, not a pattern, so a relay caller can only ever pick
# a model this deployment's own operator chose (see CFRelayChatRequest in
# models.py for why a regex/free-string model field isn't good enough).
ALLOWED_CHAT_MODELS = frozenset(
    {CF_LLM_MODEL, CF_LLM_FALLBACK_MODEL, CF_LLM_FALLBACK_MODEL_2}
)


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
