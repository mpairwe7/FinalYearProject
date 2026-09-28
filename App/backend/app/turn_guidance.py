"""Conversational guidance applied to every finished turn.

Three decisions that belong to no single branch of the chat pipeline — the
REST path (``ChatModel.generate``) and the streaming core (``run_chat_turn``)
both call :func:`apply_turn_guidance` right after ``resources_for_turn``, so
the dozen exits (workflow, calculator, FAQ, generated, …) behave alike:

* **Offer guided mode.** A "How do I file my return?" question is answered,
  not captured as a task (G39), which is right. But the answer never said a
  step-by-step guide exists, so on the local stack (2026-09-29) only people
  who happened to type "help me …" or "guide me …" ever saw a stepper. When
  the question names a task a flow covers, the turn now carries a next action
  that starts it; its text contains "guide me" and the flow's name, which is
  exactly what the workflow router needs to start that flow on click.
* **Do not repeat the empathy opener.** The openers are fixed sentences; a
  worried taxpayer answered three times read the same line three times.
* **Offer a person when distress persists.** One upset message is handled by
  tone. The same upset across consecutive turns means the answers are not
  landing, and another rephrasing is the wrong move.

Pure and deterministic apart from reading the workflow registry and flags.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .flags import flags
from .text_signals import detect_user_distress, distress_trajectory, strip_repeated_ack
from .workflows.registry import WorkflowRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: The next action that asks for an officer. Its wording matches the
#: supervisor's "talk to … officer" escalation rule, so a click routes
#: straight to the ticket queue.
HANDOFF_ACTION = "Talk to an officer"

#: Prefix of every guided-mode offer; the flow's name follows it.
GUIDE_PREFIX = "Guide me step by step through "

#: The turns that answer a question, and so may carry a guided-mode offer. An
#: allow-list, not a deny-list: a deny-list missed out_of_jurisdiction ("How do
#: I file a return in Kenya?" was offered the *Uganda* filing flow),
#: out_of_scope and officer_reply, and any mode added later would have been
#: offered too. An abstention is included on purpose: "I couldn't find a
#: reliable answer" with a step-by-step guide under it is a way forward.
_OFFER_MODES = frozenset(
    {
        "hybrid",
        "hybrid_corrected",
        "keyword",
        "vector",
        "faq_priority",
        "graph",
        "agentic",
        "education",
        "abstained",
    }
)


def guided_mode_action(message: str, result: Mapping[str, Any], rewritten: str = "") -> str:
    """The next action that starts the flow this question is about, or ``""``."""
    if not flags.is_enabled("workflows"):
        return ""
    if result.get("workflow") or result.get("escalation_required"):
        return ""
    if str(result.get("retrieval_mode") or "") not in _OFFER_MODES:
        return ""
    wf = WorkflowRegistry.match_trigger(message)
    if wf is None and rewritten:
        wf = WorkflowRegistry.match_trigger(rewritten)
    if wf is None or not wf.trigger_phrases:
        return ""
    return f"{GUIDE_PREFIX}{wf.name}"


def turns_from_history(history: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Earlier turns as ``{user_message, bot_reply}``, whichever shape *history* has.

    ``database.get_recent_turns`` returns that shape already; the WebSocket
    override path carries chat messages (``role`` / ``content``), which are
    paired up here so the streaming core can reuse the history it has already
    loaded instead of querying the database again.
    """
    turns: list[dict[str, str]] = []
    pending_user = ""
    for item in history or ():
        if "user_message" in item or "bot_reply" in item:
            turns.append(
                {"user_message": str(item.get("user_message") or ""), "bot_reply": str(item.get("bot_reply") or "")}
            )
        elif item.get("role") == "user":
            pending_user = str(item.get("content") or "")
        elif item.get("role") == "assistant":
            turns.append({"user_message": pending_user, "bot_reply": str(item.get("content") or "")})
            pending_user = ""
    return turns


def finalize_turn_actions(result: dict[str, Any], *, escalated: bool) -> dict[str, Any]:
    """Drop actions a later decision made wrong, in place.

    The streaming core decides some escalations after guidance has run (an
    abstention that opens a ticket). Once a person is being brought in, a
    guided-mode offer and a second "talk to an officer" action only compete
    with the handoff.
    """
    if escalated:
        result["next_actions"] = [
            a
            for a in (result.get("next_actions") or [])
            if a != HANDOFF_ACTION and not str(a).startswith(GUIDE_PREFIX)
        ]
    return result


def apply_turn_guidance(
    message: str,
    result: dict[str, Any],
    *,
    rewritten: str = "",
    recent_turns: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Apply the three turn-level decisions to *result* in place and return it.

    *recent_turns* are the conversation's earlier turns, oldest first, as
    ``database.get_recent_turns`` returns them (``user_message`` and
    ``bot_reply``); the current turn must not be among them.
    """
    if not isinstance(result, dict):
        return result
    prior_replies = [str(t.get("bot_reply") or "") for t in recent_turns]
    prior_messages = [str(t.get("user_message") or "") for t in recent_turns]

    reply = result.get("reply")
    if isinstance(reply, str) and reply:
        result["reply"] = strip_repeated_ack(reply, prior_replies)

    actions = [str(a) for a in (result.get("next_actions") or [])]
    offer = guided_mode_action(message, result, rewritten)
    if offer and offer not in actions:
        actions.insert(0, offer)

    if str(result.get("retrieval_mode") or "") not in ("escalated", "crisis_support"):
        # English lexicon: the rewritten (English) form lets a Luganda or
        # Kiswahili turn be read too when machine translation produced one.
        kind = detect_user_distress(rewritten or message)
        if (
            distress_trajectory(kind, prior_messages)["sustained"]
            and not result.get("escalation_required")
            and HANDOFF_ACTION not in actions
        ):
            actions.append(HANDOFF_ACTION)

    result["next_actions"] = actions
    return result
