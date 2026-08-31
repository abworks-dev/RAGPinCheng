export type Source = {
  parent_id: string;
  doc_title: string;
  section_path: string;
  category: string;
  score: number;
  rrf_score: number;
  text: string;
  doc_type: string; // "pdf" | "transcript" | "doc" | "docx" | "xls" | "xlsx" | "ppt" | "pptx"
  start_time: string | null;
  media_id: string | null;
  content_item_id?: string | null;
  content_version_id?: string | null;
  transcript_version_id?: string | null;
  company?: string | null;
  // Office document fields
  sheet_name: string | null;
  cell_range: string | null;
  slide_number: number | null;
  paragraph_anchor: string | null;
  page_number?: number | null;
  page_end?: number | null;
  topic_id?: string | null;
  heading_anchor?: string | null;
  location_quote?: string | null;
  location_confidence?: string | null;
};

export type PrepData = {
  search_query: string;
  rewrite_applied: boolean;
  query_resolution?: {
    original_query: string;
    standalone_query: string;
    kind: "standalone" | "follow_up" | "clarification_required" | "topic_switch";
    confidence: number;
    referenced_turns: number[];
    fallback_reason: string;
  } | null;
  history_chars: number;
  budget: number;
  fresh_count: number;
  final_count: number;
  used_sources: Source[];
  no_source_fallback: boolean;
  relevance?: Record<string, unknown>;
  policy_version?: string;
  answer_max_output_tokens?: number;
  answer_context_chars?: number;
  relevance_gate_enabled?: boolean;
};

export type DoneData = {
  answer_text: string;
  assistant_message_id?: number | null;
  timings: Record<string, number>;
  sources: Source[];
  history_chars: number;
  budget: number;
  finish_reason?: string;
  relevance?: Record<string, unknown>;
  citation_diagnostics?: {
    status: "valid" | "no_answer" | "uncited" | "invalid_citations";
    candidate_count: number;
    citation_marker_count: number;
    cited_count: number;
    invalid_citation_numbers: number[];
    uncited_answer: boolean;
    uncited_statement_count: number;
    located_count: number;
    version_conflict: boolean;
  };
  policy_version?: string;
  answer_max_output_tokens?: number;
  answer_context_chars?: number;
  relevance_gate_enabled?: boolean;
};

export type AnswerPolicy = {
  answer_temperature: number;
  answer_max_output_tokens: number;
  answer_context_chars: number;
  relevance_gate_enabled: boolean;
  relevance_min_score: number;
  relevance_min_rrf: number;
  relevance_min_margin: number;
  policy_version: string;
  updated_at: number | null;
  updated_by: number | null;
};

export type AnswerPolicyAuditEntry = {
  id: number;
  old_policy_json: string;
  new_policy_json: string;
  changed_by: number | null;
  changed_by_name: string | null;
  change_reason: string | null;
  created_at: number;
};

export type ChatEvent =
  | { type: "prep"; data: PrepData }
  | { type: "token"; data: { text: string } }
  | { type: "done"; data: DoneData }
  | { type: "error"; data: { message: string } };

export type ChatStage = "retrieving" | "generating" | "streaming" | "done";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt?: number;
  query?: string;
  sources?: Source[];
  prep?: PrepData;
  done?: DoneData;
  streaming?: boolean;
  stopped?: boolean;
  regenerationStopped?: boolean;
  stage?: ChatStage;
  error?: string;
  answerVersions?: AnswerVersion[];
  allAnswerVersions?: AnswerVersion[];
  viewedVersionIndex?: number;
  userVersions?: UserQuestionVersion[];
  activeUserVersionId?: string;
  viewedUserVersionIndex?: number;
};

export type AnswerVersion = {
  id: string;
  versionIndex: number;
  content: string;
  sources?: Source[];
  isActive: boolean;
  userVersionId?: string;
};

export type UserQuestionVersion = {
  id: string;
  versionIndex: number;
  content: string;
  createdAt: number;
  isActive: boolean;
};

export type AuthUser = {
  id: number;
  employee_id: string;
  real_name: string;
  role: "user" | "admin";
  csrf_token: string;
  content_permissions?: ContentPermission[];
};

export type ContentPermission =
  | "workspace.view"
  | "item.view"
  | "item.download"
  | "category.view"
  | "item.upload"
  | "item.move_draft"
  | "item.archive_draft"
  | "item.publish"
  | "item.reclassify_published"
  | "item.archive_published"
  | "trash.view"
  | "trash.restore"
  | "trash.purge"
  | "trash.policy_manage"
  | "category.manage"
  | "category.force_delete"
  | "folder.request"
  | "folder.review"
  | "import.server"
  | "index.view";

