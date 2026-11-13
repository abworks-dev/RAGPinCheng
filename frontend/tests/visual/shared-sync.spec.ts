import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("共享目录同步", () => {
  test("阶段、共享源、撤回与事件在桌面和窄屏都可见", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/shared-sync");
    await expect(page.getByRole("heading", { name: "共享目录同步", level: 1 })).toBeVisible();
    await expect(page.getByText(/日间（只登记、不自动发布）/)).toBeVisible();
    await expect(page.locator("li").filter({ hasText: "BIM 共享目录" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "立即同步一次" })).toBeVisible();
    await expect(page.getByRole("button", { name: "恢复原版本" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "同步记录" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "最近事件" })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("撤回版本可恢复并出现在同步事件中", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/shared-sync");
    const restore = page.waitForRequest((request) =>
      request.method() === "POST" && request.url().includes("/api/admin/shared-sync/withdrawals/withdrawal-1/restore"),
    );
    await page.getByRole("button", { name: "恢复原版本" }).click();
    await restore;
    await expect(page.getByText("已恢复为该资料的当前版本。")).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("没有共享源和记录时给出空状态", async ({ page }) => {
    await installAdminRoutes(page, "empty");
    await page.goto("/admin/shared-sync");
    await expect(page.getByText("还没有配置共享源")).toBeVisible();
    await expect(page.getByText("没有撤回记录")).toBeVisible();
    await expect(page.getByText("还没有同步记录")).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("加载失败时保留重试入口", async ({ page }) => {
    await installAdminRoutes(page, "error");
    await page.goto("/admin/shared-sync");
    await expect(page.getByRole("alert")).toContainText("共享同步状态加载失败");
    await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
