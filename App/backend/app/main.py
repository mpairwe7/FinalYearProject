"""URA Chatbot API – FastAPI application.

Hardened for production per OWASP LLM Top 10 (2025), NIST SSDF, and
ISO/IEC 42001:2023 security controls.
"""

import os
import re
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .models import (
    ChatRequest,
    ChatResponse,
    ClassifyRequest,
    ClassifyResponse,
    TagListResponse,
    FAQResponse,
    BatchClassifyRequest,
    BatchClassifyResponse,
    HealthResponse,
)
from .service import ChatModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan – replaces deprecated @app.on_event("startup")
# ---------------------------------------------------------------------------
_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{1,128}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down the ChatModel singleton."""
    try:
        app.state.model = ChatModel()
        logger.info("ChatModel ready – %d tags loaded", len(app.state.model._faq_index))
    except Exception:
        logger.exception("ChatModel initialisation failed")
        app.state.model = None
    yield
    app.state.model = None
    logger.info("ChatModel shut down.")


app = FastAPI(
    title="URA Chatbot API",
    version="1.0.0",
    description="AI-powered customer-service chatbot for the Uganda Revenue Authority",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------
def get_model(request: Request) -> ChatModel:
    """Retrieve the ChatModel from app state; 503 if unavailable."""
    model = getattr(request.app.state, "model", None)
    if model is None:
        raise HTTPException(status_code=503, detail="Model not initialized")
    return model


# ---------------------------------------------------------------------------
# CORS – hardened (no wildcard, no credentials, explicit methods)
# ---------------------------------------------------------------------------
_allowed_origins: list[str] = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ---------------------------------------------------------------------------
# Security headers middleware (OWASP, NIST SSDF PW.6)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Validate X-Request-ID to prevent log injection (OWASP LLM05)
    raw_id = request.headers.get("X-Request-ID", "")
    request_id = raw_id if _REQUEST_ID_RE.match(raw_id) else str(uuid.uuid4())
    logger.info(
        "request",
        extra={"request_id": request_id, "method": request.method, "path": request.url.path},
    )
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["X-Request-ID"] = request_id
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# Liveness probe – always responds; does NOT depend on model
@app.get("/health", tags=["system"])
def health_liveness() -> dict:
    """Liveness probe for orchestrators (Docker, K8s)."""
    return {"status": "alive", "version": app.version}


# Readiness probe – confirms model is loaded
@app.get("/ready", response_model=HealthResponse, tags=["system"])
def health_readiness(model: ChatModel = Depends(get_model)) -> HealthResponse:
    """Readiness probe; returns 503 if model is unavailable."""
    return HealthResponse(
        status="ready",
        version=app.version,
        model_loaded=True,
        tags_loaded=len(model._faq_index),
    )


@app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
def chat(request: ChatRequest, model: ChatModel = Depends(get_model)) -> ChatResponse:
    result = model.generate(
        message=request.message,
        conversation_id=request.conversation_id,
        top_k=request.top_k,
    )
    return ChatResponse(**result)


@app.post("/classify", response_model=ClassifyResponse, tags=["classification"])
def classify(
    request: ClassifyRequest, model: ChatModel = Depends(get_model)
) -> ClassifyResponse:
    result = model.classify(text=request.text, top_k=request.top_k)
    return ClassifyResponse(**result)


@app.post("/classify/batch", response_model=BatchClassifyResponse, tags=["classification"])
def classify_batch(
    request: BatchClassifyRequest, model: ChatModel = Depends(get_model)
) -> BatchClassifyResponse:
    results = model.classify_batch(texts=request.texts)
    return BatchClassifyResponse(**results)


@app.get("/tags", response_model=TagListResponse, tags=["knowledge"])
def list_tags(model: ChatModel = Depends(get_model)) -> TagListResponse:
    result = model.list_tags()
    return TagListResponse(**result)


@app.get("/faq/{tag}", response_model=FAQResponse, tags=["knowledge"])
def get_faq(
    tag: str = Path(..., pattern=r"^[a-z][a-z0-9_]{0,63}$"),
    model: ChatModel = Depends(get_model),
) -> FAQResponse:
    result = model.get_faq(tag=tag)
    if result is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return FAQResponse(**result)
