import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminManagedContentPage } from "./AdminManagedContentPage";

const mocks = vi.hoisted(() => ({
  role: "user" as "user" | "admin",
  permissions: ["item.review", "item.move_review", "folder.review", "trash.view", "trash.restore"] as string[],
  capabilities: vi.fn(),
  categories: vi.fn(),
  items: vi.fn(),
  preflightUpload: vi.fn(),
  upload: vi.fn(),
  uploadTasks: vi.fn(),
  uploadTask: vi.fn(),
  indexJobs: vi.fn(),
  createCategory: vi.fn(),
  renameCategory: vi.fn(),
  updateCategoryNumber: vi.fn(),
  moveCategory: vi.fn(),
  moveContent: vi.fn(),
  reclassifyContent: vi.fn(),
  reclassificationJob: vi.fn(),
  retryReclassification: vi.fn(),
  renameContent: vi.fn(),
  updateVersion: vi.fn(),
  folderRequests: vi.fn(),
  createFolderRequest: vi.fn(),
  reviewFolderRequest: vi.fn(),
  submit: vi.fn(),
  review: vi.fn(),
  publish: vi.fn(),
  regeneratePreview: vi.fn(),
  bulkReview: vi.fn(),
  bulkSubmit: vi.fn(),
  bulkPublish: vi.fn(),
  bulkMove: vi.fn(),
  bulkReclassify: vi.fn(),
  bulkArchive: vi.fn(),
  bulkRestore: vi.fn(),
  preflightBulkRestore: vi.fn(),
  exportTrash: vi.fn(),
  bulkDownload: vi.fn(),
  preflightBulkOperation: vi.fn(),
  bulkOperation: vi.fn(),
  updateBulkSelection: vi.fn(),
  executeBulkOperation: vi.fn(),
  reviewBulkItem: vi.fn(),
  cancelBulkOperation: vi.fn(),
  bulkArchiveUrl: vi.fn(),
  downloadCategory: vi.fn(),
  downloadFile: vi.fn(),
  downloadMedia: vi.fn(),
  createMediaMetadataRevision: vi.fn(),
  transcriptionSchemes: vi.fn(),
  preflightBulkTranscription: vi.fn(),
  bulkStartTranscription: vi.fn(),
  deleteContent: vi.fn(),
  trash: vi.fn(),
  trashSettings: vi.fn(),
  updateTrashSettings: vi.fn(),
  preflightTrashPurge: vi.fn(),
  previewOverdueTrashPurge: vi.fn(),
  purgeTrash: vi.fn(),
  trashPurgeRuns: vi.fn(),
  restoreContent: vi.fn(),
  auditEvents: vi.fn(),
  fileUrl: vi.fn(),
  info: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  openPreview: vi.fn(),
  openXMind: vi.fn(),
  openVideo: vi.fn(),
  previewState: { parentId: null as string | null, versionId: null as string | null },
}));

const REVIEWER_PERMISSIONS = ["item.review", "item.move_review", "folder.review", "trash.view", "trash.restore", "item.download"];
const ORGANIZER_PERMISSIONS = ["item.upload", "item.submit", "item.move_draft", "item.archive_draft", "folder.request", "item.download"];
const PUBLISHER_PERMISSIONS = ["item.publish", "item.reclassify_published", "item.archive_published", "trash.view", "index.view", "item.download"];
const CATEGORY_MANAGER_PERMISSIONS = ["category.manage", "folder.review"];

vi.mock("../../components/PdfPreview", () => ({ PdfPreview: () => null }));
vi.mock("../../hooks/usePdfPreview", () => ({
  PdfPreviewProvider: ({ children }: { children: React.ReactNode }) => children,
  usePdfPreview: () => ({ open: mocks.openPreview, openXMind: mocks.openXMind, state: { ...mocks.previewState } }),
}));

vi.mock("../../hooks/useVideoPlayer", () => ({
  useVideoPlayer: () => ({ open: mocks.openVideo, close: vi.fn(), isOpen: false, currentRequest: null }),
}));

vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({
    state: {
      status: "authed",
      user: {
        id: 2,
        employee_id: "reviewer",
        real_name: "负责人",
        role: mocks.role,
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
    preflightManagedContentUpload: mocks.preflightUpload,
    uploadManagedContent: mocks.upload,
    managedUploadTasks: mocks.uploadTasks,
    managedUploadTask: mocks.uploadTask,
    managedContentIndexJobs: mocks.indexJobs,
    createManagedCategory: mocks.createCategory,
    renameManagedCategory: mocks.renameCategory,
    updateManagedCategoryNumber: mocks.updateCategoryNumber,
    moveManagedCategory: mocks.moveCategory,
    moveManagedContent: mocks.moveContent,
    reclassifyManagedContent: mocks.reclassifyContent,
    managedContentReclassificationJob: mocks.reclassificationJob,
    retryManagedContentReclassification: mocks.retryReclassification,
    renameManagedContent: mocks.renameContent,
    updateManagedContentVersion: mocks.updateVersion,
    managedFolderRequests: mocks.folderRequests,
    createFolderRequest: mocks.createFolderRequest,
    reviewFolderRequest: mocks.reviewFolderRequest,
    submitManagedContent: mocks.submit,
    reviewManagedContent: mocks.review,
    publishManagedContent: mocks.publish,
    regenerateManagedContentPreview: mocks.regeneratePreview,
    bulkReviewManagedContent: mocks.bulkReview,
    bulkSubmitManagedContent: mocks.bulkSubmit,
    bulkPublishManagedContent: mocks.bulkPublish,
    bulkMoveManagedContent: mocks.bulkMove,
    bulkReclassifyManagedContent: mocks.bulkReclassify,
    bulkArchiveManagedContent: mocks.bulkArchive,
    bulkRestoreManagedContent: mocks.bulkRestore,
    preflightBulkRestoreManagedContent: mocks.preflightBulkRestore,
    exportManagedContentTrash: mocks.exportTrash,
    bulkDownloadManagedContent: mocks.bulkDownload,
    preflightManagedContentBulkOperation: mocks.preflightBulkOperation,
    managedContentBulkOperation: mocks.bulkOperation,
    updateManagedContentBulkSelection: mocks.updateBulkSelection,
    executeManagedContentBulkOperation: mocks.executeBulkOperation,
    reviewManagedContentBulkItem: mocks.reviewBulkItem,
    cancelManagedContentBulkOperation: mocks.cancelBulkOperation,
    managedContentBulkArchiveUrl: mocks.bulkArchiveUrl,
    downloadManagedCategory: mocks.downloadCategory,
    downloadManagedContentFile: mocks.downloadFile,
    downloadManagedMedia: mocks.downloadMedia,
    createMediaMetadataRevision: mocks.createMediaMetadataRevision,
    listTranscriptionSchemes: mocks.transcriptionSchemes,
    preflightBulkStartTranscription: mocks.preflightBulkTranscription,
    bulkStartTranscription: mocks.bulkStartTranscription,
    deleteManagedContent: mocks.deleteContent,
    managedContentTrash: mocks.trash,
    managedContentTrashSettings: mocks.trashSettings,
    updateManagedContentTrashSettings: mocks.updateTrashSettings,
    preflightManagedContentTrashPurge: mocks.preflightTrashPurge,
    previewOverdueManagedContentTrashPurge: mocks.previewOverdueTrashPurge,
    purgeManagedContentTrash: mocks.purgeTrash,
    managedContentTrashPurgeRuns: mocks.trashPurgeRuns,
    restoreManagedContent: mocks.restoreContent,
    managedContentAuditEvents: mocks.auditEvents,
    managedContentFileUrl: mocks.fileUrl,
  },
}));

vi.mock("../../components/ui/toast", () => ({
  toast: { info: mocks.info, success: mocks.success, error: mocks.error },
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
  direct_child_count: 1,
  total_child_count: 1,
  total_item_count: 1,
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
  direct_child_count: 0,
  total_child_count: 0,
  total_item_count: 0,
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
  direct_child_count: 0,
  total_child_count: 0,
  total_item_count: 0,
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
  preview_status: "ready" as const,
  version_id: "version-1",
  version_number: 1,
  original_filename: "standard.pdf",
  doc_type: "pdf",
  lifecycle_status: "awaiting_review",
  object_sha256: "a".repeat(64),
  source_origin: "web",
  source_batch_id: "batch-1",
  source_rel_path: "项目资料/建模标准/standard.pdf",
  is_current: false,
  has_published_head: false,
  latest_publication_status: null,
  publication_attempt_count: 0,
  publication_failure: null,
  latest_reviewed_by_name: null,
  latest_reviewed_at: null,
  latest_review_decision: null,
  latest_review_note: null,
  media_duration_ms: null,
  media_file_size: null,
  has_pending_revision: false,
  reclassification_job_id: null,
  reclassification_status: null,
  created_at: 1,
  updated_at: 1,
};

const mediaItem = {
  ...item,
  item_id: "media-transcript-media-1",
  title: "WhisperX 培训视频",
  content_kind: "media_transcript",
  media_id: "123e4567-e89b-12d3-a456-426614174110",
  preview_parent_id: null,
  version_id: "123e4567-e89b-12d3-a456-426614174111",
  original_filename: "training.mp4",
  doc_type: "transcript",
  lifecycle_status: "published",
  object_sha256: null,
  source_origin: "transcription",
  source_batch_id: null,
  source_rel_path: "training.mp4",
  is_current: true,
  has_published_head: true,
  latest_publication_status: "done",
  publication_attempt_count: 1,
  media_duration_ms: 65_000,
  media_file_size: 3 * 1024 * 1024,
  has_pending_revision: true,
};

async function openRootFolder(folderId = category.id) {
  fireEvent.click(await screen.findByTestId(`managed-folder-row-${folderId}`));
  await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({ category_id: folderId })));
}

function openMoreActions(title: string, index = 0) {
  fireEvent.click(screen.getAllByRole("button", { name: `更多“${title}”的操作` })[index]);
  return screen.getByRole("menu", { name: `“${title}”的更多操作` });
}

