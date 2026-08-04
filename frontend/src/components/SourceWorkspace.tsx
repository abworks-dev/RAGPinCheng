import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Clipboard,
  CirclePlay,
  FileSpreadsheet,
  FileText,
  LocateFixed,
  Presentation,
  Video,
} from "lucide-react";
import { api } from "../api/client";
import { usePdfPreview } from "../hooks/usePdfPreview";
import { timestampToSeconds, useVideoPlayer } from "../hooks/useVideoPlayer";
import type { ChatMessage, Source } from "../types";
import { useAutoHideScrollbar } from "../hooks/useAutoHideScrollbar";
import { stripMarkdown } from "../utils/markdown";
import {
  CITATION_EVENT,
  CITATION_HOVER_EVENT,
  type CitationDetail,
  type CitationHoverDetail,
} from "./citations";
import { FeedbackDialog, type FeedbackSubmission } from "./FeedbackDialog";

const citationFeedbackReasons = ["引用内容不符", "来源定位错误", "资料已过时", "其他"] as const;

type SourceSet = {
  messageId: string;
  sources: Source[];
  searchQuery?: string;
};

function sourceSetsFromMessages(messages: ChatMessage[]): SourceSet[] {
  return messages
    .filter((message) => message.role === "assistant" && message.sources?.length)
    .map((message) => ({
      messageId: message.id,
      sources: message.sources || [],
      searchQuery: message.prep?.search_query || message.query,
    }));
}

function cleanSection(source: Source): string {
  return (source.section_path || "")
    .replace(/<[^>]*>/g, "")
    .split(" > ")
    .filter(Boolean)
    .join(" / ");
}

function sourceLocator(source: Source): string {
  if (source.doc_type === "transcript") return source.start_time ? `视频 ${source.start_time}` : "视频片段";
  if (source.doc_type === "xlsx" && (source.sheet_name || source.cell_range)) {
    return [source.sheet_name, source.cell_range].filter(Boolean).join(" · ");
  }
  if (source.doc_type === "pptx" && source.slide_number) return `第 ${source.slide_number} 页`;
  return cleanSection(source) || "未提供定位信息";
}

function SourceTypeIcon({ source }: { source: Source }) {
  if (source.doc_type === "transcript") return <Video className="size-4" />;
  if (source.doc_type === "xlsx") return <FileSpreadsheet className="size-4" />;
  if (source.doc_type === "pptx") return <Presentation className="size-4" />;
  return <FileText className="size-4" />;
}

function matchesQuery(text: string, query?: string): React.ReactNode {
  if (!query) return text;
  const keywords = query
    .replace(/[^\w一-鿿]/g, " ")
    .split(/\s+/)
    .filter((word) => word.length >= 2)
    .sort((a, b) => b.length - a.length)
    .slice(0, 8);
  if (!keywords.length) return text;
  const escaped = keywords.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const chunks = text.split(new RegExp(`(${escaped.join("|")})`, "gi"));
  return chunks.map((chunk, index) =>
    keywords.some((word) => word.toLowerCase() === chunk.toLowerCase()) ? (
      <mark key={`${chunk}-${index}`} className="rounded-sm bg-amber-200/70 px-0.5 text-inherit dark:bg-amber-500/30">
        {chunk}
      </mark>
    ) : (
      chunk
    ),
  );
}

