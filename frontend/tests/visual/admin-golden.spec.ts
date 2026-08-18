import { expect, test } from "@playwright/test";
import process from "node:process";
import { installAdminRoutes } from "./fixtures/admin-fixtures";

async function openRootFolder(page: Parameters<typeof installAdminRoutes>[0]) {
  const listing = page.waitForRequest((request) => request.method() === "GET" && request.url().includes("/api/admin/content/items-page") && request.url().includes("category_id=cat-company"));
  const folder = page.viewportSize()!.width < 1024
    ? page.getByTestId("managed-folder-mobile-cat-company")
    : page.getByTestId("managed-folder-row-cat-company");
  await folder.getByRole("button").first().click();
  await listing;
}

for (const [navigationLabel, heading, slug] of [["资料管理", "资料管理", "managed-content"], ["分类管理", "分类管理", "categories"], ["索引任务", "索引任务", "index-monitor"]] as const) {
  test(`${heading} accepted golden`, async ({ page }) => {
    test.skip(heading === "索引任务" && process.platform !== "win32", "索引任务 Linux golden 尚未在 Linux Chromium 上人工接受");
    await installAdminRoutes(page, "normal");
    await page.goto("/admin");
    if (page.viewportSize()!.width < 1024) {
      const mobileNavigation = page.getByRole("button", { name: "展开管理功能" });
      await expect(mobileNavigation).toBeVisible();
      await mobileNavigation.click();
    }
    await page.getByRole("link", { name: navigationLabel === "索引任务" ? "资料管理" : navigationLabel, exact: true }).click();
    if (navigationLabel === "索引任务") await page.getByRole("tab", { name: "索引任务", exact: true }).click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    const viewport = page.viewportSize()!;
    await expect(page).toHaveScreenshot(`${slug}-normal-${viewport.width}x${viewport.height}.png`, { fullPage: true });
  });
}

test("资料管理搜索筛选展开 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);
  await page.getByRole("textbox", { name: "搜索资料" }).click();
  const searchFilters = page.getByRole("dialog", { name: "搜索筛选" });
  await expect(searchFilters).toBeVisible();
  await expect(searchFilters.getByRole("combobox", { name: "状态", exact: true })).toHaveValue("");
  await expect(searchFilters.getByRole("combobox", { name: "来源", exact: true })).toHaveValue("");
  await expect(searchFilters.getByRole("combobox", { name: "分类", exact: true })).toHaveCount(0);
  const viewport = page.viewportSize()!;
  expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth))).toBeLessThanOrEqual(viewport.width);
  if (process.platform === "win32") {
    await expect(page).toHaveScreenshot(`managed-content-search-filters-${viewport.width}x${viewport.height}.png`, { fullPage: true });
  } else {
    // Linux CI still verifies the expanded layer renders; accepted pixels are Windows-specific.
    expect((await page.screenshot({ fullPage: true })).byteLength).toBeGreaterThan(10_000);
  }
});

test("资料管理文件夹移动 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  const folder = page.viewportSize()!.width < 1024
    ? page.getByTestId("managed-folder-mobile-cat-company")
    : page.getByTestId("managed-folder-row-cat-company");
  await folder.getByRole("button", { name: /移动文件夹/ }).click();
  const dialog = page.getByRole("dialog", { name: "移动文件夹位置" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("文件夹已经位于根目录")).toBeVisible();
  await expect(dialog.getByText("不能移动到文件夹自身")).toBeVisible();
  const viewport = page.viewportSize()!;
  expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth))).toBeLessThanOrEqual(viewport.width);
  if (process.platform === "win32") {
    await expect(page).toHaveScreenshot(`managed-content-folder-move-${viewport.width}x${viewport.height}.png`);
  } else {
    expect((await page.screenshot()).byteLength).toBeGreaterThan(10_000);
  }
});

test("资料管理审核窗口 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);
  const title = page.getByText("机电专业协同检查清单", { exact: true }).filter({ visible: true });
  const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
  await item.getByRole("button", { name: "审核", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "审核资料" });
  await dialog.getByRole("button", { name: "选择退回修改" }).click();
  await dialog.getByRole("textbox", { name: "退回原因" }).fill("请补充机电碰撞检查范围");
  await expect(dialog.getByRole("button", { name: "确认退回" })).toBeEnabled();
  const viewport = page.viewportSize()!;
  expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth))).toBeLessThanOrEqual(viewport.width);
  if (process.platform === "win32") {
    await expect(page).toHaveScreenshot(`managed-content-review-${viewport.width}x${viewport.height}.png`);
  } else {
    expect((await page.screenshot()).byteLength).toBeGreaterThan(10_000);
  }
});

