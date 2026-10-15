"""Live chat between a taxpayer and the officer who has their case.

An escalated chat used to be a ticket with one ``officer_reply`` field, which
each reply overwrote, read by the taxpayer in a separate Case Room every few
seconds. This module makes it a conversation (docs/plans/
admin-service-quality-audit-plan.md §C1):

* **A thread.** ``ticket_messages`` keeps every message in order — taxpayer,
  officer, and the system's "chat ended" — so the officer, the taxpayer and
  the administrator auditing it all read the same exchange.
* **A state the taxpayer's chat follows.** ``waiting`` (in the queue; the AI
  still answers), ``officer`` (an officer has the case: the taxpayer's
  messages go to them and the AI is paused), ``ended``.
* **An end the taxpayer is told about.** An officer ends the chat; that
  resolves (or closes) the ticket, asks the taxpayer to rate the chat
  (``service_ratings`` ``chat_officer_csat``), and publishes
  ``escalation.chat_ended`` to the staff console.

The ticket row stays as it was; the chat's own state is ``ticket_chats``.
Messages are redacted on write, as the ticket's fields are.
"""

from __future__ import annotations

from collections import OrderedDict
import contextlib
import threading
import time
import uuid
from typing import Any

from . import database as db
from . import service_ratings

#: Who can write a message in the thread.
SENDERS: tuple[str, ...] = ("taxpayer", "officer", "system")

#: How an officer can end a chat: the ticket status it leaves behind.
OUTCOMES: tuple[str, ...] = ("resolved", "wontfix")

#: The ``system`` message that marks the end of a chat in the thread.
ENDED_MARKER = "chat_ended"

_schema_ready = False
_MAX_PENDING_DRAFTS = 256
_pending_copilot_drafts: OrderedDict[str, str] = OrderedDict()
_draft_lock = threading.Lock()


def _store_pending_draft(ticket_id: str, draft_en: str) -> None:
    if not ticket_id or not draft_en:
        return
    with _draft_lock:
        _pending_copilot_drafts[ticket_id] = draft_en
        _pending_copilot_drafts.move_to_end(ticket_id)
        while len(_pending_copilot_drafts) > _MAX_PENDING_DRAFTS:
            _pending_copilot_drafts.popitem(last=False)


def _pop_pending_draft(ticket_id: str) -> str:
    if not ticket_id:
        return ""
    with _draft_lock:
        return _pending_copilot_drafts.pop(ticket_id, "")


def init_schema() -> None:
    """Create ``ticket_messages``, ``ticket_chats``, and ``officer_preference_pairs`` if missing."""
    global _schema_ready
    db.execute_script(
        """
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id              TEXT PRIMARY KEY,
            ticket_id       TEXT NOT NULL,
            seq             INTEGER NOT NULL,
            sender          TEXT NOT NULL,
            text            TEXT NOT NULL DEFAULT '',
            text_localized  TEXT NOT NULL DEFAULT '',
            officer_id      TEXT NOT NULL DEFAULT '',
            officer_name    TEXT NOT NULL DEFAULT '',
            created_at      DOUBLE PRECISION NOT NULL,
            tenant_id       TEXT NOT NULL DEFAULT 'default'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_messages_seq ON ticket_messages(ticket_id, seq);

        CREATE TABLE IF NOT EXISTS ticket_chats (
            ticket_id         TEXT PRIMARY KEY,
            tenant_id         TEXT NOT NULL DEFAULT 'default',
            officer_id        TEXT NOT NULL DEFAULT '',
            officer_name      TEXT NOT NULL DEFAULT '',
            joined_at         DOUBLE PRECISION,
            ended_at          DOUBLE PRECISION,
            ended_by          TEXT NOT NULL DEFAULT '',
            ended_by_name     TEXT NOT NULL DEFAULT '',
            outcome           TEXT NOT NULL DEFAULT '',
            csat_requested_at DOUBLE PRECISION,
            csat_submitted_at DOUBLE PRECISION,
            updated_at        DOUBLE PRECISION NOT NULL
        );

        CREATE TABLE IF NOT EXISTS officer_preference_pairs (
            id                TEXT PRIMARY KEY,
            ticket_id         TEXT NOT NULL,
            tenant_id         TEXT NOT NULL DEFAULT 'default',
            locale            TEXT NOT NULL DEFAULT 'en',
            topic             TEXT NOT NULL DEFAULT '',
            prompt_context    TEXT NOT NULL DEFAULT '',
            chosen_reply      TEXT NOT NULL DEFAULT '',
            rejected_draft    TEXT NOT NULL DEFAULT '',
            edit_ratio        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            officer_id        TEXT NOT NULL DEFAULT '',
            created_at        DOUBLE PRECISION NOT NULL
        );
        """
    )
    _schema_ready = True


