import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage, Source } from "../types";
import { calculateCitationTooltipPlacement, Message } from "./Message";

const videoPlayerOpen = vi.hoisted(() => vi.fn());
const documentPreviewOpen = vi.hoisted(() => vi.fn());

vi.mock("./FeedbackBar", () => ({
  FeedbackBar: ({
    msg,
    canRegenerate,
    onRegenerate,
    onViewAnswerVersion,
  }: {
    msg: ChatMessage;
    canRegenerate?: boolean;
    onRegenerate?: (messageId: string) => void;
    onViewAnswerVersion?: (messageId: string, versionIndex: number) => void;
  }) => (
    <div aria-label="回答操作">
      <button
        type="button"
        aria-label="重新生成回答"
        disabled={!canRegenerate}
        onClick={() => onRegenerate?.(msg.id)}
      />
      {msg.answerVersions && msg.answerVersions.length > 1 && (
        <>
          <span>{msg.viewedVersionIndex} / {msg.answerVersions.length}</span>
          <button
            type="button"
            aria-label="查看上一个回答"
            onClick={() => onViewAnswerVersion?.(msg.id, msg.answerVersions![0].versionIndex)}
          />
        </>
      )}
    </div>
  ),
}));

vi.mock("../hooks/useVideoPlayer", () => ({
  timestampToSeconds: (timestamp: string | null | undefined) => timestamp === "00:00:12" ? 12 : 0,
  useVideoPlayer: () => ({ open: videoPlayerOpen }),
}));

vi.mock("../hooks/usePdfPreview", () => ({
  usePdfPreview: () => ({ open: documentPreviewOpen }),
}));

const source: Source = {
  parent_id: "parent-1",
  doc_title: "测试标准",
  section_path: "第一章",
  category: "行业规范",
  score: 0.9,
  rrf_score: 0.8,
  text: "来源正文",
  doc_type: "pdf",
  start_time: null,
  media_id: null,
  sheet_name: null,
  cell_range: null,
  slide_number: null,
  paragraph_anchor: null,
};

function assistant(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "assistant-1",
    role: "assistant",
    content: "完整回答",
    sources: [source],
    ...overrides,
  };
}

function prep(finalCount: number, noSourceFallback = false) {
  return {
    search_query: "测试查询",
    rewrite_applied: false,
    history_chars: 0,
    budget: 1000,
    fresh_count: finalCount,
    final_count: finalCount,
    used_sources: finalCount ? [source] : [],
    no_source_fallback: noSourceFallback,
  };
}

