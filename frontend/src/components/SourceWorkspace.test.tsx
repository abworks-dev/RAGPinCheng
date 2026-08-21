import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, Source } from "../types";
import { SourceWorkspace } from "./SourceWorkspace";

const videoPlayerOpen = vi.hoisted(() => vi.fn());
const documentPreviewOpen = vi.hoisted(() => vi.fn());

vi.mock("../hooks/usePdfPreview", () => ({
  usePdfPreview: () => ({ open: documentPreviewOpen }),
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

const spreadsheetSource: Source = {
  ...videoSource,
  parent_id: "spreadsheet-source-1",
  doc_title: "构件清单",
  doc_type: "xlsx",
  text: "构件统计数据",
  category: "项目资料",
  start_time: null,
  media_id: null,
  sheet_name: "统计表",
  cell_range: "B2:F20",
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
  beforeEach(() => {
    vi.restoreAllMocks();
    videoPlayerOpen.mockReset();
    documentPreviewOpen.mockReset();
  });
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

  it("opens a document from the action beside its location", () => {
    renderWorkspace(spreadsheetSource);

    expect(screen.queryByRole("button", { name: "打开完整资料" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看 统计表 · B2:F20" }));
    expect(documentPreviewOpen).toHaveBeenCalledWith(
      "spreadsheet-source-1",
      "构件清单",
      "xlsx",
      1,
      {
        sheetName: "统计表",
        cellRange: "B2:F20",
        slideNumber: null,
        paragraphAnchor: null,
      },
    );
  });

  it("shows friendly intro and company labels in the list and detail", () => {
    renderWorkspace({
      ...spreadsheetSource,
      doc_type: "pdf",
      section_path: "(intro)",
      category: "公司内部标准",
      company: "品茗股份",
      sheet_name: null,
      cell_range: null,
    });

    expect(screen.getByText("公司内部标准 · 品茗股份 · 文档开头")).toBeInTheDocument();
    expect(screen.getByText("来源 1 · 公司内部标准 · 品茗股份")).toBeInTheDocument();
  });

  it("copies and downloads all sources for the active answer only", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    renderWorkspace(spreadsheetSource);

    fireEvent.click(screen.getByRole("button", { name: "复制全部来源" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已复制 1 项来源");
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("## 1. 构件清单"));

    fireEvent.click(screen.getByRole("button", { name: "下载来源文件" }));
    expect(screen.getByRole("dialog", { name: "下载来源文件" })).toBeInTheDocument();
    expect(screen.getByText("当前来源没有可下载文件。")).toBeInTheDocument();
  });

  it("exports only the explicitly selected answer sources", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const messages: ChatMessage[] = [
      { id: "assistant-1", role: "assistant", content: "第一版", sources: [videoSource] },
      { id: "assistant-2", role: "assistant", content: "第二版", sources: [spreadsheetSource] },
    ];
    render(<SourceWorkspace messages={messages} conversationId="conversation-1" selectedMessageId="assistant-1" />);

    fireEvent.click(screen.getByRole("button", { name: "复制全部来源" }));

    expect(await screen.findByRole("status")).toHaveTextContent("已复制 1 项来源");
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("## 1. Revit界面介绍"));
    expect(writeText).not.toHaveBeenCalledWith(expect.stringContaining("构件清单"));
  });

  it("falls back when clipboard permission is denied for source copies", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("Permission denied", "NotAllowedError"));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", { configurable: true, value: execCommand });
    renderWorkspace(spreadsheetSource);

    fireEvent.click(screen.getByRole("button", { name: "复制全部来源" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已复制 1 项来源");

    fireEvent.click(screen.getByRole("button", { name: "复制来源" }));
    expect(await screen.findByText("已复制")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(2);
    expect(execCommand).toHaveBeenCalledTimes(2);
    expect(document.querySelector("textarea")).not.toBeInTheDocument();
  });

  it("reports a copy failure when both clipboard methods fail", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn().mockReturnValue(false) });
    renderWorkspace(spreadsheetSource);

    fireEvent.click(screen.getByRole("button", { name: "复制全部来源" }));
    expect(await screen.findByRole("status")).toHaveTextContent("复制全部来源失败");

    fireEvent.click(screen.getByRole("button", { name: "复制来源" }));
    expect(await screen.findByText("复制失败，请手动选择原文。")).toBeInTheDocument();
    expect(document.querySelector("textarea")).not.toBeInTheDocument();
  });

  it("disables bulk export when there are no sources", () => {
    render(<SourceWorkspace messages={[]} conversationId="conversation-1" />);

    expect(screen.getByRole("button", { name: "复制全部来源" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下载来源文件" })).toBeDisabled();
  });
});
