import type { Page, Route } from "@playwright/test";

export type AdminScenario = "normal" | "loading" | "empty" | "error" | "disabled" | "publication_failure";
export type WorkspaceUser = "admin" | "bim_engineer" | "member";

const admin = {
  id: 9001,
  employee_id: "TEST-ADMIN",
  real_name: "合成管理员",
  role: "admin",
  csrf_token: "synthetic-csrf-token",
  content_permissions: ["organize", "review", "publish", "manage_categories", "import_server"],
};

const workspaceUsers = {
  admin,
  bim_engineer: { ...admin, id: 9002, employee_id: "TEST-EDITOR", real_name: "合成资料员", role: "user", content_permissions: ["organize"] },
  member: { ...admin, id: 9003, employee_id: "TEST-MEMBER", real_name: "合成成员", role: "user", content_permissions: [] },
};

export const categories = [
  { id: "cat-company", category_key: "company_standard", parent_id: null, display_code: "03", display_name: "公司内部标准", sort_order: 10, level: 1, is_active: true, version: 3, created_at: 1700000000, updated_at: 1700000000, full_path: "03 公司内部标准", item_count: 3 },
  { id: "cat-project", category_key: "project_delivery", parent_id: null, display_code: "04", display_name: "项目资料", sort_order: 20, level: 1, is_active: true, version: 2, created_at: 1700000000, updated_at: 1700000000, full_path: "04 项目资料", item_count: 2 },
  { id: "cat-archive", category_key: "archived", parent_id: null, display_code: "99", display_name: "待确认资料", sort_order: 90, level: 1, is_active: false, version: 1, created_at: 1700000000, updated_at: 1700000000, full_path: "99 待确认资料", item_count: 0 },
];

const folderRequests = [{
  id: "folder-request-1", parent_category_id: "cat-company", parent_label: "03 公司内部标准",
  display_name: "审核标准", status: "pending", requester_name: "合成资料员", review_note: null,
  created_category_id: null, created_at: 1700000000, updated_at: 1700000000, reviewed_at: null,
}];

export const items = [
  ["draft", "建筑信息模型交付标准（合成长文件名用于响应式检查）.pdf"],
  ["awaiting_review", "机电专业协同检查清单.docx"],
  ["approved", "项目资料归档指引.xlsx"],
  ["publication_failed", "培训资料发布演练.pptx"],
  ["published", "企业知识库使用规范.md"],
].map(([status, filename], index) => ({
  item_id: `item-${index + 1}`,
  title: filename.replace(/\.[^.]+$/, ""),
  content_kind: "document",
  category_id: index % 2 ? "cat-project" : "cat-company",
  category_key: index % 2 ? "project_delivery" : "company_standard",
  category_label: index % 2 ? "04 项目资料" : "03 公司内部标准",
  category_path: index % 2 ? "04 项目资料 / 02 竣工交付 / 01 模型成果" : "03 公司内部标准 / 01 建模 / 02 机电",
  media_id: null,
  version_id: `version-${index + 1}`,
  version_number: index + 1,
  original_filename: filename,
  doc_type: filename.split(".").pop(),
  lifecycle_status: status,
  object_sha256: null,
  source_origin: index === 4 ? "legacy" : "web",
  source_batch_id: null,
  is_current: true,
  latest_publication_status: null,
  publication_attempt_count: 0,
  publication_failure: null,
  created_at: 1700000000,
  updated_at: 1700000000,
}));

const trashItems = [{
  ...items[4],
  archived_at: 1700000600,
  archived_by_name: "合成资料员",
  pre_archive_lifecycle_status: "published",
}];

