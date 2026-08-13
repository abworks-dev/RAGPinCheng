import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminCategoriesPage } from "./AdminCategoriesPage";

const mocks = vi.hoisted(() => ({
  categories: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    managedCategories: mocks.categories,
    createManagedCategory: mocks.create,
    updateManagedCategory: mocks.update,
  },
}));

vi.mock("../../components/ui/toast", () => ({
  toast: { success: mocks.success, error: mocks.error },
}));

const category = {
  id: "cat-01",
  category_key: "industry_standards",
  parent_id: null,
  display_code: "01",
  display_name: "行业规范与标准",
  sort_order: 10,
  level: 1,
  is_active: true,
  version: 3,
  created_at: 1,
  updated_at: 1,
  full_path: "01 行业规范与标准",
  item_count: 0,
};

describe("AdminCategoriesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.categories.mockResolvedValue([category]);
    mocks.update.mockResolvedValue({ ...category, version: 4 });
  });

  it("sends the category version for optimistic concurrency", async () => {
    render(<AdminCategoriesPage />);
    const name = await screen.findByLabelText("编辑行业规范与标准的显示名称");
    fireEvent.change(name, { target: { value: "行业规范" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith("cat-01", {
      display_code: "01",
      display_name: "行业规范",
      sort_order: 10,
      is_active: true,
      expected_version: 3,
    }));
  });

  it("keeps permission management out of category settings", async () => {
    render(<AdminCategoriesPage />);
    expect((await screen.findAllByText("01 行业规范与标准")).length).toBeGreaterThan(0);
    expect(screen.queryByText("industry_standards")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "资料权限" })).not.toBeInTheDocument();
  });

  it("shows loading, empty, and recoverable error states", async () => {
    let resolveCategories: ((value: typeof category[]) => void) | undefined;
    mocks.categories.mockReturnValueOnce(new Promise((resolve) => { resolveCategories = resolve; }));
    render(<AdminCategoriesPage />);
    expect(screen.getByText("正在加载分类…")).toBeInTheDocument();
    resolveCategories?.([]);
    expect(await screen.findByRole("heading", { name: "暂无分类" })).toBeInTheDocument();

    mocks.categories.mockRejectedValueOnce(new Error("分类服务暂不可用"));
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("分类服务暂不可用");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

});