def _ensure() -> None:
    if not _schema_ready:
        init_schema()


def _redact(text: str, limit: int = 4000) -> str:
    from .guardrails import redact_pii_text

    return redact_pii_text((text or "").strip())[:limit]


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------
def append_message(
    ticket: dict[str, Any],
    sender: str,
    text: str,
    *,
    text_localized: str = "",
    officer_id: str = "",
    officer_name: str = "",
) -> dict[str, Any]:
    """Add one message to the ticket's thread and return it (with its ``seq``)."""
    if sender not in SENDERS:
        raise ValueError(f"unknown sender {sender!r}")
    _ensure()
    ticket_id = str(ticket["id"])
    row = {
        "id": uuid.uuid4().hex,
        "ticket_id": ticket_id,
        "sender": sender,
        "text": _redact(text),
        "text_localized": _redact(text_localized),
        "officer_id": officer_id or "",
        "officer_name": (officer_name or "")[:128],
        "created_at": time.time(),
        "tenant_id": ticket.get("tenant_id") or "default",
    }
    # seq is the next number in this ticket's thread; the unique index makes
    # two writers that raced to the same number retry rather than collide.
    for _attempt in range(5):
        last = db.query_one("SELECT MAX(seq) AS n FROM ticket_messages WHERE ticket_id = ?", (ticket_id,))
        row["seq"] = int((last or {}).get("n") or 0) + 1
        try:
            db.execute(
                "INSERT INTO ticket_messages (id, ticket_id, seq, sender, text, text_localized, officer_id, "
                "officer_name, created_at, tenant_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], row["ticket_id"], row["seq"], row["sender"], row["text"], row["text_localized"],
                    row["officer_id"], row["officer_name"], row["created_at"], row["tenant_id"],
                ),
            )
            if sender == "officer":
                draft_prev = _pop_pending_draft(ticket_id)
                if draft_prev:
                    with contextlib.suppress(Exception):
                        capture_officer_preference_pair(
                            ticket,
                            ai_draft_en=draft_prev,
                            officer_final_en=row["text"],
                            officer_id=officer_id,
                        )
            return row
        except Exception:
            time.sleep(0.01)
    raise RuntimeError("could not append the message to the ticket thread")


