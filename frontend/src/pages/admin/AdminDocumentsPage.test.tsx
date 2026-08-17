import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDocumentsPage } from "./AdminDocumentsPage";

const mocks = vi.hoisted(() => ({
  managedContentIndexJobs: vi.fn(),
  managedCategories: vi.fn(),
  publishManagedContent: vi.fn(),
  managedContentFileUrl: vi.fn((versionId: string) => `/managed-files/${versionId}`),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  useAuth: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: mocks }));
vi.mock("../../components/ui/toast", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));
vi.mock("../../context/AuthContext", () => ({ useAuth: mocks.useAuth }));

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
    recommended_action: "请稍后在资料管理重新发布。",
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
  category_path: "03 公司内部标准 / 01 建模标准",
  version_number: 3,
  file_size: 2048,
  source_origin: "legacy",
  is_current_head: false,
  is_latest_attempt: true,
  parent_count: null,
  preview_parent_id: null,
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
    mocks.publishManagedContent.mockResolvedValue({ publication_id: "publication-2", index_job_id: "job-2", status: "pending" });
    mocks.useAuth.mockReturnValue({
      state: { status: "authed", user: { role: "admin", content_permissions: [] } },
    });
  });

  it("shows only the managed publication workbench and structured failures", async () => {
    render(<AdminDocumentsPage />);

    expect(screen.getByText("正在加载发布任务…")).toBeInTheDocument();
    const title = await screen.findByText(failedJob.title);
    const row = title.closest("tr") as HTMLElement;
    expect(screen.queryByText("旧索引资料")).not.toBeInTheDocument();
    expect(screen.queryByText("索引活动")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "索引任务" })).toBeInTheDocument();
    expect(within(row).getByText("发布失败")).toHaveClass("bg-destructive/15");
    expect(within(row).getByText(/PDF/)).toBeInTheDocument();
    expect(within(row).getByText("03 公司内部标准 / 01 建模标准")).toBeInTheDocument();
    expect(within(row).getByText("managed-document.pdf · v3 · 2.0 KB")).toBeInTheDocument();
    expect(within(row).getByText(/历史迁移/)).toBeInTheDocument();
    expect(within(row).getByText("文档解析服务请求失败。")).toBeInTheDocument();
    expect(within(row).getByText("可以重新发布")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "查看文件" })).toHaveAttribute("href", "/managed-files/version-1");
    expect(within(row).getByRole("button", { name: "重新发布" })).toBeEnabled();
    expect(screen.getByText("8")).toBeInTheDocument();
  });

  it("uses section headings when embedded in content management", async () => {
    render(<AdminDocumentsPage embedded />);

    expect(await screen.findByRole("heading", { name: "索引任务", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "资料发布任务", level: 3 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("sends search, database category, file type, source and grouped status filters", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索发布任务" }), { target: { value: "管综" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按数据库分类筛选" }), { target: { value: "cat-03" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按文件类型筛选" }), { target: { value: "pdf" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按资料来源筛选" }), { target: { value: "legacy" } });
    fireEvent.click(screen.getByRole("button", { name: "处理中" }));

    await waitFor(() => expect(mocks.managedContentIndexJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "管综",
      category_id: "cat-03",
      doc_type: "pdf",
      source_origin: "legacy",
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

  it("clears the visible publication filters together", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    const search = screen.getByRole("searchbox", { name: "搜索发布任务" });
    const category = screen.getByRole("combobox", { name: "按数据库分类筛选" });
    const type = screen.getByRole("combobox", { name: "按文件类型筛选" });
    const source = screen.getByRole("combobox", { name: "按资料来源筛选" });
    fireEvent.change(search, { target: { value: "管综" } });
    fireEvent.change(category, { target: { value: "cat-03" } });
    fireEvent.change(type, { target: { value: "pdf" } });
    fireEvent.change(source, { target: { value: "legacy" } });
    fireEvent.click(screen.getByRole("button", { name: "发布失败" }));
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));

    expect(search).toHaveValue("");
    expect(category).toHaveValue("");
    expect(type).toHaveValue("");
    expect(source).toHaveValue("");
    await waitFor(() => expect(mocks.managedContentIndexJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      query: undefined,
      category_id: undefined,
      doc_type: undefined,
      source_origin: undefined,
      status: undefined,
    })));
  });

  it("shows current-head indexing state and Parent count", async () => {
    mocks.managedContentIndexJobs.mockResolvedValue({
      ...listing,
      jobs: [{
        ...failedJob,
        id: "job-ready",
        status: "done",
        failure: null,
        error_code: null,
        error_summary: null,
        is_current_head: true,
        parent_count: 17,
        preview_parent_id: "parent-preview",
      }],
      status_counts: { processing: 0, ready: 1, failed: 0 },
    });
    render(<AdminDocumentsPage />);

    const row = (await screen.findByText(failedJob.title)).closest("tr") as HTMLElement;
    expect(within(row).getByText("当前正式版本可检索")).toBeInTheDocument();
    expect(within(row).getByText("17 个")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "重新发布" })).not.toBeInTheDocument();
  });

  it("requeues only the latest failed attempt and exposes a busy success state", async () => {
    let resolvePublish!: (value: { publication_id: string; index_job_id: string; status: string }) => void;
    mocks.publishManagedContent.mockReturnValue(new Promise((resolve) => { resolvePublish = resolve; }));
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.click(screen.getByRole("button", { name: "重新发布" }));
    expect(screen.getByRole("button", { name: "发布中…" })).toBeDisabled();
    expect(mocks.publishManagedContent).toHaveBeenCalledWith("version-1");
    resolvePublish({ publication_id: "publication-2", index_job_id: "job-2", status: "pending" });

    await waitFor(() => expect(mocks.toastSuccess).toHaveBeenCalledWith("已重新加入发布队列"));
    await waitFor(() => expect(screen.getByRole("button", { name: "重新发布" })).toBeEnabled());
  });

  it("reports retry failures and hides retry on an older attempt", async () => {
    mocks.publishManagedContent.mockRejectedValueOnce(new Error("资料状态已变化"));
    const { rerender } = render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);
    fireEvent.click(screen.getByRole("button", { name: "重新发布" }));
    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("资料状态已变化"));
    expect(screen.getByRole("button", { name: "重新发布" })).toBeEnabled();

    mocks.managedContentIndexJobs.mockResolvedValue({
      ...listing,
      jobs: [{ ...failedJob, is_latest_attempt: false }],
    });
    rerender(<AdminDocumentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "重新发布" })).not.toBeInTheDocument());
  });

  it("keeps republishing unavailable without publish permission", async () => {
    mocks.useAuth.mockReturnValue({
      state: { status: "authed", user: { role: "user", content_permissions: ["index.view"] } },
    });
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    expect(screen.queryByRole("button", { name: "重新发布" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看文件" })).toBeInTheDocument();
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
