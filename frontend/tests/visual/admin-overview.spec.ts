import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("管理员概览", () => {
  test("统计指标、运行状态和维护摘要可见", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/overview");
    await expect(page.getByRole("heading", { name: "系统概览" })).toBeVisible();
    await expect(page.getByText("用户总数")).toBeVisible();
    await expect(page.getByRole("heading", { name: "生产运行状态" })).toBeVisible();
    await expect(page.getByText("Office 新资料处理")).toBeVisible();
    await expect(page.getByText("已停用")).toBeVisible();
    await expect(page.getByText(/既有资料仍可检索和预览/)).toBeVisible();
    await expect(page.getByText("当前策略").locator("xpath=..")).toContainText("保留 30 天");
    await expect(page.getByRole("button", { name: /查看系统维护/ })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("系统维护独立路由展示策略、预览和运行记录", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/maintenance");
    await expect(page.getByRole("heading", { name: "系统维护" })).toBeVisible();
    await expect(page.getByText("待清理对话").locator("xpath=..")).toContainText("4");
    await expect(page.getByRole("heading", { name: "最近运行记录" })).toBeVisible();
    await expect(page.getByRole("region", { name: "最近运行记录" }).getByText(/^自动(?:清理)?$/).filter({ visible: true })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("概览错误状态明确", async ({ page }) => {
    await installAdminRoutes(page, "error");
    await page.goto("/admin/overview");
    await expect(page.getByText("合成加载失败")).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