const indexedDocuments = [
  {
    document_id: "document-ready", display_path: "公司标准 / synthetic-ready.pdf",
    filename: "建筑信息模型交付标准（合成长文件名用于资料列表响应式检查）.pdf", doc_title: "建筑信息模型交付标准（合成长文件名用于响应式检查）",
    category: "公司标准", doc_type: "pdf", company: null, parent_count: 18, child_count: 54,
    preview_parent_id: "parent-ready", media_id: null, file_size: 2_048_000, status: "done", is_indexed: true,
    latest_job_id: 101, error_summary: null, uploaded_by: "合成管理员", created_at: 1700000000, updated_at: 1700000300,
  },
  {
    document_id: "document-processing", display_path: "项目资料 / synthetic-processing.docx",
    filename: "机电专业协同检查清单.docx", doc_title: "机电专业协同检查清单",
    category: "项目资料", doc_type: "docx", company: null, parent_count: 0, child_count: null,
    preview_parent_id: null, media_id: null, file_size: 384_000, status: "embedding", is_indexed: false,
    latest_job_id: 102, error_summary: null, uploaded_by: "合成资料员", created_at: 1700000100, updated_at: 1700000400,
  },
  {
    document_id: "document-failed", display_path: "客户标准 / 合成客户 / synthetic-failed.xlsx",
    filename: "项目资料归档检查表.xlsx", doc_title: "项目资料归档检查表",
    category: "客户标准", doc_type: "xlsx", company: "合成客户", parent_count: 0, child_count: null,
    preview_parent_id: null, media_id: null, file_size: 96_000, status: "failed", is_indexed: false,
    latest_job_id: 103, error_summary: "解析器暂不可用，可以重试。", uploaded_by: "合成管理员", created_at: 1700000200, updated_at: 1700000500,
  },
];

