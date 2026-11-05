import { expect, test } from "@playwright/test";
import { installAuthRoutes, installChatRoutes } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow } from "./helpers/layout";

for (const kind of ["login", "register"] as const) {
  test(`${kind} layout, keyboard focus and pending recovery`, async ({ page }, testInfo) => {
    await installAuthRoutes(page);
    let finishRequest!: () => void;
    const pending = new Promise<void>((resolve) => { finishRequest = resolve; });
    await page.route(`**/api/auth/${kind}`, async (route) => {
      await pending;
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "合成校验失败，请重试" }) });
    });
    await page.goto(`/${kind}`);
    const username = page.getByLabel(kind === "login" ? "用户名" : "用户名（登录用，唯一）", { exact: true });
    const submit = page.getByRole("button", { name: kind === "login" ? "登录" : "注册", exact: true });
    await expect(username).toBeFocused();
    await expectInViewport(submit);
    await expectNoBodyOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`${kind}-normal.png`), fullPage: true });
    await username.fill("synthetic-layout-user");
    if (kind === "register") await page.getByLabel("真实姓名", { exact: true }).fill("合成用户");
    await page.getByLabel(kind === "login" ? "密码" : "密码（至少 6 位）", { exact: true }).fill("synthetic-secret");
    if (kind === "register") await page.getByLabel("确认密码", { exact: true }).fill("synthetic-secret");
    await submit.focus();
    await expect(submit).toBeFocused();
    await expect(submit).toHaveCSS("outline-style", "solid");
    await submit.press("Enter");
    const busy = page.getByRole("button", { name: kind === "login" ? "登录中…" : "注册中…" });
    await expect(busy).toBeDisabled();
    await expectInViewport(busy);
    await expectNoBodyOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`${kind}-busy.png`), fullPage: true });
    finishRequest();
    await expect(page.getByRole("alert")).toContainText("合成校验失败，请重试");
    await expect(submit).toBeEnabled();
    await expectNoBodyOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`${kind}-error.png`), fullPage: true });
  });
}

test("chat empty keeps composer visible and accepts a question", async ({ page }, testInfo) => {
  await installChatRoutes(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "从企业知识中找到可靠答案" })).toBeVisible();
  const composer = page.getByPlaceholder("向企业知识库提问");
  await expectInViewport(composer);
  await expectNoBodyOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("chat-empty.png"), fullPage: true });
  await composer.fill("合成问题");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.getByText("合成回答", { exact: true })).toBeVisible();
  await expect(composer).toBeEnabled();
  await expectNoBodyOverflow(page);
});
