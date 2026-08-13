"""Pydantic request/response schemas for the HTTP layer."""
from __future__ import annotations

from typing import Any, Literal

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
    content_permissions: list[str] = Field(default_factory=list)


# ── chat / conversations ────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, min_length=1)
    categories: list[str] | None = None
    regenerate_assistant_message_id: int | None = None
    edit_user_message_id: int | None = None


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
    content_permissions: list[str] = Field(default_factory=list)


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
    feedback_id: str
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
    status: Literal["pending", "in_progress", "resolved", "archived"] = "pending"
    resolution: Literal["knowledge_fixed", "answer_improved", "no_action", "duplicate", "other"] | None = None
    admin_note: str | None = None
    assignee_user_id: int | None = None
    assignee_name: str | None = None
    updated_at: int | None = None
    resolved_at: int | None = None


class AdminFeedbackResponse(BaseModel):
    entries: list[AdminFeedbackEntry]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]


class AdminFeedbackPatchRequest(BaseModel):
    status: Literal["pending", "in_progress", "resolved", "archived"]
    resolution: Literal["knowledge_fixed", "answer_improved", "no_action", "duplicate", "other"] | None = None
    admin_note: str | None = Field(default=None, max_length=2000)


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
    source_exists: bool
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
    document_id: str
    display_path: str
    filename: str
    doc_title: str
    category: str
    doc_type: str
    company: str | None
    parent_count: int
    preview_parent_id: str | None = None
    media_id: str | None = None
    child_count: int | None = None
    file_size: int | None = None
    status: str
    is_indexed: bool
    latest_job_id: int | None = None
    error_summary: str | None = None
    uploaded_by: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


class IndexedDocumentListResponse(BaseModel):
    documents: list[IndexedDocumentDTO]
    total: int
    status_counts: dict[str, int]


class CategoryNodeDTO(BaseModel):
    name: str
    two_level: bool                # True if uploads should ask for a subcategory
    subcategories: list[str]       # existing second-level folder names (may be empty)


class CategoryTreeResponse(BaseModel):
    categories: list[CategoryNodeDTO]
    second_level_categories: list[str]  # names that REQUIRE a subcategory on upload


class ManagedCategoryDTO(BaseModel):
    id: str
    category_key: str
    parent_id: str | None
    display_code: str
    display_name: str
    sort_order: int
    level: int
    is_active: bool
    version: int
    created_at: int
    updated_at: int
    full_path: str = ""
    item_count: int = 0


class CreateManagedCategoryRequest(BaseModel):
    category_key: str | None = Field(default=None, min_length=2, max_length=63)
    parent_id: str | None = None
    display_code: str = Field(min_length=1, max_length=12)
    display_name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0


class UpdateManagedCategoryRequest(BaseModel):
    display_code: str = Field(min_length=1, max_length=12)
    display_name: str = Field(min_length=1, max_length=100)
    sort_order: int
    is_active: bool
    expected_version: int = Field(gt=0)


class ManagedUploadEntryDTO(BaseModel):
    filename: str
    item_id: str | None = None
    version_id: str | None = None
    sha256: str | None = None
    status: Literal["accepted", "skipped"]
    reason: str | None = None


class ManagedUploadResponse(BaseModel):
    batch_id: str
    entries: list[ManagedUploadEntryDTO]


class ManagedContentItemDTO(BaseModel):
    item_id: str
    title: str
    content_kind: str
    category_id: str
    category_key: str
    category_label: str
    category_path: str = ""
    media_id: str | None
    version_id: str
    version_number: int
    original_filename: str
    doc_type: str
    lifecycle_status: str
    object_sha256: str | None
    source_origin: str
    source_batch_id: str | None
    is_current: bool
    created_at: int
    updated_at: int


class ManagedContentListResponse(BaseModel):
    items: list[ManagedContentItemDTO]
    total: int
    status_counts: dict[str, int]


class BulkManagedContentRequest(BaseModel):
    version_ids: list[str] = Field(min_length=1, max_length=20)
    approved: bool | None = None
    note: str | None = Field(default=None, max_length=2000)
    category_id: str | None = None


class BulkManagedContentResultDTO(BaseModel):
    version_id: str
    status: Literal["succeeded", "failed"]
    message: str | None = None
    index_job_id: str | None = None


