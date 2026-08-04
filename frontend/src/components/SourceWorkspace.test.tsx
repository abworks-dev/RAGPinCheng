import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage, Source } from "../types";
import { SourceWorkspace } from "./SourceWorkspace";

const videoPlayerOpen = vi.hoisted(() => vi.fn());

vi.mock("../hooks/usePdfPreview", () => ({
  usePdfPreview: () => ({ open: vi.fn() }),
}));

vi.mock("../hooks/useVideoPlayer", () => ({
  timestampToSeconds: (timestamp: string | null | undefined) => {
    if (!timestamp) return 0;
    return timestamp.split(":").reduce((total, part) => total * 60 + Number(part), 0);
  },
  useVideoPlayer: () => ({ open: videoPlayerOpen }),
}));

vi.mock("./FeedbackDialog", () => ({
  FeedbackDialog: () => null,
}));

const videoSource: Source = {
  parent_id: "video-source-1",
  doc_title: "Revit界面介绍__7d44513f",
  doc_type: "transcript",
  section_path: "",
  text: "这里介绍 Revit 工具栏。",
  category: "教学视频",
  score: 0.9,
  rrf_score: 0.9,
  start_time: "00:06:13",
  media_id: "media-1",
  sheet_name: null,
  cell_range: null,
  slide_number: null,
  paragraph_anchor: null,
};

function renderWorkspace(source = videoSource) {
  const messages: ChatMessage[] = [
    {
      id: "assistant-1",
      role: "assistant",
      content: "回答",
      sources: [source],
    },
  ];

  render(<SourceWorkspace messages={messages} conversationId="conversation-1" />);
}

describe("SourceWorkspace video sources", () => {
  it("uses the video card treatment without exposing the binding suffix", () => {
    renderWorkspace();

    expect(screen.getAllByText("Revit界面介绍")).toHaveLength(2);
    expect(screen.queryByText(/7d44513f/)).not.toBeInTheDocument();
    expect(screen.getByText("教学视频 · 时间 00:06:13")).toBeInTheDocument();
  });

  it("plays from the located timestamp only from the location action", () => {
    renderWorkspace();

    expect(screen.queryByRole("button", { name: "打开完整资料" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "从 00:06:13 播放视频" }));
    expect(videoPlayerOpen).toHaveBeenCalledWith({
      mediaId: "media-1",
      title: "Revit界面介绍",
      startSeconds: 373,
      fromSource: true,
    });
  });
});