export type ContentPermissionDefinition = {
  key: ContentPermission;
  domain: string;
  domain_label: string;
  label: string;
  description: string;
  dependencies: ContentPermission[];
};

export type ContentPermissionCatalog = {
  schema_version: number;
  permissions: ContentPermissionDefinition[];
};

export type Conversation = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  turn_index: number;
};

export type ConversationState = {
  id: string;
  title: string;
  user_id: number;
  created_at: number;
  updated_at: number;
  turn_index: number;
  messages: {
    id?: number;
    role: "user" | "assistant" | "system";
    content: string;
    sources_for_ui?: Source[] | null;
    created_at?: number;
    answer_versions?: {
      id: number;
      version_index: number;
      content: string;
      sources_for_ui?: Source[] | null;
      created_at: number;
      is_active: boolean;
      user_version_id?: number | null;
    }[] | null;
    user_versions?: {
      id: number;
      version_index: number;
      content: string;
      created_at: number;
      is_active: boolean;
    }[] | null;
  }[];
};

export type AdminUser = {
  id: number;
  employee_id: string;
  real_name: string;
  role: "user" | "admin";
  is_active: boolean;
  created_at: number;
  last_login_at: number | null;
  conversation_count: number;
  content_permissions: ContentPermission[];
};

export type AdminConversation = {
  id: string;
  title: string;
  user_id: number;
  employee_id: string;
  real_name: string;
  created_at: number;
  updated_at: number;
  turn_index: number;
};

export type AdminStats = {
  users_total: number;
  users_active: number;
  conversations_total: number;
  conversations_7d: number;
  messages_total: number;
  messages_7d: number;
};

export type AppSystemMetrics = {
  status: "healthy" | "degraded" | "unavailable";
  cpu_percent: number | null;
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  disk_used_bytes: number | null;
  disk_total_bytes: number | null;
  checked_at: number;
  error_code: string | null;
};

export type GpuSystemMetrics = {
  status: "healthy" | "degraded" | "unavailable";
  model_loaded: boolean | null;
  device_name: string | null;
  vram_used_bytes: number | null;
  vram_total_bytes: number | null;
  utilization_percent: number | null;
  temperature_celsius: number | null;
  inflight_requests: number | null;
  checked_at: number;
  data_age_seconds: number | null;
  stale: boolean;
  error_code: string | null;
};

export type SystemOverview = {
  topology: "shared" | "separate" | "unknown";
  checked_at: number;
  app: AppSystemMetrics;
  gpu: GpuSystemMetrics;
  office_processing: {
    enabled: boolean;
    mode: "deployment_config";
    disabled_reason: "office_processing_disabled" | null;
    status: "healthy" | "degraded" | "unavailable" | "disabled";
    checked_at: number;
    error_code: string | null;
    disk_free_mb?: number;
    disk_minimum_mb?: number;
  };
  external_usage: Record<"today" | "month" | "all", Record<string, {
    requests: number; successes: number; prompt_tokens: number;
    completion_tokens: number; total_tokens: number; item_count: number;
    input_bytes: number; avg_latency_ms: number | null;
  }>>;
};

export type MaintenanceSettings = {
  conversation_cleanup_enabled: boolean;
  conversation_retention_days: number | null;
  upload_max_file_mb: number;
  upload_max_batch_files: number;
  upload_max_batch_mb: number;
  updated_at: number | null;
  updated_by: number | null;
};

export type MaintenanceRun = {
  id: number;
  trigger_source: "automatic" | "manual";
  status: "succeeded" | "failed";
  retention_days: number | null;
  deleted_conversations: number;
  deleted_messages: number;
  deleted_auth_sessions: number;
  started_at: number;
  finished_at: number;
  error_summary: string | null;
};

export type MaintenanceStatus = {
  settings: MaintenanceSettings;
  sweeper_interval_seconds: number;
  last_run: MaintenanceRun | null;
};

export type CleanupPreview = {
  retention_days: number | null;
  conversations: number;
  messages: number;
  auth_sessions: number;
  oldest_conversation_at: number | null;
  newest_conversation_at: number | null;
};

export type CleanupResult = {
  run_id: number;
  retention_days: number | null;
  deleted_conversations: number;
  deleted_messages: number;
  deleted_auth_sessions: number;
  started_at: number;
  finished_at: number;
};

