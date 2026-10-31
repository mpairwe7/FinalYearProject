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


_EXPERIMENTAL_RAG_FLAGS = frozenset(
    {
        "hyde",
        "graph_fusion",
        "tool_rag",
        "tool_use",
        "langgraph",
        "self_reflect",
        "supervisor_llm_tiebreak",
    }
)


def _default_flag_category(name: str, default: bool, rollout: Rollout | None) -> str:
    """Classify each flag into a 4-tier October 2026 lifecycle taxonomy (MAINT-GAP-03)."""
    if name in _PRODUCTION_ON_FLAGS:
        return "compliance_invariant"
    if name in _EXPERIMENTAL_RAG_FLAGS:
        return "experimental_rag"
    if rollout is not None and rollout.is_addressed():
        return "release_rollout"
    if default:
        return "kill_switch"
    return "release_rollout"


@dataclass(frozen=True)
class Flag:
    name: str
    default: bool = False
    description: str = ""
    #: Optional targeting.  ``None`` means the flag is global — its
    #: value comes from the env var or the default, exactly as before.
    rollout: Rollout | None = None
    #: Lifecycle taxonomy class ("compliance_invariant" | "kill_switch" | "release_rollout" | "experimental_rag").
    category: str = ""
    #: Owning subsystem ("security_governance" | "rag_retrieval" | "agentic_runtime" | "voice_speech" | "platform").
    owner: str = "platform"

    @property
    def effective_category(self) -> str:
        return self.category or _default_flag_category(self.name, self.default, self.rollout)


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
        Flag(
            "enterprise_connectors",
            False,
            "Expose explicitly selected, deployment-bound remote MCP connectors in taxpayer chat. "
            "Only reviewed read-only operations are eligible. In-process simulator tools are never "
            "listed; a simulator bound as a remote server is labelled as demo data and is refused in production.",
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
            "Select the local generation model per turn from the supervisor's route "
            "decision (T0 none / T1 Sunflower-Qwen3.5-9B / T2-T3 Qwen3.8-27B-FP8) "
            "instead of sending every query to the single configured model.",
        ),
        Flag(
            "reasoner_for_translated_locales",
            False,
            "Allow translated locales (lg/sw/nyn/ach) to promote to the T2/T3 "
            "reasoning endpoint for English draft synthesis before Sunflower "
            "localization.",
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
        Flag(
            "voice_receptionist",
            False,
            "Simulated phone receptionist: Pipecat call socket, staff Calls page, audio bridge",
        ),
        Flag(
            "receptionist_language_detection",
            False,
            "Receptionist answers in the language the caller speaks (en/sw on Gemini Live, "
            "lg on the cascaded engine), detected per utterance; needs voice_receptionist",
        ),
        Flag(
            "knowledge_discrepancy_reporting",
            True,
            "Auto-detect conversational factual disputes, log discrepancy reports, "
            "and apply active statutory precedences & tombstones.",
        ),
        # Phase 31 (2026) — Agentic Voice CX & Continuous Relationship Orchestration
        Flag(
            "hybrid_speech_s2s",
            False,
            "Enable the Hybrid S2S + Cascaded Speech Router in voice pipelines "
            "(fast S2S path for conversational turns, regulated figure-protected "
            "cascaded path for statutory/numeric turns).",
        ),
        Flag(
            "voice_agentic_workflows",
            False,
            "Enable guided multi-step workflows, calculator MCP tools, and spoken "
            "confirmation tokens inside live voice receptionist calls.",
        ),
        Flag(
            "multi_agent_dag",
            False,
            "Enable multi-specialist DAG chaining (CUSTOMS_SPECIALIST + TAX_SPECIALIST "
            "+ TOOLS) with HandoffEnvelope verification in the graph orchestrator.",
        ),
        Flag(
            "continuous_relationship_memory",
            False,
            "Enable longitudinal ResolutionCase persistence, bidirectional Voice <-> Web "
            "carryover, and post-call background task continuation.",
        ),
        Flag(
            "emotion_aware_prosody",
            False,
            "Enable acoustic prosody analysis (pitch, energy, speech rate) in "
            "CallerAudioTap and dynamic empathy/pacing conditioning in TTS.",
        ),
        Flag(
            "spoken_figure_guard",
            False,
            "Run acoustic digit verification on synthesized TTS audio when replies "
            "contain statutory rates, UGX amounts, TINs, or PRNs.",
        ),
        Flag(
            "officer_voice_copilot",
            False,
            "Stream live ASR transcripts, English translations, and statutory rate "
            "cards to the officer desk while a voice call is bridged to staff.",
        ),
        # Phase 32 (2026) — Agentic CX, Emotional Intelligence (EQ) & Workforce Co-Execution
        Flag(
            "eq_adaptive_retrieval",
            False,
            "Run deterministic EQ assessment in Stage 0c to boost statutory "
            "relief/instalment/waiver passages and adapt response structure on "
            "hardship, anxiety, or frustration.",
        ),
        Flag(
            "zero_effort_slot_prefill",
            False,
            "Pre-fill WorkflowSession and calculator slots from consented "
            "SemanticMemory, WorkingMemory.verified_slot_bank, and scanned documents.",
        ),
        Flag(
            "workflow_specialist_bridge",
            False,
            "Allow active guided workflows to invoke TAX_SPECIALIST, "
            "CUSTOMS_SPECIALIST, or MCP calculators mid-flow to compute a slot "
            "and resume without losing workflow state.",
        ),
        Flag(
            "proactive_resolution_continuation",
            False,
            "Enable background post-interaction continuation when mcp_tasks or "
            "officer reviews complete, writing localized continuation cards to "
            "reminder_inbox.",
        ),
        Flag(
            "officer_ai_co_execution",
            False,
            "Enable pre-verified bilingual draft replies on the officer workbench "
            "and Officer -> AI procedural follow-up delegation.",
        ),
        # Phase 33 (2026) — Low-Bandwidth 2G/3G Voice Resilience & Offline Action Outbox
        Flag(
            "low_bandwidth_voice_codec",
            False,
            "Negotiate low-bitrate 8kHz mu-law / 12kbps Opus framing and "
            "caption-first audio degradation on 2G/3G or Data Saver voice connections.",
        ),
        Flag(
            "call_drop_resume",
            False,
            "Enable HMAC-signed call_resume_token and 45-second caller reconnect "
            "grace window on mobile voice socket drops.",
        ),
        Flag(
            "offline_action_outbox",
            False,
            "Enable PWA offline calculator/workflow action outbox sync into "
            "verified_slot_bank and ResolutionCase upon network recovery.",
        ),
        # Phase 34 (2026) — Enterprise Governance, Action-Level Audit v3 & Continuous QA Scorecard
        Flag(
            "action_level_audit_v3",
            False,
            "Emit structured ActionAuditRecord v3 (decision trace, guardrail verdicts, "
            "approval gate, and reversibility contract) into the tamper-evident AuditLedger.",
        ),
        Flag(
            "continuous_quality_scorecard",
            False,
            "Run 100% turn-level InteractionQualityScorecard evaluation across statutory "
            "accuracy, figure fidelity, policy adherence, empathy, and handoff cleanliness.",
        ),
        Flag(
            "risk_tiered_autonomy_gates",
            False,
            "Enforce Low/Medium/High/Critical risk-tiered autonomy gates, soft-confirmation "
            "confidence floor (>=0.80), and mandatory officer approval on High-risk actions.",
        ),
        Flag(
            "quality_circuit_breaker",
            False,
            "Automatically throttle autonomous Medium/High-risk execution when rolling "
            "quality scorecard or figure-fidelity metrics drop below the safety threshold.",
        ),
        Flag(
            "verifiable_decision_receipts",
            False,
            "Attach human-readable decision_receipt explanations (audit ID, statutory basis, "
            "verified inputs, and reversal instructions) to consequential responses.",
        ),
        # Phase 35 (October 2026) — Multimodal EQ Fusion, Workflow Tangent Resume,
        # Memory-Backed Autonomous Outcome Verification & Closed-Loop Officer Preferences
        Flag(
            "eq_multimodal_fusion",
            False,
            "Fuse multi-turn lexical distress trajectory, native Ugandan vernacular hardship "
            "cues (lg/sw/nyn/ach), and acoustic prosody into a smoothed AffectiveState score "
            "with affect-conditioned translation register.",
        ),
        Flag(
            "workflow_tangent_resume",
            False,
            "Allow active guided workflows to answer mid-flow policy side-questions via "
            "specialist/RAG and seamlessly resume the parked workflow step.",
        ),
        Flag(
            "autonomous_outcome_verification",
            False,
            "Bind missing tool arguments from consented verified_slot_bank, SemanticMemory, "
            "and prior specialist DAG receipts, and verify post-action outcomes.",
        ),
        Flag(
            "officer_preference_capture",
            False,
            "Capture PII-redacted edit diffs between AI co-pilot drafts and final officer "
            "replies in officer_chat for offline DPO/ORPO preference export.",
        ),
        # Phase 38 (October 2026) — the relationship, EQ and handoff primitives
        # reach the text channel. See docs/plans/agentic-cx-phase38-plan.md.
        Flag(
            "eq_stakes_escalation",
            False,
            "Weigh affect, stakes (enforcement, a deadline today, a large amount), effort and "
            "confidence to offer an officer in text chat (first, when urgent) and to set ticket "
            "priority and SLA on every path to an officer. Offers only; never escalates by itself.",
        ),
        Flag(
            "structured_text_handoff",
            False,
            "Attach HandoffEnvelope v2.1 to text-chat tickets (auto-escalations and the "
            "taxpayer's own request): affect, SLA, and the details to ask for pruned against "
            "what the taxpayer already gave. Slot names only, never values.",
        ),
        Flag(
            "workflow_emotional_offramp",
            False,
            "Phase 39: a guided workflow does not answer distress or enforcement news with a "
            "validation error — it pauses (answers kept) and offers to continue, an officer or "
            "cancel; and after repeated invalid answers it says what is accepted, then offers a way out.",
        ),
        Flag(
            "wellbeing_checkin",
            False,
            "Phase 39: hopelessness said amid hardship or enforcement (this or the last two "
            "messages) gets a check-in with the crisis lines before the tax answer, once per "
            "conversation. Explicit statements keep the crisis path.",
        ),
        Flag(
            "cxp_business_hours",
            False,
            "Phase 40 (G174, FLAG_CXP_BUSINESS_HOURS): timezone-aware CXP operating hours (07:00-23:00 EAT / 11 PM close). "
            "Escalations after 11 PM when no live officer is online queue for priority morning "
            "review/callback with SLA pause/resume and transparent 5-locale after-hours disclosure.",
        ),
        Flag(
            "live_web_research",
            False,
            "Phase 41 (FLAG_LIVE_WEB_RESEARCH): check official sources (ura.go.ug, ulii.org, "
            "bou.or.ug, eac.int) when a question needs current information, through a configured "
            "search provider and an ephemeral RAM-backed document sandbox, with indicative market "
            "FX. Web text stays evidence and a failed check is disclosed (ADR-0021).",
        ),
        Flag(
            "auto_qdrant_web_sync",
            False,
            "Phase 41 (FLAG_AUTO_QDRANT_WEB_SYNC): automatically compare ura.go.ug PDFs against "
            "Qdrant, delete/tombstone outdated editions, upsert newer editions into Qdrant, and "
            "record updates in web_sync_ledger to prevent redundant web searches.",
        ),
        Flag(
            "portal_copilot",
            True,
            "Phase 42 (FLAG_PORTAL_COPILOT): 4-tier URA portal task execution, Claude-style HITL "
            "confirmation cards, inline form elicitation, and inbuilt stuck-user DOM co-pilot.",
            category="kill_switch",
            owner="agentic_runtime",
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
        if os.getenv("APP_ENV", "development").lower() == "production" and name in _PRODUCTION_ON_FLAGS:
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
                "hybrid_speech_s2s",
                "multi_agent_dag",
                "spoken_figure_guard",
                "eq_adaptive_retrieval",
                "workflow_specialist_bridge",
                "low_bandwidth_voice_codec",
                "call_drop_resume",
                "action_level_audit_v3",
                "continuous_quality_scorecard",
                "risk_tiered_autonomy_gates",
                "quality_circuit_breaker",
                "verifiable_decision_receipts",
                "eq_multimodal_fusion",
                "workflow_tangent_resume",
                "autonomous_outcome_verification",
                "officer_preference_capture",
                "eq_stakes_escalation",
                "structured_text_handoff",
                "workflow_emotional_offramp",
                "wellbeing_checkin",
                "cxp_business_hours",
                "portal_copilot",
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
            "category": flag.effective_category,
            "owner": flag.owner,
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

    def by_category(self, category: str) -> list[str]:
        """Return sorted flag names belonging to *category*."""
        return sorted(
            name for name, f in _REGISTRY.items() if f.effective_category == category
        )

    def lifecycle_summary(self) -> dict[str, list[str]]:
        """Group all registered flags by their 4-tier lifecycle classification."""
        summary: dict[str, list[str]] = {
            "compliance_invariant": [],
            "kill_switch": [],
            "release_rollout": [],
            "experimental_rag": [],
        }
        for name in sorted(_REGISTRY):
            cat = _REGISTRY[name].effective_category
            summary.setdefault(cat, []).append(name)
        return summary


def is_protected(name: str) -> bool:
    """Safety flags that the admin UI must not flip off from a browser."""
    return name in _PRODUCTION_ON_FLAGS


flags = FeatureFlags()
