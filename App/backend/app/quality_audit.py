"""Service-quality audit: what taxpayers' 👍/👎 say about the AI's chat answers.

The administrator's audit pages (/admin/quality, docs/plans/
admin-service-quality-audit-plan.md §B3) read this module. It turns the
``feedback`` table into answers to three questions an auditor actually asks:

* **How helpful are the answers?** Rated answers and the helpful share, by
  day, by topic, by retrieval route and by language.
* **Do follow-up questions fare worse than first ones?** The helpful share by
  the answer's position in its conversation (1, 2, 3, 4, 5+).
* **Where did a conversation go wrong?** Each conversation's ratings in
  answer order, its pattern, and its *break point*: the first unhelpful answer
  that came after a helpful one — the place to start reading.

A rating is linked to its conversation and position since the turn id landed
(``feedback.turn_id``, ``conversation_id``, ``turn_index``). Ratings from
before that have no conversation and are read as single, unlinked answers.

Reads only. Everything returned is text already stored redacted
(``save_feedback`` and ``log_conversation`` redact on write).
"""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from . import database as db

#: The most rated answers one period reads. A period beyond it is reported as
#: truncated (``truncated: true``) rather than summarised from a silent sample.
MAX_ROWS = 5000

#: Conversation patterns, worst first: the order the audit lists them in.
PATTERNS: tuple[str, ...] = ("went_wrong", "never_helpful", "recovered", "all_helpful")

#: The answer positions the follow-up chart buckets into.
POSITION_BUCKETS: tuple[str, ...] = ("1", "2", "3", "4", "5+")

_UNLINKED_PREFIX = "msg:"

_FEEDBACK_COLUMNS = (
    "id, message_id, conversation_id, turn_id, turn_index, rating, comment, user_query, bot_reply, "
    "retrieval_mode, topic_tag, locale, sources_json, confidence, created_at"
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without a database)
# ---------------------------------------------------------------------------
def classify(ratings: Sequence[str]) -> tuple[str, int | None]:
    """The pattern of a conversation's ratings, in answer order, and its break point.

    Returns ``(pattern, break_index)``; *break_index* is a 0-based index into
    *ratings*, or ``None`` when nothing went wrong.

    * ``all_helpful`` — every rated answer was 👍.
    * ``never_helpful`` — every rated answer was 👎; it broke at the first.
    * ``went_wrong`` — it ends 👎 after at least one 👍; it broke at the first
      👎 that followed a 👍 (a helpful answer, then a follow-up that was not).
    * ``recovered`` — it ends 👍 after at least one 👎; it broke at the first 👎.

    Every mixed sequence ends either way, so these four cover all of them.
    """
    if not ratings:
        return "all_helpful", None
    downs = [i for i, r in enumerate(ratings) if r == "down"]
    if not downs:
        return "all_helpful", None
    if len(downs) == len(ratings):
        return "never_helpful", 0
    if ratings[-1] == "down":
        first_up = ratings.index("up")
        return "went_wrong", next(i for i in downs if i > first_up)
    return "recovered", downs[0]


def position_bucket(turn_index: int | None) -> str | None:
    """``"1"``…``"4"`` or ``"5+"`` for a 1-based answer position; ``None`` when unknown."""
    if not turn_index or turn_index < 1:
        return None
    return str(turn_index) if turn_index < 5 else "5+"


def _share(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _sources(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(s) for s in raw][:5]
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(s) for s in parsed][:5] if isinstance(parsed, list) else []


def _answer_key(row: dict[str, Any]) -> tuple[str, str]:
    conversation = row.get("conversation_id") or f"{_UNLINKED_PREFIX}{row.get('message_id') or row.get('id')}"
    answer = row.get("turn_id") or (f"#{row['turn_index']}" if row.get("turn_index") else "") or row.get("message_id")
    return conversation, str(answer or row.get("id"))


