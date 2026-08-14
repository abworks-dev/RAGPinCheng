import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDocumentsPage } from "./AdminDocumentsPage";

const mocks = vi.hoisted(() => ({
  adminCategoryTree: vi.fn(),
  adminListIndexedDocuments: vi.fn(),
  adminListIndexJobs: vi.fn(),
  managedContentIndexJobs: vi.fn(),
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

const managedFailedJob = {
  id: "managed-job-1",
  publication_id: "publication-1",
  version_id: "version-1",
  attempt_number: 1,
  status: "failed",
  error_code: "parser_request_failed",
  error_summary: "文档解析服务请求失败，请稍后重试。",
  failure: { code: "parser_request_failed", message: "文档解析服务请求失败。", retryable: true, recommended_action: "请稍后重试；持续失败时联系系统管理员。" },
  attempt_count: 4,
  created_at: 1785686400,
  started_at: 1785686401,
  finished_at: 1785686410,
  updated_at: 1785686410,
  title: "受管资料",
  original_filename: "managed.pdf",
  category_label: "03 公司内部标准",
};

describe("AdminDocumentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminCategoryTree.mockResolvedValue(categoryTree);
    mocks.adminListIndexedDocuments.mockResolvedValue(listing);
    mocks.adminListIndexJobs.mockResolvedValue({ jobs });
    mocks.managedContentIndexJobs.mockResolvedValue({ jobs: [], total: 0, status_counts: {} });
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

  it("uses responsive object rows instead of fixed-width narrow tables", async () => {
    mocks.managedContentIndexJobs.mockResolvedValue({
      jobs: [managedFailedJob],
      total: 1,
      status_counts: { failed: 1 },
    });
    render(<AdminDocumentsPage />);
    const documentTitle = await screen.findByText("企业交付标准");
    const documentRow = documentTitle.closest("tr");
    expect(documentRow).toHaveClass("grid", "lg:table-row");
    expect(documentRow?.closest("table")).toHaveClass("block", "lg:table");

    const managedRow = screen.getByText("受管资料").closest("tr");
    expect(managedRow).toHaveClass("grid", "lg:table-row");
    expect(managedRow?.closest("table")).toHaveClass("block", "lg:table");

    const activity = screen.getByText("旧目录索引活动").closest("details");
    fireEvent.click(within(activity as HTMLElement).getByText("旧目录索引活动"));
    const legacyRow = screen.getByText("failed.docx").closest("tr");
    expect(legacyRow).toHaveClass("grid", "lg:table-row");
    expect(legacyRow?.closest("table")).toHaveClass("block", "lg:table");
  });

  it("shows the controlled managed publication failure without exposing its code", async () => {
    mocks.managedContentIndexJobs.mockResolvedValue({
      jobs: [managedFailedJob],
      total: 1,
      status_counts: { failed: 1 },
    });
    render(<AdminDocumentsPage />);

    expect(await screen.findByText("受管资料")).toBeInTheDocument();
    expect(screen.getByText("文档解析服务请求失败。")).toBeInTheDocument();
    expect(screen.getByText("请稍后重试；持续失败时联系系统管理员。")).toBeInTheDocument();
    expect(screen.getByText("共尝试 4 次")).toBeInTheDocument();
    expect(screen.queryByText("parser_request_failed")).not.toBeInTheDocument();
  });

  it("loads publication history only when requested", async () => {
    mocks.managedContentIndexJobs.mockResolvedValue({ jobs: [managedFailedJob], total: 1, status_counts: { failed: 1 } });
    render(<AdminDocumentsPage />);
    await screen.findByText("受管资料");
    expect(mocks.managedContentIndexJobs).toHaveBeenCalledWith({ limit: 100, history: false });
    fireEvent.click(screen.getByRole("button", { name: "查看历史尝试" }));
    await waitFor(() => expect(mocks.managedContentIndexJobs).toHaveBeenLastCalledWith({ limit: 100, history: true }));
  });

  it("keeps the legacy monitor read-only and removes the old upload entry", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");
    expect(screen.getByRole("heading", { name: "索引监控" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "上传资料" })).not.toBeInTheDocument();
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

    const activity = screen.getByText("旧目录索引活动").closest("details");
    expect(activity).not.toBeNull();
    fireEvent.click(within(activity as HTMLElement).getByText("旧目录索引活动"));
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

    const activity = screen.getByText("旧目录索引活动").closest("details");
    expect(activity).not.toBeNull();
    fireEvent.click(within(activity as HTMLElement).getByText("旧目录索引活动"));

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
