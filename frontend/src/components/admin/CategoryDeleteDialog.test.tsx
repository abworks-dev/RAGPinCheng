import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CategoryDeletePreview, CategoryDeleteResult, ManagedCategory } from "../../types";
import { CategoryDeleteDialog } from "./CategoryDeleteDialog";

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("../../api/admin/content", () => ({
  adminContentApi: {
    categoryDeletePreview: mocks.preview,
    deleteCategory: mocks.remove,
  },
}));

const category: ManagedCategory = {
  id: "cat-test",
  category_key: "category_test",
  parent_id: "cat-04",
  display_code: "01",
  display_name: "测试目录",
  sort_order: 10,
  level: 2,
  is_active: true,
  version: 3,
  created_at: 1,
  updated_at: 1,
  full_path: "04 项目资料 / 01 测试目录",
  item_count: 2,
};

const preview: CategoryDeletePreview = {
  category_id: category.id,
  parent_id: category.parent_id,
  display_name: category.display_name,
  full_path: category.full_path,
  version: category.version,
  descendant_count: 1,
  folder_count: 2,
  content_count: 2,
  pending_request_count: 0,
  active_upload_count: 1,
  active_reclassification_count: 0,
  active_index_count: 1,
  archived_content_count: 1,
  active_content_count: 1,
  upload_batch_count: 2,
  media_transcript_count: 0,
  renumbered_sibling_count: 1,
  can_delete: false,
  can_force_delete: true,
  protected_category: false,
};

const result: CategoryDeleteResult = {
  deleted_folder_count: 2,
  renumbered_sibling_count: 1,
  parent_id: "cat-04",
  categories: [],
  force_delete: true,
  cleanup_status: "succeeded",
  cleanup_error_count: 0,
  run_id: "category-force-delete-test",
  deleted_item_count: 2,
  deleted_upload_batch_count: 2,
  deleted_index_job_count: 1,
  qdrant_point_count: 3,
  deleted_object_count: 2,
};

describe("CategoryDeleteDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.preview.mockResolvedValue(preview);
    mocks.remove.mockResolvedValue(result);
  });

  it("does not expose force deletion without the dedicated permission", async () => {
    render(<CategoryDeleteDialog category={category} canForceDelete={false} onClose={vi.fn()} onDeleted={vi.fn()} />);
    expect(await screen.findByText("资料（含回收站）：2 份")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "强制永久删除" })).not.toBeInTheDocument();
  });

  it("requires the exact path and acknowledgement before sending the force payload", async () => {
    const onDeleted = vi.fn();
    render(<CategoryDeleteDialog category={category} canForceDelete onClose={vi.fn()} onDeleted={onDeleted} />);
    fireEvent.click(await screen.findByRole("button", { name: "强制永久删除" }));

    const confirm = screen.getByRole("button", { name: "确认永久删除" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("输入完整目录路径确认"), { target: { value: "错误路径" } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("输入完整目录路径确认"), { target: { value: preview.full_path } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith(category.id, category.version, {
      force: true,
      typedPath: preview.full_path,
    }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(result));
  });

  it("explains protected and media blockers without enabling force deletion", async () => {
    mocks.preview.mockResolvedValue({
      ...preview,
      can_force_delete: false,
      protected_category: true,
      media_transcript_count: 2,
    });
    render(<CategoryDeleteDialog category={category} canForceDelete onClose={vi.fn()} onDeleted={vi.fn()} />);

    expect(await screen.findByText("系统默认一级分类受保护，不能强制永久删除。")).toBeInTheDocument();
    expect(screen.getByText("包含 2 份视频转录稿，请先在视频管理中处理。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "强制永久删除" })).toBeDisabled();
  });
});
