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

import logging
import os
from dataclasses import dataclass
from enum import Enum

from ..analytics import metrics

logger = logging.getLogger(__name__)

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

# Named slots for the relay's cf-relay/workers-ai-chat endpoint (main.py): a
# relay caller picks a SLOT ("primary"/"fallback"/"fallback2"), never a raw
# model string — the endpoint looks the actual id up from this dict, so the
# value that ever reaches the outbound Cloudflare URL always originates here,
# not from request data. A caller-supplied model string, even checked against
# an allowlist in a Pydantic validator, still reads as tainted to CodeQL's
# dataflow analysis (a validator is just code to it, not a proven sanitizer);
# a dict lookup by a Literal-typed key is the pattern it recognizes as safe.
CHAT_MODEL_SLOTS: dict[str, str] = {
    "primary": CF_LLM_MODEL,
    "fallback": CF_LLM_FALLBACK_MODEL,
    "fallback2": CF_LLM_FALLBACK_MODEL_2,
}


# ── Capability tiers ────────────────────────────────────────────────────────
# One model for every query shape means a rate lookup and a multi-year
# objection cost the same and get the same reasoning depth. The tier a turn
# needs is already known before any model loads: the supervisor emits a
# ``RouteDecision`` in under 2 ms, at 36/36 on its golden set. Selecting on
# that decision is therefore free — no extra classifier, no extra call, no
# extra latency.
#
# Gated by ``FLAG_MODEL_TIERING``. Off, :func:`select_tier` always returns
# ``T1``, which is the single configured model the system used before.


class ModelTier(str, Enum):
    """Generation capability required by a turn, cheapest first.

    String values are stable identifiers — they are logged to analytics
    and appear on Prometheus labels, so renaming needs a migration.
    """

    #: No generation at all. The deterministic paths — a greeting, a
    #: clarification, a rate read out of the effective-dated table, a
    #: calculator call with complete arguments — produce their answer
    #: without a model. This tier is the cheapest thing in the system
    #: and it serves the largest share of traffic.
    T0 = "none"

    #: Dense 8B. Single-hop retrieval, query rewriting, the supervisor's
    #: own tiebreak, and **every** Ugandan-language turn (see
    #: :data:`ADAPTER_BOUND_LOCALES`).
    T1 = "small"

    #: MoE, ~3B active of 30B. The agentic default: tool-calling loops,
    #: workflows, the customs specialist. Roughly 8B-class decode cost
    #: with materially better tool-call fidelity, which matters most
    #: because the main loop *is* tool calling.
    T2 = "agentic"

    #: MoE, ~22B active of 235B. Multi-hop statutory synthesis,
    #: objections and disputes, and the evaluator role. Budget-gated;
    #: this is the tier that must stay a small share of turns.
    T3 = "deep"


#: Tier → model id, environment-overridable.
#:
#: A ``dict`` lookup keyed by an enum member, exactly as
#: :data:`CHAT_MODEL_SLOTS` above. That shape is deliberate and load-bearing:
#: it guarantees the value reaching an outbound model URL originates here and
#: never from request data, which an allowlist check inside a validator does
#: not prove to dataflow analysis.
MODEL_SLOTS: dict[ModelTier, str] = {
    ModelTier.T1: os.getenv("MODEL_T1", "Qwen/Qwen3-8B"),
    ModelTier.T2: os.getenv("MODEL_T2", "Qwen/Qwen3-30B-A3B-Instruct-2507"),
    ModelTier.T3: os.getenv("MODEL_T3", "Qwen/Qwen3-235B-A22B-Instruct-2507"),
}

#: Locales pinned to T1 regardless of how hard the question is.
#:
#: The ``lg``/``sw``/``nyn``/``ach`` LoRA adapters are trained against the
#: Qwen3-8B base and loaded with ``set_adapter()``. A MoE has a different
#: architecture, so promoting one of these turns would silently drop the
#: adapter and answer a Luganda question with a model that has never been
#: tuned for it — worse than the "cheaper" tier it was promoted from.
#: Retraining the adapters is a separate, budgeted decision.
#:
#: Mirrors ``service.LOCAL_PRIMARY_LOCALES``; kept as its own name because
#: the reason differs — that one is about cloud availability, this one is
#: about which base weights the adapters fit.
ADAPTER_BOUND_LOCALES: frozenset[str] = frozenset(
    s.strip().lower()
    for s in os.getenv("ADAPTER_BOUND_LOCALES", "lg,sw,nyn,ach").split(",")
    if s.strip()
)

#: Routes that never need a model — the supervisor has already produced
#: the answer, or a deterministic tool will.
_T0_ROUTES = frozenset({"greet", "clarify", "blocked"})

#: Routes whose whole purpose is calling tools.
_T2_ROUTES = frozenset({"tools", "tax_specialist", "customs_specialist"})

