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
    await page.getByRole("button", { name: "上传视频" }).click();
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

  test("上传完成态只保留完成并关闭主操作", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_upload");
    await page.goto("/admin/media");
    await page.getByRole("button", { name: "上传视频" }).click();
    await page.getByLabel("选择视频文件").setInputFiles({
      name: "completed-upload.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(128 * 1024, 1),
    });
    await page.getByRole("button", { name: "下一步：选择转写方式" }).click();
    await page.getByRole("button", { name: /^自动转录/ }).click();
    await page.getByRole("button", { name: "上传并创建自动转录任务" }).click();

    await expect(page.getByText("已提交", { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "完成并关闭" })).toBeVisible();
    await expect(page.getByRole("button", { name: "放弃本次上传" })).toHaveCount(0);
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`media-upload-complete-${viewport.width}x${viewport.height}.png`) });
  });

  test("关闭未完成上传时可选择保留或放弃", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_upload");
    await page.goto("/admin/media");
    await page.getByRole("button", { name: "上传视频" }).click();
    await page.getByLabel("选择视频文件").setInputFiles({
      name: "unfinished-close.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(128, 1),
    });
    await page.getByRole("button", { name: "关闭" }).click();

    const prompt = page.getByRole("dialog", { name: "暂时关闭上传流程？" });
    await expect(prompt).toBeVisible();
    await expect(prompt.getByRole("button", { name: "继续操作" })).toBeVisible();
    await expect(prompt.getByRole("button", { name: "关闭并放弃" })).toBeVisible();
    await expect(prompt.getByRole("button", { name: "关闭并保留" })).toBeVisible();
    const heights = await Promise.all([
      prompt.getByRole("button", { name: "继续操作" }),
      prompt.getByRole("button", { name: "关闭并放弃" }),
      prompt.getByRole("button", { name: "关闭并保留" }),
    ].map(async (button) => (await button.boundingBox())?.height));
    expect(new Set(heights).size).toBe(1);
    await expectInViewport(prompt);
    await expectNoBodyOverflow(page);

    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`media-upload-close-options-${viewport.width}x${viewport.height}.png`) });
  });

  test("上传配置控件等高且底部操作栏不随内容滚动", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_upload");
    await page.goto("/admin/media");
    await page.getByRole("button", { name: "上传视频" }).click();
    await page.getByLabel("选择视频文件").setInputFiles([
      { name: "layout-one.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(128, 1) },
      { name: "layout-two.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(128, 2) },
      { name: "layout-three.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(128, 3) },
    ]);
    await page.getByRole("button", { name: "下一步：选择转写方式" }).click();
    await page.getByRole("button", { name: /^自动转录/ }).click();

    const controls = [
      page.getByRole("button", { name: "全选", exact: true }),
      page.getByRole("button", { name: "取消全选" }),
      page.getByLabel("批量转录方案"),
      page.getByRole("button", { name: "应用到已选择视频" }),
      page.getByRole("button", { name: "返回选择方式" }),
      page.getByRole("button", { name: "放弃本次上传" }),
      page.getByRole("button", { name: "上传并创建自动转录任务" }),
    ];
    const heights = await Promise.all(controls.map(async (control) => (await control.boundingBox())?.height));
    expect(new Set(heights).size).toBe(1);
    const viewport = page.viewportSize()!;
    if (viewport.width >= 640) {
      const schemeBounds = await page.getByLabel("批量转录方案").boundingBox();
      const applyBounds = await page.getByRole("button", { name: "应用到已选择视频" }).boundingBox();
      expect(schemeBounds).not.toBeNull();
      expect(applyBounds).not.toBeNull();
      expect(Math.abs((schemeBounds!.y + schemeBounds!.height) - (applyBounds!.y + applyBounds!.height))).toBeLessThanOrEqual(1);
    }
    await page.screenshot({ path: testInfo.outputPath(`media-upload-controls-${viewport.width}x${viewport.height}.png`) });

    const actionBar = page.getByTestId("media-upload-action-bar");
    const scrollRegion = page.getByTestId("media-upload-scroll-region");
    const before = await actionBar.boundingBox();
    await scrollRegion.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    await expect.poll(() => scrollRegion.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    const after = await actionBar.boundingBox();
    expect(before).not.toBeNull();
    expect(after).not.toBeNull();
    expect(after!.y).toBe(before!.y);
    expect(after!.height).toBe(before!.height);
    await expectInViewport(actionBar);
    await expectNoBodyOverflow(page);

    await page.screenshot({ path: testInfo.outputPath(`media-upload-fixed-actions-${viewport.width}x${viewport.height}.png`) });
  });

  test("同名视频可逐项选择重命名且窗口保持可读", async ({ page }, testInfo) => {
    await installAdminRoutes(page, "media_conflict");
    await page.goto("/admin/media");
    await page.getByRole("button", { name: "上传视频" }).click();
    await page.getByLabel("选择视频文件").setInputFiles({
      name: "same-training.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(128, 1),
    });
    await page.getByRole("button", { name: "下一步：选择转写方式" }).click();
    await page.getByRole("button", { name: /^自动转录/ }).click();
    await page.getByRole("button", { name: "上传并创建自动转录任务" }).click();

    await expect(page.getByText("发现同名资料")).toBeVisible();
    const strategy = page.getByText("处理方式").locator("..").locator("select");
    await strategy.selectOption("rename");
    await expect(page.getByText("新资料标题")).toBeVisible();
    await expect(page.getByText("新源文件名")).toBeVisible();
    await expect(page.getByRole("button", { name: "按选择上传" })).toBeVisible();
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    await page.screenshot({ path: testInfo.outputPath(`media-upload-conflict-${viewport.width}x${viewport.height}.png`) });
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
