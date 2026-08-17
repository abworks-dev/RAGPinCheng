import { expect, test } from "@playwright/test";
import { installAdminRoutes, type AdminScenario } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow, expectTouchTarget } from "./helpers/layout";

async function openTab(page: Parameters<typeof installAdminRoutes>[0], label: string, scenario: AdminScenario = "normal", workspaceUser: "admin" | "bim_engineer" | "member" = "admin", options: { includeChildFolder?: boolean; includeFolderRequest?: boolean } = {}) {
  await installAdminRoutes(page, scenario, workspaceUser, undefined, options);
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) {
    const mobileNavigation = page.getByRole("button", { name: "展开管理功能" });
    await expect(mobileNavigation).toBeVisible();
    await mobileNavigation.click();
  }
  await page.getByRole("link", { name: label === "索引任务" ? "资料管理" : label, exact: true }).click();
  if (label === "索引任务") await page.getByRole("tab", { name: "索引任务", exact: true }).click();
}

async function openRootFolder(page: Parameters<typeof installAdminRoutes>[0], folderId = "cat-company") {
  const listing = page.waitForRequest((request) => request.method() === "GET" && request.url().includes("/api/admin/content/items-page") && request.url().includes(`category_id=${folderId}`));
  const folder = page.viewportSize()!.width < 1024
    ? page.getByTestId(`managed-folder-mobile-${folderId}`)
    : page.getByTestId(`managed-folder-row-${folderId}`);
  await folder.click();
  await listing;
}

