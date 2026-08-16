import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminManagedContentPage } from "./AdminManagedContentPage";

const mocks = vi.hoisted(() => ({
  permissions: ["item.review", "item.move_review", "folder.review", "trash.view", "trash.restore"] as string[],
  capabilities: vi.fn(),
  categories: vi.fn(),
  items: vi.fn(),
  upload: vi.fn(),
  uploadTasks: vi.fn(),
  uploadTask: vi.fn(),
  createCategory: vi.fn(),
  moveContent: vi.fn(),
  renameContent: vi.fn(),
  updateVersion: vi.fn(),
  folderRequests: vi.fn(),
  createFolderRequest: vi.fn(),
  reviewFolderRequest: vi.fn(),
  submit: vi.fn(),
  review: vi.fn(),
  publish: vi.fn(),
  bulkReview: vi.fn(),
  bulkPublish: vi.fn(),
  bulkMove: vi.fn(),
  bulkArchive: vi.fn(),
  bulkDownload: vi.fn(),
  downloadFile: vi.fn(),
  deleteContent: vi.fn(),
  trash: vi.fn(),
  restoreContent: vi.fn(),
  fileUrl: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  openPreview: vi.fn(),
  previewState: { parentId: null as string | null },
}));

const REVIEWER_PERMISSIONS = ["item.review", "item.move_review", "folder.review", "trash.view", "trash.restore", "item.download"];
const ORGANIZER_PERMISSIONS = ["item.upload", "item.submit", "item.move_draft", "item.archive_draft", "folder.request", "item.download"];
const PUBLISHER_PERMISSIONS = ["item.publish", "item.archive_published", "trash.view", "index.view", "item.download"];
const CATEGORY_MANAGER_PERMISSIONS = ["category.manage", "folder.review"];

vi.mock("../../components/PdfPreview", () => ({ PdfPreview: () => null }));
vi.mock("../../hooks/usePdfPreview", () => ({
  PdfPreviewProvider: ({ children }: { children: React.ReactNode }) => children,
  usePdfPreview: () => ({ open: mocks.openPreview, state: { ...mocks.previewState } }),
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
    managedUploadTasks: mocks.uploadTasks,
    managedUploadTask: mocks.uploadTask,
    createManagedCategory: mocks.createCategory,
    moveManagedContent: mocks.moveContent,
    renameManagedContent: mocks.renameContent,
    updateManagedContentVersion: mocks.updateVersion,
    managedFolderRequests: mocks.folderRequests,
    createFolderRequest: mocks.createFolderRequest,
    reviewFolderRequest: mocks.reviewFolderRequest,
    submitManagedContent: mocks.submit,
    reviewManagedContent: mocks.review,
    publishManagedContent: mocks.publish,
    bulkReviewManagedContent: mocks.bulkReview,
    bulkPublishManagedContent: mocks.bulkPublish,
    bulkMoveManagedContent: mocks.bulkMove,
    bulkArchiveManagedContent: mocks.bulkArchive,
    bulkDownloadManagedContent: mocks.bulkDownload,
    downloadManagedContentFile: mocks.downloadFile,
    deleteManagedContent: mocks.deleteContent,
    managedContentTrash: mocks.trash,
    restoreManagedContent: mocks.restoreContent,
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

const childCategory = {
  ...category,
  id: "cat-03-01",
  category_key: "company_modeling",
  parent_id: "cat-03",
  display_code: "01",
  display_name: "建模标准",
  level: 2,
  full_path: "03 公司内部标准 / 01 建模标准",
  item_count: 0,
};

const projectCategory = {
  ...category,
  id: "cat-04",
  category_key: "project_materials",
  display_code: "04",
  display_name: "项目资料",
  sort_order: 40,
  full_path: "04 项目资料",
  item_count: 0,
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
  preview_parent_id: "parent-1",
  version_id: "version-1",
  version_number: 1,
  original_filename: "standard.pdf",
  doc_type: "pdf",
  lifecycle_status: "awaiting_review",
  object_sha256: "a".repeat(64),
  source_origin: "web",
  source_batch_id: "batch-1",
  is_current: false,
  has_published_head: false,
  latest_publication_status: null,
  publication_attempt_count: 0,
  publication_failure: null,
  created_at: 1,
  updated_at: 1,
};

async function openRootFolder(folderId = category.id) {
  const folder = folderId === projectCategory.id ? projectCategory : category;
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`${folder.display_code} ${folder.display_name}`) }));
  await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({ category_id: folderId })));
}

