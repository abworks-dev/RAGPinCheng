import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TurnNavigator } from "./TurnNavigator";

const turns = [
  { id: "turn-1", label: "第一个问题" },
  { id: "turn-2", label: "第二个问题" },
];

describe("TurnNavigator", () => {
  it("stays hidden when the conversation has fewer than two turns", () => {
    render(<TurnNavigator turns={turns.slice(0, 1)} activeTurnId="turn-1" onNavigate={vi.fn()} />);
    expect(screen.queryByRole("navigation", { name: "对话轮次快速导航" })).not.toBeInTheDocument();
  });

  it("expands on hover and marks the active turn", () => {
    render(<TurnNavigator turns={turns} activeTurnId="turn-2" onNavigate={vi.fn()} />);
    const navigation = screen.getByRole("navigation", { name: "对话轮次快速导航" });

    expect(screen.queryByText("第一个问题")).not.toBeInTheDocument();
    const collapsedActiveButton = screen.getByRole("button", { name: "跳转到问题：第二个问题" });
    expect(collapsedActiveButton).toHaveClass("bg-transparent", "text-primary");
    expect(collapsedActiveButton).not.toHaveClass("bg-primary/10");
    expect(collapsedActiveButton.querySelector("[aria-hidden='true']")).toHaveClass("w-4", "bg-primary");

    fireEvent.mouseEnter(navigation);

    expect(screen.getByText("第一个问题")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "跳转到问题：第二个问题" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "跳转到问题：第二个问题" })).toHaveClass("bg-primary/10");
    expect(screen.getByRole("button", { name: "跳转到问题：第一个问题" })).toHaveClass("hover:bg-secondary");
  });

  it("reports the selected turn", () => {
    const onNavigate = vi.fn();
    render(<TurnNavigator turns={turns} activeTurnId="turn-1" onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole("button", { name: "跳转到问题：第二个问题" }));
    expect(onNavigate).toHaveBeenCalledWith("turn-2");
  });
});
