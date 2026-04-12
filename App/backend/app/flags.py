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
