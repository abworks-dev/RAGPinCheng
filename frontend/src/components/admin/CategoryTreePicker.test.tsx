import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CategoryTreePicker } from "./CategoryTreePicker";

const root = {
  id: "cat-root",
  category_key: "root",
  parent_id: null,
  display_code: "01",
  display_name: "行业规范与标准",
  sort_order: 10,
  level: 1,
  is_active: true,
  version: 1,
  created_at: 1,
  updated_at: 1,
  full_path: "01 行业规范与标准",
  item_count: 2,
};

const child = {
  ...root,
  id: "cat-child",
  category_key: "child",
  parent_id: root.id,
  display_code: "02",
  display_name: "文件夹上传测试",
  level: 2,
  full_path: "01 行业规范与标准 / 02 文件夹上传测试",
  item_count: 1,
};

const sibling = {
  ...root,
  id: "cat-sibling",
  category_key: "sibling",
  display_code: "02",
  display_name: "客户标准",
  sort_order: 20,
  full_path: "02 客户标准",
  item_count: 0,
};

describe("CategoryTreePicker", () => {
  it("keeps hierarchy visible, marks the current directory, and selects a child", () => {
    const onChange = vi.fn();
    render(<CategoryTreePicker categories={[root, child, sibling]} value="" currentCategoryId={root.id} onChange={onChange} />);

    expect(screen.getByTestId("category-picker-item-cat-root")).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("button", { name: "展开行业规范与标准" }));
    expect(screen.getByTestId("category-picker-item-cat-child")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("category-picker-item-cat-child"));

    expect(onChange).toHaveBeenCalledWith(child.id);
  });

  it("filters by a child path and expands matching ancestors", () => {
    render(<CategoryTreePicker categories={[root, child, sibling]} value="" onChange={vi.fn()} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索目标目录" }), { target: { value: "文件夹上传" } });

    expect(screen.getByTestId("category-picker-item-cat-root")).toBeInTheDocument();
    expect(screen.getByTestId("category-picker-item-cat-child")).toBeInTheDocument();
    expect(screen.queryByTestId("category-picker-item-cat-sibling")).not.toBeInTheDocument();
  });

  it("moves tree focus with arrow keys", () => {
    render(<CategoryTreePicker categories={[root, sibling]} value="" onChange={vi.fn()} />);
    const first = screen.getByTestId("category-picker-item-cat-root");
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(screen.getByTestId("category-picker-item-cat-sibling")).toHaveFocus();
  });

  it("keeps one tree tab stop and prevents interaction while disabled", () => {
    const onChange = vi.fn();
    const { rerender } = render(<CategoryTreePicker categories={[root, child, sibling]} value="" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "展开行业规范与标准" }));
    const treeItems = screen.getAllByRole("treeitem");
    expect(treeItems.filter((item) => item.tabIndex === 0)).toHaveLength(1);
    rerender(<CategoryTreePicker categories={[root, child, sibling]} value="" onChange={onChange} disabled />);
    const expandButton = screen.getByRole("button", { name: "收起行业规范与标准" });
    expect(expandButton).toBeDisabled();
    fireEvent.click(screen.getByTestId("category-picker-item-cat-sibling"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("offers a selectable root destination and explains why a destination is unavailable", () => {
    const onChange = vi.fn();
    const { rerender } = render(<CategoryTreePicker
      categories={[root, child, sibling]}
      value="cat-root"
      onChange={onChange}
      rootOption={{ value: "__root__", label: "根目录 /", description: "一级目录所在位置" }}
      disabledCategoryReasons={{ [sibling.id]: "目标目录已有同名文件夹" }}
    />);

    const rootOption = screen.getByRole("treeitem", { name: /根目录/ });
    fireEvent.click(rootOption);
    expect(onChange).toHaveBeenCalledWith("__root__");

    const unavailable = screen.getByTestId("category-picker-item-cat-sibling");
    expect(unavailable).toHaveAttribute("aria-disabled", "true");
    expect(unavailable).toHaveAttribute("title", "目标目录已有同名文件夹");
    expect(unavailable).toHaveTextContent("目标目录已有同名文件夹");
    fireEvent.click(unavailable);
    expect(onChange).toHaveBeenCalledTimes(1);

    rerender(<CategoryTreePicker
      categories={[root, child, sibling]}
      value="__root__"
      onChange={onChange}
      rootOption={{ value: "__root__", label: "根目录 /", disabledReason: "文件夹已经位于根目录" }}
    />);
    expect(screen.getByRole("treeitem", { name: /根目录/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("文件夹已经位于根目录")).toBeInTheDocument();
  });

  it("keeps a single keyboard tab stop when the root destination is enabled", () => {
    const onChange = vi.fn();
    render(<CategoryTreePicker
      categories={[root, child, sibling]}
      value="__root__"
      onChange={onChange}
      rootOption={{ value: "__root__", label: "根目录 /" }}
    />);

    expect(screen.getAllByRole("treeitem").filter((item) => item.tabIndex === 0)).toHaveLength(1);
    const rootItem = screen.getByRole("treeitem", { name: /根目录/ });
    rootItem.focus();
    fireEvent.keyDown(rootItem, { key: "ArrowDown" });
    expect(screen.getByTestId("category-picker-item-cat-root")).toHaveFocus();
  });
});
