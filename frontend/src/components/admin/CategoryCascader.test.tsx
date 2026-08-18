import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CategoryCascader } from "./CategoryCascader";

const root = {
  id: "cat-root", category_key: "root", parent_id: null, display_code: "01", display_name: "行业规范与标准",
  sort_order: 10, level: 1, is_active: true, version: 1, created_at: 1, updated_at: 1,
  full_path: "01 行业规范与标准", item_count: 2,
};
const child = {
  ...root, id: "cat-child", category_key: "child", parent_id: root.id, display_code: "02", display_name: "文件夹上传测试",
  level: 2, full_path: "01 行业规范与标准 / 02 文件夹上传测试", item_count: 1,
};
const grandchild = {
  ...child, id: "cat-grandchild", category_key: "grandchild", parent_id: child.id, display_code: "03", display_name: "暖通",
  level: 3, full_path: "01 行业规范与标准 / 02 文件夹上传测试 / 03 暖通",
};
const sibling = { ...root, id: "cat-sibling", category_key: "sibling", display_code: "02", display_name: "客户标准", full_path: "02 客户标准" };

describe("CategoryCascader", () => {
  it("browses multiple columns and applies only the confirmed directory", () => {
    const onChange = vi.fn();
    render(<CategoryCascader categories={[root, child, grandchild, sibling]} value="" onChange={onChange} label="原目录" />);

    fireEvent.click(screen.getByRole("button", { name: /全部目录/ }));
    fireEvent.click(screen.getByTestId("category-cascader-desktop-option-cat-root"));
    expect(screen.getByTestId("category-cascader-desktop-option-cat-child")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("category-cascader-desktop-option-cat-child"));
    fireEvent.click(screen.getByRole("button", { name: "选择当前目录" }));

    expect(onChange).toHaveBeenCalledWith(child.id);
  });

  it("allows a parent directory to be selected explicitly", () => {
    const onChange = vi.fn();
    render(<CategoryCascader categories={[root, child]} value="" onChange={onChange} label="原目录" />);
    fireEvent.click(screen.getByRole("button", { name: /全部目录/ }));
    fireEvent.click(screen.getByTestId("category-cascader-desktop-option-cat-root"));
    fireEvent.click(screen.getByRole("button", { name: "选择当前目录" }));
    expect(onChange).toHaveBeenCalledWith(root.id);
  });

  it("drills down and returns to the parent level on mobile", () => {
    render(<CategoryCascader categories={[root, child]} value="" onChange={vi.fn()} label="原目录" />);
    fireEvent.click(screen.getByRole("button", { name: /全部目录/ }));
    fireEvent.click(screen.getByTestId("category-cascader-mobile-option-cat-root"));
    expect(screen.getByTestId("category-cascader-mobile-option-cat-child")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "返回上一级目录" }));
    expect(screen.getByTestId("category-cascader-mobile-option-cat-root")).toBeInTheDocument();
  });

  it("searches full paths and clears back to all directories", () => {
    const onChange = vi.fn();
    render(<CategoryCascader categories={[root, child, sibling]} value={child.id} onChange={onChange} label="原目录" />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(child.full_path) }));
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索原目录" }), { target: { value: "客户" } });
    expect(screen.getByRole("option", { name: sibling.full_path })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "全部目录" }));
    expect(onChange).toHaveBeenCalledWith("");
  });
});
