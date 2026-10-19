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

    #: Dense 9B local model (``Sunbird/Sunflower-Qwen3.5-9B``). Single-hop
    #: retrieval, query rewriting, the supervisor's own tiebreak, and
    #: Ugandan-language localization (see :data:`ADAPTER_BOUND_LOCALES`).
    T1 = "small"

    #: Local reasoning model (``Qwen/Qwen3.8-27B-FP8``) with ``thinking=False``.
    #: Serves tool-calling loops, distressed turns, and T3 turns capped by
    #: the rolling T3 share budget.
    T2 = "agentic"

    #: Local reasoning model (``Qwen/Qwen3.8-27B-FP8``). Multi-hop statutory
    #: synthesis, objections and disputes, cross-border / multi-head analysis,
    #: and the evaluator role. Budget-gated via ``REASONER_MAX_SHARE``.
    T3 = "deep"


@dataclass(frozen=True)
class ModelEndpoint:
    """Resolved local vLLM endpoint configuration for a model tier."""

    base_url: str
    model: str
    thinking: bool = False
    max_tokens: int = 768
    timeout_s: float = 120.0


#: Tier → model id, environment-overridable.
#:
#: A ``dict`` lookup keyed by an enum member, exactly as
#: :data:`CHAT_MODEL_SLOTS` above. That shape is deliberate and load-bearing:
#: it guarantees the value reaching an outbound model URL originates here and
#: never from request data, which an allowlist check inside a validator does
#: not prove to dataflow analysis.
MODEL_SLOTS: dict[ModelTier, str] = {
    ModelTier.T1: os.getenv("MODEL_T1", os.getenv("LLM_MODEL", "Sunbird/Sunflower-Qwen3.5-9B")),
    ModelTier.T2: os.getenv("MODEL_T2", os.getenv("REASONER_MODEL", "Qwen/Qwen3.8-27B-FP8")),
    ModelTier.T3: os.getenv("MODEL_T3", os.getenv("REASONER_MODEL", "Qwen/Qwen3.8-27B-FP8")),
}


