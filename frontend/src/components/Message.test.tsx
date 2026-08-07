import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Message } from "./Message";

describe("Message actions", () => {
  it("copies a user question from the action below the bubble", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <Message
        msg={{ id: "user-1", role: "user", content: "如何命名模型？" }}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "复制提问" }));
    expect(writeText).toHaveBeenCalledWith("如何命名模型？");
  });

  it("places regeneration beside copy and disables non-latest answers", () => {
    const regenerate = vi.fn();
    const { rerender } = render(
      <Message
        msg={{ id: "12", role: "assistant", content: "回答" }}
        conversationId="conversation-1"
        turnIndex={1}
        canRegenerate
        onRegenerate={regenerate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重新生成回答" }));
    expect(regenerate).toHaveBeenCalledWith("12");

    rerender(
      <Message
        msg={{ id: "12", role: "assistant", content: "回答" }}
        conversationId="conversation-1"
        turnIndex={1}
        canRegenerate={false}
        onRegenerate={regenerate}
      />,
    );
    expect(screen.getByRole("button", { name: "重新生成回答" })).toBeDisabled();
  });

  it("lets the user inspect retained answer versions", () => {
    const viewVersion = vi.fn();
    render(
      <Message
        msg={{
          id: "12",
          role: "assistant",
          content: "新回答",
          viewedVersionIndex: 2,
          answerVersions: [
            { id: "v1", versionIndex: 1, content: "旧回答", isActive: false },
            { id: "v2", versionIndex: 2, content: "新回答", isActive: true },
          ],
        }}
        conversationId="conversation-1"
        turnIndex={1}
        canRegenerate
        onViewAnswerVersion={viewVersion}
      />,
    );

    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看上一个回答" }));
    expect(viewVersion).toHaveBeenCalledWith("12", 1);
  });
});
