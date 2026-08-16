import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("管理员对话", () => {
  test("列表和共享只读详情可见", async ({ page }) => {
    await installAdminRoutes(page);
    await page.goto("/admin/conversations");
    await expect(page.getByRole("heading", { name: "对话记录" })).toBeVisible();
    await page.getByRole("button", { name: /合成项目交付规范/ }).click();
    await expect(page.getByText("合成回答：请按项目交付清单逐项核对。", { exact: true }).last()).toBeVisible();
    await page.getByText("查看编辑记录（2 个版本）").click();
    await expect(page.getByText("问题版本 1")).toBeVisible();
    await expectNoBodyOverflow(page);
  });

  test("空状态和错误状态明确", async ({ page }) => {
    await installAdminRoutes(page, "empty");
    await page.goto("/admin/conversations");
    await expect(page.getByText("暂无对话")).toBeVisible();

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installAdminRoutes(page, "error");
    await page.goto("/admin/conversations");
    await expect(page.getByText("合成加载失败")).toBeVisible();
  });
});
