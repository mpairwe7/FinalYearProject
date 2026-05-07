"""Tiny env-backed feature flag registry.

2026 production pattern: feature flags let you roll out risky changes
behind a switch without redeploying.  This module is deliberately
*tiny* — it reads ``FLAG_*`` env vars into a registry on import, and
exposes an ``is_enabled()`` helper that callers can use in hot paths.

For advanced use cases (per-user rollout, percentage splits, kill
switches) wire an OpenFeature provider (Flagsmith, Unleash, LaunchDarkly)
into ``_provider`` — the public API does not change.

Usage::

    from .flags import flags

    if flags.is_enabled("self_reflect"):
        ...  # new behaviour
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)
_PRODUCTION_ON_FLAGS = {
    "auth_required",
    "multi_tenant",
    "audit_ledger",
    "voice_consent",
}


@dataclass(frozen=True)
class Flag:
    name: str
    default: bool = False
    description: str = ""


# Canonical registry.  Add a new entry here (do not read env directly in
# callers) so flags are discoverable and auditable.
_REGISTRY: dict[str, Flag] = {
    f.name: f
    for f in [
        Flag("self_reflect", False, "Regenerate once when faithfulness is weak"),
        Flag("structured_output", False, "Emit JSON answer/citations"),
        Flag("corrective_rag", True, "Re-retrieve on low initial quality"),
        Flag("semantic_cache", True, "Cache semantically similar queries"),
        Flag("query_rewrite", True, "Spelling / abbreviation / coreference"),
        Flag("reranker", True, "Cross-encoder reranking"),
        Flag("eval_auto_run", False, "Run evaluation harness on every Nth request"),
        # Phase 14 — agentic workflows (feature-flagged off by default)
        Flag(
            "tool_use", False, "Allow the LLM to call registered tools via Qwen2.5 function-calling"
        ),
        Flag("agentic_mode", False, "Route requests through the supervisor-specialist agent graph"),
        Flag("ticket_queue", False, "Persist escalations to the tickets table for human follow-up"),
        # Phase 14 (2026) — identity & consent
        Flag("auth_required", False, "Reject unauthenticated /v1/* requests"),
        Flag("multi_tenant", False, "Enforce tenant_id isolation via RLS"),
        # Phase 15 (2026) — guided workflows + richer human handoff
        Flag(
            "workflows",
            True,
            "Route high-intent task queries into durable multi-step workflow guides",
        ),
        Flag(
            "handoff_summaries",
            True,
            "Attach structured human-triage packets to escalations and low-confidence replies",
        ),
        # Phase 15 (2026) — MCP + Tool RAG + LangGraph orchestration
        Flag("tool_rag", False, "Use Tool RAG selection instead of pasting all tool schemas"),
        Flag("langgraph", False, "Route agentic requests through the graph orchestrator"),
        # Phase 16 (2026) — personal memory
        Flag("memory_enabled", False, "Inject personal memory facts into agentic prompts"),
        # Phase 21 (2026) — audit ledger + per-segment eval
        Flag(
            "audit_ledger",
            False,
            "Append every agentic turn to the hash-chained audit_events table",
        ),
        # Phase 22 (2026) — mobile offline voice stack
        Flag(
            "voice_enabled",
            False,
            "Enable mobile on-device voice features (ASR/TTS). Server handles "
            "no audio — the flag only gates the mobile UI and scoped analytics "
            "events. Per-user consent is still required on the device.",
        ),
        # Phase 23 (2026) — streaming voice-first infrastructure
        Flag(
            "voice_streaming",
            False,
            "Enable the WebSocket streaming voice chat endpoint "
            "(/v1/voice/chat/stream). Requires SPEECH_ENABLED=true. "
            "Gates the real-time ASR/TTS pipeline with VAD and barge-in.",
        ),
        Flag(
            "voice_consent",
            False,
            "Enforce voice-specific consent checks (voice_recording, "
            "voice_analytics) before processing audio. When false, voice "
            "endpoints skip consent gates.",
        ),
        # Phase 24 (2026) — quantization & server optimization
        Flag(
            "quantization",
            False,
            "Serve quantized model variants (GGUF/AWQ/GPTQ). When enabled, "
            "the /v1/models/quantized endpoint lists available quantized versions "
            "and the server prefers quantized inference paths.",
        ),
        Flag(
            "speculative_decoding",
            False,
            "Enable speculative decoding with a smaller draft model for 1.5-2x "
            "throughput improvement. Requires a compatible draft model in artifacts/.",
        ),
        Flag(
            "prefix_caching",
            False,
            "Enable KV-cache prefix sharing across requests with identical system "
            "prompts. Reduces TTFT by 30-50% for repeated prompt prefixes.",
        ),
        # Phase 25 (2026) — production offline RAG
        Flag(
            "offline_rag",
            False,
            "Enable production offline RAG pipeline with FAISS + ONNX embedder. "
            "When enabled, the server can serve offline bundle downloads and "
            "provides /v1/offline/* endpoints for sync and status.",
        ),
        Flag(
            "offline_sync",
            False,
            "Enable background delta sync engine. Only changed chunks are "
            "transmitted (hash-based diffing). Requires offline_rag=true.",
        ),
        Flag(
            "offline_bundle_api",
            False,
            "Enable offline bundle download endpoints (/v1/offline/bundle). "
            "Serves versioned, SHA-256-verified bundles for mobile/edge clients.",
        ),
        # Phase 26 (2026) — mobile bundle optimization
        Flag(
            "mobile_bundle_check",
            False,
            "Enforce mobile bundle size limits (≤ 800 MB) in CI. "
            "Any build exceeding the limit is automatically rejected.",
        ),
        Flag(
            "on_device_search",
            False,
            "Enable on-device vector search via ONNX Runtime or ExecuTorch + "
            "FAISS Mobile. Target: < 180ms p95 on mid-range Android (4GB RAM).",
        ),
        # Phase 27 (2026) — voice-first mobile experience
        Flag(
            "voice_first_mobile",
            False,
            "Make voice the primary mobile interface. Default launch mode = "
            "Voice Chat (full-screen, animated orb). Text input remains available "
            "as secondary. Optimized for low-literacy rural users.",
        ),
        Flag(
            "voice_vision",
            False,
            "Enable voice + vision mode: speak while camera is active for "
            "document/receipt scanning. POST /v1/voice/vision/chat endpoint.",
        ),
        Flag(
            "offline_voice",
            False,
            "Enable fully offline ASR + TTS (Whisper-tiny + Piper/Sherpa). "
            "No network required for speech I/O. Target WER ≤ 18% on Ugandan English.",
        ),
        # Phase 28 (2026) — native voice-to-voice + streaming TTS + voice+vision V2
        Flag(
            "native_voice",
            False,
            "Enable native voice-to-voice engine with streaming ASR, speculative "
            "prefetch, token-level TTS (CosyVoice2), and dual-path routing. "
            "Requires CosyVoice2 model in artifacts/speech/tts/cosyvoice2/.",
        ),
        Flag(
            "streaming_tts_v2",
            False,
            "Enable token-level streaming TTS via CosyVoice2 flow-matching codec. "
            "Falls back to sentence-chunked Piper if disabled or model unavailable.",
        ),
        Flag(
            "voice_vision_v2",
            False,
            "Enable V2 voice+vision with streaming WebSocket image frames, "
            "parallel ASR+vision encoding, and Qwen2-VL document understanding. "
            "Extends the existing voice_vision flag with real-time capabilities.",
        ),
        Flag(
            "speculative_prefetch",
            False,
            "Start RAG retrieval on partial ASR hypotheses (stable prefix). "
            "Saves 100-300ms when the final query matches the prefetched prefix.",
        ),
    ]
}


class FeatureFlags:
    def __init__(self) -> None:
        self._overrides: dict[str, bool] = {}

    def is_enabled(self, name: str) -> bool:
        """Return True if the named flag is enabled.

        Resolution order: in-memory override > ``FLAG_<NAME>`` env var >
        registry default.  Unknown flag names return False and log a
        warning so typos surface quickly.
        """
        if name in self._overrides:
            return self._overrides[name]
        flag = _REGISTRY.get(name)
        if flag is None:
            logger.warning("unknown feature flag queried: %s", name)
            return False
        env_val = os.getenv(f"FLAG_{name.upper()}")
        if env_val is not None:
            return env_val.lower() in ("1", "true", "yes", "on")
        if os.getenv("APP_ENV", "development").lower() == "production":
            if name in _PRODUCTION_ON_FLAGS:
                return True
        return flag.default

    def set(self, name: str, enabled: bool) -> None:
        """Programmatic override (e.g. from an admin API).

        Overrides live in-process only.  For cluster-wide toggles, set
        the ``FLAG_<NAME>`` env var on all replicas.
        """
        if name not in _REGISTRY:
            raise KeyError(name)
        self._overrides[name] = enabled

    def clear(self, name: str) -> None:
        self._overrides.pop(name, None)

    def all(self) -> dict[str, bool]:
        return {n: self.is_enabled(n) for n in _REGISTRY}


flags = FeatureFlags()
