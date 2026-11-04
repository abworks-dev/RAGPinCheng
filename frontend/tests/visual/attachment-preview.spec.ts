import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { installAdminRoutes, items } from "./fixtures/admin-fixtures";
import { expectNoBodyOverflow } from "./helpers/layout";
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aMZkAAAAASUVORK5CYII=", "base64");

function syntheticPdf() {
  const stream = "BT /F1 24 Tf 30 100 Td (Synthetic sidecar) Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 160] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let result = "%PDF-1.4\n";
  const offsets = objects.map((body, index) => { const offset = result.length; result += `${index + 1} 0 obj\n${body}\nendobj\n`; return offset; });
  const xref = result.length;
  result += `xref\n0 6\n0000000000 65535 f \n${offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(result);
}

async function openAttachment(page: Page, format: string, options: { fail?: boolean; wait?: boolean; sidecar?: boolean; sidecarPdf?: boolean; metadataFail?: boolean; corrupt?: boolean; oversize?: boolean } = {}) {
  const imageData = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 1200; canvas.height = 800;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#e2e8f0"; context.fillRect(0, 0, 1200, 800);
    context.strokeStyle = "#94a3b8"; context.lineWidth = 2;
    for (let x = 0; x <= 1200; x += 100) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x, 800); context.stroke(); }
    for (let y = 0; y <= 800; y += 100) { context.beginPath(); context.moveTo(0, y); context.lineTo(1200, y); context.stroke(); }
    context.fillStyle = "#1d4ed8"; context.fillRect(200, 180, 800, 440);
    context.fillStyle = "#fff"; context.font = "48px sans-serif"; context.fillText("SYNTHETIC 1200 x 800", 300, 420);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const originalImage = Buffer.from(imageData, "base64");
  await installAdminRoutes(page, "normal");
  const item = { ...items[0], title: `合成附件 ${format}`, original_filename: `synthetic.${format}`, doc_type: format, preview_parent_id: null, preview_status: "ready", file_size: 1024 };
  await page.route("**/api/admin/content/items-page?*", (route) => route.fulfill({ json: { items: [item], total: 1, status_counts: { pending_publication: 1 } } }));
  let sidecar = options.sidecar || false;
  await page.route("**/api/admin/content/versions/version-1/info", async (route) => {
    if (options.metadataFail) return route.fulfill({ status: 403, json: { detail: "无资料查看权限" } });
    if (options.wait) await new Promise((resolve) => setTimeout(resolve, 1200));
    return route.fulfill({ json: { ...item, category_path: undefined, category_name: "合成目录", source_origin: "web", is_current: false, lifecycle_status: "pending_publication", rag_enabled: false, preview_kind: sidecar && options.sidecarPdf ? "pdf" : sidecar || format === "png" ? "image" : ["dxf", "ifc"].includes(format) ? format : "none", is_associated: sidecar } });
  });
  await page.route(/\/api\/admin\/content\/versions\/version-1\/(preview|associated-preview)$/, async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-csrf-token");
      sidecar = true; return route.fulfill({ json: {} });
    }
    if (options.fail) return route.fulfill({ status: 403, json: { detail: "denied" } });
    if (sidecar && options.sidecarPdf) return route.fulfill({ contentType: "application/pdf", body: syntheticPdf() });
    if (options.corrupt) return route.fulfill({ contentType: "image/png", body: Buffer.from("invalid synthetic PNG") });
    if (options.oversize) { const huge = Buffer.from(png); huge.writeUInt32BE(40000, 16); return route.fulfill({ contentType: "image/png", body: huge }); }
    if (sidecar || format === "png") return route.fulfill({ contentType: "image/png", body: sidecar ? png : originalImage });
    return route.fulfill({ contentType: "application/octet-stream", body: readFileSync(new URL(`./fixtures/attachment.${format}`, import.meta.url)) });
  });
  await page.goto("/admin");
  if (page.viewportSize()!.width < 1024) await page.getByRole("button", { name: "展开管理功能" }).click();
  await page.getByRole("link", { name: "资料管理", exact: true }).click();
  const folder = page.getByTestId(page.viewportSize()!.width < 1024 ? "managed-folder-mobile-cat-company" : "managed-folder-row-cat-company");
  await folder.getByRole("button").first().click();
  await page.getByRole("button", { name: `预览“${item.title}”`, exact: true }).filter({ visible: true }).click();
  return page.getByRole("dialog", { name: item.title, exact: true });
}

test("metadata editing and OCR lifecycle remain version bound", async ({ page }, testInfo) => {
  const sheet = await openAttachment(page, "png");
  let metadata = { version_id: "version-1", description: "合成原图说明", tags: ["图纸"], project: "合成项目", aliases: [], revision: 1 };
  let status = "no_text";
  await page.route("**/api/admin/content/versions/version-1/search-info", route => route.fulfill({ json: metadata }));
  await page.route("**/api/admin/content/versions/version-1/search-metadata", route => { expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-csrf-token"); metadata = { ...metadata, ...route.request().postDataJSON(), revision: 2 }; return route.fulfill({ json: metadata }); });
  await page.route("**/api/admin/content/versions/version-1/ocr", route => {
    if (route.request().method() === "POST") { status = "running"; return route.fulfill({ json: { id: "job-1", status } }); }
    return route.fulfill({ json: { version_id: "version-1", current_job_id: null, jobs: [{ id: "job-1", status }] } });
  });
  await page.route("**/api/admin/content/versions/version-1/ocr/cancel", route => { status = "cancelled"; return route.fulfill({ json: { version_id: "version-1", cancelled: 1 } }); });
  await sheet.getByText("文件搜索信息与原图文字识别", { exact: true }).click();
  await expect(sheet.getByLabel("描述", { exact: true })).toHaveValue("合成原图说明");
  await sheet.getByLabel("描述", { exact: true }).fill("合成更新说明");
  await sheet.getByRole("button", { name: "保存搜索信息" }).click();
  await expect(sheet.getByText("当前版本文件搜索信息已保存")).toBeVisible();
  await expect(sheet.getByText(/此状态不表示已理解图片/)).toBeVisible();
  await sheet.getByRole("button", { name: "重新识别原图文字" }).click();
  await expect(sheet.getByRole("button", { name: "重新识别原图文字" })).toBeDisabled();
  await expect(sheet.getByRole("button", { name: "取消识别", exact: true })).toBeEnabled();
  await sheet.getByRole("button", { name: "取消识别", exact: true }).click();
  await expect(sheet.getByText(/已取消 · 暂无有效文字结果/)).toBeVisible();
  await expectNoBodyOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`attachment-search-success-${page.viewportSize()!.width}x${page.viewportSize()!.height}.png`) });
});

test("image native size rotate fit metadata and keyboard focus", async ({ page }, testInfo) => {
  const sheet = await openAttachment(page, "png");
  await expect(sheet.getByText("1200 × 800 像素")).toBeVisible();
  await sheet.getByRole("button", { name: "实际大小", exact: true }).click();
  await sheet.getByRole("button", { name: "旋转", exact: true }).click();
  await sheet.getByRole("button", { name: "适合窗口", exact: true }).click();
  await sheet.getByText("文件信息 · 原文件预览").click();
  await expect(sheet.getByText("synthetic.png", { exact: true })).toBeVisible();
  await expect(sheet.getByText("不作为正文问答依据；文件信息可搜索，已启用的 OCR 文字可搜索")).toBeVisible();
  await expect(sheet.getByText("合成目录", { exact: true })).toBeVisible();
  await expect(sheet.getByText("网页上传", { exact: true })).toBeVisible();
  await expect(sheet.getByText("v1（待发布版本）", { exact: true })).toBeVisible();
  await expectNoBodyOverflow(page);
  await sheet.getByRole("button", { name: "下载原文件", exact: true }).focus();
  await expect(sheet.getByRole("button", { name: "下载原文件", exact: true })).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath("attachment-image.png") });
  await sheet.getByRole("button", { name: "关闭预览" }).click();
  await expect(page.getByRole("button", { name: "预览“合成附件 png”", exact: true }).filter({ visible: true })).toBeFocused();
});

for (const format of ["dxf", "ifc"]) test(`${format} real local worker geometry render`, async ({ page }, testInfo) => {
  const external: string[] = [];
  page.on("request", (request) => { if (/^https?:/.test(request.url()) && new URL(request.url()).hostname !== "127.0.0.1") external.push(request.url()); });
  const sheet = await openAttachment(page, format);
  await expect(sheet.getByRole("status")).toContainText(`${format.toUpperCase()} ${format === "dxf" ? "二维" : "三维"}预览`, { timeout: 45000 });
  await expect(sheet.locator("canvas")).toBeVisible();
  await expect(sheet.getByRole("button", { name: "适合窗口", exact: true })).toBeEnabled();
  await sheet.getByRole("button", { name: "适合窗口", exact: true }).click();
  expect(external).toEqual([]);
  await expectNoBodyOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`attachment-${format}.png`) });
});

test("unsupported format supports current version manual preview", async ({ page }) => {
  const sheet = await openAttachment(page, "rvt");
  await expect(sheet.getByText("此格式暂不支持浏览器原生预览。")).toBeVisible();
  await sheet.locator("input[type=file]").setInputFiles({ name: "preview.png", mimeType: "image/png", buffer: png });
  await expect(sheet.getByText("已关联当前版本预览，不影响其他版本")).toBeVisible();
  await expect(sheet.getByText("1 × 1 像素")).toBeVisible();
  await expect(sheet.getByText("文件信息 · 当前版本关联预览")).toBeVisible();
  await expectNoBodyOverflow(page);
});

test("associated PDF renders with matching same-origin worker", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => { if (/^https?:/.test(request.url()) && new URL(request.url()).hostname !== "127.0.0.1") external.push(request.url()); });
  const sheet = await openAttachment(page, "rvt", { sidecarPdf: true });
  await sheet.locator("input[type=file]").setInputFiles({ name: "preview.pdf", mimeType: "application/pdf", buffer: syntheticPdf() });
  await expect(sheet.getByText("Synthetic sidecar", { exact: true })).toBeVisible();
  await expect(sheet.locator("canvas")).toBeVisible();
  expect(external).toEqual([]);
  await expectNoBodyOverflow(page);
});

test("permission error preserves recovery and download", async ({ page }) => {
  const sheet = await openAttachment(page, "png", { fail: true });
  await expect(sheet.getByRole("alert")).toContainText("无法读取资料预览");
  await expect(sheet.getByRole("button", { name: "重试预览" })).toBeEnabled();
  await expect(sheet.getByRole("button", { name: "下载原文件", exact: true })).toBeEnabled();
  await expectNoBodyOverflow(page);
});

for (const failure of ["metadataFail", "corrupt", "oversize"] as const) test(`${failure} preserves safe fallback and close`, async ({ page }) => {
  const sheet = await openAttachment(page, "png", { [failure]: true });
  await expect(sheet.getByRole("alert")).toBeVisible();
  await expect(sheet.getByRole("button", { name: "重试预览" })).toBeEnabled();
  await expect(sheet.getByRole("button", { name: "下载原文件", exact: true })).toBeEnabled();
  await expect(sheet.locator("img")).toHaveCount(0);
  await sheet.getByRole("button", { name: "关闭预览" }).click();
  await expect(sheet).toBeHidden();
});

test("loading can close and cancels without stale preview", async ({ page }) => {
  const sheet = await openAttachment(page, "png", { wait: true });
  await expect(sheet.getByRole("status")).toContainText("正在读取");
  await expect(sheet.getByRole("button", { name: "重试预览" })).toBeDisabled();
  await sheet.getByRole("button", { name: "关闭预览" }).click();
  await expect(sheet).toBeHidden();
  await expectNoBodyOverflow(page);
});