def latest_per_answer(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One rating per answer: the latest, if the same answer was rated twice.

    The buttons rate an answer once, but a reload clears them; counting both
    ratings would weigh that answer double.
    """
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = _answer_key(row)
        if key not in latest or float(row.get("created_at") or 0) >= float(latest[key].get("created_at") or 0):
            latest[key] = row
    return list(latest.values())


def group_conversations(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Rated answers grouped by conversation, each group in answer order.

    A rating with no conversation (recorded before the link existed) is its
    own one-answer group, keyed by its message id.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_answer_key(row)[0]].append(row)
    for items in groups.values():
        items.sort(key=lambda r: (r.get("turn_index") or 0, r.get("created_at") or 0))
    return groups


def conversation_row(key: str, items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One conversation as the audit lists it: its rating strip, pattern and break point."""
    ratings = [str(r.get("rating")) for r in items]
    pattern, brk = classify(ratings)
    broke = items[brk] if brk is not None else None
    before = items[brk - 1] if brk else None
    return {
        "conversation_id": None if key.startswith(_UNLINKED_PREFIX) else key,
        "key": key,
        "linked": not key.startswith(_UNLINKED_PREFIX),
        "pattern": pattern,
        "ratings": [{"answer": r.get("turn_index") or None, "rating": r.get("rating")} for r in items],
        "rated_answers": len(items),
        "unhelpful": ratings.count("down"),
        "break_answer": (broke.get("turn_index") or None) if broke else None,
        "break_question": (broke.get("user_query") or "") if broke else "",
        "helpful_before_break": bool(before and before.get("rating") == "up"),
        "comments": sum(1 for r in items if (r.get("comment") or "").strip()),
        # The latest thing a taxpayer wrote with a 👎: what Needs review quotes.
        "down_comment": next(
            ((r.get("comment") or "").strip() for r in reversed(items) if r.get("rating") == "down" and (r.get("comment") or "").strip()),
            "",
        ),
        "topic": next((r.get("topic_tag") for r in items if r.get("topic_tag")), "") or "",
        "locale": next((r.get("locale") for r in items if r.get("locale")), "") or "",
        "first_rated_at": min(float(r.get("created_at") or 0) for r in items),
        "last_rated_at": max(float(r.get("created_at") or 0) for r in items),
    }


def _breakdown(rows: Iterable[dict[str, Any]], field: str, *, blank: str, limit: int = 8) -> list[dict[str, Any]]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        key = (r.get(field) or "").strip() or blank
        counts[key][0] += 1
        counts[key][1] += 1 if r.get("rating") == "up" else 0
    out = [
        {"key": k, "rated": rated, "helpful": up, "unhelpful": rated - up, "helpful_pct": _share(up, rated)}
        for k, (rated, up) in counts.items()
    ]
    # Most unhelpful answers first: the order an auditor works in.
    out.sort(key=lambda d: (-d["unhelpful"], -d["rated"], d["key"]))
    return out[:limit]


def summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """KPIs, breakdowns and pattern counts for one period's rated answers."""
    rated = len(rows)
    helpful = sum(1 for r in rows if r.get("rating") == "up")
    groups = group_conversations(rows)
    convs = [conversation_row(k, v) for k, v in groups.items()]
    pattern_counts = {p: 0 for p in PATTERNS}
    for c in convs:
        pattern_counts[c["pattern"]] += 1
    breaks = [c["break_answer"] for c in convs if c["pattern"] == "went_wrong" and c["break_answer"]]

    positions = {b: [0, 0] for b in POSITION_BUCKETS}
    for r in rows:
        b = position_bucket(r.get("turn_index"))
        if b is None:
            continue
        positions[b][0] += 1
        positions[b][1] += 1 if r.get("rating") == "up" else 0

    days: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        d = _day(float(r.get("created_at") or 0))
        days[d][0] += 1
        days[d][1] += 1 if r.get("rating") == "up" else 0

    linked = [c for c in convs if c["linked"]]
    return {
        "rated_answers": rated,
        "helpful": helpful,
        "unhelpful": rated - helpful,
        "helpful_pct": _share(helpful, rated),
        "comments": sum(1 for r in rows if (r.get("comment") or "").strip()),
        "conversations": len(linked),
        "unlinked_answers": len(convs) - len(linked),
        "patterns": pattern_counts,
        "median_break_answer": statistics.median(breaks) if breaks else None,
        "by_position": [
            {"position": b, "rated": n, "helpful": up, "helpful_pct": _share(up, n)}
            for b, (n, up) in positions.items()
        ],
        "by_topic": _breakdown(rows, "topic_tag", blank="unclassified"),
        "by_route": _breakdown(rows, "retrieval_mode", blank="unknown"),
        "by_language": _breakdown(rows, "locale", blank="unknown"),
        "by_day": [
            {"day": d, "rated": n, "helpful": up, "helpful_pct": _share(up, n)}
            for d, (n, up) in sorted(days.items())
        ],
    }


# ---------------------------------------------------------------------------
# Database reads
# ---------------------------------------------------------------------------
def rated_turn(turn_id: str, conversation_id: str = "", *, tenant_id: str = "default") -> dict[str, Any]:
    """The logged answer a rating names, as the rating should keep a copy of it.

    Read when the rating is given, while ``conversations`` still holds the
    turn: its position in the conversation (1 = first answer), the question
    and the reply as they were logged (already redacted), what it was built
    from, and its topic and language. Empty when the turn is unknown, or when
    it is not in the conversation the rating claims.
    """
    if not turn_id:
        return {}
    row = db.query_one(
        "SELECT id, conversation_id, created_at, user_message, bot_reply, sources, confidence, topic_tag, locale "
        "FROM conversations WHERE id = ? AND tenant_id = ?",
        (turn_id, tenant_id or "default"),
    )
    if not row or (conversation_id and row.get("conversation_id") != conversation_id):
        return {}
    position = db.query_one(
        "SELECT COUNT(*) AS n FROM conversations WHERE conversation_id = ? AND tenant_id = ? AND created_at <= ?",
        (row["conversation_id"], tenant_id or "default", row["created_at"]),
    )
    return {
        "conversation_id": row["conversation_id"],
        "turn_index": int((position or {}).get("n") or 0) or None,
        "user_query": row.get("user_message") or "",
        "bot_reply": row.get("bot_reply") or "",
        "sources_json": json.dumps(_sources(row.get("sources"))),
        "confidence": row.get("confidence"),
        "topic_tag": row.get("topic_tag") or "",
        "locale": row.get("locale") or "",
    }

def _rated_answers(since: float, until: float, tenant_id: str | None) -> tuple[list[dict[str, Any]], bool]:
    """Rated AI answers in ``[since, until)``, newest first, capped at :data:`MAX_ROWS`.

    Officer replies shown in the chat are not the AI's answers, so they are
    left out (``retrieval_mode = 'officer_reply'``).
    Repeat ratings of one answer are folded into the latest
    (:func:`latest_per_answer`).
    """
    clauses = ["created_at >= ?", "created_at < ?", "COALESCE(retrieval_mode, '') != 'officer_reply'"]
    params: list[Any] = [since, until]
    if tenant_id is not None:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    params.append(MAX_ROWS + 1)
    rows = db.query_all(
        f"SELECT {_FEEDBACK_COLUMNS} FROM feedback WHERE {' AND '.join(clauses)} "  # noqa: S608 - fixed predicates
        "ORDER BY created_at DESC LIMIT ?",
        tuple(params),
    )
    truncated = len(rows) > MAX_ROWS
    return latest_per_answer(rows[:MAX_ROWS]), truncated


def ai_chat_summary(days: int, *, tenant_id: str | None = None, now: float | None = None) -> dict[str, Any]:
    """This period's figures, plus the previous period's headline for an exact change."""
    now = time.time() if now is None else now
    span = days * 86400
    rows, truncated = _rated_answers(now - span, now, tenant_id)
    prev_rows, prev_truncated = _rated_answers(now - 2 * span, now - span, tenant_id)
    prev_helpful = sum(1 for r in prev_rows if r.get("rating") == "up")
    return {
        "period_days": days,
        "truncated": truncated,
        **summarise(rows),
        "previous": {
            "rated_answers": len(prev_rows),
            "helpful_pct": _share(prev_helpful, len(prev_rows)),
            "truncated": prev_truncated,
        },
    }


def rated_conversations(
    days: int, *, tenant_id: str | None = None, now: float | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Every rated conversation of the period, as rows (and whether the period was truncated)."""
    now = time.time() if now is None else now
    rows, truncated = _rated_answers(now - days * 86400, now, tenant_id)
    return [conversation_row(k, v) for k, v in group_conversations(rows).items()], truncated


def ai_chat_conversations(
    days: int,
    *,
    tenant_id: str | None = None,
    pattern: str = "",
    topic: str = "",
    locale: str = "",
    with_comment: bool = False,
    limit: int = 25,
    offset: int = 0,
    now: float | None = None,
) -> dict[str, Any]:
    """One page of rated conversations, the worst patterns and the newest first."""
    convs, truncated = rated_conversations(days, tenant_id=tenant_id, now=now)
    if pattern:
        convs = [c for c in convs if c["pattern"] == pattern]
    if topic:
        convs = [c for c in convs if (c["topic"] or "unclassified") == topic]
    if locale:
        convs = [c for c in convs if (c["locale"] or "unknown") == locale]
    if with_comment:
        convs = [c for c in convs if c["comments"]]
    rank = {p: i for i, p in enumerate(PATTERNS)}
    convs.sort(key=lambda c: (rank[c["pattern"]], -c["last_rated_at"]))
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    return {
        "period_days": days,
        "truncated": truncated,
        "total": len(convs),
        "limit": limit,
        "offset": offset,
        "conversations": convs[offset : offset + limit],
    }


def _feedback_for(conversation_id: str, tenant_id: str | None) -> list[dict[str, Any]]:
    clauses = ["conversation_id = ?"]
    params: list[Any] = [conversation_id]
    if tenant_id is not None:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    return db.query_all(
        f"SELECT {_FEEDBACK_COLUMNS} FROM feedback WHERE {' AND '.join(clauses)} "  # noqa: S608 - fixed predicates
        "ORDER BY created_at",
        tuple(params),
    )


def _transcript_for(conversation_id: str, tenant_id: str | None) -> list[dict[str, Any]]:
    clauses = ["conversation_id = ?"]
    params: list[Any] = [conversation_id]
    if tenant_id is not None:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    return db.query_all(
        "SELECT id, user_message, bot_reply, created_at, sources, confidence, topic_tag, locale "
        f"FROM conversations WHERE {' AND '.join(clauses)} ORDER BY created_at",  # noqa: S608 - fixed predicates
        tuple(params),
    )


def ai_chat_timeline(conversation_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
    """Every answer of one conversation in order, with its rating and where it went wrong.

    The transcript (``conversations``) is kept ``CONVERSATION_TTL_DAYS`` and a
    rating 90 days, so an older conversation shows its rated answers only —
    from the copy each rating took when it was given — and says so.
    """
    ratings = latest_per_answer(_feedback_for(conversation_id, tenant_id))
    transcript = _transcript_for(conversation_id, tenant_id)
    if not ratings and not transcript:
        return None
    by_turn = {r["turn_id"]: r for r in ratings if r.get("turn_id")}
    by_index = {r["turn_index"]: r for r in ratings if not r.get("turn_id") and r.get("turn_index")}
    used: set[str] = set()
    answers: list[dict[str, Any]] = []
    for n, row in enumerate(transcript, start=1):
        rating = by_turn.get(row["id"]) or by_index.get(n)
        if rating is not None:
            used.add(rating["id"])
        answers.append(
            {
                "answer": n,
                "turn_id": row["id"],
                "question": row.get("user_message") or "",
                "reply": row.get("bot_reply") or "",
                "created_at": row.get("created_at"),
                "sources": _sources(row.get("sources")),
                "confidence": row.get("confidence"),
                "topic": row.get("topic_tag") or "",
                "rating": rating.get("rating") if rating else None,
                "comment": (rating.get("comment") or "") if rating else "",
                "retrieval_mode": (rating.get("retrieval_mode") or "") if rating else "",
            }
        )
    # Ratings whose answer is no longer in the transcript: shown from their own copy.
    for r in ratings:
        if r["id"] in used:
            continue
        answers.append(
            {
                "answer": r.get("turn_index") or None,
                "turn_id": r.get("turn_id") or "",
                "question": r.get("user_query") or "",
                "reply": r.get("bot_reply") or "",
                "created_at": r.get("created_at"),
                "sources": _sources(r.get("sources_json")),
                "confidence": r.get("confidence"),
                "topic": r.get("topic_tag") or "",
                "rating": r.get("rating"),
                "comment": r.get("comment") or "",
                "retrieval_mode": r.get("retrieval_mode") or "",
            }
        )
    answers.sort(key=lambda a: (a["answer"] or 10**6, a["created_at"] or 0))
    rated = [a for a in answers if a["rating"]]
    pattern, brk = classify([str(a["rating"]) for a in rated])
    break_answer = rated[brk]["answer"] if brk is not None else None
    for a in answers:
        a["is_break"] = brk is not None and a is rated[brk]
    return {
        "conversation_id": conversation_id,
        "pattern": pattern,
        "break_answer": break_answer,
        "transcript_complete": bool(transcript) and len(used) == len(ratings),
        "transcript_kept_days": db._CONVERSATION_TTL_DAYS,
        "answers": answers,
    }
