import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types";
import { groupMessagesIntoTurns, MessageList } from "./MessageList";
import {
  CITATION_TOOLTIP_ACTIVE_EVENT,
  type CitationTooltipActiveDetail,
} from "./citations";

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

  it("adds a non-interactive fade boundary below the chat header", () => {
    const { container } = render(<MessageList messages={messages} conversationId="conversation-1" />);

    expect(container.firstElementChild).toHaveClass("scroll-fade-content-start");
  });

  it("keeps the message column centered and reserves space below the latest answer", () => {
    const { container } = render(<MessageList messages={messages} conversationId="conversation-1" />);

    expect(container.querySelector("[data-message-scroll-container]")).not.toHaveClass("xl:pr-64", "2xl:pr-64");
    expect(container.querySelector("[data-message-bottom-spacer]")).toHaveClass("h-20", "sm:h-24");
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

  it("pauses streaming auto-follow while a citation tooltip is active", () => {
    const streamingMessages: ChatMessage[] = [
      { id: "user-1", role: "user", content: "问题" },
      { id: "assistant-1", role: "assistant", content: "回答", streaming: true },
    ];
    const { rerender } = render(
      <MessageList messages={streamingMessages} conversationId="conversation-1" />,
    );
    scrollIntoView.mockClear();

    window.dispatchEvent(
      new CustomEvent<CitationTooltipActiveDetail>(CITATION_TOOLTIP_ACTIVE_EVENT, {
        detail: { markerId: "assistant-1:0", active: true },
      }),
    );
    rerender(
      <MessageList
        messages={[
          streamingMessages[0],
          { ...streamingMessages[1], content: "回答继续" },
        ]}
        conversationId="conversation-1"
      />,
    );
    expect(scrollIntoView).not.toHaveBeenCalled();

    window.dispatchEvent(
      new CustomEvent<CitationTooltipActiveDetail>(CITATION_TOOLTIP_ACTIVE_EVENT, {
        detail: { markerId: "assistant-1:0", active: false },
      }),
    );
    rerender(
      <MessageList
        messages={[
          streamingMessages[0],
          { ...streamingMessages[1], content: "回答继续输出" },
        ]}
        conversationId="conversation-1"
      />,
    );
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "end" });
  });

  it("preserves the assistant render instance when its optimistic id becomes persisted", () => {
    const optimisticMessages: ChatMessage[] = [
      { id: "user-1", role: "user", content: "问题" },
      { id: "assistant-pending", role: "assistant", content: "回答", streaming: true },
    ];
    const { rerender } = render(
      <MessageList messages={optimisticMessages} conversationId="conversation-1" />,
    );
    const originalAssistant = screen.getByText("回答").parentElement;

    rerender(
      <MessageList
        messages={[
          optimisticMessages[0],
          { ...optimisticMessages[1], id: "42", streaming: false },
        ]}
        conversationId="conversation-1"
      />,
    );

    expect(screen.getByText("回答").parentElement).toBe(originalAssistant);
    expect(screen.getByRole("button", { name: "查看来源 42" })).toBeInTheDocument();
  });

  it("smoothly navigates to the selected turn", () => {
    render(<MessageList messages={messages} conversationId="conversation-1" />);
    scrollIntoView.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "跳转到问题：第一个问题" }));

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });

  it("keeps the clicked turn highlighted while smooth scrolling passes the previous turn", () => {
    const { container } = render(<MessageList messages={messages} conversationId="conversation-1" />);
    const scroller = container.querySelector("[data-message-scroll-container]") as HTMLDivElement;
    const firstTurn = container.querySelector('[data-turn-id="user-1"]') as HTMLElement;
    const secondTurn = container.querySelector('[data-turn-id="user-2"]') as HTMLElement;

    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 400 });
    scroller.getBoundingClientRect = vi.fn(() => ({ top: 0 }) as DOMRect);
    firstTurn.getBoundingClientRect = vi.fn(() => ({ top: 20 }) as DOMRect);
    secondTurn.getBoundingClientRect = vi.fn(() => ({ top: 220 }) as DOMRect);

    fireEvent.click(screen.getByRole("button", { name: "跳转到问题：第二个问题" }));
    fireEvent.scroll(scroller);

    expect(screen.getByRole("button", { name: "跳转到问题：第二个问题" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "跳转到问题：第一个问题" })).not.toHaveAttribute("aria-current");
  });

  it("shows a bottom button away from the end and scrolls smoothly when clicked", () => {
    const { container } = render(<MessageList messages={messages} conversationId="conversation-1" />);
    const scroller = container.querySelector(".overflow-y-auto") as HTMLDivElement;
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    });
    scroller.scrollTo = vi.fn();

    fireEvent.scroll(scroller);
    fireEvent.click(screen.getByRole("button", { name: "回到底部" }));

    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "smooth" });
  });

  it("keeps the bottom button hidden near the end", () => {
    const { container } = render(<MessageList messages={messages} conversationId="conversation-1" />);
    const scroller = container.querySelector(".overflow-y-auto") as HTMLDivElement;
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 550 },
    });

    fireEvent.scroll(scroller);

    expect(screen.queryByRole("button", { name: "回到底部" })).not.toBeInTheDocument();
  });
});
