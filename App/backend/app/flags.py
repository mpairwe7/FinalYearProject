"""Env-backed feature flag registry with cohort-addressable rollout.

2026 production pattern: feature flags let you roll out risky changes
behind a switch without redeploying.  The core is deliberately *tiny* —
it reads ``FLAG_*`` env vars into a registry on import, and exposes an
``is_enabled()`` helper that callers can use in hot paths.

A boolean flag can only ship a change to *everyone* or to *no one*.
That is not a rollout, and it is the thing that blocks piloting a
change on 1% of traffic and expanding on evidence.  :class:`Rollout`
adds the three addressing modes that matter — a percentage of subjects,
named cohorts, and an explicit allowlist — while ``is_enabled(name)``
keeps working unchanged for every existing caller.

Usage::

    from .flags import flags

    if flags.is_enabled("self_reflect"):
        ...                                  # global switch, as before

    if flags.is_enabled("model_tiering", subject=user_id):
        ...                                  # 5% of users, stably bucketed

    if flags.is_enabled("tax_graph", subject=user_id, cohorts={"ura_staff"}):
        ...                                  # staff first, then a percentage

Ramping does not need a deploy.  ``FLAG_<NAME>_PERCENT``,
``FLAG_<NAME>_COHORTS`` and ``FLAG_<NAME>_ALLOWLIST`` override whatever
the registry declares, so 1% → 5% → 25% is an environment change on the
running replicas.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# CodeQL py/log-injection: `name` reaches these log calls before any registry
# check can run in every caller CodeQL considers (is_enabled() validates it
# against _REGISTRY first, but that guard lives in a different function, so
# static analysis can't credit it for callers it can't fully trace). Strip
# CR/LF/control characters at the log call itself so a value can never forge
# a fake log line, regardless of which caller reached it.
_LOG_STRIP_TABLE = dict.fromkeys(range(0x20), None)
_LOG_STRIP_TABLE[0x7F] = None


def _log_safe(value: str) -> str:
    """*value* with control characters (CR/LF included) removed."""
    return value.translate(_LOG_STRIP_TABLE)


_PRODUCTION_ON_FLAGS = {
    "auth_required",
    "multi_tenant",
    "audit_ledger",
    "ticket_queue",
    "voice_consent",
}

#: Bucket resolution.  10,000 buckets gives percentages one decimal
#: place, which is what a 0.5% canary needs.
_BUCKETS = 10_000


@dataclass(frozen=True)
class Rollout:
    """Who a flag is on for, when it is not simply on for everyone.

    The three modes are checked in order of decreasing specificity —
    allowlist, then cohort, then percentage — so naming a subject
    explicitly always beats the dice.
    """

    #: Share of subjects, 0.0–100.0.  Bucketing is stable: a subject
    #: stays on the same side of the split for the life of the flag.
    percent: float = 0.0
    #: Cohort labels (roles, tenants, "internal") that get the flag
    #: regardless of their bucket.
    cohorts: frozenset[str] = field(default_factory=frozenset)
    #: Individual subject ids that get the flag regardless of bucket.
    #: For smoke-testing in production against known accounts.
    allowlist: frozenset[str] = field(default_factory=frozenset)

    def is_addressed(self) -> bool:
        """True if this rollout targets anyone at all."""
        return bool(self.percent > 0 or self.cohorts or self.allowlist)


def _bucket_of(flag_name: str, subject: str) -> int:
    """Stable bucket in ``[0, _BUCKETS)`` for *subject* under *flag_name*.

    Two properties this needs, and one trap it avoids.

    **Stable across processes.**  Python's built-in ``hash()`` is salted
    per interpreter (``PYTHONHASHSEED``), so it would put the same user
    in different buckets on different replicas — the user would see the
    feature flicker on and off depending on which pod served them.
    SHA-256 is the same everywhere, forever.

    **Independent per flag.**  The flag name is mixed into the digest so
    a subject's bucket is uncorrelated across flags.  Hashing the
    subject alone would put the same unlucky 1% into the leading edge of
    *every* experiment, which correlates their results and makes each
    one unmeasurable.
    """
    digest = hashlib.sha256(f"{flag_name}:{subject}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % _BUCKETS


@dataclass(frozen=True)
class Flag:
    name: str
    default: bool = False
    description: str = ""
    #: Optional targeting.  ``None`` means the flag is global — its
    #: value comes from the env var or the default, exactly as before.
    rollout: Rollout | None = None


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
        Flag(
            "query_decomposition",
            True,
            "Split multi-intent questions into parallel retrieval queries",
        ),
        Flag(
            "hyde",
            False,
            "Hypothetical Document Embeddings on the dense leg only "
            "(template by default; HYDE_LLM=true spends one short generation)",
        ),
        Flag(
            "translate_retrieve",
            True,
            "For non-English questions, also search an English translation "
            "against the English corpus (generate still uses the user locale). "
            "No re-index. Off = original query only.",
        ),
        Flag("reranker", True, "Cross-encoder reranking"),
        Flag(
            "cloudflare_fallback",
            False,
            "Route to Cloudflare Workers AI / Vectorize / R2 + Gemini when primaries are down/over-budget",
        ),
        Flag("eval_auto_run", False, "Run evaluation harness on every Nth request"),
        # Phase 14 — agentic workflows (tool_use stays off; ticket_queue on)
        Flag(
            "tool_use", False, "Allow the LLM to call registered tools via Qwen2.5 function-calling"
        ),
        Flag(
            "agentic_mode",
            True,
            "Route requests through the supervisor-specialist agent graph. "
            "Default on after EN golden-set accuracy >= 0.95 "
            "(app.agents.eval_routing.agentic_mode_gate).",
        ),
        Flag(
            "ticket_queue",
            True,
            "Persist escalations to the tickets table for human follow-up. "
            "Default on after the staff workbench shipped (G32). "
            "escalate_to_human uses the same flag.",
        ),
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
        # Phase 29 (2026) — WebSocket-native agentic chat transport
        Flag(
            "ws_chat",
            False,
            "Enable the WebSocket text-chat endpoint at /v2/chat/stream. "
            "Persistent duplex transport that mirrors the SSE protocol shape "
            "and adds an agentic event surface (tool_call.*, retrieval.*, "
            "response.cancel). SSE endpoint /v1/chat/stream stays unchanged.",
        ),
        # Phase 30 (2026) — next-generation architecture increments.
        # All default off and all subject-addressable, so each lands on a
        # cohort before it lands on taxpayers.  See
        # docs/NEXTGEN_ARCHITECTURE_PROPOSAL_2026.md §7.2 for the order
        # these are meant to open in and the gate for each.
        Flag(
            "multilingual_routing",
            False,
            "Classify with locale-specific supervisor patterns (lg/nyn/ach) "
            "instead of the English tables alone. Unknown locales still fall "
            "back to English, so this cannot change an English decision.",
        ),
        Flag(
            "supervisor_llm_tiebreak",
            False,
            "Ask a small model for a second opinion when rule confidence is "
            "below SUPERVISOR_LLM_THRESHOLD, instead of accepting the "
            "low-confidence rule decision.",
        ),
        Flag(
            "model_tiering",
            False,
            "Select the generation model per turn from the supervisor's route "
            "decision (T0 none / T1 8B / T2 30B-A3B / T3 235B-A22B) instead of "
            "sending every query to the single configured model.",
        ),
        Flag(
            "evaluator_optimizer",
            False,
            "Verify money-bearing answers by deterministic recomputation and "
            "allow at most one bounded revision. Replaces the single "
            "faithfulness-floor reflection pass on those turns.",
        ),
        Flag(
            "tax_graph",
            False,
            "Load the effective-dated statutory knowledge graph and expose the "
            "tax_graph MCP namespace. Read-only: does not affect retrieval "
            "until graph_fusion is also on.",
        ),
        Flag(
            "graph_fusion",
            False,
            "Fuse the graph retrieval leg into RRF alongside dense and BM25. "
            "Requires tax_graph. Off means the graph is scored in shadow mode "
            "without reaching any answer.",
        ),
        Flag(
            "answer_overrides",
            True,
            "Staff CMS: exact-match answer overrides before retrieval.",
        ),
        Flag(
            "mcp_tasks",
            False,
            "Expose the tasks MCP namespace for long-running work "
            "(filing submission, OCR batches, graph extraction) with durable "
            "state and task.progress events over the WebSocket transport.",
        ),
    ]
}


def _env_rollout(name: str, declared: Rollout | None) -> Rollout | None:
    """Merge ``FLAG_<NAME>_*`` env overrides over the declared rollout.

    Ramping a rollout must not require a deploy — that is most of the
    point of having one.  An unset variable leaves the declared value
    alone, so a registry rollout is the floor and the environment moves
    it.
    """
    upper = name.upper()
    raw_pct = os.getenv(f"FLAG_{upper}_PERCENT")
    raw_cohorts = os.getenv(f"FLAG_{upper}_COHORTS")
    raw_allow = os.getenv(f"FLAG_{upper}_ALLOWLIST")
    if raw_pct is None and raw_cohorts is None and raw_allow is None:
        return declared

    base = declared or Rollout()
    percent = base.percent
    if raw_pct is not None:
        try:
            percent = max(0.0, min(100.0, float(raw_pct.strip().rstrip("%"))))
        except ValueError:
            # A malformed percentage must not silently mean 100%.  Keep
            # the declared value and make the typo visible.
            logger.warning(
                "flag %s: bad FLAG_%s_PERCENT=%r, ignoring",
                _log_safe(name),
                _log_safe(upper),
                raw_pct,
            )

    def _split(raw: str | None, fallback: frozenset[str]) -> frozenset[str]:
        if raw is None:
            return fallback
        return frozenset(p.strip() for p in raw.split(",") if p.strip())

    return Rollout(
        percent=percent,
        cohorts=_split(raw_cohorts, base.cohorts),
        allowlist=_split(raw_allow, base.allowlist),
    )


class FeatureFlags:
    def __init__(self) -> None:
        self._overrides: dict[str, bool] = {}
        #: Flags already warned about for an unsubjected rollout check,
        #: so a hot-path caller logs once rather than once per request.
        self._warned_no_subject: set[str] = set()

    def is_enabled(
        self,
        name: str,
        *,
        subject: str | None = None,
        cohorts: frozenset[str] | set[str] | None = None,
    ) -> bool:
        """Return True if the named flag is enabled for this caller.

        Resolution order, most decisive first:

        1. **in-memory override** — the admin API and the kill switch;
           beats everything, including a rollout, because it is how an
           operator stops a bad release right now.
        2. **``FLAG_<NAME>`` env var** — an explicit operator decision
           for the whole replica, so it means *everyone*, not a share.
        3. **production-on** — the safety flags that must not be a
           percentage of anything.
        4. **rollout** — allowlist, then cohort, then stable bucket.
        5. **registry default**.

        *subject* is the identity the percentage is bucketed on (a user
        or tenant id).  Omitting it on a percentage-addressed flag falls
        through to the default and logs once: a rollout that cannot see
        who is asking has nothing to bucket.

        Unknown flag names return False and log a warning so typos
        surface quickly.
        """
        if name in self._overrides:
            return self._overrides[name]
        flag = _REGISTRY.get(name)
        if flag is None:
            logger.warning("unknown feature flag queried: %s", _log_safe(name))
            return False
        env_val = os.getenv(f"FLAG_{name.upper()}")
        if env_val is not None:
            return env_val.lower() in ("1", "true", "yes", "on")
        if os.getenv("APP_ENV", "development").lower() == "production":
            if name in _PRODUCTION_ON_FLAGS:
                return True

        rollout = _env_rollout(name, flag.rollout)
        if rollout is not None and rollout.is_addressed():
            decided = self._resolve_rollout(name, rollout, subject, cohorts)
            if decided is not None:
                return decided

        return flag.default

    def _resolve_rollout(
        self,
        name: str,
        rollout: Rollout,
        subject: str | None,
        cohorts: frozenset[str] | set[str] | None,
    ) -> bool | None:
        """Apply *rollout*, or return None to fall through to the default.

        Returning None rather than False matters: a subject who is not
        in the rollout should get the flag's *default*, which for a
        production-on flag is True.  Collapsing "not targeted" into
        "off" would let adding a 5% rollout silently disable a flag for
        the other 95%.
        """
        if subject and subject in rollout.allowlist:
            return True
        if cohorts and rollout.cohorts and (set(cohorts) & rollout.cohorts):
            return True
        if rollout.percent <= 0:
            return None
        if not subject:
            if name not in self._warned_no_subject:
                self._warned_no_subject.add(name)
                logger.warning(
                    "flag %s has a percentage rollout but was checked without a "
                    "subject — falling back to the default",
                    _log_safe(name),
                )
            return None
        if _bucket_of(name, subject) < rollout.percent * (_BUCKETS / 100):
            return True
        return None

    def variant_for(
        self,
        name: str,
        subject: str | None = None,
        cohorts: frozenset[str] | set[str] | None = None,
    ) -> str:
        """Label this resolution for analytics: ``"on"`` or ``"off"``.

        Experiments are only measurable if each conversation records
        which side of the split served it.  Callers log this alongside
        the turn so :mod:`app.evaluation` can report per variant instead
        of averaging the two together.
        """
        return "on" if self.is_enabled(name, subject=subject, cohorts=cohorts) else "off"

    def logged_variants(self, subject: str | None = None) -> dict[str, str]:
        """Per-turn on/off labels for the flags that change retrieval or answers (G26)."""
        return {
            name: self.variant_for(name, subject=subject)
            for name in (
                "hyde",
                "graph_fusion",
                "translate_retrieve",
                "corrective_rag",
                "query_decomposition",
                "evaluator_optimizer",
            )
        }

    def experiment_log_fields(
        self, subject: str | None = None, locale: str = ""
    ) -> dict[str, str]:
        """kwargs for ``log_conversation`` (flag_variants JSON + locale)."""
        import json

        return {
            "flag_variants": json.dumps(self.logged_variants(subject=subject)),
            "locale": locale or "",
        }

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
        """Global resolution of every flag, with no subject.

        A percentage-addressed flag reports its default here — this is
        an introspection view of the replica, not of any one user.
        """
        return {n: self.is_enabled(n) for n in _REGISTRY}

    def describe(self, name: str) -> dict[str, object]:
        """Registry entry plus effective rollout, for the admin surface."""
        flag = _REGISTRY.get(name)
        if flag is None:
            raise KeyError(name)
        rollout = _env_rollout(name, flag.rollout)
        return {
            "name": flag.name,
            "default": flag.default,
            "description": flag.description,
            "overridden": name in self._overrides,
            "rollout": (
                {
                    "percent": rollout.percent,
                    "cohorts": sorted(rollout.cohorts),
                    "allowlist_size": len(rollout.allowlist),
                }
                if rollout is not None and rollout.is_addressed()
                else None
            ),
        }


def is_protected(name: str) -> bool:
    """Safety flags that the admin UI must not flip off from a browser."""
    return name in _PRODUCTION_ON_FLAGS


flags = FeatureFlags()
