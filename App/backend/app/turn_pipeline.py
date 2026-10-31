"""Composable Turn Pipeline, Stage SLA Contracts & Behavioral Attestation (October 2026).

Addresses ``MOD-GAP-01`` in line with:
- **ISO/IEC 25010:2023 §4.2.8.1 (Modularity & Analysability)**
- **AgentSLA (arXiv:2511.02885)**: Per-stage QoS latency budgets and machine-verifiable receipts
- **OWASP Top 10 for Agentic Applications 2026 (`ASI10` — Rogue Agents)**:
  A hashed behavioral manifest of the declared 7-stage order.

:func:`attest_turn` holds each turn, on every transport, to that manifest from the
stage timings it actually recorded, and the verdict goes into its audit row. Stage 6
is evidence, not an assumption: a turn that served model text must show a guard that
ran. Only a turn that served none (a calculator, a canned refusal) may be attested on
the strength of its audit row.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TurnStage(str, Enum):
    """Canonical 7-stage execution pipeline shared across REST and SSE/WebSocket transports."""

    STAGE_0_CONTEXT_AND_LOCALE = "stage_0_context_and_locale"
    STAGE_1_INPUT_GUARD = "stage_1_input_guard"
    STAGE_2_WORKFLOW_AND_FAST_PATH = "stage_2_workflow_and_fast_path"
    STAGE_3_SUPERVISOR_AND_PLANNER = "stage_3_supervisor_and_planner"
    STAGE_4_HYBRID_RETRIEVAL_AND_WEB = "stage_4_hybrid_retrieval_and_web"
    STAGE_5_SYNTHESIS_AND_LOCALIZATION = "stage_5_synthesis_and_localization"
    STAGE_6_OUTPUT_GUARD_AND_AUDIT = "stage_6_output_guard_and_audit"


#: Default per-stage SLA budgets in milliseconds (`AgentSLA`, arXiv:2511.02885).
DEFAULT_STAGE_SLA_BUDGETS_MS: dict[TurnStage, float] = {
    TurnStage.STAGE_0_CONTEXT_AND_LOCALE: 50.0,
    TurnStage.STAGE_1_INPUT_GUARD: 25.0,
    TurnStage.STAGE_2_WORKFLOW_AND_FAST_PATH: 120.0,
    TurnStage.STAGE_3_SUPERVISOR_AND_PLANNER: 100.0,
    TurnStage.STAGE_4_HYBRID_RETRIEVAL_AND_WEB: 2500.0,
    TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION: 12000.0,
    TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT: 150.0,
}


@dataclass(frozen=True)
class BehavioralManifest:
    """Declared behavioral contract for the URA Chatbot agent (`OWASP ASI10`)."""

    version: str = "2026.10.22"
    ordered_stages: tuple[TurnStage, ...] = (
        TurnStage.STAGE_0_CONTEXT_AND_LOCALE,
        TurnStage.STAGE_1_INPUT_GUARD,
        TurnStage.STAGE_2_WORKFLOW_AND_FAST_PATH,
        TurnStage.STAGE_3_SUPERVISOR_AND_PLANNER,
        TurnStage.STAGE_4_HYBRID_RETRIEVAL_AND_WEB,
        TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION,
        TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
    )
    mandatory_guard_stages: frozenset[TurnStage] = frozenset(
        {
            TurnStage.STAGE_1_INPUT_GUARD,
            TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
        }
    )

    def digest_sha256(self) -> str:
        payload = {
            "version": self.version,
            "ordered_stages": [s.value for s in self.ordered_stages],
            "mandatory_guard_stages": sorted(s.value for s in self.mandatory_guard_stages),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


CANONICAL_BEHAVIORAL_MANIFEST = BehavioralManifest()

#: ``trace_stage`` names, by the pipeline stage each belongs to. Abstention is
#: synthesis: on a tool turn it runs after the agentic model call.
_TRACE_STAGES: dict[str, TurnStage] = {
    "query_rewrite": TurnStage.STAGE_0_CONTEXT_AND_LOCALE,
    "lang_detect": TurnStage.STAGE_0_CONTEXT_AND_LOCALE,
    "router_translate": TurnStage.STAGE_0_CONTEXT_AND_LOCALE,
    "input_guard": TurnStage.STAGE_1_INPUT_GUARD,
    "workflow_router": TurnStage.STAGE_2_WORKFLOW_AND_FAST_PATH,
    "calculator_router": TurnStage.STAGE_2_WORKFLOW_AND_FAST_PATH,
    "cache_lookup": TurnStage.STAGE_2_WORKFLOW_AND_FAST_PATH,
    "supervisor": TurnStage.STAGE_3_SUPERVISOR_AND_PLANNER,
    "hybrid_search": TurnStage.STAGE_4_HYBRID_RETRIEVAL_AND_WEB,
    "keyword_search_fallback": TurnStage.STAGE_4_HYBRID_RETRIEVAL_AND_WEB,
    "corrective_rag": TurnStage.STAGE_4_HYBRID_RETRIEVAL_AND_WEB,
    "faq_blend": TurnStage.STAGE_4_HYBRID_RETRIEVAL_AND_WEB,
    "langgraph_execution": TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION,
    "llm_agentic": TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION,
    "abstention_check": TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION,
    "llm_generate": TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION,
    "llm_stream": TurnStage.STAGE_5_SYNTHESIS_AND_LOCALIZATION,
    "output_guard": TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
    "grounding": TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
    "claim_verification": TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
    "numeric_verification": TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
    "numeric_revision": TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
    "self_reflect": TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
}


#: Stages that put model text in front of a taxpayer unless something stops it.
_GENERATION_STAGES = frozenset({"llm_generate", "llm_agentic", "llm_stream", "langgraph_execution"})


@dataclass
class StageExecutionReceipt:
    """Machine-verifiable telemetry receipt for a single pipeline stage."""

    stage: TurnStage
    duration_ms: float
    budget_ms: float
    within_sla: bool
    short_circuited: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "duration_ms": round(self.duration_ms, 3),
            "budget_ms": round(self.budget_ms, 3),
            "within_sla": self.within_sla,
            "short_circuited": self.short_circuited,
            "metadata": dict(self.metadata),
        }


@dataclass
class TurnPipelineTrace:
    """Accumulates stage execution receipts and verifies behavioral integrity (`OWASP ASI10`)."""

    transport: str = "rest"  # "rest" | "sse" | "websocket" | "voice"
    manifest: BehavioralManifest = CANONICAL_BEHAVIORAL_MANIFEST
    receipts: list[StageExecutionReceipt] = field(default_factory=list)

    def record_stage(
        self,
        stage: TurnStage,
        duration_ms: float,
        *,
        short_circuited: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> StageExecutionReceipt:
        budget = DEFAULT_STAGE_SLA_BUDGETS_MS.get(stage, 1000.0)
        receipt = StageExecutionReceipt(
            stage=stage,
            duration_ms=max(0.0, float(duration_ms)),
            budget_ms=budget,
            within_sla=float(duration_ms) <= budget,
            short_circuited=bool(short_circuited),
            metadata=dict(metadata or {}),
        )
        self.receipts.append(receipt)
        return receipt

    def verify_behavioral_integrity(self) -> dict[str, Any]:
        """Verify stage ordering and mandatory guardrail execution (`OWASP ASI10`)."""
        executed = [r.stage for r in self.receipts]
        order_index = {s: idx for idx, s in enumerate(self.manifest.ordered_stages)}
        indices = [order_index[s] for s in executed if s in order_index]
        monotonic = indices == sorted(indices)
        missing_guards = sorted(
            s.value for s in self.manifest.mandatory_guard_stages if s not in set(executed)
        )
        sla_breaches = [r.stage.value for r in self.receipts if not r.within_sla]
        return {
            "ok": monotonic and not missing_guards,
            "transport": self.transport,
            "manifest_sha256": self.manifest.digest_sha256(),
            "monotonic_stage_order": monotonic,
            "missing_mandatory_guards": missing_guards,
            "sla_breaches": sla_breaches,
            "stage_count": len(self.receipts),
            "total_duration_ms": round(sum(r.duration_ms for r in self.receipts), 3),
        }


def attest_turn(
    timings: Mapping[str, float],
    *,
    transport: str = "rest",
    served_model_text: bool | None = None,
) -> dict[str, Any]:
    """Hold one finished turn to the manifest, from the stages it really ran.

    *timings* is ``trace_stage``'s record, in completion order. Runs of the same
    stage are summed, so a stage that reappears after a later one shows up as an
    order violation rather than being tidied away. Live research is left out of
    Stage 4's SLA: it has its own wall-clock budget, and that is what bounds it.

    Reaching this function is the audit half of Stage 6 and nothing more. It used
    to add Stage 6 for any turn that recorded none, so a mandatory guard could not
    be reported missing and the graph path, which ran no output guard at all,
    attested ``ok``. A turn that served model text now needs a guard stage to show
    for it. *served_model_text* overrides the inference from *timings*, for a model
    call whose output was discarded (the tool loop ran, then the turn abstained).
    """
    trace = TurnPipelineTrace(transport=transport)
    grouped: list[tuple[TurnStage, float]] = []
    for name, duration_ms in timings.items():
        stage = _TRACE_STAGES.get(name)
        if stage is None:
            continue
        if grouped and grouped[-1][0] is stage:
            grouped[-1] = (stage, grouped[-1][1] + float(duration_ms))
        else:
            grouped.append((stage, float(duration_ms)))
    for stage, duration_ms in grouped:
        trace.record_stage(stage, duration_ms)
    generated = (
        any(name in timings for name in _GENERATION_STAGES)
        if served_model_text is None
        else served_model_text
    )
    guarded = TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT in {stage for stage, _ in grouped}
    if not guarded and not generated:
        trace.record_stage(TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT, 0.0, metadata={"audit_only": True})

    verdict = trace.verify_behavioral_integrity()
    verdict["guard_basis"] = "verified" if guarded else "no_model_text"
    try:
        from .analytics import metrics

        metrics.inc(
            "turn_pipeline_attestations_total",
            labels={"transport": transport, "ok": str(verdict["ok"]).lower()},
        )
        for stage in verdict["sla_breaches"]:
            metrics.inc("turn_stage_sla_breaches_total", labels={"stage": stage})
    except Exception:  # noqa: BLE001 - telemetry must never fail a turn
        logger.debug("pipeline attestation metrics not recorded", exc_info=True)
    return verdict


class TurnPipelineOrchestrator:
    """Executes registered stage callables in canonical order while recording `AgentSLA` receipts."""

    def __init__(self, manifest: BehavioralManifest = CANONICAL_BEHAVIORAL_MANIFEST) -> None:
        self.manifest = manifest
        self._handlers: dict[TurnStage, Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def register_stage(
        self,
        stage: TurnStage,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._handlers[stage] = handler

    def run(
        self,
        initial_state: dict[str, Any],
        *,
        transport: str = "rest",
        raise_on_error: bool = False,
    ) -> tuple[dict[str, Any], TurnPipelineTrace]:
        state = dict(initial_state)
        trace = TurnPipelineTrace(transport=transport, manifest=self.manifest)
        caught_exc: Exception | None = None
        for stage in self.manifest.ordered_stages:
            handler = self._handlers.get(stage)
            if handler is None:
                continue
            t0 = time.perf_counter()
            try:
                out = handler(state)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                state["_stage_error"] = f"{stage.value}: {type(exc).__name__}: {exc}"
                trace.record_stage(
                    stage,
                    elapsed_ms,
                    short_circuited=True,
                    metadata={
                        "route": state.get("route", ""),
                        "failed": True,
                        "error_type": type(exc).__name__,
                    },
                )
                caught_exc = exc
                if stage != TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT:
                    audit_handler = self._handlers.get(TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT)
                    if audit_handler is not None:
                        t_audit = time.perf_counter()
                        try:
                            audit_out = audit_handler(state)
                            if isinstance(audit_out, dict):
                                state.update(audit_out)
                        finally:
                            trace.record_stage(
                                TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
                                (time.perf_counter() - t_audit) * 1000.0,
                                metadata={"route": state.get("route", ""), "after_error": True},
                            )
                break

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if isinstance(out, dict):
                state.update(out)
            short_circuit = bool(state.get("_short_circuit")) and stage != TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT
            trace.record_stage(
                stage,
                elapsed_ms,
                short_circuited=short_circuit,
                metadata={"route": state.get("route", "")},
            )
            if short_circuit:
                # Even on a fast-path or early return, Stage 6 (OutputGuard + Audit) MUST run
                audit_handler = self._handlers.get(TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT)
                if audit_handler is not None and stage != TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT:
                    t_audit = time.perf_counter()
                    audit_out = audit_handler(state)
                    if isinstance(audit_out, dict):
                        state.update(audit_out)
                    trace.record_stage(
                        TurnStage.STAGE_6_OUTPUT_GUARD_AND_AUDIT,
                        (time.perf_counter() - t_audit) * 1000.0,
                        metadata={"route": state.get("route", ""), "after_short_circuit": True},
                    )
                break
        if caught_exc is not None and raise_on_error:
            raise caught_exc
        return state, trace
