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
  await page.getByRole("link", { name: label, exact: true }).click();
}

async function openRootFolder(page: Parameters<typeof installAdminRoutes>[0], folderId = "cat-company") {
  const listing = page.waitForRequest((request) => request.method() === "GET" && request.url().includes("/api/admin/content/items-page") && request.url().includes(`category_id=${folderId}`));
  const folderName = folderId === "cat-project" ? "04 项目资料" : "03 公司内部标准";
  await page.getByRole("button", { name: new RegExp(folderName) }).click();
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
    await page.getByRole("button", { name: /04 项目资料/ }).click();
    await switchedListing;
    await expect(page.getByText(/当前目录：04 项目资料/)).toBeVisible();
    await page.getByRole("button", { name: "上传文件" }).scrollIntoViewIfNeeded();
    await expectInViewport(page.getByRole("button", { name: "上传文件" }));
    if (page.viewportSize()!.width === 390) await expectTouchTarget(page.getByRole("button", { name: "上传文件" }));
    await expect(page.getByRole("combobox", { name: "状态", exact: true })).toHaveValue("");
    await expect(page.getByText("未选择资料，单次最多 20 份")).toBeVisible();
    await expect(page.getByRole("button", { name: "批量确认" })).toBeDisabled();
    const toolbarHeightBeforeSelection = await page.getByTestId("managed-bulk-toolbar").evaluate((element) => element.getBoundingClientRect().height);
    const mobile = page.viewportSize()!.width < 1024;
    const visibleItem = mobile
      ? page.locator("li").getByText("机电专业协同检查清单", { exact: true })
      : page.getByRole("table").getByText("机电专业协同检查清单", { exact: true });
    await visibleItem.scrollIntoViewIfNeeded();
    await expect(visibleItem).toBeVisible();
    if (page.viewportSize()!.width === 768) {
      await expect(page.getByRole("table")).toBeHidden();
      await expect(page.getByRole("button", { name: "确认", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "退回", exact: true })).toBeVisible();
    }
    if (page.viewportSize()!.width === 390) {
      await expectInViewport(page.getByRole("button", { name: "确认", exact: true }).first());
    }
    const itemCheckbox = mobile
      ? page.locator("li").getByRole("checkbox", { name: "选择机电专业协同检查清单" })
      : page.getByRole("table").getByRole("checkbox", { name: "选择机电专业协同检查清单" });
    await itemCheckbox.check();
    await expect(page.getByText(/已选择\s*1\s*份，单次最多\s*20\s*份/)).toBeVisible();
    await expect(page.getByRole("button", { name: "批量确认" })).toBeEnabled();
    const toolbarHeightAfterSelection = await page.getByTestId("managed-bulk-toolbar").evaluate((element) => element.getBoundingClientRect().height);
    expect(Math.abs(toolbarHeightAfterSelection - toolbarHeightBeforeSelection)).toBeLessThanOrEqual(1);
  });

  for (const scenario of ["loading", "empty", "error", "disabled"] as const) {
    test(`${scenario} state is explicit and contained`, async ({ page }) => {
      await openTab(page, "资料管理", scenario);
      await expectNoBodyOverflow(page);
      if (scenario === "loading") await expect(page.getByRole("heading", { name: "资料管理" })).toBeVisible();
      if (scenario === "empty") await expect(page.getByText("没有符合条件的资料")).toBeVisible();
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

  test("dropping local files on the current folder requires confirmation", async ({ page }) => {
    await openTab(page, "资料管理");
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
    const row = page.getByTitle("拖动到上方文件夹可移动资料").first();
    const folder = page.getByRole("button", { name: /01 建模标准/ });
    await expect(row).toBeVisible();
    await expect(folder).toBeVisible();
    await row.dispatchEvent("dragstart");
    await expect(folder).toHaveClass(/border-primary/);
    const requestPromise = page.waitForRequest((request) => request.method() === "POST" && request.url().includes("/move"));
    await folder.dispatchEvent("dragover");
    await folder.dispatchEvent("drop");
    await requestPromise;
  });

  test("publication failure stays readable and actionable", async ({ page }) => {
    await openTab(page, "资料管理", "publication_failure");
    await openRootFolder(page);
    await page.locator("select").filter({ has: page.locator('option[value="publication_failed"]') }).selectOption("publication_failed");
    await expect(page.locator("p:visible", { hasText: "PDF 需要密码才能解析。" })).toBeVisible();
    await expect(page.locator("p:visible", { hasText: "请上传已解除密码保护的 PDF。" })).toBeVisible();
    await expect(page.locator("p:visible", { hasText: "共尝试 4 次" })).toBeVisible();
    const failedTitle = page.getByText("培训资料发布演练", { exact: true }).filter({ visible: true });
    const failedItem = page.viewportSize()!.width < 1024 ? failedTitle.locator("xpath=ancestor::li") : failedTitle.locator("xpath=ancestor::tr");
    const republish = failedItem.getByRole("button", { name: "重新发布" });
    await expect(republish).toBeEnabled();
    await republish.click();
    await expect(failedItem.getByRole("button", { name: "发布中…" })).toBeDisabled();
    await expectNoBodyOverflow(page);
  });

  test("indexed files return from the shared preview sheet to their detail dialog", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const title = page.getByText("建筑信息模型交付标准（合成长文件名用于响应式检查）", { exact: true }).filter({ visible: true });
    const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
    await item.getByRole("button", { name: "查看", exact: true }).click();

    const detail = page.getByRole("dialog").filter({ has: page.getByRole("button", { name: "预览文件" }) });
    await expect(detail).toBeVisible();
    await detail.getByRole("button", { name: "预览文件" }).click();
    await expect(page.getByRole("button", { name: "返回资料详情" })).toBeVisible();
    await expect(page.getByRole("button", { name: "关闭预览" })).toBeVisible();
    await expect(detail).toBeHidden();
    await expectNoBodyOverflow(page);

    await page.getByRole("button", { name: "返回资料详情" }).click();
    await expect(detail).toBeVisible();
    await expect(detail.getByRole("link", { name: "下载" })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("move-to-trash confirmation explains impact and exposes a stable busy state", async ({ page }) => {
    await openTab(page, "资料管理");
    await openRootFolder(page);
    const title = page.getByText("建筑信息模型交付标准（合成长文件名用于响应式检查）", { exact: true }).filter({ visible: true });
    const item = page.viewportSize()!.width < 1024 ? title.locator("xpath=ancestor::li") : title.locator("xpath=ancestor::tr");
    const remove = item.getByRole("button", { name: "移至回收站", exact: true });
    await remove.scrollIntoViewIfNeeded();
    await expectInViewport(remove);
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "移至回收站" });
    await expect(dialog).toContainText("将从资料列表和知识库检索中移除");
    await expect(dialog).toContainText("文件、版本及审核发布历史会保留");
    await expectNoBodyOverflow(page);
    const confirm = dialog.getByRole("button", { name: "确认移入" });
    await confirm.click();
    await expect(dialog.getByRole("button", { name: "处理中…" })).toBeDisabled();
  });

  test("trash exposes archive context and a stable restore flow", async ({ page }) => {
    await openTab(page, "资料管理");
    await page.getByRole("tab", { name: "回收站" }).click();
    await expect(page.getByRole("heading", { name: "回收站", exact: true })).toBeVisible();
    await expect(page.getByText(/合成资料员 于/)).toBeVisible();
    await expect(page.getByText("原状态：已发布")).toBeVisible();
    await expectNoBodyOverflow(page);
    const restore = page.getByRole("button", { name: "恢复", exact: true });
    await restore.scrollIntoViewIfNeeded();
    await expectInViewport(restore);
    const restoreRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/items/item-5/restore"));
    await restore.click();
    const dialog = page.getByRole("dialog", { name: "恢复资料" });
    await expect(dialog).toContainText("需要管理员重新发布后才会进入检索");
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
    await expect(workbench.getByText("自动转录")).toBeVisible();
    await expect(workbench.getByRole("textbox", { name: /审核备注/ })).toBeVisible();
    await expect(workbench.getByText("审核通过后可发布")).toBeVisible();
    await expect(workbench.getByText("synthetic-asr")).toBeHidden();
    await workbench.getByRole("button", { name: "预览 Markdown" }).click();
    await expect(workbench.locator("pre")).toContainText("培训开始");
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
    await expectNoBodyOverflow(page);
    const createButton = page.getByRole("button", { name: "新增分类", exact: true });
    await createButton.scrollIntoViewIfNeeded();
    await expectInViewport(createButton);
    if (page.viewportSize()!.width < 1024) {
      await expectTouchTarget(createButton);
      await page.getByRole("treeitem", { name: /公司内部标准/ }).click();
      const editor = page.getByRole("dialog", { name: "公司内部标准" });
      await expect(editor).toBeVisible();
      const categoryToggle = editor.getByRole("checkbox", { name: "公司内部标准启用" });
      await categoryToggle.scrollIntoViewIfNeeded();
      await expectInViewport(categoryToggle);
      const save = editor.getByRole("button", { name: "保存修改" });
      await save.scrollIntoViewIfNeeded();
      await expectInViewport(save);
    } else {
      const parent = page.getByTestId("category-tree-item-cat-company");
      const child = page.getByTestId("category-tree-item-cat-company-modeling");
      await parent.focus();
      await parent.press("ArrowRight");
      await expect(child).toBeFocused();
      await child.press("ArrowLeft");
      await expect(parent).toBeFocused();
      await expect(page.getByText("公司内部标准").last()).toBeVisible();
      await expect(page.getByRole("button", { name: "保存修改" })).toBeDisabled();
    }
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
  });
});