def list_messages(ticket_id: str, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    """The thread after *after_seq*, oldest first."""
    _ensure()
    return db.query_all(
        "SELECT seq, sender, text, text_localized, officer_name, created_at FROM ticket_messages "
        "WHERE ticket_id = ? AND seq > ? ORDER BY seq LIMIT ?",
        (ticket_id, max(0, int(after_seq)), max(1, min(int(limit), 1000))),
    )


def last_seq(ticket_id: str, sender: str | None = None) -> int:
    """The thread's last message number (of *sender*, when given); 0 if none."""
    _ensure()
    if sender:
        row = db.query_one(
            "SELECT MAX(seq) AS n FROM ticket_messages WHERE ticket_id = ? AND sender = ?", (ticket_id, sender)
        )
    else:
        row = db.query_one("SELECT MAX(seq) AS n FROM ticket_messages WHERE ticket_id = ?", (ticket_id,))
    return int((row or {}).get("n") or 0)


def thread_for_staff(ticket_id: str) -> list[dict[str, Any]]:
    """The whole thread as the officer and the auditor read it (English and as delivered)."""
    _ensure()
    return db.query_all(
        "SELECT seq, sender, text, text_localized, officer_name, created_at FROM ticket_messages "
        "WHERE ticket_id = ? ORDER BY seq LIMIT 1000",
        (ticket_id,),
    )


# ---------------------------------------------------------------------------
# The chat's state
# ---------------------------------------------------------------------------
def get_chat(ticket_id: str) -> dict[str, Any] | None:
    _ensure()
    return db.query_one("SELECT * FROM ticket_chats WHERE ticket_id = ?", (ticket_id,))


def _upsert_chat(ticket: dict[str, Any], **fields: Any) -> None:
    ticket_id = str(ticket["id"])
    now = time.time()
    if get_chat(ticket_id) is None:
        try:
            db.execute(
                "INSERT INTO ticket_chats (ticket_id, tenant_id, updated_at) VALUES (?, ?, ?)",
                (ticket_id, ticket.get("tenant_id") or "default", now),
            )
        except Exception:
            pass  # another request created it first
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE ticket_chats SET {sets}, updated_at = ? WHERE ticket_id = ?",  # noqa: S608  # nosec B608 - fixed keys
        (*fields.values(), now, ticket_id),
    )


def note_officer(ticket: dict[str, Any], officer_id: str, officer_name: str, *, took_case: bool = False) -> None:
    """Remember which officer has the chat.

    The first officer to act on it is recorded; an officer who then assigns
    the case to themselves (``took_case``) takes it over, so the taxpayer and
    the audit see who is actually answering.
    """
    if not officer_id:
        return
    chat = get_chat(str(ticket["id"]))
    if chat and chat.get("officer_id") and (not took_case or chat.get("officer_id") == officer_id):
        return
    _upsert_chat(ticket, officer_id=officer_id, officer_name=(officer_name or "")[:128], joined_at=time.time())


def is_text(ticket: dict[str, Any]) -> bool:
    return (ticket.get("modality") or "text") == "text"


def chat_state(ticket: dict[str, Any], chat: dict[str, Any] | None = None) -> str:
    """``waiting``, ``officer``, ``ended`` — or ``none`` for a call's ticket.

    Read from the ticket's own status, so an officer's status change is the
    state: ``assigned`` means an officer has the case, ``resolved``/``wontfix``
    that it is over.
    """
    if not is_text(ticket):
        return "none"
    status = ticket.get("status") or "open"
    if status in OUTCOMES:
        return "ended"
    if status == "assigned":
        return "officer"
    return "waiting"


def ended_by_officer(chat: dict[str, Any] | None) -> bool:
    """True once an officer ended this chat through :func:`end_chat`."""
    return bool(chat and chat.get("ended_at"))


def officer_name_for(ticket: dict[str, Any], chat: dict[str, Any] | None) -> str:
    """The name the taxpayer is shown for the officer on their case."""
    if chat and chat.get("officer_name"):
        return str(chat["officer_name"])
    assignee = str(ticket.get("assignee") or "")
    if assignee:
        return f"Officer {assignee.split('@')[0].capitalize()}"
    return ""


