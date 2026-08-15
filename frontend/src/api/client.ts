import type {
  AdminConversation,
  AdminFeedbackEntry,
  AdminFeedbackResponse,
  AdminStats,
  CleanupPreview,
  CleanupResult,
  MaintenanceRun,
  MaintenanceSettings,
  MaintenanceStatus,
  AdminUser,
  ApiConfig,
  AuthUser,
  Conversation,
  ConversationState,
  ContentPermission,
  ContentPermissionGroup,
  ContentPermissionUser,
  FeedbackPayload,
  Health,
  LlmHealth,
  MediaAsset,
  ManagedCategory,
  FolderRequest,
  ManagedContentItem,
  ManagedContentList,
  BulkManagedContentResponse,
  ManagedIndexJobList,
  ManagedUploadResponse,
  MediaTranscript,
  TranscriptionJob,
  TranscriptionProfile,
  TranscriptMarkdownPreview,
  TranscriptPublicationJob,
  TranscriptVersion,
  PublishTranscriptVersionResult,
} from "../types";

// Mutating methods send X-CSRF-Token. Cookies always go along via credentials.
const MUTATING = new Set(["POST", "PATCH", "PUT", "DELETE"]);

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

let unauthorizedHandler: (() => void) | null = null;
let contentPermissionForbiddenHandler: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn;
}

export function setContentPermissionForbiddenHandler(fn: (() => void) | null) {
  contentPermissionForbiddenHandler = fn;
}

function notifyResponse(path: string, response: Response) {
  if (response.status === 401 && unauthorizedHandler) {
    try {
      unauthorizedHandler();
    } catch {
      /* noop */
    }
  }
  if (
    response.status === 403
    && path.startsWith("/api/admin/content/")
    && contentPermissionForbiddenHandler
  ) {
    try {
      contentPermissionForbiddenHandler();
    } catch {
      /* noop */
    }
  }
}

export class ApiError extends Error {
  status: number;
  body: string;
  code: string | null;
  retryable: boolean | null;
  constructor(status: number, body: string, message: string, code: string | null = null, retryable: boolean | null = null) {
    super(message);
    this.status = status;
    this.body = body;
    this.code = code;
    this.retryable = retryable;
  }
}

function parseErrorDetail(body: string): { message: string; code: string | null; retryable: boolean | null } {
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed.detail === "string") {
      return { message: parsed.detail, code: null, retryable: null };
    }
    const detail = parsed?.detail;
    if (detail && typeof detail === "object") {
      return {
        message: typeof detail.message === "string" ? detail.message : "",
        code: typeof detail.code === "string" ? detail.code : null,
        retryable: typeof detail.retryable === "boolean" ? detail.retryable : null,
      };
    }
  } catch {
    /* keep raw body */
  }
  return { message: body, code: null, retryable: null };
}

