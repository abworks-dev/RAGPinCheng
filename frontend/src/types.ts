export type Source = {
  parent_id: string;
  doc_title: string;
  section_path: string;
  category: string;
  score: number;
  rrf_score: number;
  text: string;
  doc_type: string; // "pdf" | "transcript" | "docx" | "xlsx" | "pptx"
  start_time: string | null;
  media_id: string | null;
  // Office document fields
  sheet_name: string | null;
  cell_range: string | null;
  slide_number: number | null;
  paragraph_anchor: string | null;
};

export type PrepData = {
  search_query: string;
  rewrite_applied: boolean;
  history_chars: number;
  budget: number;
  fresh_count: number;
  final_count: number;
  used_sources: Source[];
  no_source_fallback: boolean;
  relevance?: Record<string, unknown>;
};

export type DoneData = {
  answer_text: string;
  assistant_message_id?: number | null;
  timings: Record<string, number>;
  sources: Source[];
  history_chars: number;
  budget: number;
  finish_reason?: string;
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
  query?: string;
  sources?: Source[];
  prep?: PrepData;
  done?: DoneData;
  streaming?: boolean;
  stopped?: boolean;
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

export type ContentPermission = "organize" | "review" | "publish" | "manage_categories" | "import_server";

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

export type ManagedCategory = {
  id: string;
  category_key: string;
  parent_id: string | null;
  display_code: string;
  display_name: string;
  sort_order: number;
  level: number;
  is_active: boolean;
  version: number;
  created_at: number;
  updated_at: number;
  full_path: string;
  item_count: number;
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
  version_id: string;
  version_number: number;
  original_filename: string;
  doc_type: string;
  lifecycle_status: string;
  object_sha256: string | null;
  source_origin: string;
  source_batch_id: string | null;
  is_current: boolean;
  latest_publication_status: string | null;
  publication_attempt_count: number;
  publication_failure: PublicationFailure | null;
  created_at: number;
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
};

export type BulkManagedContentResult = {
  version_id: string;
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
};

export type ManagedIndexJobList = {
  jobs: ManagedIndexJob[];
  total: number;
  status_counts: Record<string, number>;
};

export type ManagedUploadResponse = {
  batch_id: string;
  entries: {
    filename: string;
    item_id: string | null;
    version_id: string | null;
    sha256: string | null;
    status: "accepted" | "skipped";
    reason: string | null;
  }[];
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
  status: TranscriptionJobStatus;
  stage: string | null;
  processed_ms: number;
  total_ms: number;
  failure_error_code: string | null;
  error_summary: string | null;
  failure: TranscriptionFailure | null;
  result_version_id: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
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
  review_status: TranscriptReviewStatus;
  reviewed_by: number | null;
  reviewed_at: number | null;
  review_note: string | null;
  publication_status: TranscriptPublicationStatus;
  published_at: number | null;
  supersedes_version_id: string | null;
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
  review_status?: TranscriptReviewStatus | null;
  publication_status?: TranscriptPublicationStatus | null;
  publication_index_status?: "pending" | "parsing" | "chunking" | "embedding" | "done" | "failed" | null;
  is_current_version?: boolean;
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
