import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminManagedContentPage } from "./AdminManagedContentPage";

const mocks = vi.hoisted(() => ({
  permissions: ["review"] as string[],
  capabilities: vi.fn(),
  categories: vi.fn(),
  items: vi.fn(),
  upload: vi.fn(),
  submit: vi.fn(),
  review: vi.fn(),
  publish: vi.fn(),
  bulkReview: vi.fn(),
  bulkPublish: vi.fn(),
  fileUrl: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: {
        id: 2,
        employee_id: "reviewer",
        real_name: "负责人",
        role: "user",
        csrf_token: "csrf",
        content_permissions: mocks.permissions,
      },
    },
  }),
}));

vi.mock("../../api/client", () => ({
  api: {
    managedContentCapabilities: mocks.capabilities,
    managedCategories: mocks.categories,
    managedContentItems: mocks.items,
    uploadManagedContent: mocks.upload,
    submitManagedContent: mocks.submit,
    reviewManagedContent: mocks.review,
    publishManagedContent: mocks.publish,
    bulkReviewManagedContent: mocks.bulkReview,
    bulkPublishManagedContent: mocks.bulkPublish,
    managedContentFileUrl: mocks.fileUrl,
  },
}));

vi.mock("../../components/ui/toast", () => ({
  toast: { success: mocks.success, error: mocks.error },
}));

const category = {
  id: "cat-03",
  category_key: "company_standards",
  parent_id: null,
  display_code: "03",
  display_name: "公司内部标准",
  sort_order: 30,
  level: 1,
  is_active: true,
  version: 1,
  created_at: 1,
  updated_at: 1,
  full_path: "03 公司内部标准",
  item_count: 1,
};

const item = {
  item_id: "item-1",
  title: "建模标准",
  content_kind: "document",
  category_id: "cat-03",
  category_key: "company_standards",
  category_label: "03 公司内部标准",
  category_path: "03 公司内部标准",
  media_id: null,
  version_id: "version-1",
  version_number: 1,
  original_filename: "standard.pdf",
  doc_type: "pdf",
  lifecycle_status: "awaiting_review",
  object_sha256: "a".repeat(64),
  source_origin: "web",
  source_batch_id: "batch-1",
  is_current: false,
  latest_publication_status: null,
  publication_attempt_count: 0,
  publication_failure: null,
  created_at: 1,
  updated_at: 1,
};

describe("AdminManagedContentPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permissions = ["review"];
    mocks.capabilities.mockResolvedValue({ enabled: true, max_upload_bytes: 1024, supported_extensions: [".pdf"] });
    mocks.categories.mockResolvedValue([category]);
    mocks.items.mockResolvedValue({ items: [item], total: 1, status_counts: { awaiting_review: 1 } });
    mocks.review.mockResolvedValue({ ...item, lifecycle_status: "approved" });
  });

  it("shows only review actions to a reviewer and submits the decision", async () => {
    render(<AdminManagedContentPage />);
    expect((await screen.findAllByText("建模标准")).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("选择资料文件")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布" })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "确认" })[0]);
    await waitFor(() => expect(mocks.review).toHaveBeenCalledWith("version-1", true));
  });

  it("uploads selected files for an organizer", async () => {
    mocks.permissions = ["organize"];
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.upload.mockResolvedValue({
      batch_id: "batch-1",
      entries: [{ filename: "guide.md", item_id: "item-2", version_id: "version-2", sha256: "b".repeat(64), status: "accepted", reason: null }],
    });
    render(<AdminManagedContentPage />);
    const input = await screen.findByLabelText("选择资料文件");
    const file = new File(["# Guide"], "guide.md", { type: "text/markdown" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByText("已选择 1 个文件")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "3. 上传" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith([file], "cat-03"));
    expect(await screen.findByText("已接收")).toBeInTheDocument();
  });

  it("shows loading, empty, and recoverable error states", async () => {
    let resolveCapabilities: ((value: { enabled: boolean; max_upload_bytes: number; supported_extensions: string[] }) => void) | undefined;
    mocks.capabilities.mockReturnValueOnce(new Promise((resolve) => { resolveCapabilities = resolve; }));
    render(<AdminManagedContentPage />);
    expect(screen.getByText("正在加载资料…")).toBeInTheDocument();
    resolveCapabilities?.({ enabled: true, max_upload_bytes: 1024, supported_extensions: [".pdf"] });
    expect((await screen.findAllByText("建模标准")).length).toBeGreaterThan(0);

    mocks.capabilities.mockRejectedValueOnce(new Error("资料服务暂不可用"));
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("资料服务暂不可用");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("disables workflow actions and exposes the busy label while saving", async () => {
    let resolveReview: ((value: typeof item) => void) | undefined;
    mocks.review.mockReturnValueOnce(new Promise((resolve) => { resolveReview = resolve; }));
    render(<AdminManagedContentPage />);
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("button", { name: "确认" })[0]);
    expect(screen.getAllByRole("button", { name: "确认中…" })[0]).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "退回" })[0]).toBeDisabled();
    resolveReview?.({ ...item, lifecycle_status: "approved" });
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith("资料已确认"));
  });

  it("keeps failed bulk items selected and shows their per-item reason", async () => {
    mocks.bulkReview.mockResolvedValue({
      results: [{ version_id: "version-1", status: "failed", message: "资料状态已变化，请刷新后重试", index_job_id: null }],
      succeeded: 0,
      failed: 1,
    });
    render(<AdminManagedContentPage />);
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量确认" }));
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("建模标准：资料状态已变化，请刷新后重试");
    expect(screen.getByRole("button", { name: "重试失败项" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]).toBeChecked();
  });

  it("allows republishing after a non-retryable historical failure", async () => {
    mocks.permissions = ["publish"];
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "publication_failed", publication_attempt_count: 4, publication_failure: { code: "pdf_password_required", message: "PDF 需要密码才能解析。", retryable: false, recommended_action: "请上传已解除密码保护的 PDF。" } }], total: 1, status_counts: { publication_failed: 1 } });
    mocks.publish.mockResolvedValue({});
    render(<AdminManagedContentPage />);
    expect((await screen.findAllByText("PDF 需要密码才能解析。")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("请上传已解除密码保护的 PDF。").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/系统或文件处理后可重新发布/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/共尝试 4 次/).length).toBeGreaterThan(0);
    expect(screen.queryByText("pdf_password_required")).not.toBeInTheDocument();
    const republish = screen.getAllByRole("button", { name: "重新发布" })[0];
    expect(republish).toBeEnabled();
    fireEvent.click(republish);
    await waitFor(() => expect(mocks.publish).toHaveBeenCalledWith("version-1"));
  });

  it("includes non-retryable historical failures in bulk republish", async () => {
    mocks.permissions = ["publish"];
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "publication_failed", publication_attempt_count: 2, publication_failure: { code: "parser_result_invalid", message: "文档解析结果无效。", retryable: false, recommended_action: "请确认文件内容完整。" } }], total: 1, status_counts: { publication_failed: 1 } });
    mocks.bulkPublish.mockResolvedValue({ results: [{ version_id: "version-1", status: "succeeded", message: null, index_job_id: "job-3" }], succeeded: 1, failed: 0 });
    render(<AdminManagedContentPage />);
    await screen.findAllByText("文档解析结果无效。");
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量发布" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("本次将处理 1 份符合条件的资料。");
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    await waitFor(() => expect(mocks.bulkPublish).toHaveBeenCalledWith(["version-1"]));
  });
});
