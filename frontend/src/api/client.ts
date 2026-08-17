import type {
  AdminConversation,
  AdminFeedbackEntry,
  AdminFeedbackResponse,
  AdminStats,
  SystemOverview,
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
  ContentPermissionCatalog,
  ContentPermissionGroup,
  ContentPermissionUser,
  FeedbackPayload,
  Health,
  LlmHealth,
  MediaAsset,
  ManagedCategory,
  FolderRequest,
  ManagedContentItem,
  ContentTrashAuditEvent,
  ContentReclassificationJob,
  ManagedContentList,
  BulkManagedContentResponse,
  BulkRestorePreflightResult,
  ManagedIndexJobList,
  ManagedUploadResponse,
  ManagedUploadTask,
  ManagedUploadTaskList,
  MediaTranscript,
  TranscriptionJob,
  TranscriptionProfile,
  TranscriptMarkdownPreview,
  TranscriptPublicationJob,
  TranscriptVersion,
  PublishTranscriptVersionResult,
  AnswerPolicy,
  AnswerPolicyAuditEntry,
  AsrSettings,
  AsrProfileReleaseRequest,
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

export type MultipartUploadProgress = {
  loaded: number;
  total: number;
  ratio: number;
};

export type MultipartUploadCallbacks = {
  onProgress?: (progress: MultipartUploadProgress) => void;
  onUploaded?: () => void;
};

export interface ManagedContentUploadEntry {
  file: File;
  relativePath: string;
}

export type ManagedContentDownload = {
  blob: Blob;
  filename: string;
};

export type ManagedContentUploadMode = "files" | "folder";
export type ManagedUploadProgress = {
  phase: "uploading" | "processing";
  loaded: number;
  total: number;
};

export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn;
}

export function setContentPermissionForbiddenHandler(fn: (() => void) | null) {
  contentPermissionForbiddenHandler = fn;
}

