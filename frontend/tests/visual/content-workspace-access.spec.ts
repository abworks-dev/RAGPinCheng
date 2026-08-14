import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("资料成员工作台入口", () => {
  async function openUserMenu(page: Parameters<typeof installAdminRoutes>[0], userName: RegExp) {
    if (page.viewportSize()!.width < 1024) {
      await page.getByRole("button", { name: "打开会话导航" }).click();
    }
    await page.getByRole("button", { name: userName }).click();
  }

  test("BIM工程师可从用户菜单进入且只看到资料模块", async ({ page }) => {
    await installAdminRoutes(page, "normal", "bim_engineer");
    await page.goto("/");
    await openUserMenu(page, /合成资料员/);
    const entry = page.getByRole("button", { name: "资料工作台" });
    await expect(entry).toBeVisible();
    await entry.click();

    await expect(page.getByText("资料工作台").first()).toBeVisible();
    if (page.viewportSize()!.width < 1024) {
      await page.getByRole("button", { name: "展开管理功能" }).click();
    }
    await expect(page.getByRole("button", { name: "资料库", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "用户", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "分类设置", exact: true })).toHaveCount(0);
    await expectNoBodyOverflow(page);
  });

  test("无资料权限成员不显示工作台入口", async ({ page }) => {
    await installAdminRoutes(page, "normal", "member");
    await page.goto("/");
    await openUserMenu(page, /合成成员/);
    await expect(page.getByRole("button", { name: /工作台/ })).toHaveCount(0);
    await expectNoBodyOverflow(page);
  });

  test("撤权后菜单立即移除入口且直接访问不闪现工作台", async ({ page }) => {
    let permissions = ["organize"];
    await installAdminRoutes(page, "normal", "bim_engineer", () => ({
      id: 9002,
      employee_id: "TEST-EDITOR",
      real_name: "合成资料员",
      role: "user",
      csrf_token: "synthetic-csrf-token",
      content_permissions: permissions,
    }));
    await page.goto("/");

    permissions = [];
    await openUserMenu(page, /合成资料员/);
    await expect(page.getByRole("button", { name: /工作台/ })).toHaveCount(0);
    await expectNoBodyOverflow(page);

    await page.goto("/admin");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("navigation", { name: "管理功能" })).toHaveCount(0);
    await expectNoBodyOverflow(page);
  });
});
