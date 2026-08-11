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
};

const item = {
  item_id: "item-1",
  title: "建模标准",
  content_kind: "document",
  category_id: "cat-03",
  category_key: "company_standards",
  category_label: "03 公司内部标准",
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
  created_at: 1,
  updated_at: 1,
};

describe("AdminManagedContentPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permissions = ["review"];
    mocks.capabilities.mockResolvedValue({ enabled: true, max_upload_bytes: 1024, supported_extensions: [".pdf"] });
    mocks.categories.mockResolvedValue([category]);
    mocks.items.mockResolvedValue([item]);
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
    mocks.items.mockResolvedValue([]);
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
});