test.describe("资料管理", () => {
  test("normal layout keeps navigation and upload controls discoverable", async ({ page }) => {
    await openTab(page, "资料管理");
    await expect(page.getByRole("heading", { name: "资料管理" })).toBeVisible();
    await expectNoBodyOverflow(page);
    await expectInViewport(page.getByRole("button", { name: "刷新" }));
    await expect(page.getByRole("button", { name: "/", exact: true })).toBeVisible();
    const switchedListing = page.waitForRequest((request) => request.method() === "GET" && request.url().includes("/api/admin/content/items-page") && request.url().includes("category_id=cat-project"));
    const projectFolder = page.viewportSize()!.width < 1024
      ? page.getByTestId("managed-folder-mobile-cat-project")
      : page.getByTestId("managed-folder-row-cat-project");
    await projectFolder.click();
    await switchedListing;
    await expect(page.getByText(/当前目录：/)).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "资料路径" }).getByRole("button", { name: "04 项目资料" })).toBeVisible();
    await page.getByRole("button", { name: "上传文件" }).scrollIntoViewIfNeeded();
    await expectInViewport(page.getByRole("button", { name: "上传文件" }));
    if (page.viewportSize()!.width === 390) await expectTouchTarget(page.getByRole("button", { name: "上传文件" }));
    const search = page.getByRole("textbox", { name: "搜索资料" });
    await search.click();
    const searchFilters = page.getByRole("dialog", { name: "搜索筛选" });
    await expect(searchFilters).toBeVisible();
    await expect(searchFilters.getByRole("combobox", { name: "状态", exact: true })).toHaveValue("");
    await expect(searchFilters.getByRole("combobox", { name: "来源", exact: true })).toHaveValue("");
    await expect(searchFilters.getByRole("combobox", { name: "分类", exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByText("未选择资料，单次最多 20 份")).toBeVisible();
    await expect(page.getByRole("button", { name: "新建目录" })).toBeVisible();
    await expect(page.getByRole("button", { name: "批量操作" })).toHaveCount(0);

    const longTitle = "建筑信息模型交付标准（合成长文件名用于响应式检查）";
    const title = page.getByText(longTitle, { exact: true }).filter({ visible: true });
    const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
    for (const actionName of [`查看“${longTitle}”的详细信息`, `预览“${longTitle}”`, `移动“${longTitle}”`, `下载“${longTitle}”`, `重命名“${longTitle}”`, `更新“${longTitle}”`, `删除“${longTitle}”`]) {
      await expect(item.getByRole("button", { name: actionName, exact: true })).toBeVisible();
    }
    const previewButton = item.getByRole("button", { name: `预览“${longTitle}”`, exact: true });
    await previewButton.hover();
    const actionTooltip = page.getByRole("tooltip", { name: "预览文件" });
    await expect(actionTooltip).toBeVisible();
    await expectInViewport(actionTooltip);
    const [previewBox, tooltipBox] = await Promise.all([previewButton.boundingBox(), actionTooltip.boundingBox()]);
    expect(previewBox).not.toBeNull();
    expect(tooltipBox).not.toBeNull();
    const previewCenter = previewBox!.x + previewBox!.width / 2;
    const tooltipCenter = tooltipBox!.x + tooltipBox!.width / 2;
    expect(Math.abs(previewCenter - tooltipCenter)).toBeLessThanOrEqual(2);
    await page.mouse.move(0, 0);
    await expect(actionTooltip).toBeHidden();
    const deleteButton = item.getByRole("button", { name: `删除“${longTitle}”`, exact: true });
    await deleteButton.scrollIntoViewIfNeeded();
    await expectInViewport(deleteButton);
    if (page.viewportSize()!.width === 390) await expectTouchTarget(deleteButton);

    await page.getByRole("checkbox", { name: `选择${longTitle}` }).check();
    await expect(page.getByText(/已选择\s*1\s*份，单次最多\s*20\s*份/)).toBeVisible();
    await expect(page.getByRole("button", { name: "新建目录" })).toBeVisible();
    await page.getByRole("checkbox", { name: "选择机电专业协同检查清单" }).check();
    const batchButton = page.getByRole("button", { name: "批量操作" });
    await expect(batchButton).toBeVisible();
    await expect(page.getByRole("button", { name: "新建目录" })).toHaveCount(0);
    await batchButton.focus();
    await batchButton.press("Enter");
    await expectInViewport(page.getByRole("menu", { name: "批量操作" }));
    await expect(page.getByRole("menuitem", { name: "批量移动" })).toBeFocused();
    await page.getByRole("menuitem", { name: "批量移动" }).press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "批量确认" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(batchButton).toBeFocused();
  });

  test("upload tasks keep columns aligned and apply explicit search filters", async ({ page }, testInfo) => {
    await openTab(page, "资料管理");
    await page.getByRole("tab", { name: "上传任务" }).click();
    await expect(page.getByRole("heading", { name: "上传任务" })).toBeVisible();
    await expect(page.getByText("已接收 2 个 · 跳过 1 个")).toBeVisible();
    await expect(page.getByText("未完成 1 个")).toBeVisible();
    await expectNoBodyOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("managed-content-upload-tasks-normal.png"), fullPage: true });

    if (page.viewportSize()!.width >= 1280) {
      const headerX = await page.getByTestId("upload-task-header").locator(":scope > span").evaluateAll((cells) => cells.map((cell) => cell.getBoundingClientRect().x));
      const rows = page.getByTestId("upload-task-row");
      for (let index = 0; index < await rows.count(); index += 1) {
        const rowX = await rows.nth(index).locator(":scope > *").evaluateAll((cells) => cells.map((cell) => cell.getBoundingClientRect().x));
        expect(rowX).toHaveLength(headerX.length);
        rowX.forEach((x, column) => expect(Math.abs(x - headerX[column])).toBeLessThanOrEqual(1));
      }
    } else {
      await expect(page.getByTestId("upload-task-header")).toBeHidden();
    }

    const search = page.getByRole("searchbox", { name: "搜索上传任务" });
    await search.fill("竣工交付");
    const searchRequest = page.waitForRequest((request) => new URL(request.url()).searchParams.get("query") === "竣工交付");
    await page.getByRole("button", { name: "搜索", exact: true }).click();
    await searchRequest;
    await expect(page.getByText("04 项目资料 / 02 竣工交付")).toBeVisible();
    await expect(page.getByText("01 行业规范与标准 / 02 文件夹上传测试")).toHaveCount(0);

    await page.getByRole("button", { name: "清除筛选", exact: true }).click();
    await expect(page.getByText("01 行业规范与标准 / 02 文件夹上传测试")).toBeVisible();
    await page.getByRole("button", { name: /失败\s*1/ }).click();
    await expect(page.getByText("01 行业规范与标准 / 02 文件夹上传测试")).toBeVisible();
    await expect(page.getByText("04 项目资料 / 02 竣工交付")).toHaveCount(0);
    await expectNoBodyOverflow(page);
  });

  test("child folders share the list and stay before paginated files", async ({ page }) => {
    await openTab(page, "资料管理", "normal", "admin", { includeChildFolder: true });
    await openRootFolder(page);

    const isMobile = page.viewportSize()!.width < 1024;
    const folder = isMobile
      ? page.getByTestId("managed-folder-mobile-cat-company-modeling")
      : page.getByTestId("managed-folder-row-cat-company-modeling");
    const fileTitle = page.getByText("建筑信息模型交付标准（合成长文件名用于响应式检查）", { exact: true }).filter({ visible: true });
    const fileEntry = isMobile ? fileTitle.locator("xpath=ancestor::li") : fileTitle.locator("xpath=ancestor::tr");
    await expect(folder).toBeVisible();
    await expect(fileEntry).toBeVisible();
    await expect(folder.getByRole("checkbox")).toHaveCount(0);
    await expect(page.getByText("共 5 份，第 1 / 1 页")).toBeVisible();

    const [folderBox, fileBox] = await Promise.all([folder.boundingBox(), fileEntry.boundingBox()]);
    expect(folderBox).not.toBeNull();
    expect(fileBox).not.toBeNull();
    expect(folderBox!.y).toBeLessThan(fileBox!.y);
    if (isMobile) await expectTouchTarget(folder.getByRole("button", { name: /01 建模标准/ }));
    await expectNoBodyOverflow(page);

    const childListing = page.waitForRequest((request) => request.method() === "GET" && request.url().includes("/api/admin/content/items-page") && request.url().includes("category_id=cat-company-modeling"));
    await folder.click();
    await childListing;
    await expect(page.getByRole("navigation", { name: "资料路径" })).toContainText("01 建模标准（长名称用于响应式检查）");
  });

  test("single-file actions expose independent move, download, rename, and update flows", async ({ page }, testInfo) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const longTitle = "建筑信息模型交付标准（合成长文件名用于响应式检查）";
    const title = page.getByText(longTitle, { exact: true }).filter({ visible: true });
    const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");

    await item.getByRole("button", { name: `移动“${longTitle}”`, exact: true }).click();
    const moveDialog = page.getByRole("dialog", { name: "移动资料" });
    await expect(moveDialog).toContainText(longTitle);
    await expect(moveDialog.getByTestId("category-picker-item-cat-company")).toHaveAttribute("aria-disabled", "true");
    await moveDialog.getByTestId("category-picker-item-cat-project").click();
    await expect(moveDialog.getByText("已选择：04 项目资料")).toBeVisible();
    await expect(moveDialog.getByRole("button", { name: "确认移动", exact: true })).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath("managed-content-move-picker.png"), fullPage: false });
    await expectNoBodyOverflow(page);
    await moveDialog.getByRole("button", { name: "取消" }).click();

    const downloadPromise = page.waitForEvent("download");
    await item.getByRole("button", { name: `下载“${longTitle}”`, exact: true }).click();
    expect((await downloadPromise).suggestedFilename()).toBe(`${longTitle}.pdf`);

    await item.getByRole("button", { name: `重命名“${longTitle}”`, exact: true }).click();
    const renameDialog = page.getByRole("dialog", { name: "重命名资料" });
    await expect(renameDialog.getByRole("textbox", { name: "资料标题" })).toHaveValue(longTitle);
    await expect(renameDialog.getByRole("textbox", { name: /^源文件名/ })).toHaveValue(`${longTitle}.pdf`);
    await expect(renameDialog).toContainText("需要重新确认并发布");
    await renameDialog.getByRole("button", { name: "取消" }).click();

    await item.getByRole("button", { name: `更新“${longTitle}”`, exact: true }).click();
    const updateDialog = page.getByRole("dialog", { name: "更新资料文件" });
    await updateDialog.getByLabel("选择替换文件").setInputFiles({ name: "replacement.md", mimeType: "text/markdown", buffer: Buffer.from("# synthetic") });
    await expect(updateDialog).toContainText(`将使用原名称并匹配新格式：${longTitle}.md`);
    await expect(updateDialog.getByRole("button", { name: "确认更新" })).toBeEnabled();
    await expectNoBodyOverflow(page);
  });

  test("batch move and delete confirmations preserve selected-file context", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const firstTitle = "建筑信息模型交付标准（合成长文件名用于响应式检查）";
    const secondTitle = "机电专业协同检查清单";
    await page.getByRole("checkbox", { name: `选择${firstTitle}` }).check();
    await page.getByRole("checkbox", { name: `选择${secondTitle}` }).check();

    await page.getByRole("button", { name: "批量操作" }).click();
    await page.getByRole("menuitem", { name: "批量移动" }).click();
    const moveDialog = page.getByRole("dialog", { name: "批量移动资料" });
    await expect(moveDialog).toContainText("已选择 2 份资料");
    await moveDialog.getByTestId("category-picker-item-cat-project").click();
    await expect(moveDialog.getByRole("button", { name: "确认执行" })).toBeEnabled();
    await moveDialog.getByRole("button", { name: "取消" }).click();

    await page.getByRole("button", { name: "批量操作" }).click();
    await page.getByRole("menuitem", { name: "批量删除" }).click();
    const deleteDialog = page.getByRole("dialog", { name: "将 2 份资料移入回收站？" });
    await expect(deleteDialog).toContainText(firstTitle);
    await expect(deleteDialog).toContainText(secondTitle);
    await expect(deleteDialog).toContainText("不再进入检索");
    await expect(deleteDialog.getByRole("button", { name: "确认移入回收站" })).toBeDisabled();
    await expectNoBodyOverflow(page);
  });

  test("batch download stays visible and starts a single archive download", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const firstTitle = "建筑信息模型交付标准（合成长文件名用于响应式检查）";
    const secondTitle = "机电专业协同检查清单";
    await page.getByRole("checkbox", { name: `选择${firstTitle}` }).check();
    await page.getByRole("checkbox", { name: `选择${secondTitle}` }).check();

    await page.getByRole("button", { name: "批量操作" }).click();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: "批量下载" }).click();
    const packagingToast = page.locator("[data-sonner-toast]").filter({ hasText: "正在打包 2 份资料，请稍候" });
    await expect(packagingToast).toBeVisible();
    const toastBox = await packagingToast.boundingBox();
    expect(toastBox).not.toBeNull();
    expect(toastBox!.y).toBeLessThan(120);
    expect(toastBox!.x + toastBox!.width).toBeGreaterThanOrEqual(page.viewportSize()!.width - 40);
    await expect(page.getByRole("button", { name: "批量操作" })).toBeDisabled();
    expect((await downloadPromise).suggestedFilename()).toBe("managed-content.zip");
    await expect(page.getByText("已打包 2 份资料并开始下载")).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("trash shows original location and archive metadata without overflowing", async ({ page }) => {
    await openTab(page, "资料管理");
    await page.getByRole("tab", { name: "回收站" }).click();
    await expect(page.getByRole("heading", { name: "回收站", exact: true })).toBeVisible();

    const desktop = page.viewportSize()!.width >= 1024;
    const row = desktop
      ? page.getByRole("table").getByRole("row").filter({ hasText: "企业知识库使用规范" })
      : page.locator("li").filter({ hasText: "企业知识库使用规范" });
    await expect(row.getByText("03 公司内部标准 / 01 建模 / 02 机电")).toBeVisible();
    await expect(row.getByText("公司知识库归档/制度与流程/企业知识库使用规范.md")).toBeVisible();
    await expect(row.getByText("历史迁移")).toBeVisible();
    await expect(row.getByText("合成资料员")).toBeVisible();
    await row.getByRole("button", { name: "恢复" }).scrollIntoViewIfNeeded();
    await expectInViewport(row.getByRole("button", { name: "恢复" }));
    if (desktop) {
      await expect(page.getByRole("columnheader", { name: "原目录" })).toBeVisible();
      await expect(page.getByRole("columnheader", { name: "原状态" })).toBeVisible();
    } else {
      await expect(page.getByRole("table")).toBeHidden();
      await expect(row.getByText("上传路径", { exact: true })).toBeVisible();
    }
    await expectNoBodyOverflow(page);
  });

  for (const scenario of ["loading", "empty", "error", "disabled"] as const) {
    test(`${scenario} state is explicit and contained`, async ({ page }, testInfo) => {
      await openTab(page, "资料管理", scenario);
      await expectNoBodyOverflow(page);
      if (scenario === "loading") await expect(page.getByRole("heading", { name: "资料管理" })).toBeVisible();
      if (scenario === "empty") {
        await expect(page.getByText("没有符合条件的资料")).toBeVisible();
        const emptyListHeight = await page.getByTestId("managed-content-drop-list").evaluate((element) => element.getBoundingClientRect().height);
        expect(emptyListHeight).toBeGreaterThanOrEqual(page.viewportSize()!.width < 640 ? 224 : 256);
        await page.screenshot({ path: testInfo.outputPath("managed-content-empty-state.png"), fullPage: true });
      }
      if (scenario === "error") await expect(page.getByText("合成加载失败")).toBeVisible();
      if (scenario === "disabled") {
        await expect(page.getByText("资料管理当前未启用，上传和流程操作暂不可用。")).toBeVisible();
        await expect(page.getByRole("button", { name: "上传文件" })).toBeDisabled();
      }
    });
  }

  test("upload exposes a stable busy state", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    await page.getByRole("button", { name: "上传文件" }).click();
    await page.getByLabel("选择资料文件", { exact: true }).setInputFiles({ name: "synthetic.pdf", mimeType: "application/pdf", buffer: Buffer.from("synthetic fixture") });
    const upload = page.getByRole("button", { name: "确定上传" });
    await upload.click();
    await expect(page.getByRole("button", { name: "上传中…" })).toBeDisabled();
    await expectNoBodyOverflow(page);
  });

  test("folder upload confirmation keeps hierarchy and summary contained", async ({ page }, testInfo) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    await page.getByRole("button", { name: "上传文件" }).click();
    const folderButton = page.getByRole("button", { name: "上传文件夹" });
    await expect(folderButton).toBeVisible();
    if (page.viewportSize()!.width === 390) await expectTouchTarget(folderButton);

    await page.getByLabel("选择资料文件夹").evaluate((element: HTMLInputElement) => {
      const transfer = new DataTransfer();
      const guide = new File(["# Synthetic guide"], "guide.md", { type: "text/markdown" });
      Object.defineProperty(guide, "webkitRelativePath", { value: "合成资料包/01 建筑/guide.md" });
      const ignored = new File(["synthetic video"], "demo.mp4", { type: "video/mp4" });
      Object.defineProperty(ignored, "webkitRelativePath", { value: "合成资料包/demo.mp4" });
      transfer.items.add(guide);
      transfer.items.add(ignored);
      Object.defineProperty(element, "files", { configurable: true, value: transfer.files });
      element.dispatchEvent(new Event("change", { bubbles: true }));
    });

    const dialog = page.getByRole("dialog", { name: "上传文件夹" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("合成资料包");
    await expect(dialog).toContainText("2 个");
    await expect(dialog).toContainText("可上传文件");
    await expect(dialog).toContainText("已忽略");
    await expect(dialog).toContainText("合成资料包/01 建筑/guide.md");
    await expect(dialog).toContainText("合成资料包/demo.mp4");
    await expectInViewport(dialog.getByRole("button", { name: "开始上传" }));
    await expectNoBodyOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("managed-content-folder-upload-confirmation.png"), fullPage: true });
  });

  test("dropping local files on the current folder requires confirmation", async ({ page }, testInfo) => {
    await openTab(page, "资料管理");
    await page.route("**/api/admin/content/items-page**", (route) => route.fulfill({ json: { items: [], total: 0, status_counts: {} } }));
    await openRootFolder(page);
    const uploadRequests: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().includes("/api/admin/content/uploads")) {
        uploadRequests.push(request.postData() || "");
      }
    });
    const folderBrowser = page.getByTestId("managed-content-drop-list");
    const dataTransfer = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(new File(["# Dropped fixture"], "dropped.md", { type: "text/markdown" }));
      return transfer;
    });

    await expect(page.getByRole("button", { name: "上传文件" })).toBeEnabled();
    expect(await dataTransfer.evaluate((transfer) => Array.from(transfer.types))).toContain("Files");
    await folderBrowser.dispatchEvent("dragenter", { dataTransfer });
    const dropOverlay = page.getByTestId("managed-content-drop-overlay");
    await expect(dropOverlay).toBeVisible();
    await expect(dropOverlay).toContainText("松开以上传文件到“03 公司内部标准”");
    await expect(dropOverlay).toContainText("支持 PDF、Markdown、Word、Excel 和 PPT 文件");
    await dropOverlay.scrollIntoViewIfNeeded();
    await folderBrowser.dispatchEvent("dragover", { dataTransfer });
    await expectInViewport(dropOverlay.getByText("松开以上传文件到“03 公司内部标准”"));
    await page.screenshot({ path: testInfo.outputPath("managed-content-empty-drop-overlay.png"), fullPage: true });
    await folderBrowser.dispatchEvent("drop", { dataTransfer });
    await expect(dropOverlay).toBeHidden();

    const dialog = page.getByRole("dialog", { name: "确认上传" });
    await expect(dialog).toContainText("03 公司内部标准");
    await expect(dialog).toContainText("dropped.md");
    expect(uploadRequests).toHaveLength(0);
    await expectNoBodyOverflow(page);

    await dialog.getByRole("button", { name: "确定上传" }).click();
    await expect.poll(() => uploadRequests.length).toBe(1);
    expect(uploadRequests[0]).toContain('name="category_id"');
    expect(uploadRequests[0]).toContain("cat-company");
  });

  test("folder request and review controls stay contained", async ({ page }) => {
    await openTab(page, "资料管理", "normal", "bim_engineer");
    await openRootFolder(page);
    await page.getByRole("button", { name: "新建" }).click();
    const dialog = page.getByRole("dialog", { name: "申请新建文件夹" });
    await expect(dialog).toBeVisible();
    await expectInViewport(dialog.getByRole("button", { name: "提交申请" }));
    await expectNoBodyOverflow(page);

    await page.reload();
    await installAdminRoutes(page, "normal", "admin", undefined, { includeFolderRequest: true });
    await page.goto("/admin");
    if (page.viewportSize()!.width < 1024) {
      await page.getByRole("button", { name: "展开管理功能" }).click();
    }
    await page.getByRole("link", { name: "资料管理", exact: true }).click();
    await expect(page.getByText("待处理目录申请")).toBeVisible();
    await expect(page.getByText("审核标准", { exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("desktop rows can be dragged into a child folder", async ({ page }) => {
    test.skip(page.viewportSize()!.width < 1024, "桌面增强只在桌面表格中启用");
    await openTab(page, "资料管理", "normal", "admin", { includeChildFolder: true });
    await openRootFolder(page);
    const row = page.getByTitle("拖动到文件夹行可调整目录").first();
    const folder = page.getByTestId("managed-folder-row-cat-company-modeling");
    await expect(row).toBeVisible();
    await expect(folder).toBeVisible();
    await row.dispatchEvent("dragstart");
    await expect(folder).toHaveClass(/bg-primary\/5/);
    const requestPromise = page.waitForRequest((request) => request.method() === "POST" && request.url().includes("/move"));
    await folder.dispatchEvent("dragover");
    await folder.dispatchEvent("drop");
    await requestPromise;
  });

  test("publication failure stays readable and actionable", async ({ page }) => {
    await openTab(page, "资料管理", "publication_failure");
    await openRootFolder(page);
    await page.getByRole("textbox", { name: "搜索资料" }).click();
    await page.getByRole("dialog", { name: "搜索筛选" }).getByRole("combobox", { name: "状态", exact: true }).selectOption("publication_failed");
    const failedTitle = page.getByText("培训资料发布演练", { exact: true }).filter({ visible: true });
    const failedItem = page.viewportSize()!.width < 1024 ? failedTitle.locator("xpath=ancestor::li") : failedTitle.locator("xpath=ancestor::tr");
    await failedItem.getByRole("button", { name: "查看“培训资料发布演练”的详细信息" }).click();
    const detail = page.getByRole("dialog", { name: "培训资料发布演练" });
    await expect(detail).toContainText("PDF 需要密码才能解析。");
    await expect(detail).toContainText("请上传已解除密码保护的 PDF。");
    await expect(detail).toContainText("共尝试 4 次");
    const republish = detail.getByRole("button", { name: "重新发布" });
    await expect(republish).toBeEnabled();
    await republish.click();
    const confirmation = page.getByRole("dialog", { name: "重新发布资料" });
    await expect(confirmation).toContainText("完成后资料才会进入知识库检索");
    await confirmation.getByRole("button", { name: "确认重新发布" }).click();
    await expect(confirmation.getByRole("button", { name: "发布中…" })).toBeDisabled();
    await expectNoBodyOverflow(page);
  });

  test("review workflow requires a rejection reason and preserves its busy layout", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const title = page.getByText("机电专业协同检查清单", { exact: true }).filter({ visible: true });
    const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
    const workflow = item.getByRole("button", { name: "审核", exact: true });
    const detailButton = item.getByRole("button", { name: "查看“机电专业协同检查清单”的详细信息" });
    await expect(workflow).toBeVisible();
    if (page.viewportSize()!.width >= 1024) {
      const workflowBox = await workflow.boundingBox();
      const detailBox = await detailButton.boundingBox();
      expect(workflowBox).not.toBeNull();
      expect(detailBox).not.toBeNull();
      const actionGap = detailBox!.x - (workflowBox!.x + workflowBox!.width);
      expect(actionGap).toBeGreaterThanOrEqual(4);
      expect(actionGap).toBeLessThanOrEqual(12);
    } else if (page.viewportSize()!.width === 390) {
      const workflowBox = await workflow.boundingBox();
      const detailBox = await detailButton.boundingBox();
      expect(workflowBox).not.toBeNull();
      expect(detailBox).not.toBeNull();
      expect(workflowBox!.y + workflowBox!.height).toBeLessThanOrEqual(detailBox!.y);
      await expectTouchTarget(workflow);
    }
    await workflow.click();

    const dialog = page.getByRole("dialog", { name: "审核资料" });
    await expect(dialog).toContainText("04 项目资料 / 02 竣工交付 / 01 模型成果");
    await expect(dialog).toContainText("v2");
    await expect(dialog.getByRole("button", { name: "预览文件" })).toBeVisible();
    await dialog.getByRole("button", { name: "预览文件" }).click();
    await expect(page.getByRole("button", { name: "返回资料审核" })).toBeVisible();
    await page.getByRole("button", { name: "返回资料审核" }).click();
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "选择退回修改" }).click();
    const confirm = dialog.getByRole("button", { name: "确认退回" });
    await expect(confirm).toBeDisabled();
    const reason = dialog.getByRole("textbox", { name: "退回原因" });
    await reason.fill("请补充机电碰撞检查范围");
    await confirm.click();
    await expect(dialog.getByRole("button", { name: "提交中…" })).toBeDisabled();
    await expect(reason).toHaveValue("请补充机电碰撞检查范围");
    await expectNoBodyOverflow(page);
  });

  test("indexed files return from the shared preview sheet to their detail dialog", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const title = page.getByText("建筑信息模型交付标准（合成长文件名用于响应式检查）", { exact: true }).filter({ visible: true });
    const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
    const preview = item.getByRole("button", { name: `预览“建筑信息模型交付标准（合成长文件名用于响应式检查）”`, exact: true });
    await preview.click();
    await expect(page.getByRole("button", { name: "关闭预览" })).toBeVisible();
    await expect(page.getByRole("button", { name: "返回资料详情" })).toHaveCount(0);
    await page.getByRole("button", { name: "关闭预览" }).click();

    await item.getByRole("button", { name: `查看“建筑信息模型交付标准（合成长文件名用于响应式检查）”的详细信息`, exact: true }).click();

    const detail = page.getByRole("dialog").filter({ has: page.getByRole("button", { name: "预览文件" }) });
    await expect(detail).toBeVisible();
    await detail.getByRole("button", { name: "预览文件" }).click();
    await expect(page.getByRole("button", { name: "返回资料详情" })).toBeVisible();
    await expect(page.getByRole("button", { name: "关闭预览" })).toBeVisible();
    await expect(detail).toBeHidden();
    await expectNoBodyOverflow(page);

    await page.getByRole("button", { name: "返回资料详情" }).click();
    await expect(detail).toBeVisible();
    await expect(detail.getByRole("link", { name: "下载" })).toHaveCount(0);
    await expect(detail.getByRole("button", { name: "下载" })).toHaveCount(0);
    await expectNoBodyOverflow(page);
  });

  test("move-to-trash confirmation explains impact and exposes a stable busy state", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const title = page.getByText("建筑信息模型交付标准（合成长文件名用于响应式检查）", { exact: true }).filter({ visible: true });
    const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
    const remove = item.getByRole("button", { name: `删除“建筑信息模型交付标准（合成长文件名用于响应式检查）”`, exact: true });
    await remove.scrollIntoViewIfNeeded();
    await expectInViewport(remove);
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "将资料移入回收站？" });
    await expect(dialog).toContainText("将立即停止进入知识库检索");
    await expect(dialog).toContainText("文件、版本及审核发布历史会保留");
    await expectNoBodyOverflow(page);
    await dialog.getByRole("checkbox").check();
    const confirm = dialog.getByRole("button", { name: "确认移入回收站" });
    await confirm.click();
    await expect(dialog.getByRole("button", { name: "处理中…" })).toBeDisabled();
  });

  test("trash exposes archive context and a stable restore flow", async ({ page }) => {
    await openTab(page, "资料管理");
    await page.getByRole("tab", { name: "回收站" }).click();
    await expect(page.getByRole("heading", { name: "回收站", exact: true })).toBeVisible();
    await expect(page.getByText("合成资料员", { exact: true }).filter({ visible: true }).first()).toBeVisible();
    await expect(page.getByText("已发布", { exact: true }).filter({ visible: true }).first()).toBeVisible();
    await expectNoBodyOverflow(page);
    const restore = page.getByRole("button", { name: "恢复", exact: true });
    await restore.scrollIntoViewIfNeeded();
    await expectInViewport(restore);
    const restoreRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/items/item-5/restore"));
    await restore.click();
    const dialog = page.getByRole("dialog", { name: "恢复资料" });
    await expect(dialog).toContainText("需要具备发布权限的人员重新发布后才会进入检索");
    await dialog.getByRole("button", { name: "确认恢复" }).click();
    await expect(dialog.getByRole("button", { name: "恢复中…" })).toBeDisabled();
    await restoreRequest;
  });
});

