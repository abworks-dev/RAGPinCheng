import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./useChat";

const mocks = vi.hoisted(() => ({
  getConversation: vi.fn(),
  streamChat: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    getConversation: mocks.getConversation,
  },
}));

vi.mock("../api/chatStream", () => ({
  streamChat: mocks.streamChat,
}));

describe("useChat persisted message identity", () => {
  beforeEach(() => {
    mocks.getConversation.mockResolvedValue({
      id: "conversation-1",
      title: "测试",
      user_id: 1,
      created_at: 1,
      updated_at: 1,
      turn_index: 0,
      messages: [],
    });
    mocks.streamChat.mockImplementation(async function* () {
      yield {
        type: "done",
        data: {
          answer_text: "回答",
          assistant_message_id: 42,
          timings: {},
          sources: [],
          history_chars: 0,
          budget: 0,
        },
      };
    });
  });

  it("replaces the optimistic assistant id with the persisted id from done", async () => {
    const { result } = renderHook(() => useChat({ conversationId: "conversation-1" }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.send("问题");
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1]).toMatchObject({
      id: "42",
      role: "assistant",
      content: "回答",
      streaming: false,
    });
  });

  it("edits the latest persisted question and replaces its paired answer", async () => {
    mocks.getConversation.mockResolvedValue({
      id: "conversation-1",
      title: "测试",
      user_id: 1,
      created_at: 1,
      updated_at: 1,
      turn_index: 1,
      messages: [
        { id: 10, role: "user", content: "原问题" },
        {
          id: 11,
          role: "assistant",
          content: "原回答",
          answer_versions: [{
            id: 100,
            version_index: 1,
            content: "原回答",
            created_at: 1,
            is_active: true,
            user_version_id: null,
          }],
        },
      ],
    });
    mocks.streamChat.mockImplementation(async function* () {
      yield {
        type: "done",
        data: {
          answer_text: "编辑后的回答",
          assistant_message_id: 11,
          timings: {},
          sources: [],
          history_chars: 0,
          budget: 0,
        },
      };
    });
    const { result } = renderHook(() => useChat({ conversationId: "conversation-1" }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.editQuestion("10", "编辑后的问题");
    });

    expect(mocks.streamChat).toHaveBeenCalledWith(
      "conversation-1",
      { query: "编辑后的问题", edit_user_message_id: 10 },
      expect.any(AbortSignal),
    );
    expect(result.current.messages[0].content).toBe("编辑后的问题");
    expect(result.current.messages[1]).toMatchObject({
      id: "11",
      content: "编辑后的回答",
      query: "编辑后的问题",
      streaming: false,
    });

    act(() => {
      result.current.viewQuestionVersion("10", 1);
    });
    expect(result.current.messages[0].content).toBe("原问题");
    expect(result.current.messages[1]).toMatchObject({
      content: "原回答",
      query: "原问题",
    });
  });
});
