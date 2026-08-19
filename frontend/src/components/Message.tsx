import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { ChatMessage, Source } from "../types";
import { stripMarkdown } from "../utils/markdown";
import { FeedbackBar } from "./FeedbackBar";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Copy,
  Eye,
  Files,
  Pencil,
  Play,
  Send,
  Video,
  X,
} from "lucide-react";
import {
  CITATION_EVENT,
  CITATION_HOVER_EVENT,
  CITATION_TOOLTIP_ACTIVE_EVENT,
  dispatchCitation,
  linkifyCitations,
  resolveCitation,
  type CitationHoverDetail,
  type CitationTooltipActiveDetail,
} from "./citations";
import { copyText } from "../utils/clipboard";
import { usePdfPreview } from "../hooks/usePdfPreview";
import { timestampToSeconds, useVideoPlayer } from "../hooks/useVideoPlayer";
import { sourceDisplayTitle, sourceLocator } from "../lib/source-export";

const CITATION_TOOLTIP_CLOSE_DELAY_MS = 150;
const CITATION_TOOLTIP_VIEWPORT_GUTTER = 8;
const CITATION_TOOLTIP_GAP = 2;
// Keep fixed previews below the 2.5rem top fade used by MessageList.
const CITATION_TOOLTIP_TOP_FADE_HEIGHT = 40;
const PREVIEWABLE_DOCUMENT_TYPES = new Set(["pdf", "docx", "xlsx", "pptx"]);

export type CitationTooltipPlacementInput = {
  markerTop: number;
  markerBottom: number;
  markerLeft: number;
  tooltipWidth: number;
  tooltipHeight: number;
  viewportWidth: number;
  viewportHeight: number;
  boundaryTop: number;
  boundaryBottom?: number;
};

export type CitationTooltipPlacement = {
  top: number;
  left: number;
  showBelow: boolean;
};

export function calculateCitationTooltipPlacement({
  markerTop,
  markerBottom,
  markerLeft,
  tooltipWidth,
  tooltipHeight,
  viewportWidth,
  viewportHeight,
  boundaryTop,
  boundaryBottom = viewportHeight,
}: CitationTooltipPlacementInput): CitationTooltipPlacement {
  const topBoundary = Math.max(CITATION_TOOLTIP_VIEWPORT_GUTTER, boundaryTop + CITATION_TOOLTIP_VIEWPORT_GUTTER);
  const bottomBoundary = Math.min(viewportHeight - CITATION_TOOLTIP_VIEWPORT_GUTTER, boundaryBottom - CITATION_TOOLTIP_VIEWPORT_GUTTER);
  const spaceAbove = markerTop - topBoundary;
  const spaceBelow = bottomBoundary - markerBottom;
  const showBelow = spaceAbove < tooltipHeight + CITATION_TOOLTIP_GAP && spaceBelow >= spaceAbove;
  const unclampedTop = showBelow
    ? markerBottom + CITATION_TOOLTIP_GAP
    : markerTop - tooltipHeight - CITATION_TOOLTIP_GAP;
  const maxTop = Math.max(topBoundary, bottomBoundary - tooltipHeight);
  const top = Math.min(Math.max(topBoundary, unclampedTop), maxTop);
  const maxLeft = Math.max(CITATION_TOOLTIP_VIEWPORT_GUTTER, viewportWidth - tooltipWidth - CITATION_TOOLTIP_VIEWPORT_GUTTER);
  const left = Math.min(Math.max(CITATION_TOOLTIP_VIEWPORT_GUTTER, markerLeft), maxLeft);

  return { top, left, showBelow };
}

const CitationContext = React.createContext<{ messageId: string; sources: Source[] }>({
  messageId: "",
  sources: [],
});

// Demote inline `$$...$$` to `$...$` so KaTeX renders it inline instead of as
// a block break. Standalone display blocks on their own line are kept.
function normalizeMath(src: string): string {
  return src.replace(/\$\$([^\n$]+?)\$\$/g, (match, body, offset, full) => {
    const before = full[offset - 1];
    const after = full[offset + match.length];
    const inline =
      (before !== undefined && before !== "\n") ||
      (after !== undefined && after !== "\n");
    return inline ? `$${body}$` : match;
  });
}

