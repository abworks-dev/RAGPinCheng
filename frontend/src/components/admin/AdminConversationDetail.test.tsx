import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AdminConversationDetail } from "./AdminConversationDetail";

const conversation = {
  id: "conversation-1",
  title: "项目交付标准",
  user_id: 11,
  created_at: 1_720_000_000,
  updated_at: 1_720_003_600,
  turn_index: 2,
  messages: [
    {
      id: 1,
      role: "user" as const,
      content: "编辑后的问题",
      user_versions: [
        { id: 101, version_index: 1, content: "原问题", created_at: 1_720_000_000, is_active: false },
        { id: 102, version_index: 2, content: "编辑后的问题", created_at: 1_720_000_100, is_active: true },
      ],
    },
    {
      id: 2,
      role: "assistant" as const,
      content: "编辑后的回答",
      answer_versions: [
        { id: 201, version_index: 1, content: "原回答", created_at: 1_720_000_000, is_active: false, user_version_id: null },
        { id: 202, version_index: 2, content: "编辑后的回答", created_at: 1_720_000_100, is_active: true, user_version_id: 102 },
      ],
    },
  ],
};

describe("AdminConversationDetail", () => {
  it("renders recoverable detail states", () => {
    const { rerender } = render(<AdminConversationDetail conversation={null} />);
    expect(screen.getByText("选择一条对话")).toBeInTheDocument();

    rerender(<AdminConversationDetail conversation={null} loading />);
    expect(screen.getByText("正在加载对话详情…")).toBeInTheDocument();

    rerender(<AdminConversationDetail conversation={null} error="详情读取失败" />);
    expect(screen.getByRole("alert")).toHaveTextContent("详情读取失败");
  });

  it("renders messages and linked question/answer versions", () => {
    render(<AdminConversationDetail conversation={conversation} />);

    expect(screen.getByRole("heading", { name: "项目交付标准" })).toBeInTheDocument();
    expect(screen.getByText("已编辑 · 版本 2")).toBeInTheDocument();
    expect(screen.getByText("对应回答版本 2 · 当前")).toBeInTheDocument();
  });
});