export type ManagedCategory = {
  id: string;
  category_key: string;
  parent_id: string | null;
  display_code: string;
  display_name: string;
  category_kind?: "folder" | "shared_folder";
  external_source_id?: string | null;
  external_relative_path?: string | null;
  sort_order: number;
  level: number;
  is_active: boolean;
  chat_search_enabled?: boolean;
  chat_filter_selectable?: boolean;
  chat_search_effective?: boolean;
  chat_filter_effective?: boolean;
  chat_search_inherited?: boolean;
  chat_filter_inherited?: boolean;
  version: number;
  created_at: number;
  updated_at: number;
  full_path: string;
  item_count: number;
  direct_child_count?: number;
  total_child_count?: number;
  total_item_count?: number;
};

export type CategoryDeletePreview = {
  category_id: string;
  parent_id: string | null;
  display_name: string;
  full_path: string;
  version: number;
  descendant_count: number;
  folder_count: number;
  content_count: number;
  pending_request_count: number;
  active_upload_count: number;
  active_reclassification_count: number;
  active_index_count: number;
  archived_content_count: number;
  active_content_count: number;
  upload_batch_count: number;
  media_transcript_count: number;
  renumbered_sibling_count: number;
  can_delete: boolean;
  can_force_delete: boolean;
  protected_category: boolean;
};

export type CategoryDeleteResult = {
  deleted_folder_count: number;
  renumbered_sibling_count: number;
  parent_id: string | null;
  categories: ManagedCategory[];
  force_delete: boolean;
  cleanup_status: "succeeded" | "partial" | null;
  cleanup_error_count: number;
  run_id: string | null;
  deleted_item_count: number;
  deleted_upload_batch_count: number;
  deleted_index_job_count: number;
  qdrant_point_count: number;
  deleted_object_count: number;
};

export type BulkOperationAction = "move" | "publish" | "download" | "delete" | "force_delete";

export type BulkOperationCategory = {
  run_id: string;
  category_id: string;
  parent_id: string | null;
  full_path: string;
  archive_path: string;
  version: number;
  root_category_id: string;
  is_root: boolean;
  eligible: boolean;
  selected: boolean;
  reason: string | null;
  result_status: "pending" | "succeeded" | "failed" | "skipped";
  result_message: string | null;
  sort_order: number;
};

export type BulkOperationItem = {
  run_id: string;
  item_id: string;
  version_id: string;
  category_id: string;
  category_path: string;
  archive_path: string;
  title: string;
  original_filename: string;
  content_kind: string;
  lifecycle_status: string;
  size_bytes: number;
  scope_source: "category" | "direct";
  root_category_id: string | null;
  eligible: boolean;
  selected: boolean;
  reason: string | null;
  result_status: "pending" | "succeeded" | "failed" | "skipped";
  result_message: string | null;
  index_job_id: string | null;
  sort_order: number;
};

export type BulkOperation = {
  id: string;
  operation: BulkOperationAction;
  status: "awaiting_confirmation" | "queued" | "running" | "packaging" | "ready" | "succeeded" | "partial" | "failed" | "cancelled" | "expired";
  actor_user_id: number | null;
  target_category_id: string | null;
  note: string | null;
  confirmation_phrase: string | null;
  total_files: number;
  selected_files: number;
  completed_files: number;
  failed_files: number;
  total_folders: number;
  total_bytes: number;
  processed_bytes: number;
  archive_filename: string | null;
  error_summary: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  expires_at: number | null;
  updated_at: number;
  max_archive_bytes: number;
  categories: BulkOperationCategory[];
  items: BulkOperationItem[];
};

export type KnowledgeScope = {
  id: string;
  parent_id: string | null;
  display_code: string;
  display_name: string;
  full_path: string;
  level: number;
  descendant_count: number;
  chat_search_enabled: boolean;
  chat_filter_selectable: boolean;
};

