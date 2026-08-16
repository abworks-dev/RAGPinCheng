import { describe, expect, it } from "vitest";
import { contentWorkspaceTabs, hasContentWorkspaceAccess, workspaceLabel } from "./workspace-access";

describe("workspace access", () => {
  it("keeps users without content permissions outside the workspace", () => {
    expect(hasContentWorkspaceAccess({ role: "user", content_permissions: [] })).toBe(false);
  });

  it("opens the content workspace when an accessible page is granted", () => {
    expect(hasContentWorkspaceAccess({ role: "user", content_permissions: ["workspace.view", "item.view"] })).toBe(true);
    expect(workspaceLabel({ role: "user", content_permissions: ["workspace.view", "item.view"] })).toBe("资料工作台");
    expect(contentWorkspaceTabs(["workspace.view", "item.view"])).toEqual(["managed"]);
  });

  it("does not expose a dead workspace entry for prerequisites without a page", () => {
    expect(hasContentWorkspaceAccess({ role: "user", content_permissions: ["workspace.view"] })).toBe(false);
    expect(hasContentWorkspaceAccess({ role: "user", content_permissions: ["workspace.view", "category.view"] })).toBe(false);
    expect(contentWorkspaceTabs(["item.view"])).toEqual([]);
  });

  it("adds category settings only for category managers", () => {
    expect(contentWorkspaceTabs(["workspace.view", "item.view", "category.manage", "index.view"])).toEqual(["managed", "categories", "corpus"]);
  });

  it("gives administrators the management workspace regardless of explicit permissions", () => {
    expect(hasContentWorkspaceAccess({ role: "admin", content_permissions: [] })).toBe(true);
    expect(workspaceLabel({ role: "admin", content_permissions: [] })).toBe("管理工作台");
  });
});
