import { expect, test } from "@playwright/test";
import { installChatRoutes } from "./fixtures/admin-fixtures";
import { expectInViewport, expectNoBodyOverflow } from "./helpers/layout";

// Synthetic enterprise content only. Exercise the same SSE and citation consumers
// as a real conversation without sending any document or question to a backend.
const answer = `## 模型交付检查建议

交付前先核对项目约定，再检查模型与清单的一致性。以下为合成演示资料，不作为实际规范依据。[1]

| 检查对象 | 核对内容 | 处理方式 |
| --- | --- | --- |
| 模型命名 | 专业、楼层、阶段 | 按项目约定统一 |
| 交付目录 | 模型、清单、说明 | 保留可追溯版本 |
| 超长字段 | SYNTHETIC_MODEL_DELIVERY_COORDINATION_DISCIPLINE_BUILDING_LEVEL_REVISION | 在表格内查看 |

### 执行顺序

1. 核对交付清单与模型版本。
2. 记录差异，指定复核责任人。

> 最终以项目正式交付要求为准。

\`\`\`json
{"model": "SYNTHETIC_MODEL_DELIVERY_LONG_IDENTIFIER_FOR_LOCAL_SCROLL_ONLY", "revision": "P03"}
\`\`\`

完成检查后归档问题记录，并在正式交付前复核。`;
const sources = [{
  parent_id: "document-parent", doc_title: "项目交付检查清单", section_path: "交付检查 > 模型命名",
  category: "公司内部标准", company: "合成设计咨询企业", score: 0.93, rrf_score: 0.89,
  text: "模型名称应包含专业、楼层和交付阶段。交付负责人应核对模型版本、问题记录和交付目录，并保留复核意见。",
  doc_type: "docx", start_time: null, media_id: null, paragraph_anchor: "模型命名",
}];

for (const theme of ["light", "dark"] as const) {
  test(`完整回答、表格、代码与来源工作区 · ${theme}`, async ({ page }, testInfo) => {
    await page.addInitScript((value) => localStorage.setItem("pincheng-theme", value), theme);
    await installChatRoutes(page, "document");
    await page.route("**/api/conversations/conversation-chat/chat", async (route) => {
      const payload = route.request().postDataJSON();
      const text = payload.edit_user_message_id ? "已按修改后的问题重新检查。[1]" : answer;
      const events = [
        ["prep", { search_query: "合成模型交付", used_sources: sources, final_count: 1, fresh_count: 1, no_source_fallback: false }],
        ["token", { text }],
        ["done", { answer_text: text, assistant_message_id: 503, sources, timings: {}, history_chars: 0, budget: 1000 }],
      ].map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: events });
    });
    await page.goto("/");
    if (theme === "dark") await expect(page.locator("html")).toHaveClass(/dark/);
    else await expect(page.locator("html")).not.toHaveClass(/dark/);
    const composer = page.getByRole("textbox", { name: "向企业知识库提问" });
    await composer.fill("请整理合成项目的模型交付检查要点，并提供依据。");
    await expectInViewport(page.getByRole("button", { name: "发送问题" }));
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByRole("heading", { name: "模型交付检查建议" })).toBeVisible();
    await expect(page.locator(".prose-tight table")).toBeVisible();
    await expect(page.locator(".prose-tight pre")).toBeVisible();
    await expectNoBodyOverflow(page);
    const tableBounds = await page.locator(".prose-tight table").evaluate((table) => {
      const rect = table.getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: window.innerWidth };
    });
    expect(tableBounds.left).toBeGreaterThanOrEqual(0);
    expect(tableBounds.right).toBeLessThanOrEqual(tableBounds.width + 1);
    await page.getByRole("button", { name: "查看 1 个来源" }).click();
    const workspace = page.locator("aside").filter({ has: page.getByRole("heading", { name: "来源核验", exact: true }) }).filter({ visible: true });
    await expect(workspace).toBeVisible();
    await expect(workspace.getByText("引用原文", { exact: true })).toBeVisible();
    const copySource = workspace.getByRole("button", { name: "复制来源", exact: true });
    // Both the desktop rail and mobile drawer animate in. Visibility alone is
    // true mid-transition; wait for the actual bounds, not a fixed delay.
    await expect.poll(async () => {
      const box = await copySource.boundingBox();
      const viewport = page.viewportSize()!;
      return !!box && box.x >= 0 && box.y >= 0
        && box.x + box.width <= viewport.width
        && box.y + box.height <= viewport.height;
    }).toBe(true);
    await expectInViewport(copySource);
    await expect(workspace.getByRole("button", { name: "查看 模型命名" })).toBeVisible();
    const sourceTitle = workspace.getByTitle("项目交付检查清单", { exact: true });
    const sourceSummary = sourceTitle.locator("xpath=following-sibling::span");
    const sourceCard = workspace.locator("button[aria-pressed]");
    const [titleBox, summaryBox, cardBox, workspaceBox, detailBox] = await Promise.all([
      sourceTitle.boundingBox(), sourceSummary.boundingBox(), sourceCard.boundingBox(),
      workspace.boundingBox(), workspace.getByRole("region", { name: "来源详情" }).boundingBox(),
    ]);
    expect(titleBox).not.toBeNull();
    expect(summaryBox).not.toBeNull();
    expect(cardBox).not.toBeNull();
    expect(workspaceBox).not.toBeNull();
    expect(detailBox).not.toBeNull();
    expect(Math.abs(titleBox!.x - summaryBox!.x)).toBeLessThanOrEqual(1);
    expect(cardBox!.x - workspaceBox!.x).toBeCloseTo(16, 0);
    // A single source must not reserve a fraction of the full drawer height.
    expect(detailBox!.y - cardBox!.y - cardBox!.height).toBeLessThanOrEqual(13);
    await expectNoBodyOverflow(page);
    const viewport = page.viewportSize()!;
    const screenshotName = `chat-answer-sources-${theme}-${viewport.width}x${viewport.height}.png`;
    const screenshotPath = testInfo.outputPath(screenshotName);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach(screenshotName, { path: screenshotPath, contentType: "image/png" });
  });
}

test("窄屏编辑与会话导航保持可达", async ({ page }, testInfo) => {
  await installChatRoutes(page);
  await page.goto("/");
  await page.getByPlaceholder("向企业知识库提问").fill("合成原始提问");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.getByText("合成回答", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "编辑提问", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "编辑提问", exact: true });
  await expect(editor).toBeFocused();
  await editor.fill("合成修改后的提问：请补充交付目录的核对步骤。");
  await expectInViewport(editor);
  await expectNoBodyOverflow(page);
  const viewport = page.viewportSize()!;
  const screenshotName = `chat-edit-question-${viewport.width}x${viewport.height}.png`;
  const screenshotPath = testInfo.outputPath(screenshotName);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach(screenshotName, { path: screenshotPath, contentType: "image/png" });
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByText("合成原始提问", { exact: true })).toBeVisible();
  if ((page.viewportSize()?.width || 1280) < 1024) {
    await page.getByRole("button", { name: "打开会话导航" }).click();
    await expect(page.getByRole("button", { name: "新建对话", exact: true }).filter({ visible: true })).toBeVisible();
    await expectNoBodyOverflow(page);
  }
});
