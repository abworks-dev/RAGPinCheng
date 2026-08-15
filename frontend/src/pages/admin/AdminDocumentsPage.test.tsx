import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDocumentsPage } from "./AdminDocumentsPage";

const mocks = vi.hoisted(() => ({
  managedContentIndexJobs: vi.fn(),
  managedCategories: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: mocks }));

const categories = [{
  id: "cat-03",
  category_key: "company",
  parent_id: null,
  display_code: "03",
  display_name: "公司内部标准",
  sort_order: 3,
  level: 1,
  is_active: true,
  version: 1,
  created_at: 1,
  updated_at: 1,
  full_path: "03 公司内部标准",
  item_count: 2,
}];

const failedJob = {
  id: "job-1",
  publication_id: "publication-1",
  version_id: "version-1",
  attempt_number: 4,
  status: "failed",
  error_code: "parser_request_failed",
  error_summary: "文档解析服务请求失败。",
  failure: {
    code: "parser_request_failed",
    message: "文档解析服务请求失败。",
    retryable: true,
    recommended_action: "请稍后在资料库重新发布。",
  },
  attempt_count: 4,
  created_at: 1785686400,
  started_at: 1785686401,
  finished_at: 1785686410,
  updated_at: 1785686410,
  title: "超长中文资料名称用于验证发布任务列表不会遮挡相邻内容",
  original_filename: "managed-document.pdf",
  doc_type: "pdf",
  category_id: "cat-03",
  category_label: "03 公司内部标准",
};

const listing = {
  jobs: [failedJob],
  total: 1,
  status_counts: { processing: 2, ready: 5, failed: 1 },
};

describe("AdminDocumentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.managedCategories.mockResolvedValue(categories);
    mocks.managedContentIndexJobs.mockResolvedValue(listing);
  });

  it("shows only the managed publication workbench and structured failures", async () => {
    render(<AdminDocumentsPage />);

    expect(screen.getByText("正在加载发布任务…")).toBeInTheDocument();
    const title = await screen.findByText(failedJob.title);
    const row = title.closest("tr") as HTMLElement;
    expect(screen.queryByText("旧索引资料")).not.toBeInTheDocument();
    expect(screen.queryByText("索引活动")).not.toBeInTheDocument();
    expect(within(row).getByText("发布失败")).toHaveClass("bg-destructive/15");
    expect(within(row).getByText(/PDF/)).toBeInTheDocument();
    expect(within(row).getByText("文档解析服务请求失败。")).toBeInTheDocument();
    expect(within(row).getByText("可在资料库重新发布")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
  });

  it("sends search, database category, file type and grouped status filters", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索发布任务" }), { target: { value: "管综" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按数据库分类筛选" }), { target: { value: "cat-03" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按文件类型筛选" }), { target: { value: "pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "处理中" }));

    await waitFor(() => expect(mocks.managedContentIndexJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "管综",
      category_id: "cat-03",
      doc_type: "pdf",
      status: "processing",
      history: false,
      limit: 25,
      offset: 0,
    })));
  });

  it("switches between latest and historical attempts", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.click(screen.getByRole("button", { name: "查看历史尝试" }));

    await waitFor(() => expect(mocks.managedContentIndexJobs).toHaveBeenLastCalledWith(expect.objectContaining({ history: true })));
    expect(screen.getByText("正在显示全部历史尝试。")).toBeInTheDocument();
    expect(screen.getByText("共尝试 4 次 · 当前第 4 次")).toBeInTheDocument();
  });

  it("supports server-side pagination", async () => {
    mocks.managedContentIndexJobs.mockResolvedValue({ ...listing, total: 26 });
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    await waitFor(() => expect(mocks.managedContentIndexJobs).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25 })));
  });

  it("keeps an explicit recoverable error state", async () => {
    mocks.managedContentIndexJobs.mockRejectedValueOnce(new Error("服务暂不可用"));
    render(<AdminDocumentsPage />);

    expect(await screen.findByText("发布任务加载失败")).toBeInTheDocument();
    expect(screen.getByText("服务暂不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });
});
