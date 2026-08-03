import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types";
import { groupMessagesIntoTurns, MessageList } from "./MessageList";

vi.mock("./Message", () => ({
  Message: ({ msg, onToggleSources }: { msg: ChatMessage; onToggleSources?: (id: string) => void }) => (
    <div>
      <span>{msg.content}</span>
      {msg.role === "assistant" && (
        <button type="button" onClick={() => onToggleSources?.(msg.id)}>查看来源 {msg.id}</button>
      )}
    </div>
  ),
}));

const messages: ChatMessage[] = [
  { id: "user-1", role: "user", content: "第一个问题" },
  { id: "assistant-1", role: "assistant", content: "第一个回答" },
  { id: "user-2", role: "user", content: "第二个问题" },
  { id: "assistant-2", role: "assistant", content: "第二个回答" },
];

describe("MessageList", () => {
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("groups each user question with the following assistant answer", () => {
    const turns = groupMessagesIntoTurns(messages);
    expect(turns).toHaveLength(2);
    expect(turns[0]).toMatchObject({ id: "user-1", label: "第一个问题", turnIndex: 1 });
    expect(turns[0].messages.map((message) => message.id)).toEqual(["user-1", "assistant-1"]);
    expect(turns[1].messages.map((message) => message.id)).toEqual(["user-2", "assistant-2"]);
  });

  it("does not jump to the bottom when opening sources for an earlier answer", () => {
    function Harness() {
      const [sourceMessageId, setSourceMessageId] = useState<string | null>(null);
      return (
        <MessageList
          messages={messages}
          conversationId="conversation-1"
          sourceOpen={sourceMessageId !== null}
          activeSourceMessageId={sourceMessageId}
          onToggleSources={setSourceMessageId}
        />
      );
    }

    render(<Harness />);
    scrollIntoView.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "查看来源 assistant-1" }));

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("smoothly navigates to the selected turn", () => {
    render(<MessageList messages={messages} conversationId="conversation-1" />);
    scrollIntoView.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "跳转到问题：第一个问题" }));

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });
});
