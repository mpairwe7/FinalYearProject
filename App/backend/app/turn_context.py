"""Turn-scoped context: who and what this chat turn is about (ADR-0031, G484).

These lived in ``service.py``, the 15,000-line orchestrator. So the security
layer (``guardrails.OutputGuard.redact_pii``), the evaluator and the empathy
tool each imported the orchestrator to read one value. The lowest layer of the
system depended on the highest, and a failed import there fell back silently,
which in redaction meant the taxpayer's own identifiers were masked. This
module is a leaf: it imports nothing from the app, so anything may read turn
context without reaching up. ``service`` re-exports the same objects under the
same names, so existing callers keep working.

Each value is a :class:`contextvars.ContextVar`, scoped to the turn's task.
``asyncio.to_thread`` copies the context, and a raw ``threading.Thread`` must
be started under ``contextvars.copy_context().run``, as the streaming pump does.
"""

from __future__ import annotations

import contextvars
from typing import Any

#: The conversation whose state this turn reads and writes, as the state key.
ACTIVE_CONVERSATION_STATE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_conversation_state", default=""
)
#: The tenant that conversation belongs to.
ACTIVE_TENANT_ID: contextvars.ContextVar[str] = contextvars.ContextVar("active_conversation_tenant", default="")
#: TINs and PRNs the taxpayer typed this turn; echoing them back is not a disclosure (G143).
ACTIVE_USER_STATED_IDS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "active_user_stated_ids", default=()
)
#: The case facts the planner bound for this turn, if any.
ACTIVE_CASE_FACTS: contextvars.ContextVar[Any] = contextvars.ContextVar("_active_case_facts", default=None)

#: This turn's live check, shared with the threads the streamed turn runs in.
TURN_LIVE_CHECK: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_turn_live_check", default=None
)


#: What produced this turn's words, for the audit row. A deployment can answer from
#: a local model, a reasoner, Gemini or Workers AI, or fall back to a raw passage,
#: and ``result["model"]`` names the configured model whichever of those ran, so the
#: ledger could not say who wrote an answer or whether it left the country (DPPA
#: 2019 s.19). The dict is shared by reference with the turn's threads.
TURN_GENERATION: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_turn_generation", default=None
)

#: The kernel release, ruleset and as-of date this turn answers under
#: (ADR-0033), as a plain dict so this module stays a leaf. Set at turn start,
#: recorded in the audit row and carried into graph state.
ACTIVE_KERNEL_PIN: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "active_kernel_pin", default=None
)

__all__ = [
    "ACTIVE_CASE_FACTS",
    "ACTIVE_KERNEL_PIN",
    "ACTIVE_CONVERSATION_STATE",
    "ACTIVE_TENANT_ID",
    "ACTIVE_USER_STATED_IDS",
    "TURN_GENERATION",
    "TURN_LIVE_CHECK",
]
