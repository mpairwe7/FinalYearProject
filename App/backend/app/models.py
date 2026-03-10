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
# Feedback
# ---------------------------------------------------------------------------
class FeedbackRequest(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=128, description="ID of the bot message")
    rating: str = Field(..., pattern=r"^(up|down)$", description="Thumbs up or down")
    comment: str = Field("", max_length=1000, description="Optional text feedback")
    session_id: str | None = Field(None, max_length=128)
    user_query: str = Field("", max_length=2000, description="Original user question")
    bot_reply: str = Field("", max_length=5000, description="Bot response that was rated")


class FeedbackResponse(BaseModel):
    id: str
    message_id: str
    rating: str
    created_at: float


class FeedbackCommentRequest(BaseModel):
    comment: str = Field(..., min_length=1, max_length=1000, description="Follow-up comment text")


class FeedbackSummary(BaseModel):
    period_days: int
    total: int
    thumbs_up: int
    thumbs_down: int
    satisfaction_pct: float
    recent: list[dict]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
class AnalyticsEvent(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=100)
    event_data: dict = Field(default_factory=dict)
    session_id: str | None = Field(None, max_length=128)


class AnalyticsDashboard(BaseModel):
    uptime_seconds: float
    requests: dict
    chat: dict
    sessions: dict
    conversations: dict
    feedback: dict


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    model_loaded: bool
    tags_loaded: int = 0