function AnswerStatus({ msg }: { msg: ChatMessage }) {
  if (msg.error) return null;

  const sources = msg.sources?.length ? msg.sources : msg.prep?.used_sources || [];
  const sourceCount = sources.length || msg.prep?.final_count || 0;
  const stage = msg.stage || (msg.streaming ? "streaming" : msg.content ? "done" : undefined);
  const noSources = msg.prep?.no_source_fallback === true || (stage !== "retrieving" && sourceCount === 0);
  const categories = Array.from(new Set(sources.map((source) => source.category).filter(Boolean)));
  const categoryLabel = categories.length > 0 ? categories.slice(0, 3).join("、") : "企业知识库";
  let label: string | null = null;
  let tone: "warning" | "success" | "destructive" = "success";
  let pulse = false;

  if (msg.regenerationStopped) {
    label = "已停止重新生成，仍显示上一次回答";
    tone = "warning";
  } else if (msg.stopped) {
    label = "用户已停止回答，以下为已生成内容";
    tone = "warning";
  } else if (stage === "retrieving") {
    label = "正在理解问题并检索企业知识…";
    tone = "warning";
    pulse = true;
  } else if (stage === "generating") {
    label = noSources
      ? "未检索到可用资料，正在组织回复…"
      : `已检索 ${sourceCount} 份资料，正在组织回答…`;
    tone = noSources ? "destructive" : "success";
    pulse = true;
  } else if (stage === "streaming") {
    label = noSources
      ? "未检索到可用资料，正在输出回复…"
      : `正在输出回答，基于 ${sourceCount} 份资料`;
    tone = noSources ? "destructive" : "success";
    pulse = true;
  } else if (stage === "done" && msg.content) {
    if (msg.done?.finish_reason === "retrieval_low_confidence") {
      label = "资料相关性不足，未生成回答";
      tone = "destructive";
    } else {
      label = noSources
      ? "未检索到可用资料，本回答没有知识库来源"
      : `已检索 ${sourceCount} 份资料，回答基于${categoryLabel}`;
      tone = noSources ? "destructive" : "success";
    }
  }

  if (!label) return null;
  const dotClass = tone === "warning" ? "bg-warning" : tone === "destructive" ? "bg-destructive" : "bg-success";

  return (
    <div className="mb-4 flex items-center gap-2 text-xs text-muted-foreground" role="status">
      <span className="relative flex size-2.5 shrink-0">
        {pulse && <span className={`absolute inline-flex size-full animate-ping rounded-full opacity-30 ${dotClass}`} />}
        <span className={`relative inline-flex size-2.5 rounded-full ${dotClass}`} />
      </span>
      <span>{label}</span>
    </div>
  );
}

