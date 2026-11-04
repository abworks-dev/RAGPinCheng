import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { installChatRoutes } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";

// Checked-in synthetic OCR artwork, never a customer document or external URL.
const png = readFileSync(new URL("../../../services/image_ocr/synthetic-chinese.png", import.meta.url));
const file = { item_id: "synthetic-item", version_id: "synthetic-version", title: "合成长中文附件名称与项目图纸".repeat(3), original_filename: "synthetic-long-file-name.png", doc_type: "png", file_size: 1024, category_key: "internal-key", category_path: "合成分类 / " + "长分类名称".repeat(8), version_number: 2, matched_fields: ["description", "ocr_text"], snippet: "合成 OCR 文字，仅供文件匹配，不用于正文知识问答。", preview_kind: "image", can_preview: true, can_download: false };
const files = [file,
  { ...file, item_id: "associated-model", version_id: "associated-model", title: "合成关联图片模型", original_filename: "synthetic-model.ifc", doc_type: "ifc" },
  { ...file, item_id: "none-model", version_id: "none-model", title: "合成无封面模型", original_filename: "synthetic-model.dxf", doc_type: "dxf", preview_kind: "none", can_preview: false },
  { ...file, item_id: "pdf-sidecar", version_id: "pdf-sidecar", title: "合成 PDF 关联预览", original_filename: "synthetic-model.ifc", doc_type: "ifc", preview_kind: "pdf" },
  { ...file, item_id: "extra-image", version_id: "extra-image", title: "合成末尾图片", original_filename: "synthetic-extra.png" },
];

