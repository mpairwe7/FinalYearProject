"""Service-quality audit routes for administrators and auditors (``/v1/admin/quality/*``).

Read-only views over what taxpayers said about the service: the AI's chat
answers (``quality_audit``) and the satisfaction ratings of officer chats and
calls (``service_ratings``; docs/plans/admin-service-quality-audit-plan.md). Officers do not
read these: auditing the service is not the job of the people giving it.

Every route here is a staff read of taxpayer text (already redacted when it
was stored); opening one conversation's timeline is recorded in the audit
ledger as ``staff.conversation_viewed``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, HTTPException, Path, Query, Request

from ..models import QualityReviewRequest

if TYPE_CHECKING:
    from ..auth import AuthContext

router = APIRouter(tags=["admin"])

#: The audit's periods: the console's PeriodPicker offers the same four.
_DAYS = Query(30, ge=1, le=90, description="Period in days")


def _audit_reader(request: Request) -> AuthContext:
    """Administrators and auditors; officers and taxpayers are refused."""
    from ..main import _require_audit_reader, require_admin_access

    ctx = require_admin_access(request)
    _require_audit_reader(ctx)
    return ctx


def _tenant_scope(ctx: AuthContext) -> str | None:
    from ..tenancy import tenant_enabled

    return (ctx.tenant_id or "default") if tenant_enabled() else None


@router.get("/v1/admin/quality/summary")
def quality_summary(request: Request, days: int = _DAYS) -> dict[str, Any]:
    """Headline figures for the audit overview: one block per stream audited so far."""
    from .. import quality_audit

    from .. import audit_reviews, service_ratings

    ctx = _audit_reader(request)
    tenant = _tenant_scope(ctx)
    return {
        "period_days": days,
        # What the queue holds for a person to look at (the overview's panel).
        "needs_review": audit_reviews.counts(days, tenant_id=tenant),
        "ai_chat": quality_audit.ai_chat_summary(days, tenant_id=tenant),
        "officer_chat": service_ratings.headline("chat_officer_csat", days, tenant_id=tenant),
        # The caller's spoken review of the AI: at goodbye, and while transferring.
        "ai_calls": {
            "end": service_ratings.headline("call_ai_review_end", days, tenant_id=tenant),
            "transfer": service_ratings.headline("call_ai_review_transfer", days, tenant_id=tenant),
        },
        "officer_calls": service_ratings.headline("call_officer_csat", days, tenant_id=tenant),
    }


@router.get("/v1/admin/quality/ai-chat")
def quality_ai_chat(
    request: Request,
    days: int = _DAYS,
    pattern: str = Query("", pattern=r"^(|went_wrong|never_helpful|recovered|all_helpful)$"),
    topic: str = Query("", max_length=64),
    locale: str = Query("", max_length=16),
    with_comment: bool = Query(False),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
) -> dict[str, Any]:
    """How the AI's chat answers were rated: breakdowns plus one page of conversations."""
    from .. import quality_audit

    ctx = _audit_reader(request)
    tenant = _tenant_scope(ctx)
    return {
        "summary": quality_audit.ai_chat_summary(days, tenant_id=tenant),
        "page": quality_audit.ai_chat_conversations(
            days,
            tenant_id=tenant,
            pattern=pattern,
            topic=topic,
            locale=locale,
            with_comment=with_comment,
            limit=limit,
            offset=offset,
        ),
    }