def end_chat(
    ticket: dict[str, Any],
    *,
    officer_id: str,
    officer_name: str,
    outcome: str = "resolved",
    closing_message: str = "",
    closing_localized: str = "",
) -> dict[str, Any]:
    """The officer ends the chat: thread, ticket status, rating request, event.

    The caller has already checked the officer may (``_require_live_officer``)
    and that the chat has not ended. Returns the chat's new state.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    _ensure()
    ticket_id = str(ticket["id"])
    now = time.time()
    if closing_message.strip():
        db.update_ticket(
            ticket_id,
            officer_reply=closing_message,
            officer_reply_localized=closing_localized or None,
        )
        append_message(
            ticket,
            "officer",
            closing_message,
            text_localized=closing_localized,
            officer_id=officer_id,
            officer_name=officer_name,
        )
    append_message(ticket, "system", ENDED_MARKER)
    if (ticket.get("status") or "") != outcome:
        db.update_ticket(ticket_id, status=outcome)
    note_officer(ticket, officer_id, officer_name)
    chat = get_chat(ticket_id) or {}
    fields: dict[str, Any] = {
        "ended_at": now,
        "ended_by": officer_id,
        "ended_by_name": (officer_name or "")[:128],
        "outcome": outcome,
    }
    if is_text(ticket) and not chat.get("csat_requested_at"):
        fields["csat_requested_at"] = now
    _upsert_chat(ticket, **fields)
    if is_text(ticket):
        handoff = ticket.get("handoff") or {}
        service_ratings.request(
            "chat_officer_csat",
            ticket_id=ticket_id,
            channel="chat",
            subject="officer",
            tenant_id=ticket.get("tenant_id") or "default",
            conversation_id=ticket.get("conversation_id") or "",
            officer_id=chat.get("officer_id") or officer_id,
            officer_name=chat.get("officer_name") or officer_name,
            team=ticket.get("team") or "",
            topic=str(handoff.get("topic") or ticket.get("reason") or ""),
            locale=ticket.get("locale") or "",
            user_id=ticket.get("user_id") or "",
        )
    updated = db.get_ticket(ticket_id) or {**ticket, "status": outcome}
    try:
        from .ticket_events import build_event, publish

        publish(build_event(updated, event_type="escalation.chat_ended"))
    except Exception:
        pass
    return {"ticket_id": ticket_id, "status": updated.get("status"), "chat_state": chat_state(updated)}


def csat_state(ticket: dict[str, Any], chat: dict[str, Any] | None) -> dict[str, Any]:
    """Whether the taxpayer has been asked to rate this chat, and has."""
    requested = bool(chat and chat.get("csat_requested_at"))
    return {"requested": requested, "submitted": bool(chat and chat.get("csat_submitted_at"))}


def submit_csat(ticket: dict[str, Any], *, score: int | None, comment: str = "") -> bool:
    """The taxpayer's 1–5 rating of the chat (``score=None`` is a skip), once."""
    chat = get_chat(str(ticket["id"]))
    if not chat or not chat.get("csat_requested_at") or chat.get("csat_submitted_at"):
        return False
    saved = service_ratings.answer(
        "chat_officer_csat",
        ticket_id=str(ticket["id"]),
        status="answered" if score is not None else "skipped",
        score=score,
        comment=comment if score is not None else "",
    )
    _upsert_chat(ticket, csat_submitted_at=time.time())
    return saved


# ---------------------------------------------------------------------------
# Phase 32 (2026): Officer Co-Pilot Bilingual Drafts & Officer -> AI Delegation
# ---------------------------------------------------------------------------
def build_officer_copilot_draft(ticket: dict[str, Any], draft_en: str) -> dict[str, Any]:
    """Prepare a figure-protected bilingual draft reply for the officer workbench.

    Gated on ``FLAG_OFFICER_AI_CO_EXECUTION``. Uses ``service.localize_reply``
    so every statutory rate, currency amount, and citation is protected by
    ``mt.protect_figures`` and verified by ``mt.figures_survived``.
    """
    from . import mt
    from .flags import flags

    clean_en = _redact(draft_en)
    ticket_id = str(ticket.get("id") or "")
    if ticket_id and clean_en:
        _store_pending_draft(ticket_id, clean_en)
    locale = str(ticket.get("locale") or "en").strip().lower() or "en"
    if not flags.is_enabled("officer_ai_co_execution", cohorts={"ura_staff"}) or locale == "en":
        return {
            "draft_en": clean_en,
            "draft_localized": clean_en,
            "locale": locale,
            "figures_verified": True,
        }
    try:
        from .service import localize_reply

        translated = localize_reply(clean_en, locale)
        figures_ok = (
            translated != clean_en
            and mt.figures_survived(clean_en, translated, locale=locale)
        )
        localized = _redact(translated) if figures_ok else clean_en
        return {
            "draft_en": clean_en,
            "draft_localized": localized,
            "locale": locale,
            "figures_verified": figures_ok,
        }
    except Exception:
        return {
            "draft_en": clean_en,
            "draft_localized": clean_en,
            "locale": locale,
            "figures_verified": False,
        }


