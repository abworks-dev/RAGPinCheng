import type { Page, Route } from "@playwright/test";

export type AdminScenario = "normal" | "loading" | "empty" | "error" | "disabled";
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
  created_at: 1700000000,
  updated_at: 1700000000,
}));

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

export async function installAdminRoutes(page: Page, scenario: AdminScenario = "normal", workspaceUser: WorkspaceUser = "admin") {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (!path.startsWith("/api/")) return route.continue();

    if (path === "/api/auth/me") return json(route, workspaceUsers[workspaceUser]);
    if (path === "/api/categories") return json(route, { categories: [], second_level_categories: [] });
    if (path === "/api/conversations") return json(route, { conversations: [] });
    if (path === "/api/admin/users") return json(route, { users: permissionUsers.map((user) => ({
      id: user.user_id, employee_id: user.employee_id, real_name: user.real_name, role: user.role,
      is_active: user.is_active, created_at: 1700000000, last_login_at: 1700000000,
      conversation_count: user.user_id === 9001 ? 3 : 0,
      content_permissions: user.role === "admin" ? admin.content_permissions : user.permissions,
    })) });

    const isTargetRead = request.method() === "GET" && path.startsWith("/api/admin/content/");
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
      return json(route, scenario === "empty" ? [] : categories);
    }
    if (path === "/api/admin/content/items-page") {
      const rows = scenario === "empty" ? [] : items;
      return json(route, { items: rows, total: rows.length, status_counts: rows.reduce<Record<string, number>>((counts, item) => ({ ...counts, [item.lifecycle_status]: (counts[item.lifecycle_status] || 0) + 1 }), {}) });
    }
    if (path === "/api/admin/content/index-jobs") {
      return json(route, { jobs: [], total: 0, status_counts: {} });
    }
    if (path === "/api/admin/content/permissions") {
      return json(route, scenario === "empty" ? [] : permissionUsers);
    }
    if (path === "/api/admin/content/permission-groups") {
      return json(route, permissionGroups);
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
