import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CategoryDestinationPicker } from "./CategoryDestinationPicker";

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
  item_count: 0,
};

describe("CategoryDestinationPicker", () => {
  it("creates a child below the selected destination and selects it", async () => {
    const created = {
      ...root,
      id: "cat-created",
      parent_id: root.id,
      display_code: "01",
      display_name: "新建子目录",
      level: 2,
      full_path: `${root.full_path} / 01 新建子目录`,
    };
    const onCreateFolder = vi.fn().mockResolvedValue(created);
    const onChange = vi.fn();
    render(<CategoryDestinationPicker
      categories={[root]}
      value={root.id}
      onChange={onChange}
      onCreateFolder={onCreateFolder}
    />);

    fireEvent.click(screen.getByRole("button", { name: "新建文件夹" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新文件夹名称" }), {
      target: { value: "新建子目录" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => expect(onCreateFolder).toHaveBeenCalledWith(root.id, "新建子目录"));
    expect(onChange).toHaveBeenCalledWith(created.id);
  });
});