test.describe("视频管理", () => {
  test("media records keep duplicate submissions and recovery actions readable", async ({ page }) => {
    await openTab(page, "视频管理");
    await expect(page.getByRole("heading", { name: "视频管理" })).toBeVisible();
    await expect(page.getByText("同名记录 2 条").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "全部 3 条" })).toBeVisible();
    await expect(page.getByRole("button", { name: "失败 2 条" })).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新媒体资源" })).toBeVisible();
    await expect(page.getByText("转录服务当前暂停接收任务，请稍后重试。")).toBeVisible();
    await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
    const readyMediaRow = page.getByTestId("media-record-row").filter({ hasText: "项目交付培训" });
    const workbenchTrigger = readyMediaRow.getByRole("button", { name: "进入转写工作台" });
    await expect(workbenchTrigger).toBeVisible();
    await workbenchTrigger.click();
    const workbench = page.getByRole("dialog", { name: "项目交付培训" });
    await expect(workbench).toBeVisible();
    await expect(workbench.getByRole("button", { name: /自动转录/ }).first()).toBeVisible();
    await expect(workbench.getByRole("textbox", { name: /审核备注/ })).toBeVisible();
    await expect(workbench.getByText("审核通过后可发布").first()).toBeVisible();
    await expect(workbench.getByText("synthetic-asr")).toBeHidden();
    await workbench.getByRole("button", { name: "校对内容" }).first().click();
    await expect(workbench.getByRole("textbox", { name: "转录 Markdown 编辑器" })).toBeVisible();
    if (page.viewportSize()!.width < 768) {
      await workbench.getByRole("button", { name: "预览" }).click();
    }
    await expect(workbench.getByRole("region", { name: "Markdown 预览" })).toContainText("培训开始");
    await workbench.getByRole("button", { name: "关闭转写工作台" }).click();
    await expect(workbench).toBeHidden();
    await expectNoBodyOverflow(page);
    if (page.viewportSize()!.width >= 1024) {
      const headerX = await page.getByTestId("media-record-header").locator(":scope > span").evaluateAll((cells) => cells.map((cell) => cell.getBoundingClientRect().x));
      const rowX = await page.getByTestId("media-record-row").first().locator(":scope > div > *").evaluateAll((cells) => cells.map((cell) => cell.getBoundingClientRect().x));
      expect(rowX).toHaveLength(headerX.length);
      rowX.forEach((x, index) => expect(Math.abs(x - headerX[index])).toBeLessThanOrEqual(1));
    }
    if (page.viewportSize()!.width === 390) {
      const retry = page.getByRole("button", { name: "重试" });
      await retry.scrollIntoViewIfNeeded();
      await expectInViewport(retry);
      await expectTouchTarget(retry);
    }
  });

});

