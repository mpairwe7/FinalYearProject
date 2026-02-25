"""Pydantic v2 request/response models for the URA Chatbot API."""

from pydantic import BaseModel, Field
from typing import Annotated


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="User message text")
    conversation_id: str | None = Field(
        None,
        pattern=r"^[a-zA-Z0-9_-]{1,64}$",
        description="Optional conversation/session id",
    )
    top_k: int = Field(4, ge=1, le=10, description="Number of passages to retrieve")


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = Field(default_factory=list)
    model: str = "ura-gemma-2-9b"
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
class Prediction(BaseModel):
    tag: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    label: str


class ClassifyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(1, ge=1, le=10)


class ClassifyResponse(BaseModel):
    predictions: list[Prediction]
    processing_time_ms: float = Field(..., ge=0.0)


# Annotated type so each string in the batch list is independently validated
ValidText = Annotated[str, Field(min_length=1, max_length=2000)]


class BatchClassifyRequest(BaseModel):
    texts: list[ValidText] = Field(..., min_length=1, max_length=50)


class BatchClassifyResult(BaseModel):
    text: str
    tag: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class BatchClassifyResponse(BaseModel):
    results: list[BatchClassifyResult]
    processing_time_ms: float = Field(..., ge=0.0)


# ---------------------------------------------------------------------------
# Tags & FAQ
# ---------------------------------------------------------------------------
class TagInfo(BaseModel):
    id: str
    name: str
    description: str


class TagListResponse(BaseModel):
    tags: list[TagInfo]
    total: int


class FAQItem(BaseModel):
    question: str
    answer: str
    source: str


class FAQResponse(BaseModel):
    tag: str
    faqs: list[FAQItem]
    total: int


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    model_loaded: bool
    tags_loaded: int = 0
