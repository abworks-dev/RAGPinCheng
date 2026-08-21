"""Pydantic request/response schemas for the HTTP layer."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    category_ids: list[str] | None = Field(default=None, max_length=20)
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


class ContentPermissionDefinitionDTO(BaseModel):
    key: str
    domain: str
    domain_label: str
    label: str
    description: str
    dependencies: list[str] = Field(default_factory=list)


class ContentPermissionCatalogResponse(BaseModel):
    schema_version: int
    permissions: list[ContentPermissionDefinitionDTO]


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


class AppSystemMetricsDTO(BaseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    memory_used_bytes: int | None = Field(default=None, ge=0)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    disk_used_bytes: int | None = Field(default=None, ge=0)
    disk_total_bytes: int | None = Field(default=None, ge=0)
    checked_at: int
    error_code: str | None = None


class GpuSystemMetricsDTO(BaseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    model_loaded: bool | None = None
    device_name: str | None = None
    vram_used_bytes: int | None = Field(default=None, ge=0)
    vram_total_bytes: int | None = Field(default=None, ge=0)
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    temperature_celsius: float | None = Field(default=None, ge=-50, le=150)
    inflight_requests: int | None = Field(default=None, ge=0)
    checked_at: int
    data_age_seconds: int | None = Field(default=None, ge=0)
    stale: bool = False
    error_code: str | None = None


class OfficeProcessingStatusDTO(BaseModel):
    enabled: bool
    mode: Literal["deployment_config"] = "deployment_config"
    disabled_reason: Literal["office_processing_disabled"] | None = None
    status: Literal["healthy", "degraded", "unavailable", "disabled"]
    checked_at: int
    error_code: str | None = None
    disk_free_mb: int = Field(ge=0)
    disk_minimum_mb: int = Field(ge=0)


class SystemOverviewResponse(BaseModel):
    topology: Literal["shared", "separate", "unknown"]
    checked_at: int
    app: AppSystemMetricsDTO
    gpu: GpuSystemMetricsDTO
    office_processing: OfficeProcessingStatusDTO
    external_usage: dict[str, dict[str, dict[str, int | None]]]


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


class MaintenanceSettingsDTO(BaseModel):
    conversation_cleanup_enabled: bool
    conversation_retention_days: int | None
    upload_max_file_mb: int
    upload_max_batch_files: int
    upload_max_batch_mb: int
    updated_at: int | None = None
    updated_by: int | None = None


class MaintenanceSettingsPatchRequest(BaseModel):
    conversation_cleanup_enabled: bool
    conversation_retention_days: int | None = Field(default=None, ge=7, le=3650)
    upload_max_file_mb: int = Field(ge=1, le=10240)
    upload_max_batch_files: int = Field(ge=1, le=10000)
    upload_max_batch_mb: int = Field(ge=1, le=102400)

    @model_validator(mode="after")
    def validate_upload_limits(self):
        if self.upload_max_batch_mb < self.upload_max_file_mb:
            raise ValueError("单批总大小不能小于单文件上限")
        return self


class CleanupPreviewResponse(BaseModel):
    retention_days: int | None
    conversations: int
    messages: int
    auth_sessions: int
    oldest_conversation_at: int | None = None
    newest_conversation_at: int | None = None


class MaintenanceRunDTO(BaseModel):
    id: int
    trigger_source: Literal["automatic", "manual"]
    status: Literal["succeeded", "failed"]
    retention_days: int | None
    deleted_conversations: int
    deleted_messages: int
    deleted_auth_sessions: int
    started_at: int
    finished_at: int
    error_summary: str | None = None


class MaintenanceStatusResponse(BaseModel):
    settings: MaintenanceSettingsDTO
    sweeper_interval_seconds: int
    last_run: MaintenanceRunDTO | None = None


class MaintenanceRunsResponse(BaseModel):
    runs: list[MaintenanceRunDTO]


class AnswerPolicyDTO(BaseModel):
    answer_temperature: float = Field(ge=0, le=1)
    answer_max_output_tokens: int = Field(ge=256, le=4096)
    answer_context_chars: int = Field(ge=2000, le=12000)
    relevance_gate_enabled: bool
    relevance_min_score: float = Field(ge=0)
    relevance_min_rrf: float = Field(ge=0)
    relevance_min_margin: float = Field(ge=0)
    policy_version: str
    updated_at: int | None = None
    updated_by: int | None = None


class AnswerPolicyPatchRequest(BaseModel):
    answer_temperature: float = Field(ge=0, le=1)
    answer_max_output_tokens: int = Field(ge=256, le=4096)
    answer_context_chars: int = Field(ge=2000, le=12000)
    relevance_gate_enabled: bool
    relevance_min_score: float = Field(ge=0)
    relevance_min_rrf: float = Field(ge=0)
    relevance_min_margin: float = Field(ge=0)
    change_reason: str | None = Field(default=None, max_length=500)


class AnswerPolicyAuditDTO(BaseModel):
    id: int
    old_policy_json: str
    new_policy_json: str
    changed_by: int | None = None
    changed_by_name: str | None = None
    change_reason: str | None = None
    created_at: int


class AnswerPolicyAuditResponse(BaseModel):
    entries: list[AnswerPolicyAuditDTO]


class CleanupResponse(BaseModel):
    run_id: int
    retention_days: int | None
    deleted_conversations: int
    deleted_messages: int
    deleted_auth_sessions: int
    started_at: int
    finished_at: int


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
    category_kind: Literal["folder", "shared_folder"] = "folder"
    external_source_id: str | None = None
    sort_order: int
    level: int
    is_active: bool
    chat_search_enabled: bool
    chat_filter_selectable: bool
    chat_search_effective: bool = True
    chat_filter_effective: bool = True
    chat_search_inherited: bool = False
    chat_filter_inherited: bool = False
    version: int
    created_at: int
    updated_at: int
    full_path: str = ""
    item_count: int = 0
    direct_child_count: int = 0
    total_child_count: int = 0
    total_item_count: int = 0


class CreateManagedCategoryRequest(BaseModel):
    category_key: str | None = Field(default=None, min_length=2, max_length=63)
    parent_id: str | None = None
    display_code: str = Field(min_length=1, max_length=12)
    display_name: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(default=0, ge=0, le=999_999)
    target_position: int | None = Field(default=None, ge=1, le=99_999)
    confirm_number_shift: bool = False


class CreateSharedFolderRequest(BaseModel):
    parent_id: str | None = None
    display_name: str = Field(min_length=1, max_length=100)
    target_position: int | None = Field(default=None, ge=1, le=99_999)
    confirm_number_shift: bool = False
    root_alias: str = Field(default="", max_length=64)
    relative_path: str = Field(default="", max_length=1000)
    unc_path: str | None = Field(default=None, max_length=2000)
    default_scheme_id: str = Field(min_length=1, max_length=100)
    auto_enqueue: bool = False
    scan_interval_seconds: int = Field(default=900, ge=60, le=86400)


class UpdateManagedCategoryRequest(BaseModel):
    display_code: str = Field(min_length=1, max_length=12)
    display_name: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(ge=0, le=999_999)
    is_active: bool
    chat_search_enabled: bool | None = None
    chat_filter_selectable: bool | None = None
    expected_version: int = Field(gt=0)


class KnowledgeScopeDTO(BaseModel):
    id: str
    parent_id: str | None
    display_code: str
    display_name: str
    full_path: str
    level: int
    descendant_count: int = 0
    chat_search_enabled: bool = True
    chat_filter_selectable: bool = True


class KnowledgeScopeResponse(BaseModel):
    scopes: list[KnowledgeScopeDTO]


class RenameManagedCategoryRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(gt=0)


class UpdateManagedCategorySortOrderRequest(BaseModel):
    sort_order: int = Field(ge=0, le=999_999)
    expected_version: int = Field(gt=0)


class UpdateManagedCategoryNumberRequest(BaseModel):
    target_position: int = Field(ge=1, le=99_999)
    confirm_number_shift: bool = False
    expected_version: int = Field(gt=0)


class MoveManagedCategoryRequest(BaseModel):
    target_parent_id: str | None = None
    before_category_id: str | None = None
    expected_version: int = Field(gt=0)


class DeleteManagedCategoryPreviewDTO(BaseModel):
    category_id: str
    parent_id: str | None
    display_name: str
    full_path: str
    version: int
    descendant_count: int
    folder_count: int
    content_count: int
    pending_request_count: int
    active_upload_count: int
    active_reclassification_count: int
    active_index_count: int
    archived_content_count: int
    active_content_count: int
    upload_batch_count: int
    media_transcript_count: int
    renumbered_sibling_count: int
    can_delete: bool
    can_force_delete: bool
    protected_category: bool


class DeleteManagedCategoryRequest(BaseModel):
    expected_version: int = Field(gt=0)
    confirmed: bool
    force: bool = False
    typed_path: str | None = Field(default=None, max_length=2000)


class DeleteManagedCategoryResponse(BaseModel):
    deleted_folder_count: int
    renumbered_sibling_count: int
    parent_id: str | None
    categories: list[ManagedCategoryDTO]
    force_delete: bool = False
    cleanup_status: Literal["succeeded", "partial"] | None = None
    cleanup_error_count: int = 0
    run_id: str | None = None
    deleted_item_count: int = 0
    deleted_upload_batch_count: int = 0
    deleted_index_job_count: int = 0
    qdrant_point_count: int = 0
    deleted_object_count: int = 0


class ManagedUploadPreflightEntryRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    relative_path: str | None = Field(default=None, max_length=1024)
    size_bytes: int = Field(default=0, ge=0)


class ManagedUploadPreflightRequest(BaseModel):
    category_id: str = Field(min_length=1, max_length=128)
    upload_mode: Literal["files", "folder"] = "files"
    allow_folder_merge: bool = False
    entries: list[ManagedUploadPreflightEntryRequest] = Field(min_length=1, max_length=500)


class ManagedUploadFilenameConflictDTO(BaseModel):
    item_id: str
    version_id: str
    title: str
    original_filename: str
    lifecycle_status: str
    has_published_head: bool
    can_update: bool


class ManagedUploadFolderConflictDTO(BaseModel):
    relative_path: str
    category_id: str
    category_path: str
    display_name: str
    suggested_name: str
    can_rename: bool


class ManagedUploadPreflightEntryDTO(BaseModel):
    sequence: int
    filename: str
    relative_path: str | None = None
    kind: Literal["document", "video"] = "document"
    status: Literal["ready", "conflict", "blocked"]
    reason: str | None = None
    reason_code: str | None = None
    suggested_filename: str | None = None
    conflict: ManagedUploadFilenameConflictDTO | None = None


class ManagedUploadPreflightResponse(BaseModel):
    entries: list[ManagedUploadPreflightEntryDTO]
    folder_conflicts: list[ManagedUploadFolderConflictDTO]


class ManagedUploadConflictAction(BaseModel):
    strategy: Literal["skip", "create", "rename", "update"] = "create"
    filename: str | None = Field(default=None, max_length=255)
    item_id: str | None = Field(default=None, max_length=128)
    expected_version_id: str | None = Field(default=None, max_length=128)


class ManagedUploadEntryDTO(BaseModel):
    filename: str
    kind: Literal["document", "video"] = "document"
    item_id: str | None = None
    version_id: str | None = None
    media_id: str | None = None
    transcription_job_id: str | None = None
    sha256: str | None = None
    status: Literal["accepted", "skipped"]
    reason: str | None = None
    reason_code: str | None = None
    resolution: Literal["created", "renamed", "updated"] | None = None


class ManagedUploadResponse(BaseModel):
    batch_id: str
    entries: list[ManagedUploadEntryDTO]


class ManagedUploadTaskEntryDTO(BaseModel):
    sequence: int
    filename: str
    relative_path: str | None = None
    kind: Literal["document", "video"] = "document"
    size_bytes: int
    status: Literal["accepted", "skipped"]
    reason: str | None = None
    item_id: str | None = None
    version_id: str | None = None
    media_id: str | None = None
    transcription_job_id: str | None = None
    failure_code: str | None = None
    created_at: int


class ManagedUploadTaskDTO(BaseModel):
    batch_id: str
    upload_mode: Literal["files", "folder"]
    status: Literal["processing", "completed", "partial_success", "failed"]
    target_category_id: str | None = None
    target_path: str
    total_files: int
    accepted_files: int
    skipped_files: int
    total_bytes: int
    total_uploaded_bytes: int
    video_count: int = 0
    transcribable_video_count: int = 0
    created_by_name: str
    created_at: int
    updated_at: int
    error_summary: str | None = None
    entries: list[ManagedUploadTaskEntryDTO] | None = None


class ManagedUploadTaskListResponse(BaseModel):
    tasks: list[ManagedUploadTaskDTO]
    total: int
    status_counts: dict[str, int]


class PublicationFailureDTO(BaseModel):
    code: str
    message: str
    retryable: bool
    recommended_action: str


class ManagedContentItemDTO(BaseModel):
    item_id: str
    title: str
    content_kind: str
    category_id: str
    category_key: str
    category_label: str
    category_path: str = ""
    media_id: str | None
    preview_parent_id: str | None = None
    preview_status: Literal["ready", "pending", "missing", "not_applicable"] = "not_applicable"
    version_id: str
    version_number: int
    original_filename: str
    doc_type: str
    lifecycle_status: str
    object_sha256: str | None
    source_origin: str
    source_batch_id: str | None
    source_rel_path: str | None = None
    is_current: bool
    has_published_head: bool = False
    latest_publication_status: str | None = None
    publication_attempt_count: int = 0
    publication_failure: PublicationFailureDTO | None = None
    latest_reviewed_by_name: str | None = None
    latest_reviewed_at: int | None = None
    latest_review_decision: str | None = None
    latest_review_note: str | None = None
    created_at: int
    updated_at: int
    archived_at: int | None = None
    archived_by_name: str | None = None
    pre_archive_lifecycle_status: str | None = None
    purge_eligible_at: int | None = None
    retention_status: Literal["retained", "expiring", "overdue"] | None = None
    retention_days_remaining: int | None = None
    media_duration_ms: int | None = None
    media_file_size: int | None = None
    has_pending_revision: bool = False
    reclassification_job_id: str | None = None
    reclassification_status: str | None = None
    media_status: str | None = None
    transcription_job_id: str | None = None
    transcription_job_status: str | None = None
    transcription_stage: str | None = None
    transcription_failure_classification: str | None = None
    review_status: str | None = None
    publication_status: str | None = None


class ManagedContentListResponse(BaseModel):
    items: list[ManagedContentItemDTO]
    total: int
    status_counts: dict[str, int]
    retention_counts: dict[str, int] = Field(default_factory=dict)


class DeleteManagedContentRequest(BaseModel):
    expected_version_id: str = Field(min_length=1, max_length=100)


class DeleteManagedContentResponse(BaseModel):
    item_id: str
    version_id: str
    archived_at: int
    previous_status: str
    publication_withdrawn: bool


class RestoreManagedContentRequest(BaseModel):
    expected_version_id: str = Field(min_length=1, max_length=100)
    target_category_id: str | None = Field(default=None, min_length=1, max_length=100)
    replace_conflict_item_id: str | None = Field(default=None, min_length=1, max_length=100)
    replace_conflict_expected_version_id: str | None = Field(
        default=None, min_length=1, max_length=100
    )


class RestoreManagedContentResponse(BaseModel):
    item_id: str
    version_id: str
    restored_status: str
    category_id: str
    moved_to_alternate_category: bool
    replaced_conflict: bool


class ContentTrashAuditEventDTO(BaseModel):
    event_type: Literal["content.archived", "content.restored"]
    actor_name: str | None = None
    created_at: int
    previous_status: str | None = None
    restored_status: str | None = None
    restore_strategy: str | None = None
    source_category_path: str | None = None
    target_category_path: str | None = None
    category_path: str | None = None
    archive_reason: str | None = None
    replaced_title: str | None = None
    replaced_filename: str | None = None


class MoveManagedContentRequest(BaseModel):
    target_category_id: str = Field(min_length=1, max_length=100)
    expected_version_id: str = Field(min_length=1, max_length=100)


class RenameManagedContentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    original_filename: str = Field(min_length=1, max_length=255)
    expected_version_id: str = Field(min_length=1, max_length=100)
    replace_conflict_item_id: str | None = Field(default=None, min_length=1, max_length=100)
    replace_conflict_expected_version_id: str | None = Field(
        default=None, min_length=1, max_length=100
    )


class CreateMediaMetadataRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)
    request_idempotency_key: str = Field(min_length=36, max_length=36)


class BulkManagedContentItemRef(BaseModel):
    item_id: str = Field(min_length=1, max_length=100)
    expected_version_id: str = Field(min_length=1, max_length=100)


class BulkMoveManagedContentRequest(BaseModel):
    items: list[BulkManagedContentItemRef] = Field(min_length=1)
    target_category_id: str = Field(min_length=1, max_length=100)


class BulkArchiveManagedContentRequest(BaseModel):
    items: list[BulkManagedContentItemRef] = Field(min_length=1)


class BulkRestoreManagedContentRequest(BaseModel):
    items: list[BulkManagedContentItemRef] = Field(min_length=1)
    target_category_id: str | None = Field(default=None, min_length=1, max_length=100)


class BulkRestorePreflightResultDTO(BaseModel):
    item_id: str
    version_id: str
    status: Literal["ready", "conflict", "inactive_category", "version_changed", "in_progress", "not_found"]
    message: str
    target_category_path: str | None = None


class BulkRestorePreflightResponse(BaseModel):
    results: list[BulkRestorePreflightResultDTO]
    ready: int
    blocked: int


class TrashExportRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    retention_status: Literal["retained", "expiring", "overdue"] | None = None
    archived_from: int | None = Field(default=None, ge=0)
    archived_to: int | None = Field(default=None, ge=0)
    category_id: str | None = Field(default=None, max_length=100)
    archived_by: str = Field(default="", max_length=100)
    sort_direction: Literal["asc", "desc"] = "desc"


class TrashSettingsDTO(BaseModel):
    cleanup_enabled: bool
    retention_days: int
    warning_days: int
    batch_limit: int
    updated_by: int | None = None
    updated_at: int


class UpdateTrashSettingsRequest(BaseModel):
    cleanup_enabled: bool
    retention_days: int = Field(ge=1, le=3650)
    warning_days: int = Field(ge=0, le=365)
    batch_limit: int = Field(ge=1, le=20)


class TrashPurgeRequest(BaseModel):
    items: list[BulkManagedContentItemRef] = Field(min_length=1)
    confirmation: str = Field(min_length=1, max_length=100)


class TrashPurgePreflightRequest(BaseModel):
    items: list[BulkManagedContentItemRef] = Field(min_length=1)


class TrashPurgeItemDTO(BaseModel):
    item_id: str
    version_id: str
    status: Literal["ready", "blocked"]
    reason: str | None = None
    title: str
    original_filename: str
    category_path: str
    size_bytes: int
    content_kind: Literal["document", "media_transcript"] = "document"
    media_count: int = 0
    transcript_version_count: int = 0
    artifact_count: int = 0
    index_job_count: int = 0


class TrashPurgePreflightResponse(BaseModel):
    items: list[TrashPurgeItemDTO]
    ready_count: int
    blocked_count: int
    total_size_bytes: int
    media_count: int = 0
    transcript_version_count: int = 0
    artifact_count: int = 0
    index_job_count: int = 0
    confirmation_phrase: str


class TrashPurgeRunDTO(BaseModel):
    id: str
    trigger_type: Literal["manual", "automatic"]
    status: Literal["running", "succeeded", "partial", "failed"]
    candidate_count: int
    succeeded_count: int
    failed_count: int
    actor_name: str | None = None
    created_at: int
    finished_at: int | None = None


class TrashPurgeResponse(BaseModel):
    run_id: str
    status: Literal["succeeded", "partial", "failed"]
    candidate_count: int
    succeeded_count: int
    failed_count: int


class BulkDownloadManagedContentRequest(BaseModel):
    version_ids: list[str] = Field(min_length=1)


class BulkOperationCategoryRef(BaseModel):
    category_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(gt=0)


class BulkOperationItemRef(BaseModel):
    item_id: str = Field(min_length=1, max_length=128)
    expected_version_id: str = Field(min_length=1, max_length=128)


class BulkOperationPreflightRequest(BaseModel):
    operation: Literal["move", "submit", "approve", "reject", "publish", "download", "delete", "force_delete"]
    categories: list[BulkOperationCategoryRef] = Field(default_factory=list)
    items: list[BulkOperationItemRef] = Field(default_factory=list)


class BulkOperationSelectionRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=5000)
    selected: bool


class BulkOperationExecuteRequest(BaseModel):
    target_category_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    confirmation: str | None = Field(default=None, max_length=2000)


class BulkOperationCategoryDTO(BaseModel):
    run_id: str
    category_id: str
    parent_id: str | None
    full_path: str
    archive_path: str
    version: int
    root_category_id: str
    is_root: bool
    eligible: bool
    selected: bool
    reason: str | None
    result_status: str
    result_message: str | None
    sort_order: int


class BulkOperationItemDTO(BaseModel):
    run_id: str
    item_id: str
    version_id: str
    category_id: str
    category_path: str
    archive_path: str
    title: str
    original_filename: str
    content_kind: str
    lifecycle_status: str
    object_sha256: str | None
    storage_rel_path: str | None
    size_bytes: int
    scope_source: Literal["category", "direct"]
    root_category_id: str | None
    eligible: bool
    selected: bool
    reason: str | None
    result_status: str
    result_message: str | None
    index_job_id: str | None
    sort_order: int


class BulkOperationDTO(BaseModel):
    id: str
    operation: str
    status: str
    actor_user_id: int | None
    target_category_id: str | None
    note: str | None
    source_json: str
    confirmation_phrase: str | None
    total_files: int
    selected_files: int
    completed_files: int
    failed_files: int
    total_folders: int
    total_bytes: int
    processed_bytes: int
    archive_filename: str | None
    error_summary: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    expires_at: int | None
    updated_at: int
    max_archive_bytes: int
    categories: list[BulkOperationCategoryDTO] = Field(default_factory=list)
    items: list[BulkOperationItemDTO] = Field(default_factory=list)


class CreateFolderRequest(BaseModel):
    parent_category_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)


class ReviewFolderRequest(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=1000)


class FolderRequestDTO(BaseModel):
    id: str
    parent_category_id: str
    parent_label: str = ""
    display_name: str
    status: Literal["pending", "approved", "rejected"]
    requester_name: str | None = None
    review_note: str | None = None
    created_category_id: str | None = None
    created_at: int
    updated_at: int
    reviewed_at: int | None = None


class BulkManagedContentRequest(BaseModel):
    version_ids: list[str] = Field(min_length=1)
    approved: bool | None = None
    note: str | None = Field(default=None, max_length=2000)
    category_id: str | None = None


class BulkManagedContentResultDTO(BaseModel):
    version_id: str
    item_id: str | None = None
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


class ManagedPreviewDTO(BaseModel):
    version_id: str
    preview_parent_id: str
    preview_status: Literal["ready"] = "ready"


class XMindTopicDTO(BaseModel):
    id: str
    title: str
    notes: str | None = None
    children: list["XMindTopicDTO"] = Field(default_factory=list)


class XMindSheetDTO(BaseModel):
    id: str
    title: str
    root_topic: XMindTopicDTO


class XMindPreviewDTO(BaseModel):
    version_id: str
    sheets: list[XMindSheetDTO]


class ContentReclassificationJobDTO(BaseModel):
    id: str
    item_id: str
    expected_version_id: str
    source_category_id: str
    target_category_id: str
    status: Literal["pending", "applying", "committing", "rolling_back", "succeeded", "failed"]
    qdrant_point_count: int
    parent_count: int
    error_code: str | None
    error_summary: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    updated_at: int


class ManagedIndexJobDTO(BaseModel):
    id: str
    publication_id: str
    version_id: str
    attempt_number: int
    status: str
    error_code: str | None
    error_summary: str | None
    failure: PublicationFailureDTO | None = None
    attempt_count: int = 1
    created_at: int
    started_at: int | None
    finished_at: int | None
    updated_at: int
    title: str | None = None
    original_filename: str | None = None
    doc_type: str | None = None
    category_id: str | None = None
    category_label: str | None = None
    category_path: str | None = None
    version_number: int | None = None
    file_size: int | None = None
    source_origin: str | None = None
    is_archived: bool = False
    is_current_head: bool = False
    is_latest_attempt: bool = True
    parent_count: int | None = None
    preview_parent_id: str | None = None


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
    content_item_id: str | None = None
    content_version_id: str | None = None
    transcript_version_id: str | None = None
    company: str | None = None
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
    transcription_job_status: str | None = None
    transcription_stage: str | None = None
    current_phase: Literal["upload", "transcription", "review", "publication", "index", "ready", "failed"] = "upload"
    review_status: str | None = None
    publication_status: str | None = None
    publication_index_status: str | None = None
    is_current_version: bool = False
    replacement_source_media_id: str | None = None
    replacement_candidate_media_id: str | None = None
    replacement_status: str | None = None
    category_id: str | None = None
    category_path: str | None = None
    catalog_item_id: str | None = None
    current_version_id: str | None = None
    storage_kind: Literal["managed", "external"] = "managed"
    external_source_id: str | None = None
    external_relative_path: str | None = None
    external_availability: Literal["available", "missing", "superseded"] | None = None
    available_actions: list[str] = Field(default_factory=list)
    disabled_actions: dict[str, str] = Field(default_factory=dict)


class MediaUploadPreflightItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)


class MediaUploadPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=1, max_length=100)
    items: list[MediaUploadPreflightItem] = Field(min_length=1, max_length=100)


class MediaUploadConflictDTO(BaseModel):
    media_id: str
    item_id: str | None = None
    version_id: str | None = None
    title: str
    original_filename: str
    title_matches: bool
    filename_matches: bool


class MediaUploadPreflightEntryDTO(BaseModel):
    client_id: str
    status: Literal["ready", "conflict", "ambiguous"]
    suggested_title: str | None = None
    suggested_filename: str | None = None
    conflicts: list[MediaUploadConflictDTO]


class MediaUploadPreflightResponse(BaseModel):
    category_id: str
    entries: list[MediaUploadPreflightEntryDTO]


class ExternalMediaRootDTO(BaseModel):
    alias: str


class ExternalMediaSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    root_alias: str = Field(min_length=1, max_length=64)
    relative_path: str = Field(default="", max_length=1000)
    target_category_id: str = Field(min_length=1, max_length=100)
    default_scheme_id: str = Field(min_length=1, max_length=100)
    auto_enqueue: bool = False
    scan_interval_seconds: int = Field(default=900, ge=60, le=86400)


class ExternalMediaSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    target_category_id: str = Field(min_length=1, max_length=100)
    default_scheme_id: str = Field(min_length=1, max_length=100)
    auto_enqueue: bool
    scan_interval_seconds: int = Field(ge=60, le=86400)
    enabled: bool
    expected_version: int = Field(gt=0)


class ExternalMediaSourceDTO(BaseModel):
    id: str
    name: str
    root_alias: str
    relative_path: str
    target_category_id: str
    default_scheme_id: str
    auto_enqueue: bool
    scan_interval_seconds: int
    enabled: bool
    status: Literal["never_scanned", "scanning", "available", "unavailable", "scan_failed"]
    total_files: int
    available_files: int
    missing_files: int
    last_scan_at: int | None
    last_successful_scan_at: int | None
    last_error_code: str | None
    created_at: int
    updated_at: int
    version: int


class ExternalMediaScanDTO(BaseModel):
    run_id: str
    source_id: str
    discovered_count: int
    added_count: int
    changed_count: int
    missing_count: int
    enqueued_count: int = 0
    enqueue_failures: int = 0


class ExternalMediaEntryDTO(BaseModel):
    id: str
    kind: Literal["folder", "video"]
    name: str
    relative_path: str
    file_size: int | None = None
    modified_ns: int | None = None
    availability: Literal["available", "missing", "superseded"] | None = None
    media_id: str | None = None
    media_status: str | None = None
    transcription_job_id: str | None = None
    transcription_job_status: str | None = None
    review_status: str | None = None
    publication_status: str | None = None
    index_status: str | None = None


class ExternalMediaEntryListDTO(BaseModel):
    source_id: str
    parent_relative_path: str
    entries: list[ExternalMediaEntryDTO]


class ExternalMediaEnqueueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_ids: list[str] | None = Field(default=None, max_length=500)


class ExternalMediaEnqueueResult(BaseModel):
    requested: int
    enqueued: int
    failed: int
    failures: dict[str, str]


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


class AsrSegmentationDTO(BaseModel):
    preset: Literal["natural", "balanced", "fine"]
    max_segment_duration_ms: int | None
    max_segment_chars: int
    max_merge_gap_ms: int


class AsrDecodeConfigDTO(BaseModel):
    service_profile_id: str
    model_name: str
    beam_size: int
    temperature: float
    hotword_count: int
    prompt_asset_id: str | None
    service_profile_config_hash: str | None
    qualification_policy: str | None


class AsrManagedProfileDTO(BaseModel):
    profile_id: str
    display_name: str
    description: str
    profile_version: str
    application_config_hash: str
    qualification: str
    admission: str
    availability: str
    unavailable_reason_code: str | None
    release_eligible: bool
    segmentation: AsrSegmentationDTO | None
    terminology_rule_set: str | None
    protected_terms: list[str]
    decode: AsrDecodeConfigDTO


class AsrServiceStatusDTO(BaseModel):
    status: Literal["disabled", "healthy", "degraded", "unavailable"]
    queue_depth: int | None = None
    queue_limit: int | None = None
    pause_reason: str | None = None


class AsrReleaseValidationDTO(BaseModel):
    status: Literal["disabled", "ready", "unavailable"]
    reason_code: Literal["asr_disabled", "profile_identity_unavailable"] | None = None


class AsrProfileReleaseRequestDTO(BaseModel):
    request_id: str
    profile_id: str
    profile_display_name: str
    profile_config_hash: str
    status: Literal["requested", "completed", "rejected", "cancelled"]
    request_reason: str | None
    requested_by_name: str | None
    created_at: int
    updated_at: int


class AsrProfileAuditEventDTO(BaseModel):
    event_id: int
    event_type: Literal["release_requested"]
    profile_id: str
    profile_display_name: str
    actor_name: str | None
    created_at: int


class AsrSettingsResponse(BaseModel):
    service: AsrServiceStatusDTO
    release_validation: AsrReleaseValidationDTO
    profiles: list[AsrManagedProfileDTO]
    release_requests: list[AsrProfileReleaseRequestDTO]
    audit_events: list[AsrProfileAuditEventDTO]


class TranscriptionBaseDTO(BaseModel):
    id: str
    provider: str
    model: str
    revision: str
    service_profile_id: str
    config_hash: str
    qualification: str
    admission: str
    availability: str
    capabilities: dict[str, object]
    defaults: dict[str, object]


class TranscriptionSchemeDTO(BaseModel):
    id: str
    name: str
    description: str
    base_id: str
    parameters: dict[str, object]
    config_hash: str
    enabled: bool
    archived: bool
    system_preset: bool
    sort_order: int
    version: int
    created_at: int
    updated_at: int


class TranscriptionSchemeOptionDTO(BaseModel):
    scheme_id: str
    name: str
    description: str
    base_id: str
    config_hash: str
    enabled: bool
    archived: bool
    sort_order: int
    version: int
    availability: str


class TranscriptionSchemeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    base_id: str = Field(..., min_length=2, max_length=80)
    parameters: dict[str, object] = Field(default_factory=dict)


class TranscriptionSchemeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    parameters: dict[str, object] | None = None
    enabled: bool | None = None
    archived: bool | None = None
    expected_version: int = Field(..., ge=1)


class TranscriptionSchemeCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class TranscriptionSchemeOrderItem(BaseModel):
    id: str
    expected_version: int | None = Field(default=None, ge=1)


class TranscriptionSchemeOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[TranscriptionSchemeOrderItem]
    expected_version: int | None = Field(default=None, ge=1)


class AsrProfileReleaseRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(..., min_length=3, max_length=64)
    request_idempotency_key: str = Field(..., min_length=36, max_length=36)
    request_reason: str | None = Field(default=None, max_length=500)


class TranscriptionJobDTO(BaseModel):
    job_id: str
    media_id: str
    attempt_number: int
    profile_id: str
    scheme_id: str | None = None
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
    scheme_id: str | None = None
    provider_key: str | None
    model_id: str | None
    model_revision: str | None
    markdown_storage_kind: str
    review_status: str
    reviewed_by: int | None
    reviewed_at: int | None
    review_note: str | None
    publication_status: str
    published_at: int | None
    supersedes_version_id: str | None
    derived_from_version_id: str | None
    edited_by: int | None
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


class CreateTranscriptRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str
    base_markdown_sha256: str
    request_idempotency_key: str


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
    relevance: dict = Field(default_factory=dict)
    policy_version: str | None = None
    answer_max_output_tokens: int | None = None
    answer_context_chars: int | None = None
    relevance_gate_enabled: bool | None = None


class DoneEvent(BaseModel):
    timings: dict[str, float]
    sources: list[SourceDTO]
    answer_text: str
    assistant_message_id: int | None = None
    history_chars: int
    budget: int
    finish_reason: str = "stop"
    policy_version: str | None = None
    answer_max_output_tokens: int | None = None
    answer_context_chars: int | None = None
    relevance_gate_enabled: bool | None = None


class RetryTranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    request_idempotency_key: str


class StartTranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme_id: str = Field(min_length=1, max_length=100)
    request_idempotency_key: str = Field(min_length=36, max_length=36)


class BulkStartTranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme_id: str = Field(min_length=1, max_length=100)
    request_idempotency_key: str = Field(min_length=36, max_length=36)
    media_ids: list[str] | None = Field(default=None, max_length=100)
    upload_batch_id: str | None = Field(default=None, max_length=100)
    category_id: str | None = Field(default=None, max_length=100)
    recursive: bool = False


class BulkTranscriptionItemDTO(BaseModel):
    media_id: str
    title: str
    original_filename: str
    category_path: str | None = None
    status: Literal["ready", "started", "already_started", "unavailable", "failed"]
    reason: str | None = None
    transcription_job_id: str | None = None


class BulkTranscriptionPreflightResponse(BaseModel):
    scheme_id: str
    items: list[BulkTranscriptionItemDTO]
    ready_count: int
    blocked_count: int


class BulkTranscriptionResponse(BaseModel):
    scheme_id: str
    items: list[BulkTranscriptionItemDTO]
    requested: int
    started: int
    failed: int


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
        content_item_id=d.get("content_item_id"),
        content_version_id=d.get("content_version_id"),
        transcript_version_id=d.get("transcript_version_id"),
        company=d.get("company"),
        sheet_name=d.get("sheet_name"),
        cell_range=d.get("cell_range"),
        slide_number=d.get("slide_number"),
        paragraph_anchor=d.get("paragraph_anchor"),
    )
