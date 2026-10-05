"""The audit record for one answered chat turn.

Every transport that answers a taxpayer — ``/v1/chat``, the SSE stream, the
WebSocket stream, voice chat and the call receptionist — ends in
:func:`append_turn`, so the ledger holds one ``generate`` row per answer no
matter how it was delivered. Before this module only the REST path wrote a
row, which left the streamed answers the web client actually uses out of the
tamper-evident record.

The row is enough to reconstruct *why* an answer was given without becoming
a second copy of taxpayer data: question and reply are SHA-256 digests, and
the provenance block names the build, the prompt template, the indexed corpus
and the flag variants that produced the answer. The reply digest is taken on
the text that was actually served (after translation), not on the English
draft.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

#: Version of the ``generate`` payload layout. 1 had no channel, provenance
#: or citation digests; rows keep the layout they were written with.
TURN_PAYLOAD_SCHEMA = 2

_MAX_LISTED = 10
_PROVENANCE_TTL_S = 60.0
_provenance_cache: tuple[float, dict[str, Any]] | None = None
_provenance_lock = threading.Lock()


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _index_corpus_hash() -> str:
    try:
        from ..freshness import load_status

        status = load_status() or {}
    except Exception:
        logger.debug("Freshness status unavailable for audit provenance", exc_info=True)
        return ""
    return str(status.get("index_corpus_hash") or status.get("corpus_hash") or "")


def _prompt_template_sha256() -> str:
    try:
        from ..llm import SYSTEM_PROMPT
    except Exception:
        return ""
    return _sha256(SYSTEM_PROMPT)


def provenance() -> dict[str, Any]:
    """Build, prompt and corpus identity, cached briefly (read on every turn)."""
    global _provenance_cache
    now = time.monotonic()
    cached = _provenance_cache
    if cached is not None and now - cached[0] < _PROVENANCE_TTL_S:
        return dict(cached[1])
    with _provenance_lock:
        value = {
            "app_version": os.getenv("APP_VERSION", ""),
            "build_sha": os.getenv("APP_BUILD_SHA", ""),
            "prompt_template_sha256": _prompt_template_sha256(),
            "index_corpus_hash": _index_corpus_hash(),
        }
        _provenance_cache = (now, value)
    return dict(value)


def _citation_digests(citations: Iterable[Any]) -> list[str]:
    digests: list[str] = []
    for citation in list(citations or [])[:_MAX_LISTED]:
        if hasattr(citation, "model_dump"):
            citation = citation.model_dump()
        if not isinstance(citation, dict):
            citation = {"passage": str(citation)}
        key = "|".join(
            str(citation.get(field, "") or "") for field in ("source", "page", "section", "passage")
        )
        digests.append(_sha256(key))
    return digests


def _usage(message: str, reply: str, usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Provider-reported usage for the turn, else a labelled estimate."""
    if usage and usage.get("input_tokens") is not None:
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "source": str(usage.get("source") or "provider"),
        }
    try:
        from ..llm import count_tokens

        return {
            "input_tokens": count_tokens(message),
            "output_tokens": count_tokens(reply),
            "source": "estimate",
        }
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "source": "unavailable"}


def _judge_verdict(result: Mapping[str, Any]) -> str:
    judge = result.get("response_judge")
    if isinstance(judge, dict):
        return str(judge.get("verdict") or judge.get("status") or "")
    return ""


def build_turn_payload(
    *,
    message: str,
    result: Mapping[str, Any],
    channel: str,
    model_name: str = "",
    tool_calls: Iterable[Any] = (),
    tool_iterations: int = 0,
    agent_route: str = "",
    flag_variants: str = "",
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The ``generate`` payload: digests and decision context, never raw text."""
    from ..analytics import turn_outcome

    reply = str(result.get("reply", "") or "")
    sources = [str(source)[:200] for source in list(result.get("sources") or [])[:_MAX_LISTED]]
    return {
        "schema": TURN_PAYLOAD_SCHEMA,
        "channel": channel,
        "outcome": turn_outcome(result),
        "query_sha256": _sha256(message),
        "reply_sha256": _sha256(reply),
        "reply_locale": str(result.get("reply_locale") or result.get("locale") or "en"),
        "retrieval_mode": result.get("retrieval_mode", ""),
        "num_sources": len(result.get("sources") or []),
        "num_citations": len(result.get("citations") or []),
        "sources": sources,
        "citation_sha256": _citation_digests(result.get("citations") or []),
        "faithfulness_score": result.get("faithfulness_score"),
        "response_judge": _judge_verdict(result),
        "escalation_required": bool(result.get("escalation_required")),
        "escalation_reason": result.get("escalation_reason", ""),
        "model": result.get("model") or model_name,
        "locale": result.get("locale", "en"),
        "conversation_id": result.get("conversation_id") or "",
        "usage": _usage(message, reply, usage),
        "tool_calls": list(tool_calls or []),
        "tool_iterations": int(tool_iterations or 0),
        "agent_route": agent_route or "",
        "ticket_id": result.get("ticket_id", ""),
        "flag_variants_sha256": _sha256(flag_variants) if flag_variants else "",
        "provenance": provenance(),
    }


def append_turn(
    *,
    message: str,
    result: Mapping[str, Any],
    channel: str,
    session_id: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    model_name: str = "",
    tool_calls: Iterable[Any] = (),
    tool_iterations: int = 0,
    agent_route: str = "",
    usage: Mapping[str, Any] | None = None,
) -> bool:
    """Append the turn's ``generate`` row when ``audit_ledger`` is on.

    Returns whether a row was written. A failure never reaches the taxpayer,
    but it is no longer silent: it is logged at WARNING and counted in
    ``ura_audit_append_failed_total{event_type="generate"}``, which the
    ``AuditAppendFailing`` alert watches.
    """
    from ..flags import flags

    if not flags.is_enabled("audit_ledger"):
        return False
    try:
        from . import get_ledger

        variants = ""
        try:
            variants = json.dumps(flags.logged_variants(subject=user_id or None), sort_keys=True)
        except Exception:
            logger.debug("Flag variants unavailable for audit payload", exc_info=True)
        payload = build_turn_payload(
            message=message,
            result=result,
            channel=channel,
            model_name=model_name,
            tool_calls=tool_calls,
            tool_iterations=tool_iterations,
            agent_route=agent_route,
            flag_variants=variants,
            usage=usage,
        )
        get_ledger().append(
            event_type="generate",
            payload=payload,
            tenant_id=str(tenant_id or "default")[:128],
            user_id=str(user_id or session_id or "")[:128],
        )
        return True
    except Exception:
        from ..analytics import metrics

        metrics.inc("audit_append_failed_total", labels={"event_type": "generate"})
        logger.warning("Audit append failed for a %s turn", channel, exc_info=True)
        return False
