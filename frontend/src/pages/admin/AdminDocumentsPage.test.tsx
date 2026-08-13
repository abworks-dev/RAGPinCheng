import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDocumentsPage } from "./AdminDocumentsPage";

const mocks = vi.hoisted(() => ({
  adminCategoryTree: vi.fn(),
  adminListIndexedDocuments: vi.fn(),
  adminListIndexJobs: vi.fn(),
  adminUploadDocuments: vi.fn(),
  adminDeleteIndexedDocument: vi.fn(),
  adminRetryIndexJob: vi.fn(),
  adminDeleteIndexJob: vi.fn(),
  openPreview: vi.fn(),
  openVideo: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: mocks }));
vi.mock("../../components/PdfPreview", () => ({ PdfPreview: () => null }));
vi.mock("../../hooks/usePdfPreview", () => ({
  PdfPreviewProvider: ({ children }: { children: React.ReactNode }) => children,
  usePdfPreview: () => ({ open: mocks.openPreview }),
}));
vi.mock("../../hooks/useVideoPlayer", () => ({
  useVideoPlayer: () => ({ open: mocks.openVideo }),
}));

const categoryTree = {
  categories: [
    { name: "公司标准", two_level: false, subcategories: [] },
    { name: "客户标准", two_level: true, subcategories: ["甲方", "乙方"] },
  ],
  second_level_categories: ["客户标准"],
};

const readyDocument = {
  document_id: "document-ready",
  display_path: "公司标准 / company-standard.pdf",
  filename: "company-standard.pdf",
  doc_title: "企业交付标准",
  category: "公司标准",
  doc_type: "pdf",
  company: null,
  parent_count: 12,
  preview_parent_id: "parent-ready-1",
  media_id: null,
  child_count: 36,
  file_size: 2048,
  status: "done",
  is_indexed: true,
  latest_job_id: 1,
  error_summary: null,
  uploaded_by: "管理员",
  created_at: 1785686400,
  updated_at: 1785686410,
};

const failedDocument = {
  ...readyDocument,
  document_id: "document-failed",
  display_path: "客户标准 / 甲方 / failed.docx",
  filename: "failed.docx",
  doc_title: "失败的客户资料",
  category: "客户标准",
  doc_type: "docx",
  company: "甲方",
  parent_count: 0,
  preview_parent_id: null,
  media_id: null,
  child_count: null,
  status: "failed",
  is_indexed: false,
  latest_job_id: 2,
  error_summary: "资料处理失败，可重试或在索引活动中查看详情。",
  updated_at: 1785686420,
};

const listing = {
  documents: [readyDocument, failedDocument],
  total: 2,
  status_counts: { ready: 1, failed: 1, processing: 0 },
};

const baseJob = {
  user_id: 1,
  employee_id: "A001",
  real_name: "管理员",
  category: "公司标准",
  doc_type: "pdf",
  source_path: "C:/synthetic/company-standard.pdf",
  source_exists: true,
  file_size: 2048,
  error: null,
  parents: 12,
  children: 36,
  created_at: 1785686400,
  started_at: 1785686401,
  finished_at: 1785686410,
};

const jobs = [
  { ...baseJob, id: 1, filename: "company-standard.pdf", status: "done" },
  {
    ...baseJob,
    id: 2,
    filename: "failed.docx",
    status: "failed",
    error: "解析器暂不可用",
  },
];