describe("Message assistant actions", () => {
  it("keeps a top-edge tooltip below the message fade boundary", () => {
    const placement = calculateCitationTooltipPlacement({
      markerTop: 340,
      markerBottom: 358,
      markerLeft: 320,
      tooltipWidth: 320,
      tooltipHeight: 220,
      viewportWidth: 1024,
      viewportHeight: 720,
      boundaryTop: 96,
    });

    expect(placement.showBelow).toBe(false);
    expect(placement.top).toBeGreaterThanOrEqual(64);
  });

  it("flips a citation tooltip below when the top boundary has no room", () => {
    const placement = calculateCitationTooltipPlacement({
      markerTop: 120,
      markerBottom: 138,
      markerLeft: 320,
      tooltipWidth: 240,
      tooltipHeight: 220,
      viewportWidth: 1024,
      viewportHeight: 720,
      boundaryTop: 96,
    });

    expect(placement.showBelow).toBe(true);
    expect(placement.top).toBe(140);
  });

  it("places sources on the left and answer actions in the same footer", () => {
    render(<Message msg={assistant()} conversationId="conversation-1" turnIndex={1} />);

    const sources = screen.getByRole("button", { name: "查看 1 个来源" });
    const actions = screen.getByLabelText("回答操作");

    expect(sources.parentElement).toBe(actions.parentElement);
  });

  it("keeps answer actions right-aligned when there are no sources", () => {
    render(<Message msg={assistant({ sources: [] })} conversationId="conversation-1" turnIndex={1} />);

    expect(screen.queryByRole("button", { name: /查看 .* 个来源/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("回答操作")).toBeInTheDocument();
  });

  it("hides answer actions while streaming or after an error", () => {
    const { rerender } = render(
      <Message msg={assistant({ streaming: true })} conversationId="conversation-1" turnIndex={1} />,
    );
    expect(screen.queryByLabelText("回答操作")).not.toBeInTheDocument();

    rerender(
      <Message msg={assistant({ streaming: false, error: "生成失败" })} conversationId="conversation-1" turnIndex={1} />,
    );
    expect(screen.queryByLabelText("回答操作")).not.toBeInTheDocument();
  });

  it("keeps a success status visible while answer content is streaming", () => {
    render(
      <Message
        msg={assistant({ content: "正在生成的回答", streaming: true, stage: "streaming", prep: prep(1) })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("正在输出回答，基于 1 份资料");
    expect(status.querySelector(".bg-success")).toBeInTheDocument();
  });

  it("shows a destructive status when streaming without retrieved sources", () => {
    render(
      <Message
        msg={assistant({
          content: "通用回复",
          sources: [],
          streaming: true,
          stage: "streaming",
          prep: prep(0, true),
        })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("未检索到可用资料，正在输出回复");
    expect(status.querySelector(".bg-destructive")).toBeInTheDocument();
  });

  it("keeps the no-source warning after the answer is complete", () => {
    render(
      <Message
        msg={assistant({ content: "通用回复", sources: [], streaming: false, stage: "done", prep: prep(0, true) })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("未检索到可用资料，本回答没有知识库来源");
  });

  it("shows a destructive status when retrieval confidence blocks generation", () => {
    render(
      <Message
        msg={assistant({
          content: "未找到足够相关的资料。请补充具体的查询对象。",
          sources: [],
          streaming: false,
          stage: "done",
          prep: prep(0, true),
          done: {
            answer_text: "未找到足够相关的资料。请补充具体的查询对象。",
            timings: {}, sources: [], history_chars: 0, budget: 0,
            finish_reason: "retrieval_low_confidence",
          },
        })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("资料相关性不足，未生成回答");
    expect(status.querySelector(".bg-destructive")).toBeInTheDocument();
  });

  it("shows that the user stopped a partial answer", () => {
    render(
      <Message
        msg={assistant({ content: "部分回答", stopped: true, streaming: false, stage: "done", prep: prep(7) })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("用户已停止回答，以下为已生成内容");
    expect(status).not.toHaveTextContent("回答基于");
  });

  it("copies a user question over the HTTP fallback and shows a temporary check", async () => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(
      <Message
        msg={{ id: "user-1", role: "user", content: "如何命名模型？" }}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "复制提问" }));
    await act(async () => {});
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(screen.getByRole("button", { name: "提问已复制" })).toHaveClass("text-success");

    act(() => vi.advanceTimersByTime(1400));
    expect(screen.getByRole("button", { name: "复制提问" })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("edits the latest user question in place and submits with Ctrl+Enter", () => {
    const edit = vi.fn();
    render(
      <Message
        msg={{ id: "21", role: "user", content: "原问题" }}
        conversationId="conversation-1"
        turnIndex={1}
        canEdit
        onEdit={edit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑提问" }));
    const textbox = screen.getByRole("textbox", { name: "编辑提问" });
    expect(textbox).toHaveValue("原问题");
    fireEvent.change(textbox, { target: { value: "编辑后的问题" } });
    fireEvent.keyDown(textbox, { key: "Enter", ctrlKey: true });

    expect(edit).toHaveBeenCalledWith("21", "编辑后的问题");
    expect(screen.queryByRole("textbox", { name: "编辑提问" })).not.toBeInTheDocument();
  });

  it("cancels question editing without emitting a change", () => {
    const edit = vi.fn();
    render(
      <Message
        msg={{ id: "21", role: "user", content: "原问题" }}
        conversationId="conversation-1"
        turnIndex={1}
        canEdit
        onEdit={edit}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑提问" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑提问" }), {
      target: { value: "不保存" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(edit).not.toHaveBeenCalled();
    expect(screen.getByText("原问题")).toBeInTheDocument();
  });

  it("shows question version navigation on the right side of user actions", () => {
    const viewQuestionVersion = vi.fn();
    render(
      <Message
        msg={{
          id: "21",
          role: "user",
          content: "编辑后的问题",
          viewedUserVersionIndex: 2,
          userVersions: [
            { id: "u1", versionIndex: 1, content: "原问题", createdAt: 1, isActive: false },
            { id: "u2", versionIndex: 2, content: "编辑后的问题", createdAt: 2, isActive: true },
          ],
        }}
        conversationId="conversation-1"
        turnIndex={1}
        canEdit
        onViewQuestionVersion={viewQuestionVersion}
      />,
    );

    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看上一个提问" }));
    expect(viewQuestionVersion).toHaveBeenCalledWith("21", 1);
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

  it("keeps a citation preview open while moving from the marker into the preview", () => {
    vi.useFakeTimers();
    render(
      <Message
        msg={assistant({ content: "命名规则见[1]。" })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    const marker = screen.getByRole("superscript");
    fireEvent.mouseEnter(marker);
    const preview = screen.getByRole("dialog", { name: "来源 1 预览" });
    expect(preview).toHaveTextContent("测试标准");
    expect(preview).toHaveClass(
      "bg-popover",
      "text-popover-foreground",
      "fixed",
      "visible",
      "pointer-events-auto",
    );

    fireEvent.mouseLeave(marker);
    fireEvent.mouseEnter(preview);
    act(() => vi.advanceTimersByTime(150));
    expect(screen.getByRole("dialog", { name: "来源 1 预览" })).toBeInTheDocument();

    fireEvent.mouseLeave(preview);
    act(() => vi.advanceTimersByTime(150));
    expect(screen.queryByRole("dialog", { name: "来源 1 预览" })).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("keeps an open citation tooltip when the assistant id becomes persisted", () => {
    const { rerender } = render(
      <Message
        msg={assistant({ id: "assistant-pending", content: "命名规则见[1]。", streaming: true })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    fireEvent.mouseEnter(screen.getByRole("superscript"));
    expect(screen.getByRole("dialog", { name: "来源 1 预览" })).toBeVisible();

    rerender(
      <Message
        msg={assistant({ id: "42", content: "命名规则见[1]。", streaming: false })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    expect(screen.getByRole("dialog", { name: "来源 1 预览" })).toBeVisible();
  });

  it("opens a citation preview from keyboard focus and closes it with Escape", () => {
    render(
      <Message
        msg={assistant({ content: "命名规则见[1]。" })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    const marker = screen.getByRole("button", { name: "查看来源 1：测试标准" });
    fireEvent.focus(marker);
    expect(screen.getByRole("dialog", { name: "来源 1 预览" })).toBeVisible();
    expect(marker).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(marker, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "来源 1 预览" })).not.toBeInTheDocument();
    expect(marker).toHaveFocus();
    expect(marker).toHaveAttribute("aria-expanded", "false");
  });

  it("plays a video citation from its independent action at the cited timestamp", () => {
    videoPlayerOpen.mockClear();
    const videoSource: Source = {
      ...source,
      doc_title: "Revit界面介绍__7d44513f",
      doc_type: "transcript",
      start_time: "00:00:12",
      media_id: "media-1",
    };
    render(
      <Message
        msg={assistant({
          content: "工具栏用法见[Revit界面介绍__7d44513f @00:00:12]。",
          sources: [videoSource],
        })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    fireEvent.mouseEnter(screen.getByRole("superscript"));
    const action = screen.getByRole("button", { name: "从 00:00:12 播放视频" });
    const title = screen.getByText("Revit界面介绍");
    expect(action.parentElement).toBe(title.parentElement);
    expect(action.parentElement).toHaveClass("flex", "items-center");
    expect(action.parentElement).not.toHaveClass("border-t");
    fireEvent.click(action);

    expect(videoPlayerOpen).toHaveBeenCalledWith({
      mediaId: "media-1",
      title: "Revit界面介绍",
      startSeconds: 12,
      fromSource: true,
    });
    expect(screen.queryByRole("dialog", { name: "来源 1 预览" })).not.toBeInTheDocument();
  });

  it("previews a supported document from its independent action with citation location", () => {
    documentPreviewOpen.mockClear();
    const presentationSource: Source = {
      ...source,
      parent_id: "presentation-parent",
      doc_title: "项目汇报",
      doc_type: "pptx",
      section_path: "交付汇报",
      slide_number: 7,
    };
    render(
      <Message
        msg={assistant({ content: "汇报要求见[1]。", sources: [presentationSource] })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    fireEvent.mouseEnter(screen.getByRole("superscript"));
    fireEvent.click(screen.getByRole("button", { name: "预览文档：项目汇报" }));

    expect(documentPreviewOpen).toHaveBeenCalledWith(
      "presentation-parent",
      "项目汇报",
      "pptx",
      7,
      {
        sheetName: null,
        cellRange: null,
        slideNumber: 7,
        paragraphAnchor: null,
      },
    );
  });

  it.each(["pdf", "docx", "xlsx", "pptx"])(
    "offers the preview action for %s citations",
    (docType) => {
      render(
        <Message
          msg={assistant({
            content: "文档要求见[1]。",
            sources: [{ ...source, doc_type: docType }],
          })}
          conversationId="conversation-1"
          turnIndex={1}
        />,
      );

      fireEvent.mouseEnter(screen.getByRole("superscript"));
      expect(screen.getByRole("button", { name: "预览文档：测试标准" })).toBeVisible();
    },
  );

  it("does not show misleading actions for unsupported or unlinked sources", () => {
    const unsupportedSource: Source = { ...source, doc_type: "md" };
    const { rerender } = render(
      <Message
        msg={assistant({ content: "说明见[1]。", sources: [unsupportedSource] })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    fireEvent.mouseEnter(screen.getByRole("superscript"));
    expect(screen.queryByRole("button", { name: /预览文档/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /播放视频/ })).not.toBeInTheDocument();

    rerender(
      <Message
        msg={assistant({
          content: "视频说明见[培训视频 @00:00:12]。",
          sources: [{
            ...source,
            doc_title: "培训视频",
            doc_type: "transcript",
            start_time: "00:00:12",
            media_id: null,
          }],
        })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );
    expect(screen.queryByRole("button", { name: /播放视频/ })).not.toBeInTheDocument();
  });

  it("opens source verification without opening the player for video citations", () => {
    const videoSource: Source = {
      ...source,
      doc_title: "Revit界面介绍__7d44513f",
      doc_type: "transcript",
      start_time: "00:00:01",
      media_id: "media-1",
    };
    const citationListener = vi.fn();
    window.addEventListener("pincheng:citation-click", citationListener);

    render(
      <Message
        msg={assistant({
          id: "assistant-video",
          content: "工具栏用法见[Revit界面介绍__7d44513f @00:00:01]。",
          sources: [videoSource],
        })}
        conversationId="conversation-1"
        turnIndex={1}
      />,
    );

    const marker = screen.getByRole("button", { name: /查看来源 1/ });
    fireEvent.click(marker);
    expect(citationListener).toHaveBeenCalledTimes(1);
    expect(videoPlayerOpen).not.toHaveBeenCalled();
    window.removeEventListener("pincheng:citation-click", citationListener);
  });
});
