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
}));

vi.mock("../../api/client", () => ({
  api: mocks,
}));

const categoryTree = {
  categories: [
    { name: "企业标准", two_level: false, subcategories: [] },
    { name: "客户标准", two_level: true, subcategories: ["甲方", "乙方"] },
  ],
  second_level_categories: ["客户标准"],
};

const documents = [
  {
    source_path: "corpus/company-standard.pdf",
    doc_title: "企业交付标准",
    category: "企业标准",
    doc_type: "pdf",
    company: null,
    parent_count: 12,
  },
  {
    source_path: "corpus/customer-budget.xlsx",
    doc_title: "客户预算模板",
    category: "客户标准",
    doc_type: "xlsx",
    company: "甲方",
    parent_count: 5,
  },
];

const baseJob = {
  user_id: 1,
  employee_id: "A001",
  real_name: "管理员",
  category: "企业标准",
  doc_type: "pdf",
  source_path: "corpus/company-standard.pdf",
  file_size: 2048,
  error: null,
  parents: null,
  children: null,
  created_at: 1785686400,
  started_at: null,
  finished_at: null,
};

const jobs = [
  {
    ...baseJob,
    id: 1,
    filename: "company-standard.pdf",
    status: "done",
    parents: 12,
    children: 36,
    started_at: 1785686401,
    finished_at: 1785686410,
  },
  {
    ...baseJob,
    id: 2,
    filename: "failed-document.docx",
    doc_type: "docx",
    status: "failed",
    error: "解析器暂不可用",
    started_at: 1785686401,
    finished_at: 1785686410,
  },
  {
    ...baseJob,
    id: 3,
    filename: "processing.xlsx",
    doc_type: "xlsx",
    status: "embedding",
    started_at: Math.floor(Date.now() / 1000),
  },
  {
    ...baseJob,
    id: 4,
    filename: "unknown.bin",
    status: "paused",
  },
];

