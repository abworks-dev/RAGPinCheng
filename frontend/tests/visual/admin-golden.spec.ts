import { expect, test } from "@playwright/test";
import process from "node:process";
import { installAdminRoutes } from "./fixtures/admin-fixtures";

async function openRootFolder(page: Parameters<typeof installAdminRoutes>[0]) {
  const listing = page.waitForRequest((request) => request.method() === "GET" && request.url().includes("/api/admin/content/items-page") && request.url().includes("category_id=cat-company"));
  await page.getByRole("button", { name: /03 公司内部标准/ }).click();
  await listing;
}

for (const [navigationLabel, heading, slug] of [["资料管理", "资料库", "managed-content"], ["分类管理", "分类设置", "categories"], ["索引任务", "索引任务", "index-monitor"]] as const) {
  test(`${heading} accepted golden`, async ({ page }) => {
    test.skip(heading === "索引任务" && process.platform !== "win32", "索引任务 Linux golden 尚未在 Linux Chromium 上人工接受");
    await installAdminRoutes(page, "normal");
    await page.goto("/admin");
    if (page.viewportSize()!.width < 1024) {
      const mobileNavigation = page.getByRole("button", { name: "展开管理功能" });
      await expect(mobileNavigation).toBeVisible();
      await mobileNavigation.click();
    }
    await page.getByRole("button", { name: navigationLabel, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    const viewport = page.viewportSize()!;
    await expect(page).toHaveScreenshot(`${slug}-normal-${viewport.width}x${viewport.height}.png`, { fullPage: true });
  });
}

test("管理概览生产运行状态 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "生产运行状态" })).toBeVisible();
  const viewport = page.viewportSize()!;
  expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth))).toBeLessThanOrEqual(viewport.width);
  if (process.platform === "win32") {
    await expect(page).toHaveScreenshot(`overview-runtime-normal-${viewport.width}x${viewport.height}.png`, { fullPage: true });
  } else {
    // Linux CI still verifies a nonblank render and overflow; accepted pixels are Windows-specific.
    expect((await page.screenshot({ fullPage: true })).byteLength).toBeGreaterThan(10_000);
  }
});

test("资料库批量选择 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("button", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);
  const itemCheckbox = page.viewportSize()!.width < 1024
    ? page.locator("li").getByRole("checkbox", { name: "选择机电专业协同检查清单" })
    : page.getByRole("table").getByRole("checkbox", { name: "选择机电专业协同检查清单" });
  await itemCheckbox.check();
  await expect(page.getByText(/已选择\s*1\s*份/)).toBeVisible();
  await page.getByTestId("managed-bulk-toolbar").scrollIntoViewIfNeeded();
  const viewport = page.viewportSize()!;
  await expect(page).toHaveScreenshot(`managed-content-selected-${viewport.width}x${viewport.height}.png`, {
    maxDiffPixels: viewport.width === 1280 ? 100 : 0,
  });
});

test("资料库移入回收站确认 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("button", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);
  const title = page.getByText("建筑信息模型交付标准（合成长文件名用于响应式检查）", { exact: true }).filter({ visible: true });
  const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
  await item.getByRole("button", { name: "移至回收站", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "移至回收站" })).toBeVisible();
  const viewport = page.viewportSize()!;
  await expect(page).toHaveScreenshot(`managed-content-delete-confirm-${viewport.width}x${viewport.height}.png`);
});

test("索引任务区域 accepted golden", async ({ page }) => {
  test.skip(process.platform !== "win32", "索引任务区域 Linux golden 尚未在 Linux Chromium 上人工接受");
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("button", { name: "索引任务", exact: true }).click();
  const managedActivity = page.locator('[aria-labelledby="managed-index-title"]');
  await expect(page.getByText("文档解析服务请求失败。", { exact: true })).toBeVisible();
  const viewport = page.viewportSize()!;
  await expect(managedActivity).toHaveScreenshot(`index-monitor-managed-normal-${viewport.width}x${viewport.height}.png`);
});
