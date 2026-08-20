import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminCategoriesPage } from "./AdminCategoriesPage";

const mocks = vi.hoisted(() => ({
  categories: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  updateNumber: vi.fn(),
  move: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  role: "admin" as "admin" | "user",
}));

vi.mock("../../api/client", () => ({
  api: {
    managedCategories: mocks.categories,
    createManagedCategory: mocks.create,
    updateManagedCategory: mocks.update,
    updateManagedCategoryNumber: mocks.updateNumber,
    moveManagedCategory: mocks.move,
  },
}));

vi.mock("../../components/ui/toast", () => ({
  toast: { success: mocks.success, error: mocks.error },
}));

vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({ state: { status: "authed", user: { role: mocks.role, content_permissions: ["category.manage"] } } }),
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
    mocks.role = "admin";
    mocks.categories.mockResolvedValue([category, child]);
    mocks.update.mockResolvedValue({ ...category, version: 4, display_name: "行业规范" });
    mocks.create.mockResolvedValue({ ...category, id: "cat-new", display_code: "09", display_name: "新分类", full_path: "09 新分类" });
    mocks.updateNumber.mockResolvedValue([category, child]);
    mocks.move.mockResolvedValue([category, child]);
  });

  it("sends the selected category version for optimistic concurrency", async () => {
    render(<AdminCategoriesPage />);
    const name = await screen.findByLabelText("显示名称");
    fireEvent.change(name, { target: { value: "行业规范" } });
    await waitFor(() => expect(name).toHaveValue("行业规范"));
    const save = screen.getByRole("button", { name: "保存修改" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith("cat-01", {
      display_code: "01",
      display_name: "行业规范",
      sort_order: 10,
      is_active: true,
      chat_search_enabled: true,
      chat_filter_selectable: true,
      expected_version: 3,
    }));
  });

  it("shows hierarchy, direct counts, and keeps internal keys hidden", async () => {
    render(<AdminCategoriesPage />);
    const parent = await screen.findByRole("treeitem", { name: /01 行业规范与标准/ });
    expect(screen.getByRole("tree", { name: "分类层级" })).toHaveClass("border-b", "border-border");
    const status = within(parent).getByText("启用");
    const identity = within(parent).getByText("01 行业规范与标准");
    expect(status.compareDocumentPosition(identity) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(parent).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("1 个一级分类 · 共 2 个分类")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部展开" })).toBeInTheDocument();
    expect(within(parent).getByText("0 份 · 1 项")).toBeInTheDocument();
    expect(screen.queryByTestId("category-tree-item-cat-01-child")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开行业规范与标准" }));
    expect(screen.getByRole("button", { name: "全部折叠" })).toBeInTheDocument();
    expect(within(screen.getByTestId("category-tree-item-cat-01-child")).getByText("2 份")).toBeInTheDocument();
    expect(screen.queryByText("industry_standards")).not.toBeInTheDocument();
    expect(screen.queryByText("排序序号")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "调整结构" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "资料权限" })).not.toBeInTheDocument();
  });

  it("puts structure actions at the top and hides top-level delete from category managers", async () => {
    mocks.role = "user";
    render(<AdminCategoriesPage />);
    const structure = await screen.findByRole("heading", { name: "目录结构" });
    const basics = screen.getByRole("heading", { name: "基本信息" });
    expect(structure.compareDocumentPosition(basics) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("button", { name: "删除文件夹" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开行业规范与标准" }));
    fireEvent.click(screen.getByTestId("category-tree-item-cat-01-child"));
    expect(await screen.findByRole("button", { name: "删除文件夹" })).toBeVisible();
  });

  it("guards a parent category with active children from being disabled", async () => {
    render(<AdminCategoriesPage />);
    const toggle = await screen.findByRole("radio", { name: "行业规范与标准停用" });
    expect(toggle).toBeDisabled();
    expect(screen.getByText("暂不能停用")).toBeInTheDocument();
    expect(screen.getByText("该分类仍有启用的子分类，请先停用子分类。")).toBeInTheDocument();
  });

  it("keeps the saved status visible while a status change is pending", async () => {
    mocks.categories.mockResolvedValueOnce([{ ...category, item_count: 0 }]);
    render(<AdminCategoriesPage />);
    const disable = await screen.findByRole("radio", { name: "行业规范与标准停用" });
    fireEvent.click(disable);
    const detailHeading = screen.getByRole("heading", { name: "01 行业规范与标准" });
    expect(within(detailHeading.parentElement!).getByText("启用")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("待保存：停用")).toBeInTheDocument());
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

  it("confirms an occupied category number before shifting siblings", async () => {
    const sibling = { ...category, id: "cat-02", category_key: "client", display_code: "02", display_name: "客户标准", sort_order: 20, full_path: "02 客户标准" };
    mocks.categories.mockResolvedValueOnce([category, sibling]);
    mocks.updateNumber.mockResolvedValueOnce([{ ...sibling, display_code: "01", sort_order: 10, version: 4 }, { ...category, display_code: "02", sort_order: 20, version: 4 }]);
    render(<AdminCategoriesPage />);

    fireEvent.click(await screen.findByRole("button", { name: "调整编号" }));
    let dialog = screen.getByRole("dialog", { name: "调整分类编号" });
    fireEvent.change(within(dialog).getByRole("spinbutton", { name: "目标编号" }), { target: { value: "2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "下一步" }));
    dialog = screen.getByRole("dialog", { name: "确认调整编号" });
    expect(dialog).toHaveTextContent("编号 02 当前由“客户标准”使用");
    fireEvent.click(within(dialog).getByRole("button", { name: "继续调整" }));
    await waitFor(() => expect(mocks.updateNumber).toHaveBeenCalledWith("cat-01", {
      target_position: 2,
      confirm_number_shift: true,
      expected_version: 3,
    }));
  });

  it("confirms a parent change with the old and new paths", async () => {
    const target = { ...category, id: "cat-02", category_key: "client", display_code: "02", display_name: "客户标准", sort_order: 20, full_path: "02 客户标准" };
    mocks.categories.mockResolvedValueOnce([category, target]);
    mocks.move.mockResolvedValueOnce([{ ...category, parent_id: "cat-02", level: 2, version: 4, full_path: "02 客户标准 / 01 行业规范与标准" }, target]);
    render(<AdminCategoriesPage />);

    fireEvent.click(await screen.findByRole("button", { name: "移动至" }));
    const dialog = await screen.findByRole("dialog", { name: "移动分类" });
    expect(within(dialog).getByText("01 行业规范与标准")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByTestId("category-picker-item-cat-02"));
    expect(within(dialog).getByText(/目标位置：02 客户标准 \/ 末尾/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认移动" }));
    await waitFor(() => expect(mocks.move).toHaveBeenCalledWith("cat-01", {
      target_parent_id: "cat-02",
      before_category_id: null,
      expected_version: 3,
    }));
  });

  it("offers a fourth-level category as a move destination", async () => {
    const target = { ...category, id: "cat-target-level-4", category_key: "target_level_4", display_code: "02", display_name: "第四级目标", level: 4, full_path: "02 目标 / 01 二级 / 01 三级 / 01 第四级目标" };
    mocks.categories.mockResolvedValueOnce([category, target]);
    render(<AdminCategoriesPage />);

    fireEvent.click(await screen.findByRole("button", { name: "移动至" }));
    const dialog = await screen.findByRole("dialog", { name: "移动分类" });
    expect(within(dialog).getByTestId("category-picker-item-cat-target-level-4")).toBeInTheDocument();
  });

  it("creates a category from the Sheet form", async () => {
    render(<AdminCategoriesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新增分类" }));
    const dialog = await screen.findByRole("dialog", { name: "新增分类" });
    fireEvent.change(within(dialog).getByLabelText("分类名称"), { target: { value: "新分类" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "新增分类" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({
      parent_id: null,
      display_code: "02",
      display_name: "新分类",
      sort_order: 20,
      target_position: 2,
      confirm_number_shift: false,
    }));
  });

  it("allows adding a child below a fourth-level category", async () => {
    const levelFour = { ...child, id: "cat-level-4", parent_id: child.id, level: 4, display_name: "第四级", full_path: `${child.full_path} / 01 第四级`, item_count: 0 };
    mocks.categories.mockResolvedValueOnce([category, child, levelFour]);
    render(<AdminCategoriesPage />);

    fireEvent.click(await screen.findByRole("button", { name: "全部展开" }));
    fireEvent.click(await screen.findByTestId("category-tree-item-cat-level-4"));
    expect(await screen.findByRole("button", { name: "新增子分类" })).toBeEnabled();
  });

  it("confirms before creating at an occupied number", async () => {
    const sibling = { ...category, id: "cat-02", category_key: "client", display_code: "02", display_name: "客户标准", sort_order: 20, full_path: "02 客户标准" };
    mocks.categories.mockResolvedValueOnce([category, sibling]);
    render(<AdminCategoriesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新增分类" }));
    let dialog = await screen.findByRole("dialog", { name: "新增分类" });
    fireEvent.change(within(dialog).getByLabelText("分类名称"), { target: { value: "插入分类" } });
    fireEvent.change(within(dialog).getByLabelText("编号"), { target: { value: "2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "新增分类" }));
    expect(mocks.create).not.toHaveBeenCalled();
    expect(within(dialog).getByText("编号 02 已被占用")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "继续创建" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({
      parent_id: null,
      display_code: "02",
      display_name: "插入分类",
      sort_order: 20,
      target_position: 2,
      confirm_number_shift: true,
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
