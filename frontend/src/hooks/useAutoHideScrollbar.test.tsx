import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAutoHideScrollbar } from "./useAutoHideScrollbar";

function ScrollFixture() {
  const scrollbar = useAutoHideScrollbar<HTMLDivElement>();
  return (
    <div
      data-testid="scroll-area"
      ref={scrollbar.ref}
      className={scrollbar.className}
      {...scrollbar.interactionProps}
    />
  );
}

describe("useAutoHideScrollbar", () => {
  afterEach(() => vi.useRealTimers());

  it("shows during interaction and hides after the idle delay", () => {
    vi.useFakeTimers();
    render(<ScrollFixture />);
    const area = screen.getByTestId("scroll-area");

    fireEvent.mouseMove(area);
    expect(area).toHaveClass("scrollbar-visible");

    act(() => vi.advanceTimersByTime(899));
    expect(area).toHaveClass("scrollbar-visible");
    act(() => vi.advanceTimersByTime(1));
    expect(area).not.toHaveClass("scrollbar-visible");
  });
});
