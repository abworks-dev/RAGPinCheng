import { expect, test } from "@playwright/test";
import { installAuthRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("登录与注册", () => {
  test("登录失败保持表单可恢复", async ({ page }) => {
    await installAuthRoutes(page);
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "登录 · 品成 BIM 知识库" })).toBeVisible();
    await page.getByLabel("用户名", { exact: true }).fill("synthetic-user");
    await page.getByLabel("密码", { exact: true }).fill("wrong-password");
    await page.getByRole("button", { name: "登录" }).click();
    await expect(page.getByRole("alert")).toContainText("合成用户名或密码错误");
    await expect(page.getByRole("button", { name: "登录" })).toBeEnabled();
    await expectNoBodyOverflow(page);
  });

  test("注册密码校验在网络请求前阻断提交", async ({ page }) => {
    await installAuthRoutes(page);
    await page.goto("/register");
    await page.getByLabel("用户名（登录用，唯一）", { exact: true }).fill("synthetic-user");
    await page.getByLabel("真实姓名", { exact: true }).fill("合成用户");
    await page.getByLabel("密码（至少 6 位）", { exact: true }).fill("secret1");
    await page.getByLabel("确认密码", { exact: true }).fill("different1");
    await page.getByRole("button", { name: "注册" }).click();
    await expect(page.getByRole("alert")).toContainText("两次输入的密码不一致");
    await expect(page.getByRole("button", { name: "注册" })).toBeEnabled();
    await expectNoBodyOverflow(page);
  });
});