export type ManagedContentItem = {
  item_id: string;
  title: string;
  content_kind: string;
  category_id: string;
  category_key: string;
  category_label: string;
  category_path: string;
  media_id: string | null;
  preview_parent_id: string | null;
  preview_status: "ready" | "pending" | "missing" | "not_applicable";
  version_id: string;
  version_number: number;
  original_filename: string;
  doc_type: string;
  lifecycle_status: string;
  object_sha256: string | null;
  source_origin: string;
  source_batch_id: string | null;
  source_rel_path?: string | null;
  is_current: boolean;
  has_published_head: boolean;
  latest_publication_status: string | null;
  publication_attempt_count: number;
  publication_failure: PublicationFailure | null;
  latest_reviewed_by_name: string | null;
  latest_reviewed_at: number | null;
  latest_review_decision: string | null;
  latest_review_note: string | null;
  created_at: number;
  updated_at: number;
  archived_at?: number | null;
  archived_by_name?: string | null;
  pre_archive_lifecycle_status?: string | null;
  purge_eligible_at?: number | null;
  retention_status?: "retained" | "expiring" | "overdue" | null;
  retention_days_remaining?: number | null;
  media_duration_ms?: number | null;
  media_file_size?: number | null;
  file_size?: number | null;
  has_pending_revision: boolean;
  reclassification_job_id: string | null;
  reclassification_status: string | null;
  media_status?: string | null;
  transcription_job_id?: string | null;
  transcription_job_status?: string | null;
  transcription_stage?: string | null;
  transcription_failure_classification?: string | null;
  review_status?: string | null;
  publication_status?: string | null;
};

export type ManagedPreview = {
  version_id: string;
  preview_parent_id: string;
  preview_status: "ready";
};

export type XMindTopic = {
  id: string;
  title: string;
  notes: string | null;
  children: XMindTopic[];
};

export type XMindPreview = {
  version_id: string;
  sheets: Array<{ id: string; title: string; root_topic: XMindTopic }>;
};

export type ContentTrashAuditEvent = {
  event_type: "content.archived" | "content.restored";
  actor_name: string | null;
  created_at: number;
  previous_status: string | null;
  restored_status: string | null;
  restore_strategy: "original_directory" | "alternate_directory" | "replace_conflict" | null;
  source_category_path: string | null;
  target_category_path: string | null;
  category_path: string | null;
  archive_reason: "restore_conflict_replacement" | null;
  replaced_title: string | null;
  replaced_filename: string | null;
};

export type ContentReclassificationJob = {
  id: string;
  item_id: string;
  expected_version_id: string;
  source_category_id: string;
  target_category_id: string;
  status: "pending" | "applying" | "committing" | "rolling_back" | "succeeded" | "failed";
  qdrant_point_count: number;
  parent_count: number;
  error_code: string | null;
  error_summary: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
};

export type PublicationFailure = {
  code: string;
  message: string;
  retryable: boolean;
  recommended_action: string;
};

export type ManagedContentList = {
  items: ManagedContentItem[];
  total: number;
  status_counts: Record<string, number>;
  retention_counts?: Record<string, number>;
};

export type BulkRestorePreflightResult = {
  item_id: string;
  version_id: string;
  status: "ready" | "conflict" | "inactive_category" | "version_changed" | "in_progress" | "not_found";
  message: string;
  target_category_path: string | null;
};

export type TrashSettings = {
  cleanup_enabled: boolean;
  retention_days: number;
  warning_days: number;
  batch_limit: number;
  updated_by: number | null;
  updated_at: number;
};

export type TrashPurgeItem = {
  item_id: string;
  version_id: string;
  status: "ready" | "blocked";
  reason: string | null;
  title: string;
  original_filename: string;
  category_path: string;
  size_bytes: number;
  content_kind: "document" | "media_transcript";
  media_count: number;
  transcript_version_count: number;
  artifact_count: number;
  index_job_count: number;
};

export type TrashPurgePreflight = {
  items: TrashPurgeItem[];
  ready_count: number;
  blocked_count: number;
  total_size_bytes: number;
  media_count: number;
  transcript_version_count: number;
  artifact_count: number;
  index_job_count: number;
  confirmation_phrase: string;
};

export type TrashPurgeRun = {
  id: string;
  trigger_type: "manual" | "automatic";
  status: "running" | "succeeded" | "partial" | "failed";
  candidate_count: number;
  succeeded_count: number;
  failed_count: number;
  actor_name: string | null;
  created_at: number;
  finished_at: number | null;
};

export type FolderRequest = {
  id: string;
  parent_category_id: string;
  parent_label: string;
  display_name: string;
  status: "pending" | "approved" | "rejected";
  requester_name: string | null;
  review_note: string | null;
  created_category_id: string | null;
  created_at: number;
  updated_at: number;
  reviewed_at: number | null;
};

export type BulkManagedContentResult = {
  version_id: string;
  item_id?: string | null;
  status: "succeeded" | "failed";
  message: string | null;
  index_job_id: string | null;
};