@router.get("/v1/admin/quality/ai-chat/{conversation_id}")
def quality_ai_chat_timeline(
    request: Request,
    conversation_id: str = Path(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$"),
) -> dict[str, Any]:
    """One conversation, answer by answer, with its ratings and the point it went wrong."""
    from .. import quality_audit
    from ..main import _audit_staff_action

    ctx = _audit_reader(request)
    timeline = quality_audit.ai_chat_timeline(conversation_id, tenant_id=_tenant_scope(ctx))
    if timeline is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    _audit_staff_action(ctx, "staff.conversation_viewed", {"conversation_id": conversation_id})
    return timeline


#: The rating streams ``/ratings`` reads (``service_ratings.SOURCES``).
_SOURCE_PATTERN = r"^(chat_officer_csat|call_officer_csat|call_ai_review_end|call_ai_review_transfer)$"


@router.get("/v1/admin/quality/ratings")
def quality_ratings(
    request: Request,
    source: str = Query("chat_officer_csat", pattern=_SOURCE_PATTERN),
    days: int = _DAYS,
    score: int | None = Query(None, ge=1, le=5),
    officer: str = Query("", max_length=128),
    status: str = Query("", pattern=r"^(|answered|skipped|no_answer)$"),
    sentiment: str = Query("", pattern=r"^(|positive|neutral|negative|unclear)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
) -> dict[str, Any]:
    """Ratings of one stream: figures, per officer, week and language, and the ratings, lowest first.

    The officer streams are rated 1–5; the caller's spoken review of the AI is
    rated in words (``sentiment``), and sorts negative first.

    The list never names the taxpayer; each rating names the ticket or call it
    rates, which the drill-down opens through its own audited read.
    """
    from .. import service_ratings

    ctx = _audit_reader(request)
    return service_ratings.audit(
        source,
        days,
        tenant_id=_tenant_scope(ctx),
        score=score,
        officer_id=officer,
        status=status,
        sentiment=sentiment,
        limit=limit,
        offset=offset,
    )


def _reviewer_name(ctx: AuthContext) -> str:
    """Who decided, as the queue shows it: their name, username or email (never a bare ``sub``)."""
    claims = ctx.claims or {}
    return (
        str(claims.get("name") or "").strip()
        or str(claims.get("given_name") or "").strip()
        or str(claims.get("preferred_username") or "").strip()
        or (ctx.user.email if ctx.user else "")
        or ctx.user_id
        or "operator key"
    )[:128]


@router.get("/v1/admin/quality/needs-review")
def quality_needs_review(
    request: Request,
    days: int = _DAYS,
    status: str = Query("open", pattern=r"^(open|action_needed|reviewed|all)$"),
    stream: str = Query("", pattern=r"^(|ai_chat|officer_chat|officer_call|ai_call)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
) -> dict[str, Any]:
    """The items across the four streams that call for a person to look, with what was decided.

    An AI chat that went wrong or was never found helpful, or a 👎 with a
    comment; an officer chat or call rated 1 or 2; a caller who said the AI
    did not help. Newest first. ``status=open`` is what nobody has decided yet.
    """
    from .. import audit_reviews

    ctx = _audit_reader(request)
    return audit_reviews.needs_review(
        days, tenant_id=_tenant_scope(ctx), status=status, stream=stream, limit=limit, offset=offset
    )


@router.put("/v1/admin/quality/reviews/{item_type}/{item_id}")
def quality_set_review(
    request: Request,
    item_type: str = Path(..., pattern=r"^(ai_chat|rating)$"),
    item_id: str = Path(..., pattern=r"^[A-Za-z0-9_:.-]{1,128}$"),
    body: QualityReviewRequest = Body(...),
) -> dict[str, Any]:
    """Mark an item Reviewed or Action needed, with a note; ``open`` reopens it.

    Administrators and auditors: like sealing the audit trail, it adds an
    audit note and changes no case. Recorded as ``staff.quality_reviewed``
    (the note as its length). 404 for an item the audit cannot see.
    """
    from .. import audit_reviews
    from ..main import _audit_staff_action

    ctx = _audit_reader(request)
    tenant = _tenant_scope(ctx)
    if not audit_reviews.item_exists(item_type, item_id, tenant_id=tenant):
        raise HTTPException(status_code=404, detail="item not found")
    review = audit_reviews.set_review(
        item_type,
        item_id,
        status=body.status,
        note=body.note,
        reviewer_id=ctx.user_id or "",
        reviewer_name=_reviewer_name(ctx),
        tenant_id=tenant,
    )
    _audit_staff_action(
        ctx,
        "staff.quality_reviewed",
        {"item_type": item_type, "item_id": item_id, "status": body.status, "note_chars": len(body.note.strip())},
    )
    return {"item_type": item_type, "item_id": item_id, "review": review}
