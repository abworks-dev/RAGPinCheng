import { expect, test } from "@playwright/test";
import process from "node:process";
import { installAdminRoutes } from "./fixtures/admin-fixtures";

for (const [tab, slug] of [["资料库", "managed-content"], ["分类设置", "categories"], ["索引监控", "index-monitor"]] as const) {
  test(`${tab} accepted golden`, async ({ page }) => {
    test.skip(tab === "索引监控" && process.platform !== "win32", "索引监控 Linux golden 尚未在 Linux Chromium 上人工接受");
    await installAdminRoutes(page, "normal");
    await page.goto("/admin");
    if (page.viewportSize()!.width < 1024) {
      const mobileNavigation = page.getByRole("button", { name: "展开管理功能" });
      await expect(mobileNavigation).toBeVisible();
      await mobileNavigation.click();
    }
    await page.getByRole("button", { name: tab, exact: true }).click();
    await expect(page.getByRole("heading", { name: tab })).toBeVisible();
    const viewport = page.viewportSize()!;
    await expect(page).toHaveScreenshot(`${slug}-normal-${viewport.width}x${viewport.height}.png`, { fullPage: true });
  });
}

test("索引监控任务区域 accepted golden", async ({ page }) => {
  test.skip(process.platform !== "win32", "索引监控任务区域 Linux golden 尚未在 Linux Chromium 上人工接受");
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("button", { name: "索引监控", exact: true }).click();
  const managedActivity = page.locator('section[aria-labelledby="managed-index-title"]');
  await expect(page.getByText("文档解析服务请求失败。", { exact: true })).toBeVisible();
  const viewport = page.viewportSize()!;
  await expect(managedActivity).toHaveScreenshot(`index-monitor-managed-normal-${viewport.width}x${viewport.height}.png`);

  const legacyActivity = page.locator("details").filter({ hasText: "旧目录索引活动" });
  await page.getByText("旧目录索引活动", { exact: true }).click();
  await expect(page.getByText("解析器暂不可用", { exact: true }).last()).toBeVisible();
  await expect(legacyActivity).toHaveScreenshot(`index-monitor-legacy-normal-${viewport.width}x${viewport.height}.png`);
});
