"""Pydantic v2 request/response models for the URA Chatbot API."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


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
    attachment_ids: list[Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]] = Field(
        default_factory=list,
        max_length=3,
        description="Ids of analysed documents (POST /v1/documents/analyze) to ground this turn",
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
        description=(
            "hybrid | hybrid_corrected | keyword | blocked | abstained | "
            "clarification | workflow | escalated"
        ),
    )
    model: str = "Qwen/Qwen3-8B"
    conversation_id: str | None = None
    locale: str = Field("en", description="Locale used for this response")
    escalation_required: bool = Field(False, description="Whether human review is needed")
    escalation_reason: str = Field("", description="Why escalation was triggered")
    agent_role: str = Field(
        "rag_answerer",
        description=(
            "Active system role for this turn, e.g. rag_answerer, workflow_guide, "
            "clarification_agent, or escalation_triage"
        ),
    )
    workflow: dict[str, Any] | None = Field(
        None,
        description="Workflow metadata for guided multi-step tasks, if active",
    )
    handoff: dict[str, Any] | None = Field(
        None,
        description="Structured human handoff packet, when escalation or review is recommended",
    )
    response_judge: dict[str, Any] | None = Field(
        None,
        description=(
            "Post-answer quality gate metadata describing whether the reply was "
            "approved, deterministically revised, or escalated"
        ),
    )
    next_actions: list[str] = Field(
        default_factory=list,
        description="UI-friendly next suggested actions for the user",
    )
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
    conversation_id: str | None = Field(None, description="Stable conversation thread id")
    reply: str = Field("", description="LLM reply text (in user language)")
    reply_audio_base64: str = Field("", description="Base64-encoded WAV of narrated reply")
    tts_skipped: bool = Field(
        False,
        description=(
            "True when reply narration was skipped because the request had "
            "already consumed the VOICE_CHAT_BUDGET_S time budget — the "
            "client should fetch audio via /v1/tts instead"
        ),
    )
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
# Streaming voice
# ---------------------------------------------------------------------------


class VoiceStreamConfig(BaseModel):
    """Initial config frame sent by client on WebSocket connect."""

    language: str = Field("en", pattern=r"^[a-z]{2}$")
    voice: str | None = Field(None, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    conversation_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    sample_rate: int = Field(16000, ge=8000, le=48000)
    vad_sensitivity: str = Field("medium", pattern=r"^(low|medium|high)$")
    tts_enabled: bool = True
    top_k: int = Field(4, ge=1, le=10)


class VoiceAuditEntry(BaseModel):
    """One row from the voice audit log."""

    id: str
    user_id: str = ""
    session_id: str
    event_type: str
    metadata: dict = Field(default_factory=dict)
    audio_hash: str = ""
    tenant_id: str = "default"
    created_at: float


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
# Document attachments
# ---------------------------------------------------------------------------
class DocumentFields(BaseModel):
    """URA-specific fields extracted from an attached document."""

    tins: list[str] = Field(default_factory=list, description="Uganda TIN numbers found")
    amounts: list[str] = Field(default_factory=list, description="UGX currency amounts found")
    dates: list[str] = Field(default_factory=list, description="Date strings found")
    references: list[str] = Field(
        default_factory=list, description="URA reference/assessment numbers found"
    )


class DocumentTableSummary(BaseModel):
    """Compact summary of one table or spreadsheet sheet."""

    name: str
    rows: int = Field(0, ge=0)
    cols: int = Field(0, ge=0)
    headers: list[str] = Field(default_factory=list)
    numeric_totals: dict[str, float] = Field(
        default_factory=dict, description="Per-column totals for numeric columns"
    )


class DocumentAnalysisResponse(BaseModel):
    """Result of POST /v1/documents/analyze."""

    document_id: str = Field(..., description="Opaque id used in chat attachment_ids")
    filename: str
    kind: str = Field(..., description="pdf | docx | xlsx | csv | image | text")
    size_bytes: int = Field(0, ge=0)
    doc_type: str = Field(
        "generic",
        description=(
            "receipt | tin_card | assessment | customs_declaration | "
            "filing_form | invoice | generic"
        ),
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Classification confidence")
    matched_keywords: list[str] = Field(default_factory=list)
    fields: DocumentFields = Field(default_factory=DocumentFields)
    tables: list[DocumentTableSummary] = Field(default_factory=list)
    text_preview: str = Field("", description="First characters of the extracted text")
    truncated: bool = Field(False, description="Whether extracted text was truncated")
    summary: str = Field("", description="Heuristic analysis summary")
    warnings: list[str] = Field(default_factory=list)
    expires_in_seconds: int = Field(0, ge=0, description="TTL until the document is purged")


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    model_loaded: bool
    tags_loaded: int = 0
    retrieval_mode: str = "keyword"


# ---------------------------------------------------------------------------
# Quantized Models (Phase 24)
# ---------------------------------------------------------------------------
class QuantizedModelInfo(BaseModel):
    """Metadata for a single quantized model variant."""

    name: str = Field(..., description="Model name, e.g. Qwen3-8B-Q4_K_M")
    format: str = Field(..., description="Quantization format: gguf, awq, gptq, onnx")
    quant_type: str = Field(..., description="Quantization type, e.g. Q4_K_M, w4-g128")
    size_mb: float = Field(..., ge=0, description="File size in megabytes")
    faithfulness_score: float | None = Field(None, ge=0, le=1, description="Measured faithfulness")
    faithfulness_drop_pct: float | None = Field(None, description="Drop from bfloat16 baseline (%)")
    wer_increase_pct: float | None = Field(None, description="WER increase from baseline (%)")
    sha256: str = Field("", description="SHA-256 checksum of the quantized artifact")
    created_at: str = Field("", description="ISO 8601 timestamp of quantization")
    status: str = Field("available", description="available | building | failed")


class QuantizedModelsResponse(BaseModel):
    """Response for GET /v1/models/quantized."""

    models: list[QuantizedModelInfo] = Field(default_factory=list)
    total: int = 0
    baseline_faithfulness: float | None = Field(None, description="bfloat16 baseline faithfulness")


# ---------------------------------------------------------------------------
# Offline RAG (Phase 25)
# ---------------------------------------------------------------------------
class OfflineBundleInfo(BaseModel):
    """Metadata for an offline knowledge bundle."""

    version: str = Field(..., description="Semantic version, e.g. 1.2.0")
    size_bytes: int = Field(..., ge=0, description="Compressed bundle size in bytes")
    size_mb: float = Field(..., ge=0, description="Compressed bundle size in MB")
    passage_count: int = Field(0, ge=0, description="Number of passages in the index")
    index_dim: int = Field(0, ge=0, description="Embedding dimension")
    sha256: str = Field("", description="SHA-256 of the compressed bundle")
    created_at: str = Field("", description="ISO 8601 creation timestamp")
    min_app_version: str = Field("", description="Minimum app version required")


class OfflineStatusResponse(BaseModel):
    """Response for GET /v1/offline/status."""

    available: bool = Field(False, description="Whether offline bundle is available on server")
    bundle: OfflineBundleInfo | None = None
    sync_enabled: bool = Field(False, description="Whether delta sync is enabled")
    last_sync_at: str | None = Field(None, description="ISO 8601 last sync timestamp")
    pending_chunks: int = Field(0, ge=0, description="Chunks awaiting sync")


class OfflineSyncRequest(BaseModel):
    """Client sends its current state for delta sync."""

    client_version: str = Field(..., description="Client's current bundle version")
    client_chunk_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="Map of chunk_id -> SHA-256 hash for client-side chunks",
    )
    device_id: str = Field("", max_length=128, description="Device identifier for sync tracking")
    max_download_bytes: int = Field(
        50_000_000, ge=0, description="Max bytes the client is willing to download"
    )


class OfflineSyncResponse(BaseModel):
    """Server response with delta information."""

    server_version: str = Field(..., description="Current server bundle version")
    needs_full_sync: bool = Field(False, description="True if delta sync is not possible")
    changed_chunks: list[dict] = Field(
        default_factory=list,
        description="List of {chunk_id, sha256, size_bytes, download_url} for changed chunks",
    )
    deleted_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Chunk IDs that should be removed from client",
    )
    total_download_bytes: int = Field(0, ge=0, description="Total bytes to download")
    estimated_sync_seconds: float = Field(0, ge=0, description="Estimated sync time")


class OfflineAdminStats(BaseModel):
    """Response for GET /v1/admin/offline_stats."""

    total_bundles_served: int = 0
    total_syncs_completed: int = 0
    total_sync_bytes: int = 0
    active_offline_devices: int = 0
    avg_sync_duration_s: float = 0.0
    bundle_version: str = ""
    bundle_size_mb: float = 0.0
    passage_count: int = 0
    offline_faithfulness: float | None = None
    last_bundle_built_at: str = ""


# ---------------------------------------------------------------------------
# Voice-First Mobile (Phase 27)
# ---------------------------------------------------------------------------
class VoiceVisionChatRequest(BaseModel):
    """Compound voice + vision chat: audio + image -> ASR -> OCR -> LLM -> TTS."""

    language: str = Field("en", pattern=r"^[a-z]{2}$", description="User language")
    voice: str | None = Field(None, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    top_k: int = Field(4, ge=1, le=10)
    conversation_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    tts_enabled: bool = Field(True, description="Whether to synthesize audio reply")
    ocr_enabled: bool = Field(True, description="Whether to extract text from image")


class VoiceVisionChatResponse(BaseModel):
    """Full round-trip voice + vision chat result."""

    transcript: str = Field("", description="ASR transcript of user audio")
    ocr_text: str = Field("", description="Extracted text from image/document")
    reply: str = Field("", description="LLM reply text")
    reply_audio_base64: str = Field("", description="Base64-encoded WAV of narrated reply")
    sample_rate: int = 0
    duration_s: float = 0.0
    sources: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    faithfulness_score: float | None = None
    retrieval_mode: str = "keyword"
    conversation_id: str | None = None
    asr_latency_s: float = 0.0
    ocr_latency_s: float = 0.0
    llm_latency_s: float = 0.0
    tts_latency_s: float = 0.0
    total_latency_s: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Cloudflare relay (internal-only; see /internal/cf-relay/* in main.py)
# ---------------------------------------------------------------------------
class CFRelayEmbedRequest(BaseModel):
    """Forwarded Workers AI embedding request.

    No ``model`` field on purpose: this relay exists solely for the
    dense-retrieval fallback, which always embeds with the same model
    (``@cf/baai/bge-m3``, hardcoded server-side in main.py). Accepting a
    caller-supplied model string would let request data influence the
    Cloudflare URL this process builds — a partial-SSRF shape CodeQL flags
    even with a regex constraint, since a pattern isn't a real allowlist.
    ``extra="forbid"`` so a ``model`` field in the request body is rejected
    outright rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(..., min_length=1, max_length=32)


class CFRelayVectorizeQueryRequest(BaseModel):
    """Forwarded Vectorize query request."""

    model_config = ConfigDict(extra="forbid")

    vector: list[float] = Field(..., min_length=1, max_length=4096)
    top_k: int = Field(10, ge=1, le=50)
    vector_filter: dict[str, Any] | None = None