async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !headers["content-type"] && !headers["Content-Type"]) {
    headers["content-type"] = "application/json";
  }
  if (MUTATING.has(method) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const res = await fetch(path, { ...init, headers, credentials: "include" });
  notifyResponse(path, res);
  return res;
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await rawFetch(path, init);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    const detail = parseErrorDetail(txt);
    throw new ApiError(
      res.status,
      txt,
      detail.message || `${res.status} ${res.statusText}`,
      detail.code,
      detail.retryable,
    );
  }
  // 204 has no body.
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export const api = {
  // cross-cutting
  health: () => jsonFetch<Health>("/api/health"),
  llmHealth: (force = false) =>
    jsonFetch<LlmHealth>(`/api/llm_health${force ? "?force=true" : ""}`),
  config: () => jsonFetch<ApiConfig>("/api/config"),
  categories: () => jsonFetch<{ categories: string[] }>("/api/categories"),
  mediaTranscript: (mediaId: string) =>
    jsonFetch<MediaTranscript>(`/api/media/${encodeURIComponent(mediaId)}/transcript`),
  sendFeedback: (payload: FeedbackPayload) =>
    jsonFetch<{ ok: boolean }>("/api/feedback", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // auth
  me: () => jsonFetch<AuthUser>("/api/auth/me"),
  login: (employee_id: string, password: string) =>
    jsonFetch<AuthUser>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ employee_id, password }),
    }),
  register: (employee_id: string, real_name: string, password: string) =>
    jsonFetch<AuthUser>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ employee_id, real_name, password }),
    }),
  logout: () => jsonFetch<void>("/api/auth/logout", { method: "POST" }),

  // conversations
  listConversations: () =>
    jsonFetch<{ conversations: Conversation[] }>("/api/conversations"),
  createConversation: () =>
    jsonFetch<Conversation>("/api/conversations", { method: "POST", body: "{}" }),
  getConversation: (id: string) =>
    jsonFetch<ConversationState>(`/api/conversations/${id}`),
  deleteConversation: (id: string) =>
    jsonFetch<void>(`/api/conversations/${id}`, { method: "DELETE" }),

  // admin
  adminListUsers: () =>
    jsonFetch<{ users: AdminUser[] }>("/api/admin/users"),
  adminPatchUser: (
    id: number,
    body: Partial<{ is_active: boolean; role: "user" | "admin"; reset_password: string }>,
  ) =>
    jsonFetch<AdminUser>(`/api/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  adminListUserConversations: (userId: number) =>
    jsonFetch<{ conversations: AdminConversation[] }>(
      `/api/admin/users/${userId}/conversations`,
    ),
  adminListAllConversations: (limit = 200) =>
    jsonFetch<{ conversations: AdminConversation[] }>(
      `/api/admin/conversations?limit=${limit}`,
    ),
  adminGetConversation: (id: string) =>
    jsonFetch<ConversationState>(`/api/conversations/${id}`),
  adminStats: () => jsonFetch<AdminStats>("/api/admin/stats"),
  adminFeedback: (params: {
    status?: string;
    kind?: string;
    rating?: string;
    q?: string;
    page?: number;
    page_size?: number;
  } = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    return jsonFetch<AdminFeedbackResponse>(`/api/admin/feedback?${query.toString()}`);
  },
  adminPatchFeedback: (
    id: string,
    body: {
      status: AdminFeedbackEntry["status"];
      resolution?: AdminFeedbackEntry["resolution"];
      admin_note?: string;
    },
  ) =>
    jsonFetch<AdminFeedbackEntry>(`/api/admin/feedback/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  adminMaintenance: () => jsonFetch<MaintenanceStatus>("/api/admin/maintenance"),
  adminMaintenancePreview: (retentionDays?: number) =>
    jsonFetch<CleanupPreview>(
      `/api/admin/maintenance/cleanup-preview${retentionDays === undefined ? "" : `?retention_days=${retentionDays}`}`,
    ),
  adminUpdateMaintenanceSettings: (settings: Pick<MaintenanceSettings, "conversation_cleanup_enabled" | "conversation_retention_days">) =>
    jsonFetch<MaintenanceSettings>("/api/admin/maintenance/settings", {
      method: "PATCH",
      body: JSON.stringify(settings),
    }),
  adminRunMaintenanceCleanup: () =>
    jsonFetch<CleanupResult>("/api/admin/maintenance/cleanup", { method: "POST" }),
  adminMaintenanceRuns: (limit = 20) =>
    jsonFetch<{ runs: MaintenanceRun[] }>(`/api/admin/maintenance/runs?limit=${limit}`),

  // admin: managed content library
  managedContentCapabilities: () =>
    jsonFetch<{
      enabled: boolean;
      max_upload_bytes: number;
      supported_extensions: string[];
    }>("/api/admin/content/capabilities"),
  managedCategories: (includeInactive = false) =>
    jsonFetch<ManagedCategory[]>(
      `/api/admin/content/categories?include_inactive=${includeInactive}`,
    ),
  createManagedCategory: (body: {
    parent_id: string | null;
    display_code: string;
    display_name: string;
    sort_order: number;
  }) =>
    jsonFetch<ManagedCategory>("/api/admin/content/categories", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateManagedCategory: (
    categoryId: string,
    body: {
      display_code: string;
      display_name: string;
      sort_order: number;
      is_active: boolean;
      expected_version: number;
    },
  ) =>
    jsonFetch<ManagedCategory>(`/api/admin/content/categories/${encodeURIComponent(categoryId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  managedContentPermissions: () =>
    jsonFetch<ContentPermissionUser[]>("/api/admin/content/permissions"),
  updateManagedContentPermissions: (userId: number, permissions: ContentPermission[]) =>
    jsonFetch<ContentPermissionUser>(`/api/admin/content/permissions/${userId}`, {
      method: "PUT",
      body: JSON.stringify({ permissions }),
    }),
  managedContentPermissionGroups: () =>
    jsonFetch<ContentPermissionGroup[]>("/api/admin/content/permission-groups"),
  createManagedContentPermissionGroup: (body: { display_name: string; permissions: ContentPermission[] }) =>
    jsonFetch<ContentPermissionGroup>("/api/admin/content/permission-groups", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateManagedContentPermissionGroup: (
    groupId: string,
    body: Partial<{ display_name: string; permissions: ContentPermission[]; is_active: boolean }>,
  ) => jsonFetch<ContentPermissionGroup>(`/api/admin/content/permission-groups/${encodeURIComponent(groupId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  }),
  managedContentItems: (params?: {
    query?: string;
    category_id?: string;
    lifecycle_status?: string;
    source_origin?: string;
    limit?: number;
    offset?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.query) search.set("query", params.query);
    if (params?.category_id) search.set("category_id", params.category_id);
    if (params?.lifecycle_status) search.set("lifecycle_status", params.lifecycle_status);
    if (params?.source_origin) search.set("source_origin", params.source_origin);
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    return jsonFetch<ManagedContentList>(`/api/admin/content/items-page?${search}`);
  },
  deleteManagedContent: (itemId: string, expectedVersionId: string) =>
    jsonFetch<{
      item_id: string;
      version_id: string;
      archived_at: number;
      previous_status: string;
      publication_withdrawn: boolean;
    }>(`/api/admin/content/items/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
      body: JSON.stringify({ expected_version_id: expectedVersionId }),
    }),
  uploadManagedContent: async (files: File[], categoryId: string) => {
    const form = new FormData();
    files.forEach((file) => {
      form.append("files", file, file.name);
      form.append("relative_paths", file.webkitRelativePath || file.name);
    });
    form.append("category_id", categoryId);
    const headers: Record<string, string> = {};
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch("/api/admin/content/uploads", {
      method: "POST",
      headers,
      body: form,
      credentials: "include",
    });
    notifyResponse("/api/admin/content/uploads", response);
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      const detail = parseErrorDetail(body);
      throw new ApiError(response.status, body, detail.message || `${response.status} ${response.statusText}`, detail.code, detail.retryable);
    }
    return (await response.json()) as ManagedUploadResponse;
  },
  moveManagedContent: (itemId: string, targetCategoryId: string, expectedVersionId: string) =>
    jsonFetch<ManagedContentItem>(`/api/admin/content/items/${encodeURIComponent(itemId)}/move`, {
      method: "POST",
      body: JSON.stringify({ target_category_id: targetCategoryId, expected_version_id: expectedVersionId }),
    }),
  createFolderRequest: (parentCategoryId: string, displayName: string) =>
    jsonFetch<FolderRequest>("/api/admin/content/folder-requests", {
      method: "POST", body: JSON.stringify({ parent_category_id: parentCategoryId, display_name: displayName }),
    }),
  managedFolderRequests: (status?: string) =>
    jsonFetch<FolderRequest[]>(`/api/admin/content/folder-requests${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  reviewFolderRequest: (requestId: string, approved: boolean, note?: string) =>
    jsonFetch<FolderRequest>(`/api/admin/content/folder-requests/${encodeURIComponent(requestId)}/review`, {
      method: "POST", body: JSON.stringify({ approved, note: note || null }),
    }),
  submitManagedContent: (versionId: string) =>
    jsonFetch<ManagedContentItem>(`/api/admin/content/versions/${encodeURIComponent(versionId)}/submit`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  reviewManagedContent: (versionId: string, approved: boolean, categoryId?: string) =>
    jsonFetch<ManagedContentItem>(`/api/admin/content/versions/${encodeURIComponent(versionId)}/review`, {
      method: "POST",
      body: JSON.stringify({ approved, category_id: categoryId || null }),
    }),
  publishManagedContent: (versionId: string) =>
    jsonFetch<{ publication_id: string; index_job_id: string; status: string }>(
      `/api/admin/content/versions/${encodeURIComponent(versionId)}/publish`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  bulkReviewManagedContent: (versionIds: string[], approved: boolean) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-review", {
      method: "POST",
      body: JSON.stringify({ version_ids: versionIds, approved }),
    }),
  bulkPublishManagedContent: (versionIds: string[]) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-publish", {
      method: "POST",
      body: JSON.stringify({ version_ids: versionIds }),
    }),
  managedContentFileUrl: (versionId: string, download = false) =>
    `/api/admin/content/versions/${encodeURIComponent(versionId)}/file${download ? "?download=true" : ""}`,
  managedContentIndexJobs: (params?: {
    query?: string;
    category_id?: string;
    doc_type?: string;
    status?: string;
    history?: boolean;
    limit?: number;
    offset?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.query) search.set("query", params.query);
    if (params?.category_id) search.set("category_id", params.category_id);
    if (params?.doc_type) search.set("doc_type", params.doc_type);
    if (params?.status) search.set("status", params.status);
    if (params?.history) search.set("history", "true");
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    return jsonFetch<ManagedIndexJobList>(`/api/admin/content/index-jobs?${search}`);
  },

  // admin: media
  uploadMediaVideo: async (video: File, transcript: File, title: string) => {
    const fd = new FormData();
    fd.append("video", video, video.name);
    fd.append("transcript", transcript, transcript.name);
    fd.append("title", title);
    const method = "POST";
    const csrf = csrfToken;
    const headers: Record<string, string> = {};
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const res = await fetch("/api/admin/media", {
      method,
      headers,
      body: fd,
      credentials: "include",
    });
    if (res.status === 401 && unauthorizedHandler) {
      try { unauthorizedHandler(); } catch { /* noop */ }
    }
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      const detail = parseErrorDetail(txt);
      throw new ApiError(res.status, txt, detail.message || `${res.status} ${res.statusText}`, detail.code, detail.retryable);
    }
    return (await res.json()) as MediaAsset;
  },
  uploadAutomaticMediaVideo: async (
    video: File,
    title: string,
    profileId: string,
    requestIdempotencyKey: string,
  ) => {
    const fd = new FormData();
    fd.append("video", video, video.name);
    fd.append("title", title);
    fd.append("profile_id", profileId);
    fd.append("request_idempotency_key", requestIdempotencyKey);
    const headers: Record<string, string> = {};
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const res = await fetch("/api/admin/media", {
      method: "POST",
      headers,
      body: fd,
      credentials: "include",
    });
    if (res.status === 401 && unauthorizedHandler) {
      try { unauthorizedHandler(); } catch { /* noop */ }
    }
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      const detail = parseErrorDetail(txt);
      throw new ApiError(res.status, txt, detail.message || `${res.status} ${res.statusText}`, detail.code, detail.retryable);
    }
    return (await res.json()) as MediaAsset;
  },
  listMediaAssets: () => jsonFetch<MediaAsset[]>("/api/admin/media"),
  deleteFailedMediaAsset: (mediaId: string) =>
    jsonFetch<void>(`/api/admin/media/${mediaId}`, { method: "DELETE" }),
  listTranscriptionProfiles: () =>
    jsonFetch<TranscriptionProfile[]>("/api/admin/transcription/profiles"),
  listTranscriptionJobs: (latestPerMedia = true, limit = 100) =>
    jsonFetch<TranscriptionJob[]>(
      `/api/admin/transcription/jobs?latest_per_media=${latestPerMedia}&limit=${limit}`,
    ),
  getTranscriptionJob: (jobId: string) =>
    jsonFetch<TranscriptionJob>(`/api/admin/transcription/jobs/${jobId}`),
  cancelTranscriptionJob: (jobId: string) =>
    jsonFetch<TranscriptionJob>(`/api/admin/transcription/jobs/${jobId}/cancel`, { method: "POST" }),
  retryTranscription: (mediaId: string, profileId: string, requestIdempotencyKey: string) =>
    jsonFetch<TranscriptionJob>(`/api/admin/transcription/media/${mediaId}/retry`, {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, request_idempotency_key: requestIdempotencyKey }),
    }),
  listTranscriptVersions: (mediaId: string) =>
    jsonFetch<TranscriptVersion[]>(`/api/admin/transcription/media/${mediaId}/versions`),
  previewTranscriptVersion: (versionId: string) =>
    jsonFetch<TranscriptMarkdownPreview>(`/api/admin/transcription/versions/${versionId}/markdown`),
  reviewTranscriptVersion: (versionId: string, approved: boolean, reviewNote: string | null = null) =>
    jsonFetch<TranscriptVersion>(`/api/admin/transcription/versions/${versionId}/review`, {
      method: "POST",
      body: JSON.stringify({ approved, review_note: reviewNote }),
    }),
  publishTranscriptVersion: (versionId: string) =>
    jsonFetch<PublishTranscriptVersionResult>(`/api/admin/transcription/versions/${versionId}/publish`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  getTranscriptPublicationJob: (indexJobId: string) =>
    jsonFetch<TranscriptPublicationJob>(`/api/admin/transcription/publication-jobs/${indexJobId}`),
};