test.describe("索引任务", () => {
  test("normal layout keeps publication identity, filters, and failures discoverable", async ({ page }) => {
    await openTab(page, "索引任务");
    await expect(page.getByRole("heading", { name: "索引任务" })).toBeVisible();
    await expect(page.getByText("资料管理发布失败的合成长文件名资料", { exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
    await expect(page.getByRole("button", { name: "上传资料" })).toHaveCount(0);
    await expect(page.getByText("旧索引资料", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("searchbox", { name: "搜索发布任务" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "按数据库分类筛选" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "按资料来源筛选" })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看历史尝试" })).toBeVisible();
    await expect(page.getByRole("link", { name: "查看文件" })).toBeVisible();
    await expect(page.getByRole("button", { name: "重新发布" })).toBeVisible();

    const publicationFailure = page.getByText("文档解析服务请求失败。", { exact: true });
    await publicationFailure.scrollIntoViewIfNeeded();
    await expectInViewport(publicationFailure);
    await expectNoBodyOverflow(page);
  });

  for (const scenario of ["loading", "empty", "error"] as const) {
    test(`${scenario} state is explicit and contained`, async ({ page }) => {
      await openTab(page, "索引任务", scenario);
      await expectNoBodyOverflow(page);
      if (scenario === "loading") await expect(page.getByText("正在加载发布任务…")).toBeVisible();
      if (scenario === "empty") await expect(page.getByText("暂无发布任务")).toBeVisible();
      if (scenario === "error") await expect(page.getByText("合成加载失败")).toBeVisible();
    });
  }
});

test.describe("分类管理", () => {
  test("normal layout keeps form and category actions discoverable", async ({ page }) => {
    await openTab(page, "分类管理", "normal", "admin", { includeChildFolder: true });
    await expect(page.getByRole("heading", { name: "分类管理" })).toBeVisible();
    await expect(page.getByText("资料权限")).toHaveCount(0);
    await expect(page.getByText("3 个一级分类 · 共 4 个分类")).toBeVisible();
    await expect(page.getByRole("button", { name: "全部展开" })).toBeVisible();
    if (page.viewportSize()!.width >= 640) await expect(page.getByText("3 份 · 1 项")).toBeVisible();
    await expectNoBodyOverflow(page);
    const tree = page.getByRole("tree", { name: "分类层级" });
    await expect(tree).toHaveCSS("border-bottom-width", "1px");
    await expect(tree).toHaveCSS("border-bottom-style", "solid");
    const treeBox = await tree.boundingBox();
    const lastRootBox = await tree.locator(":scope > [role='treeitem']").last().boundingBox();
    expect(treeBox).not.toBeNull();
    expect(lastRootBox).not.toBeNull();
    expect(Math.abs(treeBox!.y + treeBox!.height - lastRootBox!.y - lastRootBox!.height)).toBeLessThanOrEqual(1);
    const createButton = page.getByRole("button", { name: "新增分类", exact: true });
    await createButton.scrollIntoViewIfNeeded();
    await expectInViewport(createButton);
    if (page.viewportSize()!.width < 1024) {
      await expectTouchTarget(createButton);
      await page.getByRole("treeitem", { name: /公司内部标准/ }).click();
      const editor = page.getByRole("dialog", { name: "公司内部标准" });
      await expect(editor).toBeVisible();
      const categoryToggle = editor.getByRole("radio", { name: "公司内部标准停用" });
      await categoryToggle.scrollIntoViewIfNeeded();
      await expectInViewport(categoryToggle);
      const save = editor.getByRole("button", { name: "保存修改" });
      await save.scrollIntoViewIfNeeded();
      await expectInViewport(save);
    } else {
      const parent = page.getByTestId("category-tree-item-cat-company");
      const child = page.getByTestId("category-tree-item-cat-company-modeling");
      await expect(parent).toHaveAttribute("aria-expanded", "false");
      await expect(child).toHaveCount(0);
      await parent.focus();
      await parent.press("ArrowRight");
      await expect(child).toBeVisible();
      await expect(page.getByRole("button", { name: "全部折叠" })).toBeVisible();
      await parent.press("ArrowRight");
      await expect(child).toBeFocused();
      await child.press("ArrowLeft");
      await expect(parent).toBeFocused();
      await expect(page.getByText("公司内部标准").last()).toBeVisible();
      await page.getByRole("button", { name: "调整结构" }).click();
      await expect(page.getByRole("button", { name: "完成调整" })).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByRole("button", { name: "拖动公司内部标准" })).toBeVisible();
      const save = page.getByRole("button", { name: "保存修改" });
      await expect(save).toBeDisabled();
      await expectInViewport(save);
    }
  });

  test("explicit move confirms the path and sends the concurrency contract", async ({ page }) => {
    await openTab(page, "分类管理", "normal", "admin", { includeChildFolder: true });
    await page.getByRole("treeitem", { name: /项目资料/ }).click();
    const editor = page.viewportSize()!.width < 1024
      ? page.getByRole("dialog", { name: "项目资料" })
      : page.locator("[aria-labelledby='category-list-title']").getByText("基本信息").locator("xpath=ancestor::div[contains(@class,'h-full')]");
    await expect(editor.getByRole("button", { name: "上移" })).toBeVisible();
    await expect(editor.getByRole("button", { name: "下移" })).toBeVisible();
    await editor.getByRole("button", { name: "移动至" }).click();
    const dialog = page.getByRole("dialog", { name: "移动分类" });
    await expect(dialog).toContainText("04 项目资料");
    await dialog.getByRole("combobox", { name: "目标父分类" }).selectOption("cat-company");
    await expect(dialog).toContainText("新路径：03 公司内部标准 / 04 项目资料");
    const moveRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/admin/content/categories/cat-project/move"));
    await dialog.getByRole("button", { name: "确认移动" }).click();
    const request = await moveRequest;
    expect(request.postDataJSON()).toEqual({ target_parent_id: "cat-company", before_category_id: null, expected_version: 2 });
  });

  for (const scenario of ["loading", "empty", "error"] as const) {
    test(`${scenario} state is explicit and contained`, async ({ page }) => {
      await openTab(page, "分类管理", scenario, "admin", { includeChildFolder: true });
      await expectNoBodyOverflow(page);
      if (scenario === "loading") await expect(page.getByRole("button", { name: "刷新" })).toBeDisabled();
      if (scenario === "empty") await expect(page.getByText("暂无分类")).toBeVisible();
      if (scenario === "error") await expect(page.getByText("合成加载失败")).toBeVisible();
    });
  }

  test("create exposes a stable busy state", async ({ page }) => {
    await openTab(page, "分类管理", "normal", "admin", { includeChildFolder: true });
    await page.getByRole("button", { name: "新增分类", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "新增分类" });
    await dialog.getByLabel("显示编号", { exact: true }).fill("C");
    await dialog.getByLabel("分类名称", { exact: true }).fill("合成新增分类");
    const create = dialog.getByRole("button", { name: "新增分类", exact: true });
    await create.click();
    await expect(dialog.getByRole("button", { name: "新增中…", exact: true })).toBeDisabled();
  });
});

test.describe("用户权限", () => {
  test("permissions are visible and editable from the user action menu", async ({ page }) => {
    await openTab(page, "用户管理");
    await expect(page.getByRole("heading", { name: "用户管理" })).toBeVisible();
    await expect(page.getByText("系统管理员 · 全部权限")).toBeVisible();
    await expect(page.getByText("自定义")).toBeVisible();
    await page.getByRole("button", { name: "管理 合成资料员" }).click();
    await page.getByRole("menuitem", { name: "设置权限" }).click();
    await expect(page.getByRole("dialog", { name: "设置资料权限" })).toBeVisible();
    await expectNoBodyOverflow(page);
    if (page.viewportSize()!.width === 390) {
      await expectInViewport(page.getByRole("button", { name: "保存权限" }));
    }
  });

  test("permission group management explains template semantics", async ({ page }) => {
    await openTab(page, "用户管理");
    await page.getByRole("button", { name: "权限组管理" }).click();
    await expect(page.getByRole("dialog", { name: "权限组管理" })).toContainText("修改模板不会改变既有用户权限");
    await expect(page.getByRole("dialog", { name: "权限组管理" }).getByRole("button", { name: "普通成员 预设" })).toBeVisible();
    await expectNoBodyOverflow(page);
    if (page.viewportSize()!.width === 390) {
      await expectInViewport(page.getByRole("button", { name: "保存模板" }));
    }
  });
});