for (const scenario of ["success", "error", "stale"]) test(`chat-only attachment strip dedicated preview ${scenario}`, async ({ page }, testInfo) => {
  const failure = scenario === "error";
  const stale = scenario === "stale";
  await installChatRoutes(page);
  const requests: string[] = [];
  const externalRequests: string[] = [];
  let releaseInfo!: () => void;
  const infoReady = new Promise<void>(resolve => { releaseInfo = resolve; });
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname.includes("/api/")) requests.push(url.pathname);
    if (["http:", "https:"].includes(url.protocol) && !["127.0.0.1", "localhost"].includes(url.hostname)) externalRequests.push(url.href);
  });
  await page.route("**/api/conversations/conversation-chat/chat", route => route.fulfill({ contentType: "text/event-stream", body: [
    `event: prep\ndata: ${JSON.stringify({ used_sources: [], file_results: files, search_query: "合成附件", history_chars: 0, budget: 0, fresh_count: 0, final_count: 0 })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ answer_text: "找到相关附件。", sources: [], file_results: files, timings: {}, history_chars: 0, budget: 0 })}\n\n`,
  ].join("") }));
  await page.route("**/api/attachments/versions/*/*", async route => {
    const url = new URL(route.request().url());
    const [, id, action] = url.pathname.match(/versions\/([^/]+)\/([^/]+)$/) || [];
    const selected = files.find(value => value.version_id === id);
    if (!selected || !["info", "preview"].includes(action)) return route.fulfill({ status: 403, json: { detail: "测试禁止下载或未知附件" } });
    if (action === "info" && id === file.version_id) await infoReady;
    if (action === "info") return route.fulfill({ json: { ...selected, can_preview: stale && id === file.version_id ? false : selected.can_preview, rag_enabled: false, preview_status: "ready", is_current: true, is_associated: id === "associated-model" || id === "pdf-sidecar" } });
    return failure && id === file.version_id ? route.fulfill({ status: 403, json: { detail: "附件已不可见" } }) : route.fulfill({ contentType: "image/png", body: png });
  });
  await page.goto("/"); await page.getByPlaceholder("向企业知识库提问").fill("合成附件"); await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已找到 5 个附件，仅提供文件匹配" })).toBeVisible();
  await expect(page.getByText(/未检索到可用资料/)).toHaveCount(0);
  const cards = page.getByRole("region", { name: "找到的附件" });
  const strip = cards.getByRole("list", { name: "附件列表" });
  const items = strip.getByRole("listitem");
  await expect(items).toHaveCount(5);
  await expect(page.getByRole("button", { name: /查看.*个来源/ })).toHaveCount(0);
  await expect(cards).not.toContainText("internal-key");
  await expectNoBodyOverflow(page);
  const geometry = await items.evaluateAll(elements => elements.map(element => { const box = element.getBoundingClientRect(); return { y: box.y, width: box.width, height: box.height }; }));
  expect(new Set(geometry.map(box => Math.round(box.y))).size).toBe(1);
  for (const box of geometry) { expect(box.width).toBeGreaterThanOrEqual(180); expect(box.width).toBeLessThanOrEqual(220); expect(box.height).toBeLessThan(280); }
  expect(await strip.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  expect(requests.some(url => /versions\/(extra-image|none-model|pdf-sidecar)\//.test(url))).toBe(false);
  await expect(items.first().getByRole("status", { name: "" })).toContainText("正在加载封面");
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("attachment-strip-loading.png") });
  releaseInfo();
  if (!failure && !stale) {
    const thumbnail = items.first().getByRole("img");
    await expect(thumbnail).toBeVisible();
    expect(await thumbnail.evaluate(image => getComputedStyle(image).objectFit)).toBe("contain");
    expect(await thumbnail.evaluate(image => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(1);
  } else if (failure) await expect(items.first().getByText("封面加载失败，可打开预览重试")).toBeVisible();
  else { await expect(items.first().getByText("暂无图片封面")).toBeVisible(); expect(requests.filter(url => url.endsWith(`/${file.version_id}/preview`))).toEqual([]); }
  await page.mouse.move(5, 5);
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("attachment-strip-initial.png") });
  // Equivalent to a user scrolling the conversation to its end, not hiding composer overlap by cropping.
  await strip.evaluate(element => {
    let ancestor = element.parentElement;
    while (ancestor) {
      if (ancestor.scrollHeight > ancestor.clientHeight && /auto|scroll/.test(getComputedStyle(ancestor).overflowY)) { ancestor.scrollTop = ancestor.scrollHeight; break; }
      ancestor = ancestor.parentElement;
    }
  });
  await page.mouse.move(5, 5);
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("attachment-strip-compact.png") });
  const more = cards.getByRole("button", { name: `更多信息：${file.title}`, exact: true });
  expect(await more.evaluate(element => { const box = element.getBoundingClientRect(); const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2); return hit === element || element.contains(hit); })).toBe(true);
  if (page.viewportSize()!.width < 600) await more.click();
  else await items.first().hover();
  await expect(items.first().getByText("匹配：描述、OCR 文字")).toBeVisible();
  const panelId = await more.getAttribute("aria-controls");
  await expect(page.locator(`[id="${panelId}"]`)).toHaveCSS("opacity", "1");
  const preview = items.first().getByRole("button", { name: "预览附件", exact: true });
  expect(await preview.evaluate(element => { const box = element.getBoundingClientRect(); const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2); return hit === element || element.contains(hit); })).toBe(true);
  await expect(items.first().getByRole("button", { name: "下载原文件", exact: true })).toBeDisabled();
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("attachment-strip-details.png") });
  await more.focus(); await page.keyboard.press("Tab");
  await expect(items.first().getByLabel(`${file.title}的详细信息`)).toBeFocused();
  await page.keyboard.press("Tab"); await expect(preview).toBeFocused();
  await preview.click();
  const dialog = page.getByRole("dialog", { name: file.title, exact: true });
  if (failure) { await expect(dialog.getByRole("alert")).toBeVisible(); await expect(dialog.getByRole("button", { name: "重试预览" })).toBeEnabled(); }
  else if (stale) { await expect(dialog.getByText("当前版本暂不可预览，请重新搜索或联系发布负责人。")).toBeVisible(); await expect(dialog.getByRole("button", { name: "重试预览" })).toBeEnabled(); }
  else await expect(dialog.getByRole("img")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "关联预览" })).toHaveCount(0);
  await expect(dialog.getByText("文件搜索信息与原图文字识别", { exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "下载原文件", exact: true })).toBeDisabled();
  await expectNoBodyOverflow(page);
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath(`attachment-chat-preview-${scenario}.png`) });
  await dialog.getByRole("button", { name: "关闭预览" }).click(); await expect(preview).toBeFocused();
  await preview.click(); await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape"); await expect(dialog).toBeHidden(); await expect(preview).toBeFocused();
  const right = cards.getByRole("button", { name: "向右滚动附件" });
  for (let step = 0; step < 5; step++) {
    const bounds = await strip.evaluate(element => ({ left: element.scrollLeft, max: element.scrollWidth - element.clientWidth }));
    if (bounds.left >= bounds.max - 2) break;
    await right.click();
    await expect.poll(() => strip.evaluate(element => element.scrollLeft)).toBeGreaterThanOrEqual(Math.min(bounds.left + 200, bounds.max) - 2);
  }
  await expect(items.last().getByRole("button", { name: `更多信息：${files[4].title}` })).toBeInViewport();
  const lastMore = items.last().getByRole("button", { name: `更多信息：${files[4].title}` });
  await page.keyboard.press("Tab"); // Enter keyboard modality before testing focus-visible, not a mouse-induced programmatic focus.
  await lastMore.focus(); await expect(lastMore).toBeFocused();
  expect(await lastMore.evaluate(element => getComputedStyle(element).outlineStyle !== "none" || getComputedStyle(element).boxShadow !== "none")).toBe(true);
  if (await lastMore.getAttribute("aria-expanded") !== "true") await lastMore.click();
  await lastMore.click();
  await expect(lastMore).toHaveAttribute("aria-expanded", "false");
  await expect(items.last().getByRole("button", { name: "预览附件", exact: true })).toBeHidden();
  const lastPanelId = await lastMore.getAttribute("aria-controls");
  await page.keyboard.press("Tab");
  expect(await page.evaluate(id => document.getElementById(id!)?.contains(document.activeElement), lastPanelId)).toBe(false);
  if (page.viewportSize()!.width < 600) {
    const beforeSwipe = await strip.evaluate(element => element.scrollLeft);
    const box = await strip.boundingBox();
    expect(box).not.toBeNull();
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    const y = box!.y + 60;
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: 80, y }] });
    for (const x of [120, 160, 200, 240, 280]) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect.poll(() => strip.evaluate(element => element.scrollLeft)).toBeLessThan(beforeSwipe - 50);
    await cdp.detach();
  }
  expect(requests.filter(url => url.includes("/api/admin/content/"))).toEqual([]);
  expect(externalRequests).toEqual([]);
  expect(requests.filter(url => /\/download$|\/original$/.test(url))).toEqual([]);
  expect(requests.filter(url => /versions\/(none-model|pdf-sidecar)\//.test(url))).toEqual([]);
  await expectNoBodyOverflow(page);
});
