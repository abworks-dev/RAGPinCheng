"""Pydantic request/response schemas for the HTTP layer."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── auth ────────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=64)
    real_name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    employee_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AuthMeResponse(BaseModel):
    id: int
    employee_id: str
    real_name: str
    role: str  # 'user' | 'admin'
    csrf_token: str


# ── chat / conversations ────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    query: str | None = Field(default=None, min_length=1)
    categories: list[str] | None = None
    regenerate_assistant_message_id: int | None = None


class ConversationSummaryDTO(BaseModel):
    id: str
    title: str
    created_at: int
    updated_at: int
    turn_index: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummaryDTO]


class CreateConversationResponse(BaseModel):
    id: str
    title: str
    created_at: int
    updated_at: int
    turn_index: int


# ── admin ───────────────────────────────────────────────────────────────────


class AdminUserDTO(BaseModel):
    id: int
    employee_id: str
    real_name: str
    role: str
    is_active: bool
    created_at: int
    last_login_at: int | None
    conversation_count: int


class AdminUserListResponse(BaseModel):
    users: list[AdminUserDTO]


class AdminUserPatchRequest(BaseModel):
    is_active: bool | None = None
    role: str | None = None  # 'user' | 'admin'
    reset_password: str | None = None  # new plaintext password if non-null


class AdminStatsResponse(BaseModel):
    users_total: int
    users_active: int
    conversations_total: int
    conversations_7d: int
    messages_total: int
    messages_7d: int


class AdminConversationSummaryDTO(BaseModel):
    id: str
    title: str
    user_id: int
    employee_id: str
    real_name: str
    created_at: int
    updated_at: int
    turn_index: int


class AdminConversationListResponse(BaseModel):
    conversations: list[AdminConversationSummaryDTO]


class AdminFeedbackEntry(BaseModel):
    ts: str | None = None
    kind: str | None = None
    rating: str | None = None
    note: str | None = None
    parent_id: str | None = None
    doc_title: str | None = None
    section_path: str | None = None
    start_time: str | None = None
    category: str | None = None
    query: str | None = None
    answer_text: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    turn_index: int | None = None
    message_id: str | None = None


class AdminFeedbackResponse(BaseModel):
    entries: list[AdminFeedbackEntry]
    total: int


class SweepResponse(BaseModel):
    deleted_conversations: int
    deleted_auth_sessions: int


# ── admin: indexing ─────────────────────────────────────────────────────────


class IndexJobDTO(BaseModel):
    id: int
    user_id: int | None
    employee_id: str | None
    real_name: str | None
    filename: str
    category: str
    doc_type: str
    source_path: str
    file_size: int
    status: str
    error: str | None
    parents: int | None = None
    children: int | None = None
    created_at: int
    started_at: int | None
    finished_at: int | None


class IndexJobListResponse(BaseModel):
    jobs: list[IndexJobDTO]


class UploadResponse(BaseModel):
    accepted: list[IndexJobDTO]
    skipped: list[dict]  # [{filename, reason}] for files we refused


class IndexedDocumentDTO(BaseModel):
    source_path: str
    doc_title: str
    category: str
    doc_type: str
    company: str | None
    parent_count: int


class IndexedDocumentListResponse(BaseModel):
    documents: list[IndexedDocumentDTO]


class CategoryNodeDTO(BaseModel):
    name: str
    two_level: bool                # True if uploads should ask for a subcategory
    subcategories: list[str]       # existing second-level folder names (may be empty)


class CategoryTreeResponse(BaseModel):
    categories: list[CategoryNodeDTO]
    second_level_categories: list[str]  # names that REQUIRE a subcategory on upload


class DeleteDocumentRequest(BaseModel):
    source_path: str
    delete_file: bool = False


class DeleteDocumentResponse(BaseModel):
    parents_deleted: int
    file_deleted: bool


class SourceDTO(BaseModel):
    """Shape matches ChatSession._sources_for_ui()."""
    parent_id: str
    doc_title: str
    section_path: str
    category: str
    score: float
    rrf_score: float = 0.0
    text: str
    doc_type: str  # "pdf" | "transcript" | "docx" | "xlsx" | "pptx"
    start_time: str | None = None
    media_id: str | None = None
    # Office document fields
    sheet_name: str | None = None
    cell_range: str | None = None
    slide_number: int | None = None
    paragraph_anchor: str | None = None


class MediaAssetDTO(BaseModel):
    media_id: str
    title: str
    original_filename: str
    mime_type: str
    file_size: int
    transcript_origin: str | None = None
    status: str
    created_at: int
    updated_at: int
    error: str | None = None
    transcription_job_id: str | None = None


class TranscriptionProfileDTO(BaseModel):
    profile_id: str
    display_name: str
    description: str
    qualification: str
    admission: str
    availability: str
    unavailable_reason_code: str | None = None
    requires_review: bool
    auto_publish: bool
    auto_index: bool


class TranscriptionJobDTO(BaseModel):
    job_id: str
    media_id: str
    attempt_number: int
    profile_id: str
    status: str
    stage: str | None
    processed_ms: int
    total_ms: int
    failure_error_code: str | None
    error_summary: str | None
    result_version_id: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    updated_at: int


class RetryTranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    request_idempotency_key: str


class AnswerVersionDTO(BaseModel):
    id: int
    version_index: int
    content: str
    sources_for_ui: list[SourceDTO] | None = None
    created_at: int
    is_active: bool


class MessageDTO(BaseModel):
    id: int | None = None
    role: str
    content: str
    sources_for_ui: list[SourceDTO] | None = None
    created_at: int | None = None
    answer_versions: list[AnswerVersionDTO] | None = None


class ConversationStateDTO(BaseModel):
    """Full state of one conversation — what the SPA renders on resume."""
    id: str
    title: str
    user_id: int
    created_at: int
    updated_at: int
    turn_index: int
    messages: list[MessageDTO]


class ConfigResponse(BaseModel):
    embed_model: str
    reranker_model: str
    rerank_enabled: bool
    llm_model: str
    llm_rewrite_model: str
    collection: str


class HealthResponse(BaseModel):
    status: str
    children: int
    parents: int


class LLMHealthModel(BaseModel):
    model: str
    role: str  # "generation" | "rewrite"
    ok: bool
    latency_ms: int | None = None
    error: str | None = None


class LLMHealthResponse(BaseModel):
    ok: bool
    key_present: bool
    key_masked: str
    base_url: str
    checked_at: float
    cached: bool = False
    models: list[LLMHealthModel]


class CategoriesResponse(BaseModel):
    categories: list[str]


# SSE event payload helpers (not validated on the wire, but documented).
class PrepEvent(BaseModel):
    search_query: str
    rewrite_applied: bool
    history_chars: int
    budget: int
    fresh_count: int
    final_count: int
    used_sources: list[SourceDTO]
    no_source_fallback: bool = False


class DoneEvent(BaseModel):
    timings: dict[str, float]
    sources: list[SourceDTO]
    answer_text: str
    history_chars: int
    budget: int


class FeedbackRequest(BaseModel):
    """User feedback on either an assistant answer or a specific cited source."""
    conversation_id: str | None = None
    turn_index: int | None = None
    message_id: str | None = None
    kind: str  # "answer" | "citation"
    rating: str | None = None  # "up" | "down"
    note: str | None = None
    # Citation reports carry the offending source.
    parent_id: str | None = None
    doc_title: str | None = None
    section_path: str | None = None
    start_time: str | None = None
    category: str | None = None
    # Optional context for answer-level feedback.
    query: str | None = None
    answer_text: str | None = None


class FeedbackResponse(BaseModel):
    ok: bool


def source_to_dto(d: dict[str, Any]) -> SourceDTO:
    """Convert the dict shape from ChatSession._sources_for_ui to SourceDTO."""
    return SourceDTO(
        parent_id=d["parent_id"],
        doc_title=d["doc_title"],
        section_path=d.get("section_path") or "",
        category=d.get("category") or "",
        score=float(d.get("score") or 0.0),
        rrf_score=float(d.get("rrf_score") or 0.0),
        text=d.get("text") or "",
        doc_type=d.get("doc_type") or "pdf",
        start_time=d.get("start_time"),
        media_id=d.get("media_id"),
        sheet_name=d.get("sheet_name"),
        cell_range=d.get("cell_range"),
        slide_number=d.get("slide_number"),
        paragraph_anchor=d.get("paragraph_anchor"),
    )
