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
  ExternalMediaRoot,
  ExternalMediaSource,
  ExternalMediaEntryList,
  ExternalMediaScan,
  ExternalMediaEnqueueResult,
  MediaUploadPreflightResponse,
  ManagedCategory,
  CategoryDeletePreview,
  CategoryDeleteResult,
  KnowledgeScope,
  FolderRequest,
  ManagedContentItem,
  ManagedPreview,
  XMindPreview,
  ContentTrashAuditEvent,
  ContentReclassificationJob,
  ManagedContentList,
  BulkManagedContentResponse,
  BulkOperation,
  BulkOperationAction,
  BulkRestorePreflightResult,
  TrashPurgePreflight,
  TrashPurgeRun,
  TrashSettings,
  ManagedIndexJobList,
  ManagedUploadConflictAction,
  ManagedUploadPreflightResponse,
  ManagedUploadResponse,
  ManagedUploadTask,
  ManagedUploadTaskList,
  MediaTranscript,
  TranscriptionJob,
  BulkTranscriptionPreflight,
  BulkTranscriptionResult,
  TranscriptionProfile,
  TranscriptionBase,
  TranscriptionScheme,
  TranscriptionSchemeParameters,
  TranscriptionSchemeOption,
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

export type ManagedUploadOptions = {
  allowFolderMerge?: boolean;
  conflictActions?: ManagedUploadConflictAction[];
  videoSchemeId?: string;
  videoIdempotencyKeys?: string[];
  publishIntents?: boolean[];
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
  knowledgeScopes: () => jsonFetch<{ scopes: KnowledgeScope[] }>("/api/knowledge-scopes"),
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
  adminUpdateMaintenanceSettings: (settings: Omit<MaintenanceSettings, "updated_at" | "updated_by">) =>
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
      max_batch_files: number;
      max_batch_bytes: number;
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
    category_kind?: "folder" | "shared_folder";
    external_source_id?: string | null;
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
      chat_search_enabled: boolean;
      chat_filter_selectable: boolean;
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
  managedCategoryDeletePreview: (categoryId: string) =>
    jsonFetch<CategoryDeletePreview>(
      `/api/admin/content/categories/${encodeURIComponent(categoryId)}/delete-preview`,
    ),
  deleteManagedCategory: (
    categoryId: string,
    expectedVersion: number,
    options?: { force?: boolean; typedPath?: string },
  ) =>
    jsonFetch<CategoryDeleteResult>(
      `/api/admin/content/categories/${encodeURIComponent(categoryId)}`,
      {
        method: "DELETE",
        body: JSON.stringify({
          expected_version: expectedVersion,
          confirmed: true,
          force: Boolean(options?.force),
          typed_path: options?.typedPath || null,
        }),
      },
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
    doc_type?: "pdf" | "doc" | "docx" | "xls" | "xlsx" | "ppt" | "pptx" | "xmind" | "markdown" | "transcript" | "other";
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
  managedContentTrashSettings: () =>
    jsonFetch<TrashSettings>("/api/admin/content/trash/settings"),
  updateManagedContentTrashSettings: (body: Omit<TrashSettings, "updated_by" | "updated_at">) =>
    jsonFetch<TrashSettings>("/api/admin/content/trash/settings", {
      method: "PUT", body: JSON.stringify(body),
    }),
  preflightManagedContentTrashPurge: (items: Array<{ item_id: string; expected_version_id: string }>) =>
    jsonFetch<TrashPurgePreflight>("/api/admin/content/trash/purge/preflight", {
      method: "POST", body: JSON.stringify({ items }),
    }),
  previewOverdueManagedContentTrashPurge: () =>
    jsonFetch<TrashPurgePreflight>("/api/admin/content/trash/purge-preview"),
  purgeManagedContentTrash: (items: Array<{ item_id: string; expected_version_id: string }>, confirmation: string) =>
    jsonFetch<{ run_id: string; status: string; candidate_count: number; succeeded_count: number; failed_count: number }>(
      "/api/admin/content/trash/purge", { method: "POST", body: JSON.stringify({ items, confirmation }) },
    ),
  managedContentTrashPurgeRuns: () =>
    jsonFetch<TrashPurgeRun[]>("/api/admin/content/trash/purge-runs"),
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
  preflightManagedContentUpload: (
    files: Array<File | ManagedContentUploadEntry>,
    categoryId: string,
    uploadMode: ManagedContentUploadMode = "files",
    allowFolderMerge = false,
  ) => jsonFetch<ManagedUploadPreflightResponse>("/api/admin/content/uploads/preflight", {
    method: "POST",
    body: JSON.stringify({
      category_id: categoryId,
      upload_mode: uploadMode,
      allow_folder_merge: allowFolderMerge,
      entries: files.map((entry) => {
        const file = "file" in entry ? entry.file : entry;
        return {
          filename: file.name,
          relative_path: "file" in entry ? entry.relativePath : file.webkitRelativePath || file.name,
          size_bytes: file.size,
        };
      }),
    }),
  }),
  uploadManagedContent: async (
    files: Array<File | ManagedContentUploadEntry>,
    categoryId: string,
    uploadMode: ManagedContentUploadMode = "files",
    onProgress?: (progress: ManagedUploadProgress) => void,
    options?: ManagedUploadOptions,
  ) => {
    const form = new FormData();
    files.forEach((entry) => {
      const file = "file" in entry ? entry.file : entry;
      const relativePath = "file" in entry ? entry.relativePath : file.webkitRelativePath || file.name;
      form.append("files", file, file.name);
      form.append("relative_paths", relativePath);
    });
    if (files.some((entry) => /\.mp4$/i.test("file" in entry ? entry.file.name : entry.name))) {
      files.forEach((entry, index) => {
        const file = "file" in entry ? entry.file : entry;
        form.append(
          "video_idempotency_keys",
          /\.mp4$/i.test(file.name) ? options?.videoIdempotencyKeys?.[index] || crypto.randomUUID() : "",
        );
      });
    }
    form.append("category_id", categoryId);
    form.append("upload_mode", uploadMode);
    form.append("allow_folder_merge", options?.allowFolderMerge ? "true" : "false");
    files.forEach((_entry, index) => form.append("publish", options?.publishIntents?.[index] ? "true" : "false"));
    if (options?.videoSchemeId) form.append("video_scheme_id", options.videoSchemeId);
    options?.conflictActions?.forEach((action) => {
      form.append("conflict_actions", JSON.stringify(action));
    });
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
  regenerateManagedContentPreview: (versionId: string) =>
    jsonFetch<ManagedPreview>(
      `/api/admin/content/versions/${encodeURIComponent(versionId)}/preview`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  managedContentXMindPreview: (versionId: string) =>
    jsonFetch<XMindPreview>(
      `/api/admin/content/versions/${encodeURIComponent(versionId)}/xmind-preview`,
    ),
  bulkReviewManagedContent: (versionIds: string[], approved: boolean, note?: string) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-review", {
      method: "POST",
      body: JSON.stringify({ version_ids: versionIds, approved, note: note?.trim() || null }),
    }),
  bulkSubmitManagedContent: (versionIds: string[]) =>
    jsonFetch<BulkManagedContentResponse>("/api/admin/content/bulk-submit", {
      method: "POST",
      body: JSON.stringify({ version_ids: versionIds }),
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
  preflightManagedContentBulkOperation: (
    operation: BulkOperationAction,
    categories: Array<{ category_id: string; expected_version: number }>,
    items: Array<{ item_id: string; expected_version_id: string }>,
  ) => jsonFetch<BulkOperation>("/api/admin/content/bulk-operations/preflight", {
    method: "POST", body: JSON.stringify({ operation, categories, items }),
  }),
  managedContentBulkOperation: (runId: string, includeTree = true) =>
    jsonFetch<BulkOperation>(`/api/admin/content/bulk-operations/${encodeURIComponent(runId)}?include_tree=${includeTree}`),
  updateManagedContentBulkSelection: (runId: string, itemIds: string[], selected: boolean) =>
    jsonFetch<BulkOperation>(`/api/admin/content/bulk-operations/${encodeURIComponent(runId)}/selection`, {
      method: "PATCH", body: JSON.stringify({ item_ids: itemIds, selected }),
    }),
  executeManagedContentBulkOperation: (
    runId: string,
    options: { target_category_id?: string; note?: string; confirmation?: string } = {},
  ) => jsonFetch<BulkOperation>(`/api/admin/content/bulk-operations/${encodeURIComponent(runId)}/execute`, {
    method: "POST", body: JSON.stringify(options),
  }),
  reviewManagedContentBulkItem: (runId: string, itemId: string, approved: boolean, note?: string) =>
    jsonFetch<BulkOperation>(
      `/api/admin/content/bulk-operations/${encodeURIComponent(runId)}/items/${encodeURIComponent(itemId)}/review`,
      { method: "POST", body: JSON.stringify({ approved, note: note?.trim() || null }) },
    ),
  cancelManagedContentBulkOperation: (runId: string) =>
    jsonFetch<BulkOperation>(`/api/admin/content/bulk-operations/${encodeURIComponent(runId)}/cancel`, {
      method: "POST", body: JSON.stringify({}),
    }),
  managedContentBulkArchiveUrl: (runId: string) =>
    `/api/admin/content/bulk-operations/${encodeURIComponent(runId)}/archive`,
  downloadManagedCategory: (categoryId: string, fallbackFilename: string) =>
    fileFetch(`/api/admin/content/categories/${encodeURIComponent(categoryId)}/download`, fallbackFilename, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  downloadManagedContentFile: (versionId: string, fallbackFilename: string) =>
    fileFetch(`/api/admin/content/versions/${encodeURIComponent(versionId)}/file?download=true`, fallbackFilename),
  downloadManagedMedia: (
    itemId: string,
    part: "video" | "transcript" | "all",
    fallbackFilename: string,
  ) => fileFetch(
    `/api/admin/content/items/${encodeURIComponent(itemId)}/media-download?part=${encodeURIComponent(part)}`,
    fallbackFilename,
  ),
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
    options?: { categoryId?: string; originalFilename?: string },
  ) => {
    const fd = new FormData();
    fd.append("video", video, video.name);
    fd.append("transcript", transcript, transcript.name);
    fd.append("title", title);
    if (options?.categoryId) fd.append("category_id", options.categoryId);
    if (options?.originalFilename) fd.append("original_filename", options.originalFilename);
    return multipartFetch<MediaAsset>("/api/admin/media", fd, callbacks);
  },
  uploadAutomaticMediaVideo: async (
    video: File,
    title: string,
    profileId: string,
    requestIdempotencyKey: string,
    callbacks?: MultipartUploadCallbacks,
    options?: { categoryId?: string; originalFilename?: string; replacementSourceMediaId?: string },
  ) => {
    const fd = new FormData();
    fd.append("video", video, video.name);
    fd.append("title", title);
    fd.append("profile_id", profileId);
    fd.append("scheme_id", profileId);
    fd.append("request_idempotency_key", requestIdempotencyKey);
    if (options?.categoryId) fd.append("category_id", options.categoryId);
    if (options?.originalFilename) fd.append("original_filename", options.originalFilename);
    if (options?.replacementSourceMediaId) fd.append("replacement_source_media_id", options.replacementSourceMediaId);
    return multipartFetch<MediaAsset>("/api/admin/media", fd, callbacks);
  },
  uploadReplacementMediaVideo: async (
    video: File,
    title: string,
    profileId: string,
    requestIdempotencyKey: string,
    sourceMediaId: string,
    callbacks?: MultipartUploadCallbacks,
  ) => {
    const fd = new FormData();
    fd.append("video", video, video.name);
    fd.append("title", title);
    fd.append("profile_id", profileId);
    fd.append("scheme_id", profileId);
    fd.append("request_idempotency_key", requestIdempotencyKey);
    fd.append("replacement_source_media_id", sourceMediaId);
    return multipartFetch<MediaAsset>("/api/admin/media", fd, callbacks);
  },
  listMediaAssets: () => jsonFetch<MediaAsset[]>("/api/admin/media"),
  listExternalMediaRoots: () => jsonFetch<ExternalMediaRoot[]>("/api/admin/external-media/roots"),
  listExternalMediaSources: () => jsonFetch<ExternalMediaSource[]>("/api/admin/external-media/sources"),
  createExternalMediaSource: (body: { name: string; root_alias: string; relative_path: string; target_category_id: string; default_scheme_id: string; auto_enqueue: boolean; scan_interval_seconds: number }) =>
    jsonFetch<ExternalMediaSource>("/api/admin/external-media/sources", { method: "POST", body: JSON.stringify(body) }),
  updateExternalMediaSource: (sourceId: string, body: { name: string; target_category_id: string; default_scheme_id: string; auto_enqueue: boolean; scan_interval_seconds: number; enabled: boolean; expected_version: number }) =>
    jsonFetch<ExternalMediaSource>(`/api/admin/external-media/sources/${encodeURIComponent(sourceId)}`, { method: "PATCH", body: JSON.stringify(body) }),
  scanExternalMediaSource: (sourceId: string) => jsonFetch<ExternalMediaScan>(`/api/admin/external-media/sources/${encodeURIComponent(sourceId)}/scan`, { method: "POST", body: JSON.stringify({}) }),
  listExternalMediaEntries: (sourceId: string, parent = "") => jsonFetch<ExternalMediaEntryList>(`/api/admin/external-media/sources/${encodeURIComponent(sourceId)}/entries?parent=${encodeURIComponent(parent)}`),
  enqueueExternalMedia: (sourceId: string, entryIds?: string[]) => jsonFetch<ExternalMediaEnqueueResult>(`/api/admin/external-media/sources/${encodeURIComponent(sourceId)}/enqueue`, { method: "POST", body: JSON.stringify({ entry_ids: entryIds ?? null }) }),
  preflightMediaUpload: (body: {
    category_id: string;
    items: Array<{ client_id: string; title: string; original_filename: string }>;
  }) => jsonFetch<MediaUploadPreflightResponse>("/api/admin/media/preflight", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  deleteFailedMediaAsset: (mediaId: string) =>
    jsonFetch<void>(`/api/admin/media/${mediaId}`, { method: "DELETE" }),
  archiveMediaAsset: (mediaId: string) => jsonFetch(`/api/admin/media/${mediaId}/archive`, { method: "POST", body: JSON.stringify({}) }),
  listTranscriptionProfiles: () =>
    jsonFetch<TranscriptionProfile[]>("/api/admin/transcription/profiles"),
  listTranscriptionSchemes: () =>
    jsonFetch<TranscriptionSchemeOption[]>("/api/admin/transcription/schemes"),
  adminTranscriptionBases: () =>
    jsonFetch<TranscriptionBase[]>("/api/admin/asr/bases"),
  adminTranscriptionSchemes: (includeArchived = true) =>
    jsonFetch<TranscriptionScheme[]>(`/api/admin/asr/schemes?include_archived=${includeArchived}`),
  adminCreateTranscriptionScheme: (body: { name: string; description: string; base_id: string; parameters: Partial<TranscriptionSchemeParameters> }) =>
    jsonFetch<TranscriptionScheme>("/api/admin/asr/schemes", { method: "POST", body: JSON.stringify(body) }),
  adminCopyTranscriptionScheme: (schemeId: string, body: { name: string; description?: string }) =>
    jsonFetch<TranscriptionScheme>(`/api/admin/asr/schemes/${encodeURIComponent(schemeId)}/copy`, { method: "POST", body: JSON.stringify(body) }),
  adminUpdateTranscriptionScheme: (schemeId: string, body: Partial<Pick<TranscriptionScheme, "name" | "description" | "parameters" | "enabled" | "archived">> & { expected_version: number }) =>
    jsonFetch<TranscriptionScheme>(`/api/admin/asr/schemes/${encodeURIComponent(schemeId)}`, { method: "PATCH", body: JSON.stringify(body) }),
  adminReorderTranscriptionSchemes: (order: Array<{ id: string; expected_version: number }>) =>
    jsonFetch<TranscriptionScheme[]>("/api/admin/asr/schemes/order", { method: "POST", body: JSON.stringify({ order }) }),
  listTranscriptionJobs: (latestPerMedia = true, limit = 100) =>
    jsonFetch<TranscriptionJob[]>(
      `/api/admin/transcription/jobs?latest_per_media=${latestPerMedia}&limit=${limit}`,
    ),
  startMediaTranscription: (mediaId: string, schemeId: string, requestIdempotencyKey: string) =>
    jsonFetch<TranscriptionJob>(`/api/admin/transcription/media/${encodeURIComponent(mediaId)}/start`, {
      method: "POST",
      body: JSON.stringify({ scheme_id: schemeId, request_idempotency_key: requestIdempotencyKey }),
    }),
  preflightBulkStartTranscription: (body: {
    scheme_id: string;
    request_idempotency_key: string;
    media_ids?: string[];
    upload_batch_id?: string;
    category_id?: string;
    recursive?: boolean;
  }) => jsonFetch<BulkTranscriptionPreflight>("/api/admin/transcription/bulk-start/preflight", {
    method: "POST", body: JSON.stringify(body),
  }),
  bulkStartTranscription: (body: {
    scheme_id: string;
    request_idempotency_key: string;
    media_ids?: string[];
    upload_batch_id?: string;
    category_id?: string;
    recursive?: boolean;
  }) => jsonFetch<BulkTranscriptionResult>("/api/admin/transcription/bulk-start", {
    method: "POST", body: JSON.stringify(body),
  }),
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
  createMediaMetadataRevision: (
    mediaId: string,
    expectedVersionId: string,
    title: string,
    originalFilename: string,
    requestIdempotencyKey: string,
  ) => jsonFetch<TranscriptVersion>(
    `/api/admin/transcription/media/${encodeURIComponent(mediaId)}/metadata-revisions`,
    {
      method: "POST",
      body: JSON.stringify({
        expected_version_id: expectedVersionId,
        title,
        original_filename: originalFilename,
        request_idempotency_key: requestIdempotencyKey,
      }),
    },
  ),
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
