import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManagedSummaryCard } from "./ManagedSummaryCard";

const icon = <svg aria-hidden="true" />;

describe("ManagedSummaryCard", () => {
  it("keeps zero counts readable without creating a dead control", () => {
    const { container } = render(<ManagedSummaryCard label="发布失败" value={0} icon={icon} tone="destructive" />);
    expect(screen.getByText("发布失败")).toBeVisible();
    expect(screen.getByText("0")).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    // A headerless summary must not inherit CardContent's pt-0/sm:pt-0.
    expect(container.querySelector('[data-slot="card-content"]')).toBeNull();
  });

  it("retains filter state and callback with long labels and large counts", () => {
    const onClick = vi.fn();
    const { rerender } = render(<ManagedSummaryCard label="等待人工确认后发布的资料" value={123456789} icon={icon} active onClick={onClick} />);
    const button = screen.getByRole("button", { name: "等待人工确认后发布的资料 123456789" });
    expect(button).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(<ManagedSummaryCard label="等待人工确认后发布的资料" value={123456789} icon={icon} onClick={onClick} />);
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("123456789")).toBeVisible();
  });
});
