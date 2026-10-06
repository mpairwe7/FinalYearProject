"""What a call carries over from the chat the caller rang from (G122).

Starting a call from the chat used to start from nothing: a new conversation,
with only the chat's language as a hint, so the AI asked again what the
taxpayer had just typed and the officer's brief knew nothing of it. The call
now carries the chat's task (its G6 topic) and a short English account of it.

Ownership is checked the way conversation history is read: a signed-in caller
must own the chat; a signed-out caller must hold the chat's session id. A
conversation id alone never unlocks anything.

What crosses over is coarse on purpose — the tax topics named, the taxpayer
type, the figures mentioned, and the current task's catalogue label — never
the taxpayer's own words.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .. import database as db

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: The account of the chat handed to the call (and stored with it).
MAX_CONTEXT_CHARS = 600


@dataclass(frozen=True)
class ChatCarryover:
    conversation_id: str
    #: English, coarse: topics, taxpayer type, figures, current task.
    context: str
    topic_id: str = ""
    topic_label: str = ""
    tax_type: str = ""
    locale: str = ""


def chat_carryover(
    parent_conversation_id: str,
    *,
    chat_session: str = "",
    user_id: str = "",
    tenant_id: str = "default",
) -> ChatCarryover | None:
    """The chat's task and summary, when the caller owns that chat; else ``None``."""
    parent = str(parent_conversation_id or "").strip()
    session = str(chat_session or "").strip()
    if not _ID_RE.fullmatch(parent) or (session and not _ID_RE.fullmatch(session)):
        return None
    if not user_id and not session:
        return None
    try:
        turns = db.get_recent_turns(
            session_id=None if user_id else session,
            conversation_id=parent,
            limit=25,
            user_id=user_id or None,
            tenant_id=tenant_id or "default",
        )
    except Exception:
        logger.debug("carryover: chat history unavailable", exc_info=True)
        return None
    if not turns:
        return None

    from ..context_manager import english_view, normalize_history_turns, summarize_older_turns

    english = english_view(normalize_history_turns(turns))
    parts = [summarize_older_turns(english)]
    topic = _chat_topic(parent, session=session, user_id=user_id, tenant_id=tenant_id)
    if topic:
        parts.append(f"Current task: {topic['label']}.")
    context = " ".join(p for p in parts if p).strip()[:MAX_CONTEXT_CHARS]
    locale = next((str(t.get("locale") or "") for t in reversed(turns) if t.get("locale")), "")
    return ChatCarryover(
        conversation_id=parent,
        context=context,
        topic_id=str((topic or {}).get("topic_id") or ""),
        topic_label=str((topic or {}).get("label") or ""),
        tax_type=str((topic or {}).get("tax_type") or ""),
        locale=locale,
    )


def _chat_topic(parent: str, *, session: str, user_id: str, tenant_id: str) -> dict | None:
    try:
        key = db.conversation_state_key(
            parent, session_id=session or None, user_id=user_id or None, tenant_id=tenant_id or "default"
        )
        return db.get_conversation_topic(key)
    except Exception:
        logger.debug("carryover: chat topic unavailable", exc_info=True)
        return None


def seed_call_topic(carry: ChatCarryover, *, conversation_id: str, call_id: str, user_id: str, tenant_id: str) -> None:
    """Start the call on the chat's task, so a short follow-up keeps it (G6)."""
    if not carry.topic_id:
        return
    try:
        key = db.conversation_state_key(
            conversation_id, session_id=call_id, user_id=user_id or None, tenant_id=tenant_id or "default"
        )
        db.upsert_conversation_topic(
            key,
            topic_id=carry.topic_id,
            label=carry.topic_label,
            tax_type=carry.tax_type,
            confidence=0.8,
        )
    except Exception:
        logger.debug("carryover: could not seed the call's topic", exc_info=True)
