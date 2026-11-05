import { expect, test } from "@playwright/test";
import { installAdminRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

// Review artifacts, not automatically accepted pixel baselines.
const routes = [
  ["overview", "系统概览"], ["content", "资料管理"],
  ["categories", "分类管理"], ["users", "用户管理"],
  ["conversations", "对话记录"], ["feedback", "用户反馈"],
  ["maintenance", "系统维护"], ["answer-policy", "回答策略"],
  ["asr", "转录配置"], ["content?view=index", "资料管理"],
  ["content?view=transcription", "资料管理"],
] as const;

for (const theme of ["light", "dark"] as const) for (const [route, heading] of routes) {
  test(`refreshed admin workspace ${theme}: ${route}`, async ({ page }, testInfo) => {
    await page.addInitScript((value) => {
      localStorage.setItem("pincheng-theme", value);
      document.documentElement.classList.toggle("dark", value === "dark");
    }, theme);
    await installAdminRoutes(page, "normal");
    // This page has its own API contract; keep its synthetic read fixtures scoped here.
    await page.route(/\/api\/admin\/answer-policy(?:\/audit\?limit=\d+)?$/, async (request) => {
      if (request.request().method() !== "GET") return request.fallback();
      await request.fulfill({ json: request.request().url().includes("/audit") ? { entries: [] } : {
        answer_temperature: 0.2, answer_max_output_tokens: 1200, answer_context_chars: 6000,
        relevance_gate_enabled: false, relevance_min_score: 0, relevance_min_rrf: 0,
        relevance_min_margin: 0, policy_version: "env-default-v1", updated_at: null, updated_by: null,
      } });
    });
    await page.goto(`/admin/${route}`);
    await expect(page.getByRole("heading", { level: 1, name: heading, exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByRole("main")).toHaveAttribute("id", "admin-main");
    await expect(page.getByRole("navigation", { name: "当前位置" })).toContainText(heading);
    await expect(page.getByRole("link", { name: "返回知识问答", exact: true })).toBeVisible();
    await expectNoBodyOverflow(page);
    if (route === "content?view=index") {
      for (const action of [
        page.getByRole("link", { name: "查看文件", exact: true }).first(),
        page.getByRole("button", { name: "重新发布", exact: true }).first(),
      ]) {
        await expect(action).toBeVisible();
        const bounds = await action.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
      }
    }
    const skipLink = page.getByRole("link", { name: "跳至工作区" });
    await expect(skipLink).toHaveCSS("clip-path", "inset(50%)");
    await page.keyboard.press("Tab");
    await expect(skipLink).toBeFocused();
    await expect(skipLink).toHaveCSS("clip-path", "none");
    await expect(skipLink).toHaveCSS("top", "16px");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();
    await expect(skipLink).toHaveCSS("clip-path", "inset(50%)");
    await page.getByRole("button", { name: "账户：合成管理员", exact: true }).click();
    await expect(page.getByRole("button", { name: "返回对话", exact: true })).toBeVisible();
    await expect(skipLink).toHaveCSS("clip-path", "inset(50%)");
    await page.keyboard.press("Escape");
    await page.getByRole("main").focus();
    await testInfo.attach(`admin-${route.replace(/[?=]/g, "-")}`, {
      body: await page.screenshot({ fullPage: true }), contentType: "image/png",
    });
  });
}
