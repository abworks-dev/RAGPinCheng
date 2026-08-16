import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const child = {
  ...category,
  id: "cat-01-child",
  category_key: "industry_child",
  parent_id: "cat-01",
  display_code: "01",
  display_name: "测试子分类",
  level: 2,
  full_path: "01 行业规范与标准 / 01 测试子分类",
  item_count: 2,
};

describe("AdminCategoriesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.categories.mockResolvedValue([category, child]);
    mocks.update.mockResolvedValue({ ...category, version: 4, display_name: "行业规范" });
    mocks.create.mockResolvedValue({ ...category, id: "cat-new", display_code: "09", display_name: "新分类", full_path: "09 新分类" });
  });

  it("sends the selected category version for optimistic concurrency", async () => {
    render(<AdminCategoriesPage />);
    const name = await screen.findByLabelText("显示名称");
    fireEvent.change(name, { target: { value: "行业规范" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith("cat-01", {
      display_code: "01",
      display_name: "行业规范",
      sort_order: 10,
      is_active: true,
      expected_version: 3,
    }));
  });

  it("shows hierarchy, direct counts, and keeps internal keys hidden", async () => {
    render(<AdminCategoriesPage />);
    const parent = await screen.findByRole("treeitem", { name: /01 行业规范与标准/ });
    expect(parent).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("1 个一级分类 · 共 2 个分类")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部展开" })).toBeInTheDocument();
    expect(screen.getByText("0 份直接资料 · 1 个子分类")).toBeInTheDocument();
    expect(screen.queryByTestId("category-tree-item-cat-01-child")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开行业规范与标准" }));
    expect(screen.getByRole("button", { name: "全部折叠" })).toBeInTheDocument();
    expect(screen.getByText("2 份直接资料")).toBeInTheDocument();
    expect(screen.queryByText("industry_standards")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "资料权限" })).not.toBeInTheDocument();
  });

  it("guards a parent category with active children from being disabled", async () => {
    render(<AdminCategoriesPage />);
    const toggle = await screen.findByRole("checkbox", { name: "行业规范与标准启用" });
    expect(toggle).toBeDisabled();
    expect(screen.getByText("该分类仍有启用的子分类，请先停用子分类。")).toBeInTheDocument();
  });

  it("reveals matching descendants after their parent was collapsed", async () => {
    render(<AdminCategoriesPage />);
    expect(await screen.findByRole("button", { name: "展开行业规范与标准" })).toBeInTheDocument();
    expect(screen.queryByTestId("category-tree-item-cat-01-child")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索分类" }), { target: { value: "测试子分类" } });

    expect(await screen.findByTestId("category-tree-item-cat-01-child")).toBeInTheDocument();
  });

  it("moves tree focus between an expanded parent and its child", async () => {
    render(<AdminCategoriesPage />);
    const parent = await screen.findByTestId("category-tree-item-cat-01");
    parent.focus();

    fireEvent.keyDown(parent, { key: "ArrowRight" });
    const childItem = await screen.findByTestId("category-tree-item-cat-01-child");
    fireEvent.keyDown(parent, { key: "ArrowRight" });
    expect(childItem).toHaveFocus();
    fireEvent.keyDown(childItem, { key: "ArrowLeft" });
    expect(parent).toHaveFocus();
  });

  it("creates a category from the Sheet form", async () => {
    render(<AdminCategoriesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新增分类" }));
    const dialog = await screen.findByRole("dialog", { name: "新增分类" });
    fireEvent.change(within(dialog).getByLabelText("显示编号"), { target: { value: "09" } });
    fireEvent.change(within(dialog).getByLabelText("分类名称"), { target: { value: "新分类" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "新增分类" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({
      parent_id: null,
      display_code: "09",
      display_name: "新分类",
      sort_order: 20,
    }));
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
    expect(screen.queryByRole("heading", { name: "暂无分类" })).not.toBeInTheDocument();
  });
});
