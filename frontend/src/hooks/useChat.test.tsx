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
});
