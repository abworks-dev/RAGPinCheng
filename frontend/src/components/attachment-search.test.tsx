import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AttachmentSearchPanel } from "./AttachmentSearchPanel";
import { FileResultCards } from "./FileResultCards";
import { PdfPreviewProvider } from "../hooks/usePdfPreview";
import type { FileResult } from "../types";
const mocks = vi.hoisted(() => ({ attachmentSearchInfo: vi.fn(), updateAttachmentSearchMetadata: vi.fn(), attachmentOcr: vi.fn(), startAttachmentOcr: vi.fn(), cancelAttachmentOcr: vi.fn() }));
vi.mock("../api/client", () => ({ api: mocks }));
const metadata = { version_id: "version-1", description: "合成描述", project: "合成项目", tags: ["图纸"], aliases: ["示例"], revision: 1 };
const file: FileResult = { item_id: "internal-item", version_id: "internal-version", title: "合成文件", original_filename: "synthetic.png", doc_type: "png", file_size: 2048, category_key: "internal-category", category_path: "合成目录", version_number: 1, matched_fields: ["ocr_text"], snippet: "合成文字", preview_kind: "image", can_preview: true, can_download: false };
afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); mocks.attachmentSearchInfo.mockResolvedValue(metadata); mocks.attachmentOcr.mockResolvedValue({ version_id: "version-1", current_job_id: null, jobs: [] }); mocks.updateAttachmentSearchMetadata.mockResolvedValue(metadata); });
function panel(docType = "png", canEdit = true) { render(<AttachmentSearchPanel versionId="version-1" docType={docType} canEdit={canEdit} />); fireEvent.click(screen.getByText("文件搜索信息与原图文字识别")); }
describe("attachment discovery separation", () => {
  it("toggles details closed explicitly, preserves action order and dismisses with Escape", () => {
    render(<PdfPreviewProvider><FileResultCards results={[file]} /></PdfPreviewProvider>);
    const more = screen.getByRole("button", { name: "更多信息：合成文件" });
    const details = document.getElementById(more.getAttribute("aria-controls")!)!;
    fireEvent.click(more); expect(more).toHaveAttribute("aria-expanded", "true");
    expect(more.compareDocumentPosition(screen.getByRole("button", { name: "预览附件" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(more); expect(more).toHaveAttribute("aria-expanded", "false");
    expect(details.className).toContain("invisible opacity-0"); expect(details.className).not.toContain("group-focus-within:visible");
    fireEvent.click(more); fireEvent.keyDown(more, { key: "Escape" });
    expect(more).toHaveAttribute("aria-expanded", "false"); expect(more).toHaveFocus();
  });
  it("shows independent file match reasons without technical identifiers or implied citations", () => {
    render(<PdfPreviewProvider><FileResultCards results={[file]} /></PdfPreviewProvider>);
    fireEvent.click(screen.getByRole("button", { name: "更多信息：合成文件" }));
    expect(screen.getByText("匹配：OCR 文字")).toBeInTheDocument(); expect(screen.getByText(/不作为正文问答依据/)).toBeInTheDocument();
    expect(screen.queryByText("internal-item")).not.toBeInTheDocument(); expect(screen.queryByText("internal-category")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载原文件" })).toBeDisabled(); expect(screen.getByRole("button", { name: "预览附件" })).toBeEnabled();
  });
  it("opens a chat version without fabricating parent identity", () => {
    render(<PdfPreviewProvider><FileResultCards results={[file]} /></PdfPreviewProvider>);
    fireEvent.click(screen.getByRole("button", { name: "更多信息：合成文件" }));
    fireEvent.click(screen.getByRole("button", { name: "预览附件" }));
    expect(screen.getByRole("button", { name: "预览附件" })).toBeInTheDocument();
  });
});
describe("version metadata and original OCR", () => {
  it("loads on expansion, edits approved fields only, and confirms save", async () => {
    panel(); await screen.findByDisplayValue("合成描述");
    fireEvent.change(screen.getByLabelText("标签（顿号分隔）"), { target: { value: "图纸、测试、图纸" } });
    fireEvent.click(screen.getByRole("button", { name: "保存搜索信息" }));
    await waitFor(() => expect(mocks.updateAttachmentSearchMetadata).toHaveBeenCalledWith("version-1", { description: "合成描述", project: "合成项目", tags: ["图纸", "测试"], aliases: ["示例"] }));
    expect(await screen.findByText("当前版本文件搜索信息已保存")).toBeInTheDocument();
  });
  it("does not OCR a model or associated image", async () => { panel("rvt"); await screen.findByDisplayValue("合成描述"); expect(mocks.attachmentOcr).not.toHaveBeenCalled(); expect(screen.getByText(/模型及其关联预览不自动识别/)).toBeInTheDocument(); });
  it("keeps read-only metadata visible with disabled mutations", async () => { panel("png", false); await screen.findByDisplayValue("合成描述"); expect(screen.getByRole("button", { name: "保存搜索信息" })).toBeDisabled(); expect(screen.getByRole("button", { name: "识别原图文字" })).toBeDisabled(); });
  it("shows recoverable loading failure", async () => { mocks.attachmentSearchInfo.mockRejectedValue(new Error("合成加载失败")); panel(); expect(await screen.findByRole("alert")).toHaveTextContent("合成加载失败"); expect(screen.getByRole("button", { name: "重新读取" })).toBeEnabled(); });
  it("keeps metadata editable when OCR is unconfigured", async () => { mocks.attachmentOcr.mockResolvedValue({ current_job_id: null, jobs: [], enabled: true, available: false, reason_code: "ocr_unconfigured" }); panel(); await screen.findByDisplayValue("合成描述"); expect(screen.getByText(/尚未完成配置/)).toBeInTheDocument(); expect(screen.getByRole("button", { name: "识别原图文字" })).toBeDisabled(); expect(screen.getByRole("button", { name: "保存搜索信息" })).toBeEnabled(); });
  it("keeps metadata available when the OCR status request fails", async () => { mocks.attachmentOcr.mockRejectedValue(new Error("synthetic OCR status failure")); panel(); await screen.findByDisplayValue("合成描述"); expect(screen.getByRole("alert")).toHaveTextContent("文件搜索信息仍可维护"); expect(screen.getByRole("button", { name: "保存搜索信息" })).toBeEnabled(); });
  it("shows no-text honestly and offers retry", async () => { mocks.attachmentOcr.mockResolvedValue({ current_job_id: null, jobs: [{ id: "job", status: "no_text" }] }); panel(); await screen.findByDisplayValue("合成描述"); expect(screen.getByText(/此状态不表示已理解图片/)).toBeInTheDocument(); expect(screen.getByRole("button", { name: "重新识别原图文字" })).toBeEnabled(); });
});
