import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow } from "./helpers/layout";

test.describe("转录任务", () => {
  test("旧入口保留深链参数并进入资料管理子页", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/media?media_id=media-ready&workbench=1");
    await expect(page).toHaveURL(/\/admin\/content\?(?=[^#]*view=transcription)(?=[^#]*media_id=media-ready)(?=[^#]*workbench=1)/);
    await expect(page.locator("h1").filter({ hasText: "转录任务" })).toBeVisible();
    await expect(page.getByRole("dialog", { name: "项目交付培训" })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("视频独立阶段和恢复操作可见", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/content?view=transcription");
    await expect(page.getByRole("heading", { name: "转录任务" })).toBeVisible();
    await expect(page.getByText("项目交付培训", { exact: true })).toBeVisible();
    await expect(page.getByText("转录服务当前暂停接收任务，请稍后重试。", { exact: true })).toBeVisible();
    const firstRow = page.getByTestId("media-record-row").first();
    await expect(firstRow.getByRole("button", { name: "重试" })).toBeEnabled();
    await expect(firstRow.getByRole("button", { name: "重新转录" })).toBeDisabled();
    await expect(page.getByTestId("media-record-row").nth(1).getByRole("button", { name: "清理失败任务" })).toBeEnabled();
    await expectNoBodyOverflow(page);
  });

  test("无任务与再次失败的共享来源都按权威动作完成缓存收尾", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_stale_cleanup");
    await page.goto("/admin/content?view=transcription");

    const uploadedRow = page.getByText("共享视频遗留缓存", { exact: true }).locator("xpath=ancestor::li");
    await expect(uploadedRow).toBeVisible();
    await uploadedRow.getByRole("button", { name: "完成缓存清理" }).click();
    const dialog = page.getByRole("dialog", { name: "完成缓存清理" });
    await expect(dialog).toContainText("上次清理遗留的暂存缓存");
    await expect(dialog).toContainText("不会取消或修改当前转录任务");
    await expect(dialog).toContainText("不会删除共享目录原文件");
    await dialog.getByRole("button", { name: "取消" }).click();

    const failedRow = page.getByText("共享视频再次失败", { exact: true }).locator("xpath=ancestor::li");
    await expect(failedRow).toBeVisible();
    await failedRow.getByRole("button", { name: "完成缓存清理" }).click();
    await expect(dialog).toContainText("上次清理遗留的暂存缓存");
    await expect(dialog).not.toContainText("本地失败任务和派生缓存");
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`transcription-stale-cleanup-${viewport.width}x${viewport.height}.png`) });
  });

  test("共享来源重试显示后端结构化失败原因", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_stale_cleanup");
    await page.route("**/api/admin/transcription/media/media-stale-failed/retry", (route) => route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          code: "transcription_scheme_unavailable",
          message: "当前没有可用的转录方案，请先调整共享目录的默认转录方案。",
          retryable: false,
        },
      }),
    }));
    await page.goto("/admin/content?view=transcription");

    const failedRow = page.getByText("共享视频再次失败", { exact: true }).locator("xpath=ancestor::li");
    const retry = failedRow.getByRole("button", { name: "重试" });
    await expect(retry).toBeEnabled();
    await retry.click();

    const alert = page.getByRole("alert").filter({ hasText: "当前没有可用的转录方案" });
    await expect(alert).toContainText("当前没有可用的转录方案，请先调整共享目录的默认转录方案。");
    await expectInViewport(alert);
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({
      path: testInfo.outputPath(`transcription-shared-retry-error-${viewport.width}x${viewport.height}.png`),
    });
  });

  test("媒体空状态和错误状态可恢复", async ({ page }) => {
    await installAdminRoutes(page, "empty");
    await page.goto("/admin/content?view=transcription");
    await expect(page.getByText("暂无转录任务")).toBeVisible();
    await expectNoBodyOverflow(page);

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installAdminRoutes(page, "error");
    await page.goto("/admin/content?view=transcription");
    await expect(page.getByText("合成加载失败").first()).toBeVisible();
  });

  test("整段转录显示活动状态而不是虚假的零进度", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_progress");
    await page.goto("/admin/content?view=transcription");
    const row = page.getByTestId("media-record-row");

    await expect(row.getByText(/模型整段处理中/)).toContainText("视频时长 1小时20分");
    await expect(row.getByText(/模型整段处理中/)).not.toContainText("0%");
    await expect(row.getByRole("progressbar", { name: "转录进度：转录中" })).not.toHaveAttribute("aria-valuenow");
    await expect(row.getByRole("button", { name: "取消" })).toBeEnabled();
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`transcription-indeterminate-${viewport.width}x${viewport.height}.png`) });
  });

  test("永久失败任务保留禁用原因", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_permanent_failure");
    await page.goto("/admin/content?view=transcription");
    const retry = page.getByRole("button", { name: "重试" });
    await expect(retry).toBeDisabled();
    await expect(retry).toHaveAttribute("title", "仅可重试失败或已取消且允许恢复的转录任务");
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`transcription-permanent-failure-${viewport.width}x${viewport.height}.png`) });
  });

  test("转写工作台 Markdown 校对布局", async ({ page }, testInfo) => {
    await installAdminRoutes(page);
    await page.goto("/admin/content?view=transcription");
    const row = page.getByText("项目交付培训", { exact: true }).locator("xpath=ancestor::li");
    await row.getByRole("button", { name: "进入转写工作台" }).click();
    const workbench = page.getByRole("dialog", { name: "项目交付培训" });
    await expect(workbench).toBeVisible();
    await expect(workbench.getByText("5 个版本", { exact: true })).toBeVisible();
    await expect(workbench.getByRole("button", { name: "校对内容" })).toHaveCount(5);
    await workbench.getByRole("button", { name: "校对内容" }).first().click();
    await expect(workbench.getByRole("textbox", { name: "转录 Markdown 编辑器" })).toBeVisible();
    await expect(workbench.getByText("视频校对", { exact: true })).toBeVisible();
    await expect(workbench.getByRole("button", { name: "跳转到 00:00" })).toBeVisible();
    await expect(workbench.getByRole("region", { name: "当前版本校对工作区" })).toHaveCount(1);

    if (page.viewportSize()!.width >= 1024) {
      const videoBounds = await workbench.getByLabel("视频播放器").boundingBox();
      const transcriptBounds = await workbench.getByRole("region", { name: "视频转录稿" }).boundingBox();
      expect(videoBounds).not.toBeNull();
      expect(transcriptBounds).not.toBeNull();
      expect(Math.abs(videoBounds!.height - transcriptBounds!.height)).toBeLessThanOrEqual(2);
    }

    if (page.viewportSize()!.width < 768) {
      await workbench.getByRole("button", { name: "预览" }).click();
      await expect(workbench.getByRole("region", { name: "Markdown 预览" }).getByText("培训开始", { exact: true })).toBeVisible();
      await workbench.getByRole("button", { name: "编辑" }).click();
    } else {
      await expect(workbench.getByRole("region", { name: "Markdown 预览" }).getByText("培训开始", { exact: true })).toBeVisible();
    }

    await expect.poll(() => workbench.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expectInViewport(workbench);
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({
      path: testInfo.outputPath(`transcription-markdown-editor-normal-${viewport.width}x${viewport.height}.png`),
    });
  });
});