export type BulkManagedContentResponse = {
  results: BulkManagedContentResult[];
  succeeded: number;
  failed: number;
};

export type ManagedIndexJob = {
  id: string;
  publication_id: string;
  version_id: string;
  attempt_number: number;
  status: string;
  error_code: string | null;
  error_summary: string | null;
  failure: PublicationFailure | null;
  attempt_count: number;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
  title: string | null;
  original_filename: string | null;
  doc_type: string | null;
  category_id: string | null;
  category_label: string | null;
  category_path: string | null;
  version_number: number | null;
  file_size: number | null;
  source_origin: string | null;
  is_archived: boolean;
  is_current_head: boolean;
  is_latest_attempt: boolean;
  parent_count: number | null;
  preview_parent_id: string | null;
};

export type ManagedIndexJobList = {
  jobs: ManagedIndexJob[];
  total: number;
  status_counts: Record<string, number>;
};

export type UnifiedPublicationJob = {
  // Latest-attempt metadata is supplied by the unified publication endpoint.
  id: string;
  task_type: "document" | "video_transcript";
  task_type_label: string;
  status: "processing" | "published" | "failed";
  version_id: string;
  publication_id: string | null;
  media_id: string | null;
  title: string | null;
  original_filename: string | null;
  category_id: string | null;
  category_label: string | null;
  category_path: string | null;
  source_origin: string | null;
  attempt_number: number;
  attempt_count: number;
  error_code: string | null;
  error_summary: string | null;
  retryable: boolean;
  is_archived: boolean;
  is_current_head: boolean;
  is_latest_attempt: boolean;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
  doc_type: string | null;
  version_number: number | null;
  file_size: number | null;
  parent_count: number | null;
  preview_parent_id: string | null;
  workflow_status?: string | null;
  transcription_action?: "start_transcription" | "open_transcription_job" | "open_transcript_workbench" | null;
  cancelable: boolean;
};

export type UnifiedPublicationJobList = {
  jobs: UnifiedPublicationJob[];
  total: number;
  status_counts: Record<string, number>;
};

export type ManagedUploadFilenameConflict = {
  item_id: string;
  version_id: string;
  title: string;
  original_filename: string;
  lifecycle_status: string;
  has_published_head: boolean;
  can_update: boolean;
};

export type ManagedUploadFolderConflict = {
  relative_path: string;
  category_id: string;
  category_path: string;
  display_name: string;
  suggested_name: string;
  can_rename: boolean;
};

export type ManagedUploadPreflightEntry = {
  sequence: number;
  filename: string;
  relative_path: string | null;
  kind?: "document" | "video";
  status: "ready" | "conflict" | "blocked";
  reason: string | null;
  reason_code: string | null;
  suggested_filename: string | null;
  conflict: ManagedUploadFilenameConflict | null;
};

export type ManagedUploadPreflightResponse = {
  entries: ManagedUploadPreflightEntry[];
  folder_conflicts: ManagedUploadFolderConflict[];
};

export type ManagedUploadConflictAction = {
  strategy: "skip" | "create" | "rename" | "update";
  filename?: string;
  item_id?: string;
  expected_version_id?: string;
};

export type ManagedUploadResponse = {
  batch_id: string;
  entries: {
    filename: string;
    kind?: "document" | "video";
    item_id: string | null;
    version_id: string | null;
    media_id?: string | null;
    transcription_job_id?: string | null;
    sha256: string | null;
    status: "accepted" | "skipped";
    reason: string | null;
    reason_code: string | null;
    resolution: "created" | "renamed" | "updated" | null;
  }[];
};

export type ManagedUploadTaskEntry = {
  sequence: number;
  filename: string;
  relative_path: string | null;
  kind?: "document" | "video";
  size_bytes: number;
  status: "accepted" | "skipped";
  reason: string | null;
  item_id: string | null;
  version_id: string | null;
  media_id?: string | null;
  transcription_job_id?: string | null;
  failure_code?: string | null;
  created_at: number;
};

export type ManagedUploadTask = {
  batch_id: string;
  upload_mode: "files" | "folder";
  status: "processing" | "completed" | "partial_success" | "failed";
  target_category_id: string | null;
  target_path: string;
  total_files: number;
  accepted_files: number;
  skipped_files: number;
  total_bytes: number;
  total_uploaded_bytes: number;
  video_count: number;
  transcribable_video_count: number;
  created_by_name: string;
  created_at: number;
  updated_at: number;
  error_summary: string | null;
  entries: ManagedUploadTaskEntry[] | null;
};

