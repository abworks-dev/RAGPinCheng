import type { Page, Route } from "@playwright/test";

export type AdminScenario = "normal" | "loading" | "empty" | "error" | "disabled";

const admin = {
  id: 9001,
  employee_id: "TEST-ADMIN",
  real_name: "合成管理员",
  role: "admin",
  csrf_token: "synthetic-csrf-token",
  content_permissions: ["organize", "review", "publish", "manage_categories", "import_server"],
};

export const categories = [
  { id: "cat-company", category_key: "company_standard", parent_id: null, display_code: "A", display_name: "公司标准", sort_order: 10, level: 1, is_active: true, version: 3, created_at: 1700000000, updated_at: 1700000000 },
  { id: "cat-project", category_key: "project_delivery", parent_id: null, display_code: "B", display_name: "项目交付与协同管理资料", sort_order: 20, level: 1, is_active: true, version: 2, created_at: 1700000000, updated_at: 1700000000 },
  { id: "cat-archive", category_key: "archived", parent_id: null, display_code: "Z", display_name: "历史分类", sort_order: 90, level: 1, is_active: false, version: 1, created_at: 1700000000, updated_at: 1700000000 },
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
  category_label: index % 2 ? "B 项目交付与协同管理资料" : "A 公司标准",
  media_id: null,
  version_id: `version-${index + 1}`,
  version_number: index + 1,
  original_filename: filename,
  doc_type: filename.split(".").pop(),
  lifecycle_status: status,
  object_sha256: null,
  source_origin: "合成测试数据",
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

export async function installAdminRoutes(page: Page, scenario: AdminScenario = "normal") {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (!path.startsWith("/api/")) return route.continue();

    if (path === "/api/auth/me") return json(route, admin);
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
    if (path === "/api/admin/content/items") {
      return json(route, scenario === "empty" ? [] : items);
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
      return json(route, items[0]);
    }

    throw new Error(`Visual fixture has no route for ${request.method()} ${path}`);
  });
}