describe("AdminDocumentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adminCategoryTree.mockResolvedValue(categoryTree);
    mocks.adminListIndexedDocuments.mockResolvedValue({ documents });
    mocks.adminListIndexJobs.mockResolvedValue({ jobs });
    mocks.adminUploadDocuments.mockResolvedValue({ accepted: ["a", "b"], skipped: [] });
    mocks.adminDeleteIndexedDocument.mockResolvedValue(undefined);
    mocks.adminRetryIndexJob.mockResolvedValue(undefined);
    mocks.adminDeleteIndexJob.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("loads the three existing data sources and presents semantic document and job states", async () => {
    render(<AdminDocumentsPage />);

    expect(screen.getByText("正在加载已索引资料…")).toBeInTheDocument();
    expect(screen.getByText("正在加载索引任务…")).toBeInTheDocument();
    expect(await screen.findByText("企业交付标准")).toBeInTheDocument();

    expect(mocks.adminCategoryTree).toHaveBeenCalledTimes(1);
    expect(mocks.adminListIndexedDocuments).toHaveBeenCalledTimes(1);
    expect(mocks.adminListIndexJobs).toHaveBeenCalledWith(100);
    expect(screen.getByText("Excel 表格")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toHaveClass("bg-success/15");
    expect(screen.getByText("失败")).toHaveClass("bg-destructive/15");
    expect(screen.getByText(/嵌入中/)).toHaveClass("bg-warning/15");
    expect(screen.getByText("paused")).toHaveClass("bg-secondary");
    expect(screen.getByText("解析器暂不可用")).toHaveClass("text-destructive");
  });

  it("shows a unified loading error and retries the unchanged three-call contract", async () => {
    mocks.adminCategoryTree
      .mockRejectedValueOnce(new Error("资料服务暂不可用"))
      .mockResolvedValueOnce(categoryTree);

    render(<AdminDocumentsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("资料与索引数据加载失败");
    expect(screen.getByRole("alert")).toHaveTextContent("资料服务暂不可用");

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));

    await waitFor(() => expect(mocks.adminCategoryTree).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("企业交付标准")).toBeInTheDocument();
    expect(mocks.adminListIndexedDocuments).toHaveBeenCalledTimes(2);
    expect(mocks.adminListIndexJobs).toHaveBeenNthCalledWith(2, 100);
  });

  it("uploads multiple files with the selected category and subcategory, then refreshes all data", async () => {
    const pdf = new File(["pdf"], "standard.pdf", { type: "application/pdf" });
    const docx = new File(["docx"], "guide.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });

    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.change(screen.getByLabelText("分类"), { target: { value: "客户标准" } });
    expect(screen.getByLabelText("客户")).toHaveValue("甲方");

    const fileInput = screen.getByLabelText("文件");
    fireEvent.change(fileInput, { target: { files: [pdf, docx] } });
    expect(screen.getByText(/已选择 2 个文件/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "上传 2 个文件" }));

    await waitFor(() =>
      expect(mocks.adminUploadDocuments).toHaveBeenCalledWith(
        [pdf, docx],
        "客户标准",
        "甲方",
      ),
    );
    expect(await screen.findByText("资料已加入索引队列")).toBeInTheDocument();
    expect(screen.getByText("尚未选择文件")).toBeInTheDocument();
    expect(fileInput).toHaveValue("");
    expect(mocks.adminCategoryTree).toHaveBeenCalledTimes(2);
    expect(mocks.adminListIndexedDocuments).toHaveBeenCalledTimes(2);
    expect(mocks.adminListIndexJobs).toHaveBeenCalledTimes(2);
  });

  it("keeps selected files and shows inline feedback when upload fails", async () => {
    mocks.adminUploadDocuments.mockRejectedValueOnce(new Error("上传服务暂不可用"));
    const file = new File(["pdf"], "standard.pdf", { type: "application/pdf" });

    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.change(screen.getByLabelText("文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("资料上传失败");
    expect(screen.getByRole("alert")).toHaveTextContent("上传服务暂不可用");
    expect(screen.getByTitle("standard.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传 1 个文件" })).toBeEnabled();
    expect(mocks.adminCategoryTree).toHaveBeenCalledTimes(1);
  });

  it("filters documents and preserves the two-step delete confirmation parameters", async () => {
    const confirmSpy = vi.spyOn(window, "confirm")
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(false);

    render(<AdminDocumentsPage />);
    await screen.findByText("企业交付标准");

    fireEvent.change(screen.getByLabelText("筛选已索引资料"), {
      target: { value: "预算" },
    });
    expect(screen.queryByText("企业交付标准")).not.toBeInTheDocument();
    expect(screen.getByText("客户预算模板")).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();

    const row = screen.getByText("客户预算模板").closest("tr");
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByRole("button", { name: "删除资料" }));

    await waitFor(() =>
      expect(mocks.adminDeleteIndexedDocument).toHaveBeenCalledWith(
        "corpus/customer-budget.xlsx",
        false,
      ),
    );
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(mocks.adminCategoryTree).toHaveBeenCalledTimes(2);
  });

  it("retries completed jobs and only deletes terminal job records after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminDocumentsPage />);
    await screen.findByText("company-standard.pdf");

    const completedRow = screen.getByText("company-standard.pdf").closest("tr");
    const activeRow = screen.getByText("processing.xlsx").closest("tr");
    expect(completedRow).not.toBeNull();
    expect(activeRow).not.toBeNull();
    expect(within(activeRow as HTMLElement).queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(within(activeRow as HTMLElement).queryByRole("button", { name: "删除记录" })).not.toBeInTheDocument();

    fireEvent.click(within(completedRow as HTMLElement).getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mocks.adminRetryIndexJob).toHaveBeenCalledWith(1));

    const refreshedCompletedRow = screen.getByText("company-standard.pdf").closest("tr");
    expect(refreshedCompletedRow).not.toBeNull();
    fireEvent.click(within(refreshedCompletedRow as HTMLElement).getByRole("button", { name: "删除记录" }));
    await waitFor(() => expect(mocks.adminDeleteIndexJob).toHaveBeenCalledWith(1));
  });

  it("polls active jobs every three seconds and refreshes documents when a job completes", async () => {
    vi.useFakeTimers();
    const activeJob = { ...jobs[2], status: "embedding" };
    const completedJob = {
      ...activeJob,
      status: "done",
      parents: 8,
      children: 24,
      finished_at: 1785686410,
    };
    mocks.adminListIndexJobs
      .mockResolvedValueOnce({ jobs: [activeJob] })
      .mockResolvedValueOnce({ jobs: [completedJob] });

    render(<AdminDocumentsPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText(/嵌入中/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(mocks.adminListIndexJobs).toHaveBeenCalledTimes(2);
    expect(mocks.adminListIndexedDocuments).toHaveBeenCalledTimes(2);
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });

  it("shows unified empty states for documents and index jobs", async () => {
    mocks.adminListIndexedDocuments.mockResolvedValue({ documents: [] });
    mocks.adminListIndexJobs.mockResolvedValue({ jobs: [] });

    render(<AdminDocumentsPage />);

    expect(await screen.findByText("暂无已索引资料")).toBeInTheDocument();
    expect(screen.getByText("暂无索引任务")).toBeInTheDocument();
  });
});
