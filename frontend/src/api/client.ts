import type {
  AdminConversation,
  AdminFeedbackEntry,
  AdminFeedbackResponse,
  AdminStats,
  AdminUser,
  ApiConfig,
  AuthUser,
  CategoryTree,
  Conversation,
  ConversationState,
  ContentPermission,
  ContentPermissionGroup,
  ContentPermissionUser,
  FeedbackPayload,
  Health,
  IndexJob,
  IndexedDocumentList,
  LlmHealth,
  MediaAsset,
  ManagedCategory,
  ManagedContentItem,
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

export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn;
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
  if (res.status === 401 && unauthorizedHandler) {
    // Fire-and-forget; the handler resets local auth state.
    try {
      unauthorizedHandler();
    } catch {
      /* noop */
    }
  }
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
  adminSweep: () =>
    jsonFetch<{ deleted_conversations: number; deleted_auth_sessions: number }>(
      "/api/admin/sweep",
      { method: "POST" },
    ),

  // admin: indexing
  adminCategoryTree: () =>
    jsonFetch<CategoryTree>("/api/admin/index/category-tree"),
  adminUploadDocuments: async (
    files: File[],
    category: string,
    subcategory?: string,
  ) => {
    // FormData triggers multipart; we deliberately don't set content-type
    // so the browser appends the multipart boundary itself. The CSRF
    // header is still injected by rawFetch via the X-CSRF-Token branch.
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    fd.append("category", category);
    if (subcategory) fd.append("subcategory", subcategory);
    const method = "POST";
    const csrf = csrfToken;
    const headers: Record<string, string> = {};
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const res = await fetch("/api/admin/upload", {
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
    return (await res.json()) as {
      accepted: IndexJob[];
      skipped: { filename: string; reason: string }[];
    };
  },
  adminListIndexJobs: (limit = 100) =>
    jsonFetch<{ jobs: IndexJob[] }>(`/api/admin/index/jobs?limit=${limit}`),
  adminRetryIndexJob: (id: number) =>
    jsonFetch<IndexJob>(`/api/admin/index/jobs/${id}/retry`, { method: "POST" }),
  adminDeleteIndexJob: (id: number) =>
    jsonFetch<void>(`/api/admin/index/jobs/${id}`, { method: "DELETE" }),
  adminListIndexedDocuments: (params?: {
    query?: string;
    category?: string;
    doc_type?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.query) search.set("query", params.query);
    if (params?.category) search.set("category", params.category);
    if (params?.doc_type) search.set("doc_type", params.doc_type);
    if (params?.status) search.set("status", params.status);
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    const suffix = search.toString();
    return jsonFetch<IndexedDocumentList>(`/api/admin/index/documents${suffix ? `?${suffix}` : ""}`);
  },
  adminDeleteIndexedDocument: (source_path: string, delete_file: boolean) =>
    jsonFetch<{
      parents_deleted: number;
      file_deleted: boolean;
      file_delete_status: "not_requested" | "deleted" | "missing" | "failed";
    }>(
      "/api/admin/index/documents",
      { method: "DELETE", body: JSON.stringify({ source_path, delete_file }) },
    ),

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
    category_key: string;
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
  managedContentItems: () =>
    jsonFetch<ManagedContentItem[]>("/api/admin/content/items"),
  uploadManagedContent: async (files: File[], categoryId: string) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    form.append("category_id", categoryId);
    const headers: Record<string, string> = {};
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch("/api/admin/content/uploads", {
      method: "POST",
      headers,
      body: form,
      credentials: "include",
    });
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      const detail = parseErrorDetail(body);
      throw new ApiError(response.status, body, detail.message || `${response.status} ${response.statusText}`, detail.code, detail.retryable);
    }
    return (await response.json()) as ManagedUploadResponse;
  },
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