describe("AdminManagedContentPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permissions = REVIEWER_PERMISSIONS;
    mocks.previewState.parentId = null;
    mocks.capabilities.mockResolvedValue({ enabled: true, max_upload_bytes: 1024, supported_extensions: [".pdf"] });
    mocks.categories.mockResolvedValue([category]);
    mocks.items.mockResolvedValue({ items: [item], total: 1, status_counts: { awaiting_review: 1 } });
    mocks.folderRequests.mockResolvedValue([]);
    mocks.review.mockResolvedValue({ ...item, lifecycle_status: "approved" });
    mocks.deleteContent.mockResolvedValue({ item_id: "item-1", version_id: "version-1", archived_at: 2, previous_status: "awaiting_review", publication_withdrawn: false });
    mocks.trash.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.restoreContent.mockResolvedValue({ item_id: "item-1", version_id: "version-1", restored_status: "approved" });
    mocks.uploadTasks.mockResolvedValue({ tasks: [], total: 0, status_counts: {} });
    mocks.uploadTask.mockResolvedValue({});
    window.history.replaceState({}, "", "/admin/content");
    mocks.bulkDownload.mockResolvedValue({ blob: new Blob(["zip"]), filename: "资料批量下载.zip" });
    mocks.downloadFile.mockResolvedValue({ blob: new Blob(["file"]), filename: "standard.pdf" });
  });

  it("shows only review actions to a reviewer and submits the decision", async () => {
    render(<AdminManagedContentPage />);
    await openRootFolder();
    expect((await screen.findAllByText("建模标准")).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("选择资料文件")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布" })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(mocks.review).toHaveBeenCalledWith("version-1", true));
  });

  it("keeps single and batch downloads disabled without item.download", async () => {
    mocks.permissions = REVIEWER_PERMISSIONS.filter((permission) => permission !== "item.download");
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    expect(screen.getAllByRole("button", { name: "下载“建模标准”" })[0]).toBeDisabled();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    expect(screen.getByRole("menuitem", { name: "批量下载" })).toBeDisabled();
  });

  it("shows a visible packaging status while a batch download is pending", async () => {
    let resolveDownload!: (result: { blob: Blob; filename: string }) => void;
    mocks.bulkDownload.mockReturnValueOnce(new Promise((resolve) => { resolveDownload = resolve; }));
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量下载" }));

    await waitFor(() => expect(mocks.bulkDownload).toHaveBeenCalledWith(["version-1", "version-2"]));
    await waitFor(() => expect(screen.getByTestId("bulk-download-status")).toHaveTextContent(/正在打包 2 份资料，请稍候/));
    expect(screen.getByTestId("bulk-download-status")).toHaveTextContent("文件较多时可能需要几秒");
    expect(screen.getByRole("button", { name: "批量操作" })).toBeDisabled();

    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:synthetic") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    resolveDownload({ blob: new Blob(["zip"]), filename: "资料批量下载.zip" });
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith("已打包 2 份资料并开始下载"));
    expect(screen.getByTestId("bulk-download-status")).not.toHaveTextContent(/正在打包 2 份资料，请稍候/);
  });

  it("clears the packaging status and reports a failed batch download", async () => {
    let rejectDownload!: (reason?: unknown) => void;
    mocks.bulkDownload.mockReturnValueOnce(new Promise((_, reject) => { rejectDownload = reject; }));
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量下载" }));
    await waitFor(() => expect(mocks.bulkDownload).toHaveBeenCalledWith(["version-1", "version-2"]));
    await waitFor(() => expect(screen.getByTestId("bulk-download-status")).toHaveTextContent(/正在打包 2 份资料，请稍候/));

    rejectDownload(new Error("打包服务暂时不可用"));
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("打包服务暂时不可用"));
    expect(screen.getByTestId("bulk-download-status")).not.toHaveTextContent(/正在打包 2 份资料，请稍候/);
  });

  it("opens indexed files directly and restores details after an in-dialog preview", async () => {
    const { rerender } = render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("button", { name: "查看“建模标准”" })[0]);
    expect(mocks.openPreview).toHaveBeenCalledWith("parent-1", "建模标准", "pdf", 1, {}, null);
    expect(screen.queryByRole("dialog", { name: "建模标准" })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0]);
    const updatedAtLabel = screen.getByText("最后更新时间");
    expect(updatedAtLabel.parentElement).toHaveClass("grid-cols-[max-content_minmax(0,1fr)]", "[&_dt]:whitespace-nowrap");
    fireEvent.click(screen.getByRole("button", { name: "预览文件" }));
    expect(mocks.openPreview).toHaveBeenLastCalledWith("parent-1", "建模标准", "pdf", 1, {}, "managed-content-detail");
    expect(screen.getByRole("button", { name: "预览文件" })).toBeInTheDocument();

    mocks.previewState.parentId = "parent-1";
    rerender(<AdminManagedContentPage />);
    expect(screen.queryByRole("dialog", { name: "建模标准" })).not.toBeInTheDocument();

    mocks.previewState.parentId = null;
    rerender(<AdminManagedContentPage />);
    expect(screen.getByRole("dialog", { name: "建模标准" })).toHaveAttribute("data-state", "open");
    expect(screen.getByRole("button", { name: "预览文件" })).toBeInTheDocument();
  });

  it("disables direct preview when an item has no previewable content", async () => {
    mocks.items.mockResolvedValue({
      items: [{ ...item, preview_parent_id: null }], total: 1, status_counts: { awaiting_review: 1 },
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const previewButton = screen.getAllByRole("button", { name: "查看“建模标准”（暂无可预览内容）" })[0];
    expect(previewButton).toBeDisabled();
    expect(previewButton).toHaveAttribute("title", "查看“建模标准”（暂无可预览内容）");
    expect(mocks.openPreview).not.toHaveBeenCalled();
  });

  it("shows minute-level update times and sorts them in both directions", async () => {
    const olderItem = { ...item, item_id: "item-older", version_id: "version-older", title: "较早资料", updated_at: 1_700_000_000 };
    const newerItem = { ...item, item_id: "item-newer", version_id: "version-newer", title: "较新资料", updated_at: 1_800_000_000 };
    mocks.items.mockResolvedValue({ items: [newerItem, olderItem], total: 2, status_counts: { awaiting_review: 2 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const table = screen.getByRole("table");
    expect(within(table).queryByRole("columnheader", { name: "分类" })).not.toBeInTheDocument();
    const updatedAtHeader = within(table).getByRole("columnheader", { name: /更新时间/ });
    expect(updatedAtHeader).toHaveAttribute("aria-sort", "none");
    expect(within(table).getAllByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)).toHaveLength(2);

    fireEvent.click(within(updatedAtHeader).getByRole("button", { name: /更新时间/ }));
    expect(updatedAtHeader).toHaveAttribute("aria-sort", "ascending");
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("较早资料");

    fireEvent.click(within(updatedAtHeader).getByRole("button", { name: /更新时间/ }));
    expect(updatedAtHeader).toHaveAttribute("aria-sort", "descending");
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("较新资料");
  });

  it("loads all statuses by default and keeps batch actions hidden without multi-selection", async () => {
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");

    expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({
      lifecycle_status: undefined,
    }));
    expect(screen.getByRole("combobox", { name: "状态" })).toHaveValue("");
    expect(screen.getByRole("status")).toHaveTextContent("未选择资料，单次最多 20 份");
    expect(screen.queryByRole("button", { name: "批量操作" })).not.toBeInTheDocument();
  });

  it("switches the active top-level folder and its upload target", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.categories.mockResolvedValue([category, projectCategory]);
    render(<AdminManagedContentPage />);

    expect(await screen.findByRole("button", { name: "/" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /04 项目资料/ }));

    await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({ category_id: "cat-04" })));
    expect(screen.getByText(/当前目录：04 项目资料/)).toBeInTheDocument();
  });

  it("shows archived metadata and restores an item from trash", async () => {
    mocks.trash.mockResolvedValue({
      items: [{ ...item, archived_at: 1_700_000_000, archived_by_name: "整理员", pre_archive_lifecycle_status: "published" }],
      total: 1,
      status_counts: { published: 1 },
    });
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    expect(await screen.findByText(/整理员 于/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("需要具备发布权限的人员重新发布后才会进入检索");
    fireEvent.click(screen.getByRole("button", { name: "确认恢复" }));
    await waitFor(() => expect(mocks.restoreContent).toHaveBeenCalledWith("item-1", "version-1"));
  });

  it("lets a publisher view trash without exposing restore actions", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.trash.mockResolvedValue({
      items: [{ ...item, archived_at: 1_700_000_000, archived_by_name: "发布负责人", pre_archive_lifecycle_status: "published" }],
      total: 1,
      status_counts: { published: 1 },
    });
    render(<AdminManagedContentPage />);

    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    expect(await screen.findByText(/发布负责人 于/)).toBeInTheDocument();
    expect(screen.getByText("查看已移出资料库的资料。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "恢复" })).not.toBeInTheDocument();
  });

  it("lets an organizer delete a draft after explicit confirmation", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({
      items: [{ ...item, lifecycle_status: "draft" }],
      total: 1,
      status_counts: { draft: 1 },
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");

    fireEvent.click(screen.getAllByRole("button", { name: /删除“建模标准”/ })[0]);
    expect(screen.getByRole("dialog")).toHaveTextContent("将立即停止进入知识库检索");
    expect(screen.getByRole("dialog")).toHaveTextContent("文件、版本及审核发布历史会保留");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "确认移入回收站" }));

    await waitFor(() => expect(mocks.deleteContent).toHaveBeenCalledWith("item-1", "version-1"));
    expect(mocks.success).toHaveBeenCalledWith("已将“建模标准”移至回收站");
  });

  it("requires publish permission for reviewed content and blocks publishing content", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    const firstRender = render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    expect(screen.getAllByRole("button", { name: /删除“建模标准”/ })[0]).toBeDisabled();
    firstRender.unmount();

    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({
      items: [{ ...item, lifecycle_status: "publishing" }],
      total: 1,
      status_counts: { publishing: 1 },
    });
    const { unmount } = render(<AdminManagedContentPage />);
    await openRootFolder();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /删除“建模标准”/ })[0]).toBeDisabled());
    expect(screen.getAllByRole("button", { name: /删除“建模标准”/ })[0]).toHaveAttribute("title", expect.stringContaining("当前状态或权限不允许"));
    unmount();
  });

  it("keeps the delete dialog open with a recoverable conflict message", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.deleteContent.mockRejectedValue(new Error("资料版本已变化，请刷新后重试"));
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("button", { name: /删除“建模标准”/ })[0]);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "确认移入回收站" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("资料版本已变化，请刷新后重试");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows the batch menu only after selecting more than one file", async () => {
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    mocks.bulkReview.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    expect(screen.getByText("已选择", { exact: false })).toHaveTextContent("已选择 1 份，单次最多 20 份");
    expect(screen.queryByRole("button", { name: "批量操作" })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    expect(screen.getByRole("menu", { name: "批量操作" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "批量确认" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("已选择 2 份资料");
  });

  it("keeps only failed targets in the batch delete retry dialog", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    const firstItem = { ...item, lifecycle_status: "draft" };
    const secondItem = { ...firstItem, item_id: "item-2", title: "建模标准2", version_id: "version-2", original_filename: "standard-2.pdf" };
    mocks.items.mockResolvedValue({ items: [firstItem, secondItem], total: 2, status_counts: { draft: 2 } });
    mocks.bulkArchive.mockResolvedValue({
      results: [
        { version_id: "version-1", status: "succeeded", message: null, index_job_id: null },
        { version_id: "version-2", status: "failed", message: "资料版本已变化", index_job_id: null },
      ],
      succeeded: 1,
      failed: 1,
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量删除" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /我已了解/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认移入回收站" }));

    const dialog = await screen.findByRole("dialog", { name: "将资料移入回收站？" });
    expect(within(dialog).queryByText("建模标准", { exact: true })).not.toBeInTheDocument();
    expect(within(dialog).getByText("建模标准2", { exact: true })).toBeInTheDocument();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("成功 1 份，失败 1 份：资料版本已变化");
  });

  it("uploads selected files for an organizer", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.upload.mockResolvedValue({
      batch_id: "batch-1",
      entries: [{ filename: "guide.md", item_id: "item-2", version_id: "version-2", sha256: "b".repeat(64), status: "accepted", reason: null }],
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(await screen.findByRole("button", { name: "上传文件" }));
    const input = await screen.findByLabelText("选择资料文件");
    expect(screen.getByLabelText("选择资料文件夹")).toBeInTheDocument();
    const file = new File(["# Guide"], "guide.md", { type: "text/markdown" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByText("guide.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确定上传" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith([file], "cat-03", "files", expect.any(Function)));
    expect(mocks.success).toHaveBeenCalledWith("已接收 1 个文件");
  });

  it("keeps the upload dialog and selected file after an upload failure", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.upload.mockRejectedValue(new Error("上传服务暂不可用"));
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(await screen.findByRole("button", { name: "上传文件" }));
    const file = new File(["# Retry"], "retry.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("选择资料文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "确定上传" }));

    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("上传服务暂不可用"));
    expect(screen.getByRole("dialog", { name: "上传文件" })).toBeInTheDocument();
    expect(screen.getByText("retry.md")).toBeInTheDocument();
  });

  it("keeps a plain file dropped in the file upload dialog", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getByRole("button", { name: "上传文件" }));
    const input = await screen.findByLabelText("选择资料文件");
    const file = new File(["# Dropped"], "dropped.md", { type: "text/markdown" });

    fireEvent.drop(input.closest("label")!, {
      dataTransfer: { files: [file], items: [], types: ["Files"] },
    });

    expect(await screen.findByText("dropped.md")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "上传文件" })).toHaveTextContent("dropped.md");
    expect(screen.queryByRole("dialog", { name: "确认上传" })).not.toBeInTheDocument();
  });

  it("confirms a selected folder with hierarchy and ignored files before uploading", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.upload.mockResolvedValue({
      batch_id: "batch-folder-upload",
      entries: [{ filename: "guide.md", item_id: "item-2", version_id: "version-2", sha256: "b".repeat(64), status: "accepted", reason: null }],
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(screen.getByRole("button", { name: "上传文件" }));
    fireEvent.click(screen.getByRole("button", { name: "上传文件夹" }));
    const guide = new File(["# Guide"], "guide.md", { type: "text/markdown" });
    Object.defineProperty(guide, "webkitRelativePath", { value: "资料包/01 建筑/guide.md" });
    const video = new File(["video"], "demo.mp4", { type: "video/mp4" });
    Object.defineProperty(video, "webkitRelativePath", { value: "资料包/demo.mp4" });
    fireEvent.change(screen.getByLabelText("选择资料文件夹"), { target: { files: [guide, video] } });

    const dialog = await screen.findByRole("dialog", { name: "上传文件夹" });
    expect(dialog).toHaveTextContent("资料包");
    expect(dialog).toHaveTextContent("资料包/01 建筑/guide.md");
    expect(dialog).toHaveTextContent("资料包/demo.mp4");
    expect(dialog).toHaveTextContent("已忽略");
    expect(mocks.upload).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "开始上传" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith(
      [{ file: guide, relativePath: "资料包/01 建筑/guide.md" }],
      "cat-03",
      "folder",
      expect.any(Function),
    ));
  });

  it("recursively scans a dropped folder before opening the folder confirmation", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    const guide = new File(["# Dropped folder"], "guide.md", { type: "text/markdown" });
    const fileEntry = {
      isFile: true,
      isDirectory: false,
      name: "guide.md",
      file: (success: (file: File) => void) => success(guide),
    };
    let batch = 0;
    const folderEntry = {
      isFile: false,
      isDirectory: true,
      name: "拖入资料包",
      createReader: () => ({
        readEntries: (success: (entries: unknown[]) => void) => success(batch++ === 0 ? [fileEntry] : []),
      }),
    };

    fireEvent.drop(screen.getByTestId("managed-content-drop-list"), {
      dataTransfer: {
        files: [],
        types: ["Files"],
        items: [{ webkitGetAsEntry: () => folderEntry, getAsFile: () => null }],
      },
    });

    const dialog = await screen.findByRole("dialog", { name: "上传文件夹" });
    expect(dialog).toHaveTextContent("拖入资料包/guide.md");
    expect(mocks.upload).not.toHaveBeenCalled();
  });

  it("opens a confirmation before uploading files dropped on the current folder", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.upload.mockResolvedValue({ batch_id: "batch-drop", entries: [] });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    const file = new File(["# Dropped"], "dropped.md", { type: "text/markdown" });
    const folderCard = screen.getByTestId("managed-content-drop-list");

    fireEvent.drop(folderCard!, { dataTransfer: { files: [file], types: ["Files"] } });
    expect(await screen.findByRole("dialog", { name: "确认上传" })).toHaveTextContent("03 公司内部标准");
    expect(screen.getByRole("dialog")).toHaveTextContent("dropped.md");
    expect(mocks.upload).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确定上传" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith([file], "cat-03", "files", expect.any(Function)));
  });

  it("shows the current folder in the list drop overlay and clears it after leaving", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    const folderList = screen.getByTestId("managed-content-drop-list");
    const dataTransfer = { files: [], types: ["Files"] };

    fireEvent.dragEnter(folderList, { dataTransfer });
    expect(screen.getByTestId("managed-content-drop-overlay")).toHaveTextContent("松开以上传文件到“03 公司内部标准”");
    expect(screen.getByTestId("managed-content-drop-overlay")).toHaveTextContent("支持 PDF、Markdown、Word、Excel 和 PPT 文件");

    fireEvent.dragLeave(folderList, { dataTransfer });
    expect(screen.queryByTestId("managed-content-drop-overlay")).not.toBeInTheDocument();
  });

  it("clears the list drop overlay after rejecting an unsupported file", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    render(<AdminManagedContentPage />);
    await openRootFolder();
    const folderList = screen.getByTestId("managed-content-drop-list");
    const file = new File(["video"], "sample.mp4", { type: "video/mp4" });
    const dataTransfer = { files: [file], types: ["Files"] };

    fireEvent.dragEnter(folderList, { dataTransfer });
    expect(screen.getByTestId("managed-content-drop-overlay")).toBeInTheDocument();
    fireEvent.drop(folderList, { dataTransfer });

    expect(screen.queryByTestId("managed-content-drop-overlay")).not.toBeInTheDocument();
    expect(mocks.error).toHaveBeenCalledWith("没有可上传的支持格式，仅支持 PDF、Markdown、Word、Excel 和 PPT 文件");
  });

  it("does not upload files when a folder drop is cancelled", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    const file = new File(["cancelled"], "cancelled.pdf", { type: "application/pdf" });

    fireEvent.drop(screen.getByTestId("managed-content-drop-list"), { dataTransfer: { files: [file], types: ["Files"] } });
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    expect(screen.queryByRole("dialog", { name: "确认上传" })).not.toBeInTheDocument();
    expect(mocks.upload).not.toHaveBeenCalled();
  });

  it("navigates into a child folder and uploads to the current folder", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.categories.mockResolvedValue([category, childCategory]);
    mocks.upload.mockResolvedValue({ batch_id: "batch-folder", entries: [] });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    const folderButtons = await screen.findAllByRole("button", { name: /01 建模标准/ });
    fireEvent.click(folderButtons[0]);
    await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({ category_id: "cat-03-01" })));
    const file = new File(["# Folder"], "folder.md", { type: "text/markdown" });
    fireEvent.click(screen.getByRole("button", { name: "上传文件" }));
    fireEvent.change(await screen.findByLabelText("选择资料文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "确定上传" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith([file], "cat-03-01", "files", expect.any(Function)));
  });

  it("lets a category manager create a controlled child folder", async () => {
    mocks.permissions = CATEGORY_MANAGER_PERMISSIONS;
    mocks.createCategory.mockResolvedValue(childCategory);
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(await screen.findByRole("button", { name: "新建目录" }));
    fireEvent.change(screen.getByLabelText("文件夹名称"), { target: { value: "审核标准" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(mocks.createCategory).toHaveBeenCalledWith(expect.objectContaining({
      parent_id: "cat-03", display_name: "审核标准",
    })));
  });

  it("lets an organizer submit a child folder request", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.createFolderRequest.mockResolvedValue({ id: "request-1" });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(await screen.findByRole("button", { name: "新建目录" }));
    fireEvent.change(screen.getByLabelText("文件夹名称"), { target: { value: "审核标准" } });
    fireEvent.click(screen.getByRole("button", { name: "提交申请" }));

    await waitFor(() => expect(mocks.createFolderRequest).toHaveBeenCalledWith("cat-03", "审核标准"));
    expect(mocks.success).toHaveBeenCalledWith("目录申请已提交");
  });

  it("shows pending folder requests to a reviewer and approves one", async () => {
    mocks.folderRequests.mockResolvedValue([{
      id: "request-1", parent_category_id: "cat-03", parent_label: "03 公司内部标准",
      display_name: "审核标准", status: "pending", requester_name: "整理员",
      review_note: null, created_category_id: null, created_at: 1, updated_at: 1, reviewed_at: null,
    }]);
    mocks.reviewFolderRequest.mockResolvedValue({ id: "request-1", status: "approved" });
    render(<AdminManagedContentPage />);

    expect(await screen.findByText("待处理目录申请")).toBeInTheDocument();
    expect(screen.getByText(/申请人：整理员/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准" }));

    await waitFor(() => expect(mocks.reviewFolderRequest).toHaveBeenCalledWith("request-1", true));
    expect(mocks.success).toHaveBeenCalledWith("目录申请已批准");
  });

  it("selects a nested target directory in the single-move picker", async () => {
    mocks.categories.mockResolvedValue([category, childCategory, projectCategory]);
    mocks.moveContent.mockResolvedValue({ ...item, category_id: childCategory.id });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(screen.getAllByRole("button", { name: "移动“建模标准”" })[0]);
    const dialog = screen.getByRole("dialog", { name: "移动资料" });
    expect(within(dialog).getByTestId("category-picker-item-cat-03")).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(within(dialog).getByRole("button", { name: "展开公司内部标准" }));
    fireEvent.click(within(dialog).getByTestId("category-picker-item-cat-03-01"));
    expect(within(dialog).getByText(`已选择：${childCategory.full_path}`)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认移动" }));

    await waitFor(() => expect(mocks.moveContent).toHaveBeenCalledWith("item-1", childCategory.id, "version-1"));
    expect(mocks.success).toHaveBeenCalledWith("已移动“建模标准”");
  });

  it("keeps the single-move dialog open when the request fails", async () => {
    mocks.categories.mockResolvedValue([category, projectCategory]);
    mocks.moveContent.mockRejectedValue(new Error("目标目录已停用"));
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(screen.getAllByRole("button", { name: "移动“建模标准”" })[0]);
    const dialog = screen.getByRole("dialog", { name: "移动资料" });
    fireEvent.click(within(dialog).getByTestId("category-picker-item-cat-04"));
    fireEvent.click(within(dialog).getByRole("button", { name: "确认移动" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("目标目录已停用");
    expect(dialog).toHaveAttribute("data-state", "open");
    expect(mocks.error).not.toHaveBeenCalled();
  });

  it("uses the same directory picker for batch moves", async () => {
    mocks.categories.mockResolvedValue([category, childCategory, projectCategory]);
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    mocks.bulkMove.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量移动" }));

    const dialog = screen.getByRole("dialog", { name: "批量移动资料" });
    fireEvent.click(within(dialog).getByRole("button", { name: "展开公司内部标准" }));
    fireEvent.click(within(dialog).getByTestId("category-picker-item-cat-03-01"));
    fireEvent.click(within(dialog).getByRole("button", { name: "确认执行" }));

    await waitFor(() => expect(mocks.bulkMove).toHaveBeenCalledWith([
      { item_id: "item-1", expected_version_id: "version-1" },
      { item_id: "item-2", expected_version_id: "version-2" },
    ], childCategory.id));
  });

  it("moves an existing desktop row when dropped on a child folder", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.categories.mockResolvedValue([category, childCategory]);
    mocks.items.mockResolvedValue({
      items: [{ ...item, lifecycle_status: "draft" }], total: 1, status_counts: { draft: 1 },
    });
    mocks.moveContent.mockResolvedValue({ ...item, category_id: "cat-03-01" });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const row = await screen.findByTitle("拖动到上方文件夹可移动资料");
    const targetFolder = screen.getAllByRole("button", { name: /01 建模标准/ })[0];
    fireEvent.dragStart(row);
    fireEvent.dragOver(targetFolder);
    fireEvent.drop(targetFolder);

    await waitFor(() => expect(mocks.moveContent).toHaveBeenCalledWith("item-1", "cat-03-01", "version-1"));
    expect(mocks.success).toHaveBeenCalledWith("已移动“建模标准”");
  });

  it("blocks moving a draft while an older published version remains searchable", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({
      items: [{ ...item, lifecycle_status: "draft", has_published_head: true }], total: 1, status_counts: { draft: 1 },
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    expect(screen.queryByTitle("拖动到上方文件夹可移动资料")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /移动“建模标准”/ }).every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("right-aligns seven icon actions and keeps download out of the detail dialog", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "draft" }], total: 1, status_counts: { draft: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    for (const actionName of ["查看“建模标准”的详细信息", "查看“建模标准”", "移动“建模标准”", "下载“建模标准”", "重命名“建模标准”", "更新“建模标准”", "删除“建模标准”"]) {
      expect(screen.getAllByRole("button", { name: actionName }).length).toBeGreaterThan(0);
    }
    const detailsButton = screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0];
    expect(detailsButton.parentElement).toHaveClass("ml-auto", "justify-end");
    fireEvent.click(detailsButton);
    const dialog = screen.getByRole("dialog", { name: "建模标准" });
    expect(within(dialog).queryByRole("button", { name: /下载/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
  });

  it("renames titles and filenames as a new version", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "draft" }], total: 1, status_counts: { draft: 1 } });
    mocks.renameContent.mockResolvedValue({ ...item, title: "更新后的标题", original_filename: "renamed.pdf", version_number: 2 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("button", { name: "重命名“建模标准”" })[0]);
    fireEvent.change(screen.getByLabelText("资料标题"), { target: { value: "更新后的标题" } });
    fireEvent.change(screen.getByLabelText(/^源文件名/), { target: { value: "renamed.pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(mocks.renameContent).toHaveBeenCalledWith("item-1", {
      title: "更新后的标题",
      original_filename: "renamed.pdf",
      expected_version_id: "version-1",
    }));
  });

  it("uploads a replacement file and chooses the new filename", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "draft" }], total: 1, status_counts: { draft: 1 } });
    mocks.updateVersion.mockResolvedValue({ ...item, original_filename: "replacement.pdf", version_number: 2 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("button", { name: "更新“建模标准”" })[0]);
    const replacement = new File(["# replacement"], "replacement.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("选择替换文件"), { target: { files: [replacement] } });
    expect(screen.getByText("将使用原名称并匹配新格式：standard.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "使用新文件名" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新" }));
    await waitFor(() => expect(mocks.updateVersion).toHaveBeenCalledWith(
      "item-1", replacement, "version-1", "new", undefined,
    ));
  });

  it("shows loading, empty, and recoverable error states", async () => {
    let resolveCapabilities: ((value: { enabled: boolean; max_upload_bytes: number; supported_extensions: string[] }) => void) | undefined;
    mocks.capabilities.mockReturnValueOnce(new Promise((resolve) => { resolveCapabilities = resolve; }));
    render(<AdminManagedContentPage />);
    expect(screen.getByText("正在加载资料…")).toBeInTheDocument();
    resolveCapabilities?.({ enabled: true, max_upload_bytes: 1024, supported_extensions: [".pdf"] });
    await openRootFolder();
    expect((await screen.findAllByText("建模标准")).length).toBeGreaterThan(0);

    mocks.capabilities.mockRejectedValueOnce(new Error("资料服务暂不可用"));
    fireEvent.click(screen.getByRole("button", { name: "刷新列表" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("资料服务暂不可用");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("disables workflow actions and exposes the busy label while saving", async () => {
    let resolveReview: ((value: typeof item) => void) | undefined;
    mocks.review.mockReturnValueOnce(new Promise((resolve) => { resolveReview = resolve; }));
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    expect(screen.getByRole("button", { name: "确认中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "退回" })).toBeDisabled();
    resolveReview?.({ ...item, lifecycle_status: "approved" });
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith("资料已确认"));
  });

  it("keeps failed bulk items selected and shows their per-item reason", async () => {
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    mocks.bulkReview.mockResolvedValue({
      results: [{ version_id: "version-1", status: "failed", message: "资料状态已变化，请刷新后重试", index_job_id: null }],
      succeeded: 1,
      failed: 1,
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量确认" }));
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("建模标准：资料状态已变化，请刷新后重试");
    expect(screen.getByRole("button", { name: "重试失败项" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]).toBeChecked();
  });

  it("allows republishing after a non-retryable historical failure", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "publication_failed", publication_attempt_count: 4, publication_failure: { code: "pdf_password_required", message: "PDF 需要密码才能解析。", retryable: false, recommended_action: "请上传已解除密码保护的 PDF。" } }], total: 1, status_counts: { publication_failed: 1 } });
    mocks.publish.mockResolvedValue({});
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0]);
    expect((await screen.findAllByText("PDF 需要密码才能解析。")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("请上传已解除密码保护的 PDF。").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/系统或文件处理后可重新发布/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/共尝试 4 次/).length).toBeGreaterThan(0);
    expect(screen.queryByText("pdf_password_required")).not.toBeInTheDocument();
    const republish = screen.getByRole("button", { name: "重新发布" });
    expect(republish).toBeEnabled();
    fireEvent.click(republish);
    await waitFor(() => expect(mocks.publish).toHaveBeenCalledWith("version-1"));
  });

  it("includes non-retryable historical failures in bulk republish", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2", lifecycle_status: "publication_failed" };
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "publication_failed", publication_attempt_count: 2, publication_failure: { code: "parser_result_invalid", message: "文档解析结果无效。", retryable: false, recommended_action: "请确认文件内容完整。" } }, secondItem], total: 2, status_counts: { publication_failed: 2 } });
    mocks.bulkPublish.mockResolvedValue({ results: [{ version_id: "version-1", status: "succeeded", message: null, index_job_id: "job-3" }, { version_id: "version-2", status: "succeeded", message: null, index_job_id: "job-4" }], succeeded: 2, failed: 0 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量发布" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("已选择 2 份资料");
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    await waitFor(() => expect(mocks.bulkPublish).toHaveBeenCalledWith(["version-1", "version-2"]));
  });

  it("opens the upload task page from a deep link and loads task details", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    const task = {
      batch_id: "batch-upload-1",
      upload_mode: "folder" as const,
      status: "partial_success" as const,
      target_category_id: "cat-03",
      target_path: "03 公司内部标准 / 01 建模标准",
      total_files: 2,
      accepted_files: 1,
      skipped_files: 1,
      total_bytes: 20,
      total_uploaded_bytes: 10,
      created_by_name: "整理员",
      created_at: 1,
      updated_at: 2,
      error_summary: null,
      entries: null,
    };
    mocks.uploadTasks.mockResolvedValue({ tasks: [task], total: 1, status_counts: { partial_success: 1 } });
    mocks.uploadTask.mockResolvedValue({ ...task, entries: [{ sequence: 1, filename: "guide.md", relative_path: "资料包/guide.md", size_bytes: 10, status: "accepted", reason: null, item_id: "item-1", version_id: "version-1", created_at: 1 }, { sequence: 2, filename: "video.mp4", relative_path: "资料包/video.mp4", size_bytes: 10, status: "skipped", reason: "不支持的文件格式", item_id: null, version_id: null, created_at: 1 }] });
    window.history.replaceState({}, "", "/admin/content?view=uploads");

    render(<AdminManagedContentPage />);
    expect(await screen.findByRole("heading", { name: "上传任务" })).toBeInTheDocument();
    expect(await screen.findByText("03 公司内部标准 / 01 建模标准")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(await screen.findByText("文件明细")).toBeInTheDocument();
    expect(screen.getByText("资料包/video.mp4")).toBeInTheDocument();
    expect(mocks.uploadTask).toHaveBeenCalledWith("batch-upload-1");

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("tab", { name: "资料库" }));
    expect(window.location.search).toBe("");
  });
});
