"""Satisfaction ratings of the service a taxpayer got — one table for every stream.

The administrator's service-quality audit (docs/plans/
admin-service-quality-audit-plan.md §C3) reads four kinds of rating, all here:

* ``chat_officer_csat`` — the taxpayer rates an officer chat on a 5-level scale
  when the officer ends it;
* ``call_officer_csat`` — the officer records how satisfied the caller said
  they were, at wrap-up;
* ``call_ai_review_end`` / ``call_ai_review_transfer`` — the caller's spoken
  answer to "was this helpful?", classified, at goodbye or while transferring.

A row is written when the rating is **asked for** (``status='pending'``) and
updated when it is answered, skipped or left unanswered. That makes the
response rate a count rather than a guess: asked is every row, answered is
the rows with an answer.

Text is redacted on write. ``user_id`` is the taxpayer the rated case belongs
to, so their erasure reaches it; ``officer_id``/``officer_name`` say whose
service was rated.
"""

from __future__ import annotations

import statistics
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from . import database as db

SOURCES: tuple[str, ...] = (
    "chat_officer_csat",
    "call_officer_csat",
    "call_ai_review_end",
    "call_ai_review_transfer",
)
STATUSES: tuple[str, ...] = ("pending", "answered", "skipped", "no_answer")
SENTIMENTS: tuple[str, ...] = ("positive", "neutral", "negative", "unclear")

#: The most ratings one period reads; past it the summary says ``truncated``.
MAX_ROWS = 5000

_schema_ready = False


def init_schema() -> None:
    """Create ``service_ratings`` if it does not exist (portable DDL)."""
    global _schema_ready
    db.execute_script(
        """
        CREATE TABLE IF NOT EXISTS service_ratings (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL DEFAULT 'default',
            source        TEXT NOT NULL,
            channel       TEXT NOT NULL DEFAULT '',
            subject       TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending',
            score         INTEGER,
            sentiment     TEXT NOT NULL DEFAULT '',
            comment       TEXT NOT NULL DEFAULT '',
            ticket_id     TEXT NOT NULL DEFAULT '',
            call_id       TEXT NOT NULL DEFAULT '',
            conversation_id TEXT NOT NULL DEFAULT '',
            officer_id    TEXT NOT NULL DEFAULT '',
            officer_name  TEXT NOT NULL DEFAULT '',
            team          TEXT NOT NULL DEFAULT '',
            topic         TEXT NOT NULL DEFAULT '',
            locale        TEXT NOT NULL DEFAULT '',
            user_id       TEXT NOT NULL DEFAULT '',
            created_at    DOUBLE PRECISION NOT NULL,
            answered_at   DOUBLE PRECISION
        );
        CREATE INDEX IF NOT EXISTS idx_service_ratings_source ON service_ratings(source, created_at);
        CREATE INDEX IF NOT EXISTS idx_service_ratings_ticket ON service_ratings(ticket_id);
        CREATE INDEX IF NOT EXISTS idx_service_ratings_call ON service_ratings(call_id);
        CREATE INDEX IF NOT EXISTS idx_service_ratings_user ON service_ratings(user_id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_service_ratings_case ON service_ratings(source, ticket_id, call_id);
        """
    )
    _schema_ready = True


def _ensure() -> None:
    if not _schema_ready:
        init_schema()


def sentiment_for_score(score: int | None) -> str:
    """1–2 negative, 3 neutral, 4–5 positive (the conventional CSAT bands)."""
    if score is None:
        return ""
    return "negative" if score <= 2 else "neutral" if score == 3 else "positive"


def _key_column(ticket_id: str, call_id: str) -> tuple[str, str]:
    if ticket_id:
        return "ticket_id", ticket_id
    if call_id:
        return "call_id", call_id
    raise ValueError("a rating names the ticket or the call it rates")


def find(source: str, *, ticket_id: str = "", call_id: str = "") -> dict[str, Any] | None:
    """The one rating of *source* for this ticket or call, if asked for."""
    _ensure()
    column, value = _key_column(ticket_id, call_id)
    return db.query_one(
        f"SELECT * FROM service_ratings WHERE source = ? AND {column} = ?",  # noqa: S608 - fixed column
        (source, value),
    )