def delegate_ticket_to_ai(
    ticket: dict[str, Any],
    *,
    officer_id: str,
    officer_name: str,
    workflow_id: str,
    prefill_slots: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Hand a ticket's routine procedural follow-up from a live officer back to the AI workflow engine.

    Enforces ``FLAG_OFFICER_AI_CO_EXECUTION``, seeds a pre-filled ``WorkflowSession``
    on the ticket's conversation, logs a system transition message in ``ticket_messages``,
    and records ``ura_workforce_delegation_total{direction="officer_to_ai"}``.
    """
    from .flags import flags
    from .workflows.registry import WorkflowRegistry

    if not flags.is_enabled("officer_ai_co_execution", cohorts={"ura_staff"}):
        raise RuntimeError("officer_ai_co_execution flag is disabled")
    wf = WorkflowRegistry.get(workflow_id)
    if wf is None:
        raise ValueError(f"unknown workflow_id {workflow_id!r}")

    session = WorkflowRegistry.create_session(workflow_id)
    if session is None:
        raise ValueError(f"could not create workflow session for {workflow_id!r}")
    for k, v in (prefill_slots or {}).items():
        if k and v not in ("", None):
            session.slots[str(k)] = v

    conv_id = str(ticket.get("conversation_id") or ticket["id"])
    tenant_id = str(ticket.get("tenant_id") or "default")
    user_id = str(ticket.get("user_id") or "")
    state_key = db.conversation_state_key(
        conv_id,
        session_id=str(ticket.get("session_id") or "") or None,
        user_id=user_id or None,
        tenant_id=tenant_id,
    )
    db.upsert_workflow_session(
        state_key,
        workflow_id=session.workflow_id,
        current_step_idx=session.current_step_idx,
        slots=session.slots,
        status="active",
        tenant_id=tenant_id,
    )
    transition_note = note.strip() or f"Officer {officer_name or officer_id} delegated procedural follow-up ({wf.name}) to URA AI Assistant."
    append_message(
        ticket,
        "officer",
        transition_note,
        officer_id=officer_id,
        officer_name=officer_name,
    )
    try:
        from .analytics import metrics

        metrics.inc(
            "workforce_delegation_total",
            labels={"direction": "officer_to_ai", "workflow": workflow_id},
        )
    except Exception as _exc:  # noqa: BLE001
        _ = _exc
    return {
        "ok": True,
        "ticket_id": str(ticket["id"]),
        "conversation_id": conv_id,
        "delegated_workflow_id": workflow_id,
        "prefilled_slots": dict(session.slots),
    }


def capture_officer_preference_pair(
    ticket: dict[str, Any],
    *,
    ai_draft_en: str,
    officer_final_en: str,
    officer_id: str = "",
) -> dict[str, Any] | None:
    """Persist a PII-redacted DPO/ORPO preference pair when an officer edits an AI draft."""
    from difflib import SequenceMatcher

    from .flags import flags

    if not flags.is_enabled("officer_preference_capture", cohorts={"ura_staff"}):
        return None
    _ensure()
    rejected = _redact(ai_draft_en)
    chosen = _redact(officer_final_en)
    if not rejected or not chosen:
        return None

    similarity = SequenceMatcher(None, rejected, chosen).ratio()
    edit_ratio = round(max(0.0, min(1.0, 1.0 - similarity)), 3)
    locale = str(ticket.get("locale") or "en").strip().lower() or "en"
    with contextlib.suppress(Exception):
        from .analytics import metrics

        metrics.observe(
            "officer_copilot_edit_distance_ratio",
            edit_ratio,
            labels={"locale": locale},
        )

    if rejected == chosen:
        return None

    handoff = ticket.get("handoff") if isinstance(ticket.get("handoff"), dict) else {}
    topic = str((handoff or {}).get("topic") or ticket.get("reason") or "general")[:120]
    prompt_ctx = _redact(str(ticket.get("user_query") or (handoff or {}).get("summary") or topic))
    row = {
        "id": f"pref-{uuid.uuid4().hex[:12]}",
        "ticket_id": str(ticket.get("id") or ""),
        "tenant_id": str(ticket.get("tenant_id") or "default"),
        "locale": locale,
        "topic": topic,
        "prompt_context": prompt_ctx,
        "chosen_reply": chosen,
        "rejected_draft": rejected,
        "edit_ratio": edit_ratio,
        "officer_id": str(officer_id or "")[:64],
        "created_at": time.time(),
    }
    db.execute(
        "INSERT INTO officer_preference_pairs (id, ticket_id, tenant_id, locale, topic, prompt_context, "
        "chosen_reply, rejected_draft, edit_ratio, officer_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            row["ticket_id"],
            row["tenant_id"],
            row["locale"],
            row["topic"],
            row["prompt_context"],
            row["chosen_reply"],
            row["rejected_draft"],
            row["edit_ratio"],
            row["officer_id"],
            row["created_at"],
        ),
    )
    return row


def list_officer_preference_pairs(limit: int = 100, tenant_id: str = "default") -> list[dict[str, Any]]:
    """Return captured PII-redacted officer preference pairs for offline DPO/ORPO alignment."""
    _ensure()
    return db.query_all(
        "SELECT id, ticket_id, tenant_id, locale, topic, prompt_context, chosen_reply, rejected_draft, "
        "edit_ratio, created_at FROM officer_preference_pairs WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
        (tenant_id or "default", max(1, min(int(limit), 1000))),
    )


# ---------------------------------------------------------------------------
# Privacy: erasure, export and retention
# ---------------------------------------------------------------------------
def delete_for_tickets(ticket_ids: list[str]) -> int:
    """Erase the threads and chat state of these tickets (subject erasure)."""
    if not ticket_ids:
        return 0
    _ensure()
    ph = ",".join("?" * len(ticket_ids))
    n = db.execute(f"DELETE FROM ticket_messages WHERE ticket_id IN ({ph})", tuple(ticket_ids))  # noqa: S608  # nosec B608
    db.execute(f"DELETE FROM ticket_chats WHERE ticket_id IN ({ph})", tuple(ticket_ids))  # noqa: S608  # nosec B608
    db.execute(f"DELETE FROM officer_preference_pairs WHERE ticket_id IN ({ph})", tuple(ticket_ids))  # noqa: S608  # nosec B608
    return n


def export_for_tickets(ticket_ids: list[str]) -> list[dict[str, Any]]:
    if not ticket_ids:
        return []
    _ensure()
    ph = ",".join("?" * len(ticket_ids))
    return db.query_all(
        f"SELECT ticket_id, seq, sender, text, text_localized, created_at FROM ticket_messages "  # noqa: S608
        f"WHERE ticket_id IN ({ph}) ORDER BY ticket_id, seq",
        tuple(ticket_ids),
    )


def purge_orphans() -> int:
    """Drop the threads of tickets retention has already deleted."""
    _ensure()
    n = db.execute("DELETE FROM ticket_messages WHERE ticket_id NOT IN (SELECT id FROM tickets)")
    db.execute("DELETE FROM ticket_chats WHERE ticket_id NOT IN (SELECT id FROM tickets)")
    return n