def build_endpoints() -> dict[ModelTier, ModelEndpoint]:
    """Build the tier → :class:`ModelEndpoint` map exclusively from environment variables.

    Keeps the dataflow guarantee of :data:`MODEL_SLOTS`: outbound URLs and model
    identifiers originate only from this environment-constructed mapping, never
    from request input. When ``REASONER_BASE_URL`` is unset, ``T2`` and ``T3``
    fall back to the ``T1`` endpoint.
    """
    t1_base_url = (os.getenv("VLLM_BASE_URL") or "http://localhost:8001/v1").strip()
    t1_model = (
        os.getenv("MODEL_T1") or os.getenv("LLM_MODEL") or "Sunbird/Sunflower-Qwen3.5-9B"
    ).strip()
    try:
        t1_max_out = int(os.getenv("LLM_MAX_TOKENS") or "768")
    except ValueError:
        t1_max_out = 768
    try:
        t1_timeout = float(os.getenv("VLLM_HTTP_TIMEOUT") or "60.0")
    except ValueError:
        t1_timeout = 60.0

    t1_endpoint = ModelEndpoint(
        base_url=t1_base_url,
        model=t1_model,
        thinking=False,
        max_tokens=t1_max_out,
        timeout_s=t1_timeout,
    )

    reasoner_base_url = (os.getenv("REASONER_BASE_URL") or "").strip()
    if not reasoner_base_url:
        return {
            ModelTier.T1: t1_endpoint,
            ModelTier.T2: t1_endpoint,
            ModelTier.T3: t1_endpoint,
        }

    reasoner_model = (
        os.getenv("REASONER_MODEL")
        or os.getenv("MODEL_T3")
        or "Qwen/Qwen3.8-27B-FP8"
    ).strip()
    reasoner_thinking = (os.getenv("REASONER_THINKING") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    default_reasoner_budget = 4096 if reasoner_thinking else 1536
    try:
        reasoner_max_out = int(os.getenv("REASONER_MAX_TOKENS") or str(default_reasoner_budget))
    except ValueError:
        reasoner_max_out = default_reasoner_budget
    try:
        reasoner_timeout = float(os.getenv("REASONER_DEADLINE_S") or "60.0")
    except ValueError:
        reasoner_timeout = 60.0

    t3_endpoint = ModelEndpoint(
        base_url=reasoner_base_url,
        model=reasoner_model,
        thinking=reasoner_thinking,
        max_tokens=reasoner_max_out,
        timeout_s=reasoner_timeout,
    )
    t2_endpoint = ModelEndpoint(
        base_url=reasoner_base_url,
        model=reasoner_model,
        thinking=False,
        max_tokens=min(reasoner_max_out, 1536),
        timeout_s=reasoner_timeout,
    )
    return {
        ModelTier.T1: t1_endpoint,
        ModelTier.T2: t2_endpoint,
        ModelTier.T3: t3_endpoint,
    }


ENDPOINTS: dict[ModelTier, ModelEndpoint] = build_endpoints()


def sync_endpoints() -> dict[ModelTier, ModelEndpoint]:
    """Refresh :data:`ENDPOINTS` and :data:`MODEL_SLOTS` in-place from current env vars."""
    fresh = build_endpoints()
    ENDPOINTS.clear()
    ENDPOINTS.update(fresh)
    MODEL_SLOTS[ModelTier.T1] = os.getenv(
        "MODEL_T1", os.getenv("LLM_MODEL", "Sunbird/Sunflower-Qwen3.5-9B")
    )
    MODEL_SLOTS[ModelTier.T2] = os.getenv(
        "MODEL_T2", os.getenv("REASONER_MODEL", "Qwen/Qwen3.8-27B-FP8")
    )
    MODEL_SLOTS[ModelTier.T3] = os.getenv(
        "MODEL_T3", os.getenv("REASONER_MODEL", "Qwen/Qwen3.8-27B-FP8")
    )
    return ENDPOINTS


def get_endpoint(tier: ModelTier) -> ModelEndpoint | None:
    """Return the resolved :class:`ModelEndpoint` for *tier* (``None`` for ``T0``)."""
    if tier is ModelTier.T0:
        return None
    sync_endpoints()
    return ENDPOINTS.get(tier, ENDPOINTS[ModelTier.T1])


#: Locales pinned to T1 unless ``reasoner_for_translated_locales`` is enabled.
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

#: Rolling 200-turn window used to enforce REASONER_MAX_SHARE (default 0.25).
import collections
import threading

_TIER_WINDOW_MAXLEN = 200
_RECENT_TIER_WINDOW: collections.deque[ModelTier] = collections.deque(maxlen=_TIER_WINDOW_MAXLEN)
_TIER_WINDOW_LOCK = threading.Lock()


def reset_tier_window() -> None:
    """Clear the rolling T3 share window (for unit tests)."""
    with _TIER_WINDOW_LOCK:
        _RECENT_TIER_WINDOW.clear()


def record_tier_turn(tier: ModelTier) -> float:
    """Record a completed non-T0 turn into the rolling 200-turn window and return T3 share."""
    if tier is ModelTier.T0:
        return current_t3_share()
    with _TIER_WINDOW_LOCK:
        _RECENT_TIER_WINDOW.append(tier)
        total = len(_RECENT_TIER_WINDOW)
        t3_count = sum(1 for t in _RECENT_TIER_WINDOW if t is ModelTier.T3)
        share = (t3_count / total) if total else 0.0
    try:
        metrics.set_gauge("model_tier_t3_rolling_share", share)
    except Exception:
        pass
    return share


def current_t3_share() -> float:
    """Return the current rolling T3 share across the last up-to-200 turns."""
    with _TIER_WINDOW_LOCK:
        total = len(_RECENT_TIER_WINDOW)
        if not total:
            return 0.0
        t3_count = sum(1 for t in _RECENT_TIER_WINDOW if t is ModelTier.T3)
        return t3_count / total


def is_reasoner_budget_exhausted() -> bool:
    """Return True when the rolling T3 share in the last 200 turns exceeds REASONER_MAX_SHARE."""
    try:
        max_share = float(os.getenv("REASONER_MAX_SHARE") or "0.25")
    except ValueError:
        max_share = 0.25
    if max_share <= 0.0:
        return True
    if max_share >= 1.0:
        return False
    with _TIER_WINDOW_LOCK:
        total = len(_RECENT_TIER_WINDOW)
        if total < 4:
            return False
        t3_count = sum(1 for t in _RECENT_TIER_WINDOW if t is ModelTier.T3)
        return (t3_count / total) > max_share


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
        if self.tier is ModelTier.T0:
            return ""
        ep = self.endpoint
        if ep is not None and (os.getenv("REASONER_BASE_URL") or "").strip():
            return ep.model
        return MODEL_SLOTS.get(self.tier, "")

    @property
    def endpoint(self) -> ModelEndpoint | None:
        """Resolved :class:`ModelEndpoint` for this decision (``None`` for ``T0``)."""
        return get_endpoint(self.tier)


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
    query: str = "",
    confidence: float = 1.0,
    tool_count: int = 0,
    locale: str = "en",
    escalation_reason: str = "",
    distress: str = "",
    multi_hop: bool = False,
    evaluator_rejected: bool = False,
    budget_exhausted: bool | None = None,
    enabled: bool = True,
    planner_tool_count: int = 0,
    premise_contradiction: bool = False,
    tax_heads_count: int = 0,
    allow_translated_locales: bool | None = None,
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
    sync_endpoints()
    if not enabled:
        return TierDecision(ModelTier.T1, "tiering disabled")

    tier, reason = _base_tier(route, tool_count)

    # T0 is a statement that no model runs at all. Nothing promotes it —
    # a greeting from a frustrated user is still a greeting.
    if tier is ModelTier.T0:
        return TierDecision(tier, reason)

    lang = (locale or "en").strip().lower().split("-")[0].split("_")[0]
    if lang in ADAPTER_BOUND_LOCALES:
        if allow_translated_locales is None:
            try:
                from ..flags import flags

                allow_translated_locales = flags.is_enabled("reasoner_for_translated_locales")
            except Exception:
                allow_translated_locales = False
        if not allow_translated_locales:
            return TierDecision(ModelTier.T1, f"{lang} is bound to the T1 LoRA adapters")

    promotions: list[tuple[ModelTier, str]] = []
    if query:
        import re

        q_lower = query.lower()
        statutory_concepts = sum(
            1
            for c in ("wht", "withholding", "vat", "paye", "income tax", "rental", "customs", "excise", "efris")
            if re.search(rf"\b{c}\b", q_lower)
        )
        is_cross_border = bool(
            re.search(
                r"\b(cross[-\s]?border|non[-\s]?resident|foreign|abroad|imported\s+services?|reverse\s+charge|digital\s+services?)\b",
                q_lower,
            )
        )
        is_dispute = bool(
            re.search(r"\b(dispute|disputed|objection|appeal|default\s+assessment|tribunal|tat)\b", q_lower)
        )
        has_complex_numbers = (
            len(re.findall(r"\b\d+(?:[,\.]\d+)?\s*(?:m|million|bn|billion|k|thousand|ugx|usd|\$)\b", q_lower)) >= 2
        )
        asks_how_why_explain = bool(
            re.search(
                r"\b(how|why|explain|walk\s+me\s+through|what\s+is\s+the\s+(?:rule|basis|formula|method))\b",
                q_lower,
            )
        )

        if statutory_concepts >= 2:
            promotions.append((ModelTier.T3, "multiple statutory concepts"))
        elif is_cross_border:
            promotions.append((ModelTier.T3, "cross-border / non-resident statutory transaction"))
        elif is_dispute:
            promotions.append((ModelTier.T3, "tax dispute / objection / assessment procedure"))
        elif has_complex_numbers:
            promotions.append((ModelTier.T3, "complex financial reconciliation"))
        elif planner_tool_count >= 1 and asks_how_why_explain:
            promotions.append((ModelTier.T3, "planner tool output with statutory explanation"))
    elif planner_tool_count >= 1:
        promotions.append((ModelTier.T3, "planner tool output with statutory explanation"))

    if premise_contradiction:
        promotions.append((ModelTier.T3, "premise contradiction in case facts"))
    if tax_heads_count >= 2:
        promotions.append((ModelTier.T3, f"multi-head conversation context ({tax_heads_count} tax heads)"))
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

    exhausted = is_reasoner_budget_exhausted() if budget_exhausted is None else budget_exhausted
    if exhausted and _TIER_ORDER[tier] > _TIER_ORDER[ModelTier.T2]:
        tier = ModelTier.T2
        reason = f"{reason} (capped: T3 budget exhausted)"

    if tier in (ModelTier.T2, ModelTier.T3) and not (os.getenv("REASONER_BASE_URL") or "").strip():
        reason = f"{reason} (REASONER_BASE_URL unset; falling back to T1 endpoint)"

    return TierDecision(tier, reason, promoted)


def log_tier(task: str, decision: TierDecision) -> None:
    """Record the tier that served *task* (Prometheus ``model_tier_total``) and update rolling share."""
    record_tier_turn(decision.tier)
    try:
        metrics.inc(
            "model_tier_total",
            labels={"task": task, "tier": decision.tier.value,
                    "promoted": str(decision.promoted).lower()},
        )
    except Exception:
        pass


def log_tier_fallback(reason: str = "deadline") -> None:
    """Record a reasoning-tier fallback to T1 (Prometheus ``model_tier_fallback_total``)."""
    try:
        metrics.inc("model_tier_fallback_total", labels={"reason": reason})
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