export type ManagedUploadTaskList = {
  tasks: ManagedUploadTask[];
  total: number;
  status_counts: Record<string, number>;
};

export type ContentPermissionUser = {
  user_id: number;
  employee_id: string;
  real_name: string;
  role: "user" | "admin";
  is_active: boolean;
  permissions: ContentPermission[];
};

export type ContentPermissionGroup = {
  id: string;
  group_key: string;
  display_name: string;
  permissions: ContentPermission[];
  is_system: boolean;
  is_active: boolean;
  updated_at: number;
};

export type AdminFeedbackEntry = {
  feedback_id: string;
  ts?: string | null;
  kind?: string | null;
  rating?: string | null;
  note?: string | null;
  parent_id?: string | null;
  doc_title?: string | null;
  section_path?: string | null;
  start_time?: string | null;
  category?: string | null;
  query?: string | null;
  answer_text?: string | null;
  session_id?: string | null;
  conversation_id?: string | null;
  turn_index?: number | null;
  message_id?: string | null;
  status: "pending" | "in_progress" | "resolved" | "archived";
  resolution?: "knowledge_fixed" | "answer_improved" | "no_action" | "duplicate" | "other" | null;
  admin_note?: string | null;
  assignee_user_id?: number | null;
  assignee_name?: string | null;
  updated_at?: number | null;
  resolved_at?: number | null;
};

export type AdminFeedbackResponse = {
  entries: AdminFeedbackEntry[];
  total: number;
  page: number;
  page_size: number;
  counts: Record<"pending" | "in_progress" | "resolved" | "archived", number>;
};

export type FeedbackPayload = {
  conversation_id?: string | null;
  turn_index?: number | null;
  message_id?: string | null;
  kind: "answer" | "citation";
  rating?: "up" | "down";
  note?: string;
  parent_id?: string;
  doc_title?: string;
  section_path?: string;
  start_time?: string | null;
  category?: string;
  query?: string;
  answer_text?: string;
};

export type ApiConfig = {
  embed_model: string;
  reranker_model: string;
  rerank_enabled: boolean;
  llm_model: string;
  llm_rewrite_model: string;
  collection: string;
};

export type Health = {
  status: string;
  children: number;
  parents: number;
};

export type LlmModelHealth = {
  model: string;
  role: "generation" | "rewrite" | string;
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
};

export type TranscriptionProfile = {
  profile_id: string;
  display_name: string;
  description: string;
  qualification: string;
  admission: string;
  availability: string;
  unavailable_reason_code: string | null;
  requires_review: boolean;
  auto_publish: boolean;
  auto_index: boolean;
};

export type TranscriptionSchemeOption = {
  scheme_id: string;
  name: string;
  description: string;
  base_id: string;
  config_hash: string;
  enabled: boolean;
  archived: boolean;
  sort_order: number;
  version: number;
  availability: string;
};

export type TranscriptionSchemeParameters = {
  segmentation_preset: "natural" | "balanced" | "fine" | "custom";
  max_duration_ms: number | null;
  max_chars: number;
  merge_gap_ms: number;
  terminology_profile: "none" | "bim-engineering-v1";
  prompt_asset: "asr_engineering_zh_v1" | "asr_engineering_zh_v2";
  preprocessing_preset: "standard-audio-v1";
  vad_preset: "service-default-v1";
  decode_preset: "service-default-v1";
};

export type TranscriptionScheme = {
  id: string;
  name: string;
  description: string;
  base_id: string;
  parameters: TranscriptionSchemeParameters;
  config_hash: string;
  enabled: boolean;
  archived: boolean;
  system_preset: boolean;
  sort_order: number;
  version: number;
  created_at: number;
  updated_at: number;
};

export type TranscriptionBase = {
  id: string;
  provider: string;
  model: string;
  revision: string;
  service_profile_id: string;
  config_hash: string;
  qualification: string;
  admission: string;
  availability: string;
  capabilities: Record<string, unknown>;
  defaults: Record<string, unknown>;
};

export type AsrSegmentation = {
  preset: "natural" | "balanced" | "fine";
  max_segment_duration_ms: number | null;
  max_segment_chars: number;
  max_merge_gap_ms: number;
};