test("资料管理视频转录稿 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "media_library");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);

  const title = "BIM 项目交付培训视频（合成长标题用于响应式检查）";
  await expect(page.getByText(title, { exact: true }).filter({ visible: true })).toBeVisible();
  await expect(page.getByText("视频转录稿", { exact: true }).filter({ visible: true }).first()).toBeVisible();
  await expect(page.getByText("有新转录稿待处理", { exact: true }).filter({ visible: true })).toBeVisible();
  await expect(page.getByRole("button", { name: `播放“${title}”` }).filter({ visible: true })).toBeVisible();
  await expect(page.getByRole("button", { name: `下载“${title}”` }).filter({ visible: true })).toBeVisible();
  await page.getByRole("button", { name: `更多“${title}”的操作` }).filter({ visible: true }).click();
  await expect(page.getByRole("menuitem", { name: "编辑转录稿" })).toHaveAttribute(
    "href",
    "/admin/media?media_id=media-library-1&workbench=1&action=edit-current",
  );
  await expect(page.getByRole("menuitem", { name: "替换视频" })).toHaveAttribute(
    "href",
    "/admin/media?media_id=media-library-1&action=replace",
  );
  await expect(page.getByRole("menuitem", { name: "进入视频管理" })).toHaveAttribute(
    "href",
    "/admin/media?media_id=media-library-1&workbench=1",
  );
  await page.keyboard.press("Escape");

  const viewport = page.viewportSize()!;
  expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth))).toBeLessThanOrEqual(viewport.width);
  if (process.platform === "win32") {
    await expect(page).toHaveScreenshot(`managed-content-media-transcript-${viewport.width}x${viewport.height}.png`, { fullPage: true });
  } else {
    expect((await page.screenshot({ fullPage: true })).byteLength).toBeGreaterThan(10_000);
  }
});

test("资料管理已发布文档调整分类 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);

  const title = "企业知识库使用规范";
  const itemTitle = page.getByText(title, { exact: true }).filter({ visible: true });
  const item = page.viewportSize()!.width < 1024
    ? itemTitle.locator("xpath=ancestor::li")
    : itemTitle.locator("xpath=ancestor::tr");
  await item.getByRole("button", { name: `调整“${title}”的分类` }).click();
  const dialog = page.getByRole("dialog", { name: "调整分类" });
  await expect(dialog).toContainText("同步完成前资料仍保留在原目录");
  await expect(dialog.getByText("目标目录", { exact: true })).toBeVisible();

  const viewport = page.viewportSize()!;
  expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth))).toBeLessThanOrEqual(viewport.width);
  if (process.platform === "win32") {
    await expect(page).toHaveScreenshot(`managed-content-reclassify-${viewport.width}x${viewport.height}.png`);
  } else {
    expect((await page.screenshot()).byteLength).toBeGreaterThan(10_000);
  }
});

test("系统概览生产运行状态 accepted golden", async ({ page }) => {
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

test("资料管理批量操作 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);
  const itemCheckbox = page.viewportSize()!.width < 1024
    ? page.locator("li").getByRole("checkbox", { name: "选择机电专业协同检查清单" })
    : page.getByRole("table").getByRole("checkbox", { name: "选择机电专业协同检查清单" });
  await itemCheckbox.check();
  await page.getByRole("checkbox", { name: "选择建筑信息模型交付标准（合成长文件名用于响应式检查）" }).check();
  await expect(page.getByText(/已选择\s*2\s*份/)).toBeVisible();
  await page.getByRole("button", { name: "批量操作" }).click();
  await expect(page.getByRole("menu", { name: "批量操作" })).toBeVisible();
  const viewport = page.viewportSize()!;
  await expect(page).toHaveScreenshot(`managed-content-selected-${viewport.width}x${viewport.height}.png`, {
    maxDiffPixels: viewport.width === 1280 ? 100 : 0,
  });
});

test("资料管理移入回收站确认 accepted golden", async ({ page }) => {
  await installAdminRoutes(page, "normal");
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "展开管理功能" }).click();
  }
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  await openRootFolder(page);
  const title = page.getByText("建筑信息模型交付标准（合成长文件名用于响应式检查）", { exact: true }).filter({ visible: true });
  const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
  await item.getByRole("button", { name: `更多“建筑信息模型交付标准（合成长文件名用于响应式检查）”的操作`, exact: true }).click();
  await page.getByRole("menu", { name: "“建筑信息模型交付标准（合成长文件名用于响应式检查）”的更多操作" }).getByRole("menuitem", { name: "移至回收站" }).click();
  await expect(page.getByRole("dialog", { name: "将资料移入回收站？" })).toBeVisible();
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
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  await page.getByRole("tab", { name: "索引任务", exact: true }).click();
  const managedActivity = page.locator('[aria-labelledby="managed-index-view-title"]');
  await expect(page.getByText("文档解析服务请求失败。", { exact: true })).toBeVisible();
  const viewport = page.viewportSize()!;
  await expect(managedActivity).toHaveScreenshot(`index-monitor-managed-normal-${viewport.width}x${viewport.height}.png`);
});
