import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeScopePicker } from "./KnowledgeScopePicker";

const scopes = [
  {
    id: "root",
    parent_id: null,
    display_code: "01",
    display_name: "行业规范",
    full_path: "01 行业规范",
    level: 1,
    descendant_count: 1,
    chat_search_enabled: true,
    chat_filter_selectable: true,
  },
  {
    id: "child",
    parent_id: "root",
    display_code: "01",
    display_name: "国家标准",
    full_path: "01 行业规范 / 01 国家标准",
    level: 2,
    descendant_count: 0,
    chat_search_enabled: true,
    chat_filter_selectable: true,
  },
  {
    id: "hidden",
    parent_id: null,
    display_code: "99",
    display_name: "待确认资料",
    full_path: "99 待确认资料",
    level: 1,
    descendant_count: 0,
    chat_search_enabled: true,
    chat_filter_selectable: false,
  },
];

describe("KnowledgeScopePicker", () => {
  it("shows only configured filters and submits stable category ids", () => {
    const onToggle = vi.fn();
    render(<KnowledgeScopePicker scopes={scopes} selected={[]} onToggle={onToggle} onClear={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "全部企业知识" }));
    expect(screen.getByRole("button", { name: /01 行业规范/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /01 国家标准/ })).toBeInTheDocument();
    expect(screen.queryByText(/待确认资料/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /01 国家标准/ }));
    expect(onToggle).toHaveBeenCalledWith("child");
  });
});