describe("AdminManagedContentPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "user";
    mocks.permissions = REVIEWER_PERMISSIONS;
    mocks.previewState.parentId = null;
    mocks.capabilities.mockResolvedValue({ enabled: true, max_upload_bytes: 1024, max_batch_files: 5000, max_batch_bytes: 10240, supported_extensions: [".pdf"] });
    mocks.categories.mockResolvedValue([category]);
    mocks.items.mockResolvedValue({ items: [item], total: 1, status_counts: { awaiting_review: 1 } });
    mocks.preflightUpload.mockResolvedValue({ entries: [], folder_conflicts: [] });
    mocks.folderRequests.mockResolvedValue([]);
    mocks.review.mockResolvedValue({ ...item, lifecycle_status: "approved" });
    mocks.regeneratePreview.mockResolvedValue({ version_id: "version-1", preview_parent_id: "parent-pptx", preview_status: "ready" });
    mocks.deleteContent.mockResolvedValue({ item_id: "item-1", version_id: "version-1", archived_at: 2, previous_status: "awaiting_review", publication_withdrawn: false });
    mocks.trash.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.trashSettings.mockResolvedValue({ cleanup_enabled: false, retention_days: 90, warning_days: 7, batch_limit: 20, updated_by: null, updated_at: 0 });
    mocks.trashPurgeRuns.mockResolvedValue([]);
    mocks.restoreContent.mockResolvedValue({ item_id: "item-1", version_id: "version-1", restored_status: "approved", category_id: "cat-03", moved_to_alternate_category: false, replaced_conflict: false });
    mocks.auditEvents.mockResolvedValue([]);
    mocks.uploadTasks.mockResolvedValue({ tasks: [], total: 0, status_counts: {} });
    mocks.uploadTask.mockResolvedValue({});
    mocks.indexJobs.mockResolvedValue({ jobs: [], total: 0, status_counts: {} });
    mocks.reclassifyContent.mockResolvedValue({ id: "reclass-1", status: "pending" });
    mocks.renameCategory.mockResolvedValue({ ...childCategory, display_name: "新目录名称", version: 2 });
    mocks.updateCategoryNumber.mockResolvedValue([category, { ...childCategory, version: 2 }, projectCategory]);
    mocks.moveCategory.mockResolvedValue([category, childCategory, projectCategory]);
    mocks.bulkReclassify.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });
    window.history.replaceState({}, "", "/admin/content");
    mocks.bulkDownload.mockResolvedValue({ blob: new Blob(["zip"]), filename: "资料批量下载.zip" });
    mocks.bulkArchiveUrl.mockReturnValue("/api/admin/content/bulk-operations/bulk-1/archive");
    mocks.downloadCategory.mockResolvedValue({ blob: new Blob(["zip"]), filename: "03 公司内部标准-资料打包下载.zip" });
    mocks.downloadFile.mockResolvedValue({ blob: new Blob(["file"]), filename: "standard.pdf" });
    mocks.downloadMedia.mockResolvedValue({ blob: new Blob(["media"]), filename: "WhisperX 培训视频-视频资料.zip" });
    mocks.createMediaMetadataRevision.mockResolvedValue({});
    mocks.transcriptionSchemes.mockResolvedValue([{
      scheme_id: "scheme-1", name: "标准转录", description: "测试方案", base_id: "base-1",
      config_hash: "a".repeat(64), enabled: true, archived: false, sort_order: 1, version: 1,
      availability: "available", unavailable_reason_code: null, requires_review: true,
      auto_publish: false, auto_index: false,
    }]);
  });

  it("shows library-wide status counts at the root while keeping the file list empty", async () => {
    mocks.items.mockResolvedValue({
      items: [item],
      total: 8,
      status_counts: { draft: 2, awaiting_review: 3, approved: 1, published: 2 },
    });
    render(<AdminManagedContentPage />);

    expect(await screen.findByTestId(`managed-folder-row-${category.id}`)).toBeInTheDocument();
    const overview = screen.getByRole("region", { name: "资料状态概览" });
    expect(within(overview).getByText("全部资料").parentElement).toHaveTextContent("8");
    expect(within(overview).getByText("待确认").parentElement).toHaveTextContent("3");
    expect(within(overview).getByText("已确认").parentElement).toHaveTextContent("1");
    expect(within(overview).getByText("已发布").parentElement).toHaveTextContent("2");
    expect(screen.queryByText("standard.pdf · v1")).not.toBeInTheDocument();
    expect(screen.getByText("共 0 份，第 1 / 1 页")).toBeInTheDocument();
  });

  it("returns to each parent directory and disables the action at the root", async () => {
    mocks.categories.mockResolvedValue([category, childCategory]);
    render(<AdminManagedContentPage />);

    const upButton = screen.getByRole("button", { name: "返回上一目录" });
    expect(upButton).toBeDisabled();
    await openRootFolder();
    expect(upButton).toBeEnabled();

    fireEvent.click(await screen.findByTestId(`managed-folder-row-${childCategory.id}`));
    await waitFor(() => expect(mocks.items).toHaveBeenLastCalledWith(expect.objectContaining({ category_id: childCategory.id })));
    fireEvent.click(upButton);
    await waitFor(() => expect(mocks.items).toHaveBeenLastCalledWith(expect.objectContaining({ category_id: category.id })));
    fireEvent.click(upButton);
    await waitFor(() => expect(mocks.items).toHaveBeenLastCalledWith(expect.objectContaining({ category_id: undefined })));
    expect(upButton).toBeDisabled();
  });

  it.skip("opens the dedicated review dialog and submits an optional approval note", async () => {
    render(<AdminManagedContentPage />);
    await openRootFolder();
    expect((await screen.findAllByText("建模标准")).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("选择资料文件")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布" })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "审核" })[0]);
    const dialog = screen.getByRole("dialog", { name: "审核资料" });
    expect(dialog).toHaveTextContent("03 公司内部标准");
    expect(dialog).toHaveTextContent("v1");
    expect(dialog).toHaveTextContent("网页上传");
    expect(within(dialog).getByRole("group", { name: "审核操作" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "预览文件" }));
    expect(mocks.openPreview).toHaveBeenCalledWith("parent-1", "建模标准", "pdf", 1, {}, "managed-content-review");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "审核备注（可选）" }), { target: { value: "符合发布要求" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认通过" }));
    await waitFor(() => expect(mocks.review).toHaveBeenCalledWith("version-1", true, "符合发布要求"));
  });

  it.skip("reuses the review entry from details and shows the latest review record", async () => {
    mocks.items.mockResolvedValue({
      items: [{
        ...item,
        latest_reviewed_by_name: "负责人",
        latest_reviewed_at: 1_700_000_000,
        latest_review_decision: "rejected",
        latest_review_note: "请补充适用范围",
      }],
      total: 1,
      status_counts: { awaiting_review: 1 },
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0]);
    const detailDialog = screen.getByRole("dialog", { name: "建模标准" });
    expect(detailDialog).toHaveTextContent("负责人");
    expect(detailDialog).toHaveTextContent("退回修改");
    expect(detailDialog).toHaveTextContent("请补充适用范围");
    expect(within(detailDialog).queryByRole("button", { name: "确认" })).not.toBeInTheDocument();
    expect(within(detailDialog).queryByRole("button", { name: "退回" })).not.toBeInTheDocument();
    fireEvent.click(within(detailDialog).getByRole("button", { name: "审核" }));
    expect(screen.getByRole("dialog", { name: "审核资料" })).toBeInTheDocument();
  });

  it.skip("renders only the permitted next workflow action for each lifecycle state", async () => {
    mocks.permissions = [...new Set([...REVIEWER_PERMISSIONS, ...ORGANIZER_PERMISSIONS, ...PUBLISHER_PERMISSIONS])];
    const statuses = ["draft", "rejected", "awaiting_review", "approved", "publication_failed", "publishing", "published", "superseded"];
    mocks.items.mockResolvedValue({
      items: statuses.map((lifecycle_status, index) => ({
        ...item,
        item_id: `item-${index}`,
        version_id: `version-${index}`,
        title: `资料-${lifecycle_status}`,
        lifecycle_status,
      })),
      total: statuses.length,
      status_counts: {},
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    for (const label of ["提交", "重新提交", "审核", "发布", "重新发布"]) {
      expect(screen.getAllByRole("button", { name: label }).length).toBeGreaterThan(0);
    }
    for (const label of ["发布中", "已发布", "历史版本"]) {
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
  });

  it("hides workflow actions when the account lacks the required permission", async () => {
    mocks.permissions = ["item.view"];
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "draft" }], total: 1, status_counts: { draft: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    expect(screen.queryByRole("button", { name: "提交" })).not.toBeInTheDocument();
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
    await waitFor(() => expect(mocks.info).toHaveBeenCalledWith(
      "正在打包 2 份资料，请稍候…",
      expect.objectContaining({
        id: "managed-content-bulk-download",
        description: "文件较多时可能需要几秒。",
        duration: Infinity,
      }),
    ));
    expect(screen.getByRole("button", { name: "批量操作" })).toBeDisabled();

    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:synthetic") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    resolveDownload({ blob: new Blob(["zip"]), filename: "资料批量下载.zip" });
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith(
      "已打包 2 份资料并开始下载",
      expect.objectContaining({ id: "managed-content-bulk-download", duration: 4000 }),
    ));
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
    await waitFor(() => expect(mocks.info).toHaveBeenCalledWith(
      "正在打包 2 份资料，请稍候…",
      expect.objectContaining({ id: "managed-content-bulk-download", duration: Infinity }),
    ));

    rejectDownload(new Error("打包服务暂时不可用"));
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith(
      "打包服务暂时不可用",
      expect.objectContaining({ id: "managed-content-bulk-download", duration: 5000 }),
    ));
  });

  it("opens indexed files directly and restores details after an in-dialog preview", async () => {
    const { rerender } = render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("button", { name: "预览“建模标准”" })[0]);
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

    const previewButton = screen.getAllByRole("button", { name: "预览“建模标准”" })[0];
    expect(previewButton).toBeDisabled();
    fireEvent.mouseEnter(previewButton.parentElement!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("该资料尚未生成可预览文件");
    expect(mocks.openPreview).not.toHaveBeenCalled();
  });

  it("regenerates a missing published PPTX preview from details", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({
      items: [{
        ...item,
        doc_type: "pptx",
        original_filename: "slides.pptx",
        lifecycle_status: "published",
        is_current: true,
        has_published_head: true,
        latest_publication_status: "done",
        preview_parent_id: null,
        preview_status: "missing",
      }],
      total: 1,
      status_counts: { published: 1 },
    });

    render(<AdminManagedContentPage />);
    await openRootFolder();
    const previewButton = screen.getAllByRole("button", { name: "预览“建模标准”" })[0];
    expect(previewButton).toBeDisabled();
    fireEvent.mouseEnter(previewButton.parentElement!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("PPTX 预览生成失败");

    fireEvent.click(screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "重新生成预览" }));

    await waitFor(() => expect(mocks.regeneratePreview).toHaveBeenCalledWith("version-1"));
    expect(await screen.findByRole("button", { name: "预览文件" })).toBeInTheDocument();
    expect(mocks.success).toHaveBeenCalledWith("PPTX 预览已生成");
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

  it("integrates selectable folders before files without adding them to file pagination", async () => {
    const folder = { ...childCategory, display_code: "02", display_name: "模型目录", item_count: 3, direct_child_count: 2, total_child_count: 4, total_item_count: 8 };
    mocks.categories.mockResolvedValue([category, folder]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const table = screen.getByRole("table");
    expect(within(table).queryByRole("columnheader", { name: "顺序" })).not.toBeInTheDocument();
    const folderRow = within(table).getByTestId(`managed-folder-row-${folder.id}`);
    expect(within(folderRow).getByText("02 模型目录")).toBeInTheDocument();
    expect(within(folderRow).getByText("3 份直接资料 · 2 个直接子文件夹 · 共 4 个子文件夹 · 共 8 份资料")).toBeInTheDocument();
    const fileRow = within(table).getByText("standard.pdf · v1").closest("tr");
    expect(folderRow.compareDocumentPosition(fileRow!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(within(folderRow).getByRole("checkbox", { name: "选择文件夹02 模型目录" })).not.toBeChecked();

    fireEvent.click(within(table).getByRole("checkbox", { name: "选择当前页资料" }));
    expect(within(table).getByRole("checkbox", { name: "选择建模标准" })).toBeChecked();
    expect(screen.getByText("共 1 份，第 1 / 1 页")).toBeInTheDocument();

    const mobileList = screen.getByTestId(`managed-folder-mobile-${folder.id}`).parentElement!;
    expect(mobileList.firstElementChild).toBe(screen.getByTestId(`managed-folder-mobile-${folder.id}`));
    expect(within(screen.getByTestId(`managed-folder-mobile-${folder.id}`)).getByText("02 模型目录")).toBeInTheDocument();

    fireEvent.focus(screen.getByRole("textbox", { name: "搜索资料" }));
    fireEvent.change(within(screen.getByRole("dialog", { name: "搜索筛选" })).getByRole("combobox", { name: "状态" }), { target: { value: "published" } });
    await waitFor(() => expect(mocks.items).toHaveBeenLastCalledWith(expect.objectContaining({ lifecycle_status: "published" })));
    expect(await screen.findByTestId(`managed-folder-row-${folder.id}`)).toBeInTheDocument();
  });

  it.skip("preflights a selected folder and supports per-file selection and review", async () => {
    const folder = { ...childCategory, display_code: "02", display_name: "模型目录", item_count: 1, total_item_count: 1 };
    mocks.categories.mockResolvedValue([category, folder]);
    const bulkRun = {
      id: "bulk-1",
      operation: "approve" as const,
      status: "awaiting_confirmation" as const,
      actor_user_id: 2,
      target_category_id: null,
      note: null,
      confirmation_phrase: null,
      total_files: 1,
      selected_files: 1,
      completed_files: 0,
      failed_files: 0,
      total_folders: 1,
      total_bytes: 64,
      processed_bytes: 0,
      archive_filename: null,
      error_summary: null,
      created_at: 1,
      started_at: null,
      finished_at: null,
      expires_at: null,
      updated_at: 1,
      max_archive_bytes: 10 * 1024 ** 3,
      categories: [{
        run_id: "bulk-1", category_id: folder.id, parent_id: folder.parent_id,
        full_path: folder.full_path, archive_path: "02 模型目录", version: 1,
        root_category_id: folder.id, is_root: true, eligible: true, selected: false,
        reason: null, result_status: "pending" as const, result_message: null, sort_order: 0,
      }],
      items: [{
        run_id: "bulk-1", item_id: item.item_id, version_id: item.version_id,
        category_id: folder.id, category_path: folder.full_path,
        archive_path: "02 模型目录/standard.pdf", title: item.title,
        original_filename: item.original_filename, content_kind: "document",
        lifecycle_status: "awaiting_review", size_bytes: 64, scope_source: "category" as const,
        root_category_id: folder.id, eligible: true, selected: true, reason: null,
        result_status: "pending" as const, result_message: null, index_job_id: null, sort_order: 0,
      }],
    };
    mocks.preflightBulkOperation.mockResolvedValue(bulkRun);
    mocks.updateBulkSelection.mockResolvedValue({
      ...bulkRun,
      selected_files: 0,
      total_bytes: 0,
      items: [{ ...bulkRun.items[0], selected: false }],
    });
    mocks.reviewBulkItem.mockResolvedValue({
      ...bulkRun,
      status: "succeeded" as const,
      selected_files: 0,
      completed_files: 1,
      items: [{ ...bulkRun.items[0], selected: false, result_status: "succeeded" as const }],
    });

    render(<AdminManagedContentPage />);
    await openRootFolder();
    const folderRow = screen.getByTestId(`managed-folder-row-${folder.id}`);
    fireEvent.click(within(folderRow).getByRole("checkbox", { name: "选择文件夹02 模型目录" }));
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量确认" }));

    await waitFor(() => expect(mocks.preflightBulkOperation).toHaveBeenCalledWith(
      "approve",
      [{ category_id: folder.id, expected_version: folder.version }],
      [],
    ));
    const dialog = await screen.findByRole("dialog", { name: "批量确认" });
    expect(within(dialog).getByText(folder.full_path)).toBeInTheDocument();
    expect(within(dialog).getByText(item.original_filename, { exact: false })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("checkbox", { name: `选择${item.title}` }));
    await waitFor(() => expect(mocks.updateBulkSelection).toHaveBeenCalledWith("bulk-1", [item.item_id], false));

    fireEvent.click(within(dialog).getByRole("button", { name: "通过" }));
    await waitFor(() => expect(mocks.reviewBulkItem).toHaveBeenCalledWith("bulk-1", item.item_id, true, ""));
    await waitFor(() => expect(within(dialog).getByText("批量操作已完成")).toBeInTheDocument());
    expect(mocks.preflightBulkOperation).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mocks.items.mock.calls.length).toBeGreaterThan(2));
  });

  it("shows a folder-only root instead of the empty state", async () => {
    mocks.categories.mockResolvedValue([category, projectCategory]);
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    render(<AdminManagedContentPage />);

    expect(await screen.findByTestId(`managed-folder-row-${category.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`managed-folder-row-${projectCategory.id}`)).toBeInTheDocument();
    expect(screen.queryByText("没有符合条件的资料")).not.toBeInTheDocument();
    expect(screen.getByText("共 0 份，第 1 / 1 页")).toBeInTheDocument();
  });

  it("shows folder detail and orders all folder actions", async () => {
    mocks.permissions = [...CATEGORY_MANAGER_PERMISSIONS, "item.download"];
    const detailedFolder = { ...childCategory, item_count: 2, direct_child_count: 1, total_child_count: 3, total_item_count: 9 };
    mocks.categories.mockResolvedValue([category, detailedFolder]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const row = screen.getByTestId(`managed-folder-row-${detailedFolder.id}`);
    const labels = within(row).getAllByRole("button").map((button) => button.getAttribute("aria-label") || button.textContent);
    expect(labels.slice(-7)).toEqual([
      expect.stringContaining("打开文件夹"),
      expect.stringContaining("查看文件夹"),
      expect.stringContaining("重命名文件夹"),
      expect.stringContaining("调整文件夹"),
      expect.stringContaining("移动文件夹"),
      expect.stringContaining("打包下载文件夹"),
      expect.stringContaining("删除文件夹"),
    ]);

    fireEvent.click(within(row).getByRole("button", { name: /查看文件夹/ }));
    const dialog = await screen.findByRole("dialog", { name: "01 建模标准" });
    expect(within(dialog).getByText("03 公司内部标准 / 01 建模标准")).toBeInTheDocument();
    expect(within(dialog).getByText("9 份")).toBeInTheDocument();
  });

  it("downloads a whole folder with the bulk packaging toast", async () => {
    mocks.permissions = [...CATEGORY_MANAGER_PERMISSIONS, "item.download"];
    const downloadableFolder = { ...childCategory, total_item_count: 4 };
    mocks.categories.mockResolvedValue([category, downloadableFolder]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const row = screen.getByTestId(`managed-folder-row-${downloadableFolder.id}`);
    fireEvent.click(within(row).getByRole("button", { name: /打包下载文件夹/ }));

    await waitFor(() => expect(mocks.downloadCategory).toHaveBeenCalledWith("cat-03-01", "01 建模标准-资料打包下载.zip"));
    expect(mocks.info).toHaveBeenCalledWith("正在打包 4 份资料，请稍候…", expect.objectContaining({ id: "managed-content-bulk-download" }));
    expect(mocks.success).toHaveBeenCalledWith("已打包“01 建模标准”并开始下载", expect.objectContaining({ id: "managed-content-bulk-download" }));
  });

  it("confirms an occupied folder number and shifts sibling numbers", async () => {
    mocks.permissions = CATEGORY_MANAGER_PERMISSIONS;
    const sibling = { ...childCategory, id: "cat-03-02", display_code: "02", display_name: "第二目录", sort_order: 30 };
    mocks.categories.mockResolvedValue([category, childCategory, sibling]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const row = screen.getByTestId(`managed-folder-row-${childCategory.id}`);
    fireEvent.click(within(row).getByRole("button", { name: /调整文件夹/ }));
    let dialog = screen.getByRole("dialog", { name: "调整文件夹编号" });
    fireEvent.change(within(dialog).getByRole("spinbutton", { name: "目标编号" }), { target: { value: "2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "下一步" }));
    dialog = screen.getByRole("dialog", { name: "确认调整编号" });
    expect(dialog).toHaveTextContent("编号 02 当前由“第二目录”使用");
    expect(dialog).toHaveTextContent("整体调整同级文件夹编号");
    fireEvent.click(within(dialog).getByRole("button", { name: "继续调整" }));
    await waitFor(() => expect(mocks.updateCategoryNumber).toHaveBeenCalledWith(childCategory.id, {
      target_position: 2,
      confirm_number_shift: true,
      expected_version: childCategory.version,
    }));
  });

  it("blocks normalized sibling name conflicts and renames with optimistic versioning", async () => {
    mocks.permissions = CATEGORY_MANAGER_PERMISSIONS;
    const sibling = { ...childCategory, id: "cat-03-02", display_code: "02", display_name: "项目 资料" };
    mocks.categories.mockResolvedValue([category, childCategory, sibling]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const row = screen.getByTestId(`managed-folder-row-${childCategory.id}`);
    fireEvent.click(within(row).getByRole("button", { name: /重命名文件夹/ }));
    let dialog = screen.getByRole("dialog", { name: "重命名文件夹" });
    const input = within(dialog).getByRole("textbox", { name: "文件夹名称" });
    fireEvent.change(input, { target: { value: " 项目 资料 " } });
    expect(dialog).toHaveTextContent("当前目录已有同名文件夹");
    expect(within(dialog).getByRole("button", { name: "保存名称" })).toBeDisabled();

    fireEvent.change(input, { target: { value: "新目录名称" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存名称" }));
    await waitFor(() => expect(mocks.renameCategory).toHaveBeenCalledWith(childCategory.id, {
      display_name: "新目录名称",
      expected_version: childCategory.version,
    }));
    dialog = screen.queryByRole("dialog", { name: "重命名文件夹" }) as HTMLElement;
    expect(dialog).not.toBeInTheDocument();
  });

  it("reuses the tree picker and allows an old number collision because the target is renumbered", async () => {
    mocks.permissions = CATEGORY_MANAGER_PERMISSIONS;
    const destination = { ...projectCategory, item_count: 0 };
    const conflict = { ...childCategory, id: "cat-04-01", parent_id: destination.id, display_code: childCategory.display_code, display_name: "其他名称", full_path: `${destination.full_path} / ${childCategory.display_code} 其他名称` };
    mocks.categories.mockResolvedValue([category, childCategory, destination, conflict]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const row = screen.getByTestId(`managed-folder-row-${childCategory.id}`);
    fireEvent.click(within(row).getByRole("button", { name: /移动文件夹/ }));
    const dialog = screen.getByRole("dialog", { name: "移动文件夹位置" });
    const destinationNode = within(dialog).getByTestId(`category-picker-item-${destination.id}`);
    expect(destinationNode).toHaveAttribute("aria-disabled", "false");
    fireEvent.click(destinationNode);
    expect(dialog).toHaveTextContent("移动后自动排在末尾并重新编号");
    fireEvent.click(within(dialog).getByRole("button", { name: "展开公司内部标准" }));
    expect(within(dialog).getByTestId(`category-picker-item-${childCategory.id}`)).toHaveTextContent("不能移动到文件夹自身");
  });

  it("allows moving a folder below a fourth-level destination", async () => {
    mocks.permissions = CATEGORY_MANAGER_PERMISSIONS;
    const destination = { ...projectCategory, level: 4, item_count: 0 };
    mocks.categories.mockResolvedValue([category, childCategory, destination]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const row = screen.getByTestId(`managed-folder-row-${childCategory.id}`);
    fireEvent.click(within(row).getByRole("button", { name: /移动文件夹/ }));
    const dialog = screen.getByRole("dialog", { name: "移动文件夹位置" });
    expect(within(dialog).getByTestId(`category-picker-item-${destination.id}`)).toHaveAttribute("aria-disabled", "false");
  });

  it("sorts folder and file groups independently while keeping folders first", async () => {
    const alphaFolder = { ...childCategory, id: "folder-alpha", display_code: "01", display_name: "A 目录" };
    const zetaFolder = { ...childCategory, id: "folder-zeta", display_code: "09", display_name: "Z 目录" };
    const alphaFile = { ...item, item_id: "file-alpha", version_id: "version-alpha", title: "A 资料" };
    const zetaFile = { ...item, item_id: "file-zeta", version_id: "version-zeta", title: "Z 资料" };
    mocks.categories.mockResolvedValue([category, zetaFolder, alphaFolder]);
    mocks.items.mockResolvedValue({ items: [zetaFile, alphaFile], total: 2, status_counts: { awaiting_review: 2 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const table = screen.getByRole("table");
    const titleSortButton = within(table).getByRole("button", { name: /^资料$/ });
    fireEvent.click(titleSortButton);
    let rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("01 A 目录"),
      expect.stringContaining("09 Z 目录"),
      expect.stringContaining("A 资料"),
      expect.stringContaining("Z 资料"),
    ]);

    fireEvent.click(titleSortButton);
    rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("09 Z 目录"),
      expect.stringContaining("01 A 目录"),
      expect.stringContaining("Z 资料"),
      expect.stringContaining("A 资料"),
    ]);

    fireEvent.click(within(table).getByRole("button", { name: /更新时间/ }));
    rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("01 A 目录");
    expect(rows[1]).toHaveTextContent("09 Z 目录");
  });

  it("keeps search and filters scoped to the current directory", async () => {
    render(<AdminManagedContentPage />);
    const search = screen.getByRole("textbox", { name: "搜索资料" });
    expect(search).toBeDisabled();
    expect(search).toHaveAttribute("placeholder", "选择目录后搜索资料");
    await openRootFolder();

    expect(search).toBeEnabled();
    fireEvent.focus(search);
    const filters = screen.getByRole("dialog", { name: "搜索筛选" });
    expect(within(filters).queryByRole("combobox", { name: "分类" })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "标准" } });
    fireEvent.change(within(filters).getByRole("combobox", { name: "状态" }), { target: { value: "published" } });
    fireEvent.change(within(filters).getByRole("combobox", { name: "来源" }), { target: { value: "web" } });
    await waitFor(() => expect(mocks.items).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "标准",
      category_id: category.id,
      lifecycle_status: "published",
      source_origin: "web",
    })));

    fireEvent.click(within(filters).getByRole("button", { name: "清除搜索与筛选" }));
    await waitFor(() => expect(mocks.items).toHaveBeenLastCalledWith(expect.objectContaining({
      query: undefined,
      category_id: category.id,
      lifecycle_status: undefined,
      source_origin: undefined,
    })));
    expect(search).toHaveValue("");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(search).toHaveFocus();
    expect(screen.queryByRole("dialog", { name: "搜索筛选" })).not.toBeInTheDocument();

    fireEvent.focus(search);
    expect(screen.getByRole("dialog", { name: "搜索筛选" })).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog", { name: "搜索筛选" })).not.toBeInTheDocument();
  });

  it("loads all statuses by default and keeps batch actions hidden without multi-selection", async () => {
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");

    expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({
      lifecycle_status: undefined,
    }));
    fireEvent.focus(screen.getByRole("textbox", { name: "搜索资料" }));
    expect(within(screen.getByRole("dialog", { name: "搜索筛选" })).getByRole("combobox", { name: "状态" })).toHaveValue("");
    expect(screen.getAllByRole("status").some((node) => node.textContent?.includes("未选择资料"))).toBe(true);
    expect(screen.queryByRole("button", { name: "批量操作" })).not.toBeInTheDocument();
  });

  it("places index tasks after upload tasks and keeps the selected view in the URL", async () => {
    mocks.permissions = [...PUBLISHER_PERMISSIONS, "item.upload"];
    render(<AdminManagedContentPage />);

    const tabs = screen.getByRole("tablist", { name: "资料视图" });
    expect(within(tabs).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "资料列表", "回收站", "上传任务", "索引任务",
    ]);

    fireEvent.click(within(tabs).getByRole("tab", { name: "索引任务" }));
    expect(window.location.search).toBe("?view=index");
    expect(screen.getByRole("heading", { name: "资料管理", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "索引任务", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "索引任务" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(mocks.indexJobs).toHaveBeenCalled());
  });

  it("restores a permitted index view from a direct URL", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    window.history.replaceState({}, "", "/admin/content?view=index");
    render(<AdminManagedContentPage />);

    expect(await screen.findByRole("heading", { name: "索引任务", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "索引任务" })).toHaveAttribute("aria-selected", "true");
  });

  it("falls back to the library when index view permission is missing", async () => {
    window.history.replaceState({}, "", "/admin/content?view=index");
    render(<AdminManagedContentPage />);

    await waitFor(() => expect(window.location.search).toBe(""));
    expect(screen.getByRole("tab", { name: "资料列表" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("heading", { name: "索引任务" })).not.toBeInTheDocument();
  });

  it("switches the active top-level folder and its upload target", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.categories.mockResolvedValue([category, projectCategory]);
    render(<AdminManagedContentPage />);

    expect(await screen.findByRole("button", { name: "/" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("managed-folder-row-cat-04"));

    await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({ category_id: "cat-04" })));
    expect(screen.queryByText(/当前目录：/)).not.toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "资料路径" })).getByRole("button", { name: "04 项目资料" })).toBeInTheDocument();
  });

  it("shows archived metadata and restores an item from trash", async () => {
    mocks.categories.mockResolvedValue([category, projectCategory]);
    mocks.trash.mockResolvedValue({
      items: [{ ...item, archived_at: 1_700_000_000, archived_by_name: "整理员", pre_archive_lifecycle_status: "published" }],
      total: 1,
      status_counts: { published: 1 },
    });
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    expect((await screen.findAllByText("项目资料/建模标准/standard.pdf")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("03 公司内部标准").length).toBeGreaterThan(0);
    expect(screen.getAllByText("standard.pdf · v1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("整理员").length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "恢复" })[0]);
    expect(screen.getByRole("dialog")).toHaveTextContent("重新发布后才会进入检索");
    fireEvent.click(screen.getByTestId("category-picker-item-cat-04"));
    fireEvent.click(screen.getByTestId("category-picker-item-cat-03"));
    fireEvent.click(screen.getByRole("button", { name: "确认恢复" }));
    await waitFor(() => expect(mocks.restoreContent).toHaveBeenCalledWith("item-1", "version-1", { target_category_id: "cat-03" }));
  });

  it("selects trash items in the list and preflights the chosen batch from the restore dialog", async () => {
    const second = { ...item, item_id: "item-2", version_id: "version-2", title: "项目标准", original_filename: "project.pdf" };
    mocks.trash.mockResolvedValue({ items: [
      { ...item, archived_at: 1_700_000_000, retention_status: "retained", retention_days_remaining: 30 },
      { ...second, archived_at: 1_699_000_000, retention_status: "overdue", retention_days_remaining: -2 },
    ], total: 2, status_counts: {}, retention_counts: { retained: 1, expiring: 0, overdue: 1 } });
    mocks.preflightBulkRestore.mockResolvedValue({ results: [
      { item_id: "item-1", version_id: "version-1", status: "ready", message: "可以恢复", target_category_path: "03 公司内部标准" },
      { item_id: "item-2", version_id: "version-2", status: "ready", message: "可以恢复", target_category_path: "03 公司内部标准" },
    ], ready: 2, blocked: 0 });
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    fireEvent.click((await screen.findAllByRole("checkbox", { name: "选择恢复“建模标准”" }))[0]);
    expect(screen.getByRole("button", { name: "恢复所选（1）" })).toBeVisible();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择恢复“项目标准”" })[0]);
    expect(screen.getByRole("status")).toHaveTextContent("已选择 2 份");
    fireEvent.click(screen.getByRole("button", { name: "恢复所选（2）" }));
    const dialog = screen.getByRole("dialog", { name: "批量恢复" });
    expect(within(dialog).getByRole("combobox", { name: "恢复到" })).toHaveValue("original");
    fireEvent.click(within(dialog).getByRole("button", { name: "检查恢复条件" }));
    await waitFor(() => expect(mocks.preflightBulkRestore).toHaveBeenCalledWith([
      { item_id: "item-1", expected_version_id: "version-1" },
      { item_id: "item-2", expected_version_id: "version-2" },
    ], undefined));
    expect(await screen.findByRole("dialog", { name: "确认批量恢复" })).toHaveTextContent("可恢复 2 份");
  });

  it("preflights selected trash items and requires the exact permanent-delete phrase", async () => {
    mocks.permissions = [...REVIEWER_PERMISSIONS, "trash.purge", "trash.policy_manage"];
    mocks.trash.mockResolvedValue({ items: [
      { ...item, archived_at: 1_700_000_000, retention_status: "overdue", retention_days_remaining: -2 },
    ], total: 1, status_counts: {}, retention_counts: { retained: 0, expiring: 0, overdue: 1 } });
    mocks.preflightTrashPurge.mockResolvedValue({
      items: [{ item_id: "item-1", version_id: "version-1", status: "ready", reason: null, title: "建模标准", original_filename: "standard.pdf", category_path: "03 公司内部标准", size_bytes: 1024, content_kind: "document", media_count: 0, transcript_version_count: 0, artifact_count: 0, index_job_count: 0 }],
      ready_count: 1, blocked_count: 0, total_size_bytes: 1024, media_count: 0, transcript_version_count: 0, artifact_count: 0, index_job_count: 0, confirmation_phrase: "永久删除 1 份资料",
    });
    mocks.purgeTrash.mockResolvedValue({ run_id: "purge-1", status: "succeeded", candidate_count: 1, succeeded_count: 1, failed_count: 0 });
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    fireEvent.click((await screen.findAllByRole("checkbox", { name: "选择恢复“建模标准”" }))[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "永久删除所选（1）" }));
    const dialog = await screen.findByRole("dialog", { name: "永久删除资料" });
    const confirm = within(dialog).getByRole("button", { name: "永久删除" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "永久删除 1 份资料" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(mocks.purgeTrash).toHaveBeenCalledWith([
      { item_id: "item-1", expected_version_id: "version-1" },
    ], "永久删除 1 份资料"));
  });

  it("shows media impact counts and requires the media-specific purge phrase", async () => {
    mocks.permissions = [...REVIEWER_PERMISSIONS, "trash.purge"];
    const mediaItem = { ...item, item_id: "media-item-1", version_id: "media-version-1", title: "培训视频", original_filename: "training.mp4", content_kind: "media_transcript", media_id: "media-1", doc_type: "transcript" };
    mocks.trash.mockResolvedValue({ items: [
      { ...mediaItem, archived_at: 1_700_000_000, retention_status: "retained", retention_days_remaining: 20 },
    ], total: 1, status_counts: {}, retention_counts: { retained: 1, expiring: 0, overdue: 0 } });
    mocks.preflightTrashPurge.mockResolvedValue({
      items: [{ item_id: "media-item-1", version_id: "media-version-1", status: "ready", reason: null, title: "培训视频", original_filename: "training.mp4", category_path: "05 培训资料", size_bytes: 4096, content_kind: "media_transcript", media_count: 1, transcript_version_count: 3, artifact_count: 2, index_job_count: 4 }],
      ready_count: 1, blocked_count: 0, total_size_bytes: 4096, media_count: 1, transcript_version_count: 3, artifact_count: 2, index_job_count: 4, confirmation_phrase: "永久删除 1 份资料（含 1 个视频）",
    });
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    fireEvent.click((await screen.findAllByRole("checkbox", { name: "选择恢复“培训视频”" }))[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "永久删除所选（1）" }));
    const dialog = await screen.findByRole("dialog", { name: "永久删除资料" });
    expect(dialog).toHaveTextContent("视频文件1 个");
    expect(dialog).toHaveTextContent("转录版本3 个");
    expect(dialog).toHaveTextContent("转录产物2 个");
    expect(dialog).toHaveTextContent("索引任务记录4 条");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("全部转录版本及其检索数据");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "永久删除 1 份资料（含 1 个视频）" } });
    expect(within(dialog).getByRole("button", { name: "永久删除" })).toBeEnabled();
  });

  it("keeps trash filters inside search and sorts from the archived-time column", async () => {
    mocks.categories.mockResolvedValue([category, childCategory]);
    mocks.trash.mockResolvedValue({
      items: [{ ...item, archived_at: 1_700_000_000, retention_status: "retained", retention_days_remaining: 30 }],
      total: 1,
      status_counts: {},
      retention_counts: { retained: 1, expiring: 0, overdue: 0 },
    });
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await screen.findAllByText("建模标准");

    expect(screen.queryByLabelText("回收站筛选")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开回收站筛选" }));
    const filters = screen.getByRole("dialog", { name: "回收站搜索筛选" });
    expect(within(filters).getByRole("option", { name: "保留中（1）" })).toBeInTheDocument();
    fireEvent.change(within(filters).getByRole("combobox", { name: "保留状态" }), { target: { value: "retained" } });
    fireEvent.change(within(filters).getByRole("textbox", { name: "移入人员" }), { target: { value: "管理员" } });
    fireEvent.click(within(filters).getByRole("button", { name: "全部目录" }));
    fireEvent.click(within(filters).getByTestId("category-cascader-desktop-option-cat-03"));
    fireEvent.click(within(filters).getByTestId("category-cascader-desktop-option-cat-03-01"));
    fireEvent.click(within(filters).getByRole("button", { name: "选择当前目录" }));
    await waitFor(() => expect(mocks.trash).toHaveBeenLastCalledWith(expect.objectContaining({
      retention_status: "retained",
      archived_by: "管理员",
      category_id: "cat-03-01",
    })));

    fireEvent.click(screen.getByRole("button", { name: /移入回收站/ }));
    await waitFor(() => expect(mocks.trash).toHaveBeenLastCalledWith(expect.objectContaining({ sort_direction: "asc" })));
  });

  it("keeps the restore dialog open and confirms a same-name replacement", async () => {
    mocks.permissions = [...REVIEWER_PERMISSIONS, "item.archive_published"];
    mocks.trash.mockResolvedValue({
      items: [{ ...item, archived_at: 1_700_000_000, archived_by_name: "整理员", pre_archive_lifecycle_status: "published" }],
      total: 1,
      status_counts: { published: 1 },
    });
    const conflict = {
      item_id: "item-conflict", version_id: "version-conflict", title: "现有标准",
      original_filename: "standard.pdf", lifecycle_status: "published", has_published_head: true,
    };
    mocks.restoreContent
      .mockRejectedValueOnce(Object.assign(new Error("当前目录下已存在同名资料"), {
        code: "content_filename_conflict",
        body: JSON.stringify({ detail: { conflict } }),
      }))
      .mockResolvedValueOnce({ item_id: "item-1", version_id: "version-1", restored_status: "approved", category_id: "cat-03", moved_to_alternate_category: false, replaced_conflict: true });
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    fireEvent.click((await screen.findAllByRole("button", { name: "恢复" }))[0]);
    fireEvent.click(screen.getByRole("button", { name: "确认恢复" }));
    expect(await screen.findByText("所选目录存在同名资料")).toBeInTheDocument();
    expect(screen.getByText(/现有标准/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "替换并恢复" }));
    await waitFor(() => expect(mocks.restoreContent).toHaveBeenLastCalledWith("item-1", "version-1", {
      target_category_id: "cat-03",
      replace_conflict_item_id: "item-conflict",
      replace_conflict_expected_version_id: "version-conflict",
    }));
    expect(mocks.success).toHaveBeenCalledWith("已替换同名资料并恢复“建模标准”");
  });

  it("shows productized trash audit records to a view-only publisher", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.trash.mockResolvedValue({ items: [{ ...item, archived_at: 1_700_000_000 }], total: 1, status_counts: {} });
    mocks.auditEvents.mockResolvedValue([{ event_type: "content.archived", actor_name: "整理员", created_at: 1_700_000_000, previous_status: "published", restored_status: null, restore_strategy: null, source_category_path: null, target_category_path: null, category_path: "03 公司内部标准", archive_reason: null, replaced_title: null, replaced_filename: null }]);
    render(<AdminManagedContentPage />);
    fireEvent.click(screen.getByRole("tab", { name: "回收站" }));
    fireEvent.click((await screen.findAllByRole("button", { name: /记录/ }))[0]);
    expect(await screen.findByRole("dialog", { name: "操作记录" })).toHaveTextContent("操作人：整理员");
    expect(mocks.auditEvents).toHaveBeenCalledWith("item-1");
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
    expect((await screen.findAllByText("发布负责人")).length).toBeGreaterThan(0);
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

    fireEvent.click(screen.getAllByRole("button", { name: "删除“建模标准”" })[0]);
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
    expect(screen.getAllByRole("button", { name: "删除“建模标准”" }).every((button) => button.hasAttribute("disabled"))).toBe(true);
    firstRender.unmount();

    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({
      items: [{ ...item, lifecycle_status: "publishing" }],
      total: 1,
      status_counts: { publishing: 1 },
    });
    const { unmount } = render(<AdminManagedContentPage />);
    await openRootFolder();
    const disabledDelete = screen.getAllByRole("button", { name: "删除“建模标准”" })[0];
    expect(disabledDelete).toBeDisabled();
    expect(disabledDelete.parentElement).toHaveAttribute("aria-label", "删除“建模标准”：资料正在发布，暂不能移入回收站");
    unmount();
  });

  it.skip("explains row actions on hover and gives specific reasons for disabled actions", async () => {
    mocks.items.mockResolvedValue({
      items: [{ ...item, lifecycle_status: "published", has_published_head: true }],
      total: 1,
      status_counts: { published: 1 },
    });
    const firstRender = render(<AdminManagedContentPage />);
    await openRootFolder();

    const details = screen.getAllByRole("button", { name: /查看“建模标准”的详细信息/ })[0];
    fireEvent.mouseEnter(details.parentElement!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("查看资料详情");
    fireEvent.mouseLeave(details.parentElement!);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    const disabledMove = screen.getAllByRole("button", { name: /调整“建模标准”的分类/ })[0];
    expect(disabledMove).toBeDisabled();
    fireEvent.mouseEnter(disabledMove.parentElement!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("存在待处理的新版本，暂时不能调整正式分类");
    fireEvent.click(disabledMove);
    expect(screen.queryByRole("dialog", { name: /调整分类/ })).not.toBeInTheDocument();
    firstRender.unmount();

    mocks.permissions = [];
    mocks.items.mockResolvedValue({ items: [item], total: 1, status_counts: { awaiting_review: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    const permissionBlockedMove = screen.getAllByRole("button", { name: /移动“建模标准”/ })[0];
    expect(permissionBlockedMove.parentElement).toHaveAttribute("aria-label", "移动“建模标准”：当前账号没有移动待确认资料的权限");
    fireEvent.focus(permissionBlockedMove.parentElement!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("当前账号没有移动待确认资料的权限");
    fireEvent.blur(permissionBlockedMove.parentElement!);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("keeps the delete dialog open with a recoverable conflict message", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.deleteContent.mockRejectedValue(new Error("资料版本已变化，请刷新后重试"));
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("button", { name: "删除“建模标准”" })[0]);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "确认移入回收站" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("资料版本已变化，请刷新后重试");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it.skip("shows the batch menu only after selecting more than one file", async () => {
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    mocks.bulkReview.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    expect(screen.getByText("已选择", { exact: false })).toHaveTextContent("已选择 1 份");
    expect(screen.getByText("已选择", { exact: false })).not.toHaveTextContent("单次最多");
    expect(screen.queryByRole("button", { name: "批量操作" })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    expect(screen.getByRole("menu", { name: "批量操作" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "批量确认" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("本次工作台包含 2 份资料");
  });

  it.skip("lists draft files before submitting them for review in one batch", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    const firstItem = { ...item, lifecycle_status: "draft" };
    const secondItem = { ...firstItem, item_id: "item-2", title: "建模标准2", version_id: "version-2", original_filename: "standard-2.pdf" };
    mocks.items.mockResolvedValue({ items: [firstItem, secondItem], total: 2, status_counts: { draft: 2 } });
    mocks.bulkSubmit.mockResolvedValue({
      results: [firstItem, secondItem].map((target) => ({ version_id: target.version_id, item_id: target.item_id, status: "succeeded", message: null, index_job_id: null })),
      succeeded: 2,
      failed: 0,
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量提交审核" }));

    const dialog = screen.getByRole("dialog", { name: "批量提交审核" });
    expect(within(dialog).getByLabelText("批量审核文件列表")).toHaveTextContent("建模标准");
    expect(within(dialog).getByLabelText("批量审核文件列表")).toHaveTextContent("建模标准2");
    fireEvent.click(within(dialog).getByRole("button", { name: "一键提交（2）" }));

    await waitFor(() => expect(mocks.bulkSubmit).toHaveBeenCalledWith(["version-1", "version-2"]));
    expect(await within(dialog).findByText("已提交 2")).toBeInTheDocument();
  });

  it.skip("excludes an individually reviewed file from the remaining one-click decision", async () => {
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2", original_filename: "standard-2.pdf" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    mocks.bulkReview
      .mockResolvedValueOnce({ results: [{ version_id: "version-1", status: "succeeded", message: null, index_job_id: null }], succeeded: 1, failed: 0 })
      .mockResolvedValueOnce({ results: [{ version_id: "version-2", status: "succeeded", message: null, index_job_id: null }], succeeded: 1, failed: 0 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量确认" }));

    const dialog = screen.getByRole("dialog", { name: "批量审核资料" });
    const list = within(dialog).getByLabelText("批量审核文件列表");
    fireEvent.click(within(list).getAllByRole("button", { name: "通过" })[0]);
    await waitFor(() => expect(mocks.bulkReview).toHaveBeenNthCalledWith(1, ["version-1"], true, ""));
    fireEvent.change(within(dialog).getByLabelText("批量退回原因"), { target: { value: "请补充依据" } });
    fireEvent.click(await within(dialog).findByRole("button", { name: "一键退回（1）" }));

    await waitFor(() => expect(mocks.bulkReview).toHaveBeenNthCalledWith(2, ["version-2"], false, "请补充依据"));
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
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith([file], "cat-03", "files", expect.any(Function), { allowFolderMerge: false, publishIntents: [false] }));
    expect(mocks.success).toHaveBeenCalledWith("已接收 1 个文件");
  });

  it("opens the conflict review and sends a renamed file action", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    mocks.upload.mockResolvedValue({ batch_id: "batch-conflict", entries: [] });
    mocks.preflightUpload.mockResolvedValue({
      entries: [{
        sequence: 1,
        filename: "guide.md",
        relative_path: null,
        status: "conflict",
        reason: "当前目录下已存在同名资料",
        reason_code: "content_filename_conflict",
        suggested_filename: "guide (1).md",
        conflict: {
          item_id: "item-existing",
          version_id: "version-existing",
          title: "已有指南",
          original_filename: "guide.md",
          lifecycle_status: "draft",
          has_published_head: false,
          can_update: true,
        },
      }],
      folder_conflicts: [],
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(await screen.findByRole("button", { name: "上传文件" }));
    const file = new File(["# Guide"], "guide.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("选择资料文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "确定上传" }));

    const dialog = await screen.findByRole("dialog", { name: "处理上传冲突" });
    expect(dialog).toHaveTextContent("已有指南");
    expect(within(dialog).getByRole("combobox")).toHaveValue("skip");
    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "rename" } });
    expect(within(dialog).getByLabelText("新文件名")).toHaveValue("guide (1).md");
    fireEvent.click(within(dialog).getByRole("button", { name: "按选择上传" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith(
        [file], "cat-03", "files", expect.any(Function), {
          allowFolderMerge: false,
          publishIntents: [false],
        conflictActions: [{ strategy: "rename", filename: "guide (1).md" }],
      },
    ));
  });

  it("lets a folder conflict be merged after the second preflight", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    const folder = new File(["# Guide"], "guide.md", { type: "text/markdown" });
    Object.defineProperty(folder, "webkitRelativePath", { value: "资料包/guide.md" });
    mocks.preflightUpload
      .mockResolvedValueOnce({
        entries: [{ sequence: 1, filename: "guide.md", relative_path: "资料包/guide.md", status: "conflict", reason: "同名文件夹", reason_code: "folder_name_conflict", suggested_filename: null, conflict: null }],
        folder_conflicts: [{ relative_path: "资料包", category_id: "cat-existing", category_path: "03 公司内部标准 / 资料包", display_name: "资料包", suggested_name: "资料包 (1)", can_rename: true }],
      })
      .mockResolvedValueOnce({ entries: [{ sequence: 1, filename: "guide.md", relative_path: "资料包/guide.md", status: "ready", reason: null, reason_code: null, suggested_filename: null, conflict: null }], folder_conflicts: [] });
    mocks.upload.mockResolvedValue({ batch_id: "batch-folder-conflict", entries: [] });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getByRole("button", { name: "上传文件" }));
    fireEvent.click(screen.getByRole("button", { name: "选择文件夹" }));
    fireEvent.change(screen.getByLabelText("选择资料文件夹"), { target: { files: [folder] } });
    fireEvent.click(await screen.findByRole("button", { name: "开始上传" }));
    const dialog = await screen.findByRole("dialog", { name: "处理上传冲突" });
    fireEvent.click(within(dialog).getByRole("button", { name: "合并到现有目录" }));
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith(
      [{ file: folder, relativePath: "资料包/guide.md" }], "cat-03", "folder", expect.any(Function), { allowFolderMerge: true, publishIntents: [false] },
    ));
  });

  it("keeps the background upload failure notice after the dialog closes", async () => {
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
    expect(screen.queryByRole("dialog", { name: "上传文件" })).not.toBeInTheDocument();
    const notices = screen.getAllByRole("status");
    expect(notices.some((notice) => notice.textContent?.includes("上传失败"))).toBe(true);
    expect(notices.some((notice) => notice.textContent?.includes("上传服务暂不可用"))).toBe(true);
  });

  it("keeps file and folder choices inside the upload dropzone", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(await screen.findByRole("button", { name: "上传文件" }));

    const dropzone = await screen.findByTestId("managed-upload-dropzone");
    expect(dropzone).toHaveTextContent("拖动文件或文件夹到这里");
    expect(within(dropzone).getByRole("button", { name: "选择文件" })).toBeInTheDocument();
    expect(within(dropzone).getByRole("button", { name: "选择文件夹" })).toBeInTheDocument();
  });

  it("keeps a plain file dropped in the file upload dialog", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [], total: 0, status_counts: {} });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getByRole("button", { name: "上传文件" }));
    const input = await screen.findByLabelText("选择资料文件");
    const file = new File(["# Dropped"], "dropped.md", { type: "text/markdown" });

    fireEvent.drop(screen.getByTestId("managed-upload-dropzone"), {
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
    fireEvent.click(screen.getByRole("button", { name: "选择文件夹" }));
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
      { allowFolderMerge: false, publishIntents: [false] },
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
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith([file], "cat-03", "files", expect.any(Function), { allowFolderMerge: false, publishIntents: [false] }));
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
    expect(screen.getByTestId("managed-content-drop-overlay")).toHaveTextContent("支持 PDF、Markdown、Word、Excel、PPT 和 XMind 文件");

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
    expect(mocks.error).toHaveBeenCalledWith("没有可上传的支持格式，仅支持 PDF、Markdown、Word、Excel、PPT 和 XMind 文件");
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
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith([file], "cat-03-01", "files", expect.any(Function), { allowFolderMerge: false, publishIntents: [false] }));
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

  it("allows creating a child folder from a fourth-level directory", async () => {
    mocks.permissions = CATEGORY_MANAGER_PERMISSIONS;
    mocks.categories.mockResolvedValue([{ ...category, level: 4 }]);
    render(<AdminManagedContentPage />);
    await openRootFolder();

    expect(await screen.findByRole("button", { name: "新建目录" })).toBeEnabled();
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

  it("queues a published document classification adjustment", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.categories.mockResolvedValue([category, projectCategory]);
    const publishedItem = {
      ...item,
      lifecycle_status: "published",
      is_current: true,
      has_published_head: true,
    };
    mocks.items.mockResolvedValue({ items: [publishedItem], total: 1, status_counts: { published: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(screen.getAllByRole("button", { name: "调整“建模标准”的分类" })[0]);
    const dialog = screen.getByRole("dialog", { name: "调整分类" });
    expect(within(dialog).getByText(/同步完成前资料仍保留在原目录/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByTestId("category-picker-item-cat-04"));
    fireEvent.click(within(dialog).getByRole("button", { name: "提交分类调整" }));

    await waitFor(() => expect(mocks.reclassifyContent).toHaveBeenCalledWith(
      "item-1", "cat-04", "version-1",
    ));
    expect(mocks.success).toHaveBeenCalledWith("分类调整任务已提交");
  });

  it("shows classification progress and disables another adjustment", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({
      items: [{
        ...item,
        lifecycle_status: "published",
        is_current: true,
        has_published_head: true,
        reclassification_job_id: "reclass-1",
        reclassification_status: "applying",
      }],
      total: 1,
      status_counts: { published: 1 },
    });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    expect((await screen.findAllByText("分类调整中")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "调整“建模标准”的分类" })[0]).toBeDisabled();
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
    fireEvent.click(screen.getByRole("menuitem", { name: "批量移动资料" }));

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

    const row = await screen.findByTitle("拖动到文件夹行可调整目录");
    const targetFolder = screen.getByTestId("managed-folder-row-cat-03-01");
    fireEvent.dragStart(row);
    expect(targetFolder).toHaveClass("bg-primary/5");
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

    expect(screen.queryByTitle("拖动到文件夹行可调整目录")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /调整“建模标准”的分类/ }).every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it.skip("keeps workflow first and orders all file actions consistently", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [{ ...item, lifecycle_status: "draft" }], total: 1, status_counts: { draft: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    for (const actionName of ["预览“建模标准”", "查看“建模标准”的详细信息", "重命名“建模标准”", "更新“建模标准”", "移动“建模标准”", "下载“建模标准”", "删除“建模标准”"]) {
      expect(screen.getAllByRole("button", { name: actionName }).length).toBeGreaterThan(0);
    }
    const detailsButton = screen.getAllByRole("button", { name: "查看“建模标准”的详细信息" })[0];
    const iconGroup = detailsButton.parentElement?.parentElement;
    expect(iconGroup).toHaveClass("ml-auto", "justify-end");
    expect(within(iconGroup as HTMLElement).getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual([
      "预览“建模标准”",
      "查看“建模标准”的详细信息",
      "重命名“建模标准”",
      "更新“建模标准”",
      "移动“建模标准”",
      "下载“建模标准”",
      "删除“建模标准”",
    ]);
    const actionGroup = iconGroup?.parentElement;
    expect(actionGroup).not.toBeNull();
    expect(within(actionGroup as HTMLElement).getAllByRole("button")[0]).toHaveTextContent("提交");
    expect(within(actionGroup as HTMLElement).getByRole("button", { name: "提交" })).toHaveClass("w-full");
    fireEvent.click(detailsButton);
    const dialog = screen.getByRole("dialog", { name: "建模标准" });
    expect(within(dialog).queryByRole("button", { name: /下载/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
  });

  it("renders published video transcripts with download and managed secondary actions", async () => {
    mocks.role = "admin";
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [mediaItem], total: 1, status_counts: { published: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    expect((await screen.findAllByText("视频转录稿")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("有新转录稿待处理").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/00:01:05/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "下载“WhisperX 培训视频”" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "重命名“WhisperX 培训视频”" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "删除“WhisperX 培训视频”" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "调整“WhisperX 培训视频”的归档目录" }).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole("button", { name: "播放“WhisperX 培训视频”" })[0]);
    expect(mocks.openVideo).toHaveBeenCalledWith({
      mediaId: mediaItem.media_id,
      title: mediaItem.title,
      startSeconds: 0,
      fromSource: false,
    });
    const moreMenu = openMoreActions("WhisperX 培训视频");
    expect(within(moreMenu).getByRole("menuitem", { name: "编辑转录稿" })).toHaveAttribute("href", `/admin/content?view=transcription&media_id=${mediaItem.media_id}&workbench=1&action=edit-current`);
    expect(within(moreMenu).getByRole("menuitem", { name: "替换视频" })).toHaveAttribute("href", `/admin/content?view=transcription&media_id=${mediaItem.media_id}&action=replace`);
    expect(within(moreMenu).getByRole("menuitem", { name: "进入转录任务" })).toHaveAttribute("href", `/admin/content?view=transcription&media_id=${mediaItem.media_id}&workbench=1`);
  });

  it.each([
    ["仅视频", "video", "training.mp4"],
    ["仅转录稿", "transcript", "WhisperX 培训视频-转录稿.md"],
    ["视频与转录稿", "all", "WhisperX 培训视频-视频资料.zip"],
  ] as const)("downloads published media using the %s choice", async (choice, part, fallbackFilename) => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [mediaItem], total: 1, status_counts: { published: 1 } });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:media") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(screen.getAllByRole("button", { name: "下载“WhisperX 培训视频”" })[0]);
    const dialog = screen.getByRole("dialog", { name: "下载视频资料" });
    expect(within(dialog).getAllByRole("radio")).toHaveLength(3);
    fireEvent.click(within(dialog).getByRole("radio", { name: new RegExp(`^${choice}`) }));
    fireEvent.click(within(dialog).getByRole("button", { name: "开始下载" }));

    await waitFor(() => expect(mocks.downloadMedia).toHaveBeenCalledWith(mediaItem.item_id, part, fallbackFilename));
    expect(mocks.success).toHaveBeenCalledWith("已开始下载“WhisperX 培训视频”");
  });

  it("creates a reviewed media metadata candidate while preserving the published item", async () => {
    mocks.role = "admin";
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [mediaItem], total: 1, status_counts: { published: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(within(openMoreActions("WhisperX 培训视频")).getByRole("menuitem", { name: "编辑媒体信息" }));
    const dialog = screen.getByRole("dialog", { name: "编辑媒体信息" });
    expect(dialog).toHaveTextContent("当前正式名称会保留");
    fireEvent.change(within(dialog).getByLabelText("视频标题"), { target: { value: "更新后的培训视频" } });
    fireEvent.change(within(dialog).getByLabelText(/^源文件名/), { target: { value: "updated-training.mp4" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建修订" }));

    await waitFor(() => expect(mocks.createMediaMetadataRevision).toHaveBeenCalledWith(
      mediaItem.media_id,
      mediaItem.version_id,
      "更新后的培训视频",
      "updated-training.mp4",
      expect.any(String),
    ));
    expect(mocks.success).toHaveBeenCalledWith("媒体信息修订已创建，请在转写工作台审核并发布");
  });

  it("does not send a non-admin publisher into admin-only video management", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [mediaItem], total: 1, status_counts: { published: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const moreMenu = openMoreActions("WhisperX 培训视频");
    for (const action of ["编辑转录稿", "编辑媒体信息", "替换视频", "进入转录任务"]) {
      expect(within(moreMenu).getByRole("menuitem", { name: action })).toBeDisabled();
    }
    expect(within(moreMenu).queryByRole("link")).not.toBeInTheDocument();
  });

  it("filters the library by video transcript type", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    mocks.items.mockResolvedValue({ items: [mediaItem], total: 1, status_counts: { published: 1 } });
    render(<AdminManagedContentPage />);
    await openRootFolder();

    fireEvent.click(screen.getByRole("button", { name: "展开搜索筛选" }));
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(expect.arrayContaining([
      "全部类型", "PDF", "Word", "Excel", "PPT", "Markdown", "视频转录稿", "其他",
    ]));
    fireEvent.change(screen.getByRole("combobox", { name: "类型" }), {
      target: { value: "transcript" },
    });
    await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({
      category_id: category.id,
      doc_type: "transcript",
    })));
  });

  it("requests server-side file type sorting in both directions", async () => {
    render(<AdminManagedContentPage />);
    await openRootFolder();

    const typeSort = screen.getByRole("button", { name: /类型/ });
    fireEvent.click(typeSort);
    await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({
      sort_by: "doc_type",
      sort_direction: "asc",
    })));
    fireEvent.click(typeSort);
    await waitFor(() => expect(mocks.items).toHaveBeenCalledWith(expect.objectContaining({
      sort_by: "doc_type",
      sort_direction: "desc",
    })));
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
    let resolveCapabilities: ((value: { enabled: boolean; max_upload_bytes: number; max_batch_files: number; max_batch_bytes: number; supported_extensions: string[] }) => void) | undefined;
    mocks.capabilities.mockReturnValueOnce(new Promise((resolve) => { resolveCapabilities = resolve; }));
    render(<AdminManagedContentPage />);
    expect(screen.getByText("正在加载资料…")).toBeInTheDocument();
    resolveCapabilities?.({ enabled: true, max_upload_bytes: 1024, max_batch_files: 5000, max_batch_bytes: 10240, supported_extensions: [".pdf"] });
    await openRootFolder();
    expect((await screen.findAllByText("建模标准")).length).toBeGreaterThan(0);

    mocks.capabilities.mockRejectedValueOnce(new Error("资料服务暂不可用"));
    fireEvent.click(screen.getByRole("button", { name: "刷新列表" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("资料服务暂不可用");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it.skip("disables workflow actions and exposes the busy label while saving", async () => {
    let resolveReview: ((value: typeof item) => void) | undefined;
    mocks.review.mockReturnValueOnce(new Promise((resolve) => { resolveReview = resolve; }));
    render(<AdminManagedContentPage />);
    await openRootFolder();
    await screen.findAllByText("建模标准");
    fireEvent.click(screen.getAllByRole("button", { name: "审核" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(screen.getByRole("button", { name: "提交中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "退回修改" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "提交中…" }));
    expect(mocks.review).toHaveBeenCalledTimes(1);
    resolveReview?.({ ...item, lifecycle_status: "approved" });
    await waitFor(() => expect(mocks.success).toHaveBeenCalledWith("资料已确认"));
  });

  it.skip("requires a rejection reason and preserves it after a failed request", async () => {
    mocks.review.mockRejectedValueOnce(new Error("审核服务暂不可用"));
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("button", { name: "审核" })[0]);
    const dialog = screen.getByRole("dialog", { name: "审核资料" });
    fireEvent.click(within(dialog).getByRole("button", { name: "退回修改" }));
    const submit = within(dialog).getByRole("button", { name: "确认退回" });
    expect(submit).toBeDisabled();
    const reason = within(dialog).getByRole("textbox", { name: "退回原因" });
    expect(reason).toHaveAttribute("maxlength", "2000");
    fireEvent.change(reason, { target: { value: "请补充适用范围" } });
    fireEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("审核服务暂不可用");
    expect(reason).toHaveValue("请补充适用范围");
    expect(mocks.review).toHaveBeenCalledWith("version-1", false, "请补充适用范围");
  });

  it.skip("keeps failed bulk items selected and shows their per-item reason", async () => {
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    mocks.bulkReview.mockResolvedValue({
      results: [
        { version_id: "version-1", status: "failed", message: "资料状态已变化，请刷新后重试", index_job_id: null },
        { version_id: "version-2", status: "succeeded", message: null, index_job_id: null },
      ],
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
    const dialog = screen.getByRole("dialog", { name: "批量审核资料" });
    fireEvent.click(within(dialog).getByRole("button", { name: "一键通过（2）" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("资料状态已变化，请刷新后重试");
    expect(within(dialog).getByRole("button", { name: "一键通过（1）" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]).toBeChecked();
  });

  it.skip("requires and submits a reason for bulk rejection", async () => {
    const secondItem = { ...item, item_id: "item-2", title: "建模标准2", version_id: "version-2" };
    mocks.items.mockResolvedValue({ items: [item, secondItem], total: 2, status_counts: { awaiting_review: 2 } });
    mocks.bulkReview.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准2" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量退回" }));
    const dialog = screen.getByRole("dialog", { name: "批量审核资料" });
    const submit = within(dialog).getByRole("button", { name: "一键退回（2）" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("textbox", { name: "批量退回原因" }), { target: { value: "统一补充来源" } });
    fireEvent.click(submit);
    await waitFor(() => expect(mocks.bulkReview).toHaveBeenCalledWith(["version-1", "version-2"], false, "统一补充来源"));
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
    const detailDialog = screen.getByRole("dialog", { name: "建模标准" });
    const republish = within(detailDialog).getByRole("button", { name: "重新发布" });
    expect(republish).toBeEnabled();
    fireEvent.click(republish);
    const dialog = screen.getByRole("dialog", { name: "重新发布资料" });
    expect(dialog).toHaveTextContent("完成后资料才会进入知识库检索");
    expect(mocks.publish).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认重新发布" }));
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
    expect(screen.getByLabelText("批量发布文件列表")).toHaveTextContent("建模标准2");
    fireEvent.click(screen.getByRole("button", { name: "确认发布（2）" }));
    await waitFor(() => expect(mocks.bulkPublish).toHaveBeenCalledWith(["version-1", "version-2"]));
  });

  it("confirms the publishable files and skips already published selections", async () => {
    mocks.permissions = PUBLISHER_PERMISSIONS;
    const approvedItem = { ...item, lifecycle_status: "approved" };
    const publishedItem = { ...item, item_id: "item-2", title: "已发布标准", version_id: "version-2", lifecycle_status: "published", has_published_head: true };
    mocks.items.mockResolvedValue({ items: [approvedItem, publishedItem], total: 2, status_counts: { approved: 1, published: 1 } });
    mocks.bulkPublish.mockResolvedValue({ results: [{ version_id: "version-1", status: "succeeded", message: null, index_job_id: "job-3" }], succeeded: 1, failed: 0 });
    render(<AdminManagedContentPage />);
    await openRootFolder();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择建模标准" })[0]);
    fireEvent.click(screen.getAllByRole("checkbox", { name: "选择已发布标准" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "批量发布" }));

    const dialog = screen.getByRole("dialog", { name: "批量发布资料" });
    expect(within(dialog).getByText("将发布 1")).toBeInTheDocument();
    expect(within(dialog).getByText("已跳过 1")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("批量发布文件列表")).toHaveTextContent("建模标准");
    expect(within(dialog).getByLabelText("批量发布跳过列表")).toHaveTextContent("已发布标准");
    expect(within(dialog).getByLabelText("批量发布跳过列表")).toHaveTextContent("已发布，无需重复操作");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认发布（1）" }));

    await waitFor(() => expect(mocks.bulkPublish).toHaveBeenCalledWith(["version-1"]));
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
    expect(screen.getByText("已接收 1 个 · 跳过 1 个")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "处理结果：已接收 1 个 · 跳过 1 个" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(await screen.findByText("文件明细")).toBeInTheDocument();
    const detailDialog = screen.getByRole("dialog", { name: "上传任务详情" });
    expect(within(detailDialog).getByRole("progressbar", { name: "任务处理进度 100%" })).toBeInTheDocument();
    expect(within(detailDialog).getByText("资料包/video.mp4")).toBeInTheDocument();
    fireEvent.click(within(detailDialog).getByRole("button", { name: "已接收 1" }));
    expect(within(detailDialog).queryByText("资料包/video.mp4")).not.toBeInTheDocument();
    fireEvent.change(within(detailDialog).getByRole("searchbox", { name: "搜索任务文件" }), { target: { value: "missing" } });
    expect(within(detailDialog).getByText("没有符合条件的文件")).toBeInTheDocument();
    fireEvent.click(within(detailDialog).getByRole("button", { name: "全部 2" }));
    fireEvent.change(within(detailDialog).getByRole("searchbox", { name: "搜索任务文件" }), { target: { value: "video" } });
    expect(within(detailDialog).getByText("资料包/video.mp4")).toBeInTheDocument();
    expect(mocks.uploadTask).toHaveBeenCalledWith("batch-upload-1");

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("tab", { name: "资料列表" }));
    expect(window.location.search).toBe("");
  });

  it("submits upload task searches and toggles summary filters", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    const task = {
      batch_id: "batch-upload-search",
      upload_mode: "files" as const,
      status: "failed" as const,
      target_category_id: "cat-03",
      target_path: "03 公司内部标准 / 02 文件夹上传测试",
      total_files: 1,
      accepted_files: 0,
      skipped_files: 0,
      total_bytes: 10,
      total_uploaded_bytes: 0,
      created_by_name: "整理员",
      created_at: 1,
      updated_at: 2,
      error_summary: "上传中断",
      entries: null,
    };
    mocks.uploadTasks.mockResolvedValue({ tasks: [task], total: 1, status_counts: { failed: 1 } });
    window.history.replaceState({}, "", "/admin/content?view=uploads");

    render(<AdminManagedContentPage />);
    expect(await screen.findByText("03 公司内部标准 / 02 文件夹上传测试")).toBeInTheDocument();
    expect(screen.getByText("未完成 1 个")).toBeInTheDocument();
    const failedRow = screen.getByTestId("upload-task-row");
    const failedActions = within(failedRow).getAllByRole("button");
    expect(failedActions.map((button) => button.textContent)).toEqual(["重试", "详情"]);
    expect(failedActions[0]).toBeDisabled();
    expect(failedActions[0]).toHaveAttribute("title", "原始文件仅保留在当前浏览器会话中，当前不可重试");

    const search = screen.getByRole("searchbox", { name: "搜索上传任务" });
    fireEvent.change(search, { target: { value: " guide.md " } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(mocks.uploadTasks).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "guide.md",
      status: undefined,
      offset: 0,
    })));

    const failedFilter = screen.getByRole("button", { name: /失败\s*1/ });
    fireEvent.click(failedFilter);
    await waitFor(() => expect(mocks.uploadTasks).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "guide.md",
      status: "failed",
    })));
    expect(failedFilter).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(failedFilter);
    await waitFor(() => expect(mocks.uploadTasks).toHaveBeenLastCalledWith(expect.objectContaining({ status: undefined })));
    expect(failedFilter).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    await waitFor(() => expect(mocks.uploadTasks).toHaveBeenLastCalledWith(expect.objectContaining({
      query: undefined,
      status: undefined,
    })));
    expect(search).toHaveValue("");
  });

  it("supports upload-task selection, summary export, and pagination resets", async () => {
    mocks.permissions = ORGANIZER_PERMISSIONS;
    const tasks = Array.from({ length: 21 }, (_, index) => ({
      batch_id: `batch-page-${index + 1}`,
      upload_mode: index % 2 ? "folder" as const : "files" as const,
      status: index === 0 ? "failed" as const : "completed" as const,
      target_category_id: "cat-03",
      target_path: `03 公司内部标准 / 任务 ${index + 1}`,
      total_files: 2,
      accepted_files: index === 0 ? 0 : 2,
      skipped_files: 0,
      total_bytes: 20,
      total_uploaded_bytes: index === 0 ? 0 : 20,
      created_by_name: "整理员",
      created_at: index + 1,
      updated_at: index + 1,
      error_summary: index === 0 ? "上传中断" : null,
      entries: null,
    }));
    mocks.uploadTasks.mockImplementation(async (params: { limit?: number; offset?: number } = {}) => {
      const limit = params.limit || 10;
      const offset = params.offset || 0;
      return { tasks: tasks.slice(offset, offset + limit), total: tasks.length, status_counts: { completed: 20, failed: 1 } };
    });
    window.history.replaceState({}, "", "/admin/content?view=uploads");
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:upload-summary") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });

    render(<AdminManagedContentPage />);
    expect(await screen.findByText("03 公司内部标准 / 任务 1")).toBeInTheDocument();
    const rows = screen.getAllByTestId("upload-task-row");
    fireEvent.click(within(rows[0]).getByRole("checkbox"));
    fireEvent.click(within(rows[1]).getByRole("checkbox"));
    expect(screen.getAllByRole("status").some((status) => status.textContent?.includes("已选择 2 个"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "导出任务摘要" }));
    expect(mocks.success).toHaveBeenCalledWith("已导出 2 个任务摘要");

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(mocks.uploadTasks).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 10, offset: 10 })));
    expect(screen.queryByRole("button", { name: "批量操作" })).not.toBeInTheDocument();
    expect(screen.getByText("03 公司内部标准 / 任务 11")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "每页上传任务条数" }), { target: { value: "25" } });
    await waitFor(() => expect(mocks.uploadTasks).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 25, offset: 0 })));
    expect(screen.getByText("共 21 个任务，第 1 / 1 页")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择当前页上传任务" }));
    expect(screen.getAllByRole("status").some((status) => status.textContent?.includes("已选择 21 个"))).toBe(true);
    const selectedRows = screen.getAllByTestId("upload-task-row");
    expect(within(selectedRows[20]).getByRole("checkbox")).toBeChecked();
  });
});
