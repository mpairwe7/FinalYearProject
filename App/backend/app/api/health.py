"""Health check: liveness, readiness, index freshness and the Prometheus scrape."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from ..observability.analytics import METRICS_CONTENT_TYPE, metrics
from ..auth import AuthContext
from ..config.settings import API_VERSION
from ..middleware.rate_limiter import limiter
from ..process_role import calls_process_status, process_role, refresh_call_process_gauge
from ..rag.pipeline import ChatModel
from ..rag.retriever import active_retrieval_mode
from .deps import get_model, require_admin_access
from .models import HealthResponse

router = APIRouter()


@router.get("/health", tags=["system"])
@limiter.limit("120/minute")
def health_liveness(request: Request) -> dict:
    """Liveness probe for orchestrators (Docker, K8s)."""
    return {"status": "alive", "version": API_VERSION}


@router.get("/ready", response_model=HealthResponse, tags=["system"])
def health_readiness(model: ChatModel = Depends(get_model)) -> HealthResponse:
    """Readiness probe; returns 503 if model is unavailable.

    Checks both FAQ index AND Qdrant retriever health.
    """
    # "hybrid" only when dense vectors are actually in play. A retriever that
    # fell back to SPARSE-ONLY — no embedder stamp on the collection, or no
    # sentence-transformers in the image — still had _retriever_ready True, so
    # this reported "hybrid" while BM25 did all the work. That is the field
    # operators read to decide whether retrieval is healthy, and on the
    # deployed Space it was saying yes while dense was off.
    #
    # "vector" is its own label, checked BEFORE _sparse_only: the CPU-only
    # deployments serve a sparse-only Qdrant collection whenever Vectorize is
    # unavailable, so _sparse_only stays True even while Vectorize is actively
    # serving dense queries (initialize() keeps that Qdrant state as a fallback
    # rather than discarding it). Reporting "sparse" there would be the same
    # false-negative this field was fixed to stop making. "vector" is also
    # deliberately not folded into "hybrid": Vectorize is dense-only with a
    # client-side lexical re-score, not Qdrant's reranked dense+BM25 fusion,
    # and operators reading this field should be able to tell them apart.
    retrieval_mode = active_retrieval_mode(
        model._retriever, ready=model._retriever_ready
    )
    qdrant_healthy = model._retriever.is_ready if model._retriever_ready else False
    # Optional capabilities report the backend they actually resolved to. Both
    # of these degrade silently by design — detection drops to a character
    # heuristic, translation drops to serving English — and both degradations
    # shipped unnoticed. An operator should not have to ask the assistant a
    # Luganda question to find out.
    from ..rag import generator
    from ..providers import sunbird
    from ..rag.query import language_detection_backend

    if sunbird.is_available():
        trans_cap = "sunbird"
    elif generator.is_available():
        trans_cap = "prompted_local"
    else:
        trans_cap = "unavailable"

    has_reranker = bool(getattr(getattr(model, "_retriever", None), "_reranker", None))
    dense_dev = str(getattr(getattr(model, "_retriever", None), "_dense_device", "cpu"))

    capabilities = {
        "language_detection": language_detection_backend(),
        "translation": trans_cap,
        "sunbird_accounts": sunbird.account_summary(),
        "retrieval": retrieval_mode,
        "reranker": "active" if has_reranker else "disabled",
        "dense_device": dense_dev,
        # The pgvector dense leg beside a sparse-only collection (ADR-0034): its
        # model and size, "off", or why it is unavailable. An unavailable leg
        # degrades to BM25 alone, which only this field shows.
        "local_dense": _local_dense_status(getattr(model, "_retriever", None)),
        "procedural_memory": "active",
        # Calls: "off", "ready", or "unavailable: <why>" when the receptionist
        # is switched on and could not start. The probe itself stays ready:
        # the chat does not depend on it. In the chat role (G306) the value is
        # the call process's own, read from its /ready, not this process's.
        "voice_receptionist": _receptionist_status(),
    }
    # The statutory kernel's state (ADR-0033): release, graph backend, semantic layer,
    # tables. Never fails the probe.
    from ..kernel.status import readiness_capabilities

    capabilities.update(readiness_capabilities())
    return HealthResponse(
        status="ready" if qdrant_healthy else "degraded",
        version=API_VERSION,
        model_loaded=True,
        tags_loaded=len(model._faq_index),
        retrieval_mode=retrieval_mode,
        capabilities=capabilities,
    )


def _receptionist_status() -> str:
    try:
        if process_role() == "chat":
            # This process never starts the receptionist; the call process's
            # own value is the answer (G306). The same answer sets
            # `receptionist_call_process_up` (G614), so /ready and the gauge
            # cannot disagree.
            status = calls_process_status()
            refresh_call_process_gauge(status)
            return status
        from .. import receptionist

        return receptionist.status()
    except Exception:  # noqa: BLE001 - never fails the probe
        return "unknown"


def _local_dense_status(retriever: Any) -> str:
    status = getattr(retriever, "local_dense_status", None)
    try:
        return str(status()) if callable(status) else "off"
    except Exception:  # noqa: BLE001 - never fails the probe
        return "unknown"


@router.get("/v1/index/freshness", tags=["system"])
def index_freshness() -> dict[str, Any]:
    """Last corpus-hash check (G27). Does not re-hash on this request.

    Cron writes the status file: ``python -m app.freshness --check --write-status``.
    Missing file means the check has not run yet — not that the index is fresh.
    ``ok`` says the index matches the shipped corpus. ``crawl`` says how old that
    corpus is against URA's site, and is ``null`` when no manifest shipped.
    """
    from ..freshness import load_crawl_status, load_status

    status = load_status()
    crawl = load_crawl_status()
    if status is None:
        return {"ok": None, "snapshot_missing": True, "checked_at": None, "crawl": crawl}
    return {**status, "crawl": crawl}


def _has_valid_metrics_token(request: Request) -> bool:
    """True when the request presents ``METRICS_TOKEN`` (the scrape credential).

    A dedicated, read-only secret: Prometheus needs to read ``/metrics`` and
    nothing else, so it should not hold a staff login or the break-glass
    ``INDEX_API_KEY``. Compared in constant time and never logged.
    """
    token = os.getenv("METRICS_TOKEN", "")
    if not token:
        return False
    return hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}")


def require_metrics_reader(request: Request) -> AuthContext:
    """``/metrics`` access: the scrape token, else the usual staff/operator check."""
    if _has_valid_metrics_token(request):
        return AuthContext()
    return require_admin_access(request)


@router.get("/metrics", tags=["system"])
@limiter.limit("30/minute")
def prometheus_metrics(
    request: Request,
    _ctx: AuthContext = Depends(require_metrics_reader),
) -> PlainTextResponse:
    """Prometheus metrics, aggregated across every API worker process."""
    return PlainTextResponse(content=metrics.to_prometheus(), media_type=METRICS_CONTENT_TYPE)
