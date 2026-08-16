import { expect, test } from "@playwright/test";
import { installChatRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

test.describe("聊天工作台", () => {
  test("可以提交合成问题并看到 SSE 完成态", async ({ page }) => {
    await installChatRoutes(page);
    await page.goto("/");
    const composer = page.getByPlaceholder("向企业知识库提问");
    await expect(composer).toBeVisible();
    await composer.fill("合成问题");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByText("合成回答", { exact: true })).toBeVisible();
    await expect(composer).toBeEnabled();
    await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
    await expectNoBodyOverflow(page);
  });

  test("SSE 错误保留在当前对话中", async ({ page }) => {
    await installChatRoutes(page, "error");
    await page.goto("/");
    await page.getByPlaceholder("向企业知识库提问").fill("合成失败问题");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByText("合成回答失败", { exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
  });
});