// Renders a citation superscript with tooltip preview.
// WPS-style clean badge: subtle background, no harsh borders, precise vertical alignment.
function CitationMarker({
  href,
  sources,
  messageId,
  children,
}: {
  href: string;
  sources: Source[];
  messageId: string;
  children: React.ReactNode;
}) {
  const idx = resolveCitation(href, sources);
  const source = idx >= 0 ? sources[idx] : null;
  const { open: openDocument } = usePdfPreview();
  const { open: openVideo } = useVideoPlayer();
  const [isOpen, setIsOpen] = useState(false);
  const [isHighlighted, setIsHighlighted] = useState(false);
  const [showBelow, setShowBelow] = useState(false);
  const [tooltipPosition, setTooltipPosition] = useState({ top: 0, left: 0 });
  const [isPositioned, setIsPositioned] = useState(false);
  const markerRef = useRef<HTMLElement>(null);
  const markerButtonRef = useRef<HTMLButtonElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<number | null>(null);
  const tooltipActiveRef = useRef(false);
  const suppressFocusOpenRef = useRef(false);
  const markerId = useId();

  const setTooltipActive = (active: boolean) => {
    if (tooltipActiveRef.current === active) return;
    tooltipActiveRef.current = active;
    window.dispatchEvent(
      new CustomEvent<CitationTooltipActiveDetail>(CITATION_TOOLTIP_ACTIVE_EVENT, {
        detail: { markerId, active },
      }),
    );
  };
  const cancelClose = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const openTooltip = () => {
    if (suppressFocusOpenRef.current) return;
    cancelClose();
    if (!tooltipActiveRef.current) setIsPositioned(false);
    setTooltipActive(true);
    setIsOpen(true);
  };

  const closeTooltip = (restoreFocus = false) => {
    cancelClose();
    setIsOpen(false);
    setIsPositioned(false);
    setTooltipActive(false);
    if (restoreFocus) {
      suppressFocusOpenRef.current = true;
      markerButtonRef.current?.focus({ preventScroll: true });
      queueMicrotask(() => {
        suppressFocusOpenRef.current = false;
      });
    }
  };

  const scheduleClose = () => {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => {
      const activeElement = document.activeElement;
      if (
        (activeElement && markerRef.current?.contains(activeElement))
        || (activeElement && tooltipRef.current?.contains(activeElement))
      ) {
        closeTimerRef.current = null;
        return;
      }
      closeTooltip();
      closeTimerRef.current = null;
    }, CITATION_TOOLTIP_CLOSE_DELAY_MS);
  };

  const handleEscape = (event: React.KeyboardEvent) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    closeTooltip(true);
  };

  useEffect(() => () => {
    cancelClose();
    setTooltipActive(false);
  }, []);

  // Listen for hover events from SourceWorkspace (card -> in-message highlight).
  useEffect(() => {
    function onHover(e: Event) {
      const detail = (e as CustomEvent<CitationHoverDetail>).detail;
      if (detail.messageId !== messageId) return;
      setIsHighlighted(detail.sourceIndex === idx);
    }
    window.addEventListener(CITATION_HOVER_EVENT, onHover);
    return () => window.removeEventListener(CITATION_HOVER_EVENT, onHover);
  }, [messageId, idx]);

  // Calculate while hidden so the first hover never visibly jumps into the fade layer.
  useLayoutEffect(() => {
    if (!isOpen || !markerRef.current || !tooltipRef.current) {
      setIsPositioned(false);
      return;
    }

    const positionTooltip = () => {
      if (!markerRef.current || !tooltipRef.current) return;
      const markerRect = markerRef.current.getBoundingClientRect();
      const tooltipRect = tooltipRef.current.getBoundingClientRect();
      const scrollContainer = markerRef.current.closest<HTMLElement>("[data-message-scroll-container]");
      const scrollRect = scrollContainer?.getBoundingClientRect();
      const placement = calculateCitationTooltipPlacement({
        markerTop: markerRect.top,
        markerBottom: markerRect.bottom,
        markerLeft: markerRect.left,
        tooltipWidth: tooltipRect.width,
        tooltipHeight: tooltipRect.height,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        boundaryTop: scrollRect ? scrollRect.top + CITATION_TOOLTIP_TOP_FADE_HEIGHT : 0,
        boundaryBottom: scrollRect ? Math.min(window.innerHeight, scrollRect.bottom) : window.innerHeight,
      });
      setShowBelow(placement.showBelow);
      setTooltipPosition({ top: placement.top, left: placement.left });
      setIsPositioned(true);
    };

    positionTooltip();
    const onViewportChange = () => positionTooltip();
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
    };
  }, [isOpen]);

  if (!source) {
    // Fallback: no matching source found — render as plain text.
    return <span className="text-gray-500">{children}</span>;
  }

  // Tooltip preview text: first 120 chars of the source, with markdown syntax stripped.
  const cleanText = stripMarkdown(source.text);
  const preview = cleanText.length > 120 ? cleanText.slice(0, 120) + "…" : cleanText;
  const isVideo = source.doc_type === "transcript";
  const canPlayVideo = isVideo && Boolean(source.media_id);
  const canPreviewDocument = PREVIEWABLE_DOCUMENT_TYPES.has(source.doc_type);
  const popoverId = `${markerId}-source-preview`;

  const playVideoAtCitation = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (!source.media_id) return;
    closeTooltip(true);
    openVideo({
      mediaId: source.media_id,
      title: sourceDisplayTitle(source),
      startSeconds: timestampToSeconds(source.start_time),
      fromSource: true,
    });
  };

  const previewDocumentAtCitation = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (!canPreviewDocument) return;
    closeTooltip(true);
    openDocument(
      source.parent_id,
      source.doc_title,
      source.doc_type,
      source.doc_type === "pptx" && source.slide_number ? source.slide_number : 1,
      {
        sheetName: source.sheet_name,
        cellRange: source.cell_range,
        slideNumber: source.slide_number,
        paragraphAnchor: source.paragraph_anchor,
      },
    );
  };

  return (
    <>
      <sup
        ref={markerRef}
        className="relative top-[-0.35em] mx-px align-baseline"
        onMouseEnter={openTooltip}
        onMouseLeave={scheduleClose}
        onFocus={openTooltip}
        onBlur={scheduleClose}
        onKeyDown={handleEscape}
      >
        <button
          ref={markerButtonRef}
          type="button"
          aria-label={`查看来源 ${idx + 1}：${sourceDisplayTitle(source)}`}
          aria-haspopup="dialog"
          aria-expanded={isOpen}
          aria-controls={isOpen ? popoverId : undefined}
          className={`inline-flex items-center justify-center cursor-pointer font-sans text-[11px] h-[18px] min-w-[18px] px-1 rounded transition-all ${
            isHighlighted
              ? "bg-accent text-white scale-110"
              : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:hover:bg-gray-700 border border-gray-200"
          }`}
          onClick={(e) => {
            e.preventDefault();
            if (idx >= 0) {
              dispatchCitation({ messageId, sourceIndex: idx });
            }
          }}
        >
          {idx + 1}
        </button>

      </sup>
      {isOpen && typeof document !== "undefined" && createPortal(
        <div
          id={popoverId}
          ref={tooltipRef}
          role="dialog"
          aria-label={`来源 ${idx + 1} 预览`}
          data-placement={showBelow ? "below" : "above"}
          onMouseEnter={openTooltip}
          onMouseLeave={scheduleClose}
          onFocus={openTooltip}
          onBlur={scheduleClose}
          onKeyDown={handleEscape}
          style={{ top: tooltipPosition.top, left: tooltipPosition.left }}
          className={`fixed z-[100] block min-w-[200px] max-w-[320px] max-h-[min(80vh,22rem)] overflow-y-auto rounded-ui-lg border border-border bg-popover p-3 text-xs text-popover-foreground shadow-overlay break-words ${
            isPositioned
              ? "visible pointer-events-auto opacity-100"
              : "invisible pointer-events-none opacity-0"
          }`}
        >
          <div className="mb-1 flex min-w-0 items-center justify-between gap-2">
            <span className="min-w-0 flex-1 truncate font-medium text-popover-foreground">{sourceDisplayTitle(source)}</span>
            {canPlayVideo && (
              <button
                type="button"
                onClick={playVideoAtCitation}
                aria-label={`从 ${sourceLocator(source)} 播放视频`}
                title={`从 ${sourceLocator(source)} 播放视频`}
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-ui-md text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Play className="size-4" aria-hidden="true" />
              </button>
            )}
            {canPreviewDocument && (
              <button
                type="button"
                onClick={previewDocumentAtCitation}
                aria-label={`预览文档：${sourceDisplayTitle(source)}`}
                title="预览文档"
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-ui-md text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Eye className="size-4" aria-hidden="true" />
              </button>
            )}
          </div>
          <span className="mb-2 flex items-center gap-1.5 truncate text-muted-foreground">
            {isVideo ? (
              <>
                <Video className="size-3.5" aria-hidden="true" />
                视频来源 · {sourceLocator(source)}
              </>
            ) : (
              `文档来源 · ${sourceLocator(source)}`
            )}
          </span>
          <span className="block whitespace-pre-wrap break-words leading-relaxed text-popover-foreground/85">{preview}</span>
        </div>,
        document.body,
      )}
    </>
  );
}

