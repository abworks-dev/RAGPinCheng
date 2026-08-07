import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatHeader } from "./ChatHeader";

const props = {
  title: "新对话",
  scopeLabel: "全部企业知识",
  loading: false,
  sourceOpen: false,
  onOpenConversations: vi.fn(),
  onToggleSources: vi.fn(),
};

describe("ChatHeader source control", () => {
  it("hides the source control before the conversation has sources", () => {
    render(<ChatHeader {...props} sourceCount={0} />);
    expect(screen.queryByRole("button", { name: /来源/ })).not.toBeInTheDocument();
  });

  it("shows the source control when sources are available", () => {
    render(<ChatHeader {...props} sourceCount={2} />);
    expect(screen.getByRole("button", { name: /来源/ })).toBeInTheDocument();
  });

  it("keeps the desktop source control fixed and consistent across states", () => {
    const { rerender } = render(<ChatHeader {...props} sourceCount={3} />);
    const openButton = screen.getByRole("button", { name: "展开来源" });

    expect(openButton).toHaveTextContent("来源3");
    expect(openButton).toHaveClass("transition-none", "xl:fixed", "w-[5.75rem]");

    rerender(<ChatHeader {...props} sourceCount={3} sourceOpen />);
    const closeButton = screen.getByRole("button", { name: "收起来源" });

    expect(closeButton).toHaveTextContent("来源3");
    expect(closeButton).toHaveClass("transition-none", "xl:fixed", "w-[5.75rem]");
  });
});
