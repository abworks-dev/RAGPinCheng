import React, { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { ChatMessage, Source } from "../types";
import { SourcesPanel } from "./SourcesPanel";
import { DebugPanel } from "./DebugPanel";
import { FeedbackBar } from "./FeedbackBar";
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

function StageIndicator({ msg }: { msg: ChatMessage }) {
  const stage = msg.stage;
  if (!stage || stage === "done") return null;
  let label = "";
  if (stage === "retrieving") label = "🔎 改写问题并检索资料中…";
  else if (stage === "generating") {
    const n = msg.prep?.final_count ?? msg.sources?.length ?? 0;
    label = `📝 已检索到 ${n} 条来源，正在生成回答…`;
  } else if (stage === "streaming" && !msg.content) label = "📝 正在生成回答…";
  if (!label) return null;
  return <div className="text-muted text-sm">{label}</div>;
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

  if (!source) {
    // Fallback: no matching source found — render as plain text.
    return <span className="text-gray-500">{children}</span>;
  }

  // Tooltip preview text: first 120 chars of the source.
  const preview = source.text.length > 120 ? source.text.slice(0, 120) + "…" : source.text;

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
              : "bg-gray-100 text-gray-600 hover:bg-gray-200 border border-gray-200"
          }`}
          onClick={(e) => {
            e.preventDefault();
            if (idx >= 0) dispatchCitation({ messageId, sourceIndex: idx });
          }}
        >
          {idx + 1}
        </a>

        {/* Tooltip: positioned above the superscript.
            Smart alignment: prefers expanding to the right, but if too close to
            the right edge, expand leftwards. Prevents viewport overflow. */}
        {isHovered && (
          <div className="absolute z-[100] min-w-[200px] max-w-[320px] right-0 bottom-[100%] mb-0.5 bg-white border border-gray-200 rounded-lg shadow-xl p-3 text-xs break-words">
            <div className="font-medium text-gray-900 mb-1 truncate">{source.doc_title}</div>
            <div className="text-gray-500 mb-2 truncate">
              {source.doc_type === "transcript" ? `@${source.start_time || ""}` : `§${source.section_path || ""}`}
            </div>
            <div className="text-gray-600 whitespace-pre-wrap leading-relaxed break-words">{preview}</div>
            <div className="text-gray-400 mt-2 text-[10px]">点击跳转到完整来源</div>
          </div>
        )}
      </sup>
    </>
  );
}

export function Message({
  msg,
  conversationId,
  turnIndex,
}: {
  msg: ChatMessage;
  conversationId: string | null;
  turnIndex: number;
}) {
  const isUser = msg.role === "user";
  return (
    <div className={`w-full flex ${isUser ? "justify-end" : "justify-start"} px-4`}>
      <div
        className={
          "max-w-3xl w-full rounded-2xl px-4 py-3 " +
          (isUser
            ? "bg-accent text-white ml-12"
            : "bg-panel border border-gray-200 mr-12")
        }
      >
        {isUser ? (
          <div className="whitespace-pre-wrap break-words">{msg.content}</div>
        ) : (
          <>
            <div className={"prose-tight relative " + (msg.streaming && msg.content ? "caret" : "")}>
              {msg.content ? (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                  components={{
                    a: ({ href, children, ...props }) => {
                      if (href && href.startsWith("#cite-")) {
                        return (
                          <CitationMarker
                            href={href}
                            sources={msg.sources || []}
                            messageId={msg.id}
                            {...props}
                          >
                            {children}
                          </CitationMarker>
                        );
                      }
                      return (
                        <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                          {children}
                        </a>
                      );
                    },
                  }}
                >
                  {linkifyCitations(normalizeMath(msg.content))}
                </ReactMarkdown>
              ) : null}
              <StageIndicator msg={msg} />
            </div>
            {msg.error && (
              <div className="mt-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
                ⚠️ {msg.error}
              </div>
            )}
            {msg.sources && msg.sources.length > 0 && (
              <SourcesPanel
                sources={msg.sources}
                messageId={msg.id}
                conversationId={conversationId}
              />
            )}
            {!msg.streaming && !msg.error && msg.content && (
              <FeedbackBar msg={msg} conversationId={conversationId} turnIndex={turnIndex} />
            )}
            <DebugPanel msg={msg} />
          </>
        )}
      </div>
    </div>
  );
}