function MessageMarkdownLink({
  href,
  children,
  node: _node,
  ...props
}: React.ComponentPropsWithoutRef<"a"> & { node?: unknown }) {
  const { messageId, sources } = React.useContext(CitationContext);
  if (href?.startsWith("#cite-")) {
    return (
      <CitationMarker href={href} sources={sources} messageId={messageId} {...props}>
        {children}
      </CitationMarker>
    );
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
      {children}
    </a>
  );
}

const MESSAGE_MARKDOWN_COMPONENTS = { a: MessageMarkdownLink };

export function Message({
  msg,
  conversationId,
  turnIndex,
  sourcesSelected = false,
  onToggleSources,
  canEdit = false,
  onEdit,
  onViewQuestionVersion,
  canRegenerate = false,
  onRegenerate,
  onViewAnswerVersion,
}: {
  msg: ChatMessage;
  conversationId: string | null;
  turnIndex: number;
  sourcesSelected?: boolean;
  onToggleSources?: (messageId: string) => void;
  canEdit?: boolean;
  onEdit?: (messageId: string, content: string) => void;
  onViewQuestionVersion?: (messageId: string, versionIndex: number) => void;
  canRegenerate?: boolean;
  onRegenerate?: (messageId: string) => void;
  onViewAnswerVersion?: (messageId: string, versionIndex: number) => void;
}) {
  const isUser = msg.role === "user";
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);
  const editRef = useRef<HTMLTextAreaElement | null>(null);
  const activeUserVersion = msg.userVersions?.find((version) => version.isActive);
  const viewedUserVersionIndex =
    msg.viewedUserVersionIndex ?? activeUserVersion?.versionIndex;
  const viewedUserVersionPosition = msg.userVersions?.findIndex(
    (version) => version.versionIndex === viewedUserVersionIndex,
  ) ?? -1;
  const viewingActiveUserVersion =
    !activeUserVersion || viewedUserVersionIndex === activeUserVersion.versionIndex;
  const copyResetTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
  }, []);

  useEffect(() => {
    if (!editing) setDraft(msg.content);
  }, [editing, msg.content]);

  useEffect(() => {
    if (!editing) return;
    editRef.current?.focus();
    editRef.current?.setSelectionRange(draft.length, draft.length);
  }, [editing]);

  const submitEdit = () => {
    const trimmed = draft.trim();
    if (!trimmed || trimmed === msg.content) {
      setEditing(false);
      setDraft(msg.content);
      return;
    }
    setEditing(false);
    onEdit?.(msg.id, trimmed);
  };

  const copyContent = async () => {
    try {
      await copyText(msg.content);
      setCopied(true);
      if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
      copyResetTimer.current = window.setTimeout(() => {
        setCopied(false);
        copyResetTimer.current = null;
      }, 1400);
    } catch {
      setCopied(false);
    }
  };
  return (
    <article className={`mx-auto flex w-full max-w-[50rem] ${isUser ? "justify-end" : "justify-start"} px-4 py-4`}>
      <div
        className={
          (isUser
            ? editing
              ? "flex w-full max-w-[85%] flex-col items-end"
              : "flex w-full flex-col items-end"
            : "min-w-0 w-full") +
          ""
        }
      >
        {isUser ? (
          <>
            {editing ? (
              <div className="w-full min-w-[22rem] rounded-ui-lg bg-primary p-4 text-primary-foreground shadow-surface">
                <label htmlFor={`edit-question-${msg.id}`} className="sr-only">编辑提问</label>
                <textarea
                  ref={editRef}
                  id={`edit-question-${msg.id}`}
                  value={draft}
                  rows={Math.min(8, Math.max(2, draft.split("\n").length))}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      setEditing(false);
                      setDraft(msg.content);
                    } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                      event.preventDefault();
                      submitEdit();
                    }
                  }}
                  className="max-h-72 min-h-28 w-full resize-y rounded-ui-md border border-white/30 bg-white/10 px-4 py-3 text-sm leading-6 text-primary-foreground outline-none placeholder:text-primary-foreground/60 focus:border-white/70 focus:ring-2 focus:ring-white/20"
                />
                <div className="mt-2 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(false);
                      setDraft(msg.content);
                    }}
                    className="inline-flex h-8 items-center gap-1.5 rounded-ui-md px-3 text-xs font-medium hover:bg-white/10"
                  >
                    <X className="size-3.5" />取消
                  </button>
                  <button
                    type="button"
                    onClick={submitEdit}
                    disabled={!draft.trim() || draft.trim() === msg.content}
                    className="inline-flex h-8 items-center gap-1.5 rounded-ui-md bg-white px-3 text-xs font-medium text-primary shadow-sm hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Send className="size-3.5" />发送
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="max-w-[70%] rounded-ui-lg bg-primary px-4 py-3 text-primary-foreground">
                  <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                </div>
                <div className="mt-2 flex w-full items-center justify-end gap-3">
                  {msg.userVersions && msg.userVersions.length > 1 && viewedUserVersionPosition >= 0 && (
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <button
                        type="button"
                        aria-label="查看上一个提问"
                        title="查看上一个提问"
                        disabled={viewedUserVersionPosition <= 0}
                        onClick={() => onViewQuestionVersion?.(
                          msg.id,
                          msg.userVersions![viewedUserVersionPosition - 1].versionIndex,
                        )}
                        className="inline-flex size-7 items-center justify-center rounded-ui-md hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        <ChevronLeft className="size-4" />
                      </button>
                      <span className="min-w-8 text-center tabular-nums">
                        {viewedUserVersionPosition + 1} / {msg.userVersions.length}
                      </span>
                      <button
                        type="button"
                        aria-label="查看下一个提问"
                        title="查看下一个提问"
                        disabled={viewedUserVersionPosition >= msg.userVersions.length - 1}
                        onClick={() => onViewQuestionVersion?.(
                          msg.id,
                          msg.userVersions![viewedUserVersionPosition + 1].versionIndex,
                        )}
                        className="inline-flex size-7 items-center justify-center rounded-ui-md hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        <ChevronRight className="size-4" />
                      </button>
                    </div>
                  )}
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-label={copied ? "提问已复制" : "复制提问"}
                      title={copied ? "提问已复制" : "复制提问"}
                      onClick={copyContent}
                      className={`inline-flex size-9 items-center justify-center rounded-ui-md hover:bg-secondary ${copied ? "text-success hover:text-success" : "text-muted-foreground hover:text-foreground"}`}
                    >
                      {copied ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}
                    </button>
                    {canEdit && viewingActiveUserVersion && (
                      <button
                        type="button"
                        aria-label="编辑提问"
                        title="编辑提问"
                        onClick={() => {
                          setDraft(msg.content);
                          setEditing(true);
                        }}
                        className="inline-flex size-9 items-center justify-center rounded-ui-md text-muted-foreground hover:bg-secondary hover:text-foreground"
                      >
                        <Pencil className="size-4" />
                      </button>
                    )}
                  </div>
                </div>
              </>
            )}
          </>
        ) : (
          <>
            <AnswerStatus msg={msg} />
            <div className={"prose-tight relative " + (msg.streaming && msg.content ? "caret" : "")}>
              {msg.content ? (
                <CitationContext.Provider value={{ messageId: msg.id, sources: msg.sources || [] }}>
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                    components={MESSAGE_MARKDOWN_COMPONENTS}
                  >
                    {linkifyCitations(normalizeMath(msg.content))}
                  </ReactMarkdown>
                </CitationContext.Provider>
              ) : null}
            </div>
            {msg.error && (
              <div className="mt-2 flex items-start gap-2 rounded-ui-md border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span>{msg.error}</span>
              </div>
            )}
            {!msg.streaming && !msg.error && msg.content && (
              <div className="mt-5 flex min-h-9 items-center justify-between gap-3 border-t border-border pt-3">
                {msg.sources && msg.sources.length > 0 && (
                  <button
                    type="button"
                    onClick={() => onToggleSources?.(msg.id)}
                    className={`inline-flex h-9 items-center gap-2 rounded-ui-md border px-3 text-xs font-medium shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${sourcesSelected ? "border-primary bg-primary/10 text-primary" : "border-border bg-card text-foreground hover:border-primary/40 hover:bg-secondary"}`}
                  >
                    <Files className="size-4 text-primary" />
                    查看 {msg.sources.length} 个来源
                  </button>
                )}
                <FeedbackBar
                  msg={msg}
                  conversationId={conversationId}
                  turnIndex={turnIndex}
                  canRegenerate={canRegenerate}
                  onRegenerate={onRegenerate}
                  onViewAnswerVersion={onViewAnswerVersion}
                />
              </div>
            )}
          </>
        )}
      </div>
    </article>
  );
}
