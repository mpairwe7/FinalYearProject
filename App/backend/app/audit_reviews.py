"""The administrator's "Needs review" queue and what was decided about each item.

The service-quality audit (docs/plans/admin-service-quality-audit-plan.md §E2)
reads four streams; this module gathers the items in them that call for a
person to look — an AI chat that went wrong or was never found helpful, a 👎
with a comment, an officer chat or call rated 1 or 2, a caller who said the AI
did not help — and records an auditor's decision on each: **Reviewed**, or
**Action needed**, with a note. Every decision is also a ``staff.quality_reviewed``
row on the audit ledger (written by the route).

The queue is computed from the streams on read; only the decisions are
stored, in ``audit_reviews``. A decision names its item, never a taxpayer:
the note is redacted on write, and a decision whose item has gone (retention
or a taxpayer's erasure) is purged with it.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from . import database as db
from . import quality_audit, service_ratings

ITEM_TYPES: tuple[str, ...] = ("ai_chat", "rating")
STATUSES: tuple[str, ...] = ("reviewed", "action_needed")
#: The queue's filters: ``open`` is everything not yet decided.
STATUS_FILTERS: tuple[str, ...] = ("open", "action_needed", "reviewed", "all")
STREAMS: tuple[str, ...] = ("ai_chat", "officer_chat", "officer_call", "ai_call")

#: How long a decision outlives nothing: the feedback and ratings it is about
#: are kept 90 days.
REVIEW_TTL_DAYS = 90

_STREAM_OF_SOURCE: dict[str, str] = {
    "chat_officer_csat": "officer_chat",
    "call_officer_csat": "officer_call",
    "call_ai_review_end": "ai_call",
    "call_ai_review_transfer": "ai_call",
}

_schema_ready = False


def init_schema() -> None:
    """Create ``audit_reviews`` if it does not exist (portable DDL)."""
    global _schema_ready
    db.execute_script(
        """
        CREATE TABLE IF NOT EXISTS audit_reviews (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL DEFAULT 'default',
            item_type     TEXT NOT NULL,
            item_id       TEXT NOT NULL,
            status        TEXT NOT NULL,
            note          TEXT NOT NULL DEFAULT '',
            reviewer_id   TEXT NOT NULL DEFAULT '',
            reviewer_name TEXT NOT NULL DEFAULT '',
            created_at    DOUBLE PRECISION NOT NULL,
            updated_at    DOUBLE PRECISION NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_reviews_item ON audit_reviews(tenant_id, item_type, item_id);
        """
    )
    _schema_ready = True


def _ensure() -> None:
    if not _schema_ready:
        init_schema()


def _tenant(tenant_id: str | None) -> str:
    return tenant_id or "default"


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": row["status"],
        "note": row.get("note") or "",
        "reviewer_name": row.get("reviewer_name") or "",
        "updated_at": float(row.get("updated_at") or 0.0),
    }


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
def get_review(item_type: str, item_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
    _ensure()
    row = db.query_one(
        "SELECT * FROM audit_reviews WHERE tenant_id = ? AND item_type = ? AND item_id = ?",
        (_tenant(tenant_id), item_type, item_id),
    )
    return _public(row) if row else None


def set_review(
    item_type: str,
    item_id: str,
    *,
    status: str,
    note: str = "",
    reviewer_id: str = "",
    reviewer_name: str = "",
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Record a decision on an item; ``status="open"`` takes it back to the queue.

    Returns the decision now on the item (None once reopened).
    """
    if item_type not in ITEM_TYPES:
        raise ValueError(f"unknown item type {item_type!r}")
    if status != "open" and status not in STATUSES:
        raise ValueError(f"unknown review status {status!r}")
    _ensure()
    tenant = _tenant(tenant_id)
    if status == "open":
        db.execute(
            "DELETE FROM audit_reviews WHERE tenant_id = ? AND item_type = ? AND item_id = ?",
            (tenant, item_type, item_id),
        )
        return None
    from .guardrails import redact_pii_text

    clean_note = redact_pii_text((note or "").strip())[:1000]
    now = time.time()
    if get_review(item_type, item_id, tenant_id=tenant) is None:
        try:
            db.execute(
                "INSERT INTO audit_reviews (id, tenant_id, item_type, item_id, status, note, reviewer_id, "
                "reviewer_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, tenant, item_type, item_id, status, clean_note, reviewer_id, reviewer_name[:128], now, now),
            )
            return get_review(item_type, item_id, tenant_id=tenant)
        except Exception:
            pass  # decided by someone else a moment ago: this decision replaces it
    db.execute(
        "UPDATE audit_reviews SET status = ?, note = ?, reviewer_id = ?, reviewer_name = ?, updated_at = ? "
        "WHERE tenant_id = ? AND item_type = ? AND item_id = ?",
        (status, clean_note, reviewer_id, reviewer_name[:128], now, tenant, item_type, item_id),
    )
    return get_review(item_type, item_id, tenant_id=tenant)


def reviews_for(item_type: str, ids: list[str], *, tenant_id: str | None = None) -> dict[str, dict[str, Any]]:
    """The decisions on these items, by item id."""
    if not ids:
        return {}
    _ensure()
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        ph = ",".join("?" * len(chunk))
        rows = db.query_all(
            f"SELECT * FROM audit_reviews WHERE tenant_id = ? AND item_type = ? AND item_id IN ({ph})",  # noqa: S608
            (_tenant(tenant_id), item_type, *chunk),
        )
        found.update({r["item_id"]: _public(r) for r in rows})
    return found


def item_exists(item_type: str, item_id: str, *, tenant_id: str | None = None) -> bool:
    """The item is one the audit can see (so a decision is never about nothing)."""
    if item_type == "rating":
        return service_ratings.exists(item_id, tenant_id=tenant_id)
    if item_type != "ai_chat":
        return False
    if item_id.startswith("msg:"):
        ref = item_id[4:]
        sql, params = "SELECT id FROM feedback WHERE (message_id = ? OR id = ?)", [ref, ref]
    else:
        sql, params = "SELECT id FROM feedback WHERE conversation_id = ?", [item_id]
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    return db.query_one(sql + " LIMIT 1", tuple(params)) is not None


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------
def _ai_chat_items(days: int, tenant_id: str | None, now: float) -> tuple[list[dict[str, Any]], bool]:
    convs, truncated = quality_audit.rated_conversations(days, tenant_id=tenant_id, now=now)
    items = []
    for c in convs:
        if c["pattern"] == "went_wrong":
            title = f"AI chat went wrong at answer {c['break_answer']}" if c["break_answer"] else "AI chat went wrong"
        elif c["pattern"] == "never_helpful":
            title = "AI chat never found helpful"
        elif c.get("down_comment"):
            title = "Unhelpful AI answer, with a comment"
        else:
            continue
        items.append(
            {
                "item_type": "ai_chat",
                "item_id": c["key"],
                "stream": "ai_chat",
                "title": title,
                "quote": c.get("break_question") or "",
                "comment": c.get("down_comment") or "",
                "topic": c.get("topic") or "",
                "locale": c.get("locale") or "",
                "officer_name": "",
                "occurred_at": float(c["last_rated_at"]),
                "link": (
                    {"tab": "ai-chat", "param": "conversation", "value": c["conversation_id"]}
                    if c["conversation_id"]
                    else {"tab": "ai-chat", "param": "", "value": ""}
                ),
            }
        )
    return items, truncated


def _rating_items(days: int, tenant_id: str | None, now: float) -> list[dict[str, Any]]:
    items = []
    for r in service_ratings.problems(days, tenant_id=tenant_id, now=now):
        stream = _STREAM_OF_SOURCE.get(r["source"])
        if stream is None:
            continue
        officer = r.get("officer_name") or "an officer"
        if stream == "officer_chat":
            title = f"Officer chat rated {r['score']}/5 — {officer}"
            link = {"tab": "officer-chat", "param": "ticket", "value": r.get("ticket_id") or ""}
        elif stream == "officer_call":
            title = f"Caller satisfaction {r['score']}/5 — {officer}"
            link = {"tab": "officer-calls", "param": "call", "value": r.get("call_id") or ""}
        else:
            when = "while being transferred" if r["source"] == "call_ai_review_transfer" else "at the end of the call"
            title = f"Caller said the AI didn't help ({when})"
            link = {"tab": "ai-calls", "param": "", "value": ""}
        items.append(
            {
                "item_type": "rating",
                "item_id": r["id"],
                "stream": stream,
                "title": title,
                "quote": "",
                "comment": r.get("comment") or "",
                "topic": r.get("topic") or "",
                "locale": r.get("locale") or "",
                "officer_name": r.get("officer_name") or "",
                "occurred_at": float(r.get("answered_at") or r["created_at"]),
                "link": link,
            }
        )
    return items


def needs_review(
    days: int,
    *,
    tenant_id: str | None = None,
    status: str = "open",
    stream: str = "",
    limit: int = 25,
    offset: int = 0,
    now: float | None = None,
) -> dict[str, Any]:
    """The period's problem items with their decisions: counts, then a page, newest first."""
    if status not in STATUS_FILTERS:
        raise ValueError(f"unknown status filter {status!r}")
    now = time.time() if now is None else now
    chats, truncated = _ai_chat_items(days, tenant_id, now)
    items = chats + _rating_items(days, tenant_id, now)
    decided = {
        **{("ai_chat", k): v for k, v in reviews_for("ai_chat", [i["item_id"] for i in chats], tenant_id=tenant_id).items()},
        **{
            ("rating", k): v
            for k, v in reviews_for(
                "rating", [i["item_id"] for i in items if i["item_type"] == "rating"], tenant_id=tenant_id
            ).items()
        },
    }
    for item in items:
        item["review"] = decided.get((item["item_type"], item["item_id"]))
    if stream:
        items = [i for i in items if i["stream"] == stream]
    counts = {
        "open": sum(1 for i in items if i["review"] is None),
        "action_needed": sum(1 for i in items if i["review"] and i["review"]["status"] == "action_needed"),
        "reviewed": sum(1 for i in items if i["review"] and i["review"]["status"] == "reviewed"),
    }
    counts["all"] = len(items)
    if status == "open":
        items = [i for i in items if i["review"] is None]
    elif status != "all":
        items = [i for i in items if i["review"] and i["review"]["status"] == status]
    items.sort(key=lambda i: -i["occurred_at"])
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    return {
        "period_days": days,
        "truncated": truncated,
        "counts": counts,
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


def counts(days: int, *, tenant_id: str | None = None) -> dict[str, int]:
    """Open and action-needed counts, for the overview."""
    queue = needs_review(days, tenant_id=tenant_id, status="all", limit=1)
    return {"open": queue["counts"]["open"], "action_needed": queue["counts"]["action_needed"]}


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def purge(now: float | None = None) -> int:
    """Drop decisions about items that are gone, and any past the audit period."""
    _ensure()
    cutoff = (time.time() if now is None else now) - REVIEW_TTL_DAYS * 86400
    removed = db.execute("DELETE FROM audit_reviews WHERE updated_at < ?", (cutoff,))
    removed += db.execute(
        "DELETE FROM audit_reviews WHERE item_type = 'rating' AND item_id NOT IN (SELECT id FROM service_ratings)"
    )
    removed += db.execute(
        "DELETE FROM audit_reviews WHERE item_type = 'ai_chat' AND item_id NOT LIKE 'msg:%' "
        "AND item_id NOT IN (SELECT conversation_id FROM feedback WHERE conversation_id IS NOT NULL)"
    )
    return removed