#: Reasons, matched case-insensitively on the supervisor's escalation
#: reason, that mean a human is going to read this answer. Worth the
#: deepest tier: a taxpayer disputing an assessment is the worst moment
#: to be served the cheapest model.
_DEEP_ESCALATION_CUES = ("dispute", "legal", "objection", "appeal")

#: Comparable ordering. ``ModelTier`` is a ``str`` enum, so its natural
#: ordering is alphabetical ("agentic" < "deep" < "none" < "small") and
#: means nothing here.
_TIER_ORDER: dict[ModelTier, int] = {
    ModelTier.T0: 0,
    ModelTier.T1: 1,
    ModelTier.T2: 2,
    ModelTier.T3: 3,
}


@dataclass(frozen=True)
class TierDecision:
    """Chosen tier plus why, so the choice is auditable after the fact."""

    tier: ModelTier
    reason: str
    #: True when something promoted the turn above its route's base tier.
    promoted: bool = False

    @property
    def model(self) -> str:
        """Model id for this tier; empty string for :attr:`ModelTier.T0`."""
        return MODEL_SLOTS.get(self.tier, "")


def _base_tier(route: str, tool_count: int) -> tuple[ModelTier, str]:
    """Tier implied by the route alone, before any promotion."""
    if route in _T0_ROUTES:
        return ModelTier.T0, f"{route} needs no generation"
    if route in _T2_ROUTES:
        # A single-tool turn with a whitelist is a calculator call and a
        # sentence of framing; it does not need the agentic tier.
        if tool_count and tool_count <= 1:
            return ModelTier.T1, "single-tool turn"
        return ModelTier.T2, f"{route} runs the tool loop"
    if route == "escalate":
        return ModelTier.T2, "escalation needs a careful handoff summary"
    return ModelTier.T1, "single-hop retrieval"


def select_tier(
    route: str,
    *,
    confidence: float = 1.0,
    tool_count: int = 0,
    locale: str = "en",
    escalation_reason: str = "",
    distress: str = "",
    multi_hop: bool = False,
    evaluator_rejected: bool = False,
    budget_exhausted: bool = False,
    enabled: bool = True,
) -> TierDecision:
    """Pick the generation tier for one turn.

    *route* is ``RouteDecision.route.value``; the rest are the signals the
    supervisor and graph state already carry. Taking primitives rather
    than the dataclasses keeps this module free of an import back into
    ``app.agents``, which imports :mod:`app.providers` itself.

    **Promotion only.** A turn can move up — because the evaluator
    rejected the answer, because the graph found a multi-hop path,
    because the taxpayer is distressed, or because routing was
    uncertain. It is never moved *down* mid-flight: a partially
    generated answer must not change author halfway through.

    The one exception is *budget_exhausted*, which caps at T2. That is a
    deliberate operational backstop rather than a quality decision, and
    it is reported in the reason so it is visible in the trace.
    """
    if not enabled:
        return TierDecision(ModelTier.T1, "tiering disabled")

    tier, reason = _base_tier(route, tool_count)

    # T0 is a statement that no model runs at all. Nothing promotes it —
    # a greeting from a frustrated user is still a greeting.
    if tier is ModelTier.T0:
        return TierDecision(tier, reason)

    lang = (locale or "en").strip().lower().split("-")[0].split("_")[0]
    if lang in ADAPTER_BOUND_LOCALES:
        return TierDecision(ModelTier.T1, f"{lang} is bound to the T1 LoRA adapters")

    promotions: list[tuple[ModelTier, str]] = []
    if evaluator_rejected:
        promotions.append((ModelTier.T3, "evaluator rejected the previous draft"))
    if multi_hop:
        promotions.append((ModelTier.T3, "multi-hop statutory reasoning"))
    if route == "escalate" and any(
        cue in escalation_reason.lower() for cue in _DEEP_ESCALATION_CUES
    ):
        promotions.append((ModelTier.T3, "dispute-bound escalation"))
    if distress in ("frustration", "hardship"):
        # A taxpayer who is already struggling is the last person who
        # should get the cheapest answer.
        promotions.append((ModelTier.T2, f"taxpayer distress: {distress}"))
    if confidence < 0.7:
        promotions.append((ModelTier.T2, f"low routing confidence ({confidence:.2f})"))

    promoted = False
    for candidate, why in promotions:
        if _TIER_ORDER[candidate] > _TIER_ORDER[tier]:
            tier, reason, promoted = candidate, why, True

    if budget_exhausted and _TIER_ORDER[tier] > _TIER_ORDER[ModelTier.T2]:
        return TierDecision(ModelTier.T2, f"{reason} (capped: T3 budget exhausted)", promoted)

    return TierDecision(tier, reason, promoted)


def log_tier(task: str, decision: TierDecision) -> None:
    """Record the tier that served *task* (Prometheus ``model_tier_total``)."""
    try:
        metrics.inc(
            "model_tier_total",
            labels={"task": task, "tier": decision.tier.value,
                    "promoted": str(decision.promoted).lower()},
        )
    except Exception:
        pass


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