def request(
    source: str,
    *,
    ticket_id: str = "",
    call_id: str = "",
    channel: str,
    subject: str,
    tenant_id: str = "default",
    conversation_id: str = "",
    officer_id: str = "",
    officer_name: str = "",
    team: str = "",
    topic: str = "",
    locale: str = "",
    user_id: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Record that a rating was asked for (idempotent: one row per source and case)."""
    if source not in SOURCES:
        raise ValueError(f"unknown rating source {source!r}")
    existing = find(source, ticket_id=ticket_id, call_id=call_id)
    if existing:
        return existing
    row = {
        "id": uuid.uuid4().hex,
        "tenant_id": tenant_id or "default",
        "source": source,
        "channel": channel,
        "subject": subject,
        "status": "pending",
        "ticket_id": ticket_id,
        "call_id": call_id,
        "conversation_id": conversation_id or "",
        "officer_id": officer_id or "",
        "officer_name": (officer_name or "")[:128],
        "team": team or "",
        "topic": (topic or "")[:128],
        "locale": locale or "",
        "user_id": user_id or "",
        "created_at": time.time() if now is None else now,
    }
    columns = ", ".join(row)
    try:
        db.execute(
            f"INSERT INTO service_ratings ({columns}) VALUES ({', '.join('?' for _ in row)})",  # noqa: S608 - fixed columns
            tuple(row.values()),
        )
    except Exception:
        # Asked twice at once: the unique index kept one; return that one.
        existing = find(source, ticket_id=ticket_id, call_id=call_id)
        if existing:
            return existing
        raise
    return find(source, ticket_id=ticket_id, call_id=call_id) or row


def answer(
    source: str,
    *,
    ticket_id: str = "",
    call_id: str = "",
    status: str,
    score: int | None = None,
    sentiment: str = "",
    comment: str = "",
) -> bool:
    """Record the answer to a pending rating, once. False if none is pending."""
    if status not in STATUSES or status == "pending":
        raise ValueError(f"invalid rating status {status!r}")
    if score is not None and not 1 <= int(score) <= 5:
        raise ValueError("a score is 1 to 5")
    from .guardrails import redact_pii_text

    _ensure()
    column, value = _key_column(ticket_id, call_id)
    affected = db.execute(
        "UPDATE service_ratings SET status = ?, score = ?, sentiment = ?, comment = ?, answered_at = ? "
        f"WHERE source = ? AND {column} = ? AND status = 'pending'",  # noqa: S608 - fixed column
        (
            status,
            int(score) if score is not None else None,
            sentiment or sentiment_for_score(score),
            redact_pii_text((comment or "").strip())[:1000],
            time.time(),
            source,
            value,
        ),
    )
    return affected > 0


# ---------------------------------------------------------------------------
# Reading for the audit
# ---------------------------------------------------------------------------
def _share(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _rows(source: str, since: float, until: float, tenant_id: str | None) -> tuple[list[dict[str, Any]], bool]:
    _ensure()
    clauses = ["source = ?", "created_at >= ?", "created_at < ?"]
    params: list[Any] = [source, since, until]
    if tenant_id is not None:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    params.append(MAX_ROWS + 1)
    rows = db.query_all(
        f"SELECT * FROM service_ratings WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
        tuple(params),
    )
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Asked, answered, average, CSAT % (share of 4–5), distribution, per officer."""
    asked = len(rows)
    answered = [r for r in rows if r.get("status") == "answered"]
    scored = [int(r["score"]) for r in answered if r.get("score") is not None]
    sentiments = {s: 0 for s in SENTIMENTS}
    for r in answered:
        if r.get("sentiment") in sentiments:
            sentiments[r["sentiment"]] += 1
    officers: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r.get("officer_id") or ""
        if not key:
            continue
        o = officers.setdefault(key, {"officer_id": key, "officer_name": r.get("officer_name") or key, "handled": 0, "scores": []})
        o["handled"] += 1
        if r.get("status") == "answered" and r.get("score") is not None:
            o["scores"].append(int(r["score"]))
    by_officer = []
    for o in officers.values():
        scores = o.pop("scores")
        by_officer.append(
            {
                **o,
                "rated": len(scores),
                "average": round(statistics.mean(scores), 2) if scores else None,
                "csat_pct": _share(sum(1 for s in scores if s >= 4), len(scores)),
                "low": sum(1 for s in scores if s <= 2),
            }
        )
    # Lowest average first: the order an auditor reads in.
    by_officer.sort(key=lambda o: (o["average"] if o["average"] is not None else 6, -o["handled"]))
    weeks: dict[str, list[int]] = defaultdict(list)
    for r in answered:
        if r.get("score") is None:
            continue
        week = datetime.fromtimestamp(float(r["created_at"]), tz=timezone.utc).strftime("%G-W%V")
        weeks[week].append(int(r["score"]))
    return {
        "asked": asked,
        "answered": len(answered),
        "skipped": sum(1 for r in rows if r.get("status") == "skipped"),
        "no_answer": sum(1 for r in rows if r.get("status") == "no_answer"),
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "response_rate": _share(len(answered), asked),
        "average": round(statistics.mean(scored), 2) if scored else None,
        "csat_pct": _share(sum(1 for s in scored if s >= 4), len(scored)),
        "low": sum(1 for s in scored if s <= 2),
        "distribution": {str(n): scored.count(n) for n in range(1, 6)},
        "sentiment": sentiments,
        "by_officer": by_officer,
        "by_week": [
            {"week": w, "rated": len(s), "average": round(statistics.mean(s), 2), "csat_pct": _share(sum(1 for x in s if x >= 4), len(s))}
            for w, s in sorted(weeks.items())
        ],
    }


def audit(
    source: str,
    days: int,
    *,
    tenant_id: str | None = None,
    score: int | None = None,
    officer_id: str = "",
    status: str = "",
    limit: int = 25,
    offset: int = 0,
    now: float | None = None,
) -> dict[str, Any]:
    """One source's figures for the period (and the one before), plus a page of ratings."""
    if source not in SOURCES:
        raise ValueError(f"unknown rating source {source!r}")
    now = time.time() if now is None else now
    span = days * 86400
    rows, truncated = _rows(source, now - span, now, tenant_id)
    prev_rows, _ = _rows(source, now - 2 * span, now - span, tenant_id)
    listed = [r for r in rows if r.get("status") != "pending"]
    if score is not None:
        listed = [r for r in listed if r.get("score") == score]
    if officer_id:
        listed = [r for r in listed if r.get("officer_id") == officer_id]
    if status:
        listed = [r for r in listed if r.get("status") == status]
    # Lowest score first, then newest: the ratings that need reading lead.
    listed.sort(key=lambda r: (r.get("score") if r.get("score") is not None else 6, -float(r.get("answered_at") or r["created_at"])))
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    prev = summarise(prev_rows)
    return {
        "source": source,
        "period_days": days,
        "truncated": truncated,
        "summary": summarise(rows),
        "previous": {"asked": prev["asked"], "average": prev["average"], "csat_pct": prev["csat_pct"]},
        "total": len(listed),
        "limit": limit,
        "offset": offset,
        "ratings": [_public(r) for r in listed[offset : offset + limit]],
    }


def headline(source: str, days: int, *, tenant_id: str | None = None, now: float | None = None) -> dict[str, Any]:
    """The overview's figures for one source: this period and the one before."""
    if source not in SOURCES:
        raise ValueError(f"unknown rating source {source!r}")
    now = time.time() if now is None else now
    span = days * 86400
    rows, truncated = _rows(source, now - span, now, tenant_id)
    prev_rows, _ = _rows(source, now - 2 * span, now - span, tenant_id)
    figures = summarise(rows)
    prev = summarise(prev_rows)
    keep = ("asked", "answered", "response_rate", "average", "csat_pct", "low", "distribution", "sentiment")
    return {
        "truncated": truncated,
        **{k: figures[k] for k in keep},
        "previous": {"asked": prev["asked"], "average": prev["average"], "csat_pct": prev["csat_pct"]},
    }


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """A rating as the audit lists it: no taxpayer identifier."""
    return {k: v for k, v in row.items() if k not in ("user_id", "tenant_id")}


# ---------------------------------------------------------------------------
# Privacy: erasure, export and retention
# ---------------------------------------------------------------------------
def delete_for_user(user_id: str) -> int:
    if not user_id:
        return 0
    _ensure()
    return db.execute("DELETE FROM service_ratings WHERE user_id = ?", (user_id,))


def export_for_user(user_id: str) -> list[dict[str, Any]]:
    if not user_id:
        return []
    _ensure()
    return db.query_all(
        "SELECT source, status, score, sentiment, comment, ticket_id, call_id, created_at, answered_at "
        "FROM service_ratings WHERE user_id = ? ORDER BY created_at",
        (user_id,),
    )


def purge_expired(ttl_days: int, now: float | None = None) -> int:
    _ensure()
    cutoff = (time.time() if now is None else now) - ttl_days * 86400
    return db.execute("DELETE FROM service_ratings WHERE created_at < ?", (cutoff,))
