"""One turn's live web research, shared by REST, SSE and the graph (G486).

Moved out of ``service.py``: the three paths drifted once, and the graph reached into the
orchestrator to share it. The check is recorded in :data:`app.turn_context.TURN_LIVE_CHECK`.
"""

from __future__ import annotations

import logging
from typing import Any

from .turn_context import ACTIVE_CASE_FACTS as _ACTIVE_CASE_FACTS
from .turn_context import TURN_LIVE_CHECK

logger = logging.getLogger(__name__)


def live_research_for_turn(
    query: str,
    *,
    hits: list[dict[str, Any]],
    retriever: Any,
    turn_id: str,
) -> tuple[list[dict[str, Any]], str, Any]:
    """Run the turn's live research: ``(hits, retrieval mode, outcome)``.

    One implementation for REST, SSE and the graph, because the three drifted
    once already. A crash is reported as a failed check when the question needed
    one: going quiet is how a stale answer comes to look current.
    """
    from .web_research import (
        WebResearchTurnOutcome,
        merge_into_hits,
        query_is_time_sensitive,
        run_web_research,
        unavailable_outcome,
    )

    facts = _ACTIVE_CASE_FACTS.get()
    try:
        outcome = run_web_research(
            query,
            case_facts=facts,
            hits=hits,
            retriever=retriever,
            turn_id=turn_id,
        )
        merged, mode = merge_into_hits(outcome, hits)
    except Exception:
        logger.warning("Live web research step failed", exc_info=True)
        outcome = unavailable_outcome() if query_is_time_sensitive(query, facts) else WebResearchTurnOutcome()
        merged, mode = hits, ""
    # Recorded here, outside any return path, so a stream that exits early still
    # has the check to disclose and audit.
    record = TURN_LIVE_CHECK.get()
    if record is not None:
        record.update(
            audit=outcome.audit,
            disclosure=outcome.disclosure,
            events=list(outcome.events),
        )
    return merged, mode, outcome


def with_live_check_disclosure(reply: str, disclosure: str) -> str:
    """*reply* with the live-check sentence owed to the taxpayer, once."""
    if not disclosure or not reply or disclosure in reply:
        return reply
    return f"{reply.rstrip()}\n\n{disclosure}"
