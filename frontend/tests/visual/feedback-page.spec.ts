import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("反馈管理", () => {
  test("反馈筛选和处理入口可见", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/feedback");
    await expect(page.getByRole("heading", { name: "用户反馈" })).toBeVisible();
    await expect(page.getByText("合成反馈：回答需要补充来源", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "开始处理" }).first()).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("反馈加载失败保持错误入口", async ({ page }) => {
    await installAdminRoutes(page, "error");
    await page.goto("/admin/feedback");
    await expect(page.getByText("合成加载失败")).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