export type AsrManagedProfile = {
  profile_id: string;
  display_name: string;
  description: string;
  profile_version: string;
  application_config_hash: string;
  qualification: string;
  admission: string;
  availability: string;
  unavailable_reason_code: string | null;
  release_eligible: boolean;
  segmentation: AsrSegmentation | null;
  terminology_rule_set: string | null;
  protected_terms: string[];
  decode: {
    service_profile_id: string;
    model_name: string;
    beam_size: number;
    temperature: number;
    hotword_count: number;
    prompt_asset_id: string | null;
    service_profile_config_hash: string | null;
    qualification_policy: string | null;
  };
};

export type AsrServiceStatus = {
  status: "disabled" | "healthy" | "degraded" | "unavailable";
  queue_depth: number | null;
  queue_limit: number | null;
  pause_reason: string | null;
};

export type AsrReleaseValidation = {
  status: "disabled" | "ready" | "unavailable";
  reason_code: "asr_disabled" | "profile_identity_unavailable" | null;
};

export type AsrProfileReleaseRequest = {
  request_id: string;
  profile_id: string;
  profile_display_name: string;
  profile_config_hash: string;
  status: "requested" | "completed" | "rejected" | "cancelled";
  request_reason: string | null;
  requested_by_name: string | null;
  created_at: number;
  updated_at: number;
};

export type AsrProfileAuditEvent = {
  event_id: number;
  event_type: "release_requested";
  profile_id: string;
  profile_display_name: string;
  actor_name: string | null;
  created_at: number;
};

export type AsrSettings = {
  service: AsrServiceStatus;
  release_validation: AsrReleaseValidation;
  profiles: AsrManagedProfile[];
  release_requests: AsrProfileReleaseRequest[];
  audit_events: AsrProfileAuditEvent[];
};

export type TranscriptionJobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export type TranscriptionFailure = {
  code: string;
  message: string;
  retryable: boolean;
};

export type TranscriptionJob = {
  job_id: string;
  media_id: string;
  attempt_number: number;
  profile_id: string;
  scheme_id?: string | null;
  status: TranscriptionJobStatus;
  stage: string | null;
  processed_ms: number;
  total_ms: number | null;
  failure_error_code: string | null;
  error_summary: string | null;
  failure: TranscriptionFailure | null;
  result_version_id: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
};

export type FailedMediaCleanup = {
  media_id: string;
  cleanup_mode: "deleted" | "reset";
};

export type TranscriptionActionItem = {
  media_id: string;
  status: "succeeded" | "failed";
  message: string | null;
  transcription_job_id: string | null;
  cleanup_mode: "deleted" | "reset" | null;
};

export type BulkTranscriptionActionResult = {
  items: TranscriptionActionItem[];
  succeeded: number;
  failed: number;
};

export type BulkTranscriptionItem = {
  media_id: string;
  title: string;
  original_filename: string;
  category_path: string | null;
  status: "ready" | "started" | "already_started" | "unavailable" | "failed";
  reason: string | null;
  transcription_job_id: string | null;
};

export type BulkTranscriptionPreflight = {
  scheme_id: string;
  items: BulkTranscriptionItem[];
  ready_count: number;
  blocked_count: number;
};

export type BulkTranscriptionResult = {
  scheme_id: string;
  items: BulkTranscriptionItem[];
  requested: number;
  started: number;
  failed: number;
};

export type TranscriptReviewStatus =
  | "not_required"
  | "awaiting_review"
  | "review_approved"
  | "review_rejected";

export type TranscriptPublicationStatus =
  | "not_published"
  | "publishing"
  | "published"
  | "publication_failed";

export type TranscriptVersion = {
  version_id: string;
  media_id: string;
  source: "automatic" | "manual" | string;
  profile_id: string | null;
  provider_key: string | null;
  model_id: string | null;
  model_revision: string | null;
  markdown_storage_kind: "managed_artifact" | "legacy_manual" | string;
  review_status: TranscriptReviewStatus;
  reviewed_by: number | null;
  reviewed_at: number | null;
  review_note: string | null;
  publication_status: TranscriptPublicationStatus;
  published_at: number | null;
  supersedes_version_id: string | null;
  derived_from_version_id: string | null;
  edited_by: number | null;
  markdown_sha256: string;
  created_at: number;
  updated_at: number;
  is_current: boolean;
};

export type TranscriptMarkdownPreview = {
  version_id: string;
  markdown: string;
  markdown_sha256: string;
};

export type TranscriptPublicationJob = {
  index_job_id: string;
  transcript_version_id: string;
  attempt_number: number;
  target_index_id: string;
  status: "pending" | "parsing" | "chunking" | "embedding" | "done" | "failed" | string;
  error_code: string | null;
  error_summary: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
};

