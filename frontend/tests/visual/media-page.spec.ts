import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

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
});
