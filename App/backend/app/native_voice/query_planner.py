"""Query planner — route between fast and grounded voice paths (2026).

Examines the ASR transcript and decides which execution path to take:

- **FAST**: Greeting, semantic cache hit, or simple acknowledgement.
  Skips RAG entirely → reply in < 400ms.
- **GROUNDED**: Needs retrieval + LLM synthesis.  Full 21-phase pipeline
  with speculative prefetch → reply in < 800ms.
- **VISION**: Image attached — needs parallel vision encoder + RAG.
- **ESCALATE**: Hand off to human agent (out of scope, sensitive, etc.).

The planner delegates to the existing :class:`Supervisor` for
classification and checks the semantic cache for near-duplicate queries.

Feature flag: ``native_voice``
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..agents.supervisor import Supervisor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path enum
# ---------------------------------------------------------------------------


class VoicePath(str, Enum):
    """Execution path for a voice query."""

    FAST = "fast"  # cached / greeting / simple — skip RAG
    GROUNDED = "grounded"  # needs retrieval + LLM
    VISION = "vision"  # needs vision encoder + RAG
    ESCALATE = "escalate"  # hand off to human


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathDecision:
    """Routing decision with reason and optional cached data."""

    path: VoicePath
    reason: str
    confidence: float = 1.0
    prefetch_hits: list[dict] | None = None
    cached_reply: dict | None = None  # if semantic cache hit


# ---------------------------------------------------------------------------
# Acknowledgement patterns (fast-path without retrieval)
# ---------------------------------------------------------------------------

_ACK_PATTERNS = re.compile(
    r"^(ok(ay)?|thanks?(\s+you)?|thank\s+you|got\s+it|"
    r"alright|good(bye)?|bye|sure|yes|no|webale(\s+nyo)?|"
    r"kale|yee|nedda)[\s.!?]*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# QueryPlanner
# ---------------------------------------------------------------------------


class QueryPlanner:
    """Decides fast vs grounded execution path for voice queries.

    Args:
        supervisor: The existing :class:`Supervisor` instance for
            rule-based query classification.
        cache: Optional semantic cache (``service._SemanticCache``)
            for near-duplicate query detection.
    """

    def __init__(self, supervisor, cache=None) -> None:
        self._supervisor = supervisor
        self._cache = cache

    def plan(
        self,
        transcript: str,
        *,
        has_image: bool = False,
        has_conversation_history: bool = False,
        prefetch_hits: list[dict] | None = None,
        locale: str = "en",
    ) -> PathDecision:
        """Classify *transcript* and return a routing decision.

        This is designed to be fast (< 2ms) — no model calls, no I/O.
        """
        t0 = time.perf_counter()

        # Vision path takes precedence when an image is present
        if has_image:
            return PathDecision(
                VoicePath.VISION,
                "image attached — vision+RAG path",
                confidence=1.0,
                prefetch_hits=prefetch_hits,
            )

        text = transcript.strip()

        # Quick acknowledgement detection
        if _ACK_PATTERNS.match(text):
            return PathDecision(
                VoicePath.FAST,
                f"acknowledgement: {text[:20]}",
                confidence=0.95,
            )

        # Supervisor classification (rule-based, < 1ms)
        from ..agents.state import AgentRoute

        decision = self._supervisor.classify(text, has_conversation_history)

        if decision.route == AgentRoute.GREET and decision.confidence >= 0.85:
            return PathDecision(
                VoicePath.FAST,
                f"greeting (conf={decision.confidence:.2f})",
                confidence=decision.confidence,
            )

        if decision.route == AgentRoute.ESCALATE:
            return PathDecision(
                VoicePath.ESCALATE,
                decision.reason,
                confidence=decision.confidence,
            )

        if decision.route == AgentRoute.CLARIFY:
            return PathDecision(
                VoicePath.FAST,
                "clarification needed",
                confidence=decision.confidence,
            )

        # Semantic cache check (optional, < 5ms)
        if self._cache is not None:
            try:
                cached = self._cache.get(text, locale)
                if cached is not None:
                    return PathDecision(
                        VoicePath.FAST,
                        "semantic cache hit",
                        confidence=0.9,
                        cached_reply=cached,
                    )
            except Exception:
                logger.debug("Semantic cache lookup failed", exc_info=True)

        # Default: grounded path through full RAG pipeline
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.debug(
            "QueryPlanner → GROUNDED (route=%s, conf=%.2f, plan_ms=%.1f)",
            decision.route.value,
            decision.confidence,
            elapsed_ms,
        )

        return PathDecision(
            VoicePath.GROUNDED,
            f"route={decision.route.value}",
            confidence=decision.confidence,
            prefetch_hits=prefetch_hits,
        )