class BulkManagedContentResponse(BaseModel):
    results: list[BulkManagedContentResultDTO]
    succeeded: int
    failed: int


class ReviewManagedContentRequest(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=2000)
    category_id: str | None = None


class ManagedPublicationDTO(BaseModel):
    publication_id: str
    index_job_id: str
    status: str


class ManagedIndexJobDTO(BaseModel):
    id: str
    publication_id: str
    version_id: str
    attempt_number: int
    status: str
    error_code: str | None
    error_summary: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    updated_at: int
    title: str | None = None
    original_filename: str | None = None
    category_label: str | None = None


class ManagedIndexJobListResponse(BaseModel):
    jobs: list[ManagedIndexJobDTO]
    total: int
    status_counts: dict[str, int]


class ContentPermissionUserDTO(BaseModel):
    user_id: int
    employee_id: str
    real_name: str
    role: str
    is_active: bool
    permissions: list[str]


class UpdateContentPermissionsRequest(BaseModel):
    permissions: list[str]


class ContentPermissionGroupDTO(BaseModel):
    id: str
    group_key: str
    display_name: str
    permissions: list[str]
    is_system: bool
    is_active: bool
    updated_at: int


class CreateContentPermissionGroupRequest(BaseModel):
    display_name: str = Field(..., min_length=2, max_length=30)
    permissions: list[str]


class UpdateContentPermissionGroupRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=30)
    permissions: list[str] | None = None
    is_active: bool | None = None


class DeleteDocumentRequest(BaseModel):
    document_id: str = Field(min_length=24, max_length=24)
    delete_file: bool = False


class DeleteDocumentResponse(BaseModel):
    parents_deleted: int
    file_deleted: bool
    file_delete_status: Literal["not_requested", "deleted", "missing", "failed"]


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
    review_status: str | None = None
    publication_status: str | None = None
    publication_index_status: str | None = None
    is_current_version: bool = False


class MediaTranscriptSegmentDTO(BaseModel):
    id: int
    start_ms: int
    end_ms: int | None = None
    text: str


class MediaTranscriptDTO(BaseModel):
    media_id: str
    version_id: str | None = None
    language: str | None = None
    duration_ms: int | None = None
    segments: list[MediaTranscriptSegmentDTO]


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
    failure: "TranscriptionFailureDTO | None" = None
    result_version_id: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    updated_at: int


class TranscriptionFailureDTO(BaseModel):
    code: str
    message: str
    retryable: bool


class TranscriptVersionDTO(BaseModel):
    version_id: str
    media_id: str
    source: str
    profile_id: str | None
    provider_key: str | None
    model_id: str | None
    model_revision: str | None
    review_status: str
    reviewed_by: int | None
    reviewed_at: int | None
    review_note: str | None
    publication_status: str
    published_at: int | None
    supersedes_version_id: str | None
    markdown_sha256: str
    created_at: int
    updated_at: int
    is_current: bool = False


class TranscriptMarkdownPreviewDTO(BaseModel):
    version_id: str
    markdown: str
    markdown_sha256: str


class ReviewTranscriptVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    review_note: str | None = None


class PublishTranscriptVersionRequest(BaseModel):
    """Strict empty command body; all publication controls are server-owned."""

    model_config = ConfigDict(extra="forbid")


class TranscriptPublicationJobDTO(BaseModel):
    index_job_id: str
    transcript_version_id: str
    attempt_number: int
    target_index_id: str
    status: str
    error_code: str | None
    error_summary: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    updated_at: int


class PublishTranscriptVersionResponse(BaseModel):
    version: TranscriptVersionDTO
    job: TranscriptPublicationJobDTO | None
    reused: bool


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
    user_version_id: int | None = None


class UserQuestionVersionDTO(BaseModel):
    id: int
    version_index: int
    content: str
    created_at: int
    is_active: bool


class MessageDTO(BaseModel):
    id: int | None = None
    role: str
    content: str
    sources_for_ui: list[SourceDTO] | None = None
    created_at: int | None = None
    answer_versions: list[AnswerVersionDTO] | None = None
    user_versions: list[UserQuestionVersionDTO] | None = None


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
    assistant_message_id: int | None = None
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
