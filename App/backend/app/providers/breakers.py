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
CF_TTS_BREAKER = CircuitBreaker(
    name="cf_workers_ai_tts", failure_threshold=3, reset_timeout=15, max_timeout=120
)
#: Sunflower answer generation through Sunbird's API (ADR-0036). A suspended account answers 403
#: at once; the breaker stops paying that round trip on every turn while Workers AI serves.
SUNBIRD_LLM_BREAKER = CircuitBreaker(
    name="sunbird_llm", failure_threshold=3, reset_timeout=30, max_timeout=600
)
GEMINI_BREAKER = CircuitBreaker(
    name="gemini_flash", failure_threshold=3, reset_timeout=15, max_timeout=300
)
VECTORIZE_BREAKER = CircuitBreaker(
    name="cf_vectorize", failure_threshold=3, reset_timeout=10, max_timeout=300
)
