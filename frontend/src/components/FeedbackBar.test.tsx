import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types";
import { FeedbackBar } from "./FeedbackBar";

const mocks = vi.hoisted(() => ({
  sendFeedback: vi.fn(),
  copy: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: { sendFeedback: mocks.sendFeedback },
}));

vi.mock("./ui/toast", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

const message: ChatMessage = {
  id: "assistant-2",
  role: "assistant",
  content: "## 回答\n\n请查看来源 [1]。",
  query: "测试问题",
};

function renderFeedback() {
  return render(<FeedbackBar msg={message} conversationId="conversation-1" turnIndex={2} />);
}

describe("FeedbackBar", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: mocks.copy },
    });
    mocks.copy.mockResolvedValue(undefined);
    mocks.sendFeedback.mockResolvedValue({ ok: true });
  });

  it("copies the complete answer and shows a temporary inline check without a toast", async () => {
    vi.useFakeTimers();
    renderFeedback();

    fireEvent.click(screen.getByRole("button", { name: "复制回答" }));

    await act(async () => {});
    expect(mocks.copy).toHaveBeenCalledWith(message.content);
    expect(screen.getByRole("button", { name: "回答已复制" })).toHaveClass("text-success");
    expect(mocks.toastSuccess).not.toHaveBeenCalledWith("回答已复制");

    act(() => vi.advanceTimersByTime(1400));
    expect(screen.getByRole("button", { name: "复制回答" })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("reports clipboard failures without changing feedback state", async () => {
    mocks.copy.mockRejectedValueOnce(new Error("clipboard unavailable"));
    renderFeedback();

    fireEvent.click(screen.getByRole("button", { name: "复制回答" }));

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("复制失败，请稍后重试"));
    expect(screen.getByRole("button", { name: "这个回答不好" })).toBeEnabled();
  });

  it("places regeneration and version switching before copy", () => {
    const regenerate = vi.fn();
    const viewVersion = vi.fn();
    const versionedMessage: ChatMessage = {
      ...message,
      viewedVersionIndex: 2,
      answerVersions: [
        { id: "v1", versionIndex: 1, content: "旧回答", isActive: false },
        { id: "v2", versionIndex: 2, content: "新回答", isActive: true },
      ],
    };
    render(
      <FeedbackBar
        msg={versionedMessage}
        conversationId="conversation-1"
        turnIndex={2}
        canRegenerate
        onRegenerate={regenerate}
        onViewAnswerVersion={viewVersion}
      />,
    );

    const copy = screen.getByRole("button", { name: "复制回答" });
    const regenerateButton = screen.getByRole("button", { name: "重新生成回答" });
    const versions = screen.getByLabelText("回答版本");
    expect(versions.nextElementSibling).toBe(regenerateButton);
    expect(regenerateButton.nextElementSibling).toBe(copy);

    fireEvent.click(regenerateButton);
    expect(regenerate).toHaveBeenCalledWith(message.id);
    expect(screen.getByText("2 / 2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看上一个回答" }));
    expect(viewVersion).toHaveBeenCalledWith(message.id, 1);
  });

  it("submits a categorized answer feedback with the existing association fields", async () => {
    renderFeedback();

    fireEvent.click(screen.getByRole("button", { name: "这个回答不好" }));
    expect(screen.getByRole("dialog", { name: "反馈" })).toBeInTheDocument();
    expect(screen.getByLabelText("问题分类：回答问题")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "虚假信息" }));
    fireEvent.change(screen.getByLabelText("补充说明（选填）"), {
      target: { value: "引用中的数值与原文不一致" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() =>
      expect(mocks.sendFeedback).toHaveBeenCalledWith({
        kind: "answer",
        rating: "down",
        note: "原因：虚假信息\n补充：引用中的数值与原文不一致",
        conversation_id: "conversation-1",
        turn_index: 2,
        message_id: "assistant-2",
        query: "测试问题",
        answer_text: message.content,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "反馈已提交" })).toBeDisabled();
    expect(mocks.toastSuccess).toHaveBeenCalledWith("反馈已提交，感谢你的帮助");
  });

  it("requires details for the other reason", () => {
    renderFeedback();

    fireEvent.click(screen.getByRole("button", { name: "这个回答不好" }));
    fireEvent.click(screen.getByRole("button", { name: "其他" }));

    const submit = screen.getByRole("button", { name: "提交" });
    expect(screen.getByLabelText("补充说明（必填）")).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("补充说明（必填）"), { target: { value: "回答格式难以阅读" } });
    expect(submit).toBeEnabled();
  });

  it("keeps the dialog open and shows the API error when submission fails", async () => {
    mocks.sendFeedback.mockRejectedValueOnce(new Error("反馈服务暂不可用"));
    renderFeedback();

    fireEvent.click(screen.getByRole("button", { name: "这个回答不好" }));
    fireEvent.click(screen.getByRole("button", { name: "没有帮助" }));
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("反馈服务暂不可用");
    expect(screen.getByRole("dialog", { name: "反馈" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交" })).toBeEnabled();
    expect(mocks.toastSuccess).not.toHaveBeenCalledWith("反馈已提交，感谢你的帮助");
  });
});
