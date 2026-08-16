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

  test("转写工作台 Markdown 校对布局", async ({ page }, testInfo) => {
    await installAdminRoutes(page);
    await page.goto("/admin/media");
    const row = page.getByText("项目交付培训", { exact: true }).locator("xpath=ancestor::li");
    await row.getByRole("button", { name: "进入转写工作台" }).click();
    const workbench = page.getByRole("dialog", { name: "项目交付培训" });
    await expect(workbench).toBeVisible();
    await workbench.getByRole("button", { name: "校对内容" }).click();
    await expect(workbench.getByRole("textbox", { name: "转录 Markdown 编辑器" })).toBeVisible();

    if (page.viewportSize()!.width < 768) {
      await workbench.getByRole("button", { name: "预览" }).click();
      await expect(workbench.getByText("培训开始", { exact: true })).toBeVisible();
      await workbench.getByRole("button", { name: "编辑" }).click();
    } else {
      await expect(workbench.getByText("培训开始", { exact: true })).toBeVisible();
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
