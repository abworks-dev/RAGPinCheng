import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { findActiveSegmentIndex, TranscriptPanel } from "./TranscriptPanel";

const segments = [
  { id: 0, start_ms: 0, end_ms: 7000, text: "第一段" },
  { id: 1, start_ms: 7000, end_ms: 14000, text: "第二段" },
  { id: 2, start_ms: 14000, end_ms: null, text: "第三段" },
];

describe("TranscriptPanel", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    window.matchMedia = vi.fn().mockReturnValue({ matches: true });
  });

  it("finds active cues with binary search and respects gaps", () => {
    expect(findActiveSegmentIndex(segments, 0)).toBe(0);
    expect(findActiveSegmentIndex(segments, 7000)).toBe(1);
    expect(findActiveSegmentIndex(segments, 20000)).toBe(2);
    expect(findActiveSegmentIndex([{ ...segments[0], end_ms: 5000 }, segments[1]], 6000)).toBe(-1);
  });

  it("highlights the active cue and seeks when a cue is clicked", () => {
    const onSeek = vi.fn();
    render(
      <TranscriptPanel
        segments={segments}
        currentTimeMs={8000}
        loading={false}
        error={null}
        onSeek={onSeek}
      />,
    );

    expect(screen.getByText("第二段").closest("button")).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByRole("button", { name: "跳转到 00:14" }));
    expect(onSeek).toHaveBeenCalledWith(14000);
  });

  it("pauses following on manual scroll intent and lets the user resume", () => {
    render(
      <TranscriptPanel
        segments={segments}
        currentTimeMs={8000}
        loading={false}
        error={null}
        onSeek={vi.fn()}
      />,
    );

    fireEvent.wheel(screen.getByRole("region", { name: "视频转录稿" }).querySelector("[tabindex='0']")!);
    expect(screen.getByText("已暂停跟随")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "回到当前进度" }));
    expect(screen.getByText("自动跟随")).toBeInTheDocument();
  });
});
