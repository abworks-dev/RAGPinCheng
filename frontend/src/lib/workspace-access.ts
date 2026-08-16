import type { AuthUser, ContentPermission } from "../types";

type WorkspaceUser = Pick<AuthUser, "role" | "content_permissions">;

export function hasContentWorkspaceAccess(user: WorkspaceUser): boolean {
  return user.role === "admin" || contentWorkspaceTabs(user.content_permissions || []).length > 0;
}

export function workspaceLabel(user: WorkspaceUser): "管理工作台" | "资料工作台" {
  return user.role === "admin" ? "管理工作台" : "资料工作台";
}

export function contentWorkspaceTabs(permissions: ContentPermission[]): ("managed" | "categories" | "corpus")[] {
  if (!permissions.includes("workspace.view")) return [];
  const tabs: ("managed" | "categories" | "corpus")[] = [];
  if (permissions.includes("item.view")) tabs.push("managed");
  if (permissions.includes("category.manage")) tabs.push("categories");
  if (permissions.includes("index.view")) tabs.push("corpus");
  return tabs;
}
