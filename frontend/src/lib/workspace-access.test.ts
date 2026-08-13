import { describe, expect, it } from "vitest";
import { contentWorkspaceTabs, hasContentWorkspaceAccess, workspaceLabel } from "./workspace-access";

describe("workspace access", () => {
  it("keeps users without content permissions outside the workspace", () => {
    expect(hasContentWorkspaceAccess({ role: "user", content_permissions: [] })).toBe(false);
  });

  it("opens the content workspace for any content permission", () => {
    expect(hasContentWorkspaceAccess({ role: "user", content_permissions: ["review"] })).toBe(true);
    expect(workspaceLabel({ role: "user", content_permissions: ["review"] })).toBe("资料工作台");
    expect(contentWorkspaceTabs(["review"])).toEqual(["managed"]);
  });

  it("adds category settings only for category managers", () => {
    expect(contentWorkspaceTabs(["manage_categories"])).toEqual(["managed", "categories"]);
  });

  it("gives administrators the management workspace regardless of explicit permissions", () => {
    expect(hasContentWorkspaceAccess({ role: "admin", content_permissions: [] })).toBe(true);
    expect(workspaceLabel({ role: "admin", content_permissions: [] })).toBe("管理工作台");
  });
});
