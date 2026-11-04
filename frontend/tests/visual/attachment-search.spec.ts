import { expect, test } from "@playwright/test";
import { installChatRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";
const file = { item_id: "synthetic-item", version_id: "synthetic-version", title: "合成长中文附件名称与项目图纸".repeat(3), original_filename: "synthetic-long-file-name.png", doc_type: "png", file_size: 1024, category_key: "internal-key", category_path: "合成分类 / " + "长分类名称".repeat(8), version_number: 2, matched_fields: ["description", "ocr_text"], snippet: "合成 OCR 文字，仅供文件匹配，不用于正文知识问答。", preview_kind: "image", can_preview: true, can_download: false };
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aMZkAAAAASUVORK5CYII=", "base64");
for (const failure of [false, true]) test(`chat-only attachment cards dedicated preview ${failure ? "error" : "success"}`, async ({ page }, testInfo) => {
  await installChatRoutes(page);
  const adminRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("/api/admin/content/")) adminRequests.push(request.url()); });
  await page.route("**/api/conversations/conversation-chat/chat", route => route.fulfill({ contentType: "text/event-stream", body: [
    `event: prep\ndata: ${JSON.stringify({ used_sources: [], file_results: [file], search_query: "合成附件", history_chars: 0, budget: 0, fresh_count: 0, final_count: 0 })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ answer_text: "找到相关附件。", sources: [], file_results: [file], timings: {}, history_chars: 0, budget: 0 })}\n\n`,
  ].join("") }));
  await page.route("**/api/attachments/versions/synthetic-version/info", route => route.fulfill({ json: { ...file, rag_enabled: false, preview_status: "ready", is_current: true } }));
  await page.route("**/api/attachments/versions/synthetic-version/preview", route => failure ? route.fulfill({ status: 403, json: { detail: "附件已不可见" } }) : route.fulfill({ contentType: "image/png", body: png }));
  await page.goto("/"); await page.getByPlaceholder("向企业知识库提问").fill("合成附件"); await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已找到 1 个附件，仅提供文件匹配" })).toBeVisible();
  await expect(page.getByText(/未检索到可用资料/)).toHaveCount(0);
  const cards = page.getByRole("region", { name: "找到的附件" });
  await expect(cards.getByText("匹配：描述、OCR 文字")).toBeVisible();
  await expect(cards.getByRole("button", { name: "下载原文件" })).toBeDisabled();
  await expect(page.getByRole("button", { name: /查看.*个来源/ })).toHaveCount(0);
  await expect(cards).not.toContainText("internal-key");
  await cards.getByRole("button", { name: "预览附件" }).focus(); await expect(cards.getByRole("button", { name: "预览附件" })).toBeFocused();
  await expectNoBodyOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`attachment-cards-${page.viewportSize()!.width}x${page.viewportSize()!.height}.png`) });
  await cards.getByRole("button", { name: "预览附件" }).click();
  const dialog = page.getByRole("dialog", { name: file.title, exact: true });
  if (failure) { await expect(dialog.getByRole("alert")).toBeVisible(); await expect(dialog.getByRole("button", { name: "重试预览" })).toBeEnabled(); }
  else await expect(dialog.getByText("1 × 1 像素")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "关联预览" })).toHaveCount(0);
  await expect(dialog.getByText("文件搜索信息与原图文字识别", { exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "下载原文件", exact: true })).toBeDisabled();
  expect(adminRequests).toEqual([]); await expectNoBodyOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`attachment-chat-preview-${failure ? "error" : "success"}-${page.viewportSize()!.width}x${page.viewportSize()!.height}.png`) });
  await dialog.getByRole("button", { name: "关闭预览" }).click(); await expect(cards.getByRole("button", { name: "预览附件" })).toBeFocused();
});
