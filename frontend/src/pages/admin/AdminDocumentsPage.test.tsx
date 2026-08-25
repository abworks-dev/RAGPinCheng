import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDocumentsPage } from "./AdminDocumentsPage";

const mocks = vi.hoisted(() => ({
  publicationJobs: vi.fn(),
  retryPublicationJob: vi.fn(),
  managedCategories: vi.fn(),
  publishManagedContent: vi.fn(),
  bulkPublishManagedContent: vi.fn(),
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
  retryable: true,
  task_type: "document",
  task_type_label: "普通资料",
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
  is_archived: false,
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
    mocks.publicationJobs.mockResolvedValue({
      jobs: [{ ...failedJob, task_type: "document", task_type_label: "普通资料", status: "failed" }],
      total: 1,
      status_counts: { processing: 2, published: 5, failed: 1 },
    });
    mocks.managedCategories.mockResolvedValue(categories);
    mocks.publishManagedContent.mockResolvedValue({ publication_id: "publication-2", index_job_id: "job-2", status: "pending" });
    mocks.bulkPublishManagedContent.mockResolvedValue({ results: [{ version_id: "version-1", status: "succeeded", message: null, index_job_id: "job-2" }], succeeded: 1, failed: 0 });
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
    expect(screen.getByRole("heading", { name: "发布任务" })).toBeInTheDocument();
    expect(within(row).getByText("发布失败")).toHaveClass("bg-destructive/15");
    expect(within(row).getByText(/PDF/)).toBeInTheDocument();
    expect(within(row).getByText("PDF").parentElement).toHaveClass("flex", "w-20", "flex-col");
    expect(within(row).getByText(/分类：03 公司内部标准 \/ 01 建模标准/)).toBeInTheDocument();
    expect(within(row).getByText("managed-document.pdf · v3 · 2.0 KB")).toBeInTheDocument();
    expect(within(row).getByText(/历史迁移/)).toBeInTheDocument();
    expect(within(row).getByText("文档解析服务请求失败。")).toBeInTheDocument();
    expect(within(row).getByText("可以重新发布")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "查看文件" })).toHaveAttribute("href", "/managed-files/version-1");
    expect(within(row).getByRole("button", { name: "重新发布" })).toBeEnabled();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.queryByText(/当前共/)).not.toBeInTheDocument();
    expect(screen.getByText("共 1 条任务，第 1 / 1 页")).toBeInTheDocument();
  });

  it("uses section headings when embedded in content management", async () => {
    render(<AdminDocumentsPage embedded />);

    expect(await screen.findByRole("heading", { name: "发布任务", level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "资料发布任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("sends search, database category, file type, source and grouped status filters", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索发布任务" }), { target: { value: "管综" } });
    fireEvent.click(screen.getByRole("button", { name: "展开发布任务筛选" }));
    fireEvent.change(screen.getByRole("combobox", { name: "按数据库分类筛选" }), { target: { value: "cat-03" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按文件类型筛选" }), { target: { value: "pdf" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按资料来源筛选" }), { target: { value: "legacy" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按发布状态筛选" }), { target: { value: "processing" } });

    await waitFor(() => expect(mocks.publicationJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "管综",
      category_id: "cat-03",
      doc_type: "pdf",
      source_origin: "legacy",
      status: "processing",
      history: false,
      include_archived: false,
      limit: 25,
      offset: 0,
    })));
  });

  it("switches between latest and historical attempts", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.click(screen.getByRole("button", { name: "展开发布任务筛选" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "查看历史尝试" }));

    await waitFor(() => expect(mocks.publicationJobs).toHaveBeenLastCalledWith(expect.objectContaining({ history: true })));
    expect(screen.getByText("正在显示全部历史尝试。 默认不显示回收站资料。")).toBeInTheDocument();
    expect(screen.getByText("共尝试 4 次 · 当前第 4 次")).toBeInTheDocument();
  });

  it("includes archived jobs on demand and marks them as withdrawn", async () => {
    mocks.publicationJobs.mockImplementation((params) => Promise.resolve(
      params?.include_archived
        ? { ...listing, jobs: [{ ...failedJob, is_archived: true }] }
        : { jobs: [], total: 0, status_counts: { processing: 0, ready: 0, failed: 0 } },
    ));
    render(<AdminDocumentsPage />);

    expect(await screen.findByText("暂无发布任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开发布任务筛选" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "包含回收站资料" }));

    const row = (await screen.findByText(failedJob.title)).closest("tr") as HTMLElement;
    expect(within(row).getByText("已下架")).toBeInTheDocument();
    expect(within(row).getByText("资料已移入回收站，不参与知识库检索")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "重新发布" })).toBeDisabled();
    expect(screen.getByText(/已包含回收站资料/)).toBeInTheDocument();
    expect(mocks.publicationJobs).toHaveBeenLastCalledWith(expect.objectContaining({ include_archived: true }));
  });

  it("clears the visible publication filters together", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    const search = screen.getByRole("searchbox", { name: "搜索发布任务" });
    fireEvent.click(screen.getByRole("button", { name: "展开发布任务筛选" }));
    const category = screen.getByRole("combobox", { name: "按数据库分类筛选" });
    const type = screen.getByRole("combobox", { name: "按文件类型筛选" });
    const source = screen.getByRole("combobox", { name: "按资料来源筛选" });
    fireEvent.change(search, { target: { value: "管综" } });
    fireEvent.change(category, { target: { value: "cat-03" } });
    fireEvent.change(type, { target: { value: "pdf" } });
    fireEvent.change(source, { target: { value: "legacy" } });
    fireEvent.change(screen.getByRole("combobox", { name: "按发布状态筛选" }), { target: { value: "failed" } });
    fireEvent.click(screen.getByRole("button", { name: "清除搜索与筛选" }));

    expect(search).toHaveValue("");
    expect(category).toHaveValue("");
    expect(type).toHaveValue("");
    expect(source).toHaveValue("");
    await waitFor(() => expect(mocks.publicationJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      query: undefined,
      category_id: undefined,
      doc_type: undefined,
      source_origin: undefined,
      status: undefined,
      include_archived: false,
    })));
  });

  it("closes search filters with Escape and restores focus to search", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    const search = screen.getByRole("searchbox", { name: "搜索发布任务" });
    fireEvent.click(screen.getByRole("button", { name: "展开发布任务筛选" }));
    expect(screen.getByRole("dialog", { name: "发布任务搜索筛选" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: "发布任务搜索筛选" })).not.toBeInTheDocument();
    expect(search).toHaveFocus();
  });

  it("shows current-head indexing state and Parent count", async () => {
    mocks.publicationJobs.mockResolvedValue({
      ...listing,
      jobs: [{
        ...failedJob,
        id: "job-ready",
        status: "published",
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
    expect(within(row).getByText(/内容块：17 个/)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "重新发布" })).toBeEnabled();
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

  it("selects and clears the current page from the header checkbox", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    const selectPage = screen.getByRole("checkbox", { name: "全选当前页索引任务" });
    const selectJob = screen.getByRole("checkbox", { name: `选择${failedJob.title}` });
    fireEvent.click(selectPage);
    expect(selectJob).toBeChecked();
    expect(screen.getByRole("button", { name: "批量操作（1）" })).toBeInTheDocument();

    fireEvent.click(selectPage);
    expect(selectJob).not.toBeChecked();
    expect(screen.getByRole("button", { name: "批量操作" })).toBeInTheDocument();
  });

  it("allows the latest published task to be republished", async () => {
    mocks.publicationJobs.mockResolvedValue({
      ...listing,
      jobs: [{ ...failedJob, id: "job-published", status: "published", failure: null }],
    });
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    const republish = screen.getByRole("button", { name: "重新发布" });
    expect(republish).toBeEnabled();
    fireEvent.click(republish);
    expect(mocks.publishManagedContent).toHaveBeenCalledWith("version-1");
  });

  it("places bulk actions after refresh", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    const refresh = screen.getByRole("button", { name: "刷新列表" });
    const bulk = screen.getByRole("button", { name: "批量操作" });
    expect(refresh.compareDocumentPosition(bulk) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("republishes eligible selected tasks from the bulk actions menu", async () => {
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.click(screen.getByRole("checkbox", { name: `选择${failedJob.title}` }));
    fireEvent.click(screen.getByRole("button", { name: "批量操作（1）" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量重新发布" }));

    expect(mocks.bulkPublishManagedContent).toHaveBeenCalledWith(["version-1"]);
    await waitFor(() => expect(mocks.toastSuccess).toHaveBeenCalledWith("已提交 1 个任务"));
  });

  it("reports retry failures and hides retry on an older attempt", async () => {
    mocks.publishManagedContent.mockRejectedValueOnce(new Error("资料状态已变化"));
    const { rerender } = render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);
    fireEvent.click(screen.getByRole("button", { name: "重新发布" }));
    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("资料状态已变化"));
    expect(screen.getByRole("button", { name: "重新发布" })).toBeEnabled();

    mocks.publicationJobs.mockResolvedValue({
      ...listing,
      jobs: [{ ...failedJob, is_latest_attempt: false }],
    });
    rerender(<AdminDocumentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "刷新列表" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "重新发布" })).toBeDisabled());
  });

  it("keeps republishing unavailable without publish permission", async () => {
    mocks.useAuth.mockReturnValue({
      state: { status: "authed", user: { role: "user", content_permissions: ["index.view"] } },
    });
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    expect(screen.getByRole("button", { name: "重新发布" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "查看文件" })).toBeInTheDocument();
  });

  it("supports server-side pagination", async () => {
    mocks.publicationJobs.mockResolvedValue({ ...listing, total: 26 });
    render(<AdminDocumentsPage />);
    await screen.findByText(failedJob.title);

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    await waitFor(() => expect(mocks.publicationJobs).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25 })));
    expect(screen.getByRole("combobox", { name: "跳转发布任务页码" })).toHaveValue("2");

    fireEvent.change(screen.getByRole("combobox", { name: "每页发布任务条数" }), { target: { value: "50" } });
    await waitFor(() => expect(mocks.publicationJobs).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 50, offset: 0 })));
  });

  it("keeps an explicit recoverable error state", async () => {
    mocks.publicationJobs.mockRejectedValueOnce(new Error("服务暂不可用"));
    render(<AdminDocumentsPage />);

    expect(await screen.findByText("发布任务加载失败")).toBeInTheDocument();
    expect(screen.getByText("服务暂不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });
});