export function SourceWorkspace({
  messages,
  conversationId,
  selectedMessageId,
  highlightedSourceIndex = null,
  onSelectedMessageChange,
  onSourceHighlightChange,
}: {
  messages: ChatMessage[];
  conversationId: string | null;
  selectedMessageId?: string | null;
  highlightedSourceIndex?: number | null;
  onSelectedMessageChange?: (messageId: string) => void;
  onSourceHighlightChange?: (messageId: string, sourceIndex: number) => void;
}) {
  const sets = useMemo(() => sourceSetsFromMessages(messages), [messages]);
  const latest = sets[sets.length - 1];
  const [activeIndex, setActiveIndex] = useState(0);
  const sourceListScroll = useAutoHideScrollbar<HTMLDivElement>();
  const listRefs = useRef<Record<number, HTMLButtonElement | null>>({});
  const activeMessageId = selectedMessageId || latest?.messageId || null;
  const activeSet = sets.find((set) => set.messageId === activeMessageId) || latest;
  const source = activeSet?.sources[activeIndex] || activeSet?.sources[0];
  const safeIndex = source ? Math.max(0, activeSet!.sources.indexOf(source)) : 0;

  useEffect(() => {
    if (latest && !sets.some((set) => set.messageId === activeMessageId)) {
      onSelectedMessageChange?.(latest.messageId);
      setActiveIndex(0);
    }
  }, [activeMessageId, latest, onSelectedMessageChange, sets]);

  useEffect(() => {
    const onCitation = (event: Event) => {
      const detail = (event as CustomEvent<CitationDetail>).detail;
      if (!detail || !sets.some((set) => set.messageId === detail.messageId)) return;
      onSelectedMessageChange?.(detail.messageId);
      setActiveIndex(detail.sourceIndex);
      requestAnimationFrame(() => {
        listRefs.current[detail.sourceIndex]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    };
    window.addEventListener(CITATION_EVENT, onCitation);
    return () => window.removeEventListener(CITATION_EVENT, onCitation);
  }, [onSelectedMessageChange, sets]);

  const selectSource = (messageId: string, index: number) => {
    onSelectedMessageChange?.(messageId);
    onSourceHighlightChange?.(messageId, index);
    setActiveIndex(index);
    window.dispatchEvent(
      new CustomEvent<CitationHoverDetail>(CITATION_HOVER_EVENT, {
        detail: { messageId, sourceIndex: index },
      }),
    );
  };

  if (!activeSet || !source) {
    return (
      <aside className="flex h-full flex-col bg-card">
        <WorkspaceHeader count={0} />
        <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
          <Clipboard className="mb-3 size-8 text-muted-foreground/60" />
          <h2 className="text-sm font-medium text-foreground">暂无可核验来源</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            完成一次检索后，引用资料会集中显示在这里。
          </p>
        </div>
      </aside>
    );
  }

  return (
    <aside className="flex h-full min-h-0 flex-col bg-card">
      <WorkspaceHeader count={activeSet.sources.length} />
      {sets.length > 1 && (
        <label className="border-b border-border px-4 py-3">
          <span className="mb-1 block text-[11px] font-medium text-muted-foreground">回答轮次</span>
          <select
            value={activeSet.messageId}
            onChange={(event) => {
              onSelectedMessageChange?.(event.target.value);
              setActiveIndex(0);
            }}
            className="h-9 w-full rounded-ui-md border border-input bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {sets.map((set, index) => (
              <option key={set.messageId} value={set.messageId}>
                第 {index + 1} 条含来源回答 · {set.sources.length} 项
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="grid min-h-0 flex-1 grid-rows-[minmax(12rem,0.9fr)_minmax(16rem,1.1fr)]">
        <div
          ref={sourceListScroll.ref}
          {...sourceListScroll.interactionProps}
          className={`overflow-y-auto border-b border-border p-3 ${sourceListScroll.className}`}
        >
          {activeSet.sources.map((item, index) => (
            <button
              key={`${item.parent_id}-${index}`}
              ref={(element) => {
                listRefs.current[index] = element;
              }}
              type="button"
              onClick={() => selectSource(activeSet.messageId, index)}
              className={
                "mb-2 flex w-full items-start gap-2.5 rounded-ui-md border px-3 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                (highlightedSourceIndex === index
                  ? "border-primary/70 bg-primary/10 text-foreground shadow-sm"
                  : "border-border bg-card hover:border-primary/30 hover:bg-secondary/60")
              }
            >
              <span
                className={
                  "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-ui-sm text-xs font-semibold " +
                  (highlightedSourceIndex === index
                    ? "bg-primary text-primary-foreground"
                    : "border border-border bg-secondary text-muted-foreground")
                }
              >
                {index + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-sm font-medium">
                  <SourceTypeIcon source={item} />
                  <span className="truncate">{item.doc_title}</span>
                </span>
                <span className="mt-1.5 block truncate text-xs text-muted-foreground">
                  {item.category || "未分类"} · {sourceLocator(item)}
                </span>
              </span>
              <ChevronRight className="mt-1 size-3.5 shrink-0 text-muted-foreground" />
            </button>
          ))}
        </div>
        <SourceDetail
          source={source}
          sourceIndex={safeIndex}
          messageId={activeSet.messageId}
          conversationId={conversationId}
          searchQuery={activeSet.searchQuery}
        />
      </div>
    </aside>
  );
}

function WorkspaceHeader({ count }: { count: number }) {
  return (
    <div className="flex h-14 shrink-0 items-center border-b border-border px-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">来源核验</h2>
        <p className="text-[11px] text-muted-foreground">{count ? `${count} 项回答依据` : "等待检索结果"}</p>
      </div>
    </div>
  );
}

function SourceDetail({
  source,
  sourceIndex,
  messageId,
  conversationId,
  searchQuery,
}: {
  source: Source;
  sourceIndex: number;
  messageId: string;
  conversationId: string | null;
  searchQuery?: string;
}) {
  const { open: openDocument } = usePdfPreview();
  const { open: openVideo } = useVideoPlayer();
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const detailScroll = useAutoHideScrollbar<HTMLDivElement>();
  const text = stripMarkdown(source.text);
  const visibleText = expanded || text.length <= 900 ? text : `${text.slice(0, 900)}…`;

  const openFullResource = () => {
    if (source.doc_type === "transcript" && source.media_id) {
      openVideo({
        mediaId: source.media_id,
        title: source.doc_title,
        startSeconds: timestampToSeconds(source.start_time),
        fromSource: true,
      });
      return;
    }
    if (["pdf", "docx", "xlsx", "pptx"].includes(source.doc_type)) {
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
    }
  };

  const copySource = async () => {
    try {
      await navigator.clipboard.writeText(
        `[${source.doc_title}] ${sourceLocator(source)}\n${cleanSection(source)}\n\n${text}`,
      );
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setStatus("复制失败，请手动选择原文。");
    }
  };

  const submitReport = async ({ reason, note }: FeedbackSubmission) => {
    await api.sendFeedback({
      kind: "citation",
      note: note ? `原因：${reason}\n补充：${note}` : `原因：${reason}`,
      conversation_id: conversationId,
      message_id: messageId,
      parent_id: source.parent_id,
      doc_title: source.doc_title,
      section_path: source.section_path,
      start_time: source.start_time,
      category: source.category,
    });
  };

  return (
    <div
      ref={detailScroll.ref}
      {...detailScroll.interactionProps}
      className={`min-h-0 overflow-y-auto px-4 py-4 ${detailScroll.className}`}
    >
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-ui-md bg-ui-accent text-ui-accent-foreground">
          <SourceTypeIcon source={source} />
        </span>
        <div className="min-w-0">
          <div className="text-xs text-muted-foreground">来源 {sourceIndex + 1} · {source.category || "未分类"}</div>
          <h3 className="mt-0.5 break-words text-sm font-semibold text-foreground">{source.doc_title}</h3>
        </div>
      </div>
      <div className="mt-4 rounded-ui-md border border-border bg-secondary/70 px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
          <LocateFixed className="size-3.5" />
          定位
        </div>
        <p className="mt-1 break-words text-xs leading-relaxed text-foreground">{sourceLocator(source)}</p>
      </div>
      <div className="mt-4">
        <div className="mb-2 text-[11px] font-medium text-muted-foreground">引用原文</div>
        <p className="whitespace-pre-wrap break-words border-l-2 border-info bg-info/10 px-3 py-2.5 text-xs leading-6 text-foreground">
          {matchesQuery(visibleText, searchQuery)}
        </p>
        {text.length > 900 && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            <ChevronDown className={`size-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} />
            {expanded ? "收起原文" : "展开原文"}
          </button>
        )}
      </div>
      <div className="mt-5 grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={openFullResource}
          disabled={
            source.doc_type === "transcript"
              ? !source.media_id
              : !["pdf", "docx", "xlsx", "pptx"].includes(source.doc_type)
          }
          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-ui-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {source.doc_type === "transcript" ? <CirclePlay className="size-3.5" /> : <SourceTypeIcon source={source} />}
          打开完整资料
        </button>
        <button
          type="button"
          onClick={copySource}
          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-ui-md border border-border px-3 text-xs font-medium text-foreground hover:bg-secondary"
        >
          {copied ? <Check className="size-3.5 text-success" /> : <Clipboard className="size-3.5" />}
          {copied ? "已复制" : "复制来源"}
        </button>
      </div>
      <FeedbackDialog
        category="引用问题"
        description="选择这条引用存在的问题，帮助我们改进来源核验质量。"
        reasons={citationFeedbackReasons}
        notePlaceholder="可选：补充说明这条引用的问题"
        successMessage="引用问题已提交"
        onSubmit={submitReport}
        trigger={
          <button
            type="button"
            className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-destructive"
          >
            <AlertTriangle className="size-3.5" />
            报告引用问题
          </button>
        }
      />
      {status && <p className="mt-2 text-xs text-muted-foreground">{status}</p>}
    </div>
  );
}