describe("AdminDocumentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminCategoryTree.mockResolvedValue(categoryTree);
    mocks.adminListIndexedDocuments.mockResolvedValue(listing);
    mocks.adminListIndexJobs.mockResolvedValue({ jobs });
    mocks.adminUploadDocuments.mockResolvedValue({ accepted: [jobs[0]], skipped: [] });
    mocks.adminDeleteIndexedDocument.mockResolvedValue({
      parents_deleted: 12,
      file_deleted: false,
      file_delete_status: "not_requested",
    });
    mocks.adminRetryIndexJob.mockResolvedValue(jobs[0]);
    mocks.adminDeleteIndexJob.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("uses a document-first lifecycle list without exposing internal paths", async () => {
    render(<AdminDocumentsPage />);

    expect(screen.getByText("正在加载资料…")).toBeInTheDocument();
    expect(await screen.findByText("企业交付标准")).toBeInTheDocument();
    expect(screen.getByText("失败的客户资料")).toBeInTheDocument();
    const readyRow = screen.getByText("企业交付标准").closest("tr");
    const failedRow = screen.getByText("失败的客户资料").closest("tr");
    expect(within(readyRow as HTMLElement).getByText("可检索")).toHaveClass("bg-success/15");
    expect(within(failedRow as HTMLElement).getByText("处理失败")).toHaveClass("bg-destructive/15");
    expect(screen.getByText("资料处理失败，可重试或在索引活动中查看详情。")).toBeInTheDocument();
    expect(JSON.stringify(listing.documents)).not.toContain("source_path");
    expect(screen.getAllByText("1")).toHaveLength(2);
    expect(mocks.adminListIndexedDocuments).toHaveBeenCalledWith(expect.objectContaining({
      limit: 25,
      offset: 0,
    }));
  });

  it("opens a focused upload sheet with removable file queue and preserves upload contract", async () => {
    const pdf = new File(["pdf"], "standard.pdf", { type: "application/pdf" });
    const empty = new File([], "empty.pdf", { type: "application/pdf" });

    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");
    fireEvent.click(screen.getByRole("button", { name: "上传资料" }));

    const sheet = await screen.findByRole("dialog", { name: "上传资料" });
    const input = within(sheet).getByLabelText("文件");
    fireEvent.change(input, { target: { files: [pdf, empty] } });

    expect(within(sheet).getByText("空文件，不能上传")).toBeInTheDocument();
    fireEvent.click(within(sheet).getByRole("button", { name: "移除 empty.pdf" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "上传 1 个文件" }));

    await waitFor(() => expect(mocks.adminUploadDocuments).toHaveBeenCalledWith(
      [pdf],
      "公司标准",
      undefined,
    ));
    expect(await within(sheet).findByText("资料已加入处理队列")).toBeInTheDocument();
  });

  it("sends search, category, type and status filters to the server", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.change(screen.getByPlaceholderText("搜索标题、文件名或分类…"), { target: { value: "预算" } });
    fireEvent.change(screen.getByLabelText("按分类筛选"), { target: { value: "客户标准" } });
    fireEvent.change(screen.getByLabelText("按文件类型筛选"), { target: { value: "docx" } });
    fireEvent.click(screen.getByRole("button", { name: "失败" }));

    await waitFor(() => expect(mocks.adminListIndexedDocuments).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "预算",
      category: "客户标准",
      doc_type: "docx",
      status: "failed",
      limit: 25,
      offset: 0,
    })));
  });

  it("uses one safe deletion dialog and defaults to keeping the source file", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.click(screen.getByLabelText("打开 企业交付标准 的操作菜单"));
    fireEvent.click(screen.getByRole("menuitem", { name: "移除资料" }));
    const dialog = await screen.findByRole("dialog", { name: "移除资料" });
    expect(within(dialog).getByRole("radio", { name: /保留源文件/ })).toBeChecked();

    fireEvent.click(within(dialog).getByRole("button", { name: "从知识库移除" }));
    await waitFor(() => expect(mocks.adminDeleteIndexedDocument).toHaveBeenCalledWith(
      readyDocument.document_id,
      false,
    ));
  });

  it("keeps only one document action menu open", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    const readyMenu = screen.getByLabelText("打开 企业交付标准 的操作菜单");
    const failedMenu = screen.getByLabelText("打开 失败的客户资料 的操作菜单");
    fireEvent.click(readyMenu);
    expect(readyMenu).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu", { name: "企业交付标准的资料操作" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "预览文件" })).toBeInTheDocument();
    fireEvent.click(failedMenu);
    expect(readyMenu).toHaveAttribute("aria-expanded", "false");
    expect(failedMenu).toHaveAttribute("aria-expanded", "true");
  });

  it("supports keyboard navigation and restores focus when a menu closes", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");
    const trigger = screen.getByLabelText("打开 企业交付标准 的操作菜单");

    fireEvent.click(trigger);
    const preview = await screen.findByRole("menuitem", { name: "预览文件" });
    await waitFor(() => expect(preview).toHaveFocus());
    fireEvent.keyDown(preview, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "重新索引" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("closes the fixed menu when its viewport position can change", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");
    fireEvent.click(screen.getByLabelText("打开 企业交付标准 的操作菜单"));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.scroll(window);

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("opens the shared source-file preview for an indexed document", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.click(screen.getByLabelText("打开 企业交付标准 的操作菜单"));
    fireEvent.click(screen.getByRole("menuitem", { name: "预览文件" }));

    expect(mocks.openPreview).toHaveBeenCalledWith("parent-ready-1", "企业交付标准", "pdf", 1);
    expect(screen.getByLabelText("打开 企业交付标准 的操作菜单")).toHaveAttribute("aria-expanded", "false");
  });

  it("labels and opens a video transcript with the shared video and transcript drawer", async () => {
    const transcriptDocument = {
      ...readyDocument,
      document_id: "document-transcript",
      filename: "training.md",
      doc_title: "培训视频",
      category: "教学视频",
      doc_type: "transcript",
      preview_parent_id: "parent-transcript-1",
      media_id: "media-1",
    };
    mocks.adminListIndexedDocuments.mockResolvedValueOnce({
      documents: [transcriptDocument],
      total: 1,
      status_counts: { ready: 1 },
    });
    render(<AdminDocumentsPage />);
    await screen.findByText("培训视频");

    const transcriptRow = screen.getByText("培训视频").closest("tr");
    expect(within(transcriptRow as HTMLElement).getByText("视频转写")).toBeInTheDocument();
    expect(screen.queryByText("教学视频转写")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("打开 培训视频 的操作菜单"));
    fireEvent.click(screen.getByRole("menuitem", { name: "预览视频" }));

    expect(mocks.openVideo).toHaveBeenCalledWith({
      mediaId: "media-1",
      title: "培训视频",
      startSeconds: 0,
      fromSource: false,
    });
    expect(mocks.openPreview).not.toHaveBeenCalled();
  });

  it("does not offer a broken video preview without a media association", async () => {
    mocks.adminListIndexedDocuments.mockResolvedValueOnce({
      documents: [{ ...readyDocument, document_id: "legacy-transcript", doc_type: "transcript", media_id: null }],
      total: 1,
      status_counts: { ready: 1 },
    });
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");
    fireEvent.click(screen.getByLabelText("打开 企业交付标准 的操作菜单"));

    expect(screen.queryByRole("menuitem", { name: "预览视频" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "预览文件" })).not.toBeInTheDocument();
  });

  it("requires an explicit destructive choice before deleting the source file", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.click(screen.getByLabelText("打开 企业交付标准 的操作菜单"));
    fireEvent.click(screen.getByRole("menuitem", { name: "移除资料" }));
    const dialog = await screen.findByRole("dialog", { name: "移除资料" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /同时删除源文件/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "删除资料和源文件" }));

    await waitFor(() => expect(mocks.adminDeleteIndexedDocument).toHaveBeenCalledWith(
      readyDocument.document_id,
      true,
    ));
  });

  it("warns when the index was removed but deleting the source file failed", async () => {
    mocks.adminDeleteIndexedDocument.mockResolvedValueOnce({
      parents_deleted: 12,
      file_deleted: false,
      file_delete_status: "failed",
    });
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.click(screen.getByLabelText("打开 企业交付标准 的操作菜单"));
    fireEvent.click(screen.getByRole("menuitem", { name: "移除资料" }));
    const dialog = await screen.findByRole("dialog", { name: "移除资料" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /同时删除源文件/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "删除资料和源文件" }));

    expect(await within(dialog).findByText("删除未完全完成")).toBeInTheDocument();
    expect(within(dialog).getByText(/知识库索引已移除，但源文件删除失败/)).toBeInTheDocument();
    expect(mocks.adminListIndexedDocuments).toHaveBeenCalledTimes(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(mocks.adminListIndexedDocuments).toHaveBeenCalledTimes(2));
  });

  it("retries a failed document from its lifecycle row", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("失败的客户资料");

    fireEvent.click(screen.getByLabelText("打开 失败的客户资料 的操作菜单"));
    fireEvent.click(screen.getByRole("menuitem", { name: "重试处理" }));

    await waitFor(() => expect(mocks.adminRetryIndexJob).toHaveBeenCalledWith(2));
  });

  it("keeps task history secondary and confirms record deletion in a dialog", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    const activity = screen.getByText("索引活动").closest("details");
    expect(activity).not.toBeNull();
    fireEvent.click(within(activity as HTMLElement).getByText("索引活动"));
    expect(await screen.findByText("解析器暂不可用")).toBeInTheDocument();

    const failedRow = screen.getByText("failed.docx").closest("tr");
    fireEvent.click(within(failedRow as HTMLElement).getByRole("button", { name: "删除记录" }));
    const dialog = await screen.findByRole("dialog", { name: "删除索引活动记录" });
    fireEvent.click(within(dialog).getByRole("button", { name: "删除记录" }));

    await waitFor(() => expect(mocks.adminDeleteIndexJob).toHaveBeenCalledWith(2));
  });

  it("uses historical completion wording and blocks retry when the source file is gone", async () => {
    const missingSourceJob = {
      ...jobs[0],
      id: 3,
      filename: "deleted.xlsx",
      doc_type: "xlsx",
      source_exists: false,
    };
    mocks.adminListIndexJobs.mockResolvedValueOnce({ jobs: [missingSourceJob] });

    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    const activity = screen.getByText("索引活动").closest("details");
    expect(activity).not.toBeNull();
    fireEvent.click(within(activity as HTMLElement).getByText("索引活动"));

    const row = screen.getByText("deleted.xlsx").closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("处理完成")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("源文件已删除，无法重试")).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(within(row as HTMLElement).getByRole("button", { name: "删除记录" })).toBeEnabled();
    expect(mocks.adminRetryIndexJob).not.toHaveBeenCalled();
  });

  it("polls active jobs and refreshes the unified document list", async () => {
    vi.useFakeTimers();
    const activeJob = { ...jobs[0], id: 3, filename: "processing.xlsx", status: "embedding", finished_at: null };
    mocks.adminListIndexJobs
      .mockResolvedValueOnce({ jobs: [activeJob] })
      .mockResolvedValueOnce({ jobs });

    render(<AdminDocumentsPage />);
    await act(async () => { await Promise.resolve(); });
    act(() => vi.advanceTimersByTime(3000));
    await act(async () => { await Promise.resolve(); });

    expect(mocks.adminListIndexJobs).toHaveBeenCalledTimes(2);
    expect(mocks.adminListIndexedDocuments.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