function notifyStatus(path: string, status: number) {
  if (status === 401 && unauthorizedHandler) {
    try {
      unauthorizedHandler();
    } catch {
      /* noop */
    }
  }
  if (
    status === 403
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

function notifyResponse(path: string, response: Response) {
  notifyStatus(path, response.status);
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
  if (init.body && !(init.body instanceof FormData) && !headers["content-type"] && !headers["Content-Type"]) {
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

async function multipartFetch<T>(
  path: string,
  form: FormData,
  callbacks?: MultipartUploadCallbacks,
): Promise<T> {
  if (!callbacks) {
    const res = await rawFetch(path, { method: "POST", body: form });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      const detail = parseErrorDetail(txt);
      throw new ApiError(res.status, txt, detail.message || `${res.status} ${res.statusText}`, detail.code, detail.retryable);
    }
    return (await res.json()) as T;
  }

  return new Promise<T>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.withCredentials = true;
    if (csrfToken) request.setRequestHeader("X-CSRF-Token", csrfToken);

    request.upload.onprogress = (event) => {
      const total = event.lengthComputable && event.total > 0 ? event.total : 0;
      callbacks.onProgress?.({
        loaded: event.loaded,
        total,
        ratio: total > 0 ? Math.min(1, event.loaded / total) : 0,
      });
    };
    request.upload.onload = () => callbacks.onUploaded?.();
    request.onerror = () => reject(new ApiError(0, "", "网络连接失败，请检查后重试。"));
    request.onabort = () => reject(new ApiError(0, "", "上传已取消。"));
    request.onload = () => {
      notifyStatus(path, request.status);
      const body = request.responseText || "";
      if (request.status < 200 || request.status >= 300) {
        const detail = parseErrorDetail(body);
        reject(new ApiError(
          request.status,
          body,
          detail.message || `${request.status} ${request.statusText}`,
          detail.code,
          detail.retryable,
        ));
        return;
      }
      try {
        resolve(JSON.parse(body) as T);
      } catch {
        reject(new ApiError(request.status, body, "服务器返回了无法识别的上传结果。"));
      }
    };
    request.send(form);
  });
}

function filenameFromContentDisposition(header: string | null, fallback: string): string {
  const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      /* use the quoted fallback below */
    }
  }
  const quoted = header?.match(/filename="((?:\\.|[^"])*)"/i)?.[1];
  if (quoted) return quoted.replace(/\\([\\"])/g, "$1");
  const plain = header?.match(/filename=([^;]+)/i)?.[1]?.trim();
  return plain || fallback;
}

async function fileFetch(path: string, fallbackFilename: string, init?: RequestInit): Promise<ManagedContentDownload> {
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
  return {
    blob: await res.blob(),
    filename: filenameFromContentDisposition(res.headers.get("content-disposition"), fallbackFilename),
  };
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
  adminSystemOverview: () => jsonFetch<SystemOverview>("/api/admin/system-overview"),
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
  adminAnswerPolicy: () => jsonFetch<AnswerPolicy>("/api/admin/answer-policy"),
  adminUpdateAnswerPolicy: (settings: Omit<AnswerPolicy, "policy_version" | "updated_at" | "updated_by"> & { change_reason?: string }) =>
    jsonFetch<AnswerPolicy>("/api/admin/answer-policy", {
      method: "PATCH",
      body: JSON.stringify(settings),
    }),
  adminResetAnswerPolicy: () =>
    jsonFetch<AnswerPolicy>("/api/admin/answer-policy/reset", { method: "POST" }),
  adminAnswerPolicyAudit: (limit = 50) =>
    jsonFetch<{ entries: AnswerPolicyAuditEntry[] }>(`/api/admin/answer-policy/audit?limit=${limit}`),
  adminAsrSettings: () => jsonFetch<AsrSettings>("/api/admin/asr"),
  adminCreateAsrReleaseRequest: (body: {
    profile_id: string;
    request_idempotency_key: string;
    request_reason?: string | null;
  }) => jsonFetch<AsrProfileReleaseRequest>("/api/admin/asr/release-requests", {
    method: "POST",
    body: JSON.stringify(body),
  }),

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
    sort_order?: number;
    target_position?: number;
    confirm_number_shift?: boolean;
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
      sort_order?: number;
      is_active: boolean;
      expected_version: number;
    },
  ) =>
    jsonFetch<ManagedCategory>(`/api/admin/content/categories/${encodeURIComponent(categoryId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  renameManagedCategory: (
    categoryId: string,
    body: { display_name: string; expected_version: number },
  ) => jsonFetch<ManagedCategory>(
    `/api/admin/content/categories/${encodeURIComponent(categoryId)}/name`,
    { method: "PATCH", body: JSON.stringify(body) },
  ),
  updateManagedCategorySortOrder: (
    categoryId: string,
    body: { sort_order: number; expected_version: number },
  ) => jsonFetch<ManagedCategory>(
    `/api/admin/content/categories/${encodeURIComponent(categoryId)}/sort-order`,
    { method: "PATCH", body: JSON.stringify(body) },
  ),
  updateManagedCategoryNumber: (
    categoryId: string,
    body: {
      target_position: number;
      confirm_number_shift: boolean;
      expected_version: number;
    },
  ) => jsonFetch<ManagedCategory[]>(
    `/api/admin/content/categories/${encodeURIComponent(categoryId)}/number`,
    { method: "PATCH", body: JSON.stringify(body) },
  ),
  managedContentPermissions: () =>
    jsonFetch<ContentPermissionUser[]>("/api/admin/content/permissions"),
  managedContentPermissionCatalog: () =>
    jsonFetch<ContentPermissionCatalog>("/api/admin/content/permission-catalog"),
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
  moveManagedCategory: (
    categoryId: string,
    body: {
      target_parent_id: string | null;
      before_category_id: string | null;
      expected_version: number;
    },
  ) => jsonFetch<ManagedCategory[]>(`/api/admin/content/categories/${encodeURIComponent(categoryId)}/move`, {
    method: "POST",
    body: JSON.stringify(body),
  }),
  managedContentItems: (params?: {
    query?: string;
    category_id?: string;
    lifecycle_status?: string;
    source_origin?: string;
    content_kind?: "document" | "media_transcript";
    doc_type?: "pdf" | "docx" | "xlsx" | "pptx" | "markdown" | "transcript" | "other";
    sort_by?: "doc_type";
    sort_direction?: "asc" | "desc";
    limit?: number;
    offset?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.query) search.set("query", params.query);
    if (params?.category_id) search.set("category_id", params.category_id);
    if (params?.lifecycle_status) search.set("lifecycle_status", params.lifecycle_status);
    if (params?.source_origin) search.set("source_origin", params.source_origin);
    if (params?.content_kind) search.set("content_kind", params.content_kind);
    if (params?.doc_type) search.set("doc_type", params.doc_type);
    if (params?.sort_by) search.set("sort_by", params.sort_by);
    if (params?.sort_direction) search.set("sort_direction", params.sort_direction);
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
  managedContentTrash: (params?: { query?: string; limit?: number; offset?: number; retention_status?: string; archived_from?: number; archived_to?: number; category_id?: string; archived_by?: string; sort_direction?: string }) => {
    const search = new URLSearchParams();
    if (params?.query) search.set("query", params.query);
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    if (params?.retention_status) search.set("retention_status", params.retention_status);
    if (params?.archived_from != null) search.set("archived_from", String(params.archived_from));
    if (params?.archived_to != null) search.set("archived_to", String(params.archived_to));
    if (params?.category_id) search.set("category_id", params.category_id);
    if (params?.archived_by) search.set("archived_by", params.archived_by);
    if (params?.sort_direction) search.set("sort_direction", params.sort_direction);
    return jsonFetch<ManagedContentList>(`/api/admin/content/trash?${search}`);
  },
  restoreManagedContent: (
    itemId: string,
    expectedVersionId: string,
    options?: {
      target_category_id?: string;
      replace_conflict_item_id?: string;
      replace_conflict_expected_version_id?: string;
    },
  ) =>
    jsonFetch<{
      item_id: string;
      version_id: string;
      restored_status: string;
      category_id: string;
      moved_to_alternate_category: boolean;
      replaced_conflict: boolean;
    }>(
      `/api/admin/content/items/${encodeURIComponent(itemId)}/restore`,
      { method: "POST", body: JSON.stringify({ expected_version_id: expectedVersionId, ...options }) },
    ),
  managedContentAuditEvents: (itemId: string) =>
    jsonFetch<ContentTrashAuditEvent[]>(
      `/api/admin/content/items/${encodeURIComponent(itemId)}/audit-events`,
    ),
  uploadManagedContent: async (
    files: Array<File | ManagedContentUploadEntry>,
    categoryId: string,
    uploadMode: ManagedContentUploadMode = "files",
    onProgress?: (progress: ManagedUploadProgress) => void,
  ) => {
    const form = new FormData();
    files.forEach((entry) => {
      const file = "file" in entry ? entry.file : entry;
      const relativePath = "file" in entry ? entry.relativePath : file.webkitRelativePath || file.name;
      form.append("files", file, file.name);
      form.append("relative_paths", relativePath);
    });
    form.append("category_id", categoryId);
    form.append("upload_mode", uploadMode);
    if (!onProgress) {
      const headers: Record<string, string> = {};
      if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
      const response = await fetch("/api/admin/content/uploads", {
        method: "POST", headers, body: form, credentials: "include",
      });
      notifyResponse("/api/admin/content/uploads", response);
      if (!response.ok) {
        const body = await response.text().catch(() => "");
        const detail = parseErrorDetail(body);
        throw new ApiError(response.status, body, detail.message || `${response.status} ${response.statusText}`, detail.code, detail.retryable);
      }
      return (await response.json()) as ManagedUploadResponse;
    }
    return await new Promise<ManagedUploadResponse>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/admin/content/uploads");
      xhr.withCredentials = true;
      if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
      xhr.upload.onprogress = (event) => {
        onProgress({ phase: "uploading", loaded: event.loaded, total: event.total || 0 });
      };
      xhr.onerror = () => reject(new ApiError(0, "", "网络连接失败，请稍后重试", null, true));
      xhr.onabort = () => reject(new ApiError(0, "", "上传已中断", null, true));
      xhr.onload = () => {
        const body = xhr.responseText || "";
        onProgress({ phase: "processing", loaded: xhr.status >= 200 && xhr.status < 300 ? 1 : 0, total: 1 });
        if (xhr.status === 401 && unauthorizedHandler) {
          try { unauthorizedHandler(); } catch { /* noop */ }
        }
        if (xhr.status === 403 && contentPermissionForbiddenHandler) {
          try { contentPermissionForbiddenHandler(); } catch { /* noop */ }
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          const detail = parseErrorDetail(body);
          reject(new ApiError(xhr.status, body, detail.message || `${xhr.status} ${xhr.statusText}`, detail.code, detail.retryable));
          return;
        }
        try {
          resolve(JSON.parse(body) as ManagedUploadResponse);
        } catch {
          reject(new ApiError(xhr.status, body, "服务器返回了无效响应", null, true));
        }
      };
      xhr.send(form);
    });
  },
  managedUploadTasks: (params?: { status?: string; query?: string; limit?: number; offset?: number }) => {
    const search = new URLSearchParams();
    if (params?.status) search.set("status", params.status);
    if (params?.query) search.set("query", params.query);
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    return jsonFetch<ManagedUploadTaskList>(`/api/admin/content/upload-tasks?${search}`);
  },
  managedUploadTask: (batchId: string) =>
    jsonFetch<ManagedUploadTask>(`/api/admin/content/upload-tasks/${encodeURIComponent(batchId)}`),
  moveManagedContent: (itemId: string, targetCategoryId: string, expectedVersionId: string) =>
    jsonFetch<ManagedContentItem>(`/api/admin/content/items/${encodeURIComponent(itemId)}/move`, {
      method: "POST",
      body: JSON.stringify({ target_category_id: targetCategoryId, expected_version_id: expectedVersionId }),
    }),
  reclassifyManagedContent: (itemId: string, targetCategoryId: string, expectedVersionId: string) =>
    jsonFetch<ContentReclassificationJob>(
      `/api/admin/content/items/${encodeURIComponent(itemId)}/reclassify`,
      {
        method: "POST",
        body: JSON.stringify({ target_category_id: targetCategoryId, expected_version_id: expectedVersionId }),
      },
    ),
  managedContentReclassificationJob: (jobId: string) =>
    jsonFetch<ContentReclassificationJob>(
      `/api/admin/content/reclassification-jobs/${encodeURIComponent(jobId)}`,
    ),
  retryManagedContentReclassification: (jobId: string) =>
    jsonFetch<ContentReclassificationJob>(
      `/api/admin/content/reclassification-jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  renameManagedContent: (
    itemId: string,
    body: {
      title: string;
      original_filename: string;
      expected_version_id: string;
      replace_conflict_item_id?: string;
      replace_conflict_expected_version_id?: string;
    },
  ) => jsonFetch<ManagedContentItem>(`/api/admin/content/items/${encodeURIComponent(itemId)}/rename`, {
    method: "POST",
    body: JSON.stringify(body),
  }),
  updateManagedContentVersion: async (
    itemId: string,
    file: File,
    expectedVersionId: string,
    filenameMode: "old" | "new",
    conflict?: { item_id: string; version_id: string },
  ) => {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("expected_version_id", expectedVersionId);
    form.append("filename_mode", filenameMode);
    if (conflict) {
      form.append("replace_conflict_item_id", conflict.item_id);
      form.append("replace_conflict_expected_version_id", conflict.version_id);
    }
    const headers: Record<string, string> = {};
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const path = `/api/admin/content/items/${encodeURIComponent(itemId)}/versions`;
    const response = await fetch(path, {
      method: "POST", headers, body: form, credentials: "include",
    });
    notifyResponse(path, response);
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      const detail = parseErrorDetail(body);
      throw new ApiError(response.status, body, detail.message || `${response.status} ${response.statusText}`, detail.code, detail.retryable);
    }
    return (await response.json()) as ManagedContentItem;
  },
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
  reviewManagedContent: (versionId: string, approved: boolean, note?: string, categoryId?: string) =>
    jsonFetch<ManagedContentItem>(`/api/admin/content/versions/${encodeURIComponent(versionId)}/review`, {
      method: "POST",
      body: JSON.stringify({ approved, note: note?.trim() || null, category_id: categoryId || null }),
    }),
  publishManagedContent: (versionId: string) =>
    jsonFetch<{ publication_id: string; index_job_id: string; status: string }>(
      `/api/admin/content/versions/${encodeURIComponent(versionId)}/publish`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  bulkReviewManagedContent: (versionIds: string[], approved: boolean, note?: string) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-review", {
      method: "POST",
      body: JSON.stringify({ version_ids: versionIds, approved, note: note?.trim() || null }),
    }),
  bulkPublishManagedContent: (versionIds: string[]) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-publish", {
      method: "POST",
      body: JSON.stringify({ version_ids: versionIds }),
    }),
  bulkMoveManagedContent: (
    items: Array<{ item_id: string; expected_version_id: string }>,
    targetCategoryId: string,
  ) => jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-move", {
    method: "POST",
    body: JSON.stringify({ items, target_category_id: targetCategoryId }),
  }),
  bulkReclassifyManagedContent: (
    items: Array<{ item_id: string; expected_version_id: string }>,
    targetCategoryId: string,
  ) => jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-reclassify", {
    method: "POST",
    body: JSON.stringify({ items, target_category_id: targetCategoryId }),
  }),
  bulkArchiveManagedContent: (items: Array<{ item_id: string; expected_version_id: string }>) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-archive", {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
  bulkRestoreManagedContent: (items: Array<{ item_id: string; expected_version_id: string }>, targetCategoryId?: string) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-restore", {
      method: "POST",
      body: JSON.stringify({ items, target_category_id: targetCategoryId }),
    }),
  preflightBulkRestoreManagedContent: (items: Array<{ item_id: string; expected_version_id: string }>, targetCategoryId?: string) =>
    jsonFetch<{ results: BulkRestorePreflightResult[]; ready: number; blocked: number }>("/api/admin/content/bulk-restore/preflight", {
      method: "POST", body: JSON.stringify({ items, target_category_id: targetCategoryId }),
    }),
  exportManagedContentTrash: (filters: Record<string, unknown>) =>
    fileFetch("/api/admin/content/trash/export", "回收站处置清单.csv", {
      method: "POST", body: JSON.stringify(filters),
    }),
  bulkDownloadManagedContent: (versionIds: string[]) =>
    fileFetch("/api/admin/content/bulk-download", "资料批量下载.zip", {
      method: "POST",
      body: JSON.stringify({ version_ids: versionIds }),
    }),
  downloadManagedContentFile: (versionId: string, fallbackFilename: string) =>
    fileFetch(`/api/admin/content/versions/${encodeURIComponent(versionId)}/file?download=true`, fallbackFilename),
  managedContentFileUrl: (versionId: string, download = false) =>
    `/api/admin/content/versions/${encodeURIComponent(versionId)}/file${download ? "?download=true" : ""}`,
  managedContentIndexJobs: (params?: {
    query?: string;
    category_id?: string;
    doc_type?: string;
    source_origin?: string;
    status?: string;
    history?: boolean;
    include_archived?: boolean;
    limit?: number;
    offset?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.query) search.set("query", params.query);
    if (params?.category_id) search.set("category_id", params.category_id);
    if (params?.doc_type) search.set("doc_type", params.doc_type);
    if (params?.source_origin) search.set("source_origin", params.source_origin);
    if (params?.status) search.set("status", params.status);
    if (params?.history) search.set("history", "true");
    if (params?.include_archived) search.set("include_archived", "true");
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    return jsonFetch<ManagedIndexJobList>(`/api/admin/content/index-jobs?${search}`);
  },

  // admin: media
  uploadMediaVideo: async (
    video: File,
    transcript: File,
    title: string,
    callbacks?: MultipartUploadCallbacks,
  ) => {
    const fd = new FormData();
    fd.append("video", video, video.name);
    fd.append("transcript", transcript, transcript.name);
    fd.append("title", title);
    return multipartFetch<MediaAsset>("/api/admin/media", fd, callbacks);
  },
  uploadAutomaticMediaVideo: async (
    video: File,
    title: string,
    profileId: string,
    requestIdempotencyKey: string,
    callbacks?: MultipartUploadCallbacks,
  ) => {
    const fd = new FormData();
    fd.append("video", video, video.name);
    fd.append("title", title);
    fd.append("profile_id", profileId);
    fd.append("request_idempotency_key", requestIdempotencyKey);
    return multipartFetch<MediaAsset>("/api/admin/media", fd, callbacks);
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
  previewTranscriptVersionTimeline: (versionId: string) =>
    jsonFetch<MediaTranscript>(`/api/admin/transcription/versions/${versionId}/timeline`),
  createTranscriptRevision: (
    baseVersionId: string,
    markdown: string,
    baseMarkdownSha256: string,
    requestIdempotencyKey: string,
  ) =>
    jsonFetch<TranscriptVersion>(`/api/admin/transcription/versions/${baseVersionId}/revisions`, {
      method: "POST",
      body: JSON.stringify({
        markdown,
        base_markdown_sha256: baseMarkdownSha256,
        request_idempotency_key: requestIdempotencyKey,
      }),
    }),
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
