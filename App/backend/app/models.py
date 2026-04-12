"""Pydantic v2 request/response models for the URA Chatbot API."""

from typing import Annotated

from pydantic import BaseModel, Field


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
    locale: str = Field(
        "en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$", description="ISO 639-1 locale (e.g. en, lg)"
    )


class Citation(BaseModel):
    ref: str = Field(..., description="Reference marker, e.g. '[1]'")
    source: str = Field(..., description="Source file name")
    page: str = Field("", description="Page number (PDFs)")
    section: str = Field("", description="Section or heading title")
    passage: str = Field("", max_length=500, description="Relevant passage excerpt")


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list, description="Passage-level citations")
    faithfulness_score: float | None = Field(None, description="Grounding score 0-1")
    retrieval_mode: str = Field(
        "keyword",
        description="hybrid | keyword | blocked | abstained | clarification | escalated",
    )
    model: str = "ura-qwen2.5-3b-instruct"
    conversation_id: str | None = None
    locale: str = Field("en", description="Locale used for this response")
    escalation_required: bool = Field(False, description="Whether human review is needed")
    escalation_reason: str = Field("", description="Why escalation was triggered")
    # Phase 14-D — when the supervisor routes a query to the ticket
    # queue, the ticket id comes back to the frontend so the UI can
    # display "ticket 1234abcd" to the user.  Empty string when no
    # ticket was created.
    ticket_id: str = Field("", description="Escalation ticket id, if one was created")


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
# Speech (2026 — ASR / MT / TTS)
# ---------------------------------------------------------------------------
class TranscribeRequest(BaseModel):
    """ASR request body. Audio is sent separately as raw bytes in the POST body.

    The `/asr` endpoint accepts audio as an ``application/octet-stream`` body
    (raw PCM16 / float32) with the metadata passed via query params rather
    than a JSON body, mirroring the ``/v1/index`` pattern. This model is kept
    for documentation / OpenAPI schema only.
    """

    sample_rate: int = Field(16000, ge=8000, le=48000)
    language: str | None = Field(None, pattern=r"^[a-z]{2}$", description="ISO 639-1 hint")


class TranscribeResponse(BaseModel):
    text: str
    language: str | None = None
    duration_s: float | None = None
    latency_s: float | None = None
    rtf: float | None = None
    backend: str = "unknown"
    error: str | None = None


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str | None = Field(
        None,
        pattern=r"^[a-zA-Z0-9_\-]{1,64}$",
        description="Voice id (e.g. en_US-lessac-medium, luganda-vits-v1)",
    )
    language: str = Field("en", pattern=r"^[a-z]{2}$", description="ISO 639-1 language code")
    streaming: bool = Field(False, description="Emit audio as a sentence-chunked stream")


class SynthesizeResponse(BaseModel):
    sample_rate: int
    num_samples: int
    duration_s: float
    latency_s: float
    backend: str
    voice: str
    audio_base64: str = Field("", description="Base64-encoded WAV bytes")
    error: str | None = None


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    source_lang: str = Field("en", pattern=r"^[a-z]{2}$")
    target_lang: str = Field("lg", pattern=r"^[a-z]{2}$")


class TranslateResponse(BaseModel):
    text: str
    source_lang: str
    target_lang: str
    latency_s: float
    backend: str
    error: str | None = None


class VoiceChatRequest(BaseModel):
    """Compound voice chat: audio in -> ASR -> [MT] -> LLM -> [MT] -> TTS -> audio out."""

    language: str = Field("en", pattern=r"^[a-z]{2}$", description="User language (en or lg)")
    voice: str | None = Field(None, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    top_k: int = Field(4, ge=1, le=10)
    conversation_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    tts_enabled: bool = Field(True, description="Whether to synthesize audio for the reply")


class VoiceChatResponse(BaseModel):
    """Full round-trip voice chat result."""

    transcript: str = Field("", description="ASR transcript of user audio")
    transcript_language: str | None = None
    reply: str = Field("", description="LLM reply text (in user language)")
    reply_audio_base64: str = Field("", description="Base64-encoded WAV of narrated reply")
    sample_rate: int = 0
    duration_s: float = 0.0
    sources: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    faithfulness_score: float | None = None
    retrieval_mode: str = "keyword"
    asr_latency_s: float = 0.0
    mt_latency_s: float = 0.0
    llm_latency_s: float = 0.0
    tts_latency_s: float = 0.0
    total_latency_s: float = 0.0
    asr_backend: str = ""
    tts_backend: str = ""
    mt_backend: str = ""
    error: str | None = None


class SpeechHealthResponse(BaseModel):
    status: str
    enabled: bool
    asr_backend: str
    tts_backend: str
    mt_backend: str


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
class ExportConversationRequest(BaseModel):
    messages: list[dict] = Field(
        ..., min_length=1, description="List of {role, content, timestamp} dicts"
    )
    title: str = Field("Conversation Report", max_length=200)
    session_id: str = Field("", max_length=64)


class ExportTaxSummaryRequest(BaseModel):
    calculation: dict = Field(..., description="Tax calculation with items[] and total")
    taxpayer_ref: str = Field("", max_length=50)


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    model_loaded: bool
    tags_loaded: int = 0
    retrieval_mode: str = "keyword"
