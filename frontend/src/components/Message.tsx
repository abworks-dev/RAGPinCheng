import React, { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { ChatMessage, Source } from "../types";
import { stripMarkdown } from "../utils/markdown";
import { FeedbackBar } from "./FeedbackBar";
import { timestampToSeconds, useVideoPlayer } from "../hooks/useVideoPlayer";
import { CircleAlert, CirclePlay, Files } from "lucide-react";
import {
  CITATION_EVENT,
  CITATION_HOVER_EVENT,
  dispatchCitation,
  linkifyCitations,
  resolveCitation,
  type CitationHoverDetail,
} from "./citations";

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

  if (stage === "retrieving") {
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
    label = noSources
      ? "未检索到可用资料，本回答没有知识库来源"
      : `已检索 ${sourceCount} 份资料，回答基于${categoryLabel}`;
    tone = noSources ? "destructive" : "success";
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
  const [isHovered, setIsHovered] = useState(false);
  const [isHighlighted, setIsHighlighted] = useState(false);
  const [showBelow, setShowBelow] = useState(false);
  const [showRightAligned, setShowRightAligned] = useState(false);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const { open: openPlayer } = useVideoPlayer();

  // Listen for hover events from SourcesPanel (card → in-message highlight).
  useEffect(() => {
    function onHover(e: Event) {
      const detail = (e as CustomEvent<CitationHoverDetail>).detail;
      if (detail.messageId !== messageId) return;
      setIsHighlighted(detail.sourceIndex === idx);
    }
    window.addEventListener(CITATION_HOVER_EVENT, onHover);
    return () => window.removeEventListener(CITATION_HOVER_EVENT, onHover);
  }, [messageId, idx]);

  // Smart vertical and horizontal positioning: flip to below if overflowing viewport top,
  // flip to right-aligned if overflowing viewport right edge.
  useLayoutEffect(() => {
    if (!isHovered || !tooltipRef.current) {
      setShowBelow(false);
      setShowRightAligned(false);
      return;
    }
    const rect = tooltipRef.current.getBoundingClientRect();
    // If tooltip top edge is above viewport (with 10px margin), flip to below
    if (rect.top < 10) {
      setShowBelow(true);
    }
    // If tooltip right edge is beyond viewport (with 10px margin), flip to leftwards
    if (rect.right > window.innerWidth - 10) {
      setShowRightAligned(true);
    }
  }, [isHovered]);

  if (!source) {
    // Fallback: no matching source found — render as plain text.
    return <span className="text-gray-500">{children}</span>;
  }

  // Tooltip preview text: first 120 chars of the source, with markdown syntax stripped.
  const cleanText = stripMarkdown(source.text);
  const preview = cleanText.length > 120 ? cleanText.slice(0, 120) + "…" : cleanText;

  return (
    <>
      <sup
        className="relative top-[-0.35em] align-baseline"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        <a
          className={`inline-flex items-center justify-center cursor-pointer font-sans text-[11px] h-[18px] min-w-[18px] px-1 rounded transition-all ${
            isHighlighted
              ? "bg-accent text-white scale-110"
              : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:hover:bg-gray-700 border border-gray-200"
          }`}
          onClick={(e) => {
            e.preventDefault();
            if (idx >= 0) {
              dispatchCitation({ messageId, sourceIndex: idx });
              // For transcript citations with media, also open the video player
              if (source.doc_type === "transcript" && source.media_id) {
                openPlayer({
                  mediaId: source.media_id,
                  title: source.doc_title,
                  startSeconds: timestampToSeconds(source.start_time),
                  fromSource: false,
                });
              }
            }
          }}
        >
          {idx + 1}
        </a>

        {/* Tooltip: smart vertical and horizontal positioning.
            Default: above the superscript (with tiny overlap to prevent gap flicker).
            If overflowing viewport top: flip to below (via showBelow state).
            Horizontal alignment: left-0 (rightwards) by default to avoid sidebar clipping.
            If overflowing viewport right edge: flip to right-0 (leftwards) via showRightAligned state. */}
        {isHovered && (
          <div
            ref={tooltipRef}
            className={`absolute z-[100] min-w-[200px] max-w-[320px] bg-white border border-gray-200 rounded-lg shadow-xl p-3 text-xs break-words ${
              showBelow ? "top-[100%] mt-0.5" : "bottom-[100%] mb-0.5"
            } ${showRightAligned ? "right-0" : "left-0"}`}
          >
            <div className="font-medium text-gray-900 mb-1 truncate">{source.doc_title}</div>
            <div className="text-gray-500 mb-2 truncate flex items-center gap-1.5">
              {source.doc_type === "transcript" ? (
                <>
                  {source.media_id && (
                    <CirclePlay className="size-3.5 text-primary" aria-hidden="true" />
                  )}
                  @{source.start_time || ""}
                </>
              ) : (
                `§${((source.section_path || "").replace(/<[^>]*>/g, ""))}`
              )}
            </div>
            <div className="text-gray-600 whitespace-pre-wrap leading-relaxed break-words">{preview}</div>
            <div className="text-gray-400 mt-2 text-[10px]">
              点击跳转到完整来源
              {source.doc_type === "transcript" && source.media_id && " 并播放视频"}
            </div>
          </div>
        )}
      </sup>
    </>
  );
}

type CitationRenderContextValue = {
  sources: Source[];
  messageId: string;
};

const CitationRenderContext = createContext<CitationRenderContextValue>({
  sources: [],
  messageId: "",
});

function MarkdownLink({
  href,
  children,
  ...props
}: React.ComponentPropsWithoutRef<"a">) {
  const { sources, messageId } = useContext(CitationRenderContext);
  if (href?.startsWith("#cite-")) {
    return (
      <CitationMarker href={href} sources={sources} messageId={messageId}>
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

const markdownComponents: Components = {
  a: MarkdownLink,
};

export function Message({
  msg,
  conversationId,
  turnIndex,
  sourcesSelected = false,
  onToggleSources,
}: {
  msg: ChatMessage;
  conversationId: string | null;
  turnIndex: number;
  sourcesSelected?: boolean;
  onToggleSources?: (messageId: string) => void;
}) {
  const isUser = msg.role === "user";
  return (
    <article className={`mx-auto flex w-full max-w-[50rem] ${isUser ? "justify-end" : "justify-start"} px-4 py-4`}>
      <div
        className={
          (isUser ? "max-w-[70%] rounded-ui-lg bg-primary px-4 py-3 text-primary-foreground" : "min-w-0 w-full") +
          ""
        }
      >
        {isUser ? (
          <div className="whitespace-pre-wrap break-words">{msg.content}</div>
        ) : (
          <>
            <AnswerStatus msg={msg} />
            <div className={"prose-tight relative " + (msg.streaming && msg.content ? "caret" : "")}>
              {msg.content ? (
                <CitationRenderContext.Provider value={{ sources: msg.sources || [], messageId: msg.id }}>
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                    components={markdownComponents}
                  >
                    {linkifyCitations(normalizeMath(msg.content))}
                  </ReactMarkdown>
                </CitationRenderContext.Provider>
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
                <FeedbackBar msg={msg} conversationId={conversationId} turnIndex={turnIndex} />
              </div>
            )}
          </>
        )}
      </div>
    </article>
  );
}
