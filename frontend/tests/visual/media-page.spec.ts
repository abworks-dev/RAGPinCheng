import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow } from "./helpers/layout";

test.describe("视频媒体", () => {
  test("媒体列表和转录任务状态可见", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/media");
    await expect(page.getByRole("heading", { name: "视频管理" })).toBeVisible();
    await expect(page.getByText("项目交付培训", { exact: true })).toBeVisible();
    await expect(page.getByText("转录服务当前暂停接收任务，请稍后重试。", { exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("媒体空状态和错误状态可恢复", async ({ page }) => {
    await installAdminRoutes(page, "empty");
    await page.goto("/admin/media");
    await expect(page.getByText("暂无媒体资源")).toBeVisible();
    await expectNoBodyOverflow(page);

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installAdminRoutes(page, "error");
    await page.goto("/admin/media");
    await expect(page.getByText("合成加载失败").first()).toBeVisible();
  });

  test("整段转录显示活动状态而不是虚假的零进度", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_progress");
    await page.goto("/admin/media");
    const row = page.getByTestId("media-record-row");

    await expect(row.getByText(/模型整段处理中/)).toContainText("视频时长 1小时20分");
    await expect(row.getByText(/模型整段处理中/)).not.toContainText("0%");
    await expect(row.getByRole("progressbar", { name: "转录进度：转录中" })).not.toHaveAttribute("aria-valuenow");
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`transcription-indeterminate-${viewport.width}x${viewport.height}.png`) });
  });

  test("文件发送完成后明确显示服务端准备阶段", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      class ControlledUploadRequest {
        upload = {
          onprogress: null as ((event: ProgressEvent) => void) | null,
          onload: null as ((event: ProgressEvent) => void) | null,
        };
        status = 200;
        statusText = "OK";
        responseText = JSON.stringify({ media_id: "media-uploaded", transcription_job_id: "job-uploaded" });
        withCredentials = false;
        onload: ((event: ProgressEvent) => void) | null = null;
        onerror: ((event: ProgressEvent) => void) | null = null;
        onabort: ((event: ProgressEvent) => void) | null = null;

        open() {}
        setRequestHeader() {}
        send() {
          window.setTimeout(() => this.upload.onprogress?.(new ProgressEvent("progress", {
            lengthComputable: true,
            loaded: 100,
            total: 100,
          })), 50);
          window.setTimeout(() => this.upload.onload?.(new ProgressEvent("load")), 100);
          window.setTimeout(() => this.onload?.(new ProgressEvent("load")), 10_000);
        }
      }
      Object.defineProperty(window, "XMLHttpRequest", { value: ControlledUploadRequest });
    });
    await installAdminRoutes(page, "media_upload");
    await page.goto("/admin/media");
    await page.getByLabel("选择视频文件").setInputFiles({
      name: "upload-progress.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(256 * 1024, 1),
    });
    await page.getByRole("button", { name: "下一步：选择转写方式" }).click();
    await page.getByRole("button", { name: /^自动转录/ }).click();
    await page.getByRole("button", { name: "上传并创建自动转录任务" }).click();

    await expect(page.getByText("文件已上传，正在准备音轨并创建转录任务")).toBeVisible();
    await expect(page.getByRole("progressbar", { name: "upload-progress.mp4 上传进度" })).not.toHaveAttribute("aria-valuenow");
    await expect(page.getByText(/服务端处理 1 个/)).toBeVisible();
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`media-upload-preparing-${viewport.width}x${viewport.height}.png`) });
  });

  test("转写工作台 Markdown 校对布局", async ({ page }, testInfo) => {
    await installAdminRoutes(page);
    await page.goto("/admin/media");
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
