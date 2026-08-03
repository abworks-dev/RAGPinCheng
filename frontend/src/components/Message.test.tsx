import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage, Source } from "../types";
import { Message } from "./Message";

vi.mock("./FeedbackBar", () => ({
  FeedbackBar: () => <div aria-label="回答操作">回答操作</div>,
}));

vi.mock("../hooks/useVideoPlayer", () => ({
  timestampToSeconds: () => 0,
  useVideoPlayer: () => ({ open: vi.fn() }),
}));

const source: Source = {
  parent_id: "parent-1",
  doc_title: "测试标准",
  section_path: "第一章",
  category: "行业规范",
  score: 0.9,
  rrf_score: 0.8,
  text: "来源正文",
  doc_type: "pdf",
  start_time: null,
  media_id: null,
  sheet_name: null,
  cell_range: null,
  slide_number: null,
  paragraph_anchor: null,
};

function assistant(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "assistant-1",
    role: "assistant",
    content: "完整回答",
    sources: [source],
    ...overrides,
  };
}

describe("Message assistant actions", () => {
  it("places sources on the left and answer actions in the same footer", () => {
    render(<Message msg={assistant()} conversationId="conversation-1" turnIndex={1} />);

    const sources = screen.getByRole("button", { name: "查看 1 个来源" });
    const actions = screen.getByLabelText("回答操作");

    expect(sources.parentElement).toBe(actions.parentElement);
  });

  it("keeps answer actions right-aligned when there are no sources", () => {
    render(<Message msg={assistant({ sources: [] })} conversationId="conversation-1" turnIndex={1} />);

    expect(screen.queryByRole("button", { name: /查看 .* 个来源/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("回答操作")).toBeInTheDocument();
  });

  it("hides answer actions while streaming or after an error", () => {
    const { rerender } = render(
      <Message msg={assistant({ streaming: true })} conversationId="conversation-1" turnIndex={1} />,
    );
    expect(screen.queryByLabelText("回答操作")).not.toBeInTheDocument();

    rerender(
      <Message msg={assistant({ streaming: false, error: "生成失败" })} conversationId="conversation-1" turnIndex={1} />,
    );
    expect(screen.queryByLabelText("回答操作")).not.toBeInTheDocument();
  });
});
