import type { AuthUser, ContentPermission } from "../types";

type WorkspaceUser = Pick<AuthUser, "role" | "content_permissions">;

export function hasContentWorkspaceAccess(user: WorkspaceUser): boolean {
  return user.role === "admin" || (user.content_permissions?.length || 0) > 0;
}

export function workspaceLabel(user: WorkspaceUser): "管理工作台" | "资料工作台" {
  return user.role === "admin" ? "管理工作台" : "资料工作台";
}

export function contentWorkspaceTabs(permissions: ContentPermission[]): ("managed" | "categories")[] {
  return permissions.includes("manage_categories") ? ["managed", "categories"] : ["managed"];
}
