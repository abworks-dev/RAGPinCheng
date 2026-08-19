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
    await expect(page.getByRole("button", { name: "重试" })).toBeEnabled();
    await expect(page.getByTestId("media-record-row").nth(1).getByRole("button", { name: "完整删除" })).toBeEnabled();
    await expectNoBodyOverflow(page);
  });

  test("媒体空状态和错误状态可恢复", async ({ page }) => {
    await installAdminRoutes(page, "empty");
    await page.goto("/admin/content?view=transcription");
    await expect(page.getByText("暂无媒体资源")).toBeVisible();
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
