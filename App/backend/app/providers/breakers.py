"""Per-channel circuit breakers for the cloud fallbacks.

Reuses the app's existing ``CircuitBreaker`` (``resilience.py``) verbatim — one
named breaker per provider channel, mirroring ``_LLM_CIRCUIT`` (``service.py``)
and ``self._breakers[...]`` (``speech_service.py``). A hook checks
``allow_request()`` before calling the provider, then ``record_success()`` /
``record_failure()`` — identical to every existing tier, so failures show up in
the same breaker logs.
"""

from __future__ import annotations

from ..resilience import CircuitBreaker

CF_LLM_BREAKER = CircuitBreaker(
    name="cf_workers_ai_llm", failure_threshold=3, reset_timeout=15, max_timeout=300
)
CF_EMBED_BREAKER = CircuitBreaker(
    name="cf_workers_ai_embed", failure_threshold=3, reset_timeout=10, max_timeout=300
)
CF_STT_BREAKER = CircuitBreaker(
    name="cf_workers_ai_stt", failure_threshold=3, reset_timeout=15, max_timeout=120
)
GEMINI_BREAKER = CircuitBreaker(
    name="gemini_flash", failure_threshold=3, reset_timeout=15, max_timeout=300
)
VECTORIZE_BREAKER = CircuitBreaker(
    name="cf_vectorize", failure_threshold=3, reset_timeout=10, max_timeout=300
)