const indexJobs = [
  { id: 101, user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", filename: indexedDocuments[0].filename, category: "公司标准", doc_type: "pdf", source_path: "synthetic/ready.pdf", source_exists: true, file_size: 2_048_000, status: "done", error: null, parents: 18, children: 54, created_at: 1700000000, started_at: 1700000010, finished_at: 1700000300 },
  { id: 102, user_id: 9002, employee_id: "TEST-EDITOR", real_name: "合成资料员", filename: indexedDocuments[1].filename, category: "项目资料", doc_type: "docx", source_path: "synthetic/processing.docx", source_exists: true, file_size: 384_000, status: "embedding", error: null, parents: 0, children: 0, created_at: 1700000100, started_at: 0, finished_at: null },
  { id: 103, user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", filename: indexedDocuments[2].filename, category: "客户标准", doc_type: "xlsx", source_path: "synthetic/failed.xlsx", source_exists: true, file_size: 96_000, status: "failed", error: "解析器暂不可用", parents: 0, children: 0, created_at: 1700000200, started_at: 1700000210, finished_at: 1700000250 },
  { id: 104, user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", filename: "已移除源文件的历史资料.pptx", category: "培训资料", doc_type: "pptx", source_path: "synthetic/missing.pptx", source_exists: false, file_size: 512_000, status: "done", error: null, parents: 12, children: 36, created_at: 1699990000, started_at: 1699990010, finished_at: 1699990200 },
];

const managedIndexJobs = [{
  id: "managed-job-1", publication_id: "publication-1", version_id: "version-1",
  attempt_number: 1, status: "failed", error_code: "parser_request_failed",
  error_summary: "文档解析服务请求失败，请稍后重试。",
  failure: { code: "parser_request_failed", message: "文档解析服务请求失败。", retryable: true, recommended_action: "请稍后重试；持续失败时联系系统管理员。" },
  attempt_count: 4, created_at: 1700000000, started_at: 1700000010, finished_at: 1700000020, updated_at: 1700000020,
  title: "资料库发布失败的合成长文件名资料", original_filename: "managed-publication-failure-with-long-name.pdf",
  doc_type: "pdf", category_id: "cat-03", category_label: "03 公司内部标准",
  category_path: "03 公司内部标准 / 01 建模标准", version_number: 4, file_size: 2_048_000,
  source_origin: "legacy", is_current_head: false, is_latest_attempt: true,
  parent_count: null, preview_parent_id: null,
}];

const mediaAssets = [
  {
    media_id: "media-failed-1", title: "机电协同培训录像", original_filename: "mep-training-recording.mp4",
    mime_type: "video/mp4", file_size: 3_456_789, transcript_origin: "generated", status: "failed",
    review_status: "not_required", publication_status: "not_published", publication_index_status: "pending",
    created_at: 1700000400, updated_at: 1700000400, error: "provider_unavailable",
  },
  {
    media_id: "media-failed-2", title: "机电协同培训录像（重复提交）", original_filename: "mep-training-recording.mp4",
    mime_type: "video/mp4", file_size: 3_456_789, transcript_origin: "generated", status: "failed",
    review_status: "not_required", publication_status: "not_published", publication_index_status: "pending",
    created_at: 1700000300, updated_at: 1700000300, error: "provider_unavailable",
  },
  {
    media_id: "media-ready", title: "项目交付培训", original_filename: "project-delivery-training.mp4",
    mime_type: "video/mp4", file_size: 8_765_432, transcript_origin: "generated", status: "transcript_ready",
    review_status: "awaiting_review", publication_status: "not_published", publication_index_status: "pending",
    created_at: 1700000200, updated_at: 1700000200, error: null,
  },
];

const transcriptionProfiles = [{
  profile_id: "funasr-sensevoice-zh-experimental-v1", display_name: "受控中文转录", description: "合成服务端 Profile",
  qualification: "experimental", admission: "enabled", availability: "available", unavailable_reason_code: null,
  requires_review: true, auto_publish: false, auto_index: false,
}];

const transcriptionJobs = [{
  job_id: "media-failed-job", media_id: "media-failed-1", attempt_number: 1,
  profile_id: "funasr-sensevoice-zh-experimental-v1", status: "failed", stage: null,
  processed_ms: 0, total_ms: 0, failure_error_code: "provider_unavailable",
  error_summary: "转录服务当前暂停接收任务，请稍后重试。",
  failure: { code: "provider_unavailable", message: "转录服务当前暂停接收任务，请稍后重试。", retryable: true },
  result_version_id: null, created_at: 1700000400, started_at: 1700000401, finished_at: 1700000402, updated_at: 1700000402,
}];

const permissionUsers = [
  { user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", role: "admin", is_active: true, permissions: [] },
  { user_id: 9002, employee_id: "TEST-EDITOR", real_name: "合成资料员", role: "user", is_active: true, permissions: ["organize", "review"] },
  { user_id: 9003, employee_id: "TEST-INACTIVE", real_name: "停用测试用户", role: "user", is_active: false, permissions: [] },
];

const permissionGroups = [
  { id: "permission-group-member", group_key: "member", display_name: "普通成员", permissions: [], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-bim-engineer", group_key: "bim_engineer", display_name: "BIM工程师", permissions: ["organize"], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-content-owner", group_key: "content_owner", display_name: "资料负责人", permissions: ["review"], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-system-admin", group_key: "system_admin", display_name: "系统管理员", permissions: ["organize", "review", "publish", "manage_categories", "import_server"], is_system: true, is_active: true, updated_at: 1700000000 },
];

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function installAdminRoutes(
  page: Page,
  scenario: AdminScenario = "normal",
  workspaceUser: WorkspaceUser = "admin",
  currentUser: () => typeof admin = () => workspaceUsers[workspaceUser],
  options: { includeChildFolder?: boolean; includeFolderRequest?: boolean } = {},
) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (!path.startsWith("/api/")) return route.continue();

    if (path === "/api/auth/me") return json(route, currentUser());
    if (request.method() === "GET" && path === "/api/admin/stats") {
      return json(route, {
        users_total: 3,
        users_active: 2,
        conversations_total: 12,
        conversations_7d: 4,
        messages_total: 64,
        messages_7d: 18,
      });
    }
    if (request.method() === "GET" && path === "/api/admin/media") return json(route, mediaAssets);
    if (request.method() === "GET" && path === "/api/admin/transcription/profiles") return json(route, transcriptionProfiles);
    if (request.method() === "GET" && path === "/api/admin/transcription/jobs") return json(route, transcriptionJobs);
    if (path === "/api/categories") return json(route, { categories: [], second_level_categories: [] });
    if (path === "/api/conversations") return json(route, { conversations: [] });
    if (path === "/api/admin/users") return json(route, { users: permissionUsers.map((user) => ({
      id: user.user_id, employee_id: user.employee_id, real_name: user.real_name, role: user.role,
      is_active: user.is_active, created_at: 1700000000, last_login_at: 1700000000,
      conversation_count: user.user_id === 9001 ? 3 : 0,
      content_permissions: user.role === "admin" ? admin.content_permissions : user.permissions,
    })) });

    const isIndexRead = request.method() === "GET" && path.startsWith("/api/admin/index/");
    const isTargetRead = request.method() === "GET" && (path.startsWith("/api/admin/content/") || isIndexRead);
    if (isTargetRead && scenario === "loading") {
      await new Promise((resolve) => setTimeout(resolve, 1_500));
    }
    if (isTargetRead && scenario === "error") {
      return json(route, { detail: "合成加载失败" }, 503);
    }
    if (path === "/api/admin/content/capabilities") {
      return json(route, { enabled: scenario !== "disabled", max_upload_bytes: 10_000_000, supported_extensions: [".pdf", ".md", ".docx", ".xlsx", ".pptx"] });
    }
    if (path === "/api/admin/content/categories") {
      const childFolder = { id: "cat-company-modeling", category_key: "company_modeling", parent_id: "cat-company", display_code: "01", display_name: "建模标准", sort_order: 10, level: 2, is_active: true, version: 1, created_at: 1700000000, updated_at: 1700000000, full_path: "03 公司内部标准 / 01 建模标准", item_count: 0 };
      return json(route, scenario === "empty" ? [] : options.includeChildFolder ? [...categories, childFolder] : categories);
    }
    if (path === "/api/admin/content/items-page") {
      const rows = scenario === "empty" ? [] : items.map((item) => item.lifecycle_status === "publication_failed" && scenario === "publication_failure" ? { ...item, latest_publication_status: "failed", publication_attempt_count: 4, publication_failure: { code: "pdf_password_required", message: "PDF 需要密码才能解析。", retryable: false, recommended_action: "请上传已解除密码保护的 PDF。" } } : item);
      return json(route, { items: rows, total: rows.length, status_counts: rows.reduce<Record<string, number>>((counts, item) => ({ ...counts, [item.lifecycle_status]: (counts[item.lifecycle_status] || 0) + 1 }), {}) });
    }
    if (path === "/api/admin/content/trash") {
      const rows = scenario === "empty" ? [] : trashItems;
      return json(route, { items: rows, total: rows.length, status_counts: rows.length ? { published: 1 } : {} });
    }
    if (path === "/api/admin/content/folder-requests") {
      return json(route, options.includeFolderRequest ? folderRequests : []);
    }
    if (path === "/api/admin/content/index-jobs") {
      const jobs = scenario === "empty" ? [] : managedIndexJobs;
      return json(route, { jobs, total: jobs.length, status_counts: jobs.length ? { processing: 0, ready: 0, failed: 1 } : {} });
    }
    if (path === "/api/admin/content/permissions") {
      return json(route, scenario === "empty" ? [] : permissionUsers);
    }
    if (path === "/api/admin/content/permission-groups") {
      return json(route, permissionGroups);
    }
    if (request.method() === "GET" && path === "/api/admin/index/category-tree") {
      return json(route, { categories: [{ name: "公司标准", two_level: false, subcategories: [] }, { name: "客户标准", two_level: true, subcategories: ["合成客户"] }, { name: "项目资料", two_level: false, subcategories: [] }], second_level_categories: ["客户标准"] });
    }
    if (request.method() !== "GET" && path.startsWith("/api/admin/content/")) {
      await new Promise((resolve) => setTimeout(resolve, 800));
      if (path === "/api/admin/content/uploads") {
        return json(route, { batch_id: "synthetic-batch", entries: [{ filename: "synthetic.pdf", item_id: "new-item", version_id: "new-version", sha256: null, status: "accepted", reason: null }] });
      }
      if (path.endsWith("/bulk-review") || path.endsWith("/bulk-publish")) {
        return json(route, { results: [], succeeded: 0, failed: 0 });
      }
      return json(route, items[0]);
    }
    throw new Error(`Visual fixture has no route for ${request.method()} ${path}`);
  });
}
