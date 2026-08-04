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
};

export type DoneData = {
  answer_text: string;
  timings: Record<string, number>;
  sources: Source[];
  history_chars: number;
  budget: number;
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
  stage?: ChatStage;
  error?: string;
};

export type AuthUser = {
  id: number;
  employee_id: string;
  real_name: string;
  role: "user" | "admin";
  csrf_token: string;
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

export type IndexJob = {
  id: number;
  user_id: number | null;
  employee_id: string | null;
  real_name: string | null;
  filename: string;
  category: string;
  doc_type: "pdf" | "transcript" | string;
  source_path: string;
  file_size: number;
  status: "pending" | "parsing" | "chunking" | "summarizing" | "embedding" | "done" | "failed" | string;
  error: string | null;
  parents: number | null;
  children: number | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
};

export type IndexedDocument = {
  source_path: string;
  doc_title: string;
  category: string;
  doc_type: string;
  company: string | null;
  parent_count: number;
};

export type CategoryNode = {
  name: string;
  two_level: boolean;
  subcategories: string[];
};

export type CategoryTree = {
  categories: CategoryNode[];
  second_level_categories: string[];
};

export type AdminFeedbackEntry = {
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