export type PublishTranscriptVersionResult = {
  version: TranscriptVersion;
  job: TranscriptPublicationJob | null;
  reused: boolean;
};

export type LlmHealth = {
  ok: boolean;
  key_present: boolean;
  key_masked: string;
  base_url: string;
  checked_at: number;
  cached: boolean;
  models: LlmModelHealth[];
};

export type MediaAsset = {
  media_id: string;
  title: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  transcript_origin: string | null;
  status: string;
  created_at: number;
  updated_at: number;
  error: string | null;
  transcription_job_id?: string | null;
  transcription_job_status?: TranscriptionJobStatus | null;
  transcription_stage?: string | null;
  current_phase?: "upload" | "transcription" | "review" | "publication" | "index" | "ready" | "failed";
  review_status?: TranscriptReviewStatus | null;
  publication_status?: TranscriptPublicationStatus | null;
  publication_index_status?: "pending" | "parsing" | "chunking" | "embedding" | "done" | "failed" | null;
  publication_request_status?: "pending_transcription" | "ready_to_publish" | "publishing" | "published" | "failed" | "cancelled" | null;
  is_current_version?: boolean;
  replacement_source_media_id?: string | null;
  replacement_candidate_media_id?: string | null;
  replacement_status?: "pending" | "failed" | "activated" | "cancelled" | null;
  category_id?: string | null;
  category_path?: string | null;
  catalog_item_id?: string | null;
  current_version_id?: string | null;
  latest_version_id?: string | null;
  storage_kind?: "managed" | "external";
  external_source_id?: string | null;
  external_relative_path?: string | null;
  external_availability?: "available" | "missing" | "superseded" | null;
  available_actions: string[];
  disabled_actions: Record<string, string>;
};

export type ExternalMediaRoot = { alias: string };
export type ExternalMediaSource = {
  id: string;
  name: string;
  root_alias: string;
  relative_path: string;
  target_category_id: string;
  default_scheme_id: string;
  auto_enqueue: boolean;
  scan_interval_seconds: number;
  enabled: boolean;
  status: "never_scanned" | "scanning" | "available" | "unavailable" | "scan_failed";
  total_files: number;
  available_files: number;
  missing_files: number;
  last_scan_at: number | null;
  last_successful_scan_at: number | null;
  last_error_code: string | null;
  created_at: number;
  updated_at: number;
  version: number;
};
export type ExternalMediaEntry = {
  id: string;
  kind: "folder" | "video";
  name: string;
  relative_path: string;
  file_size?: number | null;
  modified_ns?: number | null;
  availability?: "available" | "missing" | "superseded" | null;
  media_id?: string | null;
  media_status?: string | null;
  transcription_job_id?: string | null;
  transcription_job_status?: string | null;
  review_status?: string | null;
  publication_status?: string | null;
  index_status?: string | null;
  lifecycle_status?: string;
};
export type ExternalMediaEntryList = { source_id: string; parent_relative_path: string; entries: ExternalMediaEntry[] };
export type ExternalMediaScan = { run_id: string; source_id: string; discovered_count: number; added_count: number; changed_count: number; missing_count: number; enqueued_count: number; enqueue_failures: number };
export type ExternalMediaEnqueuePreviewItem = { entry_id: string; relative_path: string; file_size: number; modified_ns: number; state: "new" | "updated" | "already_transcribed"; selected: boolean };
export type ExternalMediaEnqueuePreview = { items: ExternalMediaEnqueuePreviewItem[]; selected_count: number };
export type ExternalMediaEnqueueResult = { requested: number; enqueued: number; failed: number; failures: Record<string, string> };

export type MediaUploadConflict = {
  media_id: string;
  item_id: string | null;
  version_id: string | null;
  title: string;
  original_filename: string;
  title_matches: boolean;
  filename_matches: boolean;
};

export type MediaUploadPreflightEntry = {
  client_id: string;
  status: "ready" | "conflict" | "ambiguous";
  suggested_title: string | null;
  suggested_filename: string | null;
  conflicts: MediaUploadConflict[];
};

export type MediaUploadPreflightResponse = {
  category_id: string;
  entries: MediaUploadPreflightEntry[];
};

export type MediaTranscriptSegment = {
  id: number;
  start_ms: number;
  end_ms: number | null;
  text: string;
};

export type MediaTranscript = {
  media_id: string;
  version_id: string | null;
  language: string | null;
  duration_ms: number | null;
  segments: MediaTranscriptSegment[];
};
